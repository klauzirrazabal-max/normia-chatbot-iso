"""
De que tipo es este turno.

El sistema no tenia ninguna representacion de esto, y usaba dos subproductos como
si lo fueran: "el retriever no devolvio nada" hacia de clasificador de intencion, y
"corrio una herramienta que devolvio filas" hacia de prueba de que el SGC cubre el
tema. De ahi salian las dos mitades del mismo defecto:

  - Falso hueco: cualquier mensaje que no fuera una consulta documental autonoma
    acababa en "no tengo informacion suficiente" y en la cola de Calidad. Paso con
    los saludos, con las preguntas por las capacidades y con las preguntas sobre el
    versionado; cada una se parcheo por separado.
  - Hueco perdido: un hueco real respondido desde el catalogo no se registraba.

Aqui se decide el tipo ANTES de recuperar, con reglas deterministas. La eleccion es
deliberada: los dos primeros parches se hicieron por descripcion de herramienta y
prompt, y fallaban la mitad de las veces; el tercero se hizo asi y no ha fallado.

REGLA DE ORO: ante la duda, DOCUMENTAL. Un falso positivo aqui se saltaria la
recuperacion en una consulta real, que es mucho peor que tratar de mas una
conversacion. Todo lo que no encaje con certeza cae al comportamiento de siempre.
"""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum


class TipoTurno(StrEnum):
    """Que espera el usuario de este mensaje."""

    SOCIAL = "social"
    """Saludo, cortesia o despedida. Se responde sin modelo."""

    META = "meta"
    """Sobre el asistente o sobre como esta organizada la documentacion."""

    CONVERSACIONAL = "conversacional"
    """Continua el turno anterior: repregunta, correccion, formato, asentimiento."""

    DOCUMENTAL = "documental"
    """Consulta al contenido de la documentacion. El caso por defecto."""


