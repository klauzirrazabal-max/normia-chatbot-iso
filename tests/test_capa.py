"""
El ciclo CAPA tiene dos mitades y solo estaba la primera.

`register_finding` creaba el hallazgo y ahi se acababa: ninguna linea del
proyecto instanciaba una `CapaAction`. En la base habia 43 hallazgos y 0
acciones correctivas, con cinco copias del mismo incidente por reintentos. La
clausula 10.2 pide registrar la naturaleza de la no conformidad Y las acciones
tomadas; con media mitad no se cumple.
"""

from __future__ import annotations

import pytest

from app.core.agents.tools import TOOLS_SCHEMA, _huella


def esquema(nombre: str) -> dict:
    for t in TOOLS_SCHEMA:
        if t["function"]["name"] == nombre:
            return t["function"]
    raise AssertionError(f"no esta declarada la herramienta {nombre}")


class TestHerramientaDeclarada:
    def test_existe_la_accion_correctiva(self):
        fn = esquema("registrar_accion_correctiva")
        assert set(fn["parameters"]["required"]) == {"finding_id", "description"}

    def test_pide_responsable_y_plazo(self):
        """Sin responsable y sin fecha, una accion correctiva no se puede seguir."""
        props = esquema("registrar_accion_correctiva")["parameters"]["properties"]
        assert "responsible" in props
        assert "due_date" in props

    def test_el_hallazgo_lleva_severidad(self):
        props = esquema("register_finding")["parameters"]["properties"]
        assert props["severity"]["enum"] == ["observacion", "menor", "mayor"]
        assert "area" in props

    def test_la_severidad_no_es_obligatoria(self):
        """
        El modelo la propone, no la exige: un hallazgo sin clasificar se guarda
        como 'menor' y se reclasifica. Perderlo por no saber la severidad seria
        peor que clasificarlo mal.
        """
        assert esquema("register_finding")["parameters"]["required"] == ["description"]


class TestHuellaParaDuplicados:
    """
    Dos relatos del mismo incidente son un reintento, no dos no conformidades.
    """

    @pytest.mark.parametrize("a,b", [
        ("Encontré extintores vencidos en el almacen",
         "encontre extintores vencidos en el almacen"),
        ("extintores vencidos!!", "extintores   vencidos"),
        ("Extintores Vencidos.", "extintores vencidos"),
    ])
    def test_el_mismo_hallazgo(self, a, b):
        assert _huella(a) == _huella(b)

    @pytest.mark.parametrize("a,b", [
        ("extintores vencidos en el almacen", "extintores vencidos en recepcion"),
        ("extintores vencidos", "botiquin incompleto"),
    ])
    def test_hallazgos_distintos(self, a, b):
        assert _huella(a) != _huella(b)
