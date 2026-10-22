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
        assert set(fn["parameters"]["required"]) == {
            "finding_id", "description", "responsible", "due_date",
        }

    def test_responsable_y_plazo_son_obligatorios(self):
        """
        Regla de negocio dura: no puede haber CAPAs huerfanos.

        La primera version los declaraba opcionales y se limitaba a avisar de lo
        que faltaba. Avisar no es exigir: quedo uno huerfano en la base. Ahora
        lo impiden las tres capas -- el esquema que ve el modelo, la funcion, y
        un NOT NULL en la tabla.
        """
        req = esquema("registrar_accion_correctiva")["parameters"]["required"]
        assert "responsible" in req
        assert "due_date" in req

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


class TestNoSeCreanAccionesIncompletas:
    """
    La funcion se niega y DICE que falta, para que el modelo lo pregunte.

    Devolver un error mudo haria que el modelo reintentara inventando un
    responsable, que es peor que no registrar nada.
    """

    def _llamar(self, **kw):
        from app.core.agents.tools import registrar_accion_correctiva
        return registrar_accion_correctiva(
            db=None, tenant_id="t", finding_id=1, description="Reemplazar extintores", **kw
        )

    def test_sin_responsable(self):
        r = self._llamar(responsible="", due_date="2026-11-15")
        assert r["error"] == "datos_incompletos"
        assert "quien se encarga" in r["faltan"]

    def test_sin_fecha(self):
        r = self._llamar(responsible="Jefe de Almacen", due_date="")
        assert r["error"] == "datos_incompletos"
        assert "para cuando" in r["faltan"]

    def test_fecha_ilegible_no_cuela(self):
        """'el mes que viene' no es una fecha: antes se guardaba sin plazo."""
        r = self._llamar(responsible="Calidad", due_date="el mes que viene")
        assert r["error"] == "datos_incompletos"
        assert "AAAA-MM-DD" in r["message"]


class TestPlazoEnElPasado:
    """
    Una accion correctiva no puede vencer antes de existir.

    El modelo no sabe en que ano vive. A "antes del 20 de noviembre", estando
    en octubre de 2026, contesto 2025-11-20: la accion nacia vencida hacia once
    meses y el panel la habria pintado en rojo desde el primer segundo. Se
    arregla por los dos lados -- la fecha de hoy va en el prompt, y aqui se
    rechaza lo que ya paso.
    """

    def _llamar(self, due_date):
        from app.core.agents.tools import registrar_accion_correctiva

        # Una fecha valida deja pasar la llamada hasta la consulta del hallazgo,
        # asi que hace falta una sesion que sepa decir "no existe". Lo que se
        # comprueba es que NO rebote por la fecha.
        class SesionVacia:
            def query(self, *a, **k):
                return self

            def filter_by(self, *a, **k):
                return self

            def one_or_none(self):
                return None

        return registrar_accion_correctiva(
            db=SesionVacia(), tenant_id="t", finding_id=1,
            description="Reemplazar extintores",
            responsible="Jefe de Almacen", due_date=due_date,
        )

    def test_se_rechaza_una_fecha_pasada(self):
        from datetime import date, timedelta
        ayer = (date.today() - timedelta(days=1)).isoformat()
        r = self._llamar(ayer)
        assert r["error"] == "fecha_en_el_pasado"
        assert ayer in r["message"]

    def test_hoy_vale(self):
        """El plazo puede ser hoy mismo: es urgente, no invalido."""
        from datetime import date
        r = self._llamar(date.today().isoformat())
        assert r.get("error") != "fecha_en_el_pasado"

    def test_el_futuro_vale(self):
        from datetime import date, timedelta
        r = self._llamar((date.today() + timedelta(days=30)).isoformat())
        assert r.get("error") != "fecha_en_el_pasado"


class TestLaFechaDeHoyViajaEnElPrompt:
    def test_el_recordatorio_dice_la_fecha(self):
        from datetime import date

        from app.core.guardrails.grounding_check import out_of_scope_hint

        assert date.today().isoformat() in out_of_scope_hint()
