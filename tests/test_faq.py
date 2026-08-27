"""
Tests de la validacion del FAQ.

Un FAQ de cumplimiento con una cifra inventada es peor que no tener FAQ: la
gente lo lee sin abrir el documento, y una respuesta que dice "el plazo es de 5
dias" cuando el procedimiento dice 3 se convierte en una no conformidad con
apariencia oficial.

La verificacion es DETERMINISTA a proposito. Pedirle a un modelo que juzgue si
otro modelo acerto es util como segunda opinion, pero no como garantia: los dos
comparten los mismos sesgos. Comparar cifras contra el texto fuente no opina,
comprueba.
"""

from app.core.docs.faq_validation import (
    Rechazo,
    clave_de_deduplicacion,
    validar_entrada,
)

CLAUSULA = (
    "7.6. El Jefe de TI informa al solicitante el estado de la solicitud. "
    "INCIDENCIA: Afecta a un solo usuario | 01 hora | 05 horas. "
    "INCIDENCIA: Afecta a toda la empresa | 30 minutos | Segun proveedor. "
    "El registro se hace en el formato STI-FO-01."
)


def validar(respuesta: str, pregunta: str = "¿Cuanto tarda la atencion?"):
    return validar_entrada(pregunta, respuesta, CLAUSULA, "STI-PR-01")


class TestCifras:
    """El chequeo que de verdad importa."""

    def test_acepta_una_cifra_que_esta_en_la_clausula(self):
        assert validar("El tiempo maximo de respuesta es de 1 hora.").valida

    def test_normaliza_los_ceros_a_la_izquierda(self):
        """La tabla dice '01 hora'; el modelo escribe '1 hora'. Es el mismo dato."""
        assert validar("La respuesta es en 1 hora y la solucion en 5 horas.").valida

    def test_rechaza_una_cifra_inventada(self):
        resultado = validar("El tiempo maximo de respuesta es de 3 horas.")
        assert not resultado.valida
        assert resultado.motivo is Rechazo.CIFRA_INVENTADA
        assert "3" in resultado.detalle

    def test_una_respuesta_sin_cifras_pasa(self):
        assert validar("El Jefe de TI informa al solicitante el estado.").valida

    def test_no_penaliza_el_numero_de_la_norma(self):
        """'ISO 9001' aparece en todas partes sin ser un dato de la clausula."""
        assert validar("Segun ISO 9001, el Jefe de TI informa el estado.").valida


class TestCodigos:
    def test_acepta_un_codigo_que_la_clausula_menciona(self):
        assert validar("El registro se hace en el formato STI-FO-01.").valida

    def test_acepta_el_codigo_del_propio_documento(self):
        assert validar("Segun STI-PR-01, el Jefe de TI informa el estado.").valida

    def test_rechaza_un_codigo_ajeno(self):
        resultado = validar("El registro se hace en el formato CAL-FO-77.")
        assert not resultado.valida
        # Un codigo inventado suele traer tambien una cifra inventada; cualquiera
        # de los dos motivos sirve, lo importante es que NO pase.
        assert resultado.motivo in (Rechazo.CODIGO_INVENTADO, Rechazo.CIFRA_INVENTADA)


class TestIncertidumbre:
    """Un FAQ afirma; no especula. Si el modelo duda, la clausula no respondia."""

    def test_rechaza_no_se_especifica(self):
        resultado = validar("No se especifica quien lo aprueba.")
        assert resultado.motivo is Rechazo.INCERTIDUMBRE

    def test_rechaza_probablemente(self):
        assert validar("Probablemente lo revisa el Jefe de TI.").motivo is Rechazo.INCERTIDUMBRE

    def test_rechaza_una_excusa_sobre_el_fragmento(self):
        resultado = validar("El fragmento no proporciona ese detalle.")
        assert resultado.motivo is Rechazo.INCERTIDUMBRE


class TestFormato:
    def test_la_pregunta_debe_terminar_en_interrogacion(self):
        resultado = validar("El Jefe de TI informa el estado.", pregunta="Cuanto tarda")
        assert resultado.motivo is Rechazo.PREGUNTA_VACIA

    def test_rechaza_una_respuesta_demasiado_corta(self):
        assert validar("Si.").motivo is Rechazo.LONGITUD

    def test_rechaza_una_respuesta_demasiado_larga(self):
        assert validar("El Jefe de TI informa. " * 40).motivo is Rechazo.LONGITUD


class TestDeduplicacion:
    """
    Clausulas distintas del mismo procedimiento generan preguntas casi iguales.
    En un FAQ eso se lee como descuido.
    """

    def test_dos_formas_de_la_misma_pregunta_coinciden(self):
        a = clave_de_deduplicacion("¿Quien registra el hallazgo?")
        b = clave_de_deduplicacion("¿Quien debe registrar el hallazgo?")
        assert a == b

    def test_ignora_acentos_y_mayusculas(self):
        a = clave_de_deduplicacion("¿Cuándo se hace la AUDITORÍA?")
        b = clave_de_deduplicacion("cuando se hace la auditoria")
        assert a == b

    def test_preguntas_distintas_no_colisionan(self):
        a = clave_de_deduplicacion("¿Quien registra el hallazgo?")
        b = clave_de_deduplicacion("¿Cuando se cierra el hallazgo?")
        assert a != b


