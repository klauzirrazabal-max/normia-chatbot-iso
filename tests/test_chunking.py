"""
Tests del chunking por clausula.

Si esto se rompe, el bot deja de poder citar "seccion 5.2" y el proyecto pierde
su trazabilidad. Es el punto mas fragil del pipeline porque depende de un regex
contra texto extraido de PDF.
"""

from app.core.rag.ingestion import (
    MAX_CHUNK_CHARS,
    SECTION_HEADER_RE,
    _is_header_table,
    chunk_by_section,
    clause_announces_without_delivering,
    looks_like_table_of_contents,
    render_table,
    strip_page_boilerplate,
)

TEXTO_ISO = """MANUAL DE CALIDAD

4 Contexto de la organizacion
La organizacion determina las cuestiones externas e internas que son pertinentes
para su proposito y su direccion estrategica.

4.1 Comprension de la organizacion
Se realiza el seguimiento y la revision de la informacion sobre estas cuestiones
externas e internas de forma anual.

5.2 Calibracion de instrumentos
Todo instrumento de medicion debe calibrarse cada doce meses contra un patron
trazable a un laboratorio acreditado. El registro se conserva por tres anos.
"""


def test_detecta_encabezados_numerados():
    assert SECTION_HEADER_RE.match("5.2 Calibracion de instrumentos")
    assert SECTION_HEADER_RE.match("4 Contexto de la organizacion")
    assert SECTION_HEADER_RE.match("7.1.5.2 Trazabilidad de las mediciones")


def test_ignora_lineas_que_no_son_encabezados():
    assert not SECTION_HEADER_RE.match("El instrumento 5.2 debe revisarse")
    assert not SECTION_HEADER_RE.match("todo en minusculas 5.2 algo")
    assert not SECTION_HEADER_RE.match("")


def test_chunk_por_seccion_asigna_la_clausula_correcta():
    chunks = chunk_by_section(TEXTO_ISO)
    secciones = [s for s, _ in chunks]

    assert "5.2" in secciones
    assert "4.1" in secciones

    contenido_52 = next(c for s, c in chunks if s == "5.2")
    assert "doce meses" in contenido_52
    # No debe arrastrar contenido de otra clausula.
    assert "cuestiones externas" not in contenido_52


def test_acentos_en_encabezados():
    texto = (
        "3.1 Álcance del sistema\n"
        "El alcance cubre todas las areas productivas de la planta principal."
    )
    chunks = chunk_by_section(texto, min_chars=10)
    assert chunks[0][0] == "3.1"


def test_documento_sin_estructura_numerada_no_se_pierde():
    """Un documento sin clausulas numeradas se conserva entero, sin seccion."""
    texto = (
        "Politica de calidad de la organizacion y su compromiso con la mejora continua.\n\n"
        "Segundo parrafo con contenido suficiente para superar el minimo de caracteres."
    )
    chunks = chunk_by_section(texto)

    assert chunks, "el documento no puede perderse"
    assert all(section is None for section, _ in chunks)
    recuperado = " ".join(c for _, c in chunks)
    assert "mejora continua" in recuperado
    assert "Segundo parrafo" in recuperado


def test_fallback_por_parrafos_cuando_nada_supera_el_minimo():
    """
    Si ningun bloque llega a min_chars, se cae al fallback por parrafos en vez
    de devolver vacio y perder el documento.
    """
    texto = "Parrafo A con texto.\n\nParrafo B con texto.\n\nParrafo C con texto."
    chunks = chunk_by_section(texto, min_chars=200)
    assert chunks == []  # nada supera 200 chars: no hay nada util que indexar

    chunks = chunk_by_section(texto, min_chars=15)
    assert chunks and all(section is None for section, _ in chunks)


def test_descarta_fragmentos_demasiado_cortos():
    chunks = chunk_by_section("1.1 A\nok\n", min_chars=80)
    assert chunks == [] or all(len(c) >= 80 for _, c in chunks)


