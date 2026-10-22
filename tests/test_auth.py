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
