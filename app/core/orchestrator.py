"""
Orquestador: el cerebro del sistema. Recibe un mensaje ya normalizado (venga de
WhatsApp o del widget web) y devuelve una respuesta, tambien normalizada.

Flujo:
    conversacion -> guardar entrada -> RAG -> LLM (+tools) -> guardrail -> guardar salida

El guardrail bloqueante corre DESPUES del LLM y puede descartar por completo lo
que el modelo haya respondido. Esa asimetria es intencional: en un contexto de
auditoria ISO, no responder es correcto; inventar un procedimiento no lo es.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import settings
from app.core.agents.tools import (
    ESCALATING_TOOLS,
    TOOLS_SCHEMA,
    execute_tool,
    parse_arguments,
    recover_tool_call_from_text,
    split_leaked_tool_mentions,
    strip_tool_name_prefix,
)
from app.core.guardrails.grounding_check import (
    NO_CONTEXT_MESSAGE,
    OUT_OF_SCOPE_HINT,
    PHANTOM_CITATION_MESSAGE,
    classify_citations,
    extract_cited_codes,
    has_sufficient_context,
    repair_cited_versions,
    response_cites_source,
)
from app.core.rag.retriever import (
    RetrievalResult,
    RetrievedChunk,
    build_context_block,
    build_faq_block,
    retrieve,
)
from app.models.db_models import (
    Conversation,
    Document,
    DocumentChunk,
    Message,
    Tenant,
)
from app.models.schemas import BotResponse, Citation, IncomingMessage, Suggestion
from app.services.llm_client import LLMError, get_llm_client

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = (
    "Eres NormIA, asistente de documentacion y cumplimiento ISO. Responde SIEMPRE citando "
    "el codigo y version del documento. Si no encuentras informacion suficiente, dilo "
    "claramente y ofrece escalar a Calidad."
)

ESCALATED_MESSAGE = (
    "Esa consulta esta fuera de mi alcance: no apruebo, autorizo ni modifico documentos "
    "controlados. La derive al Responsable de Calidad para que la revise."
)

def llm_error_message(referencia: str) -> str:
    """
    Mensaje de fallo con una referencia citable.

    Un "estoy con problemas tecnicos" sin mas dejaba al usuario sin nada que
    reportar y al operador sin forma de correlacionar la queja con el log.
    """
    return (
        "No pude procesar tu consulta por un problema tecnico. La incidencia quedo "
        f"registrada como {referencia}; si vuelve a pasar, menciona ese codigo al "
        "area de sistemas. Tambien puedes intentarlo de nuevo en unos segundos."
    )

# Cuantos turnos previos se reenvian al modelo. Acotado a proposito: el contexto
# ISO ya ocupa buena parte de la ventana y el historial largo dispara el costo.
HISTORY_TURNS = 6


def get_or_create_conversation(db: Session, msg: IncomingMessage) -> Conversation:
    conversation = (
        db.query(Conversation)
        .filter_by(
            tenant_id=msg.tenant_id,
            channel=msg.channel,
            external_user_id=msg.external_user_id,
        )
        .one_or_none()
    )
    if conversation is None:
        conversation = Conversation(
            tenant_id=msg.tenant_id,
            channel=msg.channel,
            external_user_id=msg.external_user_id,
        )
        db.add(conversation)
        db.flush()
    return conversation


def _load_history(db: Session, conversation_id: int, limit: int) -> list[dict[str, str]]:
    rows = (
        db.query(Message)
        .filter_by(conversation_id=conversation_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit)
        .all()
    )
    return [{"role": m.role, "content": m.content} for m in reversed(rows)]


def _system_prompt(db: Session, tenant_id: str) -> str:
    tenant = db.get(Tenant, tenant_id)
    return (tenant.system_prompt if tenant else None) or DEFAULT_SYSTEM_PROMPT


def _build_messages(
    system_prompt: str, retrieval: RetrievalResult, history: list[dict[str, str]], user_text: str
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]

    # Las preguntas frecuentes van ANTES de las clausulas y marcadas como
    # orientativas. Cierran el desajuste de vocabulario -- la clausula habla en
    # lenguaje de procedimiento y la persona en el suyo -- pero no sustituyen a
    # la fuente: cada entrada viene con el texto de su clausula, porque una
    # pregunta parecida puede tener otra respuesta ("una incidencia que me afecta
    # solo a mi" son 1 hora; "una que afecta a toda la empresa", 30 minutos).
    if retrieval.faqs:
        messages.append(
            {
                "role": "system",
                "content": (
                    "Preguntas frecuentes parecidas a la consulta, con el texto de la "
                    "clausula de la que salieron. Son ORIENTATIVAS: uselas solo si "
                    "responden exactamente lo que se pregunto. Si la pregunta del "
                    "usuario es parecida pero distinta, responde con el texto de la "
                    "clausula y no copies la respuesta del FAQ.\n\n"
                    + build_faq_block(retrieval.faqs)
                ),
            }
        )

    if retrieval.accepted:
        context = build_context_block(retrieval.accepted)
        messages.append(
            {
                "role": "system",
                "content": (
                    "Fragmentos de documentos ISO VIGENTES recuperados para esta consulta. "
                    "Responde unicamente con base en ellos y cita el codigo y version exactos "
                    "del documento que uses. Si estos fragmentos no contienen la respuesta, "
                    "dilo y escala a Calidad; no completes con conocimiento general.\n\n"
                    f"{context}\n\n{OUT_OF_SCOPE_HINT}"
                ),
            }
        )
    else:
        messages.append(
            {
                "role": "system",
                "content": (
                    "No se recupero ningun fragmento de documento vigente relevante para esta "
                    "consulta. No inventes procedimientos ni cites documentos. Indica que no "
                    "tienes informacion suficiente y escala a Calidad con escalate_to_quality."
                    f"\n\n{OUT_OF_SCOPE_HINT}"
                ),
            }
        )

    messages.extend(history)
    messages.append({"role": "user", "content": user_text})
    return messages


def _run_tool_calls(
    db: Session,
    tool_calls: list[dict[str, Any]],
    tenant_id: str,
    conversation_id: int,
    question: str = "",
    channel: str | None = None,
) -> tuple[list[dict[str, Any]], list[str], list[tuple[str, dict[str, Any]]]]:
    """
    Ejecuta cada tool call.

    Devuelve (mensajes para el LLM, nombres ejecutados, resultados crudos). Los
    resultados se conservan porque el orquestador los necesita despues: una
    respuesta construida leyendo un documento se cita a nivel de ese documento,
    no de los fragmentos que trajo el RAG.
    """
    tool_messages: list[dict[str, Any]] = []
    executed: list[str] = []
    results: list[tuple[str, dict[str, Any]]] = []

    for call in tool_calls:
        function = call.get("function", {})
        name = function.get("name", "")
        try:
            arguments = parse_arguments(function.get("arguments"))
        except ValueError as exc:
            result = {"error": "invalid_arguments", "message": str(exc)}
        else:
            result = execute_tool(
                name,
                arguments,
                db=db,
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                question=question,
                channel=channel,
            )

        executed.append(name)
        results.append((name, result))
        logger.info("tool.executed", extra={"tool": name, "result_keys": sorted(result)})

        tool_messages.append(
            {
                "role": "tool",
                "tool_call_id": call.get("id", name),
                "name": name,
                "content": _json_dump(result),
            }
        )

    return tool_messages, executed, results


def _json_dump(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)


# Frases textuales de nuestras propias instrucciones de sistema. Si aparecen en
# la respuesta, el modelo esta repitiendo el prompt en vez de contestar.
_INSTRUCTION_FINGERPRINTS = (
    "no inventes procedimientos",
    "no se recupero ningun fragmento",
    "escala a calidad con escalate_to_quality",
    "responde unicamente con base en ellos",
    "fragmentos de documentos iso vigentes recuperados",
)


def _echoes_instructions(answer: str) -> bool:
    lowered = answer.lower()
    return any(fingerprint in lowered for fingerprint in _INSTRUCTION_FINGERPRINTS)


_SECTION_RE = re.compile(r"(?:secci[oó]n|seccion|§|apartado|ac[aá]pite)\s*(\d+(?:\.\d+)*)", re.I)


def _mentioned_sections(answer: str) -> set[str]:
    """Numeros de clausula que la respuesta nombra explicitamente."""
    return {m.group(1) for m in _SECTION_RE.finditer(answer or "")}


def _known_codes(db: Session, tenant_id: str) -> set[str]:
    """
    Codigos que EXISTEN en el SGC, para distinguir una cita inventada de una real.

    Union de dos fuentes, y la union importa:

    - `document_registry`: la Lista Maestra, el registro autoritativo. Un codigo
      registrado existe aunque el asistente no lo tenga indexado -- pasa con los
      formatos cuyos PDFs vienen agrupados en archivos combinados.
    - `documents`: lo indexado. Puede incluir algo aun no registrado.

    Validar solo contra lo indexado marcaba como inventados codigos que si
    existen (INV-FO-02, 06, 11, 13, 16, 18, 21, GTH-FO-14) y descartaba
    respuestas correctas.
    """
    from app.models.db_models import RegistryEntry

    indexados = {
        code.split(" (")[0].upper()
        for (code,) in db.query(Document.code).filter(Document.tenant_id == tenant_id).distinct()
    }
    registrados = {
        code.upper()
        for (code,) in db.query(RegistryEntry.code)
        .filter(RegistryEntry.tenant_id == tenant_id)
        .distinct()
    }
    return indexados | registrados


def _version_maps(db: Session, tenant_id: str) -> tuple[dict[str, set[str]], dict[str, str]]:
    """
    (codigo -> todas sus versiones, codigo -> su version vigente).

    Las obsoletas cuentan como versiones reales: preguntar por una version
    derogada es legitimo y no debe corregirse. Solo se corrige una version que no
    existe para ese documento.
    """
    filas = (
        db.query(Document.code, Document.version, Document.status)
        .filter(Document.tenant_id == tenant_id)
        .all()
    )
    todas: dict[str, set[str]] = {}
    vigentes: dict[str, str] = {}
    for code, version, status in filas:
        base = code.split(" (")[0].upper()
        todas.setdefault(base, set()).add(version)
        if status == "vigente":
            vigentes[base] = version
    return todas, vigentes


# Herramientas cuya respuesta se apoya en el CATALOGO, no en el texto de una
# clausula. Lo que afirman ("estos documentos existen") se respalda a nivel de
# documento, no de seccion.
CATALOG_TOOLS = {"buscar_documentos"}

# Herramientas que leen un documento COMPLETO. Una respuesta construida asi se
# respalda en ese documento -- incluyendo los formatos que el propio documento
# menciona en su texto -- no en los fragmentos que trajo la busqueda vectorial.
DOCUMENT_TOOLS = {"leer_documento"}


def _catalog_citations(db: Session, tenant_id: str, answer: str) -> list[Citation]:
    """
    Citas de una respuesta de catalogo, resueltas contra la tabla de documentos.

    No sirve derivarlas de los chunks recuperados: el catalogo puede nombrar un
    documento que el RAG no recupero (paso con COM-PR-02, listado en la respuesta
    pero ausente de las fuentes). Y se cita a nivel de DOCUMENTO, sin seccion:
    "este documento existe" lo respalda el registro, no una clausula concreta.
    """
    codigos = extract_cited_codes(answer)
    if not codigos:
        return []

    docs = (
        db.query(Document)
        .filter(
            Document.tenant_id == tenant_id,
            Document.status == "vigente",
            or_(*[Document.code.ilike(f"{c}%") for c in codigos]),
        )
        .order_by(Document.code)
        .all()
    )
    vistos: set[tuple[str, str]] = set()
    citas: list[Citation] = []
    for doc in docs:
        clave = (doc.code, doc.version)
        if clave not in vistos:
            vistos.add(clave)
            citas.append(
                Citation(
                    code=doc.code,
                    version=doc.version,
                    title=doc.title,
                    effective_date=(
                        doc.effective_date.isoformat() if doc.effective_date else None
                    ),
                )
            )
    return citas


# Cuantas opciones ofrecer. Mas de esto deja de ser un menu y vuelve a ser un
# muro, solo que de botones.
MAX_SUGGESTIONS = 7

# Longitud maxima de una etiqueta de boton. Un chip con 70 caracteres rompe la
# fila y deja de parecer una opcion.
MAX_SUGGESTION_LABEL = 38

# Clausulas de tramite: aparecen en TODOS los documentos de un SGC y casi nunca
# son lo que alguien viene a consultar. No se ocultan, pero ceden su lugar a las
# secciones con contenido operativo.
SECCIONES_DE_TRAMITE = (
    "objetivo",
    "alcance",
    "documentos de referencia",
    "abreviaturas",
    "responsabilidad",
    "control de cambios",
)


def _etiqueta_corta(titulo: str) -> str:
    if len(titulo) <= MAX_SUGGESTION_LABEL:
        return titulo
    return f"{titulo[:MAX_SUGGESTION_LABEL].rsplit(' ', 1)[0]}..."


def _document_suggestions(
    tool_results: list[tuple[str, dict[str, Any]]],
) -> list[Suggestion]:
    """
    Convierte el indice de un documento en opciones de seguimiento.

    Un "resumeme este procedimiento" antes devolvia las 20 clausulas de golpe:
    correcto pero abrumador, y el usuario tenia que leerlo todo para encontrar lo
    que buscaba. Con el indice, la respuesta es un resumen breve mas un menu.

    Se prefieren las clausulas con encabezado propio ("Registro del hallazgo")
    sobre los extractos de prosa: se leen como opcion, no como fragmento.
    """
    for nombre, resultado in tool_results:
        if nombre not in DOCUMENT_TOOLS or "indice" not in resultado:
            continue

        codigo = resultado.get("codigo", "")
        entradas = resultado["indice"]
        con_titulo = [e for e in entradas if e.get("tiene_titulo")] or entradas

        # Las secciones operativas primero; las de tramite despues, si sobra sitio.
        # Se conserva el orden del documento dentro de cada grupo.
        # Comparar por PREFIJO, no por igualdad: los titulos reales son variantes
        # largas ("Alcance del sistema de gestion de la calidad", "Documentos de
        # referencia iso"), asi que la igualdad exacta no las reconocia y el menu
        # seguia encabezado por tramite.
        def _es_tramite(entrada: dict) -> bool:
            titulo = entrada["titulo"].strip().lower()
            return any(titulo.startswith(t) for t in SECCIONES_DE_TRAMITE)

        operativas = [e for e in con_titulo if not _es_tramite(e)]
        tramite = [e for e in con_titulo if _es_tramite(e)]
        elegidas = (operativas + tramite)[:MAX_SUGGESTIONS]

        sugerencias = [
            Suggestion(
                label=_etiqueta_corta(e["titulo"]),
                message=f"Del documento {codigo}, explicame la seccion {e['seccion']}",
            )
            for e in elegidas
        ]
        if sugerencias:
            sugerencias.append(
                Suggestion(
                    label="Ver el documento completo",
                    message=f"Dame el resumen completo del documento {codigo}",
                )
            )
        return sugerencias

    return []


# Peticion explicita de una clausula. Es el formato que generamos nosotros en
# los botones de sugerencia, asi que se puede reconocer con exactitud.
_PETICION_SECCION_RE = re.compile(
    r"del documento\s+(?P<codigo>[A-Z]{2,5}-[A-Z]{2,5}-\d{1,3})"
    r".{0,40}?secci[oó]n\s+(?P<seccion>\d+(?:\.\d+)*)",
    re.IGNORECASE | re.DOTALL,
)


def _direct_section_request(db: Session, tenant_id: str, text: str) -> RetrievalResult | None:
    """
    Carga una clausula concreta sin pasar por la busqueda vectorial.

    Los botones de sugerencia envian "Del documento STI-PR-01, explicame la
    seccion 6". Dejar que el modelo lo tradujera a una llamada a `leer_documento`
    fallaba: pulsando "Condiciones generales" contesto desde fragmentos de OTROS
    documentos y afirmo que la seccion 6 era "Descripcion del procedimiento"
    cuando es "Condiciones generales". La respuesta salio segura y equivocada.

    Aqui no hay nada que interpretar: el codigo y la clausula estan en el texto,
    asi que se cargan directamente y el modelo recibe el texto correcto por
    construccion. Ademas se ahorra el embedding de la consulta.
    """
    match = _PETICION_SECCION_RE.search(text or "")
    if not match:
        return None

    codigo = match.group("codigo").upper()
    seccion = match.group("seccion")

    filas = (
        db.query(DocumentChunk, Document)
        .join(Document, DocumentChunk.document_id == Document.id)
        .filter(
            Document.tenant_id == tenant_id,
            Document.status == "vigente",
            Document.code.ilike(f"{codigo}%"),
            or_(
                DocumentChunk.section == seccion,
                DocumentChunk.section.like(f"{seccion}.%"),
            ),
        )
        .order_by(DocumentChunk.id)
        .all()
    )
    if not filas:
        return None

    chunks = [
        RetrievedChunk(
            chunk_id=chunk.id,
            content=chunk.content,
            code=doc.code,
            version=doc.version,
            section=chunk.section,
            distance=0.0,  # peticion exacta, no hay distancia semantica
            title=doc.title,
        )
        for chunk, doc in filas
    ]
    logger.info(
        "rag.direct_section",
        extra={"code": codigo, "section": seccion, "chunks": len(chunks)},
    )
    return RetrievalResult(accepted=chunks, rejected=[], max_distance=0.0)


def _record_escalation(
    db: Session, msg: IncomingMessage, conversation_id: int, reason: str, trigger: str
) -> int | None:
    """
    Registra una escalacion que NO vino de una llamada a herramienta.

    El guardrail de grounding escala por su cuenta cuando no hay respaldo
    documental, y esas eran justo las que se perdian: el modelo no invocaba
    ninguna herramienta, asi que no quedaba rastro de que la consulta habia sido
    derivada.
    """
    from app.models.db_models import Escalation

    escalation = Escalation(
        tenant_id=msg.tenant_id,
        conversation_id=conversation_id,
        channel=msg.channel,
        question=msg.text,
        reason=reason,
        trigger=trigger,
    )
    db.add(escalation)
    db.flush()
    logger.info(
        "escalation.recorded",
        extra={"escalation_id": escalation.id, "trigger": trigger},
    )
    return escalation.id


def _retag_escalation(
    db: Session, tool_results: list[tuple[str, dict[str, Any]]], trigger: str
) -> None:
    """
    Reclasifica la escalacion que acaba de crear el modelo.

    `escalate_to_quality` no sabe por que se escala -- usa "fuera de alcance" por
    defecto. El guardrail si lo sabe, y la diferencia no es cosmetica: las
    escalaciones por falta de respaldo documental son la lista de preguntas que
    el SGC no cubre, y mezclarlas con las peticiones de aprobacion hace inutil
    esa lista.
    """
    from app.models.db_models import Escalation

    for nombre, resultado in tool_results:
        if nombre not in ESCALATING_TOOLS:
            continue
        escalation_id = resultado.get("escalation_id")
        if not escalation_id:
            continue
        escalation = db.get(Escalation, escalation_id)
        if escalation is not None and escalation.trigger != trigger:
            escalation.trigger = trigger
            escalation.reason = "Sin documentos vigentes que respalden la respuesta."
            db.flush()


def _record_incident(
    db: Session, msg: IncomingMessage, conversation_id: int | None, kind: str, detail: str
) -> str:
    """
    Registra un fallo tecnico y devuelve una referencia corta para el usuario.

    Sin esto, un fallo dejaba al usuario con "estoy con problemas tecnicos" y
    nada que reportar, y al operador con una linea de log imposible de
    correlacionar con la queja.
    """
    from app.models.db_models import Incident

    # Referencia derivada del id de la fila, para que sea unica sin aleatoriedad.
    incident = Incident(
        reference="pendiente",
        tenant_id=msg.tenant_id,
        conversation_id=conversation_id,
        kind=kind,
        detail=detail[:2000],
    )
    db.add(incident)
    db.flush()
    incident.reference = f"INC-{incident.id:05d}"
    db.commit()

    logger.error(
        "incident.recorded",
        extra={"reference": incident.reference, "kind": kind, "detail": detail[:200]},
    )
    return incident.reference


def _tool_yielded_data(result: dict[str, Any]) -> bool:
    """
    True si la herramienta devolvio algo verificable en lo que apoyarse.

    Correr no es lo mismo que encontrar. `buscar_documentos` sobre un tema que no
    esta en el SGC devuelve `documentos: []`: es una respuesta legitima de la
    herramienta, pero no respalda absolutamente nada. Contarla como dato
    desactivaba el guardrail bloqueante, y entonces el modelo escribia "lo he
    derivado al Responsable de Calidad" sin que se registrara la escalacion. El
    usuario se quedaba esperando una revision que nadie iba a ver.
    """
    if result.get("error"):
        return False
    for clave in ("documentos", "hallazgos", "acciones"):
        if clave in result and not result[clave]:
            return False
    return True


def _dominant_document(chunks: list[RetrievedChunk]) -> str | None:
    """
    Codigo del documento que domina lo recuperado, si hay uno.

    "Domina" es la mitad o mas de los fragmentos aceptados: si la respuesta se
    apoya sobre todo en un documento, ofrecer su indice es un siguiente paso util.
    """
    if not chunks:
        return None

    conteo: dict[str, int] = {}
    for chunk in chunks:
        codigo = chunk.code.split(" (")[0]
        conteo[codigo] = conteo.get(codigo, 0) + 1

    codigo, veces = max(conteo.items(), key=lambda kv: kv[1])
    return codigo if veces * 2 >= len(chunks) else None


def _suggestions_for_response(
    db: Session,
    tenant_id: str,
    tool_results: list[tuple[str, dict[str, Any]]],
    retrieval: RetrievalResult,
) -> list[Suggestion]:
    """
    Opciones de seguimiento, decididas por el servidor y no por el modelo.

    Medido: el modelo llama a `leer_documento` en unos dos tercios de los fraseos
    de resumen -- "de que trata CAL-PR-03?" si, "resumen del procedimiento
    CAL-PR-03" no. Dejar las opciones a su criterio las hace aparecer y
    desaparecer sin motivo visible para el usuario.

    Asi que si el modelo no leyo el documento pero la respuesta se apoya en uno,
    el indice se ofrece igual.
    """
    explicitas = _document_suggestions(tool_results)
    if explicitas:
        return explicitas

    codigo = _dominant_document(retrieval.accepted)
    if not codigo:
        return []

    from app.core.agents.tools import leer_documento

    resultado = leer_documento(db, tenant_id, codigo)
    if "indice" not in resultado:
        return []
    return _document_suggestions([("leer_documento", resultado)])


def _read_document_citations(
    tool_results: list[tuple[str, dict[str, Any]]], answer: str
) -> list[Citation]:
    """
    Citas de una respuesta que se construyo leyendo un documento completo.

    Se cita el documento leido con las secciones que la respuesta nombra
    explicitamente. Antes se listaban solo los dos fragmentos que habia traido el
    RAG, aunque el resumen referenciara doce clausulas: el lector veia dos fuentes
    para un texto respaldado por todo el documento.
    """
    citas: list[Citation] = []
    secciones = _mentioned_sections(answer)

    for nombre, resultado in tool_results:
        if nombre not in DOCUMENT_TOOLS or "codigo" not in resultado:
            continue

        codigo, version = resultado["codigo"], resultado["version"]
        disponibles = {
            s.get("seccion") for s in resultado.get("secciones", []) if s.get("seccion")
        }
        usadas = sorted(secciones & disponibles)

        titulo = resultado.get("titulo")
        vigencia = resultado.get("vigente_desde")
        if usadas:
            citas.extend(
                Citation(
                    code=codigo,
                    version=version,
                    section=s,
                    title=titulo,
                    effective_date=vigencia,
                )
                for s in usadas
            )
        else:
            citas.append(
                Citation(
                    code=codigo, version=version, title=titulo, effective_date=vigencia
                )
            )

    return citas


def _citations(
    chunks: list[RetrievedChunk],
    answer: str = "",
    executed_tools: list[str] | None = None,
    tool_results: list[tuple[str, dict[str, Any]]] | None = None,
) -> list[Citation]:
    """
    Una cita por (codigo, version, seccion), sin repetir.

    Si la respuesta menciona codigos concretos, se listan SOLO esos. Antes se
    devolvian los cuatro fragmentos recuperados aunque el modelo hubiera usado
    uno: el usuario veia tres "Fuente:" que no respaldaban nada de lo dicho, lo
    que en una auditoria es peor que no citar.
    """
    usados = extract_cited_codes(answer) if answer else set()

    if usados:
        del_codigo = [c for c in chunks if c.code.split(" (")[0].upper() in usados]
        # Si la respuesta nombra secciones concretas, quedarse solo con esas. Sin
        # esto se listaba "STI-PO-01 §7.4.4" en una respuesta que solo hablaba de
        # la §5: una fuente que no respalda nada de lo dicho.
        secciones = _mentioned_sections(answer)
        con_seccion = [c for c in del_codigo if c.section and c.section in secciones]
        relevantes = con_seccion or del_codigo
    else:
        relevantes = []

    # Sin codigos citados: NO adjuntar fuentes cuando la respuesta no afirma nada
    # sobre un documento. "Lo derive al Responsable de Calidad" o "no encontre
    # documentos sobre IA" no se apoyan en ningun documento, y listar los
    # fragmentos recuperados sugiere un respaldo que no existe -- en una auditoria
    # eso es peor que no citar.
    if not usados:
        escalando = bool(ESCALATING_TOOLS & set(executed_tools or []))
        catalogo_vacio = any(
            nombre in CATALOG_TOOLS and not resultado.get("documentos")
            for nombre, resultado in (tool_results or [])
        )
        if escalando or catalogo_vacio:
            return []

    fuente = relevantes or chunks

    seen: set[tuple[str, str, str | None]] = set()
    citations: list[Citation] = []
    for chunk in fuente:
        key = (chunk.code, chunk.version, chunk.section)
        if key not in seen:
            seen.add(key)
            citations.append(
                Citation(
                    code=chunk.code,
                    version=chunk.version,
                    section=chunk.section,
                    title=chunk.title,
                    effective_date=chunk.effective_date,
                )
            )
    return citations


def handle_message(db: Session, msg: IncomingMessage) -> BotResponse:
    started = time.perf_counter()

    conversation = get_or_create_conversation(db, msg)
    db.add(Message(conversation_id=conversation.id, role="user", content=msg.text))
    db.flush()

    history = _load_history(db, conversation.id, HISTORY_TURNS)[:-1]  # sin el turno actual

    # Una peticion explicita de clausula se sirve directa; el resto va al RAG.
    retrieval = _direct_section_request(db, msg.tenant_id, msg.text) or retrieve(
        db, msg.tenant_id, msg.text
    )

    messages = _build_messages(_system_prompt(db, msg.tenant_id), retrieval, history, msg.text)

    client = get_llm_client()
    executed_tools: list[str] = []
    tool_results: list[tuple[str, dict[str, Any]]] = []
    mentioned_tools: set[str] = set()

    try:
        completion = client.generate(messages, tools=TOOLS_SCHEMA)

        tool_calls = completion["tool_calls"]
        recovered = False
        if not tool_calls:
            # Con contexto largo el modelo a veces escribe la llamada DENTRO del
            # texto en vez de emitirla como tool_call. Sin esto el turno "funciona"
            # pero la herramienta nunca corre y el usuario ve JSON crudo.
            leaked = recover_tool_call_from_text(completion["content"])
            if leaked:
                tool_calls = [leaked]
                recovered = True
                logger.warning(
                    "llm.tool_call_recovered_from_text",
                    extra={"tool": leaked["function"]["name"]},
                )

        if tool_calls:
            tool_messages, executed_tools, tool_results = _run_tool_calls(
                db, tool_calls, msg.tenant_id, conversation.id, msg.text, msg.channel
            )
            # Si la llamada venia en el texto, ese texto NO puede quedar en el
            # historial: reenviarlo le ensena al modelo a repetir el error.
            assistant_turn = (
                {"role": "assistant", "content": None, "tool_calls": tool_calls}
                if recovered
                else completion["raw"]
            )
            messages.append(assistant_turn)
            messages.extend(tool_messages)
            # Segunda pasada: el modelo redacta la respuesta final ya con el
            # resultado de la herramienta a la vista.
            completion = client.generate(messages)

        answer = strip_tool_name_prefix(completion["content"].strip())

        # El modelo filtra el formato interno en formas variadas (JSON,
        # <tool_call>, "nombre\nMotivo: ..."). Se retira todo eso del texto y se
        # anota que herramientas menciono sin llegar a invocarlas.
        answer, mentioned_tools = split_leaked_tool_mentions(answer)

        # Red de seguridad: si aun asi quedo texto con forma de tool call, se
        # descarta antes de mostrarlo. Nunca ensenar JSON crudo al usuario.
        if recover_tool_call_from_text(answer):
            answer = ""

    except LLMError as exc:
        referencia = _record_incident(db, msg, conversation.id, "llm", str(exc))
        _record_escalation(
            db,
            msg,
            conversation.id,
            f"Fallo tecnico al procesar la consulta ({referencia}).",
            "error",
        )
        return _persist_and_return(
            db,
            conversation.id,
            BotResponse(text=llm_error_message(referencia), escalate=True, grounded=False),
            retrieval,
            executed_tools,
            started,
            llm_error=str(exc),
        )

    grounded_context = has_sufficient_context(retrieval.accepted)
    # Escalar es idempotente y sin efectos: si el modelo dijo que escalaba pero
    # no emitio la llamada, se honra la intencion. Con register_finding NO se
    # hace lo mismo -- registrar un hallazgo dos veces si seria un problema.
    escalated_by_tool = bool(
        ESCALATING_TOOLS & (set(executed_tools) | mentioned_tools)
    )
    # Herramientas que devuelven un dato verificable (un ID de hallazgo, el estado
    # de una CAPA). Cuando una de ellas corre, la respuesta se apoya en su
    # resultado, no en los documentos: es texto confiable aunque el RAG no
    # haya recuperado nada.
    data_tools_ran = any(
        nombre not in ESCALATING_TOOLS and _tool_yielded_data(resultado)
        for nombre, resultado in tool_results
    )

    # La VERSION es parte de la identidad de un documento controlado: "v1" puede
    # ser una version derogada. El modelo escribio "STI-PO-01 v1" cuando esta en
    # v2, contradiciendo la lista de fuentes que genera el servidor. Se corrige
    # con el dato verificado y queda registrado.
    if answer:
        todas, vigentes = _version_maps(db, msg.tenant_id)
        answer, correcciones = repair_cited_versions(answer, todas, vigentes)
        if correcciones:
            logger.warning(
                "guardrail.version_corrected",
                extra={
                    "corregidas": [
                        {"code": c, "citada": mala, "real": buena}
                        for c, mala, buena in correcciones
                    ]
                },
            )

    # Verificacion de citas, codigo por codigo. Un documento inventado invalida
    # la respuesta completa: la promesa del sistema es que nunca cita algo que no
    # existe, y una respuesta correcta que ademas inventa una fuente es peor que
    # no responder.
    phantom_codes: set[str] = set()
    unretrieved_codes: set[str] = set()
    if answer:
        _, unretrieved_codes, phantom_codes = classify_citations(
            answer, retrieval.accepted, _known_codes(db, msg.tenant_id)
        )
        if phantom_codes:
            logger.warning(
                "guardrail.phantom_citation",
                extra={"codes": sorted(phantom_codes), "question": msg.text[:120]},
            )
        elif unretrieved_codes:
            logger.info(
                "guardrail.citation_not_retrieved", extra={"codes": sorted(unretrieved_codes)}
            )

    if phantom_codes:
        _record_escalation(
            db,
            msg,
            conversation.id,
            f"La respuesta cito documentos inexistentes: {', '.join(sorted(phantom_codes))}.",
            "error",
        )
        response = BotResponse(
            text=PHANTOM_CITATION_MESSAGE, escalate=True, grounded=False
        )
        return _persist_and_return(
            db, conversation.id, response, retrieval, executed_tools, started
        )

    if answer and _echoes_instructions(answer):
        # El modelo repitio el prompt de sistema en vez de responder. Ha pasado
        # con contexto vacio: la instruccion "no inventes, escala a Calidad"
        # salia tal cual hacia el usuario.
        logger.warning("llm.echoed_system_prompt", extra={"answer": answer[:120]})
        answer = ""

    if not grounded_context and not data_tools_ran:
        # Guardrail BLOQUEANTE: sin respaldo documental y sin un dato de
        # herramienta, NADA de lo que escriba el modelo es confiable -- se
        # descarta completo. Es el paso que impide alucinar un procedimiento.
        #
        # Y se REGISTRA: el bot promete derivar la consulta, asi que tiene que
        # quedar en la cola de Calidad. Estas eran las escalaciones que se
        # perdian, porque no venian de una llamada a herramienta.
        if escalated_by_tool:
            # El modelo ya la registro, pero etiquetada como "fuera de alcance"
            # (su valor por defecto). El guardrail sabe la razon real: no habia
            # respaldo documental. Y esa distincion es la que importa, porque las
            # escalaciones por falta de contexto SON la lista de huecos del SGC.
            _retag_escalation(db, tool_results, "sin_contexto")
        else:
            _record_escalation(
                db,
                msg,
                conversation.id,
                "Sin documentos vigentes que respalden la respuesta.",
                "sin_contexto",
            )
        response = BotResponse(text=NO_CONTEXT_MESSAGE, escalate=True, grounded=False)
    elif not answer and escalated_by_tool:
        response = BotResponse(text=ESCALATED_MESSAGE, escalate=True, grounded=False)
    elif not answer:
        response = BotResponse(text=NO_CONTEXT_MESSAGE, escalate=True, grounded=False)
    else:
        cites = response_cites_source(answer, retrieval.accepted) if grounded_context else False
        catalog_ran = bool(CATALOG_TOOLS & set(executed_tools))
        catalog_citations = (
            _catalog_citations(db, msg.tenant_id, answer) if catalog_ran else []
        )
        document_citations = _read_document_citations(tool_results, answer)
        response = BotResponse(
            text=answer,
            suggestions=_suggestions_for_response(
                db, msg.tenant_id, tool_results, retrieval
            ),
            citations=document_citations
            or catalog_citations
            or _citations(retrieval.accepted, answer, executed_tools, tool_results),
            escalate=escalated_by_tool or (not grounded_context and not data_tools_ran),
            # Citar un documento real que NO se recupero significa que el modelo
            # tiro de memoria, no del contexto: la respuesta deja de estar
            # fundamentada aunque el codigo exista.
            # Un codigo no recuperado NO descalifica una respuesta de catalogo ni
            # una construida leyendo un documento completo: ahi la fuente es el
            # registro o el documento, no los fragmentos del RAG. Un procedimiento
            # que menciona sus propios formatos (STI-FO-01, STI-FO-02) los cita con
            # razon aunque el RAG no los haya recuperado.
            grounded=(
                bool(document_citations)
                or bool(catalog_citations)
                or (grounded_context and cites and not unretrieved_codes)
            ),
        )

    return _persist_and_return(
        db, conversation.id, response, retrieval, executed_tools, started
    )


def _persist_and_return(
    db: Session,
    conversation_id: int,
    response: BotResponse,
    retrieval: RetrievalResult,
    executed_tools: list[str],
    started: float,
    llm_error: str | None = None,
) -> BotResponse:
    elapsed_ms = round((time.perf_counter() - started) * 1000)

    debug: dict[str, Any] = {
        "retrieval": retrieval.to_debug(),
        "tools": executed_tools,
        "grounded": response.grounded,
        "escalate": response.escalate,
        "elapsed_ms": elapsed_ms,
        "model": settings.llm_model,
    }
    if llm_error:
        debug["llm_error"] = llm_error

    db.add(
        Message(
            conversation_id=conversation_id,
            role="assistant",
            content=response.text,
            retrieved_chunk_ids=[c.chunk_id for c in retrieval.accepted],
            retrieval_debug=debug,
        )
    )
    db.commit()

    logger.info(
        "turn.completed",
        extra={
            "conversation_id": conversation_id,
            "grounded": response.grounded,
            "escalate": response.escalate,
            "chunks": len(retrieval.accepted),
            "tools": executed_tools,
            "elapsed_ms": elapsed_ms,
        },
    )
    return response
