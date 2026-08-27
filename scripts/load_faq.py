"""
Carga el FAQ generado a la base, con embeddings de las PREGUNTAS.

Se vectoriza la pregunta y no la respuesta porque lo que llega del usuario
tambien es una pregunta. Ahi esta la ganancia: la clausula dice "El Jefe de TI
informa al solicitante el estado de la solicitud" y la persona escribe "quien me
avisa como va mi solicitud?" -- semanticamente lejos. La entrada del FAQ esta
escrita como pregunta, asi que coincide.

La carga es idempotente: reemplaza las entradas del tenant en una sola
transaccion. Correrla dos veces no duplica nada, y si falla a la mitad no deja el
FAQ a medio cargar.

Uso:
    uv run python scripts/load_faq.py --tenant empresa-demo-iso
    uv run python scripts/load_faq.py --archivo data/faq/faq_empresa-demo-iso.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.core.rag.embeddings import embed_texts, warmup  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402
from app.models.db_models import Document, DocumentChunk, FaqEntry  # noqa: E402

LOTE_EMBEDDINGS = 64


def main() -> int:
    parser = argparse.ArgumentParser(description="Carga el FAQ generado a la base")
    parser.add_argument("--tenant", default=settings.default_tenant_id)
    parser.add_argument("--archivo", default=None)
    args = parser.parse_args()

    configure_logging(settings.log_level)

    ruta = Path(args.archivo or f"data/faq/faq_{args.tenant}.json")
    if not ruta.exists():
        print(f"No encontre {ruta}. Genera el FAQ primero con generate_faq.py")
        return 1

    datos = json.loads(ruta.read_text(encoding="utf-8"))
    entradas = datos.get("entradas", [])
    if not entradas:
        print("El archivo no tiene entradas.")
        return 1

    warmup()
    print(f"{len(entradas)} entradas en {ruta}\n")

    with SessionLocal() as db:
        # Indice de documentos y clausulas del tenant, para enlazar cada entrada
        # con su origen. La clave foranea es lo que hace automatica la
        # invalidacion: si el documento se borra o pasa a obsoleto, sus preguntas
        # dejan de recuperarse sin proceso aparte.
        documentos = {
            doc.code.split(" (")[0].upper(): doc
            for doc in db.query(Document).filter_by(tenant_id=args.tenant).all()
        }
        chunks = {
            (doc_code, seccion): chunk_id
            for chunk_id, seccion, doc_code in db.query(
                DocumentChunk.id, DocumentChunk.section, Document.code
            )
            .join(Document, DocumentChunk.document_id == Document.id)
            .filter(Document.tenant_id == args.tenant)
            .all()
        }

        validas = []
        sin_documento = []
        for e in entradas:
            base = e["codigo"].split(" (")[0].upper()
            doc = documentos.get(base)
            if doc is None:
                sin_documento.append(e["codigo"])
                continue
            validas.append((e, doc))

        if not validas:
            print("Ninguna entrada corresponde a un documento registrado.")
            return 1

        print("Calculando embeddings de las preguntas...")
        vectores: list[list[float]] = []
        for i in range(0, len(validas), LOTE_EMBEDDINGS):
            lote = [e["pregunta"] for e, _ in validas[i : i + LOTE_EMBEDDINGS]]
            vectores.extend(embed_texts(lote))
            print(f"  {min(i + LOTE_EMBEDDINGS, len(validas))}/{len(validas)}")

        # Reemplazo completo en una transaccion: correrlo dos veces no duplica, y
        # un fallo a la mitad no deja el FAQ a medias.
        borradas = db.query(FaqEntry).filter_by(tenant_id=args.tenant).delete()

        vistas: set[tuple[int, str]] = set()
        insertadas = 0
        for (e, doc), vector in zip(validas, vectores, strict=True):
            clave = (doc.id, e["pregunta"])
            if clave in vistas:  # la tabla tiene UNIQUE(tenant, documento, pregunta)
                continue
            vistas.add(clave)

            db.add(
                FaqEntry(
                    tenant_id=args.tenant,
                    document_id=doc.id,
                    chunk_id=chunks.get((doc.code, e.get("seccion"))),
                    section=e.get("seccion"),
                    question=e["pregunta"],
                    answer=e["respuesta"],
                    embedding=vector,
                )
            )
            insertadas += 1

        db.commit()

    print()
    print(f"Entradas anteriores borradas : {borradas}")
    print(f"Entradas cargadas            : {insertadas}")
    if sin_documento:
        print(f"Sin documento registrado     : {len(sin_documento)}")
        for codigo in sorted(set(sin_documento))[:5]:
            print(f"  - {codigo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
