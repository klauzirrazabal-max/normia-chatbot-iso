"""
Tests de verificacion de citas.

El caso que motivo este modulo es real y grave: preguntando por tiempos de
atencion de TI, el asistente respondio "Segun PROC-CAL-04 v3, seccion 5.2, no
se aplica directamente..." -- y PROC-CAL-04 NO EXISTE en el SGC. El codigo
venia como ejemplo de formato en el prompt de sistema, y el modelo lo cito
como si fuera un documento real.

El guardrail anterior lo dejo pasar porque solo exigia que apareciera AL MENOS
UN codigo recuperado. Una respuesta que cita bien una fuente y ademas inventa
otra es peor que no responder: en una auditoria, la cita inventada invalida
todo el sistema.
"""

from app.core.guardrails.grounding_check import (
    PHANTOM_CITATION_MESSAGE,
    classify_citations,
    extract_cited_codes,
    extract_cited_versions,
    repair_cited_versions,
)
from app.core.orchestrator import _read_document_citations
from app.core.rag.retriever import RetrievedChunk


def chunk(code="STI-PR-01", version="v4", section="7.6", distance=0.27):
    return RetrievedChunk(
        chunk_id=1, content="x", code=code, version=version, section=section, distance=distance
    )


class TestExtraccionDeCodigos:
    def test_detecta_el_formato_del_sgc(self):
        assert extract_cited_codes("Segun STI-PR-01 v4 el plazo es NBD") == {"STI-PR-01"}

    def test_detecta_segmentos_de_distinto_largo(self):
        """PROC-CAL-04 tiene 4-3-2; STI-PR-01 tiene 3-2-2. Ambos deben detectarse."""
        codigos = extract_cited_codes("Ver PROC-CAL-04 y tambien STI-PR-01 y GTH-MN-02")
        assert codigos == {"PROC-CAL-04", "STI-PR-01", "GTH-MN-02"}

    def test_varios_codigos_en_una_respuesta(self):
        texto = "Segun CAL-PR-03 v2 y CAL-FO-10 v1, debes registrarlo."
        assert extract_cited_codes(texto) == {"CAL-PR-03", "CAL-FO-10"}

    def test_texto_sin_codigos(self):
        assert extract_cited_codes("La calibracion se hace cada ano.") == set()

    def test_vacio_no_rompe(self):
        assert extract_cited_codes("") == set()


class TestClasificacionDeCitas:
    KNOWN = {"STI-PR-01", "CAL-PR-03", "CYP-PR-02"}

    def test_cita_respaldada_por_el_contexto(self):
        respaldados, no_recuperados, inexistentes = classify_citations(
            "Segun STI-PR-01 v4, seccion 7.6, el plazo es NBD.", [chunk()], self.KNOWN
        )
        assert respaldados == {"STI-PR-01"}
        assert not no_recuperados
        assert not inexistentes

    def test_detecta_documento_inexistente(self):
        """El fallo real: PROC-CAL-04 no existe en el SGC."""
        _, _, inexistentes = classify_citations(
            "Segun PROC-CAL-04 v3, seccion 5.2, no aplica. Segun STI-PR-01 v4 el plazo es NBD.",
            [chunk()],
            self.KNOWN,
        )
        assert inexistentes == {"PROC-CAL-04"}

    def test_una_cita_correcta_no_absuelve_a_una_inventada(self):
        """
        Justo lo que el guardrail anterior dejaba pasar: la respuesta citaba
        STI-PR-01 (real y recuperado) Y PROC-CAL-04 (inventado), y como habia
        al menos un codigo valido se marcaba como fundamentada.
        """
        respaldados, _, inexistentes = classify_citations(
            "Segun PROC-CAL-04 v3 no aplica, pero segun STI-PR-01 v4 el plazo es NBD.",
            [chunk()],
            self.KNOWN,
        )
        assert respaldados, "la cita valida se reconoce"
        assert inexistentes, "y la inventada TAMBIEN se detecta"

    def test_documento_real_pero_no_recuperado(self):
        """
        El modelo tiro de memoria en vez del contexto. No es alucinacion pura,
        pero la respuesta deja de estar fundamentada en lo recuperado.
        """
        _, no_recuperados, inexistentes = classify_citations(
            "Segun CAL-PR-03 v2 debes registrar el hallazgo.", [chunk()], self.KNOWN
        )
        assert no_recuperados == {"CAL-PR-03"}
        assert not inexistentes

    def test_codigo_desambiguado_cuenta_como_recuperado(self):
        """Los codigos con calificador -- 'CAL-FO-13 (Compras)' -- deben cruzar bien."""
        respaldados, _, inexistentes = classify_citations(
            "Segun CAL-FO-13 v1 se documenta el proceso.",
            [chunk(code="CAL-FO-13 (Compras)", version="v1")],
            {"CAL-FO-13"},
        )
        assert respaldados == {"CAL-FO-13"}
        assert not inexistentes


