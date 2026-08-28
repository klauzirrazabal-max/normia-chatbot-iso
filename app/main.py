from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from sqlalchemy import text

from app.channels import admin_adapter, web_adapter
from app.config import settings
from app.core.rag.embeddings import warmup
from app.db.session import engine
from app.logging_config import configure_logging
from app.services.llm_client import get_llm_client

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(settings.log_level)
    logger.info(
        "app.starting",
        extra={"env": settings.app_env, "tenant": settings.default_tenant_id},
    )

    # Precarga del modelo de embeddings. Sin esto, la primera pregunta del
    # usuario paga ~20-30 s de carga desde disco -- suficiente para que un
    # webhook de WhatsApp expire y para arruinar una demo en vivo.
    # Ademas valida que la dimension del modelo coincida con la columna vector(N).
    warmup()

    logger.info("app.ready", extra={"port": settings.app_port})
    yield
    logger.info("app.stopping")


app = FastAPI(
    title="NormIA",
    description="Asistente de documentacion y cumplimiento ISO (RAG + agentes)",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type"],
)

app.include_router(web_adapter.router)
app.include_router(admin_adapter.router)


@app.get("/health", tags=["infra"])
def health() -> dict[str, object]:
    """Chequeo de las tres dependencias: base de datos, LLM y embeddings."""
    checks: dict[str, object] = {}

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 - el health check reporta, no propaga
        checks["database"] = f"error: {exc}"

    client = get_llm_client()
    checks["llm"] = "ok" if client.health() else "unreachable"
    checks["llm_model"] = client.model
    checks["llm_base_url"] = client.base_url

    from app.core.rag import embeddings

    checks["embeddings"] = "ok" if embeddings._model is not None else "not_loaded"

    checks["status"] = (
        "ok" if checks["database"] == "ok" and checks["llm"] == "ok" else "degraded"
    )
    return checks


# El widget y el panel se sirven desde la propia API, no desde otro puerto.
# Asi el navegador del visitante habla con el mismo origen del que descargo la
# pagina: sin esto, `api-url="http://localhost:8000"` apuntaria al localhost DE
# QUIEN VISITA. Ademas hace innecesario el CORS.
@app.get("/", include_in_schema=False)
def raiz() -> RedirectResponse:
    """
    La raiz lleva al chat.

    Sin esto, entrar al dominio pelado devuelve {"detail":"Not Found"}: el
    montaje estatico busca un index.html que no existe. Y entrar al dominio
    pelado es lo primero que hace cualquiera a quien le pasas el enlace.
    """
    return RedirectResponse(url="/demo.html")


_FRONTEND = Path(__file__).resolve().parent.parent / "frontend-widget"
if _FRONTEND.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND), html=True), name="frontend")
