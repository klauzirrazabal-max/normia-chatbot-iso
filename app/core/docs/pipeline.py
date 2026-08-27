"""
Procesamiento en segundo plano de los documentos subidos.

Cada archivo pasa por: leer membrete -> validar contra lo registrado ->
extraer texto -> chunkear por clausula -> embeddings -> guardar.

Corre fuera del ciclo de la request porque toma segundos por documento; subir
veinte archivos y esperar a que respondan todos se sentiria roto. El upload
responde de inmediato con un job por archivo y la UI consulta el estado.

Un job que termina con avisos queda en 'requiere_revision': el documento SI se
ingesta (para no bloquear el trabajo), pero aparece destacado en la lista para
que Calidad decida. La unica excepcion es un aviso bloqueante -- sin codigo no
hay forma de citar el documento, asi que no entra.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.docs.validation import assess_upload
from app.core.rag.embeddings import embed_texts
from app.core.rag.ingestion import chunk_by_section, extract_full_text
from app.db.session import SessionLocal
from app.models.db_models import Document, DocumentChunk, UploadJob

logger = logging.getLogger(__name__)


def slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", normalized.lower()).strip("_")


def _reindex_document(db: Session, document: Document, pdf_path: Path) -> int:
    """Extrae, chunkea y vectoriza. Reemplaza los chunks previos del documento."""
    db.query(DocumentChunk).filter_by(document_id=document.id).delete()

    full_text = extract_full_text(pdf_path)
    sections = chunk_by_section(full_text)
    if not sections:
        raise ValueError(
            "No se pudo extraer texto util del PDF. Puede ser un documento escaneado "
            "sin OCR: conviertelo a PDF con texto seleccionable y vuelve a subirlo."
        )

    vectors = embed_texts([content for _, content in sections])
    for (section, content), vector in zip(sections, vectors, strict=True):
        db.add(
            DocumentChunk(
                document_id=document.id,
                tenant_id=document.tenant_id,
                section=section,
                content=content,
                embedding=vector,
            )
        )
    return len(sections)


def process_job(job_id: int) -> None:
    """
    Procesa un job. Abre su propia sesion: corre en un hilo aparte, fuera del
    ciclo de vida de la sesion de la request que lo encolo.
    """
    db: Session = SessionLocal()
    try:
        job = db.get(UploadJob, job_id)
        if job is None:
            logger.warning("upload.job_missing", extra={"job_id": job_id})
            return

        job.status = "procesando"
        db.commit()

        pdf_path = Path(job.stored_path)
        assessment = assess_upload(db, job.tenant_id, pdf_path, job.original_filename)

        job.advices = [a.to_dict() for a in assessment.advices]
        job.resolved_code = assessment.resolved_code
        job.resolved_version = assessment.resolved_version

        if assessment.blocking:
            job.status = "requiere_revision"
            db.commit()
            logger.info(
                "upload.blocked",
                extra={"job_id": job_id, "filename": job.original_filename},
            )
            return

        document = (
            db.query(Document)
            .filter_by(
                tenant_id=job.tenant_id,
                code=assessment.resolved_code,
                version=assessment.resolved_version,
            )
            .one_or_none()
        )
        if document is None:
            document = Document(
                tenant_id=job.tenant_id,
                code=assessment.resolved_code,
                version=assessment.resolved_version,
                title=job.title,
                area=job.area,
                effective_date=assessment.header.revision_date or date.today(),
                status="vigente",
                source_filename=pdf_path.name,
            )
            db.add(document)
            db.flush()
            job.created_document = True
        else:
            document.source_filename = pdf_path.name
            if job.title:
                document.title = job.title
            if job.area:
                document.area = job.area

        job.chunks_created = _reindex_document(db, document, pdf_path)
        job.document_id = document.id
        job.status = "requiere_revision" if assessment.needs_review else "listo"
        db.commit()

        logger.info(
            "upload.processed",
            extra={
                "job_id": job_id,
                "code": document.code,
                "version": document.version,
                "chunks": job.chunks_created,
                "status": job.status,
                "advices": len(job.advices),
            },
        )

    except Exception as exc:  # noqa: BLE001 - un archivo malo no debe tumbar el worker
        db.rollback()
        logger.exception("upload.failed", extra={"job_id": job_id})
        job = db.get(UploadJob, job_id)
        if job is not None:
            job.status = "error"
            job.error = str(exc)
            db.commit()
    finally:
        db.close()


def apply_advice_action(db: Session, action: str, document_ids: list[int], tenant_id: str) -> dict:
    """
    Ejecuta la accion que el usuario acepto desde la UI.

    Solo se implementan las acciones que el sistema puede hacer por si mismo.
    Renombrar el archivo fuente o confirmar un codigo son cosas que hace la
    persona, no el servidor.
    """
    if action == "mark_previous_obsolete":
        afectados = (
            db.query(Document)
            .filter(Document.tenant_id == tenant_id, Document.id.in_(document_ids))
            .all()
        )
        for doc in afectados:
            doc.status = "obsoleto"
        db.commit()
        logger.info(
            "upload.marked_obsolete",
            extra={"documents": [f"{d.code} {d.version}" for d in afectados]},
        )
        return {
            "applied": True,
            "message": (
                f"{len(afectados)} documento(s) marcados como obsoletos. El asistente "
                "dejara de citarlos de inmediato."
            ),
        }

    return {
        "applied": False,
        "message": f"La accion '{action}' la debe resolver una persona, no el sistema.",
    }
