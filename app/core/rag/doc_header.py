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
    stem = Path(filename).stem.strip()
    code = code_from_filename(filename)
    return stem[len(code) :].strip() if code else stem
