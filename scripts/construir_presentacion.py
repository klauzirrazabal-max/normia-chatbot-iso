"""
Arma la presentacion de la Fase 2 desde los datos REALES de las pruebas.

No se escribe a mano. Lee:

  docs/comparacion-lora.json   las respuestas literales sin entrenar y entrenado
  docs/entrenamiento.log       el log del entrenamiento, para la curva de perdida
  docs/imagenes/*.png          las piezas generadas con difusion
  data/finetune/*/REPORTE.md   el recuento del dataset

y reutiliza el lenguaje visual de `docs/presentacion.html` -- su hoja de estilo y
su navegacion salen de ahi, no de una copia -- para que las dos laminas de la
misma exposicion se vean iguales.

Si un dato falta, la lamina lo dice. Nunca se inventa un numero.

    uv run python scripts/construir_presentacion.py
"""

from __future__ import annotations

import base64
import html
import json
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
DOCS = RAIZ / "docs"
ORIGEN = DOCS / "presentacion.html"
DESTINO = DOCS / "presentacion-fase2.html"
LOG = DOCS / "entrenamiento.log"
COMPARACION = DOCS / "comparacion-lora.json"
IMAGENES = DOCS / "imagenes"


# --- datos reales ------------------------------------------------------------


def curva_de_perdida(log: Path) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Extrae (iteracion, perdida) de entrenamiento y validacion del log real."""
    if not log.exists():
        return [], []
    texto = log.read_text(encoding="utf-8", errors="ignore")
    tren = [
        (int(i), float(p))
        for i, p in re.findall(r"Iter (\d+): Train loss ([\d.]+)", texto)
    ]
    val = [
        (int(i), float(p))
        for i, p in re.findall(r"Iter (\d+): Val loss ([\d.]+)", texto)
    ]
    return tren, val


def comparacion() -> dict | None:
    if not COMPARACION.exists():
        return None
    return json.loads(COMPARACION.read_text(encoding="utf-8"))


def familias(reporte: Path) -> list[tuple[str, str, str]]:
    """Lee la tabla de familias del REPORTE.md del dataset."""
    if not reporte.exists():
        return []
    filas = []
    for linea in reporte.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\|\s*`(\w+)`\s*\|\s*(\d+)\s*\|\s*(.*?)\s*\|", linea)
        if m:
            filas.append((m.group(1), m.group(2), m.group(3)))
    return filas


def incrustar(ruta: Path) -> str | None:
    """Imagen como data URI: la presentacion tiene que viajar en un solo fichero."""
    if not ruta.exists():
        return None
    tipo = {"png": "png", "jpg": "jpeg", "jpeg": "jpeg", "webp": "webp"}[
        ruta.suffix.lstrip(".").lower()
    ]
    return f"data:image/{tipo};base64,{base64.b64encode(ruta.read_bytes()).decode()}"


# --- grafico ------------------------------------------------------------------


def svg_perdida(tren: list[tuple[int, float]], val: list[tuple[int, float]]) -> str:
    """
    La curva de perdida, en SVG y desde los numeros del log.

    Se dibuja a mano en vez de con una libreria porque son dos series y cuarenta
    puntos: una dependencia mas no se paga sola, y asi hereda los colores del
    tema (claro y oscuro) sin re-renderizar nada.
    """
    if not tren:
        return '<p class="bajada">Sin log de entrenamiento todavia.</p>'

    an, al = 900, 340
    mx, my, mb = 54, 16, 38
    xs = [i for i, _ in tren] + [i for i, _ in val]
    ys = [p for _, p in tren] + [p for _, p in val]
    x0, x1 = min(xs), max(xs)
    y0, y1 = 0.0, max(ys) * 1.08

    def px(i: int) -> float:
        return mx + (i - x0) / max(1, x1 - x0) * (an - mx - 18)

    def py(p: float) -> float:
        return my + (1 - (p - y0) / (y1 - y0)) * (al - my - mb)

    def linea(puntos, color, ancho):
        d = " ".join(
            f"{'M' if n == 0 else 'L'}{px(i):.1f},{py(p):.1f}"
            for n, (i, p) in enumerate(puntos)
        )
        return (
            f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{ancho}" '
            'stroke-linejoin="round" stroke-linecap="round"/>'
        )

    partes = [
        f'<svg viewBox="0 0 {an} {al}" role="img" '
        'aria-label="Curva de perdida del entrenamiento" style="width:100%;height:auto">'
    ]
    # rejilla, recesiva
    for k in range(5):
        p = y1 * k / 4
        y = py(p)
        partes.append(
            f'<line x1="{mx}" y1="{y:.1f}" x2="{an - 18}" y2="{y:.1f}" '
            'stroke="var(--linea)" stroke-width="1"/>'
        )
        partes.append(
            f'<text x="{mx - 9}" y="{y + 4:.1f}" text-anchor="end" font-size="13" '
            f'fill="var(--grafito)" font-family="ui-monospace,monospace">{p:.1f}</text>'
        )
    for i in {x0, *[x for x, _ in val], x1}:
        partes.append(
            f'<text x="{px(i):.1f}" y="{al - 12}" text-anchor="middle" font-size="13" '
            f'fill="var(--grafito)" font-family="ui-monospace,monospace">{i}</text>'
        )

    partes.append(linea(tren, "var(--grafito)", 2))
    if val:
        partes.append(linea(val, "var(--sello)", 2.5))
        for i, p in val:
            partes.append(
                f'<circle cx="{px(i):.1f}" cy="{py(p):.1f}" r="4.5" fill="var(--sello)" '
                'stroke="var(--superficie)" stroke-width="2"/>'
            )
    partes.append("</svg>")
    return "".join(partes)


# --- laminas -------------------------------------------------------------------


def e(t: str) -> str:
    return html.escape(t or "")


def lamina(n: int, seccion: str, cuerpo: str, pie: str, total: int, clase: str = "") -> str:
    return f'''<section class="lamina{clase}">
  <header class="membrete"><span class="cod">NormIA · {n:02d}</span><span class="sec">{e(seccion)}</span></header>
  <div class="cuerpo">
{cuerpo}
  </div>
  <footer class="pie"><span>{e(pie)}</span><span class="num">{n:02d} / {total}</span></footer>
</section>'''


def main() -> int:
    if not ORIGEN.exists():
        print(f"falta {ORIGEN}: de ahi sale la hoja de estilo")
        return 1

    fuente = ORIGEN.read_text(encoding="utf-8")
    estilo = re.search(r"<style>(.*?)</style>", fuente, re.S).group(1)
    guion = re.search(r"<script>(.*?)</script>", fuente, re.S).group(1)

    tren, val = curva_de_perdida(LOG)
    cmp_ = comparacion()
    fam = familias(RAIZ / "data" / "finetune" / "empresa-demo-iso" / "REPORTE.md")

    print(f"  curva: {len(tren)} puntos de entrenamiento, {len(val)} de validacion")
    print(f"  comparacion: {'si' if cmp_ else 'NO -- corre comparar_estados.py'}")
    print(f"  familias del dataset: {len(fam)}")
    imgs = sorted(IMAGENES.glob("*.png")) if IMAGENES.exists() else []
    print(f"  imagenes generadas: {len(imgs)}")

    # El cuerpo de las laminas se escribe en construir_laminas para que este
    # fichero se lea de arriba abajo.
    from scripts._laminas_fase2 import construir_laminas

    cuerpos = construir_laminas(
        tren=tren, val=val, comparacion=cmp_, familias=fam,
        imagenes=imgs, svg=svg_perdida(tren, val),
        incrustar=incrustar, lamina=lamina,
    )

    salida = f'''<!doctype html>
<html lang="es" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>NormIA · Fase 2 · Fine-tuning con QLoRA</title>
<style>{estilo}</style>
</head>
<body>
<div class="solo-lector" id="anuncio" aria-live="polite"></div>
{cuerpos}
<div class="barra" id="barra"></div>
<nav class="nav">
  <button id="ant" aria-label="Lamina anterior">&#8249;</button>
  <button id="sig" aria-label="Lamina siguiente">&#8250;</button>
</nav>
<script>{guion}</script>
</body>
</html>'''

    DESTINO.write_text(salida, encoding="utf-8")
    print(f"\n  {DESTINO}  ({len(salida) / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
