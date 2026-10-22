"""
El estado del sistema de gestion, en una llamada.

Existe porque faltaba la pregunta mas basica: "¿como esta mi SGC ahora
mismo?". Habia panel de documentos, bandeja de escalaciones y lista de
hallazgos, pero nada que juntara las tres y dijera que es lo que pide
atencion hoy.

Devuelve lo que ESTE usuario puede ver. A un operario no se le cuentan las
no conformidades abiertas de toda la empresa: no es su trabajo y no es su
informacion.
"""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.channels.auth_adapter import exigir_usuario
from app.config import settings
from app.core.auth import puede_gestionar
from app.db.session import get_db
from app.models.db_models import CapaAction, Document, Escalation, Finding, User
from app.security import resolve_tenant

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["estado"])


@router.get("/estado")
def estado(
    usuario: User = Depends(exigir_usuario),
    db: Session = Depends(get_db),
) -> dict:
    tenant = resolve_tenant(usuario.tenant_id or settings.default_tenant_id)

    documentos = dict(
        db.query(Document.status, func.count(Document.id))
        .filter(Document.tenant_id == tenant)
        .group_by(Document.status)
        .all()
    )

    base = {
        "tenant": tenant,
        "usuario": {"nombre": usuario.nombre, "rol": usuario.rol},
        "documentos": {
            "vigentes": documentos.get("vigente", 0),
            "obsoletos": documentos.get("obsoleto", 0),
        },
    }

    if not puede_gestionar(usuario.rol):
        # Un operario ve lo suyo: lo que el reporto. El resto no le incumbe.
        base["mios"] = {
            "hallazgos": db.query(func.count(Finding.id))
            .filter(Finding.tenant_id == tenant, Finding.registrado_por == usuario.nombre)
            .scalar()
            or 0
        }
        return base

    sin_accion = (
        db.query(func.count(Finding.id))
        .filter(
            Finding.tenant_id == tenant,
            Finding.status != "cerrado",
            ~Finding.id.in_(db.query(CapaAction.finding_id)),
        )
        .scalar()
        or 0
    )

    # Una accion correctiva vencida es lo mas urgente que puede haber aqui: el
    # plazo se comprometio y paso.
    vencidas = (
        db.query(func.count(CapaAction.id))
        .join(Finding, Finding.id == CapaAction.finding_id)
        .filter(
            Finding.tenant_id == tenant,
            CapaAction.status != "completado",
            CapaAction.due_date < date.today(),
        )
        .scalar()
        or 0
    )

    escalaciones = dict(
        db.query(Escalation.status, func.count(Escalation.id))
        .filter(Escalation.tenant_id == tenant)
        .group_by(Escalation.status)
        .all()
    )

    base["calidad"] = {
        "hallazgos_sin_accion": sin_accion,
        "acciones_vencidas": vencidas,
        "escalaciones_pendientes": escalaciones.get("pendiente", 0),
        # Lo ofrecido y no aceptado: el inventario de lo que el SGC no cubre.
        "huecos_detectados": escalaciones.get("propuesta", 0),
    }
    return base
