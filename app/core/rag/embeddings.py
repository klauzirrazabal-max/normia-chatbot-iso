"""
Modelo de embeddings local (sin API key, sin costo, sin salida a internet).

Se carga una sola vez y se reutiliza. `warmup()` se llama en el arranque de
FastAPI para que la primera pregunta del usuario no pague los ~20-30 s de
carga del modelo desde disco.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

from app.config import settings

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

_model: SentenceTransformer | None = None
_lock = threading.Lock()


class EmbeddingDimensionMismatch(RuntimeError):
    """El modelo de embeddings no produce la dimension configurada en EMBEDDING_DIM."""


def get_model() -> SentenceTransformer:
    """Carga perezosa y thread-safe del modelo."""
    global _model
    if _model is None:
        with _lock:
            if _model is None:  # doble chequeo: otro hilo pudo cargarlo mientras esperabamos
                from sentence_transformers import SentenceTransformer

                started = time.perf_counter()
                logger.info("embeddings.loading", extra={"model": settings.embedding_model})
                _model = SentenceTransformer(settings.embedding_model)
                logger.info(
                    "embeddings.loaded",
                    extra={
                        "model": settings.embedding_model,
                        "elapsed_s": round(time.perf_counter() - started, 2),
                    },
                )
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Vectoriza una lista de textos. Los vectores salen normalizados (norma 1),
    que es lo que hace que la distancia coseno de pgvector sea comparable
    entre consultas.
    """
    if not texts:
        return []
    model = get_model()
    vectors = model.encode(texts, normalize_embeddings=True)
    return [v.tolist() for v in vectors]


def embed_query(text: str) -> list[float]:
    return embed_texts([text])[0]


def warmup() -> int:
    """
    Carga el modelo y valida que su dimension coincida con EMBEDDING_DIM.

    Falla rapido y con un mensaje claro: un desajuste aqui produciria errores
    opacos de Postgres al insertar en la columna vector(N).
    """
    dim = len(embed_query("calibracion de instrumentos de medicion"))
    if dim != settings.embedding_dim:
        raise EmbeddingDimensionMismatch(
            f"El modelo '{settings.embedding_model}' produce vectores de {dim} dimensiones, "
            f"pero EMBEDDING_DIM={settings.embedding_dim} y la columna SQL es vector("
            f"{settings.embedding_dim}). Ajusta EMBEDDING_DIM y la migracion 001_init.sql, "
            "y recrea la base con: docker compose down -v && docker compose up -d"
        )
    logger.info("embeddings.warmup_ok", extra={"dim": dim})
    return dim
