"""
Lectura del membrete de un documento controlado.

Todo documento de un SGC lleva su identidad impresa en el encabezado de la
primera pagina:

    CODIGO  : CAL-PR-03
    VERSION : 02
    REVISION: 08/04/2025

Esa es la fuente autoritativa de la identidad del documento -- mas confiable
que el nombre del archivo, que lo escribe una persona y por eso se equivoca.
En el SGC real que motivo este modulo, un archivo llamado "COM-FO-06 ..." era
en realidad CMC-FO-06 segun la lista maestra: un error de codificacion que
nadie noto hasta cruzar los datos.

Comparar lo que dice el nombre contra lo que dice el documento es lo que
permite avisarle al usuario en el momento de subirlo, en vez de descubrirlo
en una auditoria.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pdfplumber

# "CODIGO : CAL-PR-03", "CÓDIGO: CAL-PR-03", "Codigo CAL-PR-03"
CODE_RE = re.compile(
    r"C[OÓ]D(?:IGO)?\s*[:.]?\s*([A-Z]{2,4}-[A-Z]{2}-\d{1,3})", re.IGNORECASE
)
# "VERSION : 02", "VERSIÓN: 2"
VERSION_RE = re.compile(r"VERSI[OÓ]N\s*[:.]?\s*(\d{1,3})", re.IGNORECASE)
# "REVISION : 08/04/2025"
REVISION_RE = re.compile(r"REVISI[OÓ]N\s*[:.]?\s*(\d{2}/\d{2}/\d{4})", re.IGNORECASE)

# Codigo al inicio del nombre de archivo: "COM-PR-01 Compras.pdf"
FILENAME_CODE_RE = re.compile(r"^([A-Z]{2,4}-[A-Z]{2}-\d{1,3})\b")

HEADER_PAGES = 1  # el membrete vive en la primera pagina

# El bloque de aprobacion vive al FINAL, en la ultima pagina:
#
#     ELABORADO POR:          REVISADO POR:           APROBADO POR:
#     COORDINADOR DE CALIDAD  COORDINADOR DE CALIDAD  GERENTE GENERAL
#
# Son tres columnas, y `extract_text` las devuelve fundidas en dos lineas. Para
# saber QUIEN aprobo hay que mirar las coordenadas: las palabras de la fila de
# abajo que caen bajo la columna de "APROBADO".
APROBADO_RE = re.compile(r"APROBADO\s+POR", re.IGNORECASE)
ELABORADO_RE = re.compile(r"ELABORADO\s+POR", re.IGNORECASE)
REVISADO_RE = re.compile(r"REVISADO\s+POR", re.IGNORECASE)


@dataclass(frozen=True)
class DocumentHeader:
    """Identidad que el propio documento declara en su membrete."""

    code: str | None = None
    version: str | None = None
    revision_date: date | None = None
    title_guess: str | None = None

    @property
    def is_complete(self) -> bool:
        return self.code is not None and self.version is not None


@dataclass(frozen=True)
class ApprovalBlock:
    """
    Quien elaboro, reviso y aprobo el documento, segun el propio documento.

    La clausula 7.5.2 pide que la informacion documentada se revise y se apruebe
    antes de emitirse. En este SGC esa evidencia son cargos, no nombres ni
    firmas escaneadas, y viven en la ultima pagina.
    """

    elaborado_por: str | None = None
    revisado_por: str | None = None
    aprobado_por: str | None = None

    @property
    def tiene_aprobacion(self) -> bool:
        return bool(self.aprobado_por)


# El pie legal va debajo del bloque de firmas y ocupa todo el ancho, asi que
# cae dentro de las tres columnas. Sin descartarlo, un documento devolvia como
# aprobador "autorizacion expresa de la Gerencia General".
# El "Esta" del comienzo entra en el patron porque este regex tambien se usa
# para CORTAR una linea: sin el, "Aprobado por el Gerente General Esta
# prohibida la reproduccion..." dejaba el cargo como "Gerente General Esta".
_PIE_LEGAL_RE = re.compile(
    r"(?:Est[áa]\s+)?prohibid|reproducci|autorizaci[oó]n expresa", re.IGNORECASE
)

# Un cargo largo se parte en varias lineas: "JEFE DE CONTROL Y TOMA DE" /
# "DECISIONES". Leyendo solo la primera salian fragmentos sueltos, y con dos
# seguian saliendo cargos cortados por la mitad.
_LINEAS_DE_CARGO = 3


def _lineas(palabras: list[dict], tolerancia: float = 4.0) -> list[list[dict]]:
    """Agrupa palabras en lineas por su coordenada vertical."""
    filas: list[list[dict]] = []
    for w in sorted(palabras, key=lambda w: (w["top"], w["x0"])):
        if filas and abs(w["top"] - filas[-1][0]["top"]) < tolerancia:
            filas[-1].append(w)
        else:
            filas.append([w])
    return filas


def _columna(palabras: list[dict], desde_x: float, hasta_x: float, fila_y: float) -> str:
    """
    El cargo que hay bajo una de las tres etiquetas.

    Se toman hasta dos lineas porque los cargos largos se parten, y se corta en
    cuanto aparece el pie legal, que ocupa el ancho entero y por eso cae dentro
    de las tres columnas por igual.
    """
    debajo = [w for w in palabras if w["top"] > fila_y + 1]
    trozos: list[str] = []
    for fila in _lineas(debajo):
        texto_fila = " ".join(w["text"] for w in fila)
        if _PIE_LEGAL_RE.search(texto_fila):
            break
        # Se clasifica por el CENTRO de cada palabra: una palabra larga a
        # caballo de la frontera pertenece a la columna donde esta su masa.
        en_columna = [
            w for w in fila
            if desde_x <= (float(w["x0"]) + float(w["x1"])) / 2 < hasta_x
        ]
        if not en_columna:
            # Una fila vacia en ESTA columna no cierra el cargo: las tres
            # columnas tienen alturas distintas, asi que "JEFE DE CONTROL Y
            # TOMA DE" e "INVENTARIOS" acaban separadas por la segunda linea
            # de la columna del medio. Quien cierra es el pie legal.
            continue
        trozos.append(" ".join(w["text"] for w in sorted(en_columna, key=lambda w: w["x0"])))
        if len(trozos) >= _LINEAS_DE_CARGO:
            break
    return " ".join(trozos).strip()


def parse_approval_words(palabras: list[dict], ancho: float) -> ApprovalBlock:
    """
    Arma el bloque a partir de las palabras con coordenadas de la ultima pagina.

    Se trabaja con coordenadas y no con el texto plano porque las tres columnas
    salen fundidas en una sola linea: "ELABORADO POR: REVISADO POR: APROBADO
    POR:" seguida de "COORDINADOR DE CALIDAD COORDINADOR DE CALIDAD GERENTE
    GENERAL". Partir eso por espacios no tiene solucion.
    """
    etiquetas: list[tuple[str, float, float]] = []
    for w in palabras:
        texto = w["text"].upper().strip(":")
        if texto in ("ELABORADO", "REVISADO", "APROBADO"):
            centro = (float(w["x0"]) + float(w["x1"])) / 2
            etiquetas.append((texto, centro, float(w["top"])))

    if not etiquetas:
        return ApprovalBlock()

    # La fila de etiquetas es la mas baja en la que aparecen: un documento puede
    # nombrar "aprobado" en su cuerpo y eso no es el bloque de firmas.
    fila_y = max(t for _, _, t in etiquetas)
    etiquetas = [e for e in etiquetas if abs(e[2] - fila_y) < 4]
    etiquetas.sort(key=lambda e: e[1])

    # Las fronteras son los PUNTOS MEDIOS entre etiquetas, no las etiquetas.
    # Los cargos van centrados debajo de cada una, asi que empiezan a su
    # izquierda: partiendo por la posicion de la etiqueta, "JEFE DE TECNOLOGIA
    # DE LA INFORMACION" se leia como "DE LA INFORMACION" y sus tres primeras
    # palabras se le atribuian a la columna anterior.
    fronteras = [0.0]
    for i in range(len(etiquetas) - 1):
        fronteras.append((etiquetas[i][1] + etiquetas[i + 1][1]) / 2)
    fronteras.append(ancho)

    valores: dict[str, str] = {}
    for i, (nombre, _, _) in enumerate(etiquetas):
        valores[nombre] = _columna(palabras, fronteras[i], fronteras[i + 1], fila_y)

    return ApprovalBlock(
        elaborado_por=valores.get("ELABORADO") or None,
        revisado_por=valores.get("REVISADO") or None,
        aprobado_por=valores.get("APROBADO") or None,
    )


# Segundo diseno del bloque, en linea y en minusculas, sobre lineas de firma:
#
#     _______________________     _________________________________
#     Elaborado por Gestión Humana   Aprobado por el Gerente General
#
# El de columnas no lo ve, porque aqui el cargo va DETRAS de la etiqueta y no
# debajo. Sin esta variante, un manual perfectamente aprobado salia marcado
# como sin aprobar, que es el aviso falso que hace que se dejen de leer.
_APROBADO_EN_LINEA_RE = re.compile(
    r"Aprobado\s+por\s+(?:el|la|los|las)?\s*([^\n_]{3,60})", re.IGNORECASE
)
_ELABORADO_EN_LINEA_RE = re.compile(
    r"Elaborado\s+por\s+(?:el|la|los|las)?\s*([^\n_]{3,60})", re.IGNORECASE
)


def _en_linea(patron: re.Pattern[str], texto: str) -> str | None:
    """
    El cargo que sigue a la etiqueta en la misma linea.

    Se corta en la siguiente etiqueta porque las dos columnas salen fundidas:
    "Elaborado por Gestión Humana Aprobado por el Gerente General".
    """
    match = patron.search(texto)
    if not match:
        return None
    valor = match.group(1).strip(" :.\t")
    valor = re.split(r"\s+(?:Elaborado|Revisado|Aprobado)\s+por\b", valor, flags=re.I)[0]
    valor = _PIE_LEGAL_RE.split(valor)[0]
    return valor.strip(" :.-") or None


def read_approval(pdf_path: Path) -> ApprovalBlock:
    """Lee el bloque de firmas de la ultima pagina. Nunca lanza."""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            if not pdf.pages:
                return ApprovalBlock()
            ultima = pdf.pages[-1]
            bloque = parse_approval_words(ultima.extract_words(), float(ultima.width))
            if bloque.tiene_aprobacion:
                return bloque
            texto = ultima.extract_text() or ""
    except Exception:  # noqa: BLE001 - un PDF corrupto no debe tumbar la carga
        return ApprovalBlock()

    aprobado = _en_linea(_APROBADO_EN_LINEA_RE, texto)
    if not aprobado:
        return bloque
    return ApprovalBlock(
        elaborado_por=bloque.elaborado_por or _en_linea(_ELABORADO_EN_LINEA_RE, texto),
        revisado_por=bloque.revisado_por,
        aprobado_por=aprobado,
    )


def parse_header_text(text: str) -> DocumentHeader:
    code_match = CODE_RE.search(text)
    version_match = VERSION_RE.search(text)
    revision_match = REVISION_RE.search(text)

    revision: date | None = None
    if revision_match:
        try:
            revision = datetime.strptime(revision_match.group(1), "%d/%m/%Y").date()
        except ValueError:
            revision = None

    return DocumentHeader(
        code=code_match.group(1).upper() if code_match else None,
        version=f"v{int(version_match.group(1))}" if version_match else None,
        revision_date=revision,
    )


def read_header(pdf_path: Path) -> DocumentHeader:
    """Lee el membrete de la primera pagina. Nunca lanza: un PDF ilegible devuelve vacio."""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            pages = pdf.pages[:HEADER_PAGES]
            text = "\n".join((page.extract_text() or "") for page in pages)
    except Exception:  # noqa: BLE001 - un PDF corrupto no debe tumbar la carga
        return DocumentHeader()

    return parse_header_text(text)


def code_from_filename(filename: str) -> str | None:
    match = FILENAME_CODE_RE.match(Path(filename).stem.strip())
    return match.group(1).upper() if match else None


def title_from_filename(filename: str) -> str:
    """
    Un titulo presentable a partir del nombre del archivo.

    Los archivos suelen venir en forma de slug --
    "com_pr_01_compras.pdf" -- y ese texto acababa tal cual en la ficha de
    cada respuesta y en el desplegable de clausulas: "COM-PR-01 v3, seccion
    7.2 - com_pr_01_compras". Se retira el codigo, se deshacen los guiones
    bajos y se capitaliza.

    No sustituye al titulo real del documento: es lo mejor que se puede
    deducir cuando no hay otra cosa.
    """
    stem = Path(filename).stem.strip()
    code = code_from_filename(filename)
    if code:
        stem = stem[len(code):].strip()
    else:
        # El slug lleva el codigo delante en minusculas y con guiones bajos:
        # "com_pr_01_compras" -> "compras".
        sin_codigo = re.sub(r"^[a-z]{2,5}_[a-z]{2,5}_\d{1,3}_", "", stem, flags=re.I)
        stem = sin_codigo if sin_codigo != stem else stem

    limpio = re.sub(r"[_\-]+", " ", stem).strip()
    limpio = re.sub(r"\s+", " ", limpio)
    if not limpio:
        return ""
    # Capitalizacion de titulo, dejando en minuscula las palabras de enlace.
    menores = {"de", "del", "la", "las", "el", "los", "y", "o", "en", "a", "por", "para"}
    palabras = limpio.split()
    return " ".join(
        w.capitalize() if i == 0 or w.lower() not in menores else w.lower()
        for i, w in enumerate(palabras)
    )