class TestCodigoInexistenteEnElSGC:
    """
    El caso que la comparacion contra la clausula NO cubre: que el documento
    FUENTE contenga un codigo erroneo.

    Paso de verdad. CAL-PR-03 §7.4 cita "CTC-FO-09" donde las otras seis
    menciones del mismo documento dicen "CAL-FO-09", y ese codigo no existe en
    el SGC. Es una errata del documento original, y validar solo contra la
    clausula la habria copiado al FAQ con apariencia de dato verificado.
    """

    CONOCIDOS = {"CAL-PR-03", "CAL-FO-09", "STI-PR-01", "STI-FO-01"}
    CLAUSULA_CON_ERRATA = (
        "7.4. Las acciones correctivas se registran en el formato "
        "Acciones Correctivas CTC-FO-09."
    )

    def test_detecta_la_errata_heredada_del_documento(self):
        resultado = validar_entrada(
            "¿Donde se registran las acciones correctivas?",
            "Se registran en el formato Acciones Correctivas CTC-FO-09.",
            self.CLAUSULA_CON_ERRATA,
            "CAL-PR-03",
            self.CONOCIDOS,
        )
        assert not resultado.valida
        assert resultado.motivo is Rechazo.CODIGO_INEXISTENTE
        assert "CTC-FO-09" in resultado.detalle

    def test_sin_registro_de_codigos_la_errata_pasa(self):
        """Documenta el limite: el chequeo necesita el registro para funcionar."""
        resultado = validar_entrada(
            "¿Donde se registran las acciones correctivas?",
            "Se registran en el formato Acciones Correctivas CTC-FO-09.",
            self.CLAUSULA_CON_ERRATA,
            "CAL-PR-03",
        )
        assert resultado.valida

    def test_un_codigo_real_no_se_marca(self):
        resultado = validar_entrada(
            "¿Donde se registra el hallazgo?",
            "Se registra en el formato Acciones Correctivas CAL-FO-09.",
            "7.1. El hallazgo se registra en el formato Acciones Correctivas CAL-FO-09.",
            "CAL-PR-03",
            self.CONOCIDOS,
        )
        assert resultado.valida


# --- El FAQ como fuente de recuperacion -------------------------------------

from app.core.rag.retriever import RetrievalResult, RetrievedFaq, build_faq_block  # noqa: E402


def faq(
    pregunta="¿Quien me avisa como va mi solicitud?",
    respuesta="El Jefe de TI informa el estado de la solicitud.",
    reviewed=False,
    fuente="7.6. El Jefe de TI informa al solicitante el estado de la solicitud.",
):
    return RetrievedFaq(
        faq_id=1,
        question=pregunta,
        answer=respuesta,
        code="STI-PR-01",
        version="v4",
        section="7.6",
        distance=0.11,
        reviewed=reviewed,
        source_content=fuente,
    )


class TestBloqueDeFaq:
    """
    Una entrada de FAQ es una respuesta ya redactada, y el riesgo es que el
    modelo la copie aunque la pregunta sea parecida pero distinta: "una
    incidencia que me afecta solo a mi" son 1 hora, "una que afecta a toda la
    empresa" son 30 minutos, y las dos preguntas se parecen mucho.

    Por eso el bloque va marcado como orientativo y SIEMPRE acompanado del texto
    de la clausula.
    """

    def test_incluye_pregunta_respuesta_y_cita(self):
        bloque = build_faq_block([faq()])
        assert "¿Quien me avisa como va mi solicitud?" in bloque
        assert "El Jefe de TI informa el estado" in bloque
        assert "STI-PR-01 v4" in bloque
        assert "seccion 7.6" in bloque

    def test_siempre_arrastra_el_texto_de_la_clausula(self):
        """Sin la fuente, una pregunta parecida-pero-distinta no tendria correccion."""
        bloque = build_faq_block([faq()])
        assert "Texto de la clausula" in bloque
        assert "informa al solicitante el estado" in bloque

    def test_distingue_lo_revisado_de_lo_generado(self):
        assert "revisada por Calidad" in build_faq_block([faq(reviewed=True)])
        assert "sin revisar" in build_faq_block([faq(reviewed=False)])

    def test_sin_entradas_no_produce_bloque(self):
        assert build_faq_block([]) == ""

    def test_separa_varias_entradas(self):
        bloque = build_faq_block([faq(), faq(pregunta="¿Cuanto tarda una incidencia?")])
        assert "[FAQ 1]" in bloque
        assert "[FAQ 2]" in bloque


class TestResultadoConFaq:
    def test_un_acierto_de_faq_cuenta_como_contexto(self):
        """
        Si el FAQ acierta pero ninguna clausula pasa el umbral, el turno tiene
        contexto igual: el bridge de vocabulario es el punto de tener FAQ.
        """
        resultado = RetrievalResult(accepted=[], rejected=[], max_distance=0.49, faqs=[faq()])
        assert bool(resultado) is True

    def test_sin_nada_sigue_siendo_falsy(self):
        assert not RetrievalResult(accepted=[], rejected=[], max_distance=0.49)

    def test_el_debug_registra_las_faq(self):
        """La traza de auditoria debe mostrar si una respuesta se apoyo en el FAQ."""
        debug = RetrievalResult(
            accepted=[], rejected=[], max_distance=0.49, faqs=[faq(reviewed=True)]
        ).to_debug()

        assert len(debug["faqs"]) == 1
        assert debug["faqs"][0]["reviewed"] is True
        assert debug["faqs"][0]["code"] == "STI-PR-01"

    def test_sin_faq_el_debug_no_trae_la_clave(self):
        assert "faqs" not in RetrievalResult([], [], 0.49).to_debug()
