"""
Tests de lo que hace falta para exponer NormIA fuera de localhost.

El agujero real: `tenant_id` llegaba en el cuerpo de la peticion. Bastaba
conocer el identificador de otro cliente -- que no es un secreto: aparece en la
configuracion y en cualquier captura -- para consultar su SGC entero. Y
/api/admin/* respondia sin credenciales, incluidos los DELETE.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.config import settings
from app.security import VentanaDeslizante, require_admin_key, resolve_tenant


@pytest.fixture
def limpio(monkeypatch):
    monkeypatch.setattr(settings, "public_tenant_id", "")
    monkeypatch.setattr(settings, "admin_api_key", "")
    return settings


# --- Resolucion del tenant ---

def test_sin_tenant_publico_se_respeta_lo_pedido(limpio):
    assert resolve_tenant("empresa-demo-iso") == "empresa-demo-iso"


def test_con_tenant_publico_se_ignora_lo_pedido(limpio, monkeypatch):
    monkeypatch.setattr(settings, "public_tenant_id", "demo-publica")
    # Este es EL caso: alguien pide el SGC del cliente y recibe el corpus demo.
    assert resolve_tenant("empresa-demo-iso") == "demo-publica"


def test_con_tenant_publico_tampoco_sirve_uno_inventado(limpio, monkeypatch):
    monkeypatch.setattr(settings, "public_tenant_id", "demo-publica")
    assert resolve_tenant("lo-que-sea") == "demo-publica"
    assert resolve_tenant("") == "demo-publica"


# --- Clave de administracion ---

def test_sin_clave_configurada_no_se_exige_nada(limpio):
    require_admin_key(None)  # en local no debe estorbar


def test_con_clave_configurada_se_rechaza_sin_cabecera(limpio, monkeypatch):
    monkeypatch.setattr(settings, "admin_api_key", "secreta")
    with pytest.raises(HTTPException) as e:
        require_admin_key(None)
    assert e.value.status_code == 401


def test_con_clave_configurada_se_rechaza_la_incorrecta(limpio, monkeypatch):
    monkeypatch.setattr(settings, "admin_api_key", "secreta")
    with pytest.raises(HTTPException) as e:
        require_admin_key("otra")
    assert e.value.status_code == 401


def test_la_clave_correcta_pasa(limpio, monkeypatch):
    monkeypatch.setattr(settings, "admin_api_key", "secreta")
    require_admin_key("secreta")


# --- Limite de peticiones ---

def test_sin_limite_configurado_todo_pasa():
    v = VentanaDeslizante(0)
    assert all(v.permite("1.2.3.4") for _ in range(500))


def test_se_corta_al_superar_el_limite():
    v = VentanaDeslizante(3)
    assert [v.permite("1.2.3.4") for _ in range(5)] == [True, True, True, False, False]


def test_el_limite_es_por_ip():
    v = VentanaDeslizante(2)
    assert [v.permite("1.1.1.1") for _ in range(3)] == [True, True, False]
    # Otra IP no debe pagar por la primera.
    assert v.permite("2.2.2.2") is True


def test_la_ventana_libera_al_pasar_el_minuto(monkeypatch):
    reloj = {"t": 1000.0}
    monkeypatch.setattr("app.security.time.monotonic", lambda: reloj["t"])
    v = VentanaDeslizante(2)
    assert [v.permite("1.1.1.1") for _ in range(3)] == [True, True, False]
    reloj["t"] += 61.0
    assert v.permite("1.1.1.1") is True
