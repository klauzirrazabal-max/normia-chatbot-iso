"""
Genera el material visual del SGC con difusion local (SDXL sobre MPS).

POR QUE UN ASISTENTE DE CUMPLIMIENTO GENERA IMAGENES
----------------------------------------------------
No es decoracion. ISO 9001 pide explicitamente que la organizacion forme y
comunique:

    7.2 Competencia · 7.3 Toma de conciencia · 7.4 Comunicacion

Un SGC que nadie conoce no se cumple. El material de concienciacion y de
induccion da servicio a esas tres clausulas, y hoy se encarga fuera o no se hace.

COMO SE CONSTRUYE CADA PIEZA
----------------------------
El modelo genera el ARTE; el logotipo real se COMPONE encima. Pedirle a un
modelo de difusion que dibuje una marca concreta produce un logotipo parecido
pero falso, y una marca mal dibujada en material de calidad es peor que no tener
material. Asi la identidad es exacta por construccion.

Tampoco se le pide texto: los modelos de difusion lo escriben mal. El titular se
compone como texto real, nitido y corregible.

    uv run python scripts/generar_imagenes.py
    uv run python scripts/generar_imagenes.py --solo cartel-epp --pasos 40
    uv run python scripts/generar_imagenes.py --lora adapters/sdxl-abiservi
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
LOGO = RAIZ / "data" / "marca" / "logo_abiservi.png"
DESTINO = RAIZ / "docs" / "imagenes"

# Extraidos del propio logotipo, no elegidos a ojo:
#   #1050D0 azul oscuro (19.5%) · #2090E0 azul claro (22.2%) · #303030 grafito (58.3%)
AZUL_OSCURO = (0x10, 0x50, 0xD0)
AZUL_CLARO = (0x20, 0x90, 0xE0)
GRAFITO = (0x30, 0x30, 0x30)

# CLIP corta el prompt a 77 tokens y descarta el resto SIN AVISAR en la imagen.
# Medido: con la version larga se truncaba justo "no text, no letters", que es lo
# que impide que el modelo garabatee letras falsas. Lo corto va primero; lo que
# no cabe se repite en el negativo, que se evalua aparte.
MARCA = "flat vector illustration, corporate blue and white, minimal, clean, no text"

NEGATIVO = (
    "text, letters, words, typography, watermark, signature, logo, brand name, "
    "cluttered, distorted hands, extra limbs, lowres, jpeg artifacts"
)


@dataclass(frozen=True)
class Pieza:
    """Una pieza de material visual, con la clausula que la justifica."""

    nombre: str
    clausula: str
    titular: str
    prompt: str
    semilla: int


PIEZAS = [
    Pieza(
        "cartel-epp", "ISO 9001 §7.3 · OPE-PR-02",
        "El equipo de protección no es opcional",
        f"Warehouse worker in hard hat and high-visibility vest beside stacked "
        f"pallets, full figure centered, {MARCA}",
        101,
    ),
    Pieza(
        "cartel-salida", "ISO 9001 §7.3 · seguridad",
        "Una salida bloqueada es una salida que no existe",
        f"Warehouse emergency exit door kept clear, wide aisle, floor markings, "
        f"pallets stacked away from the doorway, {MARCA}",
        102,
    ),
    Pieza(
        "cartel-hallazgo", "ISO 9001 §10.2 · CAL-PR-03",
        "Si lo ves, repórtalo",
        f"Warehouse worker reporting a problem on a tablet, magnifying glass and "
        f"checklist motif, {MARCA}",
        103,
    ),
    Pieza(
        "induccion-documental", "ISO 9001 §7.5 · CAL-PR-01",
        "Solo la versión vigente tiene valor",
        f"Controlled document management, labelled folders and a filing cabinet, one "
        f"folder highlighted, {MARCA}",
        104,
    ),
    Pieza(
        "portada-sgc", "material corporativo",
        "Sistema de Gestión de la Calidad",
        f"Abstract hexagonal geometric network pattern with a subtle warehouse "
        f"silhouette, {MARCA}",
        105,
    ),
]


def cargar_pipeline(lora: str | None):
    import torch
    from diffusers import AutoencoderKL, StableDiffusionXLPipeline

    if not torch.backends.mps.is_available():
        print("MPS no disponible: esto esta pensado para Apple Silicon", file=sys.stderr)

    # EL VAE VA APARTE, Y NO ES OPCIONAL. El VAE original de SDXL desborda en
    # float16 y devuelve un tensor de NaN: la imagen sale COMPLETAMENTE NEGRA, sin
    # error ni aviso. Medido aqui: 4 de 5 piezas negras en la primera tanda.
    # `sdxl-vae-fp16-fix` es el mismo VAE reescalado para no desbordar.
    vae = AutoencoderKL.from_pretrained(
        "madebyollin/sdxl-vae-fp16-fix", torch_dtype=torch.float16
    )
    pipe = StableDiffusionXLPipeline.from_pretrained(
        "stabilityai/stable-diffusion-xl-base-1.0",
        vae=vae,
        torch_dtype=torch.float16,
        variant="fp16",
        use_safetensors=True,
    ).to("mps")
    # SDXL en 16 bits sobre memoria unificada cabe holgado, pero el "attention
    # slicing" evita picos que compiten con el entrenamiento del LLM si corre a la vez.
    pipe.enable_attention_slicing()
    if lora:
        pipe.load_lora_weights(lora)
        print(f"adaptador de imagen cargado: {lora}")
    return pipe


def esta_negra(imagen) -> bool:
    """
    Un desbordamiento del VAE produce una imagen negra, no una excepcion.

    Sin esta comprobacion el script termina anunciando cinco piezas generadas y
    cuatro son rectangulos negros con el logotipo encima. Se prefiere fallar
    ruidosamente.
    """
    muestra = list(imagen.convert("RGB").resize((40, 40)).getdata())
    return sum(sum(c) for c in muestra) / (len(muestra) * 3) < 6


def componer(arte: Path, pieza: Pieza, destino: Path) -> None:
    """
    Pone el logotipo REAL y el titular como texto real sobre el arte generado.

    La cabecera se APILA encima del arte, no se superpone. La primera version la
    dibujaba como banda translucida sobre la imagen y le tapaba la cabeza al
    operario: el modelo centra el sujeto y no tiene forma de saber que ahi va a ir
    texto. Apilando, el arte se ve entero y la cabecera siempre se lee.
    """
    from PIL import Image, ImageDraw, ImageFont

    fondo = Image.open(arte).convert("RGB")
    an, al_arte = fondo.size

    def fuente(tam: int):
        for ruta in (
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        ):
            if Path(ruta).exists():
                return ImageFont.truetype(ruta, tam)
        return ImageFont.load_default(tam)

    tam = int(an * 0.062)
    f = fuente(tam)
    medidor = ImageDraw.Draw(Image.new("RGB", (10, 10)))

    palabras, linea, lineas = pieza.titular.split(), "", []
    for w in palabras:
        tentativa = f"{linea} {w}".strip()
        if medidor.textlength(tentativa, font=f) > an * 0.88 and linea:
            lineas.append(linea)
            linea = w
        else:
            linea = tentativa
    lineas.append(linea)

    margen = int(an * 0.06)
    alto_logo = int(an * 0.115)
    cabecera = margen + alto_logo + int(tam * 0.7) + len(lineas) * int(tam * 1.16) + int(an * 0.09)

    lienzo = Image.new("RGB", (an, cabecera + al_arte), "white")
    lienzo.paste(fondo, (0, cabecera))
    dib = ImageDraw.Draw(lienzo)

    if LOGO.exists():
        logo = Image.open(LOGO).convert("RGBA")
        ancho = int(alto_logo * logo.width / logo.height)
        logo = logo.resize((ancho, alto_logo), Image.LANCZOS)
        lienzo.paste(logo, (margen, int(margen * 0.8)), logo)

    y = int(margen * 0.8) + alto_logo + int(tam * 0.7)
    for ln in lineas:
        dib.text((margen, y), ln, font=f, fill=GRAFITO)
        y += int(tam * 1.16)

    pie = fuente(int(an * 0.026))
    dib.text((margen, y + int(an * 0.012)), pieza.clausula, font=pie, fill=AZUL_CLARO)
    dib.line([(0, cabecera), (an, cabecera)], fill=AZUL_OSCURO, width=max(3, an // 190))

    lienzo.save(destino, quality=95)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--solo", nargs="*", help="generar solo estas piezas, por nombre")
    parser.add_argument("--pasos", type=int, default=30)
    parser.add_argument("--lora", default=None, help="adaptador LoRA de imagen")
    parser.add_argument("--salida", type=Path, default=DESTINO)
    args = parser.parse_args()

    piezas = [p for p in PIEZAS if not args.solo or p.nombre in args.solo]
    if not piezas:
        print(f"ninguna pieza coincide. Hay: {', '.join(p.nombre for p in PIEZAS)}")
        return 1

    args.salida.mkdir(parents=True, exist_ok=True)
    crudo = args.salida / "crudo"
    crudo.mkdir(exist_ok=True)

    import torch

    pipe = cargar_pipeline(args.lora)
    fallidas: list[str] = []

    for i, pieza in enumerate(piezas, start=1):
        print(f"[{i}/{len(piezas)}] {pieza.nombre} ...", flush=True)
        vertical = pieza.nombre != "induccion-documental"
        imagen = pipe(
            prompt=pieza.prompt,
            negative_prompt=NEGATIVO,
            num_inference_steps=args.pasos,
            guidance_scale=7.0,
            height=1024 if vertical else 768,
            width=768 if vertical else 1024,
            generator=torch.Generator("cpu").manual_seed(pieza.semilla),
        ).images[0]

        if esta_negra(imagen):
            print("      FALLO: el VAE desbordo y la imagen salio negra", file=sys.stderr)
            fallidas.append(pieza.nombre)
            continue

        arte = crudo / f"{pieza.nombre}.png"
        imagen.save(arte)
        componer(arte, pieza, args.salida / f"{pieza.nombre}.png")
        print(f"      {args.salida / (pieza.nombre + '.png')}", flush=True)

    hechas = len(piezas) - len(fallidas)
    print(f"\n{hechas} de {len(piezas)} piezas en {args.salida}")
    if fallidas:
        print(f"fallidas: {', '.join(fallidas)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
