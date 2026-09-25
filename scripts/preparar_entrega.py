"""
Arma la carpeta de entrega: lo que se sube a Colab y lo que se presenta.

    uv run python scripts/preparar_entrega.py

Produce `entrega/` con todo lo necesario y nada mas. Se regenera entera cada vez,
asi que no hay que acordarse de actualizar copias sueltas.

    entrega/
      LEEME.md                      que es cada cosa y en que orden
      NormIA_Fase2.ipynb            el notebook, listo para Colab
      normia_dataset.jsonl          584 ejemplos (se sube al panel de archivos)
      presentacion-ejecutiva.html   10 laminas
      presentacion-ejecutiva.pdf    la misma, para imprimir o adjuntar
      imagenes/                     las piezas generadas
      resultados/                   las tablas y el log del entrenamiento

AVISO: `normia_dataset.jsonl` lleva el texto de las clausulas REALES del cliente
dentro de cada prompt. La carpeta `entrega/` no se versiona por eso.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DESTINO = RAIZ / "entrega"
PY = sys.executable


LEEME = """# Entrega · NormIA Fase 2

Fine-tuning de un modelo de lenguaje sobre la documentacion de ABISERVI, y
generacion de material visual con difusion.

## Para ejecutar el notebook en Colab

1. Sube **`NormIA_Fase2.ipynb`** a [colab.research.google.com](https://colab.research.google.com).
2. **Entorno de ejecucion → Cambiar tipo de entorno → GPU** (una T4 basta).
3. Abre el panel de archivos (icono de carpeta, a la izquierda) y **arrastra
   `normia_dataset.jsonl`**.
4. **Entorno de ejecucion → Ejecutar todas.**

Tarda unos 45 minutos: la mayor parte es el entrenamiento y la descarga del
modelo de difusion.

> El dataset son 584 ejemplos derivados de los documentos controlados del
> cliente. No es publico.

## Que contiene el notebook

| # | Apartado |
|---|---|
| 1 | Analisis exploratorio del dataset |
| 2 | Prompt engineering **antes** del fine-tuning |
| 3 | Fine-tuning con Unsloth y metricas de evaluacion |
| 4 | Pipeline de generacion de imagenes con Diffusers |
| 5 | Comparacion: sin entrenar · entrenado · con fine-tuning |

## Para presentar

Abre **`presentacion-ejecutiva.html`** en cualquier navegador y navega con las
flechas. Son 10 laminas y viaja en un solo fichero: las imagenes van dentro.

Si hace falta en PDF, esta **`presentacion-ejecutiva.pdf`**.

## El resto

| Carpeta | Que hay |
|---|---|
| `imagenes/` | Las piezas generadas con SDXL, con el logotipo real compuesto |
| `resultados/` | Las tablas de medicion y el log del entrenamiento |

Todo se reproduce desde el repositorio del proyecto.
"""


def correr(orden: list[str], descripcion: str) -> bool:
    r = subprocess.run(orden, cwd=RAIZ, capture_output=True, text=True)  # noqa: S603
    if r.returncode != 0:
        print(f"  FALLO: {descripcion}\n{r.stdout[-400:]}{r.stderr[-400:]}", file=sys.stderr)
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sin-pdf", action="store_true", help="no intentar generar el PDF")
    args = parser.parse_args()

    print("Regenerando los entregables desde la fuente...")
    for orden, desc in (
        ([PY, "scripts/construir_notebook.py"], "notebook"),
        ([PY, "scripts/construir_ejecutiva.py"], "presentacion ejecutiva"),
        ([PY, "scripts/exportar_dataset_notebook.py"], "dataset"),
    ):
        if not correr(orden, desc):
            return 1

    if DESTINO.exists():
        shutil.rmtree(DESTINO)
    DESTINO.mkdir()
    (DESTINO / "imagenes").mkdir()
    (DESTINO / "resultados").mkdir()

    copias = [
        (RAIZ / "notebooks" / "NormIA_Fase2.ipynb", DESTINO / "NormIA_Fase2.ipynb"),
        (RAIZ / "notebooks" / "normia_dataset.jsonl", DESTINO / "normia_dataset.jsonl"),
        (RAIZ / "docs" / "presentacion-ejecutiva.html", DESTINO / "presentacion-ejecutiva.html"),
    ]
    for origen, destino in copias:
        if not origen.exists():
            print(f"  falta {origen}", file=sys.stderr)
            return 1
        shutil.copy2(origen, destino)

    for pieza in sorted((RAIZ / "docs" / "imagenes").glob("*.png")):
        shutil.copy2(pieza, DESTINO / "imagenes" / pieza.name)

    # Resultados: solo las tablas y el log. El detalle con las respuestas
    # literales se queda fuera -- reproduce el contenido de las clausulas.
    for nombre in (
        "entrenamiento.log", "tres-modelos-empresa-demo-iso.md",
        "banco-4b-base.txt", "banco-4b-lora.txt",
    ):
        origen = RAIZ / "docs" / nombre
        if origen.exists():
            shutil.copy2(origen, DESTINO / "resultados" / nombre)

    (DESTINO / "LEEME.md").write_text(LEEME, encoding="utf-8")

    if not args.sin_pdf:
        pdf = DESTINO / "presentacion-ejecutiva.pdf"
        # Chrome headless imprime el HTML tal cual se ve, con las imagenes
        # embebidas. Es lo unico instalado en cualquier Mac que sepa hacerlo.
        chrome = Path(
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        )
        if chrome.exists():
            subprocess.run(  # noqa: S603
                [str(chrome), "--headless", "--disable-gpu", "--no-pdf-header-footer",
                 f"--print-to-pdf={pdf}", (DESTINO / "presentacion-ejecutiva.html").as_uri()],
                capture_output=True, timeout=120,
            )
        if not pdf.exists():
            print("  (sin PDF: abre el HTML y usa Cmd+P -> Guardar como PDF)")

    total = sum(f.stat().st_size for f in DESTINO.rglob("*") if f.is_file())
    print(f"\n  {DESTINO}  ({total / 1024 / 1024:.1f} MB)\n")
    for f in sorted(DESTINO.rglob("*")):
        if f.is_file():
            print(f"    {f.relative_to(DESTINO)}  ({f.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