def test_parte_clausulas_gigantes_conservando_la_seccion():
    parrafo = "Contenido extenso de la clausula. " * 40
    texto = "6.1 Clausula muy larga\n" + "\n\n".join([parrafo] * 6)

    chunks = chunk_by_section(texto)

    assert len(chunks) > 1, "una clausula enorme debe partirse"
    assert all(section == "6.1" for section, _ in chunks), "todos los trozos conservan la seccion"
    assert all(len(content) <= MAX_CHUNK_CHARS * 1.5 for _, content in chunks)


# --- Casos encontrados en documentacion ISO real (un SGC en produccion) ---

PROCEDIMIENTO_REAL = """1. OBJETIVO
Establecer la metodologia para el tratamiento de las no conformidades detectadas
en el sistema de gestion de la calidad de la organizacion.

2. ALCANCE
Aplica a todos los procesos del sistema de gestion de la calidad.

7.3. ANALISIS DE CAUSAS
Se debe identificar la causa raiz utilizando las herramientas definidas.

7.3.1. Recopilar informacion relacionada con la No Conformidad
Utilizando todas las fuentes disponibles y los registros asociados al hallazgo.
"""


def test_detecta_numeracion_con_punto():
    """
    Los procedimientos internos numeran "1. OBJETIVO" y "7.3. ANALISIS", con punto
    tras el numero. El regex original exigia espacio inmediatamente despues del
    numero, asi que NINGUNA clausula se detectaba y el documento entero caia al
    fallback como un solo bloque: 132 documentos produjeron apenas 228 chunks.
    """
    assert SECTION_HEADER_RE.match("1. OBJETIVO")
    assert SECTION_HEADER_RE.match("7.3. ANALISIS DE CAUSAS")
    assert SECTION_HEADER_RE.match("7.3.1. Recopilar informacion relacionada")


def test_sigue_detectando_numeracion_sin_punto():
    """El estilo de la norma ISO no lleva punto. Ambos deben funcionar."""
    assert SECTION_HEADER_RE.match("5.2 Calibracion de instrumentos")


def test_procedimiento_real_produce_una_clausula_por_seccion():
    chunks = chunk_by_section(PROCEDIMIENTO_REAL, min_chars=40)
    secciones = [s for s, _ in chunks]

    assert "1" in secciones
    assert "2" in secciones
    assert "7.3" in secciones
    assert "7.3.1" in secciones
    assert len(chunks) >= 4, "cada clausula debe ser su propio chunk"


def test_quita_membrete_y_pie_repetidos():
    """
    Cada pagina de un documento controlado repite el encabezado con codigo y
    version, y el aviso de confidencialidad al pie. Repetido en cada chunk,
    domina el embedding y hace que todos los documentos se parezcan entre si.
    """
    membrete = "CODIGO : CAL-PR-03"
    pie = "Esta prohibida la reproduccion total o parcial del presente documento"
    paginas = [
        f"{membrete}\n1. OBJETIVO\nEstablecer la metodologia aplicable.\n{pie}",
        f"{membrete}\n2. ALCANCE\nAplica a todos los procesos.\n{pie}",
        f"{membrete}\n3. REFERENCIAS\nNorma ISO 9001:2015.\n{pie}",
        f"{membrete}\n4. RESPONSABILIDAD\nDel Responsable de Calidad.\n{pie}",
    ]

    texto = strip_page_boilerplate(paginas)

    assert membrete not in texto
    assert pie not in texto
    assert "1. OBJETIVO" in texto
    assert "4. RESPONSABILIDAD" in texto


def test_no_quita_nada_en_documentos_de_una_o_dos_paginas():
    """Con pocas paginas no hay evidencia suficiente para llamar algo membrete."""
    paginas = ["CODIGO : X\nContenido A", "CODIGO : X\nContenido B"]
    texto = strip_page_boilerplate(paginas)
    assert "CODIGO : X" in texto


