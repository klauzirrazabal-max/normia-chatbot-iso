"""
Construye el dataset de imagen para entrenar el LoRA de marca.

Un DreamBooth LoRA necesita 15-20 fotos del sujeto. Del cliente solo tenemos el
logotipo, asi que las piezas se fabrican: el modelo de difusion genera el
PRODUCTO EN BLANCO y el logotipo real se compone encima, en perspectiva.

Es lo mismo que hace el dataset del taller de la sesion 3 -- una marca sobre
tazas, camisetas y cuadernos -- pero construido, no recopilado.

    uv run python scripts/construir_dataset_marca.py
    uv run python scripts/construir_dataset_marca.py --solo taza camiseta --pasos 40

POR QUE COMPONER Y NO PEDIRSELO AL MODELO
------------------------------------------
Pedirle a un modelo de difusion "una taza con el logotipo de ABISERVI" produce un
logotipo parecido e inventado. Y entrenar un LoRA con logotipos falsos ensena al
modelo justamente eso: a dibujar una marca que no es la del cliente.

Componiendo, cada imagen del dataset lleva la marca EXACTA. El LoRA aprende su
forma, sus colores y como se ve sobre distintos materiales.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
LOGO = RAIZ / "data" / "marca" / "logo_abiservi.png"
DESTINO = RAIZ / "data" / "marca" / "abiservi"

# Del propio logotipo, no elegidos a ojo.
AZUL_OSCURO = (0x10, 0x50, 0xD0)

MARCA = "product photography, studio lighting, plain background, centered, sharp, no text"
NEGATIVO = (
    "text, letters, words, logo, brand name, watermark, signature, label, "
    "pattern, print, graphic, cluttered, blurry, lowres, jpeg artifacts"
)


@dataclass(frozen=True)
class Soporte:
    """Un producto sobre el que va la marca, y donde va."""

    nombre: str
    prompt: str
    semilla: int
    # Caja donde se pega el logotipo, en fracciones del ancho y alto.
    caja: tuple[float, float, float, float]
    # Inclinacion en grados: un producto fotografiado de frente no existe.
    giro: float = 0.0
    # Opacidad: sobre tela el logotipo no es un adhesivo perfecto.
    opacidad: float = 1.0


SOPORTES = [
    Soporte("camiseta-blanca", "plain white cotton t-shirt laid flat", 11,
            (0.34, 0.28, 0.66, 0.42), opacidad=0.88),
    Soporte("camiseta-azul", "plain navy blue cotton t-shirt laid flat", 12,
            (0.34, 0.28, 0.66, 0.42), opacidad=0.85),
    Soporte("taza-blanca", "plain white ceramic mug on a table", 13,
            (0.28, 0.38, 0.66, 0.56), giro=-4),
    Soporte("taza-azul", "plain dark blue ceramic mug on a table", 14,
            (0.28, 0.38, 0.66, 0.56), giro=3),
    Soporte("cuaderno", "closed hardcover notebook on a desk, plain cover", 15,
            (0.26, 0.34, 0.74, 0.52), giro=-3),
    Soporte("bolsa-tela", "plain canvas tote bag hanging", 16,
            (0.26, 0.36, 0.74, 0.54), opacidad=0.9),
    Soporte("termo", "plain stainless steel insulated bottle standing", 17,
            (0.32, 0.40, 0.68, 0.54), giro=2),
    Soporte("gorra", "plain navy baseball cap front view", 18,
            (0.32, 0.36, 0.68, 0.50), opacidad=0.94),
    Soporte("carpeta", "plain document folder on a desk, closed", 19,
            (0.24, 0.30, 0.76, 0.46)),
    Soporte("chaleco", "plain orange safety work vest on a hanger, front view", 20,
            (0.30, 0.34, 0.70, 0.50), opacidad=0.9),
    Soporte("casco", "plain white hard hat safety helmet, side view", 21,
            (0.34, 0.40, 0.66, 0.56), giro=-6),
    Soporte("portapapeles", "plain clipboard with blank paper on a warehouse desk", 22,
            (0.26, 0.28, 0.74, 0.44)),
    Soporte("libreta-pequena", "small pocket notebook on a wooden desk", 23,
            (0.28, 0.34, 0.72, 0.50), giro=4),
    Soporte("lanyard", "plain fabric lanyard with a blank badge holder", 24,
            (0.30, 0.40, 0.70, 0.56)),
    Soporte("caja", "plain cardboard shipping box on a warehouse floor", 25,
            (0.26, 0.34, 0.74, 0.50), giro=-2),
    Soporte("senal", "blank rectangular metal sign on a warehouse wall", 26,
            (0.20, 0.32, 0.80, 0.52)),
    Soporte("delantal", "plain dark apron hanging on a hook", 27,
            (0.30, 0.32, 0.70, 0.48), opacidad=0.92),
    Soporte("llavero", "plain rectangular keychain tag on a white surface", 28,
            (0.24, 0.36, 0.76, 0.56)),
]


def version_clara(logo):
    """
    La version del logotipo para soportes oscuros.

    El 58% de sus pixeles opacos tienen luminancia por debajo de 100 -- el gris
    del texto y el azul oscuro del isotipo -- y sobre una camiseta marino o una
    taza negra sencillamente desaparecen. Un disenador usaria la version en
    blanco de la marca; como no la tenemos, se deriva: lo oscuro pasa a blanco y
    el azul claro se conserva, que es lo que mantiene la identidad reconocible.
    """
    import numpy as np
    from PIL import Image

    a = np.array(logo.convert("RGBA")).astype(int)
    rgb, alfa = a[..., :3], a[..., 3]
    lum = rgb.mean(axis=2)

    # Solo se aclara lo oscuro: el azul #2090E0 ya contrasta sobre negro.
    oscuro = (lum < 110) & (alfa > 0)
    rgb[oscuro] = [255, 255, 255]
    return Image.fromarray(np.dstack([rgb, alfa]).astype("uint8"), "RGBA")


def caja_del_producto(arte, margen: float = 0.06):
    """
    Donde esta el producto de verdad, por contraste con el fondo.

    La caja fija del soporte se define ANTES de ver como sale la imagen, y el
    modelo no siempre centra el objeto: se observo el logotipo flotando fuera del
    boligrafo y fuera del termo, sobre el fondo del estudio. Aqui se mide donde
    esta la masa del producto y el logotipo se pega sobre ella.
    """
    import numpy as np
    from PIL import ImageFilter

    gris = arte.convert("L").filter(ImageFilter.GaussianBlur(4))
    a = np.array(gris).astype(int)
    # El fondo de estudio es lo que domina los bordes de la imagen.
    borde = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    fondo = np.median(borde)
    objeto = np.abs(a - fondo) > 14

    filas, cols = np.where(objeto)
    if filas.size < a.size * 0.02:      # apenas hay objeto: se deja la caja fija
        return None
    al, an = a.shape
    y0, y1 = filas.min() / al, filas.max() / al
    x0, x1 = cols.min() / an, cols.max() / an
    # Se encoge un poco para no quedar pegado al borde del producto.
    dx, dy = (x1 - x0) * margen, (y1 - y0) * margen
    return (x0 + dx, y0 + dy, x1 - dx, y1 - dy)


def cargar_pipeline():
    import torch
    from diffusers import AutoencoderKL, StableDiffusionXLPipeline

    # El VAE original desborda en float16 y devuelve NaN: imagen negra sin error.
    vae = AutoencoderKL.from_pretrained(
        "madebyollin/sdxl-vae-fp16-fix", torch_dtype=torch.float16
    )
    pipe = StableDiffusionXLPipeline.from_pretrained(
        "stabilityai/stable-diffusion-xl-base-1.0",
        vae=vae, torch_dtype=torch.float16, variant="fp16", use_safetensors=True,
    ).to("mps")
    pipe.enable_attention_slicing()
    return pipe


def esta_negra(imagen) -> bool:
    muestra = list(imagen.convert("RGB").resize((40, 40)).getdata())
    return sum(sum(c) for c in muestra) / (len(muestra) * 3) < 6


def componer(arte, soporte: Soporte, logo, logo_claro=None):
    """
    Estampa el logotipo sobre el producto, no lo pega encima.

    La diferencia esta en tres cosas, y sin ellas se nota a simple vista que es
    un montaje:

    1. SOMBREADO. Se toma la luminancia del producto justo debajo del logotipo y
       se MULTIPLICA la tinta por ella. Asi los pliegues de la tela y la sombra
       del estudio se ven A TRAVES del estampado, que es lo que hace un print
       real. Sin esto el logotipo flota.
    2. BORDE BLANDO. Una serigrafia no tiene el filo de un vector: un desenfoque
       de menos de un pixel basta para que deje de parecer recortado.
    3. TINTA, NO ADHESIVO. La opacidad baja un poco y deja pasar la textura.

    El giro tampoco es un adorno: un LoRA entrenado con la marca siempre
    perfectamente frontal aprende a dibujarla solo asi y luego no sabe ponerla en
    perspectiva.
    """
    from PIL import Image, ImageFilter

    lienzo = arte.convert("RGB").copy()
    an, al = lienzo.size

    # La caja del producto manda sobre la fija, si se detecta.
    real = caja_del_producto(arte)
    if real:
        rx0, ry0, rx1, ry1 = real
        fx0, fy0, fx1, fy1 = soporte.caja
        # La posicion relativa que pedia el soporte, trasladada al producto real.
        x0 = rx0 + (rx1 - rx0) * fx0
        x1 = rx0 + (rx1 - rx0) * fx1
        y0 = ry0 + (ry1 - ry0) * fy0
        y1 = ry0 + (ry1 - ry0) * fy1
    else:
        x0, y0, x1, y1 = soporte.caja

    # Sobre soporte oscuro, la version clara. Se decide MIRANDO la zona donde va
    # a ir el logotipo, no suponiendo por el nombre del producto.
    zona = arte.convert("L").crop((
        int(x0 * an), int(y0 * al), int(x1 * an), int(y1 * al),
    ))
    pixeles = list(zona.getdata())
    if logo_claro is not None and pixeles and sum(pixeles) / len(pixeles) < 110:
        logo = logo_claro

    ancho = int((x1 - x0) * an)
    alto = int(ancho * logo.height / logo.width)

    marca = logo.resize((ancho, alto), Image.LANCZOS)
    if soporte.giro:
        marca = marca.rotate(soporte.giro, expand=True, resample=Image.BICUBIC)

    cx = int((x0 + x1) / 2 * an) - marca.width // 2
    cy = int((y0 + y1) / 2 * al) - marca.height // 2

    # 1. El sombreado del soporte, suavizado: interesa la forma de la luz, no la
    #    textura fina del tejido.
    debajo = lienzo.crop((cx, cy, cx + marca.width, cy + marca.height))
    sombra = debajo.convert("L").filter(ImageFilter.GaussianBlur(marca.width / 26))
    minimo, maximo = sombra.getextrema()
    rango = max(maximo - minimo, 1)

    # Se normaliza para que la zona MAS clara del soporte no altere la tinta y
    # las sombras la oscurezcan hasta un 45%. Multiplicar por la luminancia en
    # crudo apagaria el logotipo entero sobre un producto oscuro.
    sombra = sombra.point(lambda v: int(255 * (0.55 + 0.45 * (v - minimo) / rango)))

    rgb = Image.merge("RGB", [
        Image.blend(canal, Image.new("L", canal.size, 0), 0).point(lambda v: v)
        for canal in marca.convert("RGB").split()
    ])
    from PIL import ImageChops

    rgb = ImageChops.multiply(rgb, Image.merge("RGB", [sombra] * 3))

    # 2 y 3. Borde blando y tinta que deja pasar el material.
    alfa = marca.getchannel("A").filter(ImageFilter.GaussianBlur(max(0.6, ancho / 900)))
    alfa = alfa.point(lambda v: int(v * soporte.opacidad))

    estampado = Image.merge("RGBA", [*rgb.split(), alfa])
    lienzo.paste(estampado, (cx, cy), estampado)
    return lienzo


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--solo", nargs="*", help="generar solo estos soportes")
    parser.add_argument("--pasos", type=int, default=30)
    parser.add_argument("--salida", type=Path, default=DESTINO)
    args = parser.parse_args()

    if not LOGO.exists():
        print(f"falta el logotipo: {LOGO}", file=sys.stderr)
        return 1

    soportes = [s for s in SOPORTES if not args.solo or s.nombre in args.solo]
    if not soportes:
        print(f"ninguno coincide. Hay: {', '.join(s.nombre for s in SOPORTES)}")
        return 1

    import torch
    from PIL import Image

    args.salida.mkdir(parents=True, exist_ok=True)
    logo = Image.open(LOGO).convert("RGBA")
    logo_claro = version_clara(logo)
    pipe = cargar_pipeline()
    fallidos: list[str] = []

    for i, soporte in enumerate(soportes, start=1):
        print(f"[{i:2}/{len(soportes)}] {soporte.nombre} ...", flush=True)
        imagen = pipe(
            prompt=f"{soporte.prompt}, {MARCA}",
            negative_prompt=NEGATIVO,
            num_inference_steps=args.pasos,
            guidance_scale=7.0,
            height=1024, width=1024,
            generator=torch.Generator("cpu").manual_seed(soporte.semilla),
        ).images[0]

        if esta_negra(imagen):
            print("      el VAE desbordo: imagen negra", file=sys.stderr)
            fallidos.append(soporte.nombre)
            continue

        pieza = componer(imagen, soporte, logo, logo_claro)
        pieza.save(args.salida / f"{soporte.nombre}.png")

        # El pie de foto que espera DreamBooth. El testigo "abiservi" es la
        # palabra que el LoRA asociara a la marca: tiene que ser rara para no
        # pisar un concepto que el modelo ya conozca.
        (args.salida / f"{soporte.nombre}.txt").write_text(
            f"a photo of abiservi logo on a {soporte.prompt}", encoding="utf-8"
        )

    hechos = len(soportes) - len(fallidos)
    print(f"\n  {hechos} de {len(soportes)} piezas en {args.salida}")
    if fallidos:
        print(f"  fallidas: {', '.join(fallidos)}", file=sys.stderr)
    if hechos < 15:
        print(f"\n  AVISO: un DreamBooth LoRA quiere 15-20 imagenes y hay {hechos}.")
    return 1 if fallidos else 0


if __name__ == "__main__":
    raise SystemExit(main())
