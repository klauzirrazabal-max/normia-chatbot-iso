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


class TestIdiomaDeLaEscena:
    """
    El usuario escribe en castellano; el modelo de difusion entiende ingles.

    La pagina pedia la escena en ingles, y eso convertia la herramienta en algo
    para quien sabe ingles. Quien la usa es un jefe de almacen en Lima. Traduce
    el sistema, que para eso tiene un modelo de lenguaje corriendo al lado.
    """

    @pytest.mark.parametrize("texto", [
        "Un operario de almacén con casco y chaleco reflectivo",
        "operario de almacen con casco y chaleco junto a pallets",
        "señalética de seguridad en el área de carga",
        "dos trabajadores revisando documentos en una oficina",
    ])
    def test_reconoce_el_castellano(self, texto):
        from app.channels.imagen_adapter import _parece_castellano
        assert _parece_castellano(texto)

    @pytest.mark.parametrize("texto", [
        "Warehouse worker in hard hat beside stacked pallets",
        "two employees reviewing quality documents in an office",
        "forklift operator wearing a safety vest",
    ])
    def test_no_confunde_el_ingles(self, texto):
        """
        Traducir ingles a ingles gasta una llamada al modelo y puede estropear
        un prompt que ya estaba bien.
        """
        from app.channels.imagen_adapter import _parece_castellano
        assert not _parece_castellano(texto)

    def test_una_sola_coincidencia_no_basta(self):
        """
        "a" y "o" existen en los dos idiomas. Con una sola palabra funcional se
        traduciria ingles al ingles sin motivo.
        """
        from app.channels.imagen_adapter import _parece_castellano
        assert not _parece_castellano("a worker in a warehouse")

    def test_si_la_traduccion_falla_se_usa_el_original(self, monkeypatch):
        """
        Una pieza peor es mejor que ninguna pieza. El usuario no tiene por que
        enterarse de que hay un paso intermedio.
        """
        import app.services.llm_client as cliente
        from app.channels.imagen_adapter import _a_ingles

        def revienta():
            raise RuntimeError("modelo caido")

        monkeypatch.setattr(cliente, "get_llm_client", revienta)
        original = "Un operario de almacén con casco y chaleco"
        assert _a_ingles(original) == original

    def test_se_descarta_una_traduccion_desbocada(self, monkeypatch):
        """Si el modelo divaga en vez de traducir, no se usa su respuesta."""
        import app.services.llm_client as cliente
        from app.channels.imagen_adapter import _a_ingles

        class Divaga:
            def generate(self, *a, **k):
                return {"content": "Claro, aqui tienes la traduccion. " + "bla " * 200}

        monkeypatch.setattr(cliente, "get_llm_client", lambda: Divaga())
        original = "Un operario de almacén con casco"
        assert _a_ingles(original) == original
