"""
Nada llega a la bandeja de Calidad sin que el usuario lo autorice.

La regla es una sola y no admite excepciones por camino: una escalacion nace
`propuesta` y solo pasa a `pendiente` cuando el usuario contesta que si. Se
incumplia por dos sitios a la vez:

- `escalate_to_quality` no pasaba `status`, asi que tomaba el `pendiente` por
  defecto del modelo: el bot derivaba por su cuenta. Se acumularon 306 consultas
  que nadie habia pedido derivar, y una bandeja asi se deja de mirar.
- El guardrail de cita fantasma respondia "lo derivo al Responsable de Calidad"
  con `escalate=True` -- el widget pintaba "Esta consulta quedo derivada" --
  sobre un registro que seguia en `propuesta` y que Calidad no veia nunca. Es la
  promesa incumplida que motivo la tabla `escalations`, por el otro lado.

Y la misma pregunta dejaba DOS registros: el de la herramienta y el del
guardrail, uno de ellos saltandose la confirmacion.
"""

from __future__ import annotations

from app.core.guardrails.grounding_check import (
    NO_CONTEXT_MESSAGE,
    PHANTOM_CITATION_MESSAGE,
    PREGUNTA_DERIVAR,
    con_pregunta_de_derivar,
    ofrece_derivar,
)


def test_los_dos_guardrails_preguntan_en_vez_de_afirmar():
    for mensaje in (NO_CONTEXT_MESSAGE, PHANTOM_CITATION_MESSAGE):
        assert ofrece_derivar(mensaje)
        assert mensaje.rstrip().endswith("?")


def test_el_mensaje_de_cita_fantasma_ya_no_afirma_haber_derivado():
    """
    Lo que se veia en pantalla era "Esta consulta quedo derivada al Responsable
    de Calidad" encima de una fila en `propuesta`. Afirmar la derivacion es lo
    que no puede volver.
    """
    texto = PHANTOM_CITATION_MESSAGE.lower()
    assert "derivarlo al responsable" not in texto
    assert "prefiero no arriesgarme" in texto


def test_se_anade_la_pregunta_a_una_respuesta_del_modelo():
    """
    El modelo no es de fiar para acordarse de preguntar: a veces ni se entera de
    haber llamado a la herramienta, porque escribio la llamada como texto plano.
    """
    assert con_pregunta_de_derivar("Eso lo aprueba Calidad.").endswith(PREGUNTA_DERIVAR)


def test_no_se_pregunta_dos_veces():
    assert con_pregunta_de_derivar(NO_CONTEXT_MESSAGE) == NO_CONTEXT_MESSAGE.rstrip()
    assert con_pregunta_de_derivar(NO_CONTEXT_MESSAGE).count(PREGUNTA_DERIVAR) == 1


def test_un_texto_cualquiera_no_cuenta_como_ofrecimiento():
    """
    La comprobacion es lo que autoriza a un "si" del usuario a derivar. Si
    cualquier mensaje contara, un "si" dicho a otra cosa derivaria una consulta
    que nadie ofrecio derivar -- paso en la primera prueba del consentimiento.
    """
    assert not ofrece_derivar("El procedimiento OPE-PR-04 v1 lo explica en 7.2.4.")
    assert not ofrece_derivar("")


def test_la_herramienta_deja_la_escalacion_como_propuesta():
    """
    Esta era la puerta que quedaba abierta: el guardrail ya nacia como
    propuesta, pero la herramienta que llama el modelo no.
    """
    import inspect

    from app.core.agents import tools

    fuente = inspect.getsource(tools.escalate_to_quality)
    assert 'status="propuesta"' in fuente


def test_el_guardrail_reetiqueta_en_vez_de_duplicar():
    """
    Una pregunta dejaba DOS registros -- el de la herramienta (`pendiente`) y el
    del guardrail (`propuesta`) -- y el primero se saltaba la confirmacion.

    Este test llama a `_retag_escalation` con la forma REAL de `tool_results`:
    pares (nombre, resultado). Se paso `executed_tools`, que son solo nombres, y
    los 424 tests pasaron igual mientras el chat devolvia 500 -- ninguno
    ejercitaba esta rama con datos de verdad.
    """
    from app.core.orchestrator import _retag_escalation

    class _DB:
        def get(self, *_):
            return None

        def flush(self):
            pass

    assert _retag_escalation(_DB(), [("escalate_to_quality", {"escalation_id": 7})], "error")
    assert not _retag_escalation(_DB(), [("buscar_documentos", {"hits": 3})], "error")
    assert not _retag_escalation(_DB(), [], "error")


def test_la_pregunta_del_modelo_se_sustituye_no_se_acumula():
    """
    Quedaba: "¿Confirmas que lo derive? ¿Quieres que lo derive al Responsable de
    Calidad para que lo revise?". Dos veces lo mismo, seguidas.
    """
    texto = "No puedo aprobar documentos.\n\nHe preparado una derivación. ¿Confirmas que lo derive?"
    salida = con_pregunta_de_derivar(texto)
    assert salida.count("¿") == 1
    assert salida.endswith(PREGUNTA_DERIVAR)
    assert "He preparado una derivación." in salida


def test_sustituir_la_pregunta_no_se_come_la_respuesta():
    salida = con_pregunta_de_derivar("El plazo lo fija OPE-PR-04. ¿Te lo amplío?")
    assert "El plazo lo fija OPE-PR-04." in salida
    assert salida.endswith(PREGUNTA_DERIVAR)