# --- Tablas: el fallo mas costoso encontrado en produccion ------------------

def test_render_table_conserva_la_fila_completa():
    """
    `extract_text()` aplana las tablas y rompe la relacion celda-columna. El
    cuadro de SLA de STI-PR-01 salia asi:

        Segun acuerdo con
        REQUERIMIENTO: 1 IMAC De acuerdo a disponibilidad de proveedor
        solicitante

    El valor de la columna "tiempo de respuesta" quedaba partido y separado de
    su fila, y el asistente respondia el valor de la OTRA columna. En un
    contexto ISO eso importa: SLA, matrices de responsabilidad y plazos de CAPA
    viven en tablas.
    """
    disponibilidad = "De acuerdo a disponibilidad"
    filas = [
        ["TIPO DE SOLICITUD", None, "TIEMPO MÁXIMO\nDE RESPUESTA", None, "TIEMPO MÁXIMO"],
        ["REQUERIMIENTO: 1 IMAC", "Según acuerdo con solicitante", None, disponibilidad, None],
        ["REQUERIMIENTO: Más de 1 IMAC", "NBD", None, disponibilidad, None],
    ]

    texto = render_table(filas)
    lineas = texto.splitlines()

    assert lineas[1] == (
        "REQUERIMIENTO: 1 IMAC | Según acuerdo con solicitante | De acuerdo a disponibilidad"
    )
    assert lineas[2] == "REQUERIMIENTO: Más de 1 IMAC | NBD | De acuerdo a disponibilidad"


def test_render_table_colapsa_columnas_vacias_de_celdas_combinadas():
    filas = [["A", None, "", "B", None]]
    assert render_table(filas) == "A | B"


def test_render_table_descarta_filas_totalmente_vacias():
    filas = [["A", "B"], [None, ""], ["C", "D"]]
    assert render_table(filas).splitlines() == ["A | B", "C | D"]


def test_detecta_el_membrete_como_tabla_de_encabezado():
    """El membrete tambien se extrae como tabla; no debe entrar como contenido."""
    membrete = [
        [None, "ATENCIÓN DE SOLICITUDES TECNOLÓGICAS", "CÓDIGO", ":", "STI-PR-01"],
        [None, None, "VERSIÓN", ":", "04"],
    ]
    assert _is_header_table(membrete) is True


def test_una_tabla_de_contenido_no_se_confunde_con_el_membrete():
    sla = [
        ["TIPO DE SOLICITUD", "TIEMPO MÁXIMO DE RESPUESTA"],
        ["INCIDENCIA: Afecta a un sólo usuario", "01 hora"],
    ]
    assert _is_header_table(sla) is False


# --- Tabla de contenidos y clausulas ciegas ---------------------------------

INDICE_REAL = """2. ALCANCE DEL SISTEMA DE GESTIÓN DE LA CALIDAD ........................ 3
3. DOCUMENTOS DE REFERENCIA ........................................... 3
4. RESPONSABILIDADES .................................................. 4
5. ABREVIATURAS Y DEFINICIONES ........................................ 4"""


def test_detecta_la_tabla_de_contenidos():
    """
    El indice se colaba como contenido porque sus lineas empiezan con el numero
    de seccion y pasan el regex de encabezado. Resultado: DOS chunks con seccion
    "2" en el Manual de la Calidad -- uno con el alcance real y otro con puntos
    de relleno.

    Y el del indice ganaba la busqueda mas veces, porque repite los titulos de
    todas las secciones. Preguntando "que es el sistema de calidad" el modelo
    recibio el indice y respondio, con razon, que no tenia el texto de la
    seccion 2 -- mientras la cita al pie afirmaba que si.
    """
    assert looks_like_table_of_contents(INDICE_REAL) is True


def test_una_clausula_normal_no_se_confunde_con_el_indice():
    contenido = (
        "2. ALCANCE DEL SISTEMA DE GESTIÓN DE LA CALIDAD\n"
        "El alcance cubre todas las areas productivas y los servicios de inventario."
    )
    assert looks_like_table_of_contents(contenido) is False


