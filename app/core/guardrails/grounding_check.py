"""
Guardrail de grounding: evita que el bot responda algo que no este respaldado
por los documentos recuperados. Esto es lo que hace confiable a un chatbot en
un contexto de auditoria ISO -- no puede "inventar" un procedimiento.

Dos niveles:

- BLOQUEANTE: si no hay contexto suficiente (ningun chunk paso el umbral de
  distancia), no se deja pasar la respuesta del modelo. Se responde
  NO_CONTEXT_MESSAGE y se escala. El modelo no tiene voto aqui.

- INFORMATIVO: si SI hubo contexto, se verifica que la respuesta mencione al
  menos un codigo de documento recuperado. No bloquea (el modelo puede
  parafrasear legitimamente), pero marca `grounded=False` y queda en el log
  de auditoria.
"""

from __future__ import annotations

import re

from app.core.rag.retriever import RetrievedChunk


def has_sufficient_context(chunks: list[RetrievedChunk], min_chunks: int = 1) -> bool:
    return len(chunks) >= min_chunks


# Cuantas palabras distintivas del titulo deben aparecer para contar como cita.
# Con una sola habria falsos positivos: "calidad" o "gestion" salen en casi
# cualquier respuesta sobre un SGC.
MIN_PALABRAS_DE_TITULO = 2

# Palabras que aparecen en tantos titulos de un SGC que no distinguen nada.
_PALABRAS_COMUNES = {
    "de", "del", "la", "el", "los", "las", "y", "en", "para", "por", "a",
    "procedimiento", "manual", "instructivo", "formato", "politica", "plan",
    "gestion", "calidad", "sistema", "documento", "documentos", "general",
}


def _palabras_distintivas(titulo: str) -> set[str]:
    return {
        p
        for p in re.findall(r"\w+", _sin_acentos(titulo or ""))
        if len(p) > 3 and p not in _PALABRAS_COMUNES
    }


def _sin_acentos(texto: str) -> str:
    import unicodedata

    descompuesto = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in descompuesto if not unicodedata.combining(c)).lower()


def response_cites_source(response_text: str, chunks: list[RetrievedChunk]) -> bool:
    """
    La respuesta debe atribuir su contenido a un documento recuperado.

    Vale el CODIGO o el TITULO. El codigo es lo que el sistema verifica, pero
    "STI-PR-01 v4" no le dice nada a quien pregunta: obligar al modelo a
    escribirlo en la prosa hacia que la respuesta empezara demostrando
    cumplimiento en vez de ayudando, y un asistente que no se lee no se usa.

    Citar por titulo -- "el procedimiento de Atencion de Solicitudes
    Tecnologicas, seccion 7.6" -- atribuye igual y se entiende. El codigo, la
    version y la fecha de vigencia van en la ficha de fuente, que arma el
    servidor desde la base y no depende de que el modelo los escriba bien.

    Se exigen dos palabras distintivas del titulo, no una: con una sola,
    "calidad" o "gestion" convertirian cualquier respuesta en citada.
    """
    if not chunks:
        return False

    normalizada = _normalize(response_text)
    if any(_normalize(chunk.code) in normalizada for chunk in chunks):
        return True

    en_respuesta = set(re.findall(r"\w+", _sin_acentos(response_text)))
    return any(
        len(_palabras_distintivas(chunk.title) & en_respuesta) >= MIN_PALABRAS_DE_TITULO
        for chunk in chunks
        if chunk.title
    )


def _normalize(text: str) -> str:
    return re.sub(r"[\s_\-]+", "", text).upper()


NO_CONTEXT_MESSAGE = (
    "No tengo informacion suficiente en los documentos vigentes para responder eso "
    "con certeza. Te voy a conectar con el Responsable de Calidad para que lo revise."
)

OUT_OF_SCOPE_HINT = (
    "Recuerda: no apruebas, autorizas ni modificas documentos. Si te lo piden, "
    "escala a Calidad con la herramienta escalate_to_quality."
)


# --- Verificacion de citas -------------------------------------------------
#
# `response_cites_source` solo exige que aparezca AL MENOS UN codigo recuperado.
# Eso deja pasar una respuesta que cita un documento correcto y ademas inventa
# otro. Ocurrio de verdad: el modelo cito "PROC-CAL-04 v3" -- un codigo que
# venia como EJEMPLO DE FORMATO en el prompt de sistema y no existe en el SGC.
#
# En un asistente de cumplimiento eso es el fallo mas grave posible: la promesa
# entera del sistema es que nunca cita un documento que no existe. Asi que cada
# codigo mencionado se verifica uno por uno.

# Codigo de documento controlado: AREA-TIPO-NUMERO (STI-PR-01, GTH-MN-02,
# PROC-CAL-04). Los segmentos varian de largo entre organizaciones, asi que el
# patron es deliberadamente amplio: es mejor revisar un falso positivo que
# dejar pasar una cita inventada.
CODE_PATTERN = re.compile(r"\b[A-Z]{2,5}-[A-Z]{2,5}-\d{1,3}\b")


