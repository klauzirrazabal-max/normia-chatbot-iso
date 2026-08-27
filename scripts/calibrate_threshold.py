"""
Calibra RAG_MAX_DISTANCE contra los documentos reales del tenant.

El umbral separa "esto SI esta en mis documentos" de "esto NO esta". Ese corte
depende de tu corpus concreto: no existe un valor universal, y dejarlo mal
puesto rompe el guardrail en las dos direcciones (bot que inventa, o bot que
escala todo).

Uso:
    uv run python scripts/calibrate_threshold.py \
        --dentro "como se calibran los instrumentos" "cada cuanto se audita" \
        --fuera  "cual es la capital de Francia" "receta de pizza"

Sin argumentos usa preguntas fuera-de-alcance genericas y te muestra solo la
distribucion de distancias, para que veas el rango de tu corpus.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.core.rag.embeddings import (
    embed_query,  # noqa: E402
    warmup,  # noqa: E402
)
from app.core.rag.retriever import _fetch_candidates  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402

FUERA_POR_DEFECTO = [
    "cual es la capital de Francia",
    "receta para preparar pizza napolitana",
    "quien gano el mundial de futbol de 2022",
    "como configurar un router wifi en casa",
]


def best_distances(db, tenant_id: str, questions: list[str]) -> list[tuple[str, float | None]]:
    out = []
    for q in questions:
        candidates = _fetch_candidates(db, tenant_id, embed_query(q), 1)
        out.append((q, candidates[0].distance if candidates else None))
    return out


def _print_block(title: str, rows: list[tuple[str, float | None]]) -> list[float]:
    print(f"\n{title}")
    print("-" * len(title))
    values = []
    for question, distance in rows:
        shown = f"{distance:.4f}" if distance is not None else "sin resultados"
        print(f"  {shown:>14}  {question}")
        if distance is not None:
            values.append(distance)
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description="Calibra el umbral de distancia del RAG")
    parser.add_argument("--tenant", default=settings.default_tenant_id)
    parser.add_argument(
        "--dentro",
        nargs="*",
        default=[],
        help="Preguntas que SI deberian responderse con tus documentos",
    )
    parser.add_argument(
        "--fuera",
        nargs="*",
        default=FUERA_POR_DEFECTO,
        help="Preguntas que NO deberian responderse (el bot debe escalar)",
    )
    args = parser.parse_args()

    warmup()

    with SessionLocal() as db:
        dentro = best_distances(db, args.tenant, args.dentro) if args.dentro else []
        fuera = best_distances(db, args.tenant, args.fuera)

    print(f"\nUmbral actual (RAG_MAX_DISTANCE): {settings.rag_max_distance}")

    dentro_vals = (
        _print_block("DENTRO DE ALCANCE (distancia deberia ser BAJA)", dentro) if dentro else []
    )
    fuera_vals = _print_block("FUERA DE ALCANCE (distancia deberia ser ALTA)", fuera)

    print("\n" + "=" * 60)

    if not dentro_vals:
        print(
            "Pasa preguntas con --dentro (usando tus documentos reales) para\n"
            "obtener una recomendacion de umbral. Sin ellas solo veo un lado."
        )
        if fuera_vals:
            print(f"\nMenor distancia fuera-de-alcance: {min(fuera_vals):.4f}")
            print("El umbral debe quedar POR DEBAJO de ese valor.")
        return 0

    peor_dentro = max(dentro_vals)
    mejor_fuera = min(fuera_vals) if fuera_vals else None

    print(f"Peor caso dentro de alcance : {peor_dentro:.4f}  (el umbral debe ser MAYOR)")
    if mejor_fuera is not None:
        print(f"Mejor caso fuera de alcance : {mejor_fuera:.4f}  (el umbral debe ser MENOR)")

    if mejor_fuera is None:
        recomendado = peor_dentro + 0.05
    elif peor_dentro < mejor_fuera:
        recomendado = round(statistics.mean([peor_dentro, mejor_fuera]), 3)
        margen = mejor_fuera - peor_dentro
        print(f"\nHay separacion limpia entre ambos grupos (margen {margen:.4f}).")
    else:
        recomendado = round(peor_dentro + 0.02, 3)
        print(
            "\nATENCION: los dos grupos se solapan. Ningun umbral los separa del todo.\n"
            "Suele significar que faltan documentos que cubran esas preguntas, o que\n"
            "el chunking parte las clausulas en trozos poco especificos."
        )

    print(f"\n  Umbral recomendado -> RAG_MAX_DISTANCE={recomendado}")
    print("  Ponlo en tu .env y reinicia el backend.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
