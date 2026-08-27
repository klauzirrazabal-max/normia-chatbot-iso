"""
Carga la Lista Maestra de Documentos Internos al registro.

La Lista Maestra es la fuente autoritativa del SGC: que documentos existen, en
que version y desde cuando. Tenerla en la base separa dos preguntas que antes se
confundian -- "existe en el SGC" y "esta indexado por el asistente" -- y esa
diferencia es tanto un arreglo como una metrica.

El arreglo: el verificador de citas daba por inexistentes codigos que si estan
registrados pero cuyos PDFs vienen agrupados (INV-FO-02, 06, 11, 13...), y
descartaba respuestas correctas.

La metrica: cuantos documentos del SGC cubre realmente el asistente.

Uso:
    uv run python scripts/load_registry.py --maestra "/ruta/CAL-FO-01 Lista maestra.pdf"
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.db_models import Document, RegistryEntry  # noqa: E402
from scripts.build_metadata import parse_master_list  # noqa: E402


def _fecha(valor: str | None) -> date | None:
    if not valor:
        return None
    try:
        return datetime.strptime(valor, "%Y-%m-%d").date()
    except ValueError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Carga la Lista Maestra al registro")
    parser.add_argument("--tenant", default=settings.default_tenant_id)
    parser.add_argument("--maestra", required=True, help="PDF de la Lista Maestra")
    args = parser.parse_args()

    ruta = Path(args.maestra).expanduser()
    if not ruta.exists():
        print(f"No encontre {ruta}")
        return 1

    entradas = parse_master_list(ruta)
    print(f"Lista Maestra: {len(entradas)} documentos con version controlada\n")

    with SessionLocal() as db:
        # Reemplazo completo: la Lista Maestra es la verdad, no un acumulado.
        borradas = db.query(RegistryEntry).filter_by(tenant_id=args.tenant).delete()

        for code, datos in entradas.items():
            db.add(
                RegistryEntry(
                    tenant_id=args.tenant,
                    code=code,
                    version=datos["version"],
                    effective_date=_fecha(datos.get("effective_date")),
                )
            )
        db.commit()

        registrados = {e.code for e in db.query(RegistryEntry).filter_by(tenant_id=args.tenant)}
        indexados = {
            d.code.split(" (")[0]
            for d in db.query(Document).filter_by(tenant_id=args.tenant)
        }

    sin_indexar = sorted(registrados - indexados)
    sin_registrar = sorted(indexados - registrados)

    print(f"Entradas anteriores borradas : {borradas}")
    print(f"Entradas cargadas            : {len(entradas)}")
    print()
    print(f"Cobertura del asistente      : {len(registrados & indexados)}/{len(registrados)}")

    if sin_indexar:
        print(f"\nRegistrados pero SIN indexar ({len(sin_indexar)}):")
        print("  El asistente no puede responder sobre estos documentos.")
        for code in sin_indexar:
            print(f"    {code}")

    if sin_registrar:
        print(f"\nIndexados pero NO registrados en la Lista Maestra ({len(sin_registrar)}):")
        print("  Estan bajo el asistente pero fuera del control documental.")
        for code in sin_registrar:
            print(f"    {code}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
