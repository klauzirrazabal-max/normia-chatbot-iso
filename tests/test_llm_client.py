"""
Tests del cliente de LLM.

El caso critico: Qwen3 es un modelo de razonamiento y emite su cadena de
pensamiento dentro de <think>...</think>. Si eso llega al usuario, la demo se
ve rota; si llega al guardrail de citas, el chequeo se corrompe.
"""

from app.services.llm_client import (
    OpenAICompatibleClient,
    _to_native_messages,
    strip_reasoning,
)


class TestStripReasoning:
    def test_quita_el_bloque_de_pensamiento(self):
        crudo = (
            "<think>El usuario pregunta por calibracion. Busco en el fragmento 1.</think>\n"
            "Segun PROC-CAL-04 v3, seccion 5.2, la calibracion es anual."
        )
        assert strip_reasoning(crudo) == (
            "Segun PROC-CAL-04 v3, seccion 5.2, la calibracion es anual."
        )

    def test_bloque_multilinea(self):
        crudo = "<think>\nlinea uno\nlinea dos\n</think>\nRespuesta final."
        assert strip_reasoning(crudo) == "Respuesta final."

    def test_varios_bloques(self):
        crudo = "<think>a</think>Primera parte. <think>b</think>Segunda parte."
        assert strip_reasoning(crudo) == "Primera parte. Segunda parte."

    def test_etiqueta_huerfana_sin_cierre(self):
        """Si el modelo se corta a media cadena, no debe filtrarse la etiqueta."""
        assert "think" not in strip_reasoning("<think>razonando sin cerrar").lower()

    def test_texto_normal_no_se_toca(self):
        texto = "Segun MC-01 v2, el alcance cubre toda la planta."
        assert strip_reasoning(texto) == texto

    def test_respeta_mayusculas_en_la_etiqueta(self):
        assert strip_reasoning("<THINK>x</THINK>Hola") == "Hola"


class TestAdaptadorNativoDeOllama:
    """
    El modo rapido (razonamiento apagado) usa la API nativa de Ollama, que habla
    un dialecto distinto al de OpenAI. Las dos diferencias se encontraron
    depurando: el benchmark fallo en la pregunta 7 y TODAS las siguientes, con un
    error que no apuntaba a la causa -- "Value looks like object, but can't find
    closing '}' symbol" -- porque Ollama intentaba parsear los argumentos de la
    herramienta, que iban como string JSON en vez de objeto.
    """

    def test_convierte_argumentos_de_string_a_objeto(self):
        msgs = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "t0",
                        "type": "function",
                        "function": {"name": "buscar_documentos", "arguments": '{"tema":"TI"}'},
                    }
                ],
            }
        ]
        adaptado = _to_native_messages(msgs)
        argumentos = adaptado[0]["tool_calls"][0]["function"]["arguments"]

        assert argumentos == {"tema": "TI"}, "debe ser objeto, no string"
        assert not isinstance(argumentos, str)

    def test_argumentos_ya_objeto_se_conservan(self):
        msgs = [
            {
                "role": "assistant",
                "tool_calls": [{"function": {"name": "f", "arguments": {"a": 1}}}],
            }
        ]
        assert _to_native_messages(msgs)[0]["tool_calls"][0]["function"]["arguments"] == {"a": 1}

    def test_argumentos_corruptos_no_rompen(self):
        msgs = [
            {
                "role": "assistant",
                "tool_calls": [{"function": {"name": "f", "arguments": "{roto"}}],
            }
        ]
        assert _to_native_messages(msgs)[0]["tool_calls"][0]["function"]["arguments"] == {}

    def test_resultado_de_herramienta_usa_tool_name(self):
        msgs = [{"role": "tool", "tool_call_id": "t0", "name": "f", "content": "{}"}]
        adaptado = _to_native_messages(msgs)[0]

        assert adaptado["tool_name"] == "f"
        assert "tool_call_id" not in adaptado
        assert "name" not in adaptado

    def test_no_toca_los_mensajes_normales(self):
        msgs = [
            {"role": "system", "content": "Eres NormIA."},
            {"role": "user", "content": "Hola"},
        ]
        assert _to_native_messages(msgs) == msgs

    def test_no_muta_la_lista_original(self):
        original = [{"role": "tool", "name": "f", "content": "{}"}]
        copia = [dict(m) for m in original]
        _to_native_messages(original)
        assert original == copia, "el historial del orquestador no debe alterarse"


class TestDeteccionDeOllama:
    def test_detecta_ollama_por_puerto_y_sufijo(self):
        cliente = OpenAICompatibleClient(base_url="http://localhost:11434/v1")
        assert cliente.is_ollama is True
        assert cliente._native_endpoint == "http://localhost:11434/api/chat"

    def test_groq_no_es_ollama(self):
        """El modo rapido no debe activarse contra un proveedor en la nube."""
        cliente = OpenAICompatibleClient(base_url="https://api.groq.com/openai/v1")
        assert cliente.is_ollama is False
