"""
Tests del dispatcher de herramientas.

La spec original mapeaba nombre -> funcion en un dict plano, pero las tres
implementaciones tienen firmas incompatibles entre si. `execute_tool` es el
dispatcher explicito que resuelve eso e impide que el modelo inyecte
tenant_id o conversation_id.
"""

import pytest

from app.core.agents.tools import (
    TOOL_NAMES,
    TOOLS_SCHEMA,
    execute_tool,
    parse_arguments,
    recover_tool_call_from_text,
    strip_tool_name_prefix,
)


class TestParseArguments:
    def test_string_json(self):
        assert parse_arguments('{"description": "fuga en linea 3"}') == {
            "description": "fuga en linea 3"
        }

    def test_dict_pasa_directo(self):
        assert parse_arguments({"finding_id": 7}) == {"finding_id": 7}

    def test_vacio_es_dict_vacio(self):
        assert parse_arguments("") == {}
        assert parse_arguments(None) == {}

    def test_json_invalido_falla_con_mensaje_claro(self):
        with pytest.raises(ValueError, match="no son JSON valido"):
            parse_arguments("{roto")

    def test_json_que_no_es_objeto_falla(self):
        with pytest.raises(ValueError, match="no son un objeto"):
            parse_arguments("[1, 2, 3]")


class TestExecuteTool:
    def test_herramienta_desconocida_no_rompe(self):
        resultado = execute_tool(
            "borrar_base_de_datos", {}, db=None, tenant_id="t", conversation_id=1
        )
        assert resultado["error"] == "unknown_tool"

    def test_escalar_no_necesita_base_de_datos(self):
        resultado = execute_tool(
            "escalate_to_quality",
            {"reason": "pide aprobar un cambio de procedimiento"},
            db=None,
            tenant_id="t",
            conversation_id=1,
        )
        assert resultado["escalated"] is True
        assert "aprobar" in resultado["reason"]

    def test_escalar_sin_motivo_no_rompe(self):
        resultado = execute_tool(
            "escalate_to_quality", {}, db=None, tenant_id="t", conversation_id=1
        )
        assert resultado["escalated"] is True
        assert resultado["reason"] == "no especificado"

    def test_registrar_hallazgo_sin_descripcion_es_rechazado(self):
        resultado = execute_tool(
            "register_finding", {"description": "  "}, db=None, tenant_id="t", conversation_id=1
        )
        assert resultado["error"] == "invalid_arguments"

    def test_capa_con_finding_id_no_numerico_es_rechazado(self):
        resultado = execute_tool(
            "get_capa_status",
            {"finding_id": "el ultimo"},
            db=None,
            tenant_id="t",
            conversation_id=1,
        )
        assert resultado["error"] == "invalid_arguments"


def test_los_nombres_declarados():
    assert {
        "register_finding",
        "get_capa_status",
        "escalate_to_quality",
        "buscar_documentos",
        "leer_documento",
        "describir_capacidades",
        "ampliar_hallazgo",
    } == TOOL_NAMES


class TestRecuperacionDeToolCallEnTexto:
    """
    Con contexto largo (fragmentos ISO + varias herramientas), un modelo local a
    veces escribe la llamada a herramienta DENTRO del texto en vez de emitirla
    como tool_call estructurado. El turno no falla: la herramienta simplemente
    nunca corre, no se escala, y el usuario ve JSON crudo en pantalla.

    Observado con qwen3:30b-a3b sobre el SGC real durante la construccion.
    """

    def test_recupera_nombre_seguido_de_json(self):
        texto = 'escalate_to_quality\n{"reason": "pide aprobar un cambio de procedimiento"}'
        call = recover_tool_call_from_text(texto)

        assert call is not None
        assert call["function"]["name"] == "escalate_to_quality"
        assert "aprobar" in call["function"]["arguments"]

    def test_recupera_etiqueta_tool_call(self):
        texto = (
            '<tool_call>{"name": "register_finding", '
            '"arguments": {"description": "fuga"}}</tool_call>'
        )
        call = recover_tool_call_from_text(texto)
        assert call["function"]["name"] == "register_finding"

    def test_recupera_bloque_json_cercado(self):
        texto = '```json\n{"name": "escalate_to_quality", "arguments": {"reason": "ambiguo"}}\n```'
        call = recover_tool_call_from_text(texto)
        assert call["function"]["name"] == "escalate_to_quality"

    def test_una_respuesta_normal_no_se_confunde(self):
        texto = "Segun CAL-PR-04 v2, seccion 7.2, debes registrar la salida no conforme."
        assert recover_tool_call_from_text(texto) is None

    def test_nunca_ejecuta_una_herramienta_inventada(self):
        """El modelo no puede inventarse una herramienta escribiendo su nombre."""
        assert recover_tool_call_from_text('borrar_base_de_datos\n{"todo": true}') is None
        assert recover_tool_call_from_text('{"name": "rm_rf", "arguments": {}}') is None

    def test_texto_sin_json_no_rompe(self):
        assert recover_tool_call_from_text("") is None
        assert recover_tool_call_from_text("escalate_to_quality") is None


