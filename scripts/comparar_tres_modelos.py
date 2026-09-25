"""
Los tres estados DEL MODELO, como en el taller de la sesion 2.

  1. SIN ENTRENAMIENTO  Qwen3-4B-Base. Los pesos antes de cualquier
                        post-entrenamiento. Solo sabe continuar texto: no
                        conversa, no obedece, no termina. Se le pregunta SIN
                        plantilla de chat, porque no tiene.
  2. CON ENTRENAMIENTO  Qwen3-4B (Instruct). Misma arquitectura y tamano, pero
                        paso por SFT y alineacion. Conversa y estructura --
                        pero no sabe nada del SGC del cliente.
  3. CON FINE-TUNING    Instruct + el adaptador QLoRA de este proyecto.

Es el eje que pide el enunciado: cuanto post-entrenamiento tiene el modelo. Es
DISTINTO del que mide `comparar_tres_estados.py`, que varia cuanto andamiaje
tiene el sistema (sin RAG / con RAG / RAG+LoRA). Los dos son utiles y se
presentan juntos.

Aqui los tres estados corren en igualdad de condiciones: **sin RAG**, como en el
taller. Asi se ve lo que aporta el modelo por si mismo, sin que la recuperacion
tape la diferencia.

    uv run python scripts/comparar_tres_modelos.py \\
        --base models/Qwen3-4B-Base-4bit \\
        --instruct mlx-community/Qwen3-4B-4bit \\
        --adaptador adapters/qwen3-4b-normia
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from app.core import orchestrator  # noqa: E402
from app.core.guardrails.grounding_check import extract_cited_codes  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.db_models import Document  # noqa: E402
from scripts.benchmark_models import CASOS  # noqa: E402

ETIQUETAS = [
    ("base", "Sin entrenamiento"),
    ("instruct", "Con entrenamiento"),
    ("lora", "Con fine-tuning"),
]


def codigos_del_sgc(tenant: str) -> set[str]:
    with SessionLocal() as db:
        return {
            code.split(" (")[0].upper()
            for (code,) in db.query(Document.code).filter_by(tenant_id=tenant).distinct()
        }


def prompt_sistema(tenant: str) -> str:
    with SessionLocal() as db:
        return orchestrator._system_prompt(db, tenant)


def responder(modelo, tok, pregunta: str, sistema: str, crudo: bool, max_tokens: int) -> str:
    """
    `crudo=True` es el estado 1: sin plantilla de chat, porque el modelo base no
    tiene. Se le da la pregunta como texto y continua. Es exactamente lo que hace
    el taller, y lo que revela que un modelo base no conversa.
    """
    from mlx_lm import generate

    if crudo:
        entrada = f"{pregunta}\n"
    else:
        entrada = tok.apply_chat_template(
            [{"role": "system", "content": sistema}, {"role": "user", "content": pregunta}],
            add_generation_prompt=True,
            tokenize=False,
        )
    return generate(modelo, tok, prompt=entrada, max_tokens=max_tokens, verbose=False).strip()


def medir(caso, respuesta: str, reales: set[str]) -> dict:
    citados = extract_cited_codes(respuesta)
    fantasmas = citados - reales
    return {
        "pregunta": caso.pregunta,
        "texto": respuesta,
        "codigos": sorted(citados),
        "fantasmas": sorted(fantasmas),
        "cita_correcta": (
            bool(caso.codigos_esperados & citados)
            if caso.debe_citar and caso.codigos_esperados else None
        ),
        "sin_fantasmas": not fantasmas,
        "caracteres": len(respuesta),
    }


def resumen(filas: list[dict]) -> dict[str, str]:
    def pct(vals):
        v = [x for x in vals if x is not None]
        return f"{100 * sum(v) / len(v):.0f}%" if v else "n/a"

    import statistics
    return {
        "Cita el documento correcto": pct([f["cita_correcta"] for f in filas]),
        "Sin citas inventadas": pct([f["sin_fantasmas"] for f in filas]),
        "Longitud mediana": f"{statistics.median(f['caracteres'] for f in filas):.0f}",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="ruta del modelo SIN post-entrenamiento")
    parser.add_argument("--instruct", required=True)
    parser.add_argument("--adaptador", required=True)
    parser.add_argument("--tenant", default="empresa-demo-iso")
    parser.add_argument("--max-tokens", type=int, default=220)
    args = parser.parse_args()

    from mlx_lm import load

    reales = codigos_del_sgc(args.tenant)
    sistema = prompt_sistema(args.tenant)
    estados: dict[str, list[dict]] = {}

    for clave, ruta, adaptador, crudo in (
        ("base", args.base, None, True),
        ("instruct", args.instruct, None, False),
        ("lora", args.instruct, args.adaptador, False),
    ):
        etiqueta = dict(ETIQUETAS)[clave]
        print(f"── {etiqueta} ──", flush=True)
        modelo, tok = load(ruta, adapter_path=adaptador)
        filas = []
        for i, caso in enumerate(CASOS, start=1):
            print(f"  [{i:2}/{len(CASOS)}] {caso.pregunta[:50]:50}", flush=True)
            filas.append(medir(caso, responder(
                modelo, tok, caso.pregunta, sistema, crudo, args.max_tokens
            ), reales))
        estados[clave] = filas
        del modelo, tok

    resumenes = {k: resumen(v) for k, v in estados.items()}
    destino = RAIZ / "docs" / f"tres-modelos-{args.tenant}"
    destino.with_suffix(".json").write_text(
        json.dumps({"resumen": resumenes, "detalle": estados}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )

    metricas = list(resumenes["instruct"])
    lineas = [
        "# Los tres estados del modelo",
        "",
        "Las mismas 12 preguntas, **sin RAG** en los tres casos, como en el taller.",
        "Asi se ve lo que aporta el modelo por si mismo.",
        "",
        "| Metrica | " + " | ".join(t for _, t in ETIQUETAS) + " |",
        "|---|" + "---|" * len(ETIQUETAS),
    ]
    for m in metricas:
        fila = " | ".join(resumenes[k].get(m, "n/a") for k, _ in ETIQUETAS)
        lineas.append(f"| {m} | {fila} |")

    lineas += ["", "## Una respuesta de cada estado", ""]
    for k, t in ETIQUETAS:
        lineas += [f"**{t}** — «{estados[k][0]['pregunta']}»", "",
                   "> " + (estados[k][0]["texto"][:400] or "(vacio)").replace("\n", "\n> "), ""]
    destino.with_suffix(".md").write_text("\n".join(lineas) + "\n", encoding="utf-8")

    ancho = max(len(m) for m in metricas)
    print()
    print(f"  {'':<{ancho}}" + "".join(f"{t:>21}" for _, t in ETIQUETAS))
    for m in metricas:
        celdas = "".join(f"{resumenes[k].get(m, 'n/a'):>21}" for k, _ in ETIQUETAS)
        print(f"  {m:<{ancho}}{celdas}")
    print(f"\n  {destino}.{{json,md}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
