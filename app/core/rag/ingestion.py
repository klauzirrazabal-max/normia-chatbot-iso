"""
Ingesta de documentos ISO: PDF -> texto -> chunks por seccion/clausula -> embeddings -> DB.

Requisitos por tenant en data/knowledge_base/<tenant_id>/:
  - los PDFs de los documentos controlados
  - metadata.csv con columnas: filename,code,version,title,area,effective_date,status

El chunking es por seccion (ej. "5.2") y NO por tamano fijo de caracteres, porque en un
contexto ISO cada respuesta debe poder citar la clausula exacta (trazabilidad real).
"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pdfplumber
from sqlalchemy.orm import Session

from app.core.rag.embeddings import embed_texts
from app.models.db_models import Document, DocumentChunk

logger = logging.getLogger(__name__)

# Detecta encabezados de clausula. Cubre las dos convenciones que aparecen en
# documentacion ISO real:
#   "5.2 Calibracion de instrumentos"     (estilo de la norma)
#   "7.3. ANALISIS DE CAUSAS"             (estilo de procedimiento interno)
# El punto final tras el numero es OPCIONAL: exigir un espacio inmediatamente
# despues del numero hacia que ningun procedimiento con el segundo estilo se
# detectara, y el documento entero caia al fallback como un solo bloque.
SECTION_HEADER_RE = re.compile(
    r"^\s*(\d{1,2}(?:\.\d{1,2}){0,3})\.?\s+([A-ZÁÉÍÓÚÑ][^\n]{0,120})\s*$"
)

# Una linea que se repite en (casi) todas las paginas es membrete o pie: el
# encabezado con codigo/version y el aviso de confidencialidad. Sin quitarlo se
# cuela en cada chunk, diluye el embedding y desperdicia contexto del LLM.
BOILERPLATE_PAGE_RATIO = 0.6
MIN_PAGES_FOR_BOILERPLATE = 3

REQUIRED_COLUMNS = {"filename", "code", "version"}
VALID_STATUS = {"vigente", "obsoleto"}

# Chunks demasiado grandes degradan el retrieval y disparan el consumo de tokens:
# una clausula ISO larga se parte en trozos que conservan la misma etiqueta de seccion.
MAX_CHUNK_CHARS = 3000


@dataclass(frozen=True)
class IngestionReport:
    documents_ingested: int
    chunks_created: int
    skipped: list[str]

    def summary(self) -> str:
        lines = [
            f"Documentos ingestados: {self.documents_ingested}",
            f"Chunks creados:        {self.chunks_created}",
        ]
        if self.skipped:
            lines.append(f"Omitidos ({len(self.skipped)}):")
            lines.extend(f"  - {s}" for s in self.skipped)
        return "\n".join(lines)


def load_metadata(tenant_dir: Path) -> dict[str, dict[str, str]]:
    metadata_path = tenant_dir / "metadata.csv"
    if not metadata_path.exists():
        raise FileNotFoundError(
            f"No encontre {metadata_path}. Crea un metadata.csv con columnas: "
            "filename,code,version,title,area,effective_date,status"
        )

    with open(metadata_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        columns = set(reader.fieldnames or [])
        missing = REQUIRED_COLUMNS - columns
        if missing:
            raise ValueError(
                f"A {metadata_path} le faltan columnas obligatorias: {sorted(missing)}. "
                f"Encontradas: {sorted(columns)}"
            )

        rows: dict[str, dict[str, str]] = {}
        for line_no, row in enumerate(reader, start=2):
            filename = (row.get("filename") or "").strip()
            if not filename:
                raise ValueError(f"{metadata_path}:{line_no} tiene 'filename' vacio")

            status = (row.get("status") or "vigente").strip().lower()
            if status not in VALID_STATUS:
                raise ValueError(
                    f"{metadata_path}:{line_no} tiene status='{status}'. "
                    f"Solo se acepta: {sorted(VALID_STATUS)}"
                )
            row["status"] = status
            rows[filename] = row

    return rows


def _parse_date(value: str | None) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"effective_date='{value}' no es una fecha valida. Usa formato YYYY-MM-DD."
        ) from exc


# Un codigo de documento partido por el salto de linea del PDF:
# "el formato Acciones Correctivas CAL-FO- 09." El modelo lo lee bien, pero el
# texto indexado queda roto: la verificacion de citas no reconocia CAL-FO-09
# como presente en el contexto y marcaba la respuesta como no fundamentada.
_CODIGO_PARTIDO_RE = re.compile(r"\b([A-Z]{2,5}-[A-Z]{2,5}-)[\s\u00a0]+(\d{1,3})\b")

# Palabra cortada al final de linea: "presen-\ntacion". Si lo que sigue empieza
# en minuscula es una division silabica y el guion desaparece; si empieza en
# mayuscula o digito el guion es parte del termino y se conserva.
_GUION_SILABICO_RE = re.compile(r"(\w)-[ \t]*\n[ \t]*([a-záéíóúñü])")
_GUION_COMPUESTO_RE = re.compile(r"(\w-)[ \t]*\n[ \t]*([A-Z0-9])")


def repair_pdf_linebreaks(texto: str) -> str:
    """
    Reune lo que el salto de linea del PDF partio.

    La extraccion corta palabras y codigos al final de cada linea. Deja el texto
    legible para una persona pero roto para cualquier comparacion exacta: buscar
    "CAL-FO-09" no encuentra "CAL-FO- 09", y la verificacion de citas concluia
    que el modelo se habia inventado un codigo que estaba delante suyo.
    """
    texto = _CODIGO_PARTIDO_RE.sub(r"\1\2", texto)
    texto = _GUION_SILABICO_RE.sub(r"\1\2", texto)
    texto = _GUION_COMPUESTO_RE.sub(r"\1\2", texto)
    return texto


def strip_page_boilerplate(pages: list[str]) -> str:
    """
    Quita membretes y pies que se repiten pagina a pagina.

    En un documento controlado, cada pagina lleva el mismo encabezado
    (CODIGO / VERSION / REVISION / PAGINA n de m) y el mismo aviso de
    confidencialidad al pie. Repetido en cada chunk, ese texto domina el
    embedding y hace que todos los documentos se parezcan entre si.
    """
    if len(pages) < MIN_PAGES_FOR_BOILERPLATE:
        return "\n".join(pages)

    counts: dict[str, int] = {}
    for page in pages:
        for line in {ln.strip() for ln in page.splitlines() if ln.strip()}:
            counts[line] = counts.get(line, 0) + 1

    threshold = max(2, int(len(pages) * BOILERPLATE_PAGE_RATIO))
    boilerplate = {
        line
        for line, count in counts.items()
        # "PAGINA : 1 de 4" cambia por pagina, asi que se filtra por prefijo largo
        # solo lo que se repite literal; el resto lo cubre el regex de paginado.
        if count >= threshold
    }

    cleaned_pages = []
    for page in pages:
        kept = [
            line
            for line in page.splitlines()
            if line.strip() and line.strip() not in boilerplate
        ]
        cleaned_pages.append("\n".join(kept))

    return "\n".join(cleaned_pages)


# Linea de tabla de contenidos: titulo, puntos de relleno y numero de pagina.
# "2. ALCANCE DEL SISTEMA DE GESTION DE LA CALIDAD ............ 3"
_LINEA_DE_INDICE_RE = re.compile(r"\.{4,}\s*\d*\s*$")

# Una clausula que solo anuncia su contenido y no lo trae: el texto real esta en
# una imagen o un grafico que la extraccion no alcanza.
MAX_CHARS_CLAUSULA_ANUNCIO = 250


def looks_like_table_of_contents(texto: str) -> bool:
    """
    True si el bloque es el indice del documento, no contenido.

    El indice se cuela como contenido porque sus lineas empiezan con el numero de
    seccion y pasan el regex de encabezado. El resultado: DOS chunks con seccion
    "2", uno con el alcance real y otro con puntos de relleno.

    Y el del indice gana la busqueda vectorial mas veces, porque repite los
    titulos de todas las secciones. Preguntando "que es el sistema de calidad",
    el modelo recibio el indice del Manual y respondio, con razon, que no tenia el
    texto de la seccion 2 -- mientras la cita al pie afirmaba que si.
    """
    lineas = [ln for ln in (texto or "").splitlines() if ln.strip()]
    if not lineas:
        return False

    con_relleno = sum(1 for ln in lineas if _LINEA_DE_INDICE_RE.search(ln))

    # El chunker parte el indice en UNA LINEA POR SECCION, porque cada entrada
    # empieza con su numero y pasa el regex de encabezado. Asi que el caso normal
    # es un chunk de una sola linea, no un bloque.
    if len(lineas) == 1:
        return con_relleno == 1

    return con_relleno >= 2 and con_relleno * 2 >= len(lineas)


def clause_announces_without_delivering(texto: str) -> bool:
    """
    True si la clausula anuncia su contenido pero no lo trae.

    Pasa cuando el texto real es una imagen o un grafico: la seccion 2 del Manual
    de la Calidad dice "El Alcance del Sistema de Gestion de la Calidad de
    la organizacion es:" y ahi se corta -- el alcance en si esta en una figura que la
    extraccion no alcanza.

    No es un fallo de la ingesta: el PDF no tiene ese texto. Pero hay que
    AVISARLO, porque el asistente no podra responder sobre esa clausula y el
    usuario merece saber cuales quedaron ciegas.

    La firma es corta y fiable: acaba en dos puntos y el bloque es breve. Es una
    heuristica, no una prueba -- de ahi que solo genere un aviso y no descarte
    el chunk.
    """
    limpio = (texto or "").strip()
    return bool(limpio) and limpio.endswith(":") and len(limpio) < MAX_CHARS_CLAUSULA_ANUNCIO


def render_table(rows: list[list[str | None]]) -> str:
    """
    Convierte una tabla en filas delimitadas por '|', una por linea.

    Es la diferencia entre que el asistente responda bien o mal sobre un SLA.
    `extract_text()` aplana la tabla y rompe la relacion entre celda y columna:
    una fila "REQUERIMIENTO: 1 IMAC | Segun acuerdo con solicitante | De acuerdo
    a disponibilidad de proveedor" sale como tres lineas sueltas y desordenadas,
    y el modelo termina atribuyendo el valor de una columna a otra.

    En documentacion ISO eso importa mucho: los tiempos de respuesta, las
    matrices de responsabilidad y los plazos de CAPA viven en tablas.
    """
    rendered: list[str] = []
    for row in rows:
        # Las celdas combinadas dejan columnas vacias; se colapsan para no
        # generar filas llenas de separadores sin contenido.
        cells = [(c or "").replace("\n", " ").strip() for c in row]
        cells = [c for c in cells if c]
        if cells:
            rendered.append(" | ".join(cells))
    return "\n".join(rendered)


# El membrete es una tabla pequena: 4 filas por 5 columnas tipicamente. Un
# FORMATO, en cambio, es una tabla grande que constituye el documento entero.
MAX_FILAS_MEMBRETE = 6


def _is_header_table(rows: list[list[str | None]]) -> bool:
    """
    True si la tabla es el membrete del documento, no su contenido.

    Se exige que sea PEQUENA, y esa condicion es la que faltaba: la Lista Maestra
    y los formatos son una sola tabla grande cuyo encabezado tambien dice
    CODIGO/VERSION. Sin el limite de filas se descartaba el documento completo, y
    47 de 132 documentos -- casi todos formatos -- quedaron sin una sola linea
    indexada.
    """
    if len(rows) > MAX_FILAS_MEMBRETE:
        return False
    flat = " ".join((c or "") for row in rows[:2] for c in row).upper()
    return sum(k in flat for k in ("CÓDIGO", "CODIGO", "VERSIÓN", "VERSION", "PÁGINA")) >= 2


def extract_page_content(page) -> str:
    """
    Texto de una pagina con las tablas renderizadas EN SU POSICION.

    Se recorre la pagina de arriba hacia abajo: el texto sobre cada tabla se
    extrae recortando esa franja, luego se inserta la tabla, y se sigue debajo.
    Asi la tabla queda bajo el encabezado de clausula que le corresponde y el
    chunk conserva su etiqueta de seccion correcta.
    """
    try:
        tables = page.find_tables()
    except Exception:  # noqa: BLE001 - un PDF raro cae al modo solo-texto
        return page.extract_text() or ""

    if not tables:
        return page.extract_text() or ""

    partes: list[str] = []
    cursor = 0.0

    for table in sorted(tables, key=lambda t: t.bbox[1]):
        x0, top, x1, bottom = table.bbox

        if top > cursor:
            franja = page.crop((0, cursor, page.width, top))
            texto = franja.extract_text() or ""
            if texto.strip():
                partes.append(texto)

        filas = table.extract()
        if filas and not _is_header_table(filas):
            renderizada = render_table(filas)
            if renderizada:
                partes.append(renderizada)

        cursor = max(cursor, bottom)

    if cursor < page.height:
        franja = page.crop((0, cursor, page.width, page.height))
        texto = franja.extract_text() or ""
        if texto.strip():
            partes.append(texto)

    resultado = "\n".join(partes)

    # Red de seguridad: si el filtrado dejo la pagina vacia pero el PDF si tenia
    # texto, se devuelve el texto plano. Perder el orden de una tabla es malo;
    # perder el documento entero en silencio es mucho peor -- y fue justo lo que
    # paso con los formatos, cuya unica tabla se confundia con el membrete.
    if not resultado.strip():
        return page.extract_text() or ""

    return resultado


def extract_full_text(pdf_path: Path) -> str:
    with pdfplumber.open(pdf_path) as pdf:
        pages = [extract_page_content(page) for page in pdf.pages]
    return repair_pdf_linebreaks(strip_page_boilerplate(pages))


def _split_oversized(section: str | None, content: str) -> list[tuple[str | None, str]]:
    """Parte un chunk demasiado largo por parrafos, conservando la etiqueta de seccion."""
    if len(content) <= MAX_CHUNK_CHARS:
        return [(section, content)]

    pieces: list[tuple[str | None, str]] = []
    buffer: list[str] = []
    size = 0
    for paragraph in content.split("\n\n"):
        if size + len(paragraph) > MAX_CHUNK_CHARS and buffer:
            pieces.append((section, "\n\n".join(buffer).strip()))
            buffer, size = [], 0
        buffer.append(paragraph)
        size += len(paragraph)
    if buffer:
        pieces.append((section, "\n\n".join(buffer).strip()))
    return pieces


def chunk_by_section(full_text: str, min_chars: int = 80) -> list[tuple[str | None, str]]:
    """
    Divide el texto en (seccion, contenido). Si no detecta encabezados numerados,
    cae a un fallback por parrafos para no perder el documento completo.
    """
    chunks: list[tuple[str | None, list[str]]] = []
    current_section: str | None = None
    current_lines: list[str] = []

    for line in full_text.splitlines():
        match = SECTION_HEADER_RE.match(line)
        if match:
            if current_lines:
                chunks.append((current_section, current_lines))
            current_section = match.group(1)
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_lines:
        chunks.append((current_section, current_lines))

    result = [(section, "\n".join(ls).strip()) for section, ls in chunks]
    result = [(s, c) for s, c in result if len(c) >= min_chars]
    # El indice del documento no es contenido: solo repite titulos y numeros de
    # pagina, y compite con las clausulas reales en la busqueda vectorial.
    result = [(s, c) for s, c in result if not looks_like_table_of_contents(c)]

    if not result:
        # Fallback: sin estructura numerada detectada, partir por parrafos dobles
        paragraphs = [p.strip() for p in full_text.split("\n\n") if len(p.strip()) >= min_chars]
        result = [(None, p) for p in paragraphs]

    return [piece for section, content in result for piece in _split_oversized(section, content)]


def ingest_tenant(
    tenant_id: str,
    db: Session,
    base_dir: str = "data/knowledge_base",
    replace: bool = True,
) -> IngestionReport:
    """
    Procesa todos los documentos de un tenant y los deja listos para RAG.

    Con `replace=True` (por defecto) la ingesta es idempotente: reemplaza los chunks
    del documento en vez de duplicarlos si vuelves a correrla.
    """
    tenant_dir = Path(base_dir) / tenant_id
    metadata = load_metadata(tenant_dir)

    documents_ingested = 0
    total_chunks = 0
    skipped: list[str] = []

    for filename, meta in metadata.items():
        pdf_path = tenant_dir / filename
        if not pdf_path.exists():
            skipped.append(f"{filename}: esta en metadata.csv pero no existe en {tenant_dir}")
            continue

        document = (
            db.query(Document)
            .filter_by(tenant_id=tenant_id, code=meta["code"], version=meta["version"])
            .one_or_none()
        )
        if document is None:
            document = Document(
                tenant_id=tenant_id,
                code=meta["code"],
                version=meta["version"],
                title=(meta.get("title") or "").strip() or None,
                area=(meta.get("area") or "").strip() or None,
                effective_date=_parse_date(meta.get("effective_date")),
                status=meta["status"],
                source_filename=filename,
            )
            db.add(document)
            db.flush()  # para obtener document.id
        else:
            document.status = meta["status"]
            document.title = (meta.get("title") or "").strip() or document.title
            document.area = (meta.get("area") or "").strip() or document.area
            if replace:
                db.query(DocumentChunk).filter_by(document_id=document.id).delete()

        full_text = extract_full_text(pdf_path)
        sections = chunk_by_section(full_text)
        if not sections:
            skipped.append(f"{filename}: no se pudo extraer texto util (PDF escaneado sin OCR?)")
            continue

        vectors = embed_texts([content for _, content in sections])

        for section, content in sections:
            if clause_announces_without_delivering(content):
                skipped.append(
                    f"{meta['code']} seccion {section or '?'}: la clausula anuncia su "
                    "contenido pero el PDF no lo trae como texto (probablemente una "
                    "imagen). El asistente no podra responder sobre ella."
                )

        for (section, content), vector in zip(sections, vectors, strict=True):
            db.add(
                DocumentChunk(
                    document_id=document.id,
                    tenant_id=tenant_id,
                    section=section,
                    content=content,
                    embedding=vector,
                )
            )
            total_chunks += 1

        documents_ingested += 1
        logger.info(
            "ingestion.document_done",
            extra={
                "code": meta["code"],
                "version": meta["version"],
                "status": meta["status"],
                "chunks": len(sections),
            },
        )

    db.commit()
    return IngestionReport(documents_ingested, total_chunks, skipped)
