"""
Genera metadata.csv desde la Lista Maestra de Documentos Internos (CAL-FO-01).

En un SGC ISO, la lista maestra es la fuente autoritativa de que documentos
existen, en que version y desde cuando. Derivar el metadata.csv de ahi -- en vez
de escribirlo a mano -- es lo que mantiene al bot alineado con el SGC real: si
manana sube la version de un procedimiento, se regenera y listo.

Cada campo sale de su fuente mas confiable:
  - codigo, version, fecha  -> la lista maestra
  - titulo                  -> el nombre del archivo (limpio y ya desambiguado)
  - area                    -> la carpeta de primer nivel

Uso:
    uv run python scripts/build_metadata.py \
        --source "/ruta/a/ISO 9001" \
        --dest data/knowledge_base/empresa-demo-iso \
        --copy
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import shutil
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pdfplumber

# Tail de cada fila de la lista maestra: CODIGO VERSION FECHA
MASTER_ROW_RE = re.compile(
    r"\b(?P<code>[A-Z]{3}-[A-Z]{2}-\d{2})\s+(?P<version>\d+|N/A)\s+"
    r"(?P<date>\d{2}/\d{2}/\d{4}|N/A)\s*$"
)

# Codigo al inicio del nombre de archivo: "COM-PR-01 Compras.pdf"
FILENAME_CODE_RE = re.compile(r"^(?P<code>[A-Z]{3}-[A-Z]{2}-\d{2})\s+(?P<title>.+)$")

DEFAULT_MASTER = "LISTAS MAESTRAS/CAL-FO-01 Lista maestra de documentos internos.pdf"


def parse_master_list(pdf_path: Path) -> dict[str, dict[str, str]]:
    """Devuelve {codigo: {version, effective_date}} leido de la lista maestra."""
    entries: dict[str, dict[str, str]] = {}

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for line in (page.extract_text() or "").splitlines():
                match = MASTER_ROW_RE.search(line.strip())
                if not match:
                    continue

                version = match.group("version")
                raw_date = match.group("date")

                if version == "N/A":
                    continue  # documento sin control de version formal

                effective = ""
                if raw_date != "N/A":
                    try:
                        effective = datetime.strptime(raw_date, "%d/%m/%Y").date().isoformat()
                    except ValueError:
                        effective = ""

                entries[match.group("code")] = {
                    "version": f"v{int(version)}",
                    "effective_date": effective,
                }

    return entries


def slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", normalized.lower()).strip("_")


def collect_pdfs(source: Path) -> list[tuple[Path, str, str, str]]:
    """Devuelve (ruta, codigo, titulo, area) de cada PDF cuyo nombre lleva codigo."""
    found = []
    for pdf in sorted(source.rglob("*.pdf")):
        stem = pdf.stem.strip()
        match = FILENAME_CODE_RE.match(stem)
        if not match:
            continue

        relative = pdf.relative_to(source)
        area = relative.parts[0] if len(relative.parts) > 1 else "General"
        found.append((pdf, match.group("code"), match.group("title").strip(), area))
    return found


def file_hash(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dedupe_and_disambiguate(rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], int]:
    """
    Un mismo codigo puede repetirse por dos razones distintas, y se tratan distinto:

    1. El MISMO archivo esta guardado en dos carpetas (CAL-FO-01 vive en
       FORMATOS y en LISTAS MAESTRAS). Es un solo documento: se conserva uno.

    2. Archivos DISTINTOS comparten codigo porque el formato se instancia por
       proceso (CAL-FO-13, Ficha de Caracterizacion, tiene una por area). Son
       documentos distintos: se les anexa su calificador, que es como los
       distingue el propio SGC -> "CAL-FO-13 (Compras)".

    Distinguir los dos casos requiere mirar el contenido, no el nombre. Sin esto,
    el caso 1 inflaria el corpus con duplicados y el caso 2 chocaria contra
    UNIQUE(tenant_id, code, version): el segundo archivo pisaria los chunks del
    primero, con perdida silenciosa de datos.
    """
    by_code: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_code[row["code"]].append(row)

    resultado: list[dict[str, str]] = []
    duplicados = 0

    for code, group in by_code.items():
        # Caso 1: colapsar archivos identicos, quedandose con el de ruta mas corta.
        por_hash: dict[str, dict[str, str]] = {}
        for row in group:
            digest = file_hash(row["_source"])
            previo = por_hash.get(digest)
            if previo is None:
                por_hash[digest] = row
            else:
                duplicados += 1
                if len(row["_source"]) < len(previo["_source"]):
                    por_hash[digest] = row

        unicos = list(por_hash.values())

        # Caso 2: si quedan varios contenidos distintos, desambiguar el codigo.
        if len(unicos) > 1:
            for row in unicos:
                row["code"] = f"{code} ({_qualifier(row)})"
            _force_unique_codes(code, unicos)

        resultado.extend(unicos)

    return resultado, duplicados


def _qualifier(row: dict[str, str]) -> str:
    """Calificador preferido: el parentesis del titulo; si no, la carpeta contenedora."""
    match = re.search(r"\(([^)]+)\)", row["title"])
    return match.group(1) if match else Path(row["_source"]).parent.name


def _force_unique_codes(base_code: str, rows: list[dict[str, str]]) -> None:
    """
    El calificador puede seguir repitiendose: la Ficha de Caracterizacion de
    Gestion Comercial existe en FORMATOS/ y en PROCEDIMIENTOS/, y ambas dicen
    "(Gestion Comercial)". Se anade la carpeta raiz, y como ultimo recurso un
    ordinal -- porque un choque aqui llega a la base como
    UNIQUE(tenant_id, code, version) violado y descarta un documento entero.
    """
    conteo: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        conteo[row["code"]].append(row)

    for grupo in conteo.values():
        if len(grupo) == 1:
            continue
        for row in grupo:
            raiz = Path(row["_source"]).parts
            try:
                indice = raiz.index("ISO 9001")
                carpeta_raiz = raiz[indice + 1]
            except (ValueError, IndexError):
                carpeta_raiz = Path(row["_source"]).parent.name
            row["code"] = f"{base_code} ({_qualifier(row)} - {carpeta_raiz})"

    # Red de seguridad: si algo sigue chocando, ordinal.
    vistos: dict[str, int] = {}
    for row in rows:
        n = vistos.get(row["code"], 0)
        vistos[row["code"]] = n + 1
        if n:
            row["code"] = f"{row['code']} #{n + 1}"


def assign_filenames(rows: list[dict[str, str]]) -> None:
    """Nombre plano, estable y garantizado unico dentro del tenant."""
    usados: set[str] = set()
    for row in rows:
        base = f"{slugify(row['code'])}_{slugify(row['title'])[:60]}"
        candidato = f"{base}.pdf"
        n = 2
        while candidato in usados:
            candidato = f"{base}_{n}.pdf"
            n += 1
        usados.add(candidato)
        row["filename"] = candidato


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Genera metadata.csv desde la lista maestra de un SGC ISO"
    )
    parser.add_argument("--source", required=True, help="Carpeta raiz del SGC")
    parser.add_argument("--master", default=None, help="Ruta a la lista maestra (CAL-FO-01)")
    parser.add_argument("--dest", required=True, help="Carpeta del tenant")
    parser.add_argument("--copy", action="store_true", help="Copiar los PDFs al tenant")
    args = parser.parse_args()

    source = Path(args.source).expanduser()
    dest = Path(args.dest).expanduser()
    master_path = Path(args.master) if args.master else source / DEFAULT_MASTER

    if not master_path.exists():
        print(f"ERROR: no encontre la lista maestra en {master_path}")
        return 1

    master = parse_master_list(master_path)
    print(f"Lista maestra: {len(master)} documentos con version controlada\n")

    pdfs = collect_pdfs(source)
    rows: list[dict[str, str]] = []
    sin_codigo_en_maestra: list[str] = []

    for path, code, title, area in pdfs:
        entry = master.get(code)
        if entry is None:
            sin_codigo_en_maestra.append(f"{code} - {title}")
            continue

        rows.append(
            {
                "code": code,
                "version": entry["version"],
                "title": title,
                "area": area,
                "effective_date": entry["effective_date"],
                "status": "vigente",
                "_source": str(path),
            }
        )

    rows, duplicados = dedupe_and_disambiguate(rows)
    assign_filenames(rows)
    if duplicados:
        print(f"Archivos identicos colapsados (mismo doc en varias carpetas): {duplicados}\n")

    dest.mkdir(parents=True, exist_ok=True)
    if args.copy:
        for row in rows:
            shutil.copy2(row["_source"], dest / row["filename"])

    columns = ["filename", "code", "version", "title", "area", "effective_date", "status"]
    csv_path = dest / "metadata.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda r: r["code"]))

    print(f"Escrito {csv_path} con {len(rows)} documentos.")
    if args.copy:
        print(f"Copiados {len(rows)} PDFs a {dest}")

    por_area: dict[str, int] = defaultdict(int)
    for row in rows:
        por_area[row["area"]] += 1
    print("\nPor area:")
    for area, count in sorted(por_area.items(), key=lambda kv: -kv[1]):
        print(f"  {count:>3}  {area}")

    en_maestra_sin_pdf = set(master) - {r["code"].split(" (")[0] for r in rows}
    if en_maestra_sin_pdf:
        print(f"\nEn la lista maestra pero sin PDF localizado ({len(en_maestra_sin_pdf)}):")
        for code in sorted(en_maestra_sin_pdf):
            print(f"  {code}")

    if sin_codigo_en_maestra:
        print(f"\nPDF con codigo pero ausente de la lista maestra ({len(sin_codigo_en_maestra)}):")
        for item in sorted(sin_codigo_en_maestra):
            print(f"  {item}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
