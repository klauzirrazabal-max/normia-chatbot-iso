"""
Tests del guardrail de grounding y del umbral de distancia.

Esta es la tesis del proyecto: el bot no inventa. Si estos tests pasan a rojo,
NormIA deja de ser auditable y se convierte en un chatbot cualquiera.
"""


from app.core.guardrails.grounding_check import (
    NO_CONTEXT_MESSAGE,
    has_sufficient_context,
    response_cites_source,
)
from app.core.rag.retriever import RetrievalResult, RetrievedChunk, build_context_block


def chunk(code="PROC-CAL-04", version="v3", section="5.2", distance=0.20, content="texto"):
    return RetrievedChunk(
        chunk_id=1,
        content=content,
        code=code,
        version=version,
        section=section,
        distance=distance,
    )


class TestSuficienciaDeContexto:
    def test_sin_chunks_no_hay_contexto(self):
        assert has_sufficient_context([]) is False

    def test_un_chunk_basta(self):
        assert has_sufficient_context([chunk()]) is True


class TestCitaDeFuente:
    def test_detecta_el_codigo_citado(self):
        texto = "Segun PROC-CAL-04 v3, seccion 5.2, la calibracion es anual."
        assert response_cites_source(texto, [chunk()]) is True

    def test_sin_codigo_no_esta_fundamentada(self):
        texto = "La calibracion de instrumentos se hace una vez al ano."
        assert response_cites_source(texto, [chunk()]) is False

    def test_tolera_variantes_de_formato_del_codigo(self):
        """El modelo puede escribir el codigo con espacios o guiones bajos."""
        for variante in ["PROC CAL 04", "proc-cal-04", "PROC_CAL_04", "Proc-Cal-04"]:
            texto = f"De acuerdo con {variante} version 3, el plazo es anual."
            assert response_cites_source(texto, [chunk()]) is True, variante

    def test_sin_chunks_nunca_esta_fundamentada(self):
        assert response_cites_source("PROC-CAL-04 dice que si", []) is False


class TestUmbralDeDistancia:
    """
    El bug central de la spec original: sin umbral, `ORDER BY distancia LIMIT k`
    siempre devuelve k chunks mientras exista un solo documento en la base. El
    orquestador nunca ve una lista vacia, nunca escala, y el bot responde con
    contexto irrelevante.
    """

    def test_descarta_lo_que_supera_el_umbral(self):
        cercano = chunk(distance=0.21)
        lejano = chunk(code="MC-01", distance=0.92)

        aceptados = [c for c in (cercano, lejano) if c.distance <= 0.45]
        assert aceptados == [cercano]

    def test_resultado_vacio_es_falsy(self):
        vacio = RetrievalResult(accepted=[], rejected=[chunk(distance=0.95)], max_distance=0.45)
        assert not vacio
        assert has_sufficient_context(vacio.accepted) is False

    def test_resultado_con_aceptados_es_truthy(self):
        lleno = RetrievalResult(accepted=[chunk()], rejected=[], max_distance=0.45)
        assert lleno

    def test_el_debug_registra_aceptados_y_rechazados(self):
        """La traza de auditoria debe conservar tambien lo descartado."""
        resultado = RetrievalResult(
            accepted=[chunk(distance=0.20)],
            rejected=[chunk(code="MC-01", distance=0.88)],
            max_distance=0.45,
        )
        debug = resultado.to_debug()

        assert len(debug["accepted"]) == 1
        assert len(debug["rejected"]) == 1
        assert debug["rejected"][0]["distance"] == 0.88
        assert debug["max_distance"] == 0.45