def test_el_mensaje_de_cita_fantasma_no_repite_el_codigo_inventado():
    """Nombrar el documento inventado en la disculpa lo haria parecer real."""
    assert "PROC-CAL-04" not in PHANTOM_CITATION_MESSAGE
    assert "Calidad" in PHANTOM_CITATION_MESSAGE


class TestCitasDeDocumentoLeido:
    """
    Una respuesta construida leyendo un documento completo se cita a nivel de ese
    documento, con las secciones que menciona. Antes se listaban solo los dos
    fragmentos que habia traido el RAG, aunque el resumen referenciara doce
    clausulas: dos fuentes para un texto respaldado por todo el documento.
    """

    RESULTADO = (
        "leer_documento",
        {
            "codigo": "STI-PR-01",
            "version": "v4",
            "titulo": "Atencion de Solicitudes Tecnologicas",
            "secciones": [
                {"seccion": "1", "contenido": "objetivo"},
                {"seccion": "6.1", "contenido": "soporte nivel 1"},
                {"seccion": "7.6", "contenido": "cuadro de tiempos"},
                {"seccion": "9", "contenido": "anexos"},
            ],
        },
    )

    def test_cita_las_secciones_que_la_respuesta_nombra(self):
        respuesta = (
            "El objetivo esta en la Seccion 1 y los tiempos en la seccion 7.6 del "
            "procedimiento STI-PR-01 v4."
        )
        citas = _read_document_citations([self.RESULTADO], respuesta)
        secciones = {c.section for c in citas}

        assert secciones == {"1", "7.6"}
        assert all(c.code == "STI-PR-01" and c.version == "v4" for c in citas)

    def test_sin_secciones_nombradas_cita_el_documento_entero(self):
        citas = _read_document_citations([self.RESULTADO], "El procedimiento regula el soporte.")
        assert len(citas) == 1
        assert citas[0].section is None
        assert citas[0].code == "STI-PR-01"

    def test_ignora_secciones_que_el_documento_no_tiene(self):
        """El modelo puede nombrar una seccion inexistente; no se cita."""
        citas = _read_document_citations([self.RESULTADO], "Ver la seccion 99.")
        assert all(c.section != "99" for c in citas)

    def test_otras_herramientas_no_generan_citas_de_documento(self):
        resultado = ("buscar_documentos", {"total": 2, "documentos": []})
        assert _read_document_citations([resultado], "Seccion 1") == []

    def test_sin_resultados_no_cita_nada(self):
        assert _read_document_citations([], "Seccion 1") == []


