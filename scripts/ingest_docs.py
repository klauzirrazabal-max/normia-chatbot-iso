"""Ingesta los documentos ISO de un tenant. Idempotente: reemplaza, no duplica."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.core.rag.embeddings import warmup  # noqa: E402
from app.core.rag.ingestion import ingest_tenant  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402
from app.models.db_models import Tenant  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingesta documentos ISO de un tenant")
    parser.add_argument("--tenant", default=settings.default_tenant_id)
    parser.add_argument("--base-dir", default="data/knowledge_base")
    parser.add_argument(
        "--append",
        action="store_true",
        help="Agrega chunks sin borrar los existentes (por defecto se reemplazan)",
    )
    args = parser.parse_args()

    configure_logging(settings.log_level)
    warmup()

    with SessionLocal() as db:
        if db.get(Tenant, args.tenant) is None:
            print(
                f"ERROR: el tenant '{args.tenant}' no existe.\n"
                "Corre primero: uv run python scripts/seed_demo_tenant.py",
                file=sys.stderr,
            )
            return 1

        report = ingest_tenant(
            args.tenant, db, base_dir=args.base_dir, replace=not args.append
        )

    print()
    print(report.summary())
    return 0 if report.chunks_created else 1


if __name__ == "__main__":
    raise SystemExit(main())