class TestBloqueDeContexto:
    def test_cada_fragmento_va_rotulado_con_su_cita(self):
        bloque = build_context_block([chunk(content="La calibracion es anual.")])

        assert "PROC-CAL-04 v3" in bloque
        assert "Seccion: 5.2" in bloque
        assert "La calibracion es anual." in bloque

    def test_separa_multiples_fragmentos(self):
        bloque = build_context_block(
            [chunk(code="PROC-CAL-04"), chunk(code="MC-01", section=None)]
        )
        assert "[Fragmento 1]" in bloque
        assert "[Fragmento 2]" in bloque
        assert "MC-01" in bloque

    def test_sin_seccion_no_inventa_la_etiqueta(self):
        bloque = build_context_block([chunk(section=None)])
        assert "Seccion:" not in bloque


class TestEtiquetaDeCita:
    def test_con_seccion(self):
        assert chunk().citation_label == "PROC-CAL-04 v3, seccion 5.2"

    def test_sin_seccion(self):
        assert chunk(section=None).citation_label == "PROC-CAL-04 v3"


def test_mensaje_sin_contexto_ofrece_escalar():
    assert "Calidad" in NO_CONTEXT_MESSAGE
    assert "no tengo informacion suficiente" in NO_CONTEXT_MESSAGE.lower()


# --- Correr una herramienta no es lo mismo que encontrar algo ---
#
# Bug real: preguntando por vacaciones (tema ausente del SGC) el modelo llamaba a
# buscar_documentos, que devolvia `documentos: []`. Eso contaba como "corrio una
# herramienta de datos" y desactivaba el guardrail bloqueante, asi que el bot
# escribia "lo he derivado al Responsable de Calidad" y la escalacion nunca se
# registraba. La promesa al usuario quedaba vacia.


def test_busqueda_sin_resultados_no_cuenta_como_dato():
    from app.core.orchestrator import _tool_yielded_data

    vacio = {
        "tema": "vacaciones",
        "documentos": [],
        "message": "No hay documentos vigentes que coincidan con 'vacaciones'.",
    }
    assert _tool_yielded_data(vacio) is False


def test_busqueda_con_resultados_si_cuenta_como_dato():
    from app.core.orchestrator import _tool_yielded_data

    con_datos = {
        "tema": "respaldo",
        "total": 1,
        "documentos": [{"codigo": "STI-PO-01", "version": "v2"}],
    }
    assert _tool_yielded_data(con_datos) is True


def test_herramienta_con_error_no_cuenta_como_dato():
    from app.core.orchestrator import _tool_yielded_data

    fallo = {"documento": "XXX-YY-99", "error": "not_found", "message": "No encontre..."}
    assert _tool_yielded_data(fallo) is False


def test_dato_verificable_sin_listas_cuenta():
    from app.core.orchestrator import _tool_yielded_data

    # get_capa_status devuelve un estado, no una lista: sigue siendo verificable.
    assert _tool_yielded_data({"capa_id": "CAPA-7", "estado": "abierta"}) is True


# --- El aviso de "no verificado" solo cuando hay algo que verificar ---
#
# Salia en cada turno: al describir sus capacidades, al pedir datos para
# registrar un hallazgo, al confirmar que lo registro. Un aviso que aparece
# siempre deja de leerse, y entonces no avisa cuando importa.


def test_una_respuesta_que_no_nombra_documentos_no_afirma_nada():
    # Misma prueba que decide si se adjuntan citas: las dos deben coincidir.
    fragmentos = [chunk(code="CAL-PR-03", section="6", content="Plazo de registro")]
    assert response_cites_source("Cuentame que paso y con eso la registro.", fragmentos) is False


def test_una_respuesta_que_cita_por_titulo_si_afirma():
    fragmentos = [chunk(code="CAL-PR-03", section="6", content="texto")]
    fragmentos = [
        RetrievedChunk(
            chunk_id=1, content="Plazo de registro", code="CAL-PR-03", version="v2",
            section="6", distance=0.2, title="No Conformidad y Acciones Correctivas",
        )
    ]
    respuesta = "El plazo es de 3 dias, segun No Conformidad y Acciones Correctivas."
    assert response_cites_source(respuesta, fragmentos) is True