class TestPrefijoDeNombreDeHerramienta:
    """
    Tras ejecutar una herramienta, el modelo a veces antepone su nombre a la
    respuesta final: "escalate_to_quality: Solicitud derivada...". Es un
    artefacto interno, no informacion para el usuario.
    """

    def test_quita_prefijo_con_dos_puntos(self):
        assert strip_tool_name_prefix(
            "escalate_to_quality: Solicitud derivada a Calidad."
        ) == "Solicitud derivada a Calidad."

    def test_quita_prefijo_con_guion(self):
        assert strip_tool_name_prefix(
            "register_finding - Hallazgo registrado con ID 3."
        ) == "Hallazgo registrado con ID 3."

    def test_no_toca_una_respuesta_normal(self):
        texto = "Segun CAL-PR-04 v2 la respuesta es anual."
        assert strip_tool_name_prefix(texto) == texto

    def test_no_recorta_texto_que_solo_menciona_la_herramienta(self):
        texto = "Puedo usar escalate_to_quality si lo necesitas."
        assert strip_tool_name_prefix(texto) == texto

    def test_vacio_no_rompe(self):
        assert strip_tool_name_prefix("") == ""


class TestBuscarDocumentos:
    """
    "Tienes informacion sobre politicas de TI?" es una pregunta de INVENTARIO,
    no de contenido. El RAG busca fragmentos parecidos y devuelve cosas como la
    seccion "ABREVIATURAS Y DEFINICIONES TI" -- semanticamente cercana al tema,
    inutil como respuesta. Paso de verdad: el bot contesto "existen politicas en
    STI-PO-01 (Seccion 5)" citando el glosario, sin nombrar ninguna politica.
    """

    def test_tema_vacio_es_rechazado(self):
        resultado = execute_tool(
            "buscar_documentos", {"tema": "   "}, db=None, tenant_id="t", conversation_id=1
        )
        assert resultado["error"] == "invalid_arguments"

    def test_esta_declarada_en_el_esquema(self):
        assert "buscar_documentos" in TOOL_NAMES

    def test_su_descripcion_orienta_al_caso_de_inventario(self):
        esquema = next(
            t for t in TOOLS_SCHEMA if t["function"]["name"] == "buscar_documentos"
        )
        descripcion = esquema["function"]["description"].lower()
        assert "catalogo" in descripcion
        assert "tema" in esquema["function"]["parameters"]["properties"]


# --- Preguntar por el asistente no es una consulta al SGC ---
#
# Bug real: a "que puedes hacer?" el bot respondia "no tengo informacion
# suficiente en los documentos vigentes" y derivaba la consulta a Calidad. El
# guardrail de fundamentacion esta para impedir que se invente CONTENIDO
# DOCUMENTAL, y esa pregunta no afirma nada de ningun documento. Peor: ensuciaba
# la cola de Calidad, que es la lista de huecos del SGC.


def test_describir_capacidades_esta_declarada_al_modelo():
    from app.core.agents.tools import TOOLS_SCHEMA

    nombres = {t["function"]["name"] for t in TOOLS_SCHEMA}
    assert "describir_capacidades" in nombres


def test_describir_capacidades_no_escala():
    from app.core.agents.tools import ESCALATING_TOOLS

    # Si estuviera aqui, correrla marcaria la conversacion como escalada.
    assert "describir_capacidades" not in ESCALATING_TOOLS


def test_describir_capacidades_cuenta_como_dato_verificable():
    from app.core.orchestrator import _tool_yielded_data

    # Lo que desactiva el guardrail bloqueante: si esto fuera False, la respuesta
    # se descartaria y volveriamos al fallo original.
    resultado = {
        "asistente": "NormIA",
        "documentos_vigentes": 6,
        "areas": ["POLITICAS"],
        "puedo": ["Responder que dice un procedimiento vigente."],
        "no_puedo": ["Aprobar documentos controlados."],
    }
    assert _tool_yielded_data(resultado) is True


def test_describir_capacidades_sin_documentos_sigue_siendo_dato():
    from app.core.orchestrator import _tool_yielded_data

    # Un tenant recien creado no tiene documentos, pero la respuesta sobre que
    # puede hacer el asistente sigue siendo valida y no debe escalar.
    vacio = {"asistente": "NormIA", "documentos_vigentes": 0, "areas": [], "puedo": ["..."]}
    assert _tool_yielded_data(vacio) is True
