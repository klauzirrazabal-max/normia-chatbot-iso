"""
Recuperacion de chunks relevantes.

Dos filtros, no uno:

1. Relacional  -> solo documentos con status='vigente' del tenant.
   Es lo que impide citar una version derogada.

2. Semantico   -> solo chunks cuya distancia coseno este por debajo de
   RAG_MAX_DISTANCE.

El filtro (2) es el que hace real al guardrail. Sin el, `ORDER BY distancia
LIMIT k` SIEMPRE devuelve k chunks mientras exista un solo documento en la
base, por irrelevantes que sean: el orquestador nunca ve una lista vacia,
nunca escala, y el bot responde con contexto basura. Ese es exactamente el
escenario "pregunta algo que no esta en tus documentos" de la demo.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace

from sqlalchemy.orm import Session

from app.config import settings
from app.core.rag.embeddings import embed_query
from app.models.db_models import Document, DocumentChunk

logger = logging.getLogger(__name__)


# Clausulas de tramite: existen en TODOS los documentos de un SGC y casi nunca
# contienen la respuesta a una pregunta sustantiva. El problema es que ganan la
# busqueda vectorial: "1. OBJETIVO" y "3. DOCUMENTOS DE REFERENCIA" repiten
# "sistema de gestion de la calidad" de forma densa y corta, mientras la clausula
# que de verdad responde -- "14.2 NUESTROS PROCESOS" -- habla de gestion
# gerencial, facturacion e inventarios y menciona el SGC de pasada.
#
# Preguntando "que es el sistema de calidad", el modelo recibio el objetivo del
# Manual y las normas de referencia, y nunca vio los once procesos que constituyen
# el sistema. Respondio sobre el DOCUMENTO en vez de sobre el sistema.
#
# La solucion principal es que el modelo navegue el indice (ver leer_documento).
# Esta penalizacion es la red para cuando no lo haga.
_TITULOS_DE_TRAMITE = (
    "objetivo",
    "alcance",
    "documentos de referencia",
    "abreviaturas",
    "responsabilidad",
    "control de cambios",
)

# Cuanto se penaliza. Suficiente para que ceda ante contenido operativo, poco
# para que siga ganando si es lo que se pregunto ("cual es el objetivo de X").
BOILERPLATE_PENALTY = 0.06


def _es_clausula_de_tramite(contenido: str) -> bool:
    """True si la clausula es de tramite, mirando su encabezado."""
    encabezado = re.sub(r"^\s*\d+(?:\.\d+)*\.?\s*", "", (contenido or "")[:90]).lower()
    return any(encabezado.startswith(t) for t in _TITULOS_DE_TRAMITE)


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: int
    content: str
    code: str
    version: str
    section: str | None
    distance: float
    title: str | None = None
    effective_date: str | None = None

    @property
    def citation_label(self) -> str:
        base = f"{self.code} {self.version}"
        return f"{base}, seccion {self.section}" if self.section else base

    def to_debug(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "code": self.code,
            "version": self.version,
            "section": self.section,
            "title": self.title,
            "distance": round(self.distance, 4),
        }


@dataclass(frozen=True)
class RetrievedFaq:
    """
    Una pregunta frecuente que coincide con la consulta.

    Trae SIEMPRE el texto de la clausula de origen, no solo su respuesta: si la
    pregunta del usuario no es exactamente la del FAQ, el modelo necesita la
    fuente para responder bien igual. La entrada del FAQ orienta; la clausula
    fundamenta.
    """

    faq_id: int
    question: str
    answer: str
    code: str
    version: str
    section: str | None
    distance: float
    reviewed: bool
    source_content: str = ""

    def to_debug(self) -> dict:
        return {
            "faq_id": self.faq_id,
            "question": self.question,
            "code": self.code,
            "version": self.version,
            "section": self.section,
            "reviewed": self.reviewed,
            "distance": round(self.distance, 4),
        }


@dataclass(frozen=True)
class RetrievalResult:
    """Lo aceptado y lo descartado. Se guarda completo para auditoria."""

    accepted: list[RetrievedChunk]
    rejected: list[RetrievedChunk]
    max_distance: float
    faqs: list[RetrievedFaq] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.accepted or self.faqs)

    def to_debug(self) -> dict:
        debug = {
            "max_distance": self.max_distance,
            "accepted": [c.to_debug() for c in self.accepted],
            "rejected": [c.to_debug() for c in self.rejected],
        }
        if self.faqs:
            debug["faqs"] = [f.to_debug() for f in self.faqs]
        return debug


def _fetch_candidates(
    db: Session, tenant_id: str, query_vector: list[float], limit: int
) -> list[RetrievedChunk]:
    distance = DocumentChunk.embedding.cosine_distance(query_vector).label("distance")

    rows = (
        db.query(DocumentChunk, Document, distance)
        .join(Document, DocumentChunk.document_id == Document.id)
        .filter(Document.tenant_id == tenant_id, Document.status == "vigente")
        .order_by(distance)
        .limit(limit)
        .all()
    )

    return [
        RetrievedChunk(
            chunk_id=chunk.id,
            content=chunk.content,
            code=doc.code,
            version=doc.version,
            section=chunk.section,
            distance=float(dist),
            title=doc.title,
            effective_date=doc.effective_date.isoformat() if doc.effective_date else None,
        )
        for chunk, doc, dist in rows
    ]


# Cuantas entradas de FAQ se traen y cuan cerca deben estar. El umbral es mas
# estricto que el de las clausulas porque una entrada de FAQ lejana no aporta
# contexto util: solo mete una pregunta parecida que puede despistar.
FAQ_TOP_K = 3
FAQ_MAX_DISTANCE = 0.35


def retrieve_faqs(
    db: Session,
    tenant_id: str,
    query_vector: list[float],
    top_k: int = FAQ_TOP_K,
    max_distance: float = FAQ_MAX_DISTANCE,
) -> list[RetrievedFaq]:
    """
    Busca preguntas frecuentes parecidas a la consulta.

    Resuelve el desajuste de vocabulario: la clausula dice "El Jefe de TI informa
    al solicitante el estado de la solicitud" y la persona pregunta "quien me
    avisa como va mi solicitud?". Semanticamente estan lejos -- lenguaje de
    procedimiento contra lenguaje de persona -- pero la entrada del FAQ ESTA
    escrita como pregunta, asi que coincide.

    Se filtra por documento vigente, igual que las clausulas: si Calidad marca un
    documento como obsoleto, sus preguntas dejan de recuperarse en la consulta
    siguiente, sin proceso de invalidacion aparte.
    """
    from app.models.db_models import FaqEntry

    distance = FaqEntry.embedding.cosine_distance(query_vector).label("distance")

    filas = (
        db.query(FaqEntry, Document, DocumentChunk, distance)
        .join(Document, FaqEntry.document_id == Document.id)
        .outerjoin(DocumentChunk, FaqEntry.chunk_id == DocumentChunk.id)
        .filter(Document.tenant_id == tenant_id, Document.status == "vigente")
        .order_by(distance)
        .limit(top_k)
        .all()
    )

    return [
        RetrievedFaq(
            faq_id=faq.id,
            question=faq.question,
            answer=faq.answer,
            code=doc.code,
            version=doc.version,
            section=faq.section,
            distance=float(dist),
            reviewed=faq.reviewed,
            source_content=chunk.content if chunk else "",
        )
        for faq, doc, chunk, dist in filas
        if float(dist) <= max_distance
    ]


def _apply_boilerplate_penalty(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """
    Aleja las clausulas de tramite y reordena por la distancia ajustada.

    No las excluye: si alguien pregunta "cual es el objetivo del Manual", la
    clausula Objetivo sigue siendo la mejor respuesta y la penalizacion no
    alcanza para desplazarla. Lo que evita es que desplace al contenido operativo
    en una pregunta sustantiva.
    """
    ajustados = [
        replace(chunk, distance=chunk.distance + BOILERPLATE_PENALTY)
        if _es_clausula_de_tramite(chunk.content)
        else chunk
        for chunk in chunks
    ]
    return sorted(ajustados, key=lambda c: c.distance)


def retrieve(
    db: Session,
    tenant_id: str,
    query: str,
    top_k: int | None = None,
    max_distance: float | None = None,
    relative_margin: float | None = None,
) -> RetrievalResult:
    """
    Busca los chunks relevantes aplicando DOS cortes, que resuelven problemas distintos:

    1. Umbral absoluto (`max_distance`) -> decide SI se responde.
       Calibrado con preguntas reales dentro y fuera de alcance.

    2. Margen relativo (`relative_margin`) -> decide CUALES de los aceptados
       sirven. Una consulta puede tener un fragmento claramente pertinente
       (d=0.27) y otros tres que apenas pasan el umbral (d=0.34-0.41) y solo
       aportan ruido: el modelo los lee, los mezcla, y termina citando
       documentos que no responden la pregunta.

    Bajar el umbral absoluto NO resuelve (2): con los documentos reales, la
    pregunta legitima "cual es la politica de la calidad" mide 0.4448, asi que
    un corte agresivo dejaria al bot sin responder sobre su propia politica.
    El corte relativo se adapta a cada consulta.
    """
    top_k = top_k if top_k is not None else settings.rag_top_k
    max_distance = max_distance if max_distance is not None else settings.rag_max_distance
    relative_margin = (
        relative_margin if relative_margin is not None else settings.rag_relative_margin
    )

    # Se piden mas candidatos de los necesarios porque la penalizacion de tramite
    # reordena: si se pidieran solo top_k, una clausula operativa buena podria
    # quedar fuera de la consulta original.
    vector = embed_query(query)
    candidates = _fetch_candidates(db, tenant_id, vector, top_k * 3)
    candidates = _apply_boilerplate_penalty(candidates)[:top_k]
    faqs = retrieve_faqs(db, tenant_id, vector)

    dentro_del_umbral = [c for c in candidates if c.distance <= max_distance]
    rejected = [c for c in candidates if c.distance > max_distance]

    if dentro_del_umbral and relative_margin > 0:
        mejor = dentro_del_umbral[0].distance  # vienen ordenados por distancia
        corte = mejor + relative_margin
        accepted = [c for c in dentro_del_umbral if c.distance <= corte]
        rejected += [c for c in dentro_del_umbral if c.distance > corte]
        rejected.sort(key=lambda c: c.distance)
    else:
        accepted = dentro_del_umbral

    logger.info(
        "rag.retrieve",
        extra={
            "tenant_id": tenant_id,
            "accepted": len(accepted),
            "rejected": len(rejected),
            "faqs": len(faqs),
            "best_distance": round(candidates[0].distance, 4) if candidates else None,
            "best_faq_distance": round(faqs[0].distance, 4) if faqs else None,
            "max_distance": max_distance,
        },
    )

    return RetrievalResult(
        accepted=accepted, rejected=rejected, max_distance=max_distance, faqs=faqs
    )


def build_faq_block(faqs: list[RetrievedFaq]) -> str:
    """
    Bloque de preguntas frecuentes para el contexto del modelo.

    Va marcado como ORIENTATIVO a proposito. Una entrada del FAQ es una respuesta
    ya redactada, y el riesgo es que el modelo la copie aunque la pregunta del
    usuario sea parecida pero distinta -- "una incidencia que me afecta solo a
    mi" (1 hora) contra "una que afecta a toda la empresa" (30 minutos) se
    parecen mucho y se responden distinto.

    Por eso siempre se acompana del texto de la clausula: si no coinciden, el
    modelo tiene la fuente para responder bien igual.
    """
    if not faqs:
        return ""

    partes = []
    for i, faq in enumerate(faqs, start=1):
        sello = "revisada por Calidad" if faq.reviewed else "generada, sin revisar"
        bloque = [
            f"[FAQ {i}] ({sello}) {faq.code} {faq.version}"
            + (f", seccion {faq.section}" if faq.section else ""),
            f"P: {faq.question}",
            f"R: {faq.answer}",
        ]
        if faq.source_content:
            bloque.append(f"Texto de la clausula:\n{faq.source_content}")
        partes.append("\n".join(bloque))

    return "\n\n---\n\n".join(partes)


def build_context_block(chunks: list[RetrievedChunk]) -> str:
    """
    Arma el bloque de contexto que ve el LLM.

    Cada fragmento va rotulado con su codigo, version y seccion, para que el
    modelo tenga literalmente delante el texto de la cita que debe producir.
    """
    parts = []
    for i, chunk in enumerate(chunks, start=1):
        encabezado = f"[Fragmento {i}] Documento: {chunk.code} {chunk.version}"
        # El TITULO es lo que hace utilizable la respuesta: "STI-PO-01" no le
        # dice nada a nadie; "Politica de Uso de Equipos y Dispositivos" si.
        # Sin esto en el contexto, el modelo no puede nombrar el documento
        # aunque quiera -- solo tiene el codigo.
        if chunk.title:
            encabezado += f' | Titulo: "{chunk.title}"'
        if chunk.section:
            encabezado += f" | Seccion: {chunk.section}"
        parts.append(f"{encabezado}\n{chunk.content}")
    return "\n\n---\n\n".join(parts)
