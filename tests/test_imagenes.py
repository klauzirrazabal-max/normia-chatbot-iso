"""
Material visual: que se puede pedir, y contra que documentos se valida.

Dos guardrails, y los dos son obligatorios: la descripcion tiene que hablar
del entorno de trabajo, y la clausula que cita tiene que existir de verdad en
la base. Un cartel sin respaldo es propaganda.
"""

from __future__ import annotations

import pytest

from app.channels.imagen_adapter import _fuera_de_alcance, _palabras, pide_texto


class TestVocabularioEnDosIdiomas:
    """
    Faltaba el castellano entero.

    La pagina del generador pide la escena en ingles a proposito -- el modelo
    de difusion rinde mejor asi -- pero por el CHAT la gente escribe en
    castellano y el modelo redacta la descripcion en castellano. Medido antes
    del arreglo: de cuatro peticiones legitimas solo paso la que por casualidad
    llevaba la palabra "pallets".
    """

    @pytest.mark.parametrize("descripcion", [
        "señalética de seguridad para el área de carga",
        "operario de almacén con casco y chaleco reflectivo junto a pallets apilados",
        "trabajador usando arnés en altura sobre una plataforma",
        "operario revisando un extintor en el pasillo del almacén",
        "inspección de EPP antes de iniciar la jornada",
        "dos trabajadores revisando carpetas de documentos controlados en una oficina",
    ])
    def test_material_del_sistema_pasa(self, descripcion):
        assert _fuera_de_alcance(descripcion) is None

    @pytest.mark.parametrize("descripcion", [
        "warehouse worker in hard hat beside stacked pallets",
        "two employees reviewing quality documents in an office",
    ])
    def test_el_ingles_sigue_pasando(self, descripcion):
        assert _fuera_de_alcance(descripcion) is None


class TestLoQueNoEsDelSistema:
    @pytest.mark.parametrize("descripcion", [
        "una invitación para la fiesta de cumpleaños de mi hijo",
        "un paisaje al atardecer con una playa",
        "mi mascota disfrazada de superhéroe",
        "un retrato de un político famoso",
    ])
    def test_se_rechaza(self, descripcion):
        assert _fuera_de_alcance(descripcion) is not None

    def test_una_palabra_permitida_no_salva_la_peticion(self):
        """
        Al abrir el vocabulario al castellano, la lista de lo prohibido pasa a
        sostener el rechazo: antes bastaba con no reconocer ninguna palabra.
        "Oficina" esta permitida, y aun asi esto no es material del SGC.
        """
        assert _fuera_de_alcance("una invitación para la fiesta de la oficina") is not None


class TestAcentos:
    """
    `[a-z]+` sobre el texto crudo partia "señalética" en "se" y "tica", y
    "almacén" en "almac" y "n". Ninguna coincidia con nada, asi que la peticion
    se rechazaba por estar fuera de alcance.
    """

    def test_las_palabras_sobreviven_a_los_acentos(self):
        palabras = _palabras("señalética del almacén con protección")
        assert "senaletica" in palabras
        assert "almacen" in palabras
        assert "proteccion" in palabras


class TestTextoEnLaImagen:
    """El modelo de difusion escribe letras ilegibles: el titular va aparte."""

    def test_se_rechaza_pedir_un_panel_con_texto(self):
        assert pide_texto("an infographic poster with text explaining the rules")

    def test_una_escena_no_pide_texto(self):
        assert not pide_texto("operario de almacén con casco junto a pallets")
