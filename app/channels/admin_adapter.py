"""
API de administracion documental.

Contrato con la UI:

    POST   /api/admin/documents        subir N archivos -> devuelve un job por archivo
    GET    /api/admin/jobs             estado de los jobs (la UI hace polling)
    POST   /api/admin/jobs/{id}/apply  aplicar la accion que sugirio un aviso
    DELETE /api/admin/jobs/{id}        descartar un job y su archivo
    GET    /api/admin/documents        biblioteca agrupada por area
    PATCH  /api/admin/documents/{id}   cambiar estado vigente/obsoleto
    DELETE /api/admin/documents/{id}   eliminar documento y sus chunks

El upload NO procesa: guarda el archivo, crea el job y responde. El trabajo
pesado (membrete, validacion, texto, embeddings) va a BackgroundTasks, para que
subir veinte PDFs no deje la pantalla colgada.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.core.docs.pipeline import apply_advice_action, process_job
from app.db.session import get_db
from app.security import require_admin_key
from app.models.db_models import (
    Document,
    DocumentChunk,
    Escalation,
    Incident,
    UploadJob,
)

logger = logging.getLogger(__name__)

# La dependencia va en el router entero: si se anade un endpoint nuevo queda
# protegido sin que nadie tenga que acordarse.
router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_key)],
)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB por archivo
UPLOAD_DIR = Path("data/knowledge_base")


def _safe_stem(name: str) -> str:
    """
    Nombre de archivo seguro para guardar en disco.

    El nombre viene del cliente, asi que se normaliza a ASCII y se reduce a
    [a-z0-9_]: nada de separadores de ruta ni de '..' que se escapen del
    directorio del tenant.
    """
    normalized = unicodedata.normalize("NFKD", Path(name).stem).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", normalized.lower()).strip("_")[:80] or "documento"


class ApplyActionRequest(BaseModel):
    action: str
    document_ids: list[int] = Field(default_factory=list)


class EscalationPatch(BaseModel):
    status: str | None = None
    assigned_to: str | None = None
    resolution: str | None = None


class DocumentPatch(BaseModel):
    status: str | None = None
    title: str | None = None
    area: str | None = None


# Endpoints sincronos (`def`): SQLAlchemy es sincrono y FastAPI los despacha a
# threadpool. Con `async def` bloquearian el event loop.
@router.post("/documents", status_code=status.HTTP_202_ACCEPTED)
def upload_documents(
    background: BackgroundTasks,
    files: list[UploadFile] = File(...),
    tenant_id: str = Form(default=""),
    area: str = Form(default=""),
    db: Session = Depends(get_db),
) -> dict:
    """Recibe los archivos, encola su procesamiento y responde de inmediato."""
    tenant = tenant_id or settings.default_tenant_id
    tenant_dir = UPLOAD_DIR / tenant
    tenant_dir.mkdir(parents=True, exist_ok=True)

    jobs: list[dict] = []
    rejected: list[dict] = []

    for upload in files:
        original = upload.filename or "documento.pdf"

        if not original.lower().endswith(".pdf"):
            rejected.append({"filename": original, "reason": "Solo se aceptan archivos PDF."})
            continue

        payload = upload.file.read()
        if len(payload) > MAX_UPLOAD_BYTES:
            rejected.append(
                {
                    "filename": original,
                    "reason": f"Supera el limite de {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
                }
            )
            continue
        if not payload:
            rejected.append({"filename": original, "reason": "El archivo esta vacio."})
            continue

        stem = _safe_stem(original)
        destination = tenant_dir / f"{stem}.pdf"
        n = 2
        while destination.exists():
            destination = tenant_dir / f"{stem}_{n}.pdf"
            n += 1
        destination.write_bytes(payload)

        job = UploadJob(
            tenant_id=tenant,
            original_filename=original,
            stored_path=str(destination),
            status="pendiente",
            title=Path(original).stem,
            area=area or None,
        )
        db.add(job)
        db.flush()
        jobs.append({"job_id": job.id, "filename": original})

    db.commit()

    for job in jobs:
        background.add_task(process_job, job["job_id"])

    logger.info(
        "upload.queued",
        extra={"tenant_id": tenant, "queued": len(jobs), "rejected": len(rejected)},
    )
    return {"queued": jobs, "rejected": rejected}


@router.get("/jobs")
def list_jobs(
    tenant_id: str = "", limit: int = 50, db: Session = Depends(get_db)
) -> dict:
    """Estado de los jobs recientes. La UI consulta esto mientras haya alguno en curso."""
    tenant = tenant_id or settings.default_tenant_id

    rows = (
        db.query(UploadJob)
        .filter(UploadJob.tenant_id == tenant)
        .order_by(UploadJob.created_at.desc(), UploadJob.id.desc())
        .limit(limit)
        .all()
    )

    jobs = [
        {
            "id": j.id,
            "filename": j.original_filename,
            "status": j.status,
            "code": j.resolved_code,
            "version": j.resolved_version,
            "document_id": j.document_id,
            "chunks": j.chunks_created,
            "advices": j.advices or [],
            "error": j.error,
        }
        for j in rows
    ]

    return {
        "jobs": jobs,
        "pending": sum(1 for j in jobs if j["status"] in ("pendiente", "procesando")),
        "needs_review": sum(1 for j in jobs if j["status"] == "requiere_revision"),
    }


@router.post("/jobs/{job_id}/apply")
def apply_action(
    job_id: int, payload: ApplyActionRequest, db: Session = Depends(get_db)
) -> dict:
    job = db.get(UploadJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job no encontrado.")

    result = apply_advice_action(db, payload.action, payload.document_ids, job.tenant_id)

    if result["applied"]:
        # El aviso resuelto se retira de la lista; si no queda ninguno, el job cierra.
        restantes = [a for a in (job.advices or []) if a.get("action") != payload.action]
        job.advices = restantes
        if not any(a.get("severity") in ("error", "warning") for a in restantes):
            job.status = "listo" if job.document_id else job.status
        db.commit()

    return result


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(job_id: int, db: Session = Depends(get_db)) -> None:
    """Descarta un job y borra su archivo: el flujo de 'quitalo y vuelve a cargarlo'."""
    job = db.get(UploadJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job no encontrado.")

    stored = Path(job.stored_path)
    if stored.exists():
        stored.unlink()

    # Solo se borra el documento si fue ESTE job el que lo creo. Si el job
    # actualizo un documento que ya estaba en el SGC, descartarlo retiraria un
    # documento controlado que nadie pidio eliminar.
    if job.document_id and job.created_document:
        document = db.get(Document, job.document_id)
        if document is not None:
            db.delete(document)  # cascade borra sus chunks

    db.delete(job)
    db.commit()


@router.get("/documents")
def list_documents(tenant_id: str = "", db: Session = Depends(get_db)) -> dict:
    """Biblioteca completa, agrupada por area y con el conteo de chunks de cada documento."""
    tenant = tenant_id or settings.default_tenant_id

    chunk_counts = (
        db.query(DocumentChunk.document_id, func.count(DocumentChunk.id).label("n"))
        .group_by(DocumentChunk.document_id)
        .subquery()
    )

    rows = (
        db.query(Document, func.coalesce(chunk_counts.c.n, 0))
        .outerjoin(chunk_counts, Document.id == chunk_counts.c.document_id)
        .filter(Document.tenant_id == tenant)
        .order_by(Document.area, Document.code, Document.version)
        .all()
    )

    areas: dict[str, list[dict]] = {}
    for doc, chunks in rows:
        areas.setdefault(doc.area or "Sin area", []).append(
            {
                "id": doc.id,
                "code": doc.code,
                "version": doc.version,
                "title": doc.title,
                "status": doc.status,
                "effective_date": doc.effective_date.isoformat() if doc.effective_date else None,
                "chunks": int(chunks),
                "source_filename": doc.source_filename,
            }
        )

    total = sum(len(v) for v in areas.values())
    vigentes = sum(1 for v in areas.values() for d in v if d["status"] == "vigente")

    # Un mismo codigo con dos versiones vigentes es una no conformidad de control
    # documental: se resalta aparte porque el asistente citaria ambas.
    por_codigo: dict[str, list[dict]] = {}
    for lista in areas.values():
        for doc in lista:
            if doc["status"] == "vigente":
                por_codigo.setdefault(doc["code"], []).append(doc)
    conflictos = [
        {
            "code": code,
            "versions": [d["version"] for d in docs],
            "document_ids": [d["id"] for d in docs],
        }
        for code, docs in por_codigo.items()
        if len(docs) > 1
    ]

    return {
        "areas": areas,
        "totals": {
            "documents": total,
            "vigentes": vigentes,
            "obsoletos": total - vigentes,
            "chunks": sum(d["chunks"] for v in areas.values() for d in v),
        },
        "conflicts": conflictos,
    }


@router.patch("/documents/{document_id}")
def patch_document(
    document_id: int, payload: DocumentPatch, db: Session = Depends(get_db)
) -> dict:
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Documento no encontrado.")

    if payload.status is not None:
        if payload.status not in ("vigente", "obsoleto"):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "El estado solo puede ser 'vigente' u 'obsoleto'.",
            )
        document.status = payload.status
    if payload.title is not None:
        document.title = payload.title
    if payload.area is not None:
        document.area = payload.area

    db.commit()
    logger.info(
        "admin.document_patched",
        extra={"document_id": document_id, "status": document.status},
    )
    return {"id": document.id, "status": document.status, "title": document.title}


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(document_id: int, db: Session = Depends(get_db)) -> None:
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Documento no encontrado.")
    db.delete(document)  # cascade borra sus chunks
    db.commit()
    logger.info("admin.document_deleted", extra={"document_id": document_id})


# --- Escalaciones -----------------------------------------------------------
#
# Antes de que existieran estos endpoints, `escalate_to_quality` solo escribia
# una linea de log: el bot afirmaba haber derivado la consulta y nadie la
# recibia. La cola tambien es la mejor fuente de mejora del SGC -- son las
# preguntas reales que la documentacion no cubre.

ESCALATION_STATUSES = ("pendiente", "en_revision", "resuelta", "descartada")

TRIGGER_LABELS = {
    "sin_contexto": "Sin respaldo documental",
    "fuera_de_alcance": "Fuera de alcance",
    "error": "Fallo tecnico",
}


@router.get("/escalations")
def list_escalations(
    tenant_id: str = "",
    status: str = "",
    limit: int = 100,
    db: Session = Depends(get_db),
) -> dict:
    """Cola de consultas derivadas al Responsable de Calidad."""
    tenant = tenant_id or settings.default_tenant_id

    consulta = db.query(Escalation).filter(Escalation.tenant_id == tenant)
    if status:
        consulta = consulta.filter(Escalation.status == status)

    filas = (
        consulta.order_by(Escalation.created_at.desc(), Escalation.id.desc())
        .limit(limit)
        .all()
    )

    por_estado: dict[str, int] = {}
    for (estado, total) in (
        db.query(Escalation.status, func.count(Escalation.id))
        .filter(Escalation.tenant_id == tenant)
        .group_by(Escalation.status)
        .all()
    ):
        por_estado[estado] = total

    return {
        "escalations": [
            {
                "id": e.id,
                "question": e.question,
                "reason": e.reason,
                "trigger": e.trigger,
                "trigger_label": TRIGGER_LABELS.get(e.trigger, e.trigger),
                "status": e.status,
                "channel": e.channel,
                "assigned_to": e.assigned_to,
                "resolution": e.resolution,
                "created_at": e.created_at.isoformat() if e.created_at else None,
                "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
            }
            for e in filas
        ],
        "counts": {estado: por_estado.get(estado, 0) for estado in ESCALATION_STATUSES},
        "pending": por_estado.get("pendiente", 0),
    }


@router.patch("/escalations/{escalation_id}")
def patch_escalation(
    escalation_id: int, payload: EscalationPatch, db: Session = Depends(get_db)
) -> dict:
    escalation = db.get(Escalation, escalation_id)
    if escalation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Escalacion no encontrada.")

    if payload.status is not None:
        if payload.status not in ESCALATION_STATUSES:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Estado invalido. Se acepta: {list(ESCALATION_STATUSES)}",
            )
        escalation.status = payload.status
        # Cerrar una escalacion sella la fecha; reabrirla la limpia, para que el
        # historial no afirme que se resolvio algo que sigue abierto.
        escalation.resolved_at = (
            func.now() if payload.status in ("resuelta", "descartada") else None
        )
    if payload.assigned_to is not None:
        escalation.assigned_to = payload.assigned_to or None
    if payload.resolution is not None:
        escalation.resolution = payload.resolution or None

    db.commit()
    logger.info(
        "admin.escalation_patched",
        extra={"escalation_id": escalation_id, "status": escalation.status},
    )
    return {"id": escalation.id, "status": escalation.status}


@router.get("/incidents")
def list_incidents(limit: int = 50, db: Session = Depends(get_db)) -> dict:
    """Fallos tecnicos recientes, con la referencia que se le mostro al usuario."""
    filas = (
        db.query(Incident)
        .order_by(Incident.created_at.desc(), Incident.id.desc())
        .limit(limit)
        .all()
    )
    return {
        "incidents": [
            {
                "reference": i.reference,
                "kind": i.kind,
                "detail": i.detail,
                "created_at": i.created_at.isoformat() if i.created_at else None,
            }
            for i in filas
        ]
    }
