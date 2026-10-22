"""
Adapter del canal web (widget embebible).

Su unica responsabilidad es traducir entre el contrato HTTP del widget y el
formato interno comun (IncomingMessage / BotResponse). Toda la logica vive en
el orquestador, que es lo que permite agregar WhatsApp despues sin refactorizar.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.channels.auth_adapter import usuario_actual
from app.core import orchestrator
from app.db.session import get_db
from app.models.db_models import User
from app.models.schemas import IncomingMessage, WebChatRequest, WebChatResponse
from app.security import check_rate_limit, resolve_tenant

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["web"])


# Endpoint sincrono (`def`, no `async def`) a proposito: SQLAlchemy y httpx son
# sincronos aqui. Con `async def` bloquearian el event loop y el servidor
# atenderia una sola conversacion a la vez. Con `def`, FastAPI lo despacha a un
# threadpool y mantiene la concurrencia.
@router.post("/chat", response_model=WebChatResponse)
def chat(
    payload: WebChatRequest,
    request: Request,
    db: Session = Depends(get_db),
    usuario: User | None = Depends(usuario_actual),
) -> WebChatResponse:
    check_rate_limit(request)

    # Quien ha entrado consulta SU sistema de gestion.
    #
    # `resolve_tenant` sirve el corpus publico e ignora lo que pida el cliente:
    # es lo correcto para una demostracion abierta, donde nadie tiene cuenta y
    # el identificador de un cliente no es un secreto. Pero con sesion, servir
    # el corpus publico a alguien de ABISERVI es responderle con documentos
    # que no son los suyos.
    #
    # El cuerpo de la peticion no decide nunca: o manda la sesion, o manda la
    # configuracion.
    tenant_id = (
        usuario.tenant_id if usuario is not None else resolve_tenant(payload.tenant_id)
    )

    msg = IncomingMessage(
        tenant_id=tenant_id,
        channel="web",
        # Con sesion, la conversacion es de la PERSONA y no del navegador: se
        # recupera desde otro equipo y queda atribuida a quien pregunto.
        external_user_id=(f"u{usuario.id}" if usuario else payload.session_id),
        text=payload.message,
    )

    try:
        response = orchestrator.handle_message(db, msg)
    except Exception:
        db.rollback()
        logger.exception("web.chat_failed", extra={"tenant_id": tenant_id})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No se pudo procesar el mensaje.",
        ) from None

    return WebChatResponse(
        reply=response.text,
        citations=response.citations,
        escalate=response.escalate,
        grounded=response.grounded,
        suggestions=response.suggestions,
        solicitud_imagen=response.solicitud_imagen,
    )
