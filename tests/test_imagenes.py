"""
Material visual: que se puede pedir, y contra que documentos se valida.

Dos guardrails, y los dos son obligatorios: la descripcion tiene que hablar
del entorno de trabajo, y la clausula que cita tiene que existir de verdad en
la base. Un cartel sin respaldo es propaganda.
"""

from __future__ import annotations

import pytest

from app.channels.imagen_adapter import _palabras, _rechazo_inapelable, pide_texto


class TestElSuelo:
    """
    Lo que no se genera diga lo que diga el texto.

    El CRITERIO lo pone el modelo, que lee la frase entera y entiende el
    contexto: la lista de palabras permitidas que habia aqui rechazaba "que se
    vea real, no dibujo" por la palabra "dibujo", cuando pedia lo contrario, y
    solo funcionaba en el idioma en que estuviera escrita.

    Pero un guardrail que se puede convencer con palabras no es un guardrail.
    Esto es el suelo: no se le pregunta a nadie y no admite negaciones, porque
    "sin armas" sigue hablando de armas y aqui no se corren riesgos.
    """

    @pytest.mark.parametrize("texto", [
        "un arma apoyada en una estanteria del almacen",
        "una persona desnuda en la oficina",
        "un nino jugando en el muelle de carga",
        "sangre en el suelo del almacen",
    ])
    def test_no_se_genera_nunca(self, texto):
        assert _rechazo_inapelable(texto) is not None

    def test_ni_siquiera_negandolo(self):
        """
        A diferencia del juicio del modelo, el suelo NO atiende negaciones: una
        descripcion que necesita nombrar un arma para excluirla sigue siendo
        una descripcion sobre armas, y el coste de equivocarse por exceso aqui
        es que alguien reescriba una frase.
        """
        assert _rechazo_inapelable("un almacen sin armas") is not None

    @pytest.mark.parametrize("texto", [
        "Un operario de almacén con casco y chaleco reflectivo",
        "warehouse worker in hard hat beside stacked pallets",
        "una invitación para la fiesta de cumpleaños",
    ])
    def test_lo_demas_lo_juzga_el_modelo(self, texto):
        """
        Incluso lo que claramente no es del sistema pasa el suelo: rechazarlo
        es trabajo del juez, que sabe DECIR POR QUE. El suelo solo corta lo que
        no se discute.
        """
        assert _rechazo_inapelable(texto) is None


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


class TestElJuez:
    """
    El criterio lo pone el modelo. Estas pruebas fijan el CONTRATO de la
    funcion -- que devuelve, y que hace cuando el modelo falla -- sin depender
    de que haya un modelo levantado.
    """

    def test_si_el_modelo_no_contesta_se_deja_pasar(self, monkeypatch):
        """
        Quedarse sin generar porque el juez no contesto seria cambiar
        disponibilidad por una certeza que no se tiene. El suelo y el resto de
        guardrails siguen puestos.
        """
        import app.services.llm_client as cliente
        from app.channels.imagen_adapter import juzgar_alcance

        def revienta():
            raise RuntimeError("modelo caido")

        monkeypatch.setattr(cliente, "get_llm_client", revienta)
        vale, motivo, escena = juzgar_alcance("lo que sea")
        assert vale is True
        assert escena == ""

    def test_una_respuesta_ilegible_tambien_deja_pasar(self, monkeypatch):
        import app.services.llm_client as cliente
        from app.channels.imagen_adapter import juzgar_alcance

        class Divaga:
            def generate(self, *a, **k):
                return {"content": "Pues mira, depende de como lo veas."}

        monkeypatch.setattr(cliente, "get_llm_client", lambda: Divaga())
        assert juzgar_alcance("lo que sea")[0] is True

    def test_el_veredicto_y_la_escena_vienen_juntos(self, monkeypatch):
        """Una sola llamada: juzgar no cuesta mas que traducir."""
        import app.services.llm_client as cliente
        from app.channels.imagen_adapter import juzgar_alcance

        class Juez:
            def generate(self, *a, **k):
                return {
                    "content":
                        '{"vale": false, "motivo": "Es un evento personal.",'
                        ' "escena_en": ""}'
                }

        monkeypatch.setattr(cliente, "get_llm_client", lambda: Juez())
        vale, motivo, _ = juzgar_alcance("la fiesta de mi hijo")
        assert vale is False
        assert "evento personal" in motivo


# --- Presupuesto de tokens de CLIP ------------------------------------------
#
# CLIP corta en 77 tokens SIN AVISAR: no lanza error, no deja traza, solo
# ignora el resto. El negativo llego a tener 135 y el generador descartaba 58,
# entre ellos "poorly drawn face", "poorly drawn hands" y "cluttered" -- los
# terminos que mas pesaban contra las deformidades que se veian en las piezas.
# Estuvieron meses escritos en el fichero sin efecto ninguno.
#
# Un limite que se incumple sin sintoma solo se nota midiendolo.

LIMITE_CLIP = 77


def _tokens(texto: str) -> int:
    transformers = pytest.importorskip("transformers")
    tk = transformers.CLIPTokenizer.from_pretrained("openai/clip-vit-large-patch14")
    return len(tk(texto)["input_ids"])


def test_negativo_entra_entero_en_clip():
    from scripts.generar_imagenes import NEGATIVO

    assert _tokens(NEGATIVO) <= LIMITE_CLIP


def test_la_marca_deja_sitio_a_la_escena():
    """
    La escena va PRIMERO y el estilo detras, asi que lo que se pierde al cortar
    es el estilo. La marca sola tiene que dejar holgura para una escena normal:
    si se la come entera, "sharp focus on the face" no llega y vuelven las caras
    inventadas.
    """
    from scripts.generar_imagenes import MARCA

    escena = "worker inspecting safety harness stitching before working at height"
    assert _tokens(f"{escena}, {MARCA}") <= LIMITE_CLIP
