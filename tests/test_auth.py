"""
Identidad: quien eres, que puedes hacer, y que no se puede falsificar.

No habia usuarios: solo una clave de administracion que el navegador pedia con
un dialogo y guardaba en localStorage, donde cualquier script de la pagina
podia leerla. Para un SGC eso no basta, porque marcar un documento como
obsoleto es un acto controlado y la norma pregunta QUIEN lo hizo.
"""

from __future__ import annotations

import pytest

from app.core.auth import (
    Rol,
    cifrar,
    firmar_sesion,
    leer_sesion,
    puede_administrar,
    puede_gestionar,
    verificar,
)


class TestContrasenas:
    def test_la_correcta_pasa(self):
        assert verificar("secreta123", cifrar("secreta123"))

    def test_la_incorrecta_no(self):
        assert not verificar("otra", cifrar("secreta123"))

    def test_cada_hash_lleva_su_propia_sal(self):
        """
        Dos personas con la misma contrasena no pueden tener el mismo hash: si
        lo tuvieran, romper uno los rompe todos y ademas se ve a simple vista
        quien comparte clave.
        """
        assert cifrar("misma") != cifrar("misma")

    def test_la_contrasena_no_se_guarda_en_claro(self):
        assert "secreta123" not in cifrar("secreta123")

    @pytest.mark.parametrize("basura", ["", "nada", "scrypt$roto", "md5$a$b$c$d$e"])
    def test_un_hash_corrupto_devuelve_falso_en_vez_de_reventar(self, basura):
        """Una fila estropeada no puede tumbar el acceso de todos los demas."""
        assert verificar("loquesea", basura) is False


class TestSesion:
    def test_ida_y_vuelta(self):
        assert leer_sesion(firmar_sesion(42)).user_id == 42

    def test_no_se_puede_cambiar_de_usuario_editando_la_cookie(self):
        """
        Es el ataque obvio: cambiar el 7 por un 1 y entrar como otro. La firma
        deja de cuadrar y la sesion no vale.
        """
        cookie = firmar_sesion(7)
        _, resto = cookie.split(".", 1)
        assert leer_sesion(f"1.{resto}") is None

    def test_no_se_puede_alargar_editando_la_fecha(self):
        cookie = firmar_sesion(7)
        user, _, firma = cookie.rsplit(".", 2)[0].split(".")[0], None, cookie.rsplit(".", 1)[1]
        assert leer_sesion(f"{user}.99999999999.{firma}") is None

    def test_una_sesion_caducada_no_vale(self):
        assert leer_sesion(firmar_sesion(7, duracion=-10)) is None

    @pytest.mark.parametrize("basura", [None, "", "cualquier.cosa", "1.2", "a.b.c"])
    def test_lo_que_no_es_una_sesion_no_lo_es(self, basura):
        assert leer_sesion(basura) is None


class TestRoles:
    def test_el_operario_no_entra_al_panel(self):
        """Consulta y reporta; no gestiona documentos ajenos."""
        assert not puede_gestionar(Rol.OPERARIO)
        assert not puede_administrar(Rol.OPERARIO)

    def test_calidad_gestiona_pero_no_administra(self):
        assert puede_gestionar(Rol.CALIDAD)
        assert not puede_administrar(Rol.CALIDAD)

    def test_el_administrador_puede_todo(self):
        assert puede_gestionar(Rol.ADMIN)
        assert puede_administrar(Rol.ADMIN)

    def test_un_rol_inventado_no_abre_nada(self):
        """Si alguien escribe 'superadmin' en la base, no hereda permisos."""
        assert not puede_gestionar("superadmin")
        assert not puede_administrar("root")


class TestElClienteNoSaleDeLaURL:
    """
    El identificador de cliente no es un secreto: aparece en la configuracion,
    en los scripts y en cualquier captura de pantalla. Durante un tiempo fue la
    UNICA puerta del panel, que lo leia del parametro `tenant_id`.

    Medido: con una sesion de `demo-publica`, pedir
    `/api/admin/documents?tenant_id=empresa-demo-iso` devolvia los 133
    documentos del cliente real.
    """

    def _usuario(self, tenant):
        class U:
            tenant_id = tenant
        return U()

    def test_con_sesion_manda_el_cliente_del_usuario(self):
        from app.channels.admin_adapter import tenant_de
        yo = self._usuario("demo-publica")
        assert tenant_de(yo, "empresa-demo-iso") == "demo-publica"
        assert tenant_de(yo, "") == "demo-publica"

    def test_sin_sesion_solo_se_llega_con_la_clave_de_servicio(self):
        """
        Un script de carga no tiene cuenta y tiene que poder decir sobre que
        cliente trabaja. Ahi el parametro sigue valiendo, porque para llegar
        hace falta la clave.
        """
        from app.channels.admin_adapter import tenant_de
        assert tenant_de(None, "empresa-demo-iso") == "empresa-demo-iso"

    def test_las_clausulas_citables_son_las_del_cliente(self):
        """
        Ofrecerle a alguien de un cliente el catalogo de otro seria darle
        clausulas que no son suyas, y una pieza respaldada por un documento
        ajeno no respalda nada.
        """
        from app.channels.imagen_adapter import tenant_visible
        assert tenant_visible(self._usuario("empresa-demo-iso")) == "empresa-demo-iso"

    def test_sin_sesion_las_clausulas_son_las_publicas(self, monkeypatch):
        from app.config import settings as cfg

        from app.channels.imagen_adapter import tenant_visible

        monkeypatch.setattr(cfg, "public_tenant_id", "demo-publica")
        assert tenant_visible(None) == "demo-publica"
