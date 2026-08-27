"""
Adapter del canal web (widget embebible).

Su unica responsabilidad es traducir entre el contrato HTTP del widget y el
formato interno comun (IncomingMessage / BotResponse). Toda la logica vive en
el orquestador, que es lo que permite agregar WhatsApp despues sin refactorizar.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core import orchestrator
from app.db.session import get_db
from app.models.schemas import IncomingMessage, WebChatRequest, WebChatResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["web"])


# Endpoint sincrono (`def`, no `async def`) a proposito: SQLAlchemy y httpx son
# sincronos aqui. Con `async def` bloquearian el event loop y el servidor
# atenderia una sola conversacion a la vez. Con `def`, FastAPI lo despacha a un
# threadpool y mantiene la concurrencia.
@router.post("/chat", response_model=WebChatResponse)
def chat(payload: WebChatRequest, db: Session = Depends(get_db)) -> WebChatResponse:
    msg = IncomingMessage(
        tenant_id=payload.tenant_id,
        channel="web",
        external_user_id=payload.session_id,
        text=payload.message,
    )

    try:
        response = orchestrator.handle_message(db, msg)
    except Exception:
        db.rollback()
        logger.exception("web.chat_failed", extra={"tenant_id": payload.tenant_id})
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
    )
