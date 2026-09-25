"""
Empaqueta el dataset en un solo fichero, listo para subir al notebook.

El notebook corre en Colab, donde no hay ni Postgres ni los PDFs. Necesita el
dataset como UN fichero que se arrastra al panel de archivos.

    uv run python scripts/exportar_dataset_notebook.py

Produce `notebooks/normia_dataset.jsonl` con las tres particiones marcadas, para
que el notebook pueda separarlas sin adivinar.

AVISO SOBRE LOS DATOS: por defecto exporta `empresa-demo-iso`, que son 584
ejemplos con el texto de las clausulas REALES del cliente dentro de cada prompt.
Ese fichero NO se versiona -- el .gitignore lo cubre -- y subirlo a Colab es
sacarlo de la maquina. Con `--tenant demo-publica` exporta el corpus sintetico,
que son 101 ejemplos y si puede viajar.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default="empresa-demo-iso")
    parser.add_argument("--nombre", default="normia_dataset.jsonl")
    parser.add_argument("--salida", type=Path, default=RAIZ / "notebooks")
    args = parser.parse_args()

    origen = RAIZ / "data" / "finetune" / args.tenant
    if not origen.exists():
        print(f"no existe {origen}: corre antes build_finetune_dataset.py")
        return 1

    args.salida.mkdir(parents=True, exist_ok=True)
    destino = args.salida / args.nombre

    cuenta: Counter[str] = Counter()
    familias: Counter[str] = Counter()

    with destino.open("w", encoding="utf-8") as fh:
        for particion in ("train", "valid", "test"):
            ruta = origen / f"{particion}.jsonl"
            if not ruta.exists():
                continue
            for linea in ruta.read_text(encoding="utf-8").splitlines():
                if not linea.strip():
                    continue
                registro = json.loads(linea)
                # La particion viaja DENTRO del registro. En Colab no hay tres
                # ficheros, hay uno, y separarlos ahi de nuevo al azar
                # destruiria el reparto por grupo que evita la fuga.
                registro["particion"] = particion
                ultimo = registro["messages"][-1]
                registro["tipo"] = "tool_call" if ultimo.get("tool_calls") else "texto"
                cuenta[particion] += 1
                familias[registro["tipo"]] += 1
                fh.write(json.dumps(registro, ensure_ascii=False) + "\n")

    total = sum(cuenta.values())
    print(f"\n  {destino}")
    print(f"  {total} ejemplos  ({'; '.join(f'{k}: {v}' for k, v in cuenta.items())})")
    print(f"  {'; '.join(f'{k}: {v}' for k, v in familias.items())}")
    print(f"  {destino.stat().st_size / 1024 / 1024:.1f} MB")
    if total < 500:
        print(
            f"\n  AVISO: el enunciado pide un minimo de 500 ejemplos y hay {total}. "
            f"Con --tenant empresa-demo-iso son 584."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
