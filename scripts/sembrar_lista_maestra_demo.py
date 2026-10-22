"""
Carga la Lista Maestra del corpus de demostracion desde su metadata.csv.

`load_registry.py` lee la Lista Maestra REAL del cliente, que es un PDF con
tabla. El corpus de demostracion no tiene ese PDF: tiene un CSV, que es la
misma informacion en el formato que produce su generador.

Hace falta porque sin registro, la validacion de ingesta marca cada documento
del tenant de demostracion como "no figura en la Lista Maestra". Es cierto, y
es exactamente lo que no se quiere ensenar: que el SGC de la demostracion esta
descontrolado.

    uv run python scripts/sembrar_lista_maestra_demo.py --tenant demo-publica
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionLocal  # noqa: E402
from app.models.db_models import RegistryEntry  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default="demo-publica")
    parser.add_argument("--base-dir", default="data/knowledge_base")
    args = parser.parse_args()

    csv_path = Path(args.base_dir) / args.tenant / "metadata.csv"
    if not csv_path.exists():
        print(f"no existe {csv_path}: corre antes generate_demo_corpus.py")
        return 1

    db = SessionLocal()
    try:
        borradas = db.query(RegistryEntry).filter_by(tenant_id=args.tenant).delete()
        filas = list(csv.DictReader(csv_path.open(encoding="utf-8")))
        for fila in filas:
            db.add(
                RegistryEntry(
                    tenant_id=args.tenant,
                    code=fila["code"].strip().upper(),
                    version=fila["version"].strip(),
                    title=fila["title"].strip(),
                    process=fila.get("area", "").strip() or None,
                    doc_type=None,
                    # En la Lista Maestra esta columna es la FECHA DE APROBACION.
                    effective_date=date.fromisoformat(fila["effective_date"].strip()),
                    source="metadata_csv",
                )
            )
        db.commit()
        print(f"  Lista Maestra de '{args.tenant}': {len(filas)} entradas"
              + (f" (se reemplazaron {borradas})" if borradas else ""))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
