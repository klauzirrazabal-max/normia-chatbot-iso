"""
Lanza las preguntas del banco contra el backend configurado y devuelve JSON.

Auxiliar de `comparar_estados.py`. Vive en su propio proceso a proposito: el
cliente de LLM se configura por variables de entorno y cachea un singleton, asi
que cambiar de servidor dentro del mismo proceso arrastraria la conexion
anterior y la comparacion mediria otra cosa.
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
from app.models.schemas import IncomingMessage  # noqa: E402
from scripts.benchmark_models import CASOS  # noqa: E402


def descripcion(caso) -> str:
    """Que se espera de este caso, en una linea legible para la presentacion."""
    partes = []
    if caso.herramienta_esperada:
        partes.append(f"llamar a `{caso.herramienta_esperada}`")
    if caso.debe_escalar:
        partes.append("escalar a Calidad")
    if caso.codigos_esperados:
        partes.append(f"citar {', '.join(sorted(caso.codigos_esperados))}")
    if not caso.debe_escalar and not caso.herramienta_esperada:
        partes.append("responder sin escalar")
    return "; ".join(partes)


def acierta(caso, herramienta: str | None, escala: bool, codigos: set[str]) -> bool:
    """
    Criterio unico para los dos estados. Deliberadamente estricto: si el caso
    esperaba una herramienta y no se llamo, es un fallo aunque el texto suene bien.
    """
    if caso.herramienta_esperada and herramienta != caso.herramienta_esperada:
        return False
    if caso.debe_escalar != escala:
        return False
    if caso.debe_citar and caso.codigos_esperados:
        return bool(codigos & {c.upper() for c in caso.codigos_esperados})
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--etiqueta", required=True)
    args = parser.parse_args()

    salida = []
    for i, caso in enumerate(CASOS, start=1):
        print(f"  [{i:2}/{len(CASOS)}] {caso.pregunta[:52]:52}", file=sys.stderr, flush=True)
        with SessionLocal() as db:
            msg = IncomingMessage(
                tenant_id=args.tenant,
                channel="web",
                external_user_id=f"cmp-{args.etiqueta}-{i}",
                text=caso.pregunta,
            )
            try:
                r = orchestrator.handle_message(db, msg)
            except Exception as exc:  # noqa: BLE001 - un fallo puntual no corta el lote
                print(f"      ERROR: {exc}", file=sys.stderr)
                salida.append({
                    "pregunta": caso.pregunta, "esperado": descripcion(caso),
                    "texto": f"(error: {exc})", "herramienta": None, "escala": False,
                    "codigos": [], "acierta": False,
                })
                continue

            # Las herramientas ejecutadas no viajan en BotResponse: el
            # orquestador las deja en `retrieval_debug` del mensaje que acaba de
            # guardar. Es de donde las lee tambien `benchmark_models.py`.
            ultimo = (
                db.query(orchestrator.Message)
                .filter_by(role="assistant")
                .order_by(orchestrator.Message.id.desc())
                .first()
            )
            usadas = ((ultimo.retrieval_debug or {}) if ultimo else {}).get("tools", [])
            herramienta = usadas[0] if usadas else None
            codigos = extract_cited_codes(r.text)
            salida.append({
                "pregunta": caso.pregunta,
                "esperado": descripcion(caso),
                "texto": r.text.strip(),
                "herramienta": herramienta,
                "escala": bool(r.escalate),
                "codigos": sorted(codigos),
                "acierta": acierta(caso, herramienta, bool(r.escalate), codigos),
            })

    print(json.dumps(salida, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
