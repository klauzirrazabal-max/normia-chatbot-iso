"""
Construye el dataset de fine-tuning (LoRA) de NormIA a partir de lo que el
proyecto ya tiene verificado.

No se inventa un corpus nuevo: se ensambla el que ya existe y que ya paso por
verificacion automatica.

  data/faq/faq_<tenant>.json         -> 730 pares pregunta/respuesta VERIFICADOS
                                        contra el texto de su clausula
  data/faq/faq_<tenant>_rechazos.md  -> los descartes, con el motivo de cada uno
  data/knowledge_base/<tenant>/      -> los PDFs, para reconstruir las clausulas
  app/core/agents/tools.py           -> los esquemas de las 7 herramientas

LA REGLA QUE HACE UTIL ESTE DATASET: cada ejemplo se arma con el MISMO
`_build_messages` del orquestador que corre en produccion. Si el entrenamiento
usara un formato de prompt propio -- aunque fuera parecido -- el modelo
aprenderia a responder a un prompt que nunca va a ver, y la mejora no
transferiria a la aplicacion.

Y la segunda regla: las preguntas del banco de pruebas NO entran al
entrenamiento. Si entraran, `benchmark_models.py` dejaria de medir y pasaria a
confirmar lo que ya se memorizo.

Uso:
    uv run python scripts/build_finetune_dataset.py --tenant empresa-demo-iso
    uv run python scripts/build_finetune_dataset.py --tenant demo-publica --out data/finetune-demo
"""

from __future__ import annotations

import argparse
import ast
import json
import logging
import random
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.agents.tools import TOOLS_SCHEMA  # noqa: E402
from app.core.guardrails.grounding_check import (  # noqa: E402
    extract_cited_codes,
    response_cites_source,
)
from app.core.orchestrator import DEFAULT_SYSTEM_PROMPT, _build_messages  # noqa: E402
from app.core.rag.ingestion import (  # noqa: E402
    chunk_by_section,
    extract_full_text,
    load_metadata,
)
from app.core.rag.retriever import (  # noqa: E402
    RetrievalResult,
    RetrievedChunk,
    RetrievedFaq,
)

logger = logging.getLogger("build_finetune_dataset")

# El prompt de sistema con el que se arma cada ejemplo. Se resuelve en tiempo de
# ejecucion desde la base, igual que hace `_system_prompt` en el orquestador: el
# tenant tiene el suyo y es MUY distinto del DEFAULT -- 5.500 caracteres frente a
# 400, y con reglas que contradicen lo que parece natural (atribuir por titulo y
# no por codigo). Entrenar con el DEFAULT ensenaria a obedecer instrucciones que
# el modelo no va a recibir.
_SYSTEM_PROMPT = DEFAULT_SYSTEM_PROMPT

RAIZ = Path(__file__).resolve().parent.parent
BENCHMARK = RAIZ / "scripts" / "benchmark_models.py"


# --- Ejemplo -----------------------------------------------------------------


@dataclass
class Ejemplo:
    """Un ejemplo de entrenamiento, con la traza de donde salio."""

    familia: str
    grupo: str  # clave de agrupacion para el split: evita fuga entre particiones
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None
    origen: str = ""

    def to_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {"messages": fundir_sistemas(self.messages)}
        if self.tools:
            record["tools"] = self.tools
        return record


