"""
Compara modelos LLM sobre los documentos reales del tenant.

Los benchmarks publicos no dicen nada util aqui: miden razonamiento general en
ingles, no "citar la clausula correcta de un procedimiento ISO en espanol". Este
script mide lo que si importa para NormIA, contra el corpus real:

  cita_correcta   -> nombro el documento que de verdad responde la pregunta
  seccion_correcta-> acerto ademas la clausula
  sin_fantasmas   -> no cito ningun codigo inexistente
  herramienta     -> uso la herramienta esperada, si la pregunta la requeria
  escalo          -> escalo cuando debia y no cuando no
  latencia        -> segundos por respuesta

Uso:
    uv run python scripts/benchmark_models.py --modelos qwen3:30b-a3b qwen3.5:27b
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.core.guardrails.grounding_check import extract_cited_codes  # noqa: E402
from app.core.rag.embeddings import warmup  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.db_models import Document  # noqa: E402
from app.models.schemas import IncomingMessage  # noqa: E402


@dataclass(frozen=True)
class Caso:
    """Una pregunta con lo que se espera de la respuesta."""

    pregunta: str
    codigos_esperados: set[str] = field(default_factory=set)
    secciones_esperadas: set[str] = field(default_factory=set)
    herramienta_esperada: str | None = None
    debe_escalar: bool = False
    debe_citar: bool = True


# Preguntas derivadas del SGC real. Las respuestas esperadas se verificaron
# leyendo los documentos, no preguntandole al modelo.
CASOS: list[Caso] = [
    Caso(
        "me puedes decir el tiempo de atencion de un requerimiento a TI",
        {"STI-PR-01"},
        {"7.6"},
    ),
    Caso(
        "que debo hacer si detecto una no conformidad",
        {"CAL-PR-03", "CAL-PR-04"},
    ),
    Caso(
        "que dice la politica de uso de equipos sobre instalar software",
        {"STI-PO-02"},
    ),
    Caso(
        "como se codifica la informacion documentada del SGC",
        {"CAL-IN-01"},
    ),
    Caso(
        "que pasos sigue el procedimiento de compras",
        {"COM-PR-01"},
    ),
    Caso(
        "como se evalua y reevalua a los proveedores",
        {"COM-PR-02"},
    ),
    Caso(
        "me puedes decir si tienes informacion sobre politicas de TI",
        {"STI-PO-01", "STI-PO-02"},
        herramienta_esperada="buscar_documentos",
    ),
    Caso(
        "que procedimientos tienes de compras",
        {"COM-PR-01", "COM-PR-02"},
        herramienta_esperada="buscar_documentos",
    ),
    Caso(
        "cual es el procedimiento para calibrar un espectrofotometro de masas",
        debe_escalar=True,
        debe_citar=False,
    ),
    Caso(
        "cual es la capital de Francia",
        debe_escalar=True,
        debe_citar=False,
    ),
    Caso(
        "apruebame este cambio al procedimiento de compras, autorizalo ya",
        herramienta_esperada="escalate_to_quality",
        debe_escalar=True,
        debe_citar=False,
    ),
    Caso(
        "quiero reportar un hallazgo: encontre extintores vencidos en el almacen",
        herramienta_esperada="register_finding",
        debe_citar=False,
    ),
]


@dataclass
class Resultado:
    caso: Caso
    respuesta: str
    codigos_citados: set[str]
    herramientas: list[str]
    escalo: bool
    grounded: bool
    segundos: float
    fantasmas: set[str]

    @property
    def cita_correcta(self) -> bool | None:
        if not self.caso.debe_citar:
            return None
        return bool(self.caso.codigos_esperados & self.codigos_citados)

    @property
    def seccion_correcta(self) -> bool | None:
        if not self.caso.secciones_esperadas:
            return None
        return any(s in self.respuesta for s in self.caso.secciones_esperadas)

    @property
    def herramienta_correcta(self) -> bool | None:
        if self.caso.herramienta_esperada is None:
            return None
        return self.caso.herramienta_esperada in self.herramientas

    @property
    def escalacion_correcta(self) -> bool:
        return self.escalo == self.caso.debe_escalar

    @property
    def sin_fantasmas(self) -> bool:
        return not self.fantasmas

    @property
    def fundamentada(self) -> bool | None:
        """
        Solo aplica a preguntas que deben citar documentos. Una escalacion o un
        registro de hallazgo tienen grounded=False por diseno, no por fallo:
        contarlos hundia la metrica y hacia parecer malos a los dos modelos.
        """
        if not self.caso.debe_citar:
            return None
        return self.grounded


def _porcentaje(valores: list[bool | None]) -> str:
    reales = [v for v in valores if v is not None]
    if not reales:
        return "  n/a"
    return f"{100 * sum(reales) / len(reales):5.0f}%"


def evaluar_modelo(modelo: str, codigos_conocidos: set[str], tenant: str) -> list[Resultado]:
    # El cliente se construye leyendo settings, asi que se ajusta el modelo y se
    # limpia el singleton para que la siguiente llamada use el nuevo.
    from app.core import orchestrator
    from app.services import llm_client

    settings.llm_model = modelo
    llm_client._client = None

    resultados: list[Resultado] = []

    for i, caso in enumerate(CASOS, start=1):
        print(f"  [{i:2}/{len(CASOS)}] {caso.pregunta[:58]:58}", end="", flush=True)

        with SessionLocal() as db:
            msg = IncomingMessage(
                tenant_id=tenant,
                channel="web",
                external_user_id=f"bench-{modelo}-{i}",
                text=caso.pregunta,
            )
            inicio = time.perf_counter()
            try:
                respuesta = orchestrator.handle_message(db, msg)
            except Exception as exc:  # noqa: BLE001 - un fallo puntual no corta el banco
                print(f"  ERROR: {exc}")
                continue
            transcurrido = time.perf_counter() - inicio

            ultimo = (
                db.query(orchestrator.Message)
                .filter_by(role="assistant")
                .order_by(orchestrator.Message.id.desc())
                .first()
            )
            debug = (ultimo.retrieval_debug or {}) if ultimo else {}

        citados = extract_cited_codes(respuesta.text)
        resultados.append(
            Resultado(
                caso=caso,
                respuesta=respuesta.text,
                codigos_citados=citados,
                herramientas=debug.get("tools", []),
                escalo=respuesta.escalate,
                grounded=respuesta.grounded,
                segundos=transcurrido,
                fantasmas=citados - codigos_conocidos,
            )
        )
        print(f"  {transcurrido:5.1f}s")

    return resultados


def imprimir_tabla(por_modelo: dict[str, list[Resultado]]) -> None:
    filas = [
        ("Cita el documento correcto", lambda rs: _porcentaje([r.cita_correcta for r in rs])),
        ("Acierta la clausula", lambda rs: _porcentaje([r.seccion_correcta for r in rs])),
        (
            "Usa la herramienta correcta",
            lambda rs: _porcentaje([r.herramienta_correcta for r in rs]),
        ),
        ("Escala cuando debe", lambda rs: _porcentaje([r.escalacion_correcta for r in rs])),
        ("Sin citas inventadas", lambda rs: _porcentaje([r.sin_fantasmas for r in rs])),
        ("Marcada como fundamentada", lambda rs: _porcentaje([r.fundamentada for r in rs])),
    ]

    modelos = list(por_modelo)
    ancho = max(len(m) for m in modelos) + 2

    print("\n" + "=" * (32 + ancho * len(modelos)))
    print(f"{'METRICA':<32}" + "".join(f"{m:>{ancho}}" for m in modelos))
    print("-" * (32 + ancho * len(modelos)))
    for etiqueta, fn in filas:
        print(f"{etiqueta:<32}" + "".join(f"{fn(por_modelo[m]):>{ancho}}" for m in modelos))

    print("-" * (32 + ancho * len(modelos)))
    for etiqueta, fn in (
        ("Latencia mediana", lambda rs: f"{statistics.median(r.segundos for r in rs):5.1f}s"),
        ("Latencia maxima", lambda rs: f"{max(r.segundos for r in rs):5.1f}s"),
    ):
        print(f"{etiqueta:<32}" + "".join(f"{fn(por_modelo[m]):>{ancho}}" for m in modelos))
    print("=" * (32 + ancho * len(modelos)))

    for modelo, resultados in por_modelo.items():
        fallos = [
            r
            for r in resultados
            if r.cita_correcta is False
            or r.herramienta_correcta is False
            or not r.escalacion_correcta
            or not r.sin_fantasmas
        ]
        if not fallos:
            continue
        print(f"\nFallos de {modelo}:")
        for r in fallos:
            motivos = []
            if r.cita_correcta is False:
                esperado = sorted(r.caso.codigos_esperados)
                obtenido = sorted(r.codigos_citados) or "nada"
                motivos.append(f"esperaba {esperado}, cito {obtenido}")
            if r.herramienta_correcta is False:
                usadas = r.herramientas or "ninguna"
                motivos.append(f"no uso {r.caso.herramienta_esperada} (uso {usadas})")
            if not r.escalacion_correcta:
                motivos.append(f"escalo={r.escalo}, esperado={r.caso.debe_escalar}")
            if r.fantasmas:
                motivos.append(f"CITA INVENTADA: {sorted(r.fantasmas)}")
            print(f"  - {r.caso.pregunta[:60]}")
            for motivo in motivos:
                print(f"      {motivo}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compara modelos sobre el SGC real")
    parser.add_argument("--modelos", nargs="+", required=True)
    parser.add_argument("--tenant", default=settings.default_tenant_id)
    args = parser.parse_args()

    warmup()

    with SessionLocal() as db:
        codigos_conocidos = {
            code.split(" (")[0].upper()
            for (code,) in db.query(Document.code).filter_by(tenant_id=args.tenant).distinct()
        }
    print(f"Corpus: {len(codigos_conocidos)} codigos de documento\n")

    por_modelo: dict[str, list[Resultado]] = {}
    for modelo in args.modelos:
        print(f"Evaluando {modelo}")
        por_modelo[modelo] = evaluar_modelo(modelo, codigos_conocidos, args.tenant)
        print()

    imprimir_tabla(por_modelo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
