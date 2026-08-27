"""
Genera un FAQ desde los documentos del SGC, con verificacion.

Recorre cada clausula, le pide al modelo local las preguntas que ESE texto
responde, y VERIFICA cada respuesta contra la clausula antes de aceptarla.

Por que la verificacion no es opcional: un FAQ de cumplimiento con una cifra
inventada es peor que no tener FAQ. La gente lo lee sin abrir el documento, y
una respuesta que dice "el plazo es de 5 dias" cuando el procedimiento dice 3
se convierte en una no conformidad con apariencia oficial.

El proceso es reanudable. Procesar 765 clausulas toma alrededor de una hora, y
perder ese trabajo por un fallo en la clausula 700 seria absurdo: cada resultado
se escribe a un JSONL en el momento, y al reanudar se saltan las ya hechas.

Salidas en data/faq/:
    faq_<tenant>.md          FAQ para la empresa, por area y documento
    faq_<tenant>.json        set de validacion: pregunta + cita esperada
    faq_<tenant>.jsonl       checkpoint (una linea por clausula procesada)
    faq_<tenant>_rechazos.md lo que se descarto y por que -- auditable

Uso:
    uv run python scripts/generate_faq.py --tenant empresa-demo-iso
    uv run python scripts/generate_faq.py --areas PROCEDIMIENTOS MANUALES
    uv run python scripts/generate_faq.py --reanudar
    uv run python scripts/generate_faq.py --solo-informe   # rehacer .md desde el checkpoint
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.core.docs.faq_validation import (  # noqa: E402
    clave_de_deduplicacion,
    validar_entrada,
)
from app.core.rag.ingestion import clause_announces_without_delivering  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402
from app.models.db_models import Document, DocumentChunk  # noqa: E402
from app.services.llm_client import LLMError, get_llm_client  # noqa: E402

# Clausulas de tramite: existen en todos los documentos ISO y no generan
# preguntas que alguien haga de verdad ("cual es el objetivo del manual?").
TITULOS_DE_TRAMITE = (
    "objetivo",
    "alcance",
    "documentos de referencia",
    "abreviaturas",
    "responsabilidad",
    "control de cambios",
    "anexos",
    "registros",
)

MIN_CHARS_CLAUSULA = 120
MAX_PREGUNTAS_POR_CLAUSULA = 2
REINTENTOS_JSON = 2

PROMPT_SISTEMA = """Eres un especialista en sistemas de gestion de la calidad ISO 9001.

Te doy el texto de UNA clausula de un documento controlado. Genera las preguntas
que una persona de la organizacion haria de verdad y que ESTA clausula responde.

Reglas estrictas:
- La respuesta debe salir UNICAMENTE del texto que te doy. No completes con
  conocimiento general sobre ISO.
- NUNCA escribas una cifra, un plazo, un porcentaje o un codigo de documento que
  no aparezca literalmente en el texto. Se verifica automaticamente y la entrada
  se descarta.
- Preguntas como las haria un trabajador, no como las escribiria un auditor:
  "Cuanto tarda TI en atender una incidencia?" en vez de "Cual es el tiempo
  maximo de respuesta establecido en el cuadro N 01?".
- Si la clausula no responde nada concreto y util, devuelve una lista vacia. Es
  preferible a una pregunta forzada.
- No digas "el texto no especifica" ni "se asume": si no lo sabes, no generes la
  pregunta.

Responde SOLO con JSON valido, sin texto alrededor:
{"faq": [{"pregunta": "...", "respuesta": "..."}]}