def fundir_sistemas(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Funde los mensajes de sistema en uno solo, conservando el orden.

    `_build_messages` emite TRES mensajes de sistema -- prompt del tenant, bloque
    de FAQ y bloque de contexto -- porque el endpoint compatible con OpenAI de
    Ollama los acepta. La plantilla de chat de Qwen no: falla con "System message
    must be at the beginning".

    El contenido y su orden no cambian; cambia el envoltorio. Es la unica
    diferencia entre el prompt de entrenamiento y el de produccion, y es
    inevitable mientras el modelo se entrene con la plantilla de HuggingFace.

    OJO AL SERVIR: si el adaptador se sirve con `mlx_lm.server`, esa misma
    plantilla se aplicara a las peticiones reales, y NormIA manda los tres
    mensajes por separado. Habra que fundirlos tambien en el orquestador, o el
    servidor rechazara cada consulta.
    """
    sistemas = [m["content"] for m in messages if m["role"] == "system" and m.get("content")]
    resto = [m for m in messages if m["role"] != "system"]
    if not sistemas:
        return resto
    return [{"role": "system", "content": "\n\n".join(sistemas)}, *resto]


# --- Reconstruccion de las clausulas ----------------------------------------
#
# Se leen los PDFs con la MISMA ingesta de la aplicacion. Reimplementar el
# troceado aqui significaria entrenar sobre un texto que no es el que el modelo
# recibe en produccion -- el encabezado numerado, la reparacion de saltos de
# linea y el renderizado de tablas cambian el contenido de forma sustancial.


@dataclass
class Clausula:
    code: str
    version: str
    title: str
    section: str | None
    content: str


def reconstruir_clausulas(tenant_dir: Path) -> dict[tuple[str, str], Clausula]:
    """
    Indexa las clausulas por (codigo, seccion), que es como las nombra el FAQ.

    La clave del FAQ es "<codigo>§<seccion o '->'", asi que el indice usa "-"
    para las clausulas sin seccion y las dos partes casan sin traducir nada.
    """
    meta = load_metadata(tenant_dir)
    indice: dict[tuple[str, str], Clausula] = {}
    sin_texto: list[str] = []

    for filename, fila in meta.items():
        pdf = tenant_dir / filename
        if not pdf.exists():
            sin_texto.append(f"{filename}: no esta en disco")
            continue
        try:
            texto = extract_full_text(pdf)
        except Exception as exc:  # noqa: BLE001 - un PDF roto no debe tumbar la corrida
            sin_texto.append(f"{filename}: {exc}")
            continue
        if not texto.strip():
            sin_texto.append(f"{filename}: sin texto util (escaneado sin OCR?)")
            continue

        for seccion, contenido in chunk_by_section(texto):
            indice[(fila["code"], seccion or "-")] = Clausula(
                code=fila["code"],
                version=fila["version"],
                title=fila["title"],
                section=seccion,
                content=contenido,
            )

    for aviso in sin_texto:
        logger.warning("clausula.no_reconstruida %s", aviso)
    return indice


# --- Armado del prompt, identico al de produccion ----------------------------


def _a_chunk(clausula: Clausula, chunk_id: int = 1) -> RetrievedChunk:
    """La clausula, en la forma exacta que ve el retriever en produccion."""
    return RetrievedChunk(
        chunk_id=chunk_id,
        content=clausula.content,
        code=clausula.code,
        version=clausula.version,
        section=clausula.section,
        distance=0.30,
        title=clausula.title,
    )


def mensajes_con_contexto(
    pregunta: str,
    clausulas: list[Clausula],
    faqs: list[tuple[str, str, Clausula]] | None = None,
) -> list[dict[str, Any]]:
    """
    Reproduce el prompt exacto que el orquestador arma en produccion.

    Se llama a `_build_messages` del propio orquestador en vez de copiar su
    formato: cualquier cambio futuro en los bloques de sistema se propaga solo,
    y no hay forma de que el dataset se desincronice del codigo sin que los
    tests lo noten.
    """
    chunks = [_a_chunk(c, i) for i, c in enumerate(clausulas, start=1)]
    entradas_faq = [
        RetrievedFaq(
            faq_id=i,
            question=p,
            answer=r,
            code=c.code,
            version=c.version,
            section=c.section,
            distance=0.25,
            reviewed=False,
            source_content=c.content,
        )
        for i, (p, r, c) in enumerate(faqs or [], start=1)
    ]
    retrieval = RetrievalResult(
        accepted=chunks, rejected=[], max_distance=0.527, faqs=entradas_faq
    )
    return _build_messages(_SYSTEM_PROMPT, retrieval, [], pregunta)


def mensajes_sin_contexto(pregunta: str) -> list[dict[str, Any]]:
    """El prompt cuando la busqueda no devolvio nada: la rama que debe escalar."""
    vacio = RetrievalResult(accepted=[], rejected=[], max_distance=0.527, faqs=[])
    return _build_messages(_SYSTEM_PROMPT, vacio, [], pregunta)


# --- Familia 1: respuesta fundamentada con cita ------------------------------
#
# OJO CON LA FUENTE: se lee `faq_<tenant>.json`, no `faq_<tenant>.jsonl`. El
# .jsonl es el CHECKPOINT crudo de la generacion -- una linea por clausula
# procesada, anterior a la validacion -- y conserva 24 respuestas que citan
# documentos inexistentes. Entrenar con el le ensenaria al modelo justo el fallo
# que este proyecto existe para evitar. El .json es lo que valido
# `faq_validation.py` y lo que `load_faq.py` carga en la base.
#
# Ninguna de las 730 respuestas del FAQ cita su codigo: el FAQ guarda el dato, y
# la ficha de referencia la arma el servidor desde la base. Pero el guardrail de
# fundamentacion exige que el CODIGO aparezca en el TEXTO, asi que el objetivo de
# entrenamiento tiene que llevarlo. Esto es, literalmente, el formato de cita que
# queremos ensenar.

# El prompt de sistema del tenant es explicito, y va en contra de lo que parece
# natural: "ATRIBUYE SIEMPRE, pero por TITULO y seccion, no por codigo", y pone
# como ejemplo de lo que NO hay que hacer un "Segun STI-PR-01 v4, seccion 7.6...".
#
# El guardrail acompana: `response_cites_source` acepta el codigo O dos palabras
# distintivas del titulo, y su docstring razona por que el titulo es preferible --
# "STI-PR-01 v4" no le dice nada a quien pregunta, y la ficha con codigo, version
# y fecha la arma el servidor desde la base, no el modelo.
#
# Asi que el objetivo de entrenamiento atribuye por TITULO y seccion, y no
# escribe codigos. Ademas de respetar el prompt, elimina de raiz la posibilidad
# de que un objetivo cite un documento inexistente: si no hay codigos, no hay
# fantasmas.

_PLANTILLAS_ATRIBUCION = (
    "{respuesta} Lo define {fuente}.",
    "{respuesta} Esta en {fuente}.",
    "{respuesta} Lo indica {fuente}.",
    "{respuesta} Lo establece {fuente}.",
)

# Segmento central del codigo -> como se nombra ese documento en prosa.
_TIPO_EN_PROSA = {
    "PR": "el procedimiento",
    "PO": "la politica",
    "MN": "el manual",
    "IN": "el instructivo",
    "FO": "el formato",
    "PL": "el plan",
    "OR": "el organigrama",
    "OB": "el documento",
}


def _codigo_base(codigo: str) -> str:
    """
    "CAL-FO-13 (Gestion de la Calidad)" -> "CAL-FO-13".

    Es el codigo que `extract_cited_codes` reconoce y contra el que el guardrail
    compara.
    """
    return codigo.split(" (")[0].strip()


def _titulo_legible(titulo: str) -> str:
    """
    Los titulos del SGC vienen en mayusculas o en capitalizacion mixta segun
    quien escribio el documento. En medio de una frase, "POLITICA DE LA CALIDAD"
    se lee como un grito; se normaliza sin tocar los que ya venian bien.
    """
    limpio = (titulo or "").strip()
    if limpio and limpio == limpio.upper():
        return limpio.capitalize()
    return limpio


def _tipo_y_titulo(clausula: Clausula) -> str:
    partes = _codigo_base(clausula.code).split("-")
    tipo = _TIPO_EN_PROSA.get(partes[1].upper() if len(partes) > 1 else "", "el documento")
    return f"{tipo} {_titulo_legible(clausula.title)}"


def _fuente_en_prosa(clausula: Clausula) -> str:
    fuente = _tipo_y_titulo(clausula)
    if clausula.section:
        fuente += f", seccion {clausula.section}"
    return fuente


def _con_atribucion(respuesta: str, clausula: Clausula, variante: int) -> str:
    """
    Atribuye por titulo, y cae al codigo SOLO cuando el titulo no basta.

    `response_cites_source` exige dos palabras distintivas del titulo, y descarta
    las que salen en cualquier documento de un SGC ("gestion", "calidad",
    "procedimiento", "documento"...). Un documento titulado "Gestion de la
    Calidad" no tiene NINGUNA palabra distintiva: atribuirlo solo por titulo
    nunca pasaria la comprobacion, y son 144 de las 730 entradas.

    El prompt del tenant desaconseja escribir el codigo, pero no lo prohibe, y el
    guardrail lo acepta. Asi que el codigo entra como respaldo entre parentesis
    unicamente en los casos donde el titulo no puede sostener la atribucion solo.
    """
    plantilla = _PLANTILLAS_ATRIBUCION[variante % len(_PLANTILLAS_ATRIBUCION)]
    base = respuesta.rstrip(". ").strip() + "."
    chunk = _a_chunk(clausula)

    por_titulo = plantilla.format(respuesta=base, fuente=_fuente_en_prosa(clausula))
    if response_cites_source(por_titulo, [chunk]):
        return por_titulo

    codigo = f"{_codigo_base(clausula.code)} {clausula.version}"
    fuente = f"{_tipo_y_titulo(clausula)} ({codigo})"
    if clausula.section:
        fuente += f", seccion {clausula.section}"
    return plantilla.format(respuesta=base, fuente=fuente)


def familia_cita(
    faq_path: Path,
    indice: dict[tuple[str, str], Clausula],
    excluidas: set[str],
    codigos_reales: set[str],
) -> tuple[list[Ejemplo], list[str]]:
    ejemplos: list[Ejemplo] = []
    descartes: list[str] = []

    entradas = json.loads(faq_path.read_text(encoding="utf-8"))["entradas"]

    # La variante de plantilla se reparte por clausula, no por posicion global:
    # asi las dos preguntas de una misma clausula no salen con la misma formula
    # y el modelo no aprende una sola manera de citar.
    vistas: Counter[str] = Counter()

    for entrada in entradas:
        codigo = entrada["codigo"]
        seccion = entrada["seccion"]
        clave = f"{codigo}§{seccion or '-'}"
        clausula = indice.get((codigo, seccion or "-"))
        if clausula is None:
            descartes.append(f"{codigo} §{seccion}: clausula no hallada")
            continue

        pregunta = entrada["pregunta"].strip()
        if normalizar(pregunta) in excluidas:
            descartes.append(f"{pregunta[:70]}: esta en el banco de pruebas")
            continue

        respuesta = _con_atribucion(entrada["respuesta"].strip(), clausula, vistas[clave])
        vistas[clave] += 1

        # Se valida con el GUARDRAIL DE PRODUCCION, no con una regla propia. Si
        # `response_cites_source` no reconoce la atribucion, la respuesta
        # quedaria marcada `grounded=False` en produccion: entrenar con ella
        # ensenaria a redactar respuestas que el propio sistema no acepta.
        if not response_cites_source(respuesta, [_a_chunk(clausula)]):
            descartes.append(f"{pregunta[:70]}: la atribucion no la reconoce el guardrail")
            continue

        # Red que de verdad importa: ningun objetivo de entrenamiento puede citar
        # un documento que no existe. Atribuyendo por titulo no deberia aparecer
        # ningun codigo, pero la respuesta del FAQ puede traer uno heredado del
        # texto de la clausula.
        citados = extract_cited_codes(respuesta)
        fantasmas = citados - codigos_reales - extract_cited_codes(clausula.content)
        if fantasmas:
            descartes.append(
                f"{pregunta[:70]}: cita codigo inexistente ({', '.join(sorted(fantasmas))})"
            )
            continue

        ejemplos.append(
            Ejemplo(
                familia="cita",
                grupo=clave,
                messages=[
                    *mensajes_con_contexto(pregunta, [clausula]),
                    {"role": "assistant", "content": respuesta},
                ],
                tools=TOOLS_SCHEMA,
                origen=f"FAQ {codigo} §{seccion or '-'}",
            )
        )

    return ejemplos, descartes


# --- Familia 2: fantasmas, del fichero de rechazos ---------------------------
#
# Los 14 descartes del FAQ citan un codigo que no existe en el SGC, casi siempre
# copiado de una errata del propio documento fuente. Son los unicos ejemplos
# NEGATIVOS etiquetados que tiene el proyecto, y entrenan justo el fallo mas
# grave: citar un documento inexistente.
#
# El objetivo NO es la respuesta descartada. Es la conducta correcta ante esa
# misma pregunta: responder sin el codigo fantasma, o negarse.

# Una entrada puede listar VARIOS codigos inexistentes separados por coma
# ("CSST-FO-20, CSST-FO-21"). Capturar solo el primero dejaba fuera 6 de las 14,
# y ademas habria colado en el objetivo de entrenamiento los codigos restantes,
# que es exactamente lo que esta familia debe ensenar a no hacer.
_RECHAZO = re.compile(
    r"^- \*\*(?P<codigo>[^ ]+) §(?P<seccion>[^*]+)\*\* — (?P<fantasmas>[A-Z0-9, -]+?)\s*\n"
    r"\s*- P: (?P<pregunta>.+?)\n"
    r"\s*- R: (?P<respuesta>.+?)$",
    re.MULTILINE,
)


def familia_fantasma(
    rechazos_path: Path, indice: dict[tuple[str, str], Clausula], excluidas: set[str]
) -> tuple[list[Ejemplo], list[str]]:
    if not rechazos_path.exists():
        return [], [f"{rechazos_path.name}: no existe, familia omitida"]

    ejemplos: list[Ejemplo] = []
    descartes: list[str] = []
    texto = rechazos_path.read_text(encoding="utf-8")

    for m in _RECHAZO.finditer(texto):
        pregunta = m.group("pregunta").strip()
        if normalizar(pregunta) in excluidas:
            descartes.append(f"{pregunta[:70]}: esta en el banco de pruebas")
            continue

        clausula = indice.get((m.group("codigo"), m.group("seccion").strip()))
        if clausula is None:
            descartes.append(f"{m.group('codigo')}: clausula no hallada")
            continue

        fantasmas = {c.strip().upper() for c in m.group("fantasmas").split(",") if c.strip()}
        # Se retiran los codigos inexistentes de la respuesta y se cita el
        # documento que SI la respalda. Esa es la conducta que queremos.
        limpia = m.group("respuesta")
        for fantasma in fantasmas:
            limpia = re.sub(rf"\s*{re.escape(fantasma)}\b", "", limpia)
        limpia = re.sub(r"\s+([,.])", r"\1", limpia).strip()
        respuesta = _con_atribucion(limpia, clausula, 0)

        if fantasmas & extract_cited_codes(respuesta):
            descartes.append(f"{pregunta[:70]}: no se pudo retirar el codigo fantasma")
            continue

        ejemplos.append(
            Ejemplo(
                familia="fantasma",
                grupo=f"{clausula.code}§{clausula.section or '-'}",
                messages=[
                    *mensajes_con_contexto(pregunta, [clausula]),
                    {"role": "assistant", "content": respuesta},
                ],
                tools=TOOLS_SCHEMA,
                origen=f"rechazo por {', '.join(sorted(fantasmas))} inexistente(s)",
            )
        )

    return ejemplos, descartes


# --- Familia 3: sin respaldo -> escalar --------------------------------------
#
# La rama que sostiene la tesis del proyecto. Cuando la busqueda no devuelve
# nada, la unica conducta aceptable es decirlo y llamar a escalate_to_quality.

_FUERA_DE_ALCANCE = [
    "cuantos dias de vacaciones me tocan este ano",
    "como pido un adelanto de sueldo",
    "cual es la clave del wifi de la oficina",
    "que dice el convenio colectivo sobre horas extra",
    "cual es el procedimiento para calibrar un cromatografo de gases",
    "como se tramita una baja por enfermedad",
    "donde veo mi nomina del mes pasado",
    "que menu hay hoy en el comedor",
    "cual es la politica de teletrabajo",
    "como reservo una sala de reuniones",
    "que seguro medico tiene la empresa",
    "cuando es la proxima subida salarial",
    "cual es el horario del comedor",
    "como solicito dias de permiso sin sueldo",
    "que descuentos tengo en el gimnasio",
    "cual es la capital de Australia",
    "cuanto cuesta un montacargas nuevo",
    "quien gano el partido de ayer",
    "como configuro el correo en mi movil",
    "que temperatura hace hoy en el almacen",
]

_APROBACION = [
    "apruebame este cambio al procedimiento de compras",
    "autoriza la nueva version del manual de calidad",
    "cambia el plazo de la seccion 6.2 a 48 horas",
    "dame de baja el documento STI-PO-02",
    "firma la revision del procedimiento de auditorias",
    "modifica el responsable del proceso de compras",
    "valida esta no conformidad y cierrala",
    "sube la version del instructivo de codificacion a v3",
    "borra el hallazgo que registre ayer",
    "aprueba el plan de auditoria de este trimestre",
    "cambia el estado de CAL-PR-03 a obsoleto",
]


def _llamada(nombre: str, argumentos: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": f"call_{nombre}",
                "type": "function",
                # Los argumentos van como OBJETO, no como string JSON. La
                # plantilla de chat de Qwen itera `arguments.items()`, y con un
                # string revienta con "Can only get item pairs from a mapping".
                # Es la misma correccion que ya hace `llm_client.py` al recibir.
                "function": {"name": nombre, "arguments": argumentos},
            }
        ],
    }


def familia_escalacion(excluidas: set[str]) -> tuple[list[Ejemplo], list[str]]:
    ejemplos: list[Ejemplo] = []
    descartes: list[str] = []

    for pregunta in _FUERA_DE_ALCANCE:
        if normalizar(pregunta) in excluidas:
            descartes.append(f"{pregunta}: esta en el banco de pruebas")
            continue
        ejemplos.append(
            Ejemplo(
                familia="escalacion",
                grupo=f"escalacion/sin_contexto/{normalizar(pregunta)[:40]}",
                messages=[
                    *mensajes_sin_contexto(pregunta),
                    _llamada(
                        "escalate_to_quality",
                        {"reason": "No hay respaldo documental en el SGC"},
                    ),
                ],
                tools=TOOLS_SCHEMA,
                origen="tema ausente del SGC",
            )
        )

    for pregunta in _APROBACION:
        if normalizar(pregunta) in excluidas:
            descartes.append(f"{pregunta}: esta en el banco de pruebas")
            continue
        ejemplos.append(
            Ejemplo(
                familia="escalacion",
                grupo=f"escalacion/fuera_de_alcance/{normalizar(pregunta)[:40]}",
                messages=[
                    *mensajes_sin_contexto(pregunta),
                    _llamada(
                        "escalate_to_quality",
                        {"reason": "Pide aprobar, autorizar o modificar un documento controlado"},
                    ),
                ],
                tools=TOOLS_SCHEMA,
                origen="pide aprobar / autorizar / modificar",
            )
        )

    return ejemplos, descartes


# --- Familia 4: herramientas -------------------------------------------------
#
# Existe por un fallo concreto y ya medido: con contexto largo el modelo escribe
# la llamada DENTRO del texto en vez de emitirla. El orquestador lo parchea con
# `recover_tool_call_from_text`, y cada recuperacion deja un warning
# `llm.tool_call_recovered_from_text` en el log. Contar esos warnings antes y
# despues del fine-tuning es la metrica mas limpia de esta fase.

_SOCIAL = [
    ("hola", "Hola. Soy NormIA. Respondo sobre los documentos ISO vigentes de la organizacion."),
    ("buenos dias", "Buenos dias. Dime que necesitas consultar del sistema de gestion."),
    ("gracias", "A ti. Si necesitas otra consulta del SGC, aqui estoy."),
    ("adios", "Hasta luego."),
]

_CAPACIDADES = [
    "que puedes hacer",
    "en que me puedes ayudar",
    "quien eres",
    "como funcionas",
    "para que sirves",
]

_HALLAZGOS = [
    "quiero reportar que encontre extintores vencidos en el almacen",
    "hay una no conformidad: el area de recepcion no registra las temperaturas",
    "reporto un hallazgo, los operarios de carga no llevan calzado de seguridad",
    "detecte que las balanzas del muelle no tienen certificado de calibracion vigente",
    "quiero registrar una desviacion: se despacho sin guia de remision",
    "encontre que el registro de limpieza del almacen lleva tres semanas sin firmar",
    "hay un incumplimiento, el proveedor entrego sin certificado de calidad",
    "reporto que la puerta de emergencia del almacen esta bloqueada con pallets",
    "detecte lotes sin identificar en la zona de cuarentena",
    "quiero dejar constancia de que el montacargas opera sin revision mensual",
]


def familia_herramientas(
    indice: dict[tuple[str, str], Clausula], excluidas: set[str], rng: random.Random
) -> tuple[list[Ejemplo], list[str]]:
    ejemplos: list[Ejemplo] = []
    descartes: list[str] = []

    def agregar(
        familia: str, grupo: str, pregunta: str, turno: dict[str, Any], origen: str
    ) -> None:
        if normalizar(pregunta) in excluidas:
            descartes.append(f"{pregunta[:70]}: esta en el banco de pruebas")
            return
        ejemplos.append(
            Ejemplo(
                familia=familia,
                grupo=grupo,
                messages=[*mensajes_sin_contexto(pregunta), turno],
                tools=TOOLS_SCHEMA,
                origen=origen,
            )
        )

    # describir_capacidades: preguntar por el asistente NO es una consulta al SGC.
    # Se escalaba a Calidad hasta que se corrigio; el fine-tuning lo fija.
    for pregunta in _CAPACIDADES:
        agregar(
            "herramienta",
            f"herramienta/describir_capacidades/{normalizar(pregunta)[:40]}",
            pregunta,
            _llamada("describir_capacidades", {}),
            "pregunta por el propio asistente",
        )

    # Saludo: tampoco se escala, y tampoco se llama a ninguna herramienta.
    for saludo, respuesta in _SOCIAL:
        agregar(
            "social",
            f"social/saludo/{normalizar(saludo)[:40]}",
            saludo,
            {"role": "assistant", "content": respuesta},
            "saludo o cortesia",
        )

    for texto in _HALLAZGOS:
        agregar(
            "herramienta",
            f"herramienta/register_finding/{normalizar(texto)[:40]}",
            texto,
            _llamada("register_finding", {"description": texto}),
            "reporta una desviacion",
        )

    for fid in (1, 3, 7, 12, 18, 25, 34, 41):
        pregunta = rng.choice(
            [
                f"como va el hallazgo {fid}",
                f"cual es el estado de las acciones correctivas del hallazgo {fid}",
                f"dime que paso con el hallazgo numero {fid}",
            ]
        )
        agregar(
            "herramienta",
            f"herramienta/get_capa_status/{fid}",
            pregunta,
            _llamada("get_capa_status", {"finding_id": fid}),
            "consulta CAPA por id",
        )

    # buscar_documentos y leer_documento se templan con el catalogo REAL del
    # tenant: un ejemplo que nombra un documento inexistente ensenaria al modelo
    # a inventar codigos, que es exactamente el fallo que perseguimos.
    docs = sorted({(c.code, c.title) for c in indice.values()})
    rng.shuffle(docs)

    for code, title in docs[:60]:
        agregar(
            "herramienta",
            f"herramienta/leer_documento/{_codigo_base(code)}",
            rng.choice(
                [
                    f"resumeme el documento {_codigo_base(code)}",
                    f"de que trata {_codigo_base(code)}",
                    f"que dice el documento {title.lower()}",
                ]
            ),
            _llamada("leer_documento", {"referencia": _codigo_base(code)}),
            f"catalogo real: {code}",
        )

    temas = sorted({c.code.split("-")[0] for c in indice.values()})
    tipos = ["politicas", "procedimientos", "manuales", "instructivos", "formatos"]
    for tema in temas:
        for tipo in rng.sample(tipos, k=4):
            agregar(
                "herramienta",
                f"herramienta/buscar_documentos/{tema}-{tipo}",
                rng.choice(
                    [
                        f"tienen {tipo} de {tema}",
                        f"que {tipo} hay sobre {tema}",
                        f"me puedes decir si tienes {tipo} de {tema}",
                    ]
                ),
                _llamada("buscar_documentos", {"tema": f"{tipo} de {tema}"}),
                f"catalogo real: area {tema}",
            )

    return ejemplos, descartes


# --- Banco de pruebas: lo que NO puede entrar --------------------------------


def normalizar(texto: str) -> str:
    """Minusculas, sin acentos y sin puntuacion, para comparar preguntas."""
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"[^a-z0-9 ]", "", sin_tildes.lower()).strip()


def preguntas_del_banco() -> set[str]:
    """
    Lee las preguntas de `benchmark_models.py` sin importarlo.

    Se parsea el AST en vez de importar el modulo porque importarlo arrastra la
    conexion a la base y la carga del modelo de embeddings, y este script tiene
    que poder correr sin Postgres levantado.
    """
    if not BENCHMARK.exists():
        logger.warning("banco.no_encontrado %s", BENCHMARK)
        return set()

    arbol = ast.parse(BENCHMARK.read_text(encoding="utf-8"))
    preguntas: set[str] = set()
    for nodo in ast.walk(arbol):
        if (
            isinstance(nodo, ast.Call)
            and isinstance(nodo.func, ast.Name)
            and nodo.func.id == "Caso"
            and nodo.args
            and isinstance(nodo.args[0], ast.Constant)
            and isinstance(nodo.args[0].value, str)
        ):
            preguntas.add(normalizar(nodo.args[0].value))
    return preguntas


# --- Equilibrio entre familias ----------------------------------------------


def limitar_familia(
    ejemplos: list[Ejemplo], familia: str, tope: int, rng: random.Random
) -> tuple[list[Ejemplo], int]:
    """
    Recorta una familia sobrerrepresentada, submuestreando por GRUPO.

    Sin esto el dataset sale 13 a 1 a favor de `cita`, y el modelo dedica casi
    toda su capacidad al formato de la cita -- que ya hace razonablemente bien --
    en vez de a emitir la llamada a herramienta, que es el fallo que motivo esta
    fase. Se recorta por grupo y no por ejemplo para no partir una clausula:
    dejar una de sus dos preguntas y quitar la otra sesga hacia las clausulas
    con mas preguntas generadas.
    """
    de_la_familia = [e for e in ejemplos if e.familia == familia]
    if tope <= 0 or len(de_la_familia) <= tope:
        return ejemplos, 0

    grupos = sorted({e.grupo for e in de_la_familia})
    rng.shuffle(grupos)

    conservados: set[str] = set()
    acumulado = 0
    por_grupo = Counter(e.grupo for e in de_la_familia)
    for grupo in grupos:
        if acumulado >= tope:
            break
        conservados.add(grupo)
        acumulado += por_grupo[grupo]

    resto = [e for e in ejemplos if e.familia != familia]
    recortada = [e for e in de_la_familia if e.grupo in conservados]
    return resto + recortada, len(de_la_familia) - len(recortada)


# --- Particion ---------------------------------------------------------------


@dataclass
class Particion:
    train: list[Ejemplo] = field(default_factory=list)
    valid: list[Ejemplo] = field(default_factory=list)
    test: list[Ejemplo] = field(default_factory=list)


def particionar(
    ejemplos: list[Ejemplo], valid_frac: float, test_frac: float, rng: random.Random
) -> Particion:
    """
    Reparte por GRUPO, no por ejemplo.

    Las dos preguntas de una misma clausula comparten casi todo el prompt: si una
    cae en train y la otra en valid, la validacion mide memorizacion y no
    generalizacion. El grupo mantiene juntas las que vienen de la misma fuente.

    Y el reparto se hace FAMILIA POR FAMILIA, no sobre el monton: ver la nota de
    abajo sobre `buscar_documentos`.
    """
    valid: set[str] = set()
    test: set[str] = set()

    # ESTRATIFICADO POR FAMILIA. Repartir sobre el total dejaba familias enteras
    # en una sola particion: `buscar_documentos` cayo completa en valid y train
    # se quedo sin un solo ejemplo de esa herramienta. Reparte cada familia por
    # separado, asi las tres particiones ven todas las conductas.
    familias = sorted({e.familia for e in ejemplos})
    for familia in familias:
        grupos = sorted({e.grupo for e in ejemplos if e.familia == familia})
        rng.shuffle(grupos)

        # Con pocos grupos, el tope de 1 se come la familia entera: `social`
        # tiene 4 grupos y un valid+test de 1+1 dejaria la mitad fuera de train.
        n_valid = max(1, round(len(grupos) * valid_frac)) if len(grupos) >= 3 else 0
        n_test = max(1, round(len(grupos) * test_frac)) if len(grupos) >= 3 else 0
        valid.update(grupos[:n_valid])
        test.update(grupos[n_valid : n_valid + n_test])

    p = Particion()
    for e in ejemplos:
        if e.grupo in valid:
            p.valid.append(e)
        elif e.grupo in test:
            p.test.append(e)
        else:
            p.train.append(e)
    return p


# --- Salida ------------------------------------------------------------------


def escribir_jsonl(ruta: Path, ejemplos: list[Ejemplo]) -> None:
    with ruta.open("w", encoding="utf-8") as fh:
        for e in ejemplos:
            fh.write(json.dumps(e.to_record(), ensure_ascii=False) + "\n")


def escribir_reporte(
    ruta: Path,
    tenant: str,
    particion: Particion,
    descartes: list[str],
    excluidas: set[str],
) -> None:
    """
    El informe existe por la misma razon que `faq_<tenant>_rechazos.md`: un
    dataset sin traza de que entro y que quedo fuera no es auditable, y la tesis
    del proyecto es justamente la trazabilidad.
    """
    todos = particion.train + particion.valid + particion.test
    por_familia = Counter(e.familia for e in todos)

    lineas = [
        f"# Dataset de fine-tuning — {tenant}",
        "",
        "Generado por `scripts/build_finetune_dataset.py`. No se edita a mano:",
        "se vuelve a generar.",
        "",
        "## Reparto",
        "",
        "| Particion | Ejemplos |",
        "|---|---|",
        f"| train | {len(particion.train)} |",
        f"| valid | {len(particion.valid)} |",
        f"| test | {len(particion.test)} |",
        f"| **total** | **{len(todos)}** |",
        "",
        "El reparto es por GRUPO (clausula de origen), no por ejemplo: dos preguntas",
        "de la misma clausula comparten casi todo el prompt y separarlas convertiria",
        "la validacion en una medida de memorizacion.",
        "",
        "## Familias",
        "",
        "| Familia | Ejemplos | Que ensena |",
        "|---|---|---|",
    ]
    glosario = {
        "cita": "Responder con respaldo y citar codigo, version y seccion",
        "fantasma": "No citar un documento que no existe (los 14 rechazos del FAQ)",
        "escalacion": "Negarse y llamar a escalate_to_quality cuando no hay respaldo",
        "herramienta": "Emitir la llamada como tool_call, no escribirla en el texto",
        "social": "Saludar sin escalar a Calidad",
    }
    for familia, n in por_familia.most_common():
        lineas.append(f"| `{familia}` | {n} | {glosario.get(familia, '')} |")

    lineas += [
        "",
        "## Excluido del entrenamiento",
        "",
        f"**{len(excluidas)} preguntas del banco de pruebas** (`scripts/benchmark_models.py`)",
        "quedan fuera de las tres particiones. Si entraran, el benchmark dejaria de",
        "medir generalizacion y pasaria a confirmar lo memorizado.",
        "",
        f"## Descartes durante la construccion ({len(descartes)})",
        "",
    ]
    if descartes:
        lineas += [f"- {d}" for d in descartes]
    else:
        lineas.append("Ninguno.")
    lineas.append("")

    ruta.write_text("\n".join(lineas), encoding="utf-8")


def prompt_del_tenant(tenant_id: str) -> tuple[str, str]:
    """
    Devuelve (prompt, procedencia). Replica `_system_prompt` del orquestador.

    Si la base no esta levantada se cae al DEFAULT, pero AVISANDO: un dataset
    construido con el prompt equivocado se entrena igual de bien y no sirve para
    nada, que es la peor forma de fallar.
    """
    try:
        from app.db.session import SessionLocal
        from app.models.db_models import Tenant

        with SessionLocal() as db:
            tenant = db.get(Tenant, tenant_id)
            if tenant and tenant.system_prompt:
                return tenant.system_prompt, f"tenant '{tenant_id}' (base de datos)"
    except Exception as exc:  # noqa: BLE001 - sin base seguimos, pero avisando
        logger.warning("tenant.prompt_no_leido %s", exc)

    return DEFAULT_SYSTEM_PROMPT, "DEFAULT_SYSTEM_PROMPT (no se pudo leer el tenant)"


# --- Entrada -----------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default="empresa-demo-iso")
    parser.add_argument("--out", type=Path, default=None, help="por defecto data/finetune/<tenant>")
    parser.add_argument("--valid-frac", type=float, default=0.10)
    parser.add_argument("--test-frac", type=float, default=0.10)
    parser.add_argument(
        "--cap-cita",
        type=int,
        default=400,
        help="tope de la familia `cita`, la mayoritaria. 0 = sin tope",
    )
    parser.add_argument("--seed", type=int, default=3407)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    rng = random.Random(args.seed)

    tenant_dir = RAIZ / "data" / "knowledge_base" / args.tenant
    faq_path = RAIZ / "data" / "faq" / f"faq_{args.tenant}.json"
    rechazos_path = RAIZ / "data" / "faq" / f"faq_{args.tenant}_rechazos.md"
    destino = args.out or (RAIZ / "data" / "finetune" / args.tenant)

    if not tenant_dir.exists():
        logger.error("no existe %s", tenant_dir)
        return 1
    if not faq_path.exists():
        logger.error("no existe %s -- genera el FAQ antes con scripts/generate_faq.py", faq_path)
        logger.error("OJO: hace falta el .json VALIDADO, no el .jsonl (checkpoint crudo)")
        return 1

    global _SYSTEM_PROMPT
    _SYSTEM_PROMPT, procedencia = prompt_del_tenant(args.tenant)
    logger.info("prompt de sistema: %s (%d caracteres)", procedencia, len(_SYSTEM_PROMPT))
    if _SYSTEM_PROMPT is DEFAULT_SYSTEM_PROMPT:
        logger.warning(
            "se usara el prompt POR DEFECTO: si el tenant tiene el suyo, el dataset "
            "no coincidira con lo que el modelo ve en produccion"
        )

    excluidas = preguntas_del_banco()
    logger.info("banco de pruebas: %d preguntas excluidas del entrenamiento", len(excluidas))

    logger.info("reconstruyendo clausulas desde los PDFs de %s...", args.tenant)
    indice = reconstruir_clausulas(tenant_dir)
    logger.info("clausulas reconstruidas: %d", len(indice))
    if not indice:
        logger.error("no se reconstruyo ninguna clausula; revisa el metadata.csv")
        return 1

    # Los codigos que de verdad existen en el SGC, para que ningun objetivo de
    # entrenamiento cite un documento inventado.
    codigos_reales = {_codigo_base(c.code).upper() for c in indice.values()}

    ejemplos: list[Ejemplo] = []
    descartes: list[str] = []

    for constructor in (
        lambda: familia_cita(faq_path, indice, excluidas, codigos_reales),
        lambda: familia_fantasma(rechazos_path, indice, excluidas),
        lambda: familia_escalacion(excluidas),
        lambda: familia_herramientas(indice, excluidas, rng),
    ):
        nuevos, fallidos = constructor()
        ejemplos.extend(nuevos)
        descartes.extend(fallidos)

    if not ejemplos:
        logger.error("no se construyo ningun ejemplo")
        return 1

    ejemplos, recortados = limitar_familia(ejemplos, "cita", args.cap_cita, rng)
    if recortados:
        descartes.append(
            f"familia `cita`: {recortados} ejemplos recortados por --cap-cita={args.cap_cita}, "
            "para que la familia mayoritaria no ahogue a las de herramientas y escalacion"
        )

    particion = particionar(ejemplos, args.valid_frac, args.test_frac, rng)

    destino.mkdir(parents=True, exist_ok=True)
    escribir_jsonl(destino / "train.jsonl", particion.train)
    escribir_jsonl(destino / "valid.jsonl", particion.valid)
    escribir_jsonl(destino / "test.jsonl", particion.test)
    escribir_reporte(destino / "REPORTE.md", args.tenant, particion, descartes, excluidas)

    print()
    print(f"  Destino : {destino}")
    print(f"  train   : {len(particion.train):>5}")
    print(f"  valid   : {len(particion.valid):>5}")
    print(f"  test    : {len(particion.test):>5}")
    print(f"  descartes: {len(descartes)}  (detalle en REPORTE.md)")
    print()
    for familia, n in Counter(e.familia for e in ejemplos).most_common():
        print(f"  {familia:<12} {n:>5}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
