"""
Tests de la busqueda en el catalogo.

Dos bugs reales encontrados probando con un SGC en produccion:

1. Confundir TEMA con TIPO. "politicas de TI" nombra los dos, pero la busqueda
   hacia OR de todo y devolvia la Politica de la Calidad (es politica, no es de
   TI) y un Acuse de Recepcion (menciona politicas, es un formato).

2. Normalizacion Unicode. Los titulos llegaron de nombres de archivo de macOS,
   que usa NFD ("i" + acento combinante); los literales del codigo son NFC. Son
   bytes distintos, asi que buscar "auditoria" devolvia CERO documentos aunque
   existan cinco. El bot habria dicho "no tengo esa informacion" sobre
   documentos que si existen -- el fallo que el sistema entero debe evitar.
"""

import unicodedata

from app.core.agents.tools import (
    TOOL_NAMES,
    TOOLS_SCHEMA,
    _clasificar_terminos,
    execute_tool,
    sin_acentos,
)


class TestBusquedaSinAcentos:
    """
    La busqueda debe ser insensible a acentos en LAS DOS direcciones, y las dos
    fallaban:

      * titulo con acento en NFD (viene de nombres de archivo de macOS) contra
        termino en NFC -> "auditoria" no encontraba "Auditorias"
      * titulo con acento contra termino escrito sin acento (lo normal) ->
        "atencion de solicitudes" no encontraba "Atencion de Solicitudes"
    """

    def test_quita_diacriticos_y_baja_a_minusculas(self):
        assert sin_acentos("Auditorías Internas") == "auditorias internas"
        assert sin_acentos("Atención de Solicitudes Tecnológicas") == (
            "atencion de solicitudes tecnologicas"
        )

    def test_normaliza_nfd_y_nfc_al_mismo_resultado(self):
        nfc = unicodedata.normalize("NFC", "Política")
        nfd = unicodedata.normalize("NFD", "Política")
        assert nfc != nfd, "las dos formas son bytes distintos"
        assert sin_acentos(nfc) == sin_acentos(nfd) == "politica"

    def test_un_termino_sin_acento_coincide_con_un_titulo_acentuado(self):
        titulo = sin_acentos(unicodedata.normalize("NFD", "Atención de Solicitudes"))
        assert sin_acentos("atencion de solicitudes") in titulo

    def test_texto_sin_acentos_no_cambia(self):
        assert sin_acentos("compras") == "compras"

    def test_vacio_no_rompe(self):
        assert sin_acentos("") == ""
        assert sin_acentos(None) == ""


class TestClasificacionDeTerminos:
    def test_separa_tipo_de_tema(self):
        tipos, temas, libres = _clasificar_terminos("politicas de TI")
        assert tipos == ["-PO-"]
        assert temas and temas[0][0] == "STI"
        assert not libres

    def test_ignora_palabras_de_relleno(self):
        _, _, libres = _clasificar_terminos("que documentos hay sobre las compras")
        assert "que" not in libres
        assert "sobre" not in libres

    def test_solo_tipo(self):
        tipos, temas, _ = _clasificar_terminos("manuales")
        assert tipos == ["-MN-"]
        assert not temas

    def test_un_asunto_no_se_mapea_a_un_area_completa(self):
        """
        "auditoria" es un asunto DENTRO de Calidad, no un area. Mapearlo al
        prefijo CAL devolvia los 25 documentos del area entera.
        """
        _, temas, _ = _clasificar_terminos("auditoria")
        assert temas
        prefijo, palabras = temas[0]
        assert prefijo is None, "un asunto no debe arrastrar toda su area"
        assert palabras

    def test_un_area_real_si_lleva_prefijo(self):
        _, temas, _ = _clasificar_terminos("compras")
        assert temas[0][0] == "COM"

    def test_termino_desconocido_queda_como_palabra_libre(self):
        _, temas, libres = _clasificar_terminos("radiologica")
        assert not temas
        assert libres == ["radiologica"]


class TestSinonimosDemasiadoAmplios:
    """
    Un sinonimo mal elegido contamina la respuesta sin que el modelo tenga culpa.

    Caso real: "TI" mapeaba a la palabra "informacion", que arrastraba
    "Control de Informacion Documentada" (CAL-PR-01, de Calidad) a la lista de
    procedimientos de TI. El modelo reporto fielmente el catalogo que le dimos;
    el error estaba en el catalogo.
    """

    def test_ti_no_matchea_la_palabra_informacion(self):
        _, temas, _ = _clasificar_terminos("TI")
        prefijo, palabras = temas[0]

        assert prefijo == "STI"
        assert not any("informacion" in p or "información" in p for p in palabras), (
            "'informacion' aparece en demasiados titulos de un SGC como para servir "
            "de sinonimo de TI; el prefijo STI y 'tecnologia' ya cubren el area"
        )

    def test_el_area_de_ti_sigue_siendo_alcanzable_por_tecnologia(self):
        """El area real es 'SOPORTE DE TECNOLOGIA DE LA INFORMACION'."""
        _, temas, _ = _clasificar_terminos("ti")
        _, palabras = temas[0]
        assert any("tecnolog" in p for p in palabras)


class TestLeerDocumento:
    """
    Resumir no es buscar.

    Caso real: "sobre atencion de solicitudes me puedes dar un resumen?" El RAG
    trajo 4 fragmentos de un documento de 20 clausulas -- y dos de ellos eran de
    OTROS documentos, porque "atencion de solicitudes" se parece semanticamente
    a "atencion de reclamos". Con 2 de 20 clausulas el modelo dijo, con razon,
    que no podia resumir y escalo a Calidad: la respuesta fue honesta, el
    sistema fue incapaz.

    Un resumen es una operacion a nivel de DOCUMENTO. `leer_documento` lo lee
    entero, en orden.
    """

    def test_documento_vacio_es_rechazado(self):
        resultado = execute_tool(
            "leer_documento", {"documento": "  "}, db=None, tenant_id="t", conversation_id=1
        )
        assert resultado["error"] == "invalid_arguments"

    def test_esta_declarada_en_el_esquema(self):
        assert "leer_documento" in TOOL_NAMES

    def test_su_descripcion_explica_por_que_existe(self):
        esquema = next(t for t in TOOLS_SCHEMA if t["function"]["name"] == "leer_documento")
        descripcion = esquema["function"]["description"].lower()
        assert "resumen" in descripcion
        assert "completo" in descripcion
        assert "documento" in esquema["function"]["parameters"]["properties"]


# --- El TIPO es un filtro, y perderlo cambia la respuesta ---
#
# "que politicas de TI tenemos?" devolvia tres documentos, y el tercero era un
# PROCEDIMIENTO. La herramienta acertaba: con 'politicas de TI' devuelve dos. El
# modelo llamaba con 'TI' a secas, porque los ejemplos de la propia descripcion
# eran palabras sueltas ('TI', 'politica') y le ensenaron a mandar solo una.


def test_el_tipo_filtra_y_el_tema_solo_no():
    from app.core.agents.tools import TOOLS_SCHEMA

    spec = next(t for t in TOOLS_SCHEMA if t["function"]["name"] == "buscar_documentos")
    desc = spec["function"]["parameters"]["properties"]["tema"]["description"]
    # La descripcion debe ensenar la forma COMBINADA, no palabras sueltas.
    assert "politicas de TI" in desc
    assert "TAL CUAL" in desc
