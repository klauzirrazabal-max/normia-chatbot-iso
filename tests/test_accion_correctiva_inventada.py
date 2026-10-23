"""
Una accion correctiva afirmada tiene que existir.

Medido en vivo durante el ensayo de la presentacion: al contestar "se encarga el
jefe de operaciones, antes del 15 de noviembre", el bot respondio "La accion
correctiva quedo registrada con el ID 100". No se ejecuto ninguna herramienta en
ese turno y la accion 100 no existia -- la ultima era la 9. El hallazgo se quedo
`abierto`, sin responsable ni fecha, y el usuario creia haber cerrado el ciclo.

En una auditoria, una no conformidad mayor sin accion correctiva es un hallazgo
contra la organizacion. Que ademas nadie se entere es lo que lo hace caro.
"""

from __future__ import annotations

from app.core.orchestrator import _ID_ACCION_RE, _acciones_inventadas


class _Query:
    def __init__(self, existentes):
        self._existentes = existentes
        self._pedidos: set[int] = set()

    def filter(self, criterio):
        # `CapaAction.id.in_(afirmados)` -- se leen los valores del IN.
        self._pedidos = {v for v in criterio.right.value}
        return self

    def all(self):
        return [(i,) for i in sorted(self._pedidos & self._existentes)]


class _DB:
    def __init__(self, existentes=frozenset()):
        self._existentes = set(existentes)

    def query(self, _col):
        return _Query(self._existentes)


def test_caza_el_id_inventado_del_ensayo():
    texto = (
        "La acción correctiva quedó registrada con el **ID 100**, asignada al "
        "**Jefe de Operaciones** con fecha límite **15 de noviembre de 2026**."
    )
    assert _acciones_inventadas(_DB(existentes={9}), texto) == [100]


def test_no_molesta_cuando_la_accion_si_existe():
    texto = "La acción correctiva quedó registrada con el ID 9."
    assert _acciones_inventadas(_DB(existentes={9}), texto) == []


def test_el_id_de_un_hallazgo_no_cuenta_como_accion():
    """
    "He registrado la no conformidad con el ID 50" es verdad y no habla de una
    accion correctiva. Confundirlos bloquearia el turno bueno del ciclo.
    """
    texto = "He registrado la no conformidad con el ID 50 y severidad mayor."
    assert _acciones_inventadas(_DB(existentes=set()), texto) == []


def test_una_respuesta_documental_no_dispara_nada():
    texto = "La velocidad máxima es 15 km/h. Lo define OPE-PO-02 v1, sección 4.1."
    assert _acciones_inventadas(_DB(existentes=set()), texto) == []


def test_el_patron_lee_el_id_en_los_dos_ordenes():
    assert _ID_ACCION_RE.search("acción correctiva registrada con el ID 12")
    assert _ID_ACCION_RE.search("ID 12 corresponde a la acción correctiva")


# --- El mismo fallo con el hallazgo ------------------------------------------


class _DB2:
    """Base con dos tablas: hallazgos y acciones, por id."""

    def __init__(self, hallazgos=frozenset(), acciones=frozenset()):
        self._por_tabla = {"findings": set(hallazgos), "capa_actions": set(acciones)}
        self._actual: set[int] = set()

    def query(self, col):
        self._actual = self._por_tabla[col.parent.tables[0].name]
        return self

    def filter(self, criterio):
        self._pedidos = {v for v in criterio.right.value}
        return self

    def all(self):
        return [(i,) for i in sorted(self._pedidos & self._actual)]


def test_caza_el_hallazgo_inventado_del_ensayo():
    """
    "He registrado la no conformidad con el ID 51" cuando el ultimo era el 50 y
    `register_finding` no se ejecuto en ese turno.
    """
    from app.core.orchestrator import _registros_inventados

    texto = "He registrado la no conformidad con el **ID 51** y severidad **mayor**."
    assert _registros_inventados(_DB2(hallazgos={50}), texto) == ["hallazgo #51"]


def test_no_molesta_cuando_el_hallazgo_si_existe():
    from app.core.orchestrator import _registros_inventados

    texto = "He registrado la no conformidad con el ID 50 y severidad mayor."
    assert _registros_inventados(_DB2(hallazgos={50}), texto) == []


def test_caza_los_dos_a_la_vez():
    from app.core.orchestrator import _registros_inventados

    texto = (
        "He registrado la no conformidad con el ID 51. "
        "La acción correctiva quedó registrada con el ID 100."
    )
    assert _registros_inventados(_DB2(hallazgos={50}, acciones={9}), texto) == [
        "accion correctiva #100",
        "hallazgo #51",
    ]


def test_una_consulta_documental_sigue_sin_disparar_nada():
    from app.core.orchestrator import _registros_inventados

    texto = "La velocidad máxima es 15 km/h, según OPE-PO-02 v1, sección 4.1."
    assert _registros_inventados(_DB2(), texto) == []
