"""
El usuario decide si su consulta se deriva a Calidad.

Antes no decidia: el asistente afirmaba "te voy a conectar con el Responsable de
Calidad" y escribia la escalacion en el mismo turno. El resultado medido fueron
306 escalaciones pendientes que nadie encargo, y una bandeja asi se deja de
mirar. Estos tests fijan las dos mitades del arreglo: que el detector de si/no
sea conservador, y que lo ofrecido no entre en la bandeja.
"""

from __future__ import annotations

import pytest

from app.core.routing import leer_consentimiento


class TestAcepta:
    @pytest.mark.parametrize("texto", [
        "si", "sí", "Sí", "SI", "sip", "claro", "dale", "ok", "okey", "vale",
        "bueno", "listo", "correcto", "adelante", "por favor", "sí, por favor",
        "ok gracias", "hazlo", "derivalo", "escalalo", "está bien", "de acuerdo",
    ])
    def test_afirmaciones(self, texto):
        assert leer_consentimiento(texto) is True


class TestRechaza:
    @pytest.mark.parametrize("texto", [
        "no", "No", "nop", "no gracias", "No, gracias", "mejor no", "dejalo",
        "olvidalo", "cancela", "no hace falta", "no es necesario", "después",
        "más tarde", "no por ahora", "ya no",
    ])
    def test_negaciones(self, texto):
        assert leer_consentimiento(texto) is False


class TestNoContesta:
    """
    Lo ambiguo vale None, y None deja seguir el flujo normal.

    Es el lado seguro del error: si el detector no entiende, la consulta se
    atiende como consulta y la propuesta se queda sin aceptar. Lo contrario
    (asumir un si) es exactamente el bug que se esta arreglando.
    """

    @pytest.mark.parametrize("texto", [
        "cual es el procedimiento de compras?",
        "y el almacen?",
        "no entiendo el procedimiento de compras, me lo explicas?",
        "si el documento esta obsoleto, que hago?",
        "",
        "   ",
    ])
    def test_ni_si_ni_no(self, texto):
        assert leer_consentimiento(texto) is None

    def test_un_parrafo_no_es_una_respuesta(self):
        """
        Un si o un no son cortos. Un parrafo que empieza por "si" es una
        pregunta condicional, y tratarlo como consentimiento le secuestra el
        turno al usuario.
        """
        largo = (
            "si quieres te explico que paso en el almacen la semana pasada con "
            "los extintores y de paso revisamos el procedimiento completo"
        )
        assert leer_consentimiento(largo) is None