class TestCodigosMencionadosEnElContexto:
    """
    Los procedimientos ISO se referencian entre si constantemente: la clausula
    "8. REGISTROS" de STI-PR-01 lista literalmente STI-FO-01 y STI-FO-02, y
    "3. DOCUMENTOS DE REFERENCIA" existe en todos ellos.

    Citar esos codigos es leer bien el documento. Pero el guardrail los trataba
    como "recordados de memoria" y marcaba la respuesta como no fundamentada:
    preguntando por la seccion 8, la respuesta era correcta y salia con
    grounded=False.
    """

    KNOWN = {"STI-PR-01", "STI-FO-01", "STI-FO-02", "CAL-PR-03"}

    def registros(self):
        return RetrievedChunk(
            chunk_id=1,
            content=(
                "8. REGISTROS Codigo | Nombre S/C | Formulario de Solicitudes "
                "STI-FO-01 | Dashboard de Solicitudes STI-FO-02 | Cronograma"
            ),
            code="STI-PR-01",
            version="v4",
            section="8",
            distance=0.0,
        )

    def test_un_codigo_que_esta_en_el_texto_cuenta_como_respaldado(self):
        respuesta = "Segun STI-PR-01 v4 seccion 8, los registros son STI-FO-01 y STI-FO-02."
        respaldados, no_recuperados, inexistentes = classify_citations(
            respuesta, [self.registros()], self.KNOWN
        )

        assert respaldados == {"STI-PR-01", "STI-FO-01", "STI-FO-02"}
        assert not no_recuperados, "estan en el contexto, no se recordaron"
        assert not inexistentes

    def test_un_codigo_ausente_del_contexto_sigue_siendo_sospechoso(self):
        """Si no estaba ni en los fragmentos ni en su texto, el modelo lo recordo."""
        _, no_recuperados, inexistentes = classify_citations(
            "Segun STI-PR-01 v4 y tambien CAL-PR-03 v2...", [self.registros()], self.KNOWN
        )
        assert no_recuperados == {"CAL-PR-03"}
        assert not inexistentes

    def test_una_cita_inventada_sigue_detectandose(self):
        """El cambio no debe abrir la puerta a codigos que no existen."""
        _, _, inexistentes = classify_citations(
            "Segun PROC-CAL-04 v3...", [self.registros()], self.KNOWN
        )
        assert inexistentes == {"PROC-CAL-04"}


class TestVerificacionDeVersion:
    """
    El guardrail comprobaba que el codigo existiera, pero no la version.

    Caso real: preguntando por politicas de IA, la respuesta escribio
    "STI-PO-01 v1" cuando el documento esta en v2 -- y la lista de fuentes, que
    genera el servidor desde la base, decia v2. El texto se contradecia con sus
    propias fuentes.

    En un SGC la version es parte de la identidad del documento: "v1" puede ser
    una version derogada, asi que citarla mal equivale a citar otro documento.
    """

    TODAS = {"STI-PO-01": {"v2"}, "CAL-IN-01": {"v1", "v2"}, "COM-PR-01": {"v3"}}
    VIGENTES = {"STI-PO-01": "v2", "CAL-IN-01": "v2", "COM-PR-01": "v3"}

    def repara(self, texto):
        return repair_cited_versions(texto, self.TODAS, self.VIGENTES)

    def test_extrae_los_pares_citados(self):
        pares = extract_cited_versions("Segun STI-PO-01 v2 y COM-PR-01 v3, el proceso...")
        assert pares == {("STI-PO-01", "v2"), ("COM-PR-01", "v3")}

    def test_reconoce_las_formas_de_escribir_la_version(self):
        for texto in ("STI-PO-01 v2", "STI-PO-01 (v2)", "STI-PO-01 version 2", "STI-PO-01, v. 2"):
            assert ("STI-PO-01", "v2") in extract_cited_versions(texto), texto

    def test_corrige_una_version_inexistente(self):
        texto, correcciones = self.repara("Segun STI-PO-01 v1, la politica establece...")
        assert "STI-PO-01 v2" in texto
        assert correcciones == [("STI-PO-01", "v1", "v2")]

    def test_no_toca_una_version_correcta(self):
        texto, correcciones = self.repara("Segun STI-PO-01 v2, la politica establece...")
        assert not correcciones
        assert "v2" in texto

    def test_respeta_una_version_obsoleta_que_si_existe(self):
        """
        Preguntar por una version derogada es legitimo: CAL-IN-01 v1 existe como
        obsoleta, asi que citarla no es un error y no se corrige.
        """
        texto, correcciones = self.repara("La version anterior CAL-IN-01 v1 decia otra cosa.")
        assert not correcciones
        assert "CAL-IN-01 v1" in texto

    def test_no_inventa_una_version_para_un_codigo_desconocido(self):
        texto, correcciones = self.repara("Segun XXX-YY-99 v7...")
        assert not correcciones
        assert "XXX-YY-99 v7" in texto

    def test_corrige_varias_en_el_mismo_texto(self):
        texto, correcciones = self.repara("Segun STI-PO-01 v1 y COM-PR-01 v1, el flujo...")
        assert len(correcciones) == 2
        assert "STI-PO-01 v2" in texto
        assert "COM-PR-01 v3" in texto

    def test_texto_vacio_no_rompe(self):
        assert self.repara("") == ("", [])
