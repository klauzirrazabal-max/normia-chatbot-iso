"""
Lanza las preguntas del banco contra el backend configurado y devuelve JSON.

Auxiliar de `comparar_estados.py` y `comparar_tres_estados.py`. Vive en su
propio proceso a proposito: el cliente de LLM se configura por variables de
entorno y cachea un singleton, asi que cambiar de servidor dentro del mismo
proceso arrastraria la conexion anterior y la comparacion mediria otra cosa.

Dos caminos:

  --estado rag | rag_lora   el camino completo de NormIA: recuperacion,
                            herramientas y los cuatro guardrails.
  --estado sin_rag          el modelo a pelo, con el prompt de sistema del
                            tenant y la pregunta. Sin documentos, sin
                            herramientas, sin guardrails.

`sin_rag` es el unico que NO pasa por el orquestador, y tiene que ser asi: si
pasara, los guardrails harian su trabajo y no se veria lo que el modelo hace
cuando nadie lo frena. Que es justamente lo que hay que ensenar.
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
from app.models.schemas import IncomingMessage  # noqa: E402
from app.services.llm_client import LLMError, get_llm_client  # noqa: E402
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


def codigos_del_sgc(tenant: str) -> set[str]:
    with SessionLocal() as db:
        return {
            code.split(" (")[0].upper()
            for (code,) in db.query(Document.code).filter_by(tenant_id=tenant).distinct()
        }


def preguntar_sin_rag(texto: str, tenant: str) -> str:
    """
    El modelo solo: prompt de sistema del tenant + pregunta. Nada mas.

    Se usa el MISMO prompt de sistema que en los otros dos estados para que la
    unica diferencia sea el RAG. Sin herramientas y sin contexto documental, lo
    que conteste sale de sus pesos.
    """
    with SessionLocal() as db:
        prompt = orchestrator._system_prompt(db, tenant)
    try:
        r = get_llm_client().generate(
            [{"role": "system", "content": prompt}, {"role": "user", "content": texto}]
        )
    except LLMError as exc:
        return f"(error: {exc})"
    return r["content"].strip()


def medir(caso, respuesta: str, herramientas: list[str], escalo: bool, reales: set[str]) -> dict:
    """Los mismos criterios que `benchmark_models.py`, para poder comparar cifras."""
    citados = extract_cited_codes(respuesta)
    fantasmas = citados - reales

    cita_correcta = (
        bool(caso.codigos_esperados & citados)
        if caso.debe_citar and caso.codigos_esperados
        else None
    )
    herramienta_correcta = (
        (caso.herramienta_esperada in herramientas)
        if caso.herramienta_esperada is not None
        else None
    )
    escalacion_correcta = escalo == caso.debe_escalar
    acierta = (
        cita_correcta is not False
        and herramienta_correcta is not False
        and escalacion_correcta
        and not fantasmas
    )
    return {
        "pregunta": caso.pregunta,
        "esperado": descripcion(caso),
        "texto": respuesta,
        "herramienta": herramientas[0] if herramientas else None,
        "escala": escalo,
        "codigos": sorted(citados),
        "fantasmas": sorted(fantasmas),
        "cita_correcta": cita_correcta,
        "herramienta_correcta": herramienta_correcta,
        "escalacion_correcta": escalacion_correcta,
        "sin_fantasmas": not fantasmas,
        "acierta": acierta,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--etiqueta", required=True)
    parser.add_argument("--estado", default="rag", choices=["sin_rag", "rag", "rag_lora"])
    args = parser.parse_args()

    reales = codigos_del_sgc(args.tenant)
    salida = []

    for i, caso in enumerate(CASOS, start=1):
        print(f"  [{i:2}/{len(CASOS)}] {caso.pregunta[:52]:52}", file=sys.stderr, flush=True)

        if args.estado == "sin_rag":
            salida.append(medir(caso, preguntar_sin_rag(caso.pregunta, args.tenant),
                                [], False, reales))
            continue

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
                salida.append(medir(caso, f"(error: {exc})", [], False, reales))
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
            salida.append(medir(caso, r.text.strip(), usadas, bool(r.escalate), reales))

    print(json.dumps(salida, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
