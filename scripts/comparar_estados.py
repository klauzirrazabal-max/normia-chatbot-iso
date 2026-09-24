"""
La misma pregunta, al modelo SIN entrenar y al entrenado. Lado a lado.

Es la comparacion del taller de la sesion 2 aplicada a NormIA: no un numero
agregado, sino las respuestas literales, para poder leer que cambio.

Las preguntas NO salen del aire: son las del banco de pruebas
(`scripts/benchmark_models.py`), que estan excluidas del dataset de
entrenamiento. El modelo afinado no las ha visto nunca -- si acierta, generaliza.

    uv run python scripts/comparar_estados.py \\
        --modelo mlx-community/Qwen3-4B-4bit \\
        --adaptador adapters/qwen3-4b-normia

Escribe docs/comparacion-lora.md (para leer) y docs/comparacion-lora.json (para
la presentacion).
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

TIMEOUT_LLM = "180"


def esperar(url: str, intentos: int = 120) -> bool:
    for _ in range(intentos):
        try:
            with urllib.request.urlopen(url, timeout=3) as r:  # noqa: S310 - localhost
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(2)
    return False


def preguntar_todo(puerto: int, modelo: str, tenant: str, etiqueta: str) -> list[dict]:
    """
    Lanza las preguntas contra el servidor ya levantado.

    Corre en un SUBPROCESO porque el cliente de NormIA se configura por entorno y
    cachea un singleton: cambiar de servidor en el mismo proceso arrastraria la
    conexion anterior.
    """
    guion = RAIZ / "scripts" / "_preguntar_lote.py"
    entorno = {
        **os.environ,
        "LLM_BASE_URL": f"http://127.0.0.1:{puerto}/v1",
        "LLM_API_KEY": "mlx",
        "LLM_MODEL": modelo,
        "LLM_TIMEOUT_SECONDS": TIMEOUT_LLM,
        # El dataset se construyo con los mensajes de sistema fundidos, porque la
        # plantilla de entrenamiento lo exige. La inferencia tiene que fundirlos
        # tambien o el adaptador no transfiere.
        "LLM_MERGE_SYSTEM_MESSAGES": "true",
    }
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(guion), "--tenant", tenant, "--etiqueta", etiqueta],
        cwd=RAIZ, env=entorno, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        print(proc.stdout + proc.stderr, file=sys.stderr)
        raise RuntimeError(f"fallo el lote de {etiqueta}")
    return json.loads(proc.stdout)


def con_servidor(modelo: str, adaptador: str | None, puerto: int, fn):
    orden = [
        sys.executable, "-m", "mlx_lm", "server",
        "--model", modelo, "--port", str(puerto), "--log-level", "WARNING",
    ]
    if adaptador:
        orden += ["--adapter-path", adaptador]

    proc = subprocess.Popen(  # noqa: S603
        orden, cwd=RAIZ, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        if not esperar(f"http://127.0.0.1:{puerto}/v1/models"):
            raise RuntimeError("el servidor no respondio a tiempo")
        return fn()
    finally:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=30)


def escribir_markdown(destino: Path, modelo: str, adaptador: str, pares: list[tuple]) -> None:
    lineas = [
        "# Sin entrenar contra entrenado",
        "",
        f"Modelo base: `{modelo}`  ",
        f"Adaptador LoRA: `{adaptador}`",
        "",
        "Las preguntas son las del banco de pruebas, **excluidas del dataset de",
        "entrenamiento**: el modelo afinado no las ha visto nunca.",
        "",
    ]
    v = lambda ok: "acierta" if ok else "falla"  # noqa: E731
    aciertos_base = aciertos_lora = 0
    for i, (base, lora) in enumerate(pares, start=1):
        assert base["pregunta"] == lora["pregunta"]
        esperado = base["esperado"]
        ok_b, ok_l = base["acierta"], lora["acierta"]
        aciertos_base += ok_b
        aciertos_lora += ok_l

        hb = base["herramienta"] or "ninguna"
        hl = lora["herramienta"] or "ninguna"
        lineas += [
            f"## {i}. {base['pregunta']}",
            "",
            f"*Lo correcto:* {esperado}",
            "",
            "| | Sin entrenar | Con LoRA |",
            "|---|---|---|",
            f"| Veredicto | {v(ok_b)} | {v(ok_l)} |",
            f"| Herramienta | `{hb}` | `{hl}` |",
            f"| Escala | {base['escala']} | {lora['escala']} |",
            f"| Cita | {', '.join(base['codigos']) or '—'} | {', '.join(lora['codigos']) or '—'} |",
            "",
            "**Sin entrenar:**",
            "",
            f"> {base['texto'] or '(sin texto)'}",
            "",
            "**Con LoRA:**",
            "",
            f"> {lora['texto'] or '(sin texto)'}",
            "",
        ]

    total = len(pares)
    # Tras el parrafo de introduccion, que ocupa las lineas 0-7. Insertarlo antes
    # lo partia por la mitad.
    lineas[8:8] = [
        f"**Resultado: {aciertos_base}/{total} sin entrenar, {aciertos_lora}/{total} con LoRA.**",
        "",
    ]
    destino.write_text("\n".join(lineas), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modelo", required=True)
    parser.add_argument("--adaptador", required=True)
    parser.add_argument("--tenant", default="empresa-demo-iso")
    parser.add_argument("--puerto", type=int, default=8090)
    args = parser.parse_args()

    if not Path(args.adaptador).exists():
        print(f"no existe el adaptador: {args.adaptador}", file=sys.stderr)
        return 1

    print("── SIN ENTRENAR ──", flush=True)
    base = con_servidor(
        args.modelo, None, args.puerto,
        lambda: preguntar_todo(args.puerto, args.modelo, args.tenant, "base"),
    )

    print("── CON LoRA ──", flush=True)
    lora = con_servidor(
        args.modelo, args.adaptador, args.puerto,
        lambda: preguntar_todo(args.puerto, args.modelo, args.tenant, "lora"),
    )

    pares = list(zip(base, lora, strict=True))
    docs = RAIZ / "docs"
    escribir_markdown(docs / "comparacion-lora.md", args.modelo, args.adaptador, pares)
    (docs / "comparacion-lora.json").write_text(
        json.dumps(
            {"modelo": args.modelo, "adaptador": args.adaptador,
             "pares": [{"base": b, "lora": a} for b, a in pares]},
            ensure_ascii=False, indent=1,
        ),
        encoding="utf-8",
    )

    ab = sum(b["acierta"] for b, _ in pares)
    al = sum(a["acierta"] for _, a in pares)
    print(f"\n  sin entrenar : {ab}/{len(pares)}")
    print(f"  con LoRA     : {al}/{len(pares)}")
    print("\n  docs/comparacion-lora.md  ·  docs/comparacion-lora.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