def test_una_sola_linea_con_puntos_no_es_un_indice():
    """Un punto suspensivo aislado en prosa no debe descartar la clausula."""
    assert looks_like_table_of_contents("7.1. El proceso continua... hasta el cierre.") is False


def test_el_indice_no_llega_a_los_chunks():
    texto = INDICE_REAL + "\n\n1. OBJETIVO\nDescribir el sistema de gestion de la calidad."
    secciones = [s for s, _ in chunk_by_section(texto, min_chars=20)]

    assert "1" in secciones
    assert secciones.count("2") == 0, "la entrada de indice no debe entrar como seccion 2"


def test_detecta_una_clausula_que_anuncia_sin_entregar():
    """
    El texto real esta en una imagen: la seccion 2 del Manual dice "El Alcance
    ... es:" y ahi se corta. No es un fallo de la ingesta -- el PDF no lo tiene
    -- pero hay que avisarlo.
    """
    truncada = (
        "2. ALCANCE DEL SISTEMA DE GESTIÓN DE LA CALIDAD "
        "El Alcance del Sistema de Gestión de la Calidad de la organizacion es:"
    )
    assert clause_announces_without_delivering(truncada) is True


def test_una_clausula_con_contenido_no_se_marca():
    completa = (
        "6.1. Soporte nivel 1: Se guia al usuario via telefonica o acceso remoto "
        "para atender la solicitud reportada en el formulario."
    )
    assert clause_announces_without_delivering(completa) is False


def test_un_bloque_largo_que_acaba_en_dos_puntos_no_se_marca():
    """Una clausula extensa que introduce una lista si tiene contenido."""
    largo = "8. COMPROMISO GERENCIAL " + ("Estamos comprometidos con el SGC. " * 12) + ":"
    assert clause_announces_without_delivering(largo) is False


def test_el_membrete_pequeno_si_se_descarta():
    membrete = [
        [None, "ATENCIÓN DE SOLICITUDES TECNOLÓGICAS", "CÓDIGO", ":", "STI-PR-01"],
        [None, None, "VERSIÓN", ":", "04"],
        [None, None, "REVISIÓN", ":", "08/07/2025"],
        [None, None, "PÁGINA", ":", "1 de 4"],
    ]
    assert _is_header_table(membrete) is True


def test_una_tabla_grande_con_codigo_en_el_encabezado_NO_es_membrete():
    """
    La regresion mas costosa de la sesion: un FORMATO es una sola tabla grande
    cuyo encabezado tambien dice CODIGO/VERSION. Sin exigir que el membrete sea
    pequeno, se descartaba el documento entero -- 47 de 132 documentos, casi
    todos formatos, quedaron sin una sola linea indexada.

    Y fallaba en silencio: se reportaban "132 documentos ingestados" porque cada
    documento se creaba como fila, aunque sin chunks.
    """
    lista_maestra = [
        ["Item", "Proceso", "Nombre del Documento", "CÓDIGO", "VERSIÓN"],
        ["1", "Organigrama", "Organigrama General", "ADM-OR-01", "6"],
        ["2", "Organigrama", "Organigrama Funcional", "ADM-OR-02", "7"],
        ["3", "Gestión Gerencial", "Gestión de Riesgos", "GGR-PR-01", "2"],
        ["4", "Gestión Gerencial", "Partes Interesadas", "GGR-FO-01", "1"],
        ["5", "Calidad", "Manual de la Calidad", "CAL-MN-01", "3"],
        ["6", "Calidad", "Control de Información", "CAL-PR-01", "2"],
        ["7", "Calidad", "Auditorías Internas", "CAL-PR-02", "2"],
    ]
    assert _is_header_table(lista_maestra) is False, (
        "una tabla de 8 filas es el contenido del documento, no su membrete"
    )
