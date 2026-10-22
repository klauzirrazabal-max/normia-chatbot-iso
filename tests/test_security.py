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


# --- De quien nos fiamos para saber la IP ------------------------------------
#
# El limite de peticiones era decorativo. `ip_del_cliente` leia
# `X-Forwarded-For` y se quedaba con su PRIMER valor, que es exactamente el que
# escribe el cliente. Medido antes del arreglo: 60 peticiones seguidas desde la
# misma maquina cambiando esa cabecera, 0 bloqueadas de 60.
#
# Importa mas de lo que parece porque la generacion de imagenes es publica y
# ocupa la GPU casi un minuto por pieza.

from types import SimpleNamespace  # noqa: E402

from app.security import ip_del_cliente  # noqa: E402


def peticion(xff=None, cf=None, ip="203.0.113.9"):
    cabeceras = {}
    if xff:
        cabeceras["x-forwarded-for"] = xff
    if cf:
        cabeceras["cf-connecting-ip"] = cf
    return SimpleNamespace(headers=cabeceras, client=SimpleNamespace(host=ip))


class TestSinProxyDeclarado:
    """Por defecto no hay proxy, y entonces NINGUNA cabecera merece credito."""

    def test_se_ignora_la_cabecera_reenviada(self, monkeypatch):
        monkeypatch.setattr(settings, "trusted_proxy", "")
        assert ip_del_cliente(peticion(xff="1.2.3.4")) == "203.0.113.9"

    def test_falsificarla_ya_no_evade_el_limite(self, monkeypatch):
        monkeypatch.setattr(settings, "trusted_proxy", "")
        limitador = VentanaDeslizante(5)
        vistas = {ip_del_cliente(peticion(xff=f"10.0.0.{i}")) for i in range(40)}
        assert vistas == {"203.0.113.9"}, "todas deben caer en el mismo cubo"
        permitidas = sum(1 for _ in range(40) if limitador.permite("203.0.113.9"))
        assert permitidas == 5


class TestTrasCloudflare:
    """Cloudflare SOBRESCRIBE `cf-connecting-ip`; lo que mande el cliente se pierde."""

    def test_se_usa_la_cabecera_de_cloudflare(self, monkeypatch):
        monkeypatch.setattr(settings, "trusted_proxy", "cloudflare")
        assert ip_del_cliente(peticion(cf="198.51.100.7")) == "198.51.100.7"

    def test_x_forwarded_for_sigue_sin_valer(self, monkeypatch):
        monkeypatch.setattr(settings, "trusted_proxy", "cloudflare")
        assert ip_del_cliente(peticion(xff="1.2.3.4")) == "203.0.113.9"


class TestTrasProxyGenerico:
    def test_se_toma_el_ultimo_salto(self, monkeypatch):
        """
        El ultimo valor lo anade el proxy en el que confiamos. Los anteriores
        los pudo escribir el cliente, asi que se descartan.
        """
        monkeypatch.setattr(settings, "trusted_proxy", "nginx")
        assert ip_del_cliente(peticion(xff="1.2.3.4, 198.51.100.7")) == "198.51.100.7"


class TestClaveCondicionada:
    """
    El catalogo de clausulas solo necesita clave si sirve datos del cliente.

    Pedirsela a quien solo va a generar un cartel era la friccion que convertia
    la demo en un dialogo del navegador pidiendo credenciales.
    """

    def test_con_tenant_publico_pasa_sin_clave(self, limpio, monkeypatch):
        from app.security import require_admin_key_salvo_demo

        monkeypatch.setattr(settings, "public_tenant_id", "demo-publica")
        monkeypatch.setattr(settings, "admin_api_key", "secreto")
        require_admin_key_salvo_demo(None)

    def test_sin_tenant_publico_si_la_exige(self, limpio, monkeypatch):
        from app.security import require_admin_key_salvo_demo

        monkeypatch.setattr(settings, "admin_api_key", "secreto")
        with pytest.raises(HTTPException) as exc:
            require_admin_key_salvo_demo(None)
        assert exc.value.status_code == 401