def extract_cited_codes(text: str) -> set[str]:
    """Todos los codigos con forma de documento controlado que aparecen en el texto."""
    return {m.group(0).upper() for m in CODE_PATTERN.finditer(text or "")}


def classify_citations(
    text: str, chunks: list[RetrievedChunk], known_codes: set[str]
) -> tuple[set[str], set[str], set[str]]:
    """
    Clasifica los codigos citados en tres grupos:

      respaldados   -> el codigo es de un fragmento recuperado, o aparece dentro
                       del texto de uno. Correcto.
      no_recuperados-> existen en el SGC pero el modelo no los tenia delante.
                       Los recordo; sospechoso, no necesariamente falso.
      inexistentes  -> NO existen en el SGC. Alucinacion pura: invalida la respuesta.

    Un codigo mencionado DENTRO del contexto cuenta como respaldado, no como
    recordado. Los procedimientos ISO se referencian entre si constantemente: la
    clausula "8. REGISTROS" de STI-PR-01 lista literalmente STI-FO-01 y STI-FO-02,
    y la clausula "3. DOCUMENTOS DE REFERENCIA" existe en todos ellos. Citar esos
    codigos es leer bien el documento, no inventar -- pero se marcaba como
    respuesta no fundamentada.
    """
    citados = extract_cited_codes(text)
    recuperados = {c.code.split(" (")[0].upper() for c in chunks}

    # Codigos que el propio contexto nombra en su texto.
    contexto = " ".join(c.content for c in chunks)
    en_el_contexto = extract_cited_codes(contexto)

    conocidos = {c.upper() for c in known_codes}

    respaldados = citados & (recuperados | en_el_contexto)
    resto = citados - respaldados
    no_recuperados = resto & conocidos
    inexistentes = resto - conocidos

    return respaldados, no_recuperados, inexistentes


PHANTOM_CITATION_MESSAGE = (
    "No puedo darte una respuesta confiable a eso: al redactarla me referi a un documento "
    "que no existe en el sistema documental. Prefiero no arriesgarme y derivarlo al "
    "Responsable de Calidad."
)


# --- Verificacion de VERSION -------------------------------------------------
#
# El guardrail de citas comprobaba que el codigo existiera, pero no la version.
# Ocurrio: preguntando por politicas de IA, la respuesta escribio
# "STI-PO-01 v1" cuando el documento esta en v2 -- y la lista de fuentes, que
# genera el servidor desde la base, decia v2. El texto y sus propias fuentes se
# contradecian.
#
# En un SGC la version es parte de la identidad del documento: "v1" puede ser
# una version derogada, asi que citarla mal equivale a citar otro documento.

# "STI-PO-01 v2", "STI-PO-01 (v2)", "STI-PO-01 version 2", "STI-PO-01, v. 2"
CITED_VERSION_RE = re.compile(
    r"\b(?P<code>[A-Z]{2,5}-[A-Z]{2,5}-\d{1,3})"
    r"(?P<gap>[\s,(]{0,3}(?:versi[oó]n|ver\.?|v\.?)\s*)"
    r"(?P<num>\d{1,3})\b",
    re.IGNORECASE,
)


def extract_cited_versions(text: str) -> set[tuple[str, str]]:
    """Pares (codigo, version) que el texto afirma, normalizados a 'vN'."""
    return {
        (m.group("code").upper(), f"v{int(m.group('num'))}")
        for m in CITED_VERSION_RE.finditer(text or "")
    }


def repair_cited_versions(
    text: str, versiones_reales: dict[str, set[str]], vigente: dict[str, str]
) -> tuple[str, list[tuple[str, str, str]]]:
    """
    Corrige las versiones citadas que no existen para ese documento.

    `versiones_reales` mapea codigo -> todas sus versiones registradas (incluidas
    las obsoletas: preguntar por una version derogada es legitimo y no se toca).
    `vigente` mapea codigo -> su version vigente, que es con la que se corrige.

    Se corrige en vez de bloquear porque la version correcta es un dato conocido:
    sustituir un numero sin verificar por el verificado deja la respuesta bien, y
    la correccion queda en el log de auditoria.

    Devuelve (texto corregido, [(codigo, citada, corregida), ...]).
    """
    correcciones: list[tuple[str, str, str]] = []

    def reemplazo(match: re.Match[str]) -> str:
        codigo = match.group("code").upper()
        citada = f"v{int(match.group('num'))}"
        conocidas = versiones_reales.get(codigo)

        if not conocidas or citada in conocidas:
            return match.group(0)

        correcta = vigente.get(codigo)
        if not correcta:
            return match.group(0)

        correcciones.append((codigo, citada, correcta))
        return f"{match.group('code')} {correcta}"

    return CITED_VERSION_RE.sub(reemplazo, text or ""), correcciones