def _normalizar(texto: str) -> str:
    limpio = (texto or "").strip().strip("!¡?¿.,;: \t\n")
    limpio = unicodedata.normalize("NFKD", limpio.lower())
    limpio = "".join(c for c in limpio if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", limpio).strip()


# --- Social -----------------------------------------------------------------

_SALUDOS = frozenset({
    "hola", "holaa", "ola", "buenas", "buenos dias", "buenas tardes", "buenas noches",
    "que tal", "que tal todo", "como estas", "como va", "como va todo", "que tal estas",
    "hey", "saludos", "buen dia", "hola normia", "buenas!", "hola que tal",
})
_CORTESIA = frozenset({
    "gracias", "muchas gracias", "mil gracias", "ok gracias", "vale gracias",
    "perfecto gracias", "genial gracias", "ok", "vale", "perfecto", "entendido",
    "de acuerdo", "genial", "excelente", "muy bien",
})
_DESPEDIDAS = frozenset({
    "adios", "hasta luego", "chao", "chau", "nos vemos", "hasta pronto", "bye",
    "hasta manana", "buenas noches gracias",
})

_SOCIALES = _SALUDOS | _CORTESIA | _DESPEDIDAS

# "Gracias, hasta luego" escalaba: era social en dos segmentos y la lista solo
# reconocia formulas enteras. Se parten por coma, punto y la conjuncion.
_SEPARADOR = re.compile(r"\s*(?:,|\.|;|\by\b)\s*")

MAX_CHARS_SOCIAL = 45


def clasificar_social(texto: str) -> str | None:
    """
    Que clase de formula social es, o None si no lo es.

    Exige que TODOS los segmentos sean sociales: "buenos dias, necesito la politica
    de seguridad" no entra, porque el segundo segmento pide algo.
    """
    limpio = _normalizar(texto)
    if not limpio or len(limpio) > MAX_CHARS_SOCIAL:
        return None

    segmentos = [s for s in _SEPARADOR.split(limpio) if s]
    if not segmentos or not all(s in _SOCIALES for s in segmentos):
        return None

    # La intencion la marca el ultimo segmento: "hola, gracias" cierra agradeciendo.
    ultimo = segmentos[-1]
    if ultimo in _DESPEDIDAS:
        return "despedida"
    if ultimo in _CORTESIA:
        return "cortesia"
    return "saludo"


# --- Meta: sobre el asistente o sobre el conjunto documental -----------------

_META_PATRONES = (
    r"\bque (puedes|sabes) hacer\b",
    r"\ben que (me )?(puedes )?ayuda",
    r"\bquien eres\b",
    r"\bcomo funcionas\b",
    r"\bque eres\b",
    r"\bpara que sirves\b",
    r"\bde que (tienes certeza|puedes hablar|hablas)\b",
    r"\bque (documentos|documentacion) (tienes|manejas|cubres|conoces)\b",
    r"\bestan versionad",
    r"\bque versiones (hay|tienes|tienen)\b",
    r"\btienes (informacion de )?la v\d",
    r"\bcuantos documentos\b",
    r"\bque (areas|tipos de documento)\b",
    r"\bayuda\b$",
)
_META_RE = re.compile("|".join(_META_PATRONES))


# --- Conversacional: continua el turno anterior -------------------------------

# Formulas que SOLO tienen sentido encadenadas a algo dicho antes. Sin turno
# previo no son conversacionales: son una consulta cualquiera mal formulada.
#
# Van en dos niveles segun su riesgo de falso positivo:
#
#  - INEQUIVOCAS: la formula ya identifica la intencion por si sola. "no, me
#    referia al de compras" nombra un documento y aun asi es una correccion.
#  - DEBILES: pistas que tambien aparecen en consultas legitimas. "y que dice el
#    manual sobre el alcance?" empieza por "y" y es documental de pleno derecho.
#    Para estas se exige ademas que el mensaje no traiga vocabulario del dominio.
_CONVERSACIONAL_INEQUIVOCO = re.compile("|".join((
    r"^(si|sip|claro|dale|adelante|correcto|por supuesto)([, ]+(por favor|claro|gracias|adelante))?$",
    r"^(no|nop|no gracias|mejor no)$",
    r"\b(mas (corto|breve|largo|detalle|detallado)|resumelo|resumemelo|amplia|"
    r"explicalo mejor|otra vez|de nuevo)\b",
    r"\b(en (una )?tabla|en (una )?lista|con vinetas|en puntos|en bullets)\b",
    r"\bno,? me refer",
    r"\bno es (eso|correcto|lo que)\b",
    r"\beso no es lo que dice\b",
    r"\bque te pregunte\b",
)))

_CONVERSACIONAL_DEBIL = re.compile("|".join((
    r"^(y|pero|entonces|ademas|tambien) ",
    r"\bla anterior\b",
    r"\b(leelo|leela|el completo|la completa)\b",
)))

# Si aparece cualquiera de estas, el mensaje habla de la documentacion y va al RAG
# aunque traiga una pista debil. Es la regla de oro hecha lista.
_LEXICO_DOMINIO = re.compile(r"\b("
    r"documento|documentos|manual|manuales|politica|politicas|procedimiento|procedimientos|"
    r"instructivo|formato|formatos|registro|clausula|seccion|apartado|norma|iso|version|"
    r"vigente|obsoleto|plazo|plazos|tiempo|tiempos|responsable|alcance|objetivo|"
    r"requisito|requisitos|auditoria|calidad|conformidad|hallazgo|correctiva|"
    r"proveedor|proveedores|compras|contrasena|contrasenas|respaldo|seguridad|"
    r"solicitud|solicitudes|incidencia|equipo|equipos"
    r")\b")

# Instrucciones sobre COMO debe comportarse el asistente. No son consultas al SGC
# y no dependen de que haya turno previo: "responde siempre en ingles" puede ser lo
# primero que alguien escriba. La cola tenia varias, escaladas como si fueran huecos
# de documentacion.
_INSTRUCCION_RE = re.compile("|".join((
    r"^responde (siempre |solo |unicamente )?(en|con)\b",
    r"^(habla|escribe|contesta) (siempre |solo )?(en|con)\b",
    r"\bno (me )?(cites|menciones|uses)\b",
    r"\b(se|sé) (mas |menos )?(breve|formal|informal|tecnico|conciso)\b",
    r"^(usa|utiliza) (un )?(tono|lenguaje)\b",
)))

# Intentos de saltarse las reglas NO son instrucciones legitimas: van por la ruta
# DOCUMENTAL, que es la mas estricta. Tratarlos como conversacion les quitaria el
# guardrail bloqueante, que es justo lo que el atacante busca.
_INYECCION_RE = re.compile("|".join((
    r"\bignora (tus|las|todas) ",
    r"\bolvida (lo anterior|todo|tus|tus reglas)",
    r"\bnuevas instrucciones\b",
    r"\bactua como\b",
    r"\bsystem prompt\b",
    r"\bsin importar (tus|las) reglas\b",
)))

# Un mensaje con un codigo de documento SIEMPRE es documental, por corto que sea.
_CODIGO_RE = re.compile(r"\b[a-z]{2,5}-[a-z]{2,5}-\d{1,3}\b")

MAX_CHARS_CONVERSACIONAL = 70


def clasificar_turno(texto: str, *, hay_turno_previo: bool = False) -> TipoTurno:
    """
    Tipo del turno. Ante la duda, DOCUMENTAL.

    `hay_turno_previo` importa para lo conversacional: "si, por favor" solo
    continua algo si hay algo que continuar. En el primer mensaje de una
    conversacion no hay nada a lo que asentir.
    """
    if clasificar_social(texto) is not None:
        return TipoTurno.SOCIAL

    limpio = _normalizar(texto)
    if not limpio:
        return TipoTurno.DOCUMENTAL

    # Un codigo de documento desempata siempre hacia documental.
    if _CODIGO_RE.search(limpio):
        return TipoTurno.DOCUMENTAL

    if _META_RE.search(limpio):
        return TipoTurno.META

    if _INYECCION_RE.search(limpio):
        return TipoTurno.DOCUMENTAL

    if _INSTRUCCION_RE.search(limpio):
        return TipoTurno.CONVERSACIONAL

    if hay_turno_previo and len(limpio) <= MAX_CHARS_CONVERSACIONAL:
        if _CONVERSACIONAL_INEQUIVOCO.search(limpio):
            return TipoTurno.CONVERSACIONAL
        if _CONVERSACIONAL_DEBIL.search(limpio) and not _LEXICO_DOMINIO.search(limpio):
            return TipoTurno.CONVERSACIONAL

    return TipoTurno.DOCUMENTAL


# --- Respuestas deterministas para lo social ---------------------------------
# Texto que LEE el usuario: va con acentos, a diferencia de los comentarios.

RESPUESTA_SALUDO = (
    "¡Hola! Soy NormIA: respondo sobre la documentación ISO vigente de la organización, "
    "citando siempre el documento y la sección de donde sale la respuesta. "
    "¿Qué necesitas consultar?"
)
RESPUESTA_CORTESIA = "¡A ti! Si te surge otra consulta sobre la documentación, aquí estoy."
RESPUESTA_DESPEDIDA = (
    "Hasta luego. Vuelve cuando necesites consultar algo del sistema de gestión de calidad."
)

_RESPUESTAS = {
    "saludo": RESPUESTA_SALUDO,
    "cortesia": RESPUESTA_CORTESIA,
    "despedida": RESPUESTA_DESPEDIDA,
}


def respuesta_social(texto: str) -> str | None:
    """Respuesta a una formula social, o None si el mensaje no lo es."""
    clase = clasificar_social(texto)
    return _RESPUESTAS.get(clase) if clase else None
