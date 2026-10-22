"""
Evidencia de aprobacion: quien aprobo el documento y cuando.

La clausula 7.5.2 pide que la informacion documentada se revise y se apruebe
antes de emitirse. La ingesta validaba QUE documento es y en que version, pero
no si alguien lo habia aprobado: un procedimiento sin aprobar que el asistente
cita como vigente es justo la no conformidad que este producto evita.

La evidencia esta repartida en dos sitios, y por eso son dos comprobaciones:
el documento declara QUIEN lo aprobo en su bloque de firmas, y la Lista Maestra
declara CUANDO.
"""

from __future__ import annotations

import pytest

from app.core.docs.validation import TIPOS_CON_APROBACION, tipo_de_documento
from app.core.rag.doc_header import ApprovalBlock, parse_approval_words


def palabra(texto: str, x0: float, top: float, ancho: float = 60.0) -> dict:
    return {"text": texto, "x0": x0, "x1": x0 + ancho, "top": top, "bottom": top + 10}


class TestBloqueEnColumnas:
    """
    El diseno habitual: tres etiquetas y el cargo DEBAJO de cada una.

        ELABORADO POR:          REVISADO POR:           APROBADO POR:
        COORDINADOR DE CALIDAD  COORDINADOR DE CALIDAD  GERENTE GENERAL
    """

    def test_lee_los_tres_cargos(self):
        palabras = [
            palabra("ELABORADO", 130, 684), palabra("POR:", 195, 684),
            palabra("REVISADO", 286, 684), palabra("POR:", 345, 684),
            palabra("APROBADO", 437, 684), palabra("POR:", 500, 684),
            palabra("JEFE", 120, 700, 30), palabra("DE", 155, 700, 20),
            palabra("COSTOS", 180, 700, 45),
            palabra("COORDINADOR", 266, 700, 55), palabra("DE", 325, 700, 15),
            palabra("CALIDAD", 345, 700, 45),
            palabra("GERENTE", 430, 700, 50), palabra("GENERAL", 485, 700, 50),
        ]
        b = parse_approval_words(palabras, 595.0)
        assert b.aprobado_por == "GERENTE GENERAL"
        assert b.revisado_por == "COORDINADOR DE CALIDAD"
        assert b.elaborado_por == "JEFE DE COSTOS"
        assert b.tiene_aprobacion

    def test_el_cargo_va_centrado_bajo_su_etiqueta(self):
        """
        Los cargos se centran, asi que empiezan a la IZQUIERDA de la etiqueta.

        Partiendo por la posicion de la etiqueta, "JEFE DE TECNOLOGIA DE LA
        INFORMACION" se leia como "DE LA INFORMACION" y sus tres primeras
        palabras se le atribuian a la columna anterior.
        """
        palabras = [
            palabra("ELABORADO", 130, 684), palabra("REVISADO", 286, 684),
            palabra("APROBADO", 437, 684),
            palabra("JEFE", 394, 700, 16), palabra("DE", 412, 700, 8),
            palabra("TECNOLOGÍA", 422, 700, 43), palabra("DE", 467, 700, 8),
            palabra("LA", 478, 700, 8), palabra("INFORMACIÓN", 488, 700, 48),
        ]
        b = parse_approval_words(palabras, 595.0)
        assert b.aprobado_por == "JEFE DE TECNOLOGÍA DE LA INFORMACIÓN"

    def test_un_hueco_no_corta_el_cargo(self):
        """
        Las tres columnas tienen alturas distintas: "JEFE DE CONTROL Y TOMA DE"
        e "INVENTARIOS" acaban separadas por la segunda linea de la columna del
        medio. Cortar en el hueco truncaba el cargo.
        """
        palabras = [
            palabra("ELABORADO", 130, 684), palabra("REVISADO", 286, 684),
            palabra("APROBADO", 437, 684),
            palabra("JEFE", 408, 700, 18), palabra("DE", 428, 700, 10),
            palabra("CONTROL", 441, 700, 36),
            palabra("COORDINADOR", 262, 712, 55),   # solo la columna del medio
            palabra("INVENTARIOS", 439, 724, 50),
        ]
        b = parse_approval_words(palabras, 595.0)
        assert b.aprobado_por == "JEFE DE CONTROL INVENTARIOS"

    def test_el_pie_legal_no_es_un_cargo(self):
        """Ocupa el ancho entero, asi que cae dentro de las tres columnas."""
        palabras = [
            palabra("APROBADO", 437, 684),
            palabra("Está", 107, 700, 20), palabra("prohibida", 130, 700, 40),
            palabra("la", 175, 700, 10), palabra("reproducción", 190, 700, 55),
            palabra("Gerencia", 440, 700, 40), palabra("General", 485, 700, 40),
        ]
        b = parse_approval_words(palabras, 595.0)
        assert not b.tiene_aprobacion

    def test_sin_bloque_devuelve_vacio(self):
        b = parse_approval_words([palabra("REGISTROS", 100, 200)], 595.0)
        assert b == ApprovalBlock()
        assert not b.tiene_aprobacion


class TestTipoDeDocumento:
    @pytest.mark.parametrize("code,tipo", [
        ("CAL-PR-03", "PR"), ("GTH-MN-01", "MN"), ("CAL-IN-01", "IN"),
        ("STI-PL-01", "PL"), ("CAL-FO-09", "FO"), ("ADM-OR-01", "OR"),
    ])
    def test_el_tipo_va_en_el_segmento_del_medio(self, code, tipo):
        assert tipo_de_documento(code) == tipo

    def test_sin_codigo(self):
        assert tipo_de_documento(None) is None

    def test_un_formato_no_necesita_aprobacion_propia(self):
        """
        Medido sobre el SGC real: los 30 procedimientos, 3 manuales, 5
        instructivos y 2 planes llevan su bloque de firmas; de los 84 formatos,
        solo 14. Un formato es una plantilla, y lo aprobado es el procedimiento
        que lo invoca. Exigirselo produciria 70 avisos falsos.
        """
        assert "FO" not in TIPOS_CON_APROBACION
        assert set(TIPOS_CON_APROBACION) == {"PR", "MN", "IN", "PL"}


class TestBloqueEnLinea:
    """
    El segundo diseno: el cargo va DETRAS de la etiqueta, no debajo.

        _______________________   _________________________________
        Elaborado por Gestión Humana   Aprobado por el Gerente General

    El lector de columnas no lo ve. Sin esta variante, un manual perfectamente
    aprobado salia marcado como sin aprobar, que es el aviso falso que hace que
    se dejen de leer los avisos.
    """

    def _leer(self, texto):
        from app.core.rag.doc_header import _APROBADO_EN_LINEA_RE, _en_linea
        return _en_linea(_APROBADO_EN_LINEA_RE, texto)

    def test_cargo_detras_de_la_etiqueta(self):
        assert self._leer("Aprobado por el Gerente General") == "Gerente General"

    def test_las_dos_columnas_salen_fundidas(self):
        """Se corta en la siguiente etiqueta, o el cargo se traga la otra mitad."""
        linea = "Elaborado por Gestión Humana Aprobado por el Gerente General"
        assert self._leer(linea) == "Gerente General"

    def test_no_se_traga_el_pie_legal(self):
        linea = (
            "Aprobado por el Gerente General Está prohibida la reproducción total "
            "o parcial del presente documento"
        )
        assert self._leer(linea) == "Gerente General"

    def test_sin_etiqueta_no_inventa(self):
        assert self._leer("8. REGISTROS\n9. ANEXOS") is None