Cada pregunta termina en '?'. Cada respuesta es de una o dos frases, concreta y
en espanol."""


@dataclass
class EntradaFAQ:
    pregunta: str
    respuesta: str
    codigo: str
    version: str
    titulo_documento: str
    area: str
    seccion: str | None = None


@dataclass
class Rechazo:
    pregunta: str
    respuesta: str
    codigo: str
    seccion: str | None
    motivo: str
    detalle: str


@dataclass
class Reporte:
    entradas: list[EntradaFAQ] = field(default_factory=list)
    rechazos: list[Rechazo] = field(default_factory=list)
    duplicadas: int = 0
    clausulas_procesadas: int = 0
    clausulas_candidatas: int = 0
    fallos: list[str] = field(default_factory=list)


def es_clausula_de_tramite(contenido: str) -> bool:
    encabezado = re.sub(r"^\s*\d+(?:\.\d+)*\.?\s*", "", (contenido or "")[:90]).lower()
    return any(encabezado.startswith(t) for t in TITULOS_DE_TRAMITE)


def vale_la_pena(contenido: str) -> bool:
    """Una clausula genera FAQ si tiene contenido, no es tramite y no esta ciega."""
    if len(contenido) < MIN_CHARS_CLAUSULA:
        return False
    if es_clausula_de_tramite(contenido):
        return False
    return not clause_announces_without_delivering(contenido)


def extraer_json(texto: str) -> dict:
    """
    Saca el objeto JSON de la respuesta del modelo.

    Aunque se le pida JSON puro, un modelo local a veces lo envuelve en un bloque
    cercado o le pone una frase delante. Se busca el primer objeto balanceado en
    vez de exigir que la respuesta entera sea JSON.
    """
    texto = (texto or "").strip()
    inicio = texto.find("{")
    if inicio == -1:
        return {}

    profundidad = 0
    for i, ch in enumerate(texto[inicio:], start=inicio):
        if ch == "{":
            profundidad += 1
        elif ch == "}":
            profundidad -= 1
            if profundidad == 0:
                try:
                    return json.loads(texto[inicio : i + 1])
                except json.JSONDecodeError:
                    return {}
    return {}


def preguntas_de_clausula(
    cliente,
    codigo: str,
    version: str,
    titulo: str,
    area: str,
    seccion: str | None,
    contenido: str,
) -> list[dict]:
    """
    Pide las preguntas al modelo, reintentando si el JSON sale mal formado.

    Un JSON roto no es un fallo del modelo sino del formato, y suele arreglarse
    al segundo intento. Perder una clausula por eso seria tirar contenido bueno.
    """
    contexto = (
        f"Documento: {codigo} {version} - {titulo}\n"
        f"Area: {area}\n"
        f"Clausula: {seccion or 'sin numero'}\n\n"
        f"{contenido}"
    )

    for intento in range(REINTENTOS_JSON + 1):
        respuesta = cliente.generate(
            [
                {"role": "system", "content": PROMPT_SISTEMA},
                {"role": "user", "content": contexto},
            ]
        )
        datos = extraer_json(respuesta["content"])
        faq = datos.get("faq")

        if isinstance(faq, list):
            return [item for item in faq[:MAX_PREGUNTAS_POR_CLAUSULA] if isinstance(item, dict)]
        if "faq" in datos:  # la clave existe pero vacia o mal tipada: no reintentar
            return []
        if intento == REINTENTOS_JSON:
            raise ValueError("el modelo no devolvio JSON valido tras varios intentos")

    return []


# --- Checkpoint --------------------------------------------------------------
#
# Una linea JSON por clausula procesada, escrita en el momento. Reanudar es leer
# que claves ya estan y saltarlas.


def clave_clausula(codigo: str, seccion: str | None) -> str:
    return f"{codigo}§{seccion or '-'}"


def leer_checkpoint(ruta: Path) -> tuple[dict[str, dict], set[str]]:
    if not ruta.exists():
        return {}, set()
    registros: dict[str, dict] = {}
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        if not linea.strip():
            continue
        try:
            registro = json.loads(linea)
        except json.JSONDecodeError:
            continue  # linea a medias por un corte: se reprocesa esa clausula
        registros[registro["clave"]] = registro
    return registros, set(registros)


def escribir_checkpoint(ruta: Path, registro: dict) -> None:
    with open(ruta, "a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")
        f.flush()


def generar(
    tenant_id: str,
    areas: list[str] | None,
    codigos: list[str] | None,
    checkpoint: Path,
    reanudar: bool,
) -> Reporte:
    cliente = get_llm_client()
    reporte = Reporte()

    hechas: set[str] = set()
    previos: dict[str, dict] = {}
    if reanudar:
        previos, hechas = leer_checkpoint(checkpoint)
        print(f"Reanudando: {len(hechas)} clausulas ya procesadas\n")
    elif checkpoint.exists():
        checkpoint.unlink()

    with SessionLocal() as db:
        consulta = (
            db.query(DocumentChunk, Document)
            .join(Document, DocumentChunk.document_id == Document.id)
            .filter(Document.tenant_id == tenant_id, Document.status == "vigente")
        )
        if areas:
            consulta = consulta.filter(Document.area.in_(areas))
        if codigos:
            patron = "^(" + "|".join(re.escape(c) for c in codigos) + ")"
            consulta = consulta.filter(Document.code.op("~")(patron))

        filas = [
            (
                chunk.section,
                chunk.content,
                doc.code,
                doc.version,
                doc.title or doc.code,
                doc.area or "Sin area",
            )
            for chunk, doc in consulta.order_by(Document.code, DocumentChunk.id).all()
        ]

    candidatas = [f for f in filas if vale_la_pena(f[1])]
    reporte.clausulas_candidatas = len(candidatas)
    pendientes = [f for f in candidatas if clave_clausula(f[2], f[0]) not in hechas]

    print(f"{len(filas)} clausulas, {len(candidatas)} candidatas, {len(pendientes)} pendientes\n")

    for i, (seccion, contenido, codigo, version, titulo, area) in enumerate(pendientes, start=1):
        clave = clave_clausula(codigo, seccion)
        etiqueta = f"{codigo} §{seccion or '-'}"
        print(f"  [{i:4}/{len(pendientes)}] {etiqueta:34}", end="", flush=True)

        inicio = time.perf_counter()
        try:
            crudas = preguntas_de_clausula(
                cliente, codigo, version, titulo, area, seccion, contenido
            )
        except (LLMError, ValueError) as exc:
            reporte.fallos.append(f"{etiqueta}: {exc}")
            print("  ERROR")
            continue

        registro = {
            "clave": clave,
            "codigo": codigo,
            "version": version,
            "titulo": titulo,
            "area": area,
            "seccion": seccion,
            "generadas": crudas,
        }
        escribir_checkpoint(checkpoint, registro)
        previos[clave] = registro
        reporte.clausulas_procesadas += 1
        print(f"  {len(crudas)} generada(s)  {time.perf_counter()-inicio:5.1f}s")

    # La validacion corre sobre TODO el checkpoint, no solo lo recien generado:
    # asi un cambio en las reglas se aplica al conjunto sin regenerar nada.
    contenidos = {clave_clausula(f[2], f[0]): f[1] for f in candidatas}
    _validar_y_deduplicar(reporte, previos, contenidos, _codigos_del_tenant(tenant_id))
    return reporte


def _codigos_del_tenant(tenant_id: str) -> set[str]:
    """
    Codigos que existen en el SGC: lo indexado MAS la Lista Maestra.

    Validar solo contra lo indexado descartaba respuestas correctas: los formatos
    INV-FO-02, 06, 11, 13, 16, 18, 21 y GTH-FO-14 estan registrados, pero sus
    PDFs vienen agrupados en archivos combinados y nunca se ingestaron por
    separado. Ocho preguntas buenas se perdieron por eso.
    """
    from app.models.db_models import RegistryEntry

    with SessionLocal() as db:
        indexados = {
            code.split(" (")[0].upper()
            for (code,) in db.query(Document.code).filter_by(tenant_id=tenant_id).distinct()
        }
        registrados = {
            code.upper()
            for (code,) in db.query(RegistryEntry.code)
            .filter_by(tenant_id=tenant_id)
            .distinct()
        }
    return indexados | registrados


def _validar_y_deduplicar(
    reporte: Reporte,
    registros: dict[str, dict],
    contenidos: dict[str, str],
    codigos_conocidos: set[str],
) -> None:
    vistas: set[str] = set()

    for clave, registro in registros.items():
        clausula = contenidos.get(clave, "")
        for item in registro.get("generadas", []):
            pregunta = str(item.get("pregunta", "")).strip()
            respuesta = str(item.get("respuesta", "")).strip()

            resultado = validar_entrada(
                pregunta, respuesta, clausula, registro["codigo"], codigos_conocidos
            )
            if not resultado.valida:
                reporte.rechazos.append(
                    Rechazo(
                        pregunta=pregunta,
                        respuesta=respuesta,
                        codigo=registro["codigo"],
                        seccion=registro["seccion"],
                        motivo=str(resultado.motivo),
                        detalle=resultado.detalle,
                    )
                )
                continue

            dedup = clave_de_deduplicacion(pregunta)
            if dedup in vistas:
                reporte.duplicadas += 1
                continue
            vistas.add(dedup)

            reporte.entradas.append(
                EntradaFAQ(
                    pregunta=pregunta,
                    respuesta=respuesta,
                    codigo=registro["codigo"],
                    version=registro["version"],
                    titulo_documento=registro["titulo"],
                    area=registro["area"],
                    seccion=registro["seccion"],
                )
            )


def escribir_markdown(reporte: Reporte, destino: Path, tenant_id: str) -> None:
    por_area: dict[str, dict[str, list[EntradaFAQ]]] = defaultdict(lambda: defaultdict(list))
    for e in reporte.entradas:
        por_area[e.area][f"{e.codigo} {e.version} — {e.titulo_documento}"].append(e)

    lineas = [
        "# Preguntas frecuentes del Sistema de Gestión de la Calidad",
        "",
        f"Generado automáticamente desde los documentos vigentes de `{tenant_id}`.",
        "",
        f"**{len(reporte.entradas)} preguntas** verificadas, de "
        f"{reporte.clausulas_procesadas} cláusulas.",
        "",
        "Cada respuesta se comprobó contra el texto de la cláusula que cita: toda cifra "
        "y todo código que aparece aquí existe en el documento original. Se descartaron "
        f"{len(reporte.rechazos)} respuestas que no pasaron esa comprobación.",
        "",
        "> Este documento es un resumen de apoyo. Ante cualquier duda, **el documento "
        "controlado prevalece**.",
        "",
        "---",
        "",
    ]

    for area in sorted(por_area):
        lineas += [f"## {area}", ""]
        for documento in sorted(por_area[area]):
            lineas += [f"### {documento}", ""]
            for e in por_area[area][documento]:
                seccion = f", sección {e.seccion}" if e.seccion else ""
                lineas += [
                    f"**{e.pregunta}**",
                    "",
                    f"{e.respuesta}",
                    "",
                    f"*Fuente: {e.codigo} {e.version}{seccion}*",
                    "",
                ]

    destino.write_text("\n".join(lineas), encoding="utf-8")


def escribir_rechazos(reporte: Reporte, destino: Path) -> None:
    """
    Lo descartado, con su motivo. Es lo que hace auditable el proceso: sin este
    archivo, "se verificaron las respuestas" seria una afirmacion sin respaldo.
    """
    por_motivo: dict[str, list[Rechazo]] = defaultdict(list)
    for r in reporte.rechazos:
        por_motivo[r.motivo].append(r)

    explicacion = {
        "cifra_inventada": (
            "La respuesta contiene un numero que NO aparece en la clausula. Es el "
            "rechazo mas importante: una cifra inventada en un FAQ de cumplimiento "
            "se lee como oficial."
        ),
        "codigo_inventado": "Cita un documento que la clausula no menciona.",
        "codigo_inexistente": (
            "Cita un codigo que NO existe en el SGC. Suele venir del propio documento "
            "fuente: si la clausula trae una errata, validar solo contra ella la "
            "copiaria al FAQ. Revisa el documento original."
        ),
        "incertidumbre": (
            "La respuesta admite no saber o especula. Significa que la clausula no "
            "respondia la pregunta."
        ),
        "longitud": "Respuesta demasiado corta o demasiado larga.",
        "pregunta_vacia": "Pregunta mal formada o sin signo de interrogacion.",
    }

    lineas = [
        "# Respuestas descartadas en la generación del FAQ",
        "",
        f"**{len(reporte.rechazos)} descartadas** de "
        f"{len(reporte.rechazos) + len(reporte.entradas) + reporte.duplicadas} generadas "
        f"({reporte.duplicadas} más eran duplicadas).",
        "",
        "Cada respuesta se comprueba contra el texto de su cláusula antes de entrar al "
        "FAQ. Esta lista existe para que la verificación sea auditable y no una "
        "afirmación sin respaldo.",
        "",
    ]

    for motivo in sorted(por_motivo, key=lambda m: -len(por_motivo[m])):
        rechazos = por_motivo[motivo]
        lineas += [
            f"## {motivo} ({len(rechazos)})",
            "",
            explicacion.get(motivo, ""),
            "",
        ]
        for r in rechazos[:40]:
            seccion = f" §{r.seccion}" if r.seccion else ""
            lineas += [
                f"- **{r.codigo}{seccion}** — {r.detalle}",
                f"  - P: {r.pregunta}",
                f"  - R: {r.respuesta[:200]}",
            ]
        if len(rechazos) > 40:
            lineas.append(f"- _...y {len(rechazos) - 40} más_")
        lineas.append("")

    destino.write_text("\n".join(lineas), encoding="utf-8")


def escribir_json(reporte: Reporte, destino: Path) -> None:
    """Set de validacion: pregunta + la cita que deberia producir el asistente."""
    destino.write_text(
        json.dumps(
            {
                "total": len(reporte.entradas),
                "descartadas": len(reporte.rechazos),
                "duplicadas": reporte.duplicadas,
                "clausulas_procesadas": reporte.clausulas_procesadas,
                "entradas": [asdict(e) for e in reporte.entradas],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Genera un FAQ verificado desde el SGC")
    parser.add_argument("--tenant", default=settings.default_tenant_id)
    parser.add_argument("--areas", nargs="*", help="Limitar a estas areas")
    parser.add_argument("--codigos", nargs="*", help="Limitar a estos codigos")
    parser.add_argument("--salida", default="data/faq")
    parser.add_argument(
        "--reanudar", action="store_true", help="Continuar desde el checkpoint"
    )
    parser.add_argument(
        "--solo-informe",
        action="store_true",
        help="No generar: revalidar el checkpoint y rehacer los informes",
    )
    args = parser.parse_args()

    configure_logging("WARNING")  # el progreso ya se imprime; el log solo estorbaria

    destino = Path(args.salida)
    destino.mkdir(parents=True, exist_ok=True)
    checkpoint = destino / f"faq_{args.tenant}.jsonl"

    if args.solo_informe:
        reporte = Reporte()
        registros, _ = leer_checkpoint(checkpoint)
        reporte.clausulas_procesadas = len(registros)
        # En modo informe no se recorre el corpus, asi que el total de candidatas
        # es lo que ya hay en el checkpoint.
        reporte.clausulas_candidatas = len(registros)
        with SessionLocal() as db:
            contenidos = {
                clave_clausula(d.code, c.section): c.content
                for c, d in db.query(DocumentChunk, Document)
                .join(Document, DocumentChunk.document_id == Document.id)
                .filter(Document.tenant_id == args.tenant)
                .all()
            }
        _validar_y_deduplicar(
            reporte, registros, contenidos, _codigos_del_tenant(args.tenant)
        )
    else:
        reporte = generar(
            args.tenant, args.areas, args.codigos, checkpoint, args.reanudar
        )

    if not reporte.entradas:
        print("\nNo se genero ninguna pregunta valida.")
        return 1

    md = destino / f"faq_{args.tenant}.md"
    js = destino / f"faq_{args.tenant}.json"
    rechazos = destino / f"faq_{args.tenant}_rechazos.md"

    escribir_markdown(reporte, md, args.tenant)
    escribir_json(reporte, js)
    escribir_rechazos(reporte, rechazos)

    generadas = len(reporte.entradas) + len(reporte.rechazos) + reporte.duplicadas
    print()
    print(f"Generadas        : {generadas}")
    print(f"  aceptadas      : {len(reporte.entradas)}")
    print(f"  descartadas    : {len(reporte.rechazos)}")
    print(f"  duplicadas     : {reporte.duplicadas}")
    print(f"Clausulas usadas : {reporte.clausulas_procesadas} de {reporte.clausulas_candidatas}")
    if reporte.fallos:
        print(f"Fallos           : {len(reporte.fallos)}")
        for f in reporte.fallos[:5]:
            print(f"  - {f}")
    print()
    for ruta in (md, js, rechazos, checkpoint):
        print(f"  {ruta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
