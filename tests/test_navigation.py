"""
Tests de la navegacion explicita por clausulas.

Los botones de sugerencia envian "Del documento STI-PR-01, explicame la seccion
6". Dejar que el modelo tradujera eso a una llamada a `leer_documento` no era
fiable -- medido, lo hacia en unos dos tercios de los casos.

El fallo observado fue peligroso, no ruidoso: al pulsar "Condiciones generales"
el modelo respondio desde fragmentos de OTROS documentos y afirmo que la seccion
6 era "Descripcion del procedimiento" cuando es "Condiciones generales". La
respuesta salio segura y equivocada, con fuentes de STI-PR-02, STI-PO-01,
STI-PO-02 y CAL-MN-01.

Como el formato del mensaje lo generamos nosotros, no hay nada que interpretar:
se enruta en el servidor y el modelo recibe el texto correcto por construccion.
"""

from app.core.orchestrator import _PETICION_SECCION_RE, _dominant_document
from app.core.rag.retriever import RetrievedChunk


def chunk(code="STI-PR-01", section="6", distance=0.3):
    return RetrievedChunk(
        chunk_id=1, content="x", code=code, version="v4", section=section, distance=distance
    )


class TestReconocimientoDePeticionDeSeccion:
    def test_reconoce_el_mensaje_que_generan_los_botones(self):
        match = _PETICION_SECCION_RE.search("Del documento STI-PR-01, explicame la seccion 6")
        assert match
        assert match.group("codigo") == "STI-PR-01"
        assert match.group("seccion") == "6"

    def test_reconoce_clausulas_anidadas(self):
        match = _PETICION_SECCION_RE.search("Del documento CAL-PR-03, explicame la seccion 7.3.1")
        assert match.group("seccion") == "7.3.1"

    def test_tolera_acento_en_seccion(self):
        assert _PETICION_SECCION_RE.search("Del documento STI-PR-01, explícame la sección 7.6")

    def test_no_confunde_una_pregunta_normal(self):
        """Una consulta de contenido debe seguir yendo a la busqueda vectorial."""
        assert _PETICION_SECCION_RE.search("cual es el tiempo de atencion de TI?") is None
        assert _PETICION_SECCION_RE.search("que dice STI-PR-01 sobre los tiempos?") is None

    def test_exige_codigo_y_seccion(self):
        assert _PETICION_SECCION_RE.search("explicame la seccion 6") is None
        assert _PETICION_SECCION_RE.search("Del documento STI-PR-01, resumeme todo") is None


class TestDocumentoDominante:
    """
    Las opciones de seguimiento se ofrecen cuando la respuesta se apoya sobre todo
    en un documento. Se decide en el servidor porque dejarlo al modelo hacia que
    los botones aparecieran y desaparecieran sin motivo visible.
    """

    def test_un_documento_mayoritario(self):
        chunks = [chunk(), chunk(section="6.1"), chunk(code="CMC-PR-03", section="2")]
        assert _dominant_document(chunks) == "STI-PR-01"

    def test_empate_a_la_mitad_cuenta_como_dominante(self):
        chunks = [chunk(), chunk(code="CMC-PR-03")]
        assert _dominant_document(chunks) == "STI-PR-01"

    def test_sin_dominante_no_se_ofrecen_opciones(self):
        chunks = [chunk(), chunk(code="CMC-PR-03"), chunk(code="COM-PR-01")]
        assert _dominant_document(chunks) is None

    def test_sin_fragmentos_no_hay_dominante(self):
        assert _dominant_document([]) is None

    def test_el_calificador_del_codigo_no_divide_el_conteo(self):
        """'CAL-FO-13 (Compras)' y 'CAL-FO-13 (Ventas)' son el mismo formato base."""
        chunks = [chunk(code="CAL-FO-13 (Compras)"), chunk(code="CAL-FO-13 (Calidad)")]
        assert _dominant_document(chunks) == "CAL-FO-13"
