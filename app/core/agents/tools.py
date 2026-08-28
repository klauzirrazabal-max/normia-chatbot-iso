"""
Herramientas (function calling) que el LLM puede invocar ademas de responder texto.

Esquema en formato OpenAI-style, compatible con Ollama, Groq, vLLM y OpenRouter.

Las implementaciones tienen firmas distintas entre si (unas necesitan la sesion
de DB, otras el tenant, otras nada), asi que NO se pueden invocar con un
`fn(**args)` uniforme. `execute_tool` es el dispatcher explicito que inyecta el
contexto de servidor y pasa al modelo solo sus propios argumentos -- de paso
evita que un argumento alucinado por el modelo pise `tenant_id`.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.db_models import CapaAction, Finding

logger = logging.getLogger(__name__)

TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "register_finding",
            "description": (
                "Registra un hallazgo o posible no conformidad reportado por el usuario. "
                "Usala cuando el usuario describa una desviacion, incumplimiento o problema "
                "de calidad que deba quedar documentado."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "Descripcion del hallazgo, en las palabras del usuario",
                    }
                },
                "required": ["description"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_capa_status",
            "description": (
                "Consulta el estado de las acciones correctivas (CAPA) asociadas a un "
                "hallazgo ya registrado. Requiere el ID numerico del hallazgo."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "finding_id": {"type": "integer", "description": "ID del hallazgo"}
                },
                "required": ["finding_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_documentos",
            "description": (
                "Busca en el CATALOGO de documentos controlados por tema, area o tipo, y "
                "devuelve codigo, titulo, version y area de cada uno. Usala cuando el "
                "usuario pregunte QUE documentos existen sobre algo ('tienes politicas de "
                "TI?', 'que procedimientos hay de compras?', 'listame los manuales'), en "
                "vez de responder con fragmentos sueltos."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tema": {
                        "type": "string",
                        "description": (
                            "Copia las palabras del usuario TAL CUAL, incluido el TIPO si "
                            "lo menciona. Si pregunta por 'politicas de TI', pasa "
                            "'politicas de TI' -- no solo 'TI': el tipo es un filtro, y "
                            "perderlo devuelve tambien procedimientos y manuales del area. "
                            "Ej: 'politicas de TI', 'procedimientos de compras', "
                            "'manuales de calidad', 'auditoria'"
                        ),
                    }
                },
                "required": ["tema"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "leer_documento",
            "description": (
                "Lee un documento controlado. Usala cuando el usuario pida un resumen, "
                "una explicacion general o los pasos de un documento entero ('resumeme "
                "atencion de solicitudes', 'de que trata STI-PR-01'). "
                "Por defecto devuelve el INDICE de clausulas mas las clausulas con mas "
                "contenido: con eso RESPONDES la pregunta y ofreces al usuario elegir otra "
                "seccion. Pasa `seccion` para leer una clausula concreta, o `completo` "
                "solo si el usuario pide el documento entero."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "documento": {
                        "type": "string",
                        "description": (
                            "Codigo exacto (STI-PR-01) o parte del titulo "
                            "(atencion de solicitudes)"
                        ),
                    },
                    "seccion": {
                        "type": "string",
                        "description": (
                            "Numero de clausula a leer, ej. '7.6'. Incluye sus "
                            "subclausulas. Omitir para obtener el indice."
                        ),
                    },
                    "completo": {
                        "type": "boolean",
                        "description": (
                            "true solo si el usuario pidio explicitamente el documento "
                            "entero. Por defecto false."
                        ),
                    },
                },
                "required": ["documento"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ampliar_hallazgo",
            "description": (
                "Anade informacion a un hallazgo YA registrado. Usala SIEMPRE que el "
                "usuario amplie o precise algo sobre un hallazgo que acabas de registrar "
                "-- fecha, cliente, personal implicado, impacto. NUNCA vuelvas a llamar a "
                "register_finding para el mismo incidente: crearia una no conformidad "
                "duplicada."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "finding_id": {
                        "type": "integer",
                        "description": "El ID que devolvio register_finding.",
                    },
                    "detalles": {
                        "type": "string",
                        "description": "La informacion nueva, redactada de forma completa.",
                    },
                },
                "required": ["finding_id", "detalles"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describir_capacidades",
            "description": (
                "Describe QUE puede hacer este asistente y sobre que documentacion "
                "responde. Usala cuando el usuario pregunte por el asistente en si "
                "('que puedes hacer?', 'en que me ayudas?', 'quien eres?', 'como "
                "funcionas?', 'ayuda'), y NO por el contenido de un documento. "
                "Preguntar por las capacidades no es una consulta al SGC: no la "
                "escales a Calidad."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_quality",
            "description": (
                "Escala la conversacion al Responsable de Calidad. Usala cuando la pregunta "
                "sea ambigua, critica, o pida algo fuera de tu alcance (aprobar, autorizar o "
                "modificar un documento controlado)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "Motivo de la escalacion"}
                },
                "required": ["reason"],
                "additionalProperties": False,
            },
        },
    },
]

TOOL_NAMES = {t["function"]["name"] for t in TOOLS_SCHEMA}

# Herramientas cuyo uso implica derivar la conversacion a un humano.
ESCALATING_TOOLS = {"escalate_to_quality"}


def register_finding(
    db: Session, tenant_id: str, conversation_id: int | None, description: str
) -> dict[str, Any]:
    finding = Finding(
        tenant_id=tenant_id, conversation_id=conversation_id, description=description
    )
    db.add(finding)
    db.commit()
    db.refresh(finding)
    return {
        "finding_id": finding.id,
        "status": finding.status,
        "message": f"Hallazgo registrado con ID {finding.id}.",
    }


def get_capa_status(db: Session, finding_id: int) -> dict[str, Any]:
    finding = db.get(Finding, finding_id)
    if finding is None:
        return {
            "finding_id": finding_id,
            "error": "not_found",
            "message": f"No existe un hallazgo con ID {finding_id}.",
        }

    actions = db.query(CapaAction).filter_by(finding_id=finding_id).all()
    if not actions:
        return {
            "finding_id": finding_id,
            "finding_status": finding.status,
            "actions": [],
            "message": "No hay acciones correctivas registradas todavia para ese hallazgo.",
        }

    return {
        "finding_id": finding_id,
        "finding_status": finding.status,
        "actions": [
            {
                "description": a.description,
                "responsible": a.responsible,
                "due_date": a.due_date.isoformat() if a.due_date else None,
                "status": a.status,
            }
            for a in actions
        ],
    }


# Un tema y un tipo son cosas distintas, y confundirlos arruina la precision.
# "politicas de TI" nombra AMBOS: el tema es TI, el tipo es politica. Buscar con
# un OR de todo devolvia la Politica de la Calidad (es politica, no es de TI) y
# un Acuse de Recepcion (menciona politicas, es un formato). Cuando la consulta
# trae tema Y tipo, se exigen los dos.

# Tipo de documento -> segmento del codigo. En el SGC: AREA-TIPO-NUMERO.
_TIPOS = {
    "politica": "-PO-",
    "politicas": "-PO-",
    "procedimiento": "-PR-",
    "procedimientos": "-PR-",
    "manual": "-MN-",
    "manuales": "-MN-",
    "formato": "-FO-",
    "formatos": "-FO-",
    "instructivo": "-IN-",
    "instructivos": "-IN-",
    "plan": "-PL-",
    "planes": "-PL-",
    "organigrama": "-OR-",
    "organigramas": "-OR-",
    "alcance": "-AL-",
    "objetivo": "-OB-",
    "objetivos": "-OB-",
}

# Tema -> (prefijo de area o None, palabras que aparecen en titulo o area).
#
# El prefijo solo se usa cuando el termino ES un area del SGC. "Auditoria" no lo
# es: es un asunto DENTRO de Calidad, y mapearlo a "CAL" devolvia los 25
# documentos del area entera. Esos casos van con prefijo None y se buscan solo
# por titulo.
_TEMAS: dict[str, tuple[str | None, list[str]]] = {
    # OJO: NO incluir "informacion" aqui. El area es "SOPORTE DE TECNOLOGIA DE LA
    # INFORMACION", asi que "tecnologia" ya la cubre entera; anadir "informacion"
    # arrastraba "Control de Informacion Documentada" (CAL-PR-01), que es de
    # Calidad, a la lista de procedimientos de TI. En un sistema de control
    # documental esa palabra aparece por todas partes.
    "ti": ("STI", ["tecnologia", "tecnología"]),
    "sistemas": ("STI", ["tecnologia", "tecnología", "sistemas"]),
    "informatica": ("STI", ["tecnologia", "tecnología"]),
    "tecnologia": ("STI", ["tecnologia", "tecnología"]),
    "calidad": ("CAL", ["calidad"]),
    "compras": ("COM", ["compra", "proveedor"]),
    "proveedores": ("COM", ["proveedor"]),
    "rrhh": ("GTH", ["humana", "personal"]),
    "personal": ("GTH", ["humana", "personal"]),
    "inventario": ("INV", ["inventario"]),
    "inventarios": ("INV", ["inventario"]),
    "comercial": ("CMC", ["comercial", "cliente"]),
    "cliente": ("CMC", ["cliente"]),
    "conciliacion": ("CNC", ["conciliacion", "conciliación"]),
    "facturacion": ("FAC", ["facturacion", "facturación"]),
    "costos": ("CYP", ["costo", "presupuesto"]),
    "logistica": ("SPL", ["logistic", "carga", "almacen", "almacén"]),
    "gerencial": ("GGR", ["gerencial", "riesgo"]),
    "riesgos": (None, ["riesgo"]),
    "auditoria": (None, ["auditoria", "auditoría"]),
    "auditorias": (None, ["auditoria", "auditoría"]),
    "riesgo": (None, ["riesgo"]),
    "capacitacion": (None, ["capacitacion", "capacitación"]),
    "seguridad": (None, ["seguridad"]),
    "hallazgo": (None, ["no conforme", "correctiva"]),
    "reclamo": (None, ["reclamo"]),
    "reclamos": (None, ["reclamo"]),
}

_IRRELEVANTES = {"de", "del", "la", "el", "los", "las", "sobre", "para", "que", "hay", "tienes"}


def sin_acentos(texto: str) -> str:
    """Minusculas y sin diacriticos. La contraparte en Python de normia_unaccent()."""
    descompuesto = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in descompuesto if not unicodedata.combining(c)).lower()


def _unaccent(columna):
    """
    Expresion SQL equivalente a `sin_acentos` sobre una columna.

    La busqueda tiene que ser insensible a acentos en las DOS direcciones:

      * Los titulos vienen de nombres de archivo de macOS, en Unicode NFD ("i" +
        acento combinante). Los literales de este archivo son NFC. Bytes
        distintos: buscar "auditoria" no encontraba "Auditorias".

      * Y al contrario, la gente escribe sin acentos: "atencion de solicitudes"
        no encontraba "Atencion de Solicitudes Tecnologicas".

    El fallo era silencioso -- cero resultados, o sea el bot diciendo "no tengo
    esa informacion" sobre documentos que si existen. Hay indices funcionales
    sobre esta misma expresion (migracion 004) para que no cueste un scan.
    """
    return func.lower(func.normia_unaccent(columna))


def _como(columna, termino: str):
    """Coincidencia parcial insensible a acentos y mayusculas."""
    return _unaccent(columna).like(f"%{sin_acentos(termino)}%")

MAX_CATALOG_RESULTS = 25


def _clasificar_terminos(tema: str) -> tuple[list[str], list[tuple[str, list[str]]], list[str]]:
    """Separa la consulta en tipos de documento, temas conocidos y palabras libres."""
    palabras = [w for w in re.split(r"[^\w]+", tema.lower()) if w and w not in _IRRELEVANTES]

    tipos: list[str] = []
    temas: list[tuple[str, list[str]]] = []
    libres: list[str] = []

    for palabra in palabras:
        if palabra in _TIPOS:
            tipos.append(_TIPOS[palabra])
        elif palabra in _TEMAS:
            temas.append(_TEMAS[palabra])
        elif len(palabra) > 2:
            libres.append(palabra)

    return tipos, temas, libres


def ampliar_hallazgo(
    db: Session, tenant_id: str, finding_id: int, detalles: str
) -> dict[str, Any]:
    """
    Anade informacion a un hallazgo existente.

    Existe por un fallo concreto: el bot registraba la no conformidad y despues
    pedia fecha, cliente y personal implicado. Cuando el usuario los daba, no
    tenia donde ponerlos -- solo existia register_finding -- asi que creaba un
    SEGUNDO hallazgo del mismo incidente. Dos no conformidades por un mismo hecho
    inflan los indicadores y duplican el analisis de causa.

    Se anade, no se reescribe: la descripcion original es lo que reporto el
    usuario y no debe perderse al precisarla.
    """
    detalles = (detalles or "").strip()
    if not detalles:
        return {"error": "invalid_arguments", "message": "Falta 'detalles'."}

    hallazgo = (
        db.query(Finding).filter_by(id=finding_id, tenant_id=tenant_id).one_or_none()
    )
    if hallazgo is None:
        return {
            "error": "not_found",
            "message": (
                f"No existe el hallazgo {finding_id}. Usa el ID que devolvio "
                "register_finding; no lo inventes."
            ),
        }

    hallazgo.description = f"{hallazgo.description}\n\nAmpliacion: {detalles}"
    db.commit()
    logger.info("tool.finding_extended", extra={"finding_id": finding_id})
    return {
        "finding_id": hallazgo.id,
        "estado": hallazgo.status,
        "message": "Informacion anadida al hallazgo existente. No lo registres de nuevo.",
    }


def describir_capacidades(db: Session, tenant_id: str) -> dict[str, Any]:
    """
    Que sabe hacer el asistente, con las cifras reales de este tenant.

    Existe por un fallo concreto: a "que puedes hacer?" el bot respondia "no tengo
    informacion suficiente" y derivaba la consulta a Calidad. El guardrail de
    fundamentacion esta para impedir que se INVENTE contenido documental, y una
    pregunta sobre el propio asistente no afirma nada de ningun documento.

    Se resuelve como herramienta y no como excepcion en el orquestador porque asi
    la respuesta se apoya en un resultado verificable del servidor -- las cifras
    salen de la base -- y el guardrail deja de bloquear por la via normal, sin
    abrirle un agujero.
    """
    from app.models.db_models import Document

    filas = (
        db.query(Document.area, Document.code)
        .filter(Document.tenant_id == tenant_id, Document.status == "vigente")
        .all()
    )
    areas = sorted({a for a, _ in filas if a})
    return {
        "asistente": "NormIA",
        "documentos_vigentes": len(filas),
        "areas": areas,
        "puedo": [
            "Responder que dice un procedimiento, politica o manual vigente, citando "
            "codigo, version y seccion.",
            "Decirte que documentos existen sobre un tema.",
            "Resumir un documento y llevarte a la seccion que te interese.",
            "Registrar un hallazgo o desviacion y devolverte su identificador.",
            "Consultar el estado de una accion correctiva.",
            "Derivar a Calidad lo que la documentacion no cubre.",
        ],
        "no_puedo": [
            "Aprobar, autorizar ni modificar documentos controlados.",
            "Responder desde versiones obsoletas.",
            "Responder sobre temas que no esten en la documentacion vigente.",
        ],
        "instruccion": (
            "Resume esto con tus palabras, en tono cercano y sin listar las tres "
            "cosas que no puedes salvo que venga a cuento. Cierra invitando a "
            "preguntar. NO cites ningun codigo de documento aqui."
        ),
    }


def buscar_documentos(db: Session, tenant_id: str, tema: str) -> dict[str, Any]:
    """
    Consulta el CATALOGO, no los fragmentos.

    "Tienes informacion sobre politicas de TI?" es una pregunta de inventario, no
    de contenido. El RAG busca fragmentos semanticamente parecidos y devuelve
    cosas como la seccion "ABREVIATURAS Y DEFINICIONES" -- parecida al tema,
    inutil como respuesta. El catalogo responde lo que de verdad se pregunta:
    que documentos existen, como se llaman y en que version estan.
    """
    from app.models.db_models import Document

    if not (tema or "").strip():
        return {"error": "invalid_arguments", "message": "Falta el tema a buscar."}

    tipos, temas, libres = _clasificar_terminos(tema)

    filtros = [Document.tenant_id == tenant_id, Document.status == "vigente"]

    if tipos:
        filtros.append(or_(*[Document.code.ilike(f"%{t}%") for t in tipos]))

    if temas:
        condiciones = []
        for prefijo, palabras in temas:
            if prefijo:
                condiciones.append(Document.code.ilike(f"{prefijo}-%"))
            for palabra in palabras:
                condiciones.append(_como(Document.title, palabra))
                if prefijo:
                    condiciones.append(_como(Document.area, palabra))
        filtros.append(or_(*condiciones))

    # Palabras que no reconocemos: se aceptan en titulo o area. Si no hay tema ni
    # tipo, son el unico criterio; si los hay, amplian sin relajar los anteriores.
    if libres and not (tipos or temas):
        filtros.append(
            or_(
                *[
                    cond
                    for palabra in libres
                    for cond in (
                        _como(Document.title, palabra),
                        _como(Document.area, palabra),
                        Document.code.ilike(f"%{palabra}%"),
                    )
                ]
            )
        )

    documentos = (
        db.query(Document)
        .filter(*filtros)
        .order_by(Document.code)
        .limit(MAX_CATALOG_RESULTS + 1)
        .all()
    )

    truncado = len(documentos) > MAX_CATALOG_RESULTS
    documentos = documentos[:MAX_CATALOG_RESULTS]

    if not documentos:
        return {
            "tema": tema,
            "documentos": [],
            "message": (
                f"No hay documentos vigentes que coincidan con '{tema}'. "
                "Dilo claramente; no afirmes que existen."
            ),
        }

    resultado = {
        "tema": tema,
        "total": len(documentos),
        "documentos": [
            {
                "codigo": d.code,
                "version": d.version,
                "titulo": d.title,
                "area": d.area,
                "vigente_desde": d.effective_date.isoformat() if d.effective_date else None,
            }
            for d in documentos
        ],
        "message": (
            "Presentalos al usuario por TITULO, con su codigo y version entre parentesis. "
            "Estos son los documentos que existen; no describas su contenido salvo que lo "
            "tengas en los fragmentos recuperados."
        ),
    }
    if truncado:
        resultado["message"] += f" Se muestran los primeros {MAX_CATALOG_RESULTS}; hay mas."
    return resultado


# Tope de texto que se devuelve de un documento. Con 256K de contexto sobra,
# pero conviene un limite explicito: un manual largo podria desplazar el resto
# de la conversacion, y es mejor avisar que truncar en silencio.
MAX_DOCUMENT_CHARS = 24_000


def _resolver_documento(db: Session, tenant_id: str, referencia: str):
    """Encuentra el documento por codigo exacto, luego por prefijo, luego por titulo."""
    from app.models.db_models import Document

    referencia = referencia.strip()
    base = db.query(Document).filter(
        Document.tenant_id == tenant_id, Document.status == "vigente"
    )

    # 1. Codigo tal cual (o con calificador: "CAL-FO-13 (Compras)").
    exacto = base.filter(Document.code.ilike(f"{referencia}%")).order_by(Document.code).all()
    if exacto:
        return exacto

    # 2. Titulo, insensible a acentos en ambas direcciones.
    return base.filter(_como(Document.title, referencia)).order_by(Document.code).all()


# Titulos de clausulas de tramite. Existen en todos los documentos ISO y casi
# nunca contienen la respuesta a una pregunta sustantiva.
_TITULOS_DE_TRAMITE = (
    "objetivo",
    "alcance",
    "documentos de referencia",
    "abreviaturas",
    "responsabilidad",
    "control de cambios",
)

# Cuantas clausulas sustantivas se devuelven con el indice, y cuanto texto.
CLAUSULAS_SUSTANTIVAS = 4
MAX_CHARS_SUSTANCIA = 9000

_NUMERO_INICIAL_RE = re.compile(r"^\s*\d+(?:\.\d+)*\.?\s*")
# Un codigo de documento cortando el titulo: "DOCUMENTOS DE REFERENCIA GTH-PR-01 ..."
_ES_CODIGO_RE = re.compile(r"^[A-Z]{2,5}-[A-Z]{2,5}-\d{1,3}$")
# Conectores que van en minusculas dentro de un titulo en mayusculas.
_CONECTORES = {"DE", "DEL", "LA", "EL", "LOS", "LAS", "Y", "E", "EN", "PARA", "POR", "A"}

MAX_TITULO_INDICE = 72


def _es_clausula_de_tramite(contenido: str) -> bool:
    """True si la clausula es de tramite, mirando su encabezado."""
    encabezado = _NUMERO_INICIAL_RE.sub("", (contenido or "")[:90]).lower()
    return any(encabezado.startswith(t) for t in _TITULOS_DE_TRAMITE)


def _titulo_de_seccion(contenido: str) -> tuple[str, bool]:
    """
    Etiqueta corta para una clausula, para armar el indice.

    Las secciones de primer nivel llevan su titulo en MAYUSCULAS ("OBJETIVO",
    "CONDICIONES GENERALES") seguido del cuerpo; las subclausulas son prosa
    directa sin titulo. Se toma la racha inicial de palabras en mayusculas y se
    corta en la primera que no lo sea.

    Hacerlo con un solo regex fallaba cuando el titulo iba seguido de mas
    mayusculas -- "DOCUMENTOS DE REFERENCIA GTH-PR-01 Procedimiento..." se
    llevaba el codigo y la prosa. Palabra por palabra es mas simple y acierta.

    El indice es lo que el usuario ve como opciones, asi que tiene que leerse
    como un menu, no como un volcado.
    """
    texto = _NUMERO_INICIAL_RE.sub("", (contenido or "").replace("\n", " ")).strip()
    if not texto:
        return "(sin titulo)", False

    palabras = texto.split()
    titulo: list[str] = []
    for palabra in palabras:
        limpia = palabra.strip(".,;:")
        if not limpia:
            break
        # Un codigo de documento, un separador de tabla o un token con dos puntos
        # marcan el final del titulo y el comienzo del cuerpo.
        if _ES_CODIGO_RE.match(limpia) or "|" in palabra or ":" in palabra:
            break
        if limpia.upper() != limpia or limpia.isdigit():
            break
        titulo.append(limpia)
        if len(" ".join(titulo)) > MAX_TITULO_INDICE:
            break

    # Una sola palabra corta ("TI") no es un titulo utilizable.
    if titulo and len(" ".join(titulo)) >= 4 and not (
        len(titulo) == 1 and titulo[0] in _CONECTORES
    ):
        frase = " ".join(titulo)
        return frase[0] + frase[1:].lower(), True

    if len(texto) <= MAX_TITULO_INDICE:
        return texto, False
    return f"{texto[:MAX_TITULO_INDICE].rsplit(' ', 1)[0]}...", False


def leer_documento(
    db: Session,
    tenant_id: str,
    documento: str,
    seccion: str | None = None,
    completo: bool = False,
) -> dict[str, Any]:
    """
    Devuelve un documento completo, clausula por clausula.

    Existe porque resumir no es buscar. Pedir "un resumen de atencion de
    solicitudes" por busqueda vectorial trae 4 fragmentos de un documento de 20
    clausulas -- y encima mezclados con otros documentos, porque "atencion de
    solicitudes" se parece semanticamente a "atencion de reclamos". El modelo
    terminaba diciendo con razon que no podia resumir. Un resumen es una
    operacion a nivel de DOCUMENTO, no de fragmento.
    """
    from app.models.db_models import DocumentChunk

    if not (documento or "").strip():
        return {"error": "invalid_arguments", "message": "Falta el documento a leer."}

    candidatos = _resolver_documento(db, tenant_id, documento)

    if not candidatos:
        return {
            "documento": documento,
            "error": "not_found",
            "message": (
                f"No encontre un documento vigente que corresponda a '{documento}'. "
                "Usa buscar_documentos para ver que existe; no inventes su contenido."
            ),
        }

    if len(candidatos) > 1:
        return {
            "documento": documento,
            "error": "ambiguous",
            "opciones": [
                {"codigo": d.code, "version": d.version, "titulo": d.title}
                for d in candidatos[:10]
            ],
            "message": (
                "La referencia coincide con varios documentos. Preguntale al usuario "
                "cual de estos quiere antes de continuar."
            ),
        }

    doc = candidatos[0]
    chunks = (
        db.query(DocumentChunk)
        .filter_by(document_id=doc.id)
        .order_by(DocumentChunk.id)
        .all()
    )

    cabecera = {
        "codigo": doc.code,
        "version": doc.version,
        "titulo": doc.title,
        "area": doc.area,
        "vigente_desde": doc.effective_date.isoformat() if doc.effective_date else None,
        "total_clausulas": len(chunks),
    }

    indice = []
    for c in chunks:
        if not c.section:
            continue
        titulo, con_titulo = _titulo_de_seccion(c.content)
        # `tiene_titulo` distingue una clausula con encabezado propio ("Registro
        # del hallazgo") de un extracto de prosa. Las primeras se leen bien como
        # opcion de menu; las segundas no.
        indice.append({"seccion": c.section, "titulo": titulo, "tiene_titulo": con_titulo})

    # --- Una clausula concreta (y sus subclausulas) ---
    if seccion:
        pedida = seccion.strip().rstrip(".")
        elegidos = [
            c
            for c in chunks
            if c.section and (c.section == pedida or c.section.startswith(f"{pedida}."))
        ]
        if not elegidos:
            return {
                **cabecera,
                "error": "section_not_found",
                "indice": indice,
                "message": (
                    f"El documento no tiene una clausula {pedida}. Ofrecele al usuario "
                    "las que si existen, listadas en 'indice'."
                ),
            }
        return {
            **cabecera,
            "secciones": [
                {"seccion": c.section, "contenido": c.content} for c in elegidos
            ],
            "message": (
                f"Clausula {pedida} de {doc.code} {doc.version}. Responde con base en "
                "este texto y cita la seccion exacta."
            ),
        }

    # --- Documento entero (solo si lo pidieron) ---
    if completo:
        secciones: list[dict[str, Any]] = []
        acumulado = 0
        truncado = False
        for chunk in chunks:
            if acumulado + len(chunk.content) > MAX_DOCUMENT_CHARS:
                truncado = True
                break
            secciones.append({"seccion": chunk.section, "contenido": chunk.content})
            acumulado += len(chunk.content)

        mensaje = (
            f"Contenido completo de {doc.code} {doc.version}. Resume con base en estas "
            "clausulas y cita la seccion de cada afirmacion."
        )
        if truncado:
            mensaje += (
                f" ATENCION: se trunco en {len(secciones)} de {len(chunks)} clausulas "
                "por longitud. Dilo al usuario si tu resumen queda incompleto."
            )
        return {
            **cabecera,
            "clausulas_incluidas": len(secciones),
            "secciones": secciones,
            "message": mensaje,
        }

    # --- Por defecto: indice + las clausulas con mas sustancia ---
    #
    # Devolver las 20 clausulas para un "resumeme esto" producia un muro de texto.
    # Pero devolver solo las primeras (Objetivo, Alcance, Documentos de
    # referencia) era peor: el modelo respondia sobre el DOCUMENTO en vez de sobre
    # el tema. Preguntando "que es el sistema de calidad" contaba el objetivo del
    # Manual y nunca veia "14.2 NUESTROS PROCESOS", que es donde se define.
    #
    # Asi que se eligen las clausulas mas EXTENSAS que no sean de tramite. La
    # extension es un proxy imperfecto pero eficaz de donde esta el contenido: en
    # un procedimiento ISO, la clausula larga es la que describe el proceso.
    candidatas = [
        c for c in chunks if c.section and not _es_clausula_de_tramite(c.content)
    ]
    candidatas.sort(key=lambda c: len(c.content), reverse=True)

    sustancia: list[dict[str, Any]] = []
    acumulado = 0
    for chunk in candidatas[:CLAUSULAS_SUSTANTIVAS]:
        if acumulado + len(chunk.content) > MAX_CHARS_SUSTANCIA:
            break
        sustancia.append({"seccion": chunk.section, "contenido": chunk.content})
        acumulado += len(chunk.content)

    # Se devuelven en el orden del documento, no por tamano.
    orden = {c.section: i for i, c in enumerate(chunks)}
    sustancia.sort(key=lambda s: orden.get(s["seccion"], 0))

    return {
        **cabecera,
        "indice": indice,
        "secciones": sustancia,
        "message": (
            f"Indice de {doc.code} {doc.version} y sus clausulas con mas contenido. "
            "RESPONDE LA PREGUNTA con base en esas clausulas: explica en que consiste "
            "el tema, no de que trata el documento. Luego ofrecele al usuario elegir "
            "otra seccion del indice si quiere mas detalle. Si necesitas una clausula "
            "que no esta aqui, vuelve a llamar a leer_documento con `seccion`."
        ),
    }


def escalate_to_quality(
    reason: str,
    db: Session | None = None,
    tenant_id: str | None = None,
    conversation_id: int | None = None,
    question: str = "",
    channel: str | None = None,
    trigger: str = "fuera_de_alcance",
) -> dict[str, Any]:
    """
    Deriva la consulta al Responsable de Calidad, DEJANDO REGISTRO.

    Antes solo escribia una linea de log: el bot afirmaba haber derivado la
    consulta y nadie la recibia. Se contaron 39 escalaciones sin un solo
    registro. En cumplimiento, prometer una accion y no ejecutarla es peor que
    declararse incapaz -- queda un rastro falso de que algo se hizo.

    Devolver el ID permite que la respuesta sea verificable ("queda registrada
    como #42") en vez de una frase amable.
    """
    from app.models.db_models import Escalation

    if db is None or tenant_id is None:
        # Camino degradado: sin sesion no se puede registrar. Se avisa en WARNING
        # para que no pase inadvertido, porque implica una promesa incumplida.
        logger.warning("tool.escalated_unpersisted", extra={"reason": reason})
        return {
            "escalated": True,
            "reason": reason,
            "message": "La consulta fue derivada al Responsable de Calidad.",
        }

    escalation = Escalation(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        channel=channel,
        question=question or "(sin registrar)",
        reason=reason,
        trigger=trigger,
    )
    db.add(escalation)
    db.commit()
    db.refresh(escalation)

    logger.info(
        "tool.escalated",
        extra={"escalation_id": escalation.id, "trigger": trigger, "reason": reason},
    )
    return {
        "escalated": True,
        "escalation_id": escalation.id,
        "reason": reason,
        "message": (
            f"La consulta quedo registrada como escalacion #{escalation.id} para el "
            "Responsable de Calidad. Menciona ese numero al usuario para que pueda "
            "darle seguimiento."
        ),
    }


def parse_arguments(raw: Any) -> dict[str, Any]:
    """
    Los argumentos de un tool call llegan como string JSON (a veces como dict).
    Nunca hacer matching de texto sobre el string serializado: parsear siempre.
    """
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"Argumentos de herramienta no son JSON valido: {raw!r}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"Argumentos de herramienta no son un objeto: {parsed!r}")
    return parsed


def execute_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    db: Session,
    tenant_id: str,
    conversation_id: int | None,
    question: str = "",
    channel: str | None = None,
) -> dict[str, Any]:
    """
    Dispatcher explicito. El contexto de servidor (db, tenant_id, conversation_id,
    la pregunta original y el canal) lo inyecta el servidor, NUNCA el modelo.
    """
    if name == "ampliar_hallazgo":
        try:
            finding_id = int(arguments["finding_id"])
        except (KeyError, TypeError, ValueError):
            return {"error": "invalid_arguments", "message": "Falta 'finding_id'."}
        return ampliar_hallazgo(db, tenant_id, finding_id, str(arguments.get("detalles", "")))

    if name == "describir_capacidades":
        return describir_capacidades(db, tenant_id)

    if name == "register_finding":
        description = str(arguments.get("description", "")).strip()
        if not description:
            return {"error": "invalid_arguments", "message": "Falta 'description'."}
        return register_finding(db, tenant_id, conversation_id, description)

    if name == "get_capa_status":
        try:
            finding_id = int(arguments["finding_id"])
        except (KeyError, TypeError, ValueError):
            return {
                "error": "invalid_arguments",
                "message": "Falta 'finding_id' o no es un numero entero.",
            }
        return get_capa_status(db, finding_id)

    if name == "leer_documento":
        seccion = arguments.get("seccion")
        return leer_documento(
            db,
            tenant_id,
            str(arguments.get("documento", "")),
            seccion=str(seccion) if seccion else None,
            completo=bool(arguments.get("completo", False)),
        )

    if name == "buscar_documentos":
        return buscar_documentos(db, tenant_id, str(arguments.get("tema", "")))

    if name == "escalate_to_quality":
        reason = str(arguments.get("reason", "")).strip() or "no especificado"
        return escalate_to_quality(
            reason,
            db=db,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            question=question,
            channel=channel,
        )

    return {
        "error": "unknown_tool",
        "message": f"La herramienta '{name}' no existe. Disponibles: {sorted(TOOL_NAMES)}",
    }


# --- Recuperacion de tool calls que el modelo escribio como texto ------------
#
# Con contexto largo (fragmentos ISO + varias herramientas), un modelo local a
# veces escribe la llamada a herramienta DENTRO del texto de la respuesta en vez
# de emitirla como tool_call estructurado. El turno "funciona": no hay error, la
# herramienta nunca se ejecuta, y el usuario ve JSON crudo en pantalla.
#
# Se reconocen las formas que emiten los modelos de la familia Qwen/Llama:
#   <tool_call>{"name": "...", "arguments": {...}}</tool_call>
#   ```json\n{"name": "...", "arguments": {...}}\n```
#   nombre_de_herramienta\n{"arg": "..."}
#   {"name": "...", "arguments": {...}}
#
# Solo se acepta si el nombre esta en TOOL_NAMES: nunca se ejecuta algo que el
# modelo se haya inventado.

_TOOL_CALL_TAG_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_NAME_THEN_JSON_RE = re.compile(
    r"^\s*(?P<name>[a-z_]{3,40})\s*[:\n]\s*(?P<args>\{.*\})\s*$", re.DOTALL
)
_BARE_JSON_RE = re.compile(r"^\s*(\{.*\})\s*$", re.DOTALL)


def _as_call(name: str, arguments: Any) -> dict[str, Any] | None:
    if name not in TOOL_NAMES:
        return None
    if isinstance(arguments, dict):
        arguments = json.dumps(arguments, ensure_ascii=False)
    return {
        "id": f"recovered_{name}",
        "type": "function",
        "function": {"name": name, "arguments": arguments or "{}"},
    }


def recover_tool_call_from_text(text: str) -> dict[str, Any] | None:
    """Devuelve un tool_call en formato OpenAI si el texto contiene uno, o None."""
    if not text or "{" not in text:
        return None

    for pattern in (_TOOL_CALL_TAG_RE, _JSON_FENCE_RE, _BARE_JSON_RE):
        match = pattern.search(text)
        if not match:
            continue
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "name" in payload:
            call = _as_call(str(payload["name"]), payload.get("arguments", {}))
            if call:
                return call

    match = _NAME_THEN_JSON_RE.match(text.strip())
    if match:
        try:
            arguments = json.loads(match.group("args"))
        except json.JSONDecodeError:
            return None
        return _as_call(match.group("name"), arguments)

    return None


_TOOL_NAMES_ALTERNATION = "|".join(sorted(TOOL_NAMES))
_TOOL_PREFIX_RE = re.compile(
    r"^\s*(?:<tool_call>|```json|```)?\s*"
    rf"(?P<name>{_TOOL_NAMES_ALTERNATION})\s*[:\-\u2013]\s*",
    re.IGNORECASE,
)


def strip_tool_name_prefix(text: str) -> str:
    """
    Quita el nombre de la herramienta cuando el modelo lo antepone a la respuesta
    final ("escalate_to_quality: Solicitud derivada a Calidad...").

    Es un artefacto del modelo, no informacion para el usuario: filtrado deja una
    respuesta en lenguaje natural en vez de una que parece un log interno.
    """
    if not text:
        return text
    cleaned = _TOOL_PREFIX_RE.sub("", text, count=1)
    return cleaned.strip()


# Una linea que ARRANCA con el nombre de una herramienta es filtracion del
# formato interno, no contenido para el usuario. Anclar al inicio de linea evita
# falsos positivos: una respuesta que menciona la herramienta de pasada
# ("puedo escalarlo a Calidad si quieres") no se toca.
_LEAKED_LINE_RE = re.compile(
    rf"^\s*(?:<tool_call>|```json|```|\*\*)?\s*(?P<name>{_TOOL_NAMES_ALTERNATION})\b.*$",
    re.IGNORECASE,
)
# Linea de argumento que suele seguir a la anterior: "Motivo: ...", "reason: ..."
_LEAKED_ARG_RE = re.compile(
    r"^\s*(?:motivo|razon|razón|reason|description|descripcion|descripción|"
    r"finding_id|argumentos?|arguments?)\s*[:=]",
    re.IGNORECASE,
)


def split_leaked_tool_mentions(text: str) -> tuple[str, set[str]]:
    """
    Separa la respuesta del formato interno que el modelo haya filtrado.

    Devuelve (texto limpio, nombres de herramienta mencionados). El modelo local
    varia la forma en que filtra la llamada -- JSON, etiqueta <tool_call>,
    "nombre\\nMotivo: ..." -- asi que en vez de perseguir cada formato se retira
    cualquier linea encabezada por un nombre de herramienta conocido.
    """
    if not text:
        return text, set()

    mentioned: set[str] = set()
    kept: list[str] = []
    skip_arg_lines = False

    for line in text.splitlines():
        match = _LEAKED_LINE_RE.match(line)
        if match:
            mentioned.add(match.group("name").lower())
            skip_arg_lines = True
            continue
        if skip_arg_lines:
            if _LEAKED_ARG_RE.match(line) or not line.strip() or line.strip() in "{}```":
                continue
            skip_arg_lines = False
        kept.append(line)

    cleaned = "\n".join(kept).strip()
    # Limpieza de restos: fences vacios y llaves sueltas que quedaron colgando.
    cleaned = re.sub(
        r"^\s*(?:```(?:json)?|</?tool_call>|\{|\})\s*$", "", cleaned, flags=re.MULTILINE
    )
    return cleaned.strip(), mentioned
