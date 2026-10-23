"""
Las mismas preguntas en TRES estados, para un sistema con RAG.

  1. SIN RAG     el modelo solo, con el prompt de sistema y la pregunta.
                 Sin documentos, sin herramientas, sin guardrails.
  2. CON RAG     NormIA tal como esta hoy: recuperacion, herramientas y los
                 cuatro guardrails.
  3. RAG + LoRA  lo mismo, con el adaptador entrenado.

Sirve para separar QUE aporta cada pieza. El fine-tuning se suele vender como
la respuesta a todo; aqui se ve cuanto de la mejora es del RAG y cuanto del
adaptador, que casi nunca es lo que uno espera.

    uv run python scripts/comparar_tres_estados.py \\
        --modelo mlx-community/Qwen3-4B-4bit \\
        --adaptador adapters/qwen3-4b-normia \\
        --tenant empresa-demo-iso

Escribe docs/tres-estados-<tenant>.json y .md.

AVISO SOBRE LOS DATOS: corrido contra un tenant real, el .md reproduce contenido
de sus clausulas y NO debe versionarse -- el .gitignore ya lo cubre. Los
PORCENTAJES y los codigos INVENTADOS si son publicables: un codigo inventado no
existe en ningun SGC, por definicion.
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


def lote(puerto: int, modelo: str, tenant: str, estado: str) -> list[dict]:
    """Un subproceso por estado: el cliente cachea un singleton por entorno."""
    entorno = {
        **os.environ,
        "LLM_BASE_URL": f"http://127.0.0.1:{puerto}/v1",
        "LLM_API_KEY": "mlx",
        "LLM_MODEL": modelo,
        "LLM_TIMEOUT_SECONDS": TIMEOUT_LLM,
        "LLM_MERGE_SYSTEM_MESSAGES": "true",
    }
    proc = subprocess.run(  # noqa: S603
        [
            sys.executable, str(RAIZ / "scripts" / "_preguntar_lote.py"),
            "--tenant", tenant, "--etiqueta", estado, "--estado", estado,
        ],
        cwd=RAIZ, env=entorno, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        print(proc.stdout + proc.stderr, file=sys.stderr)
        raise RuntimeError(f"fallo el lote de {estado}")
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


ETIQUETAS = {"sin_rag": "Sin RAG", "rag": "Con RAG", "rag_lora": "RAG + LoRA"}


def resumen(filas: list[dict]) -> dict[str, str]:
    """Los mismos criterios que el banco, para que las cifras sean comparables."""
    def pct(vals):
        v = [x for x in vals if x is not None]
        return f"{100 * sum(v) / len(v):.0f}%" if v else "n/a"

    return {
        "Cita el documento correcto": pct([f["cita_correcta"] for f in filas]),
        "Sin citas inventadas": pct([f["sin_fantasmas"] for f in filas]),
        "Usa la herramienta correcta": pct([f["herramienta_correcta"] for f in filas]),
        "Escala cuando debe": pct([f["escalacion_correcta"] for f in filas]),
        "Acierta en conjunto": pct([f["acierta"] for f in filas]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modelo", required=True)
    parser.add_argument("--adaptador", required=True)
    parser.add_argument("--tenant", default="empresa-demo-iso")
    parser.add_argument("--puerto", type=int, default=8091)
    args = parser.parse_args()

    if not Path(args.adaptador).exists():
        print(f"no existe el adaptador: {args.adaptador}", file=sys.stderr)
        return 1

    estados: dict[str, list[dict]] = {}

    # 1 y 2 comparten servidor: es el mismo modelo sin adaptador, y levantarlo
    # dos veces solo anade un minuto de carga.
    def sin_adaptador():
        print("── 1. SIN RAG ──", flush=True)
        estados["sin_rag"] = lote(args.puerto, args.modelo, args.tenant, "sin_rag")
        print("── 2. CON RAG ──", flush=True)
        estados["rag"] = lote(args.puerto, args.modelo, args.tenant, "rag")

    con_servidor(args.modelo, None, args.puerto, sin_adaptador)

    print("── 3. RAG + LoRA ──", flush=True)
    con_servidor(
        args.modelo, args.adaptador, args.puerto,
        lambda: estados.__setitem__(
            "rag_lora", lote(args.puerto, args.modelo, args.tenant, "rag_lora")
        ),
    )

    resumenes = {k: resumen(v) for k, v in estados.items()}
    destino = RAIZ / "docs"
    (destino / f"tres-estados-{args.tenant}.json").write_text(
        json.dumps(
            {"modelo": args.modelo, "adaptador": args.adaptador,
             "tenant": args.tenant, "resumen": resumenes, "detalle": estados},
            ensure_ascii=False, indent=1,
        ),
        encoding="utf-8",
    )

    claves = list(ETIQUETAS)
    lineas = [
        f"# Tres estados — {args.tenant}",
        "",
        f"Modelo: `{args.modelo}` · Adaptador: `{args.adaptador}`",
        "",
        "| Métrica | " + " | ".join(ETIQUETAS[k] for k in claves) + " |",
        "|---|" + "---|" * len(claves),
    ]
    for metrica in resumen(estados["rag"]):
        fila = " | ".join(resumenes[k].get(metrica, "n/a") for k in claves)
        lineas.append(f"| {metrica} | {fila} |")

    lineas += [
        "",
        "## Como leer la fila de herramientas",
        "",
        "El **0 % sin RAG es estructural, no un fallo del modelo**: en ese estado no se",
        "le pasan herramientas, asi que no puede llamarlas. Esa fila mide lo que aporta",
        "el sistema completo, no la capacidad del modelo.",
        "",
        "Las filas que SI comparan al modelo consigo mismo son las de cita, citas",
        "inventadas y escalacion.",
        "",
        "## Los códigos que se inventó sin RAG",
        "",
    ]
    inventados = sorted({c for f in estados["sin_rag"] for c in f["fantasmas"]})
    if inventados:
        lineas.append(
            "Ninguno existe en el SGC. Es lo que hace un modelo al que se le "
            "pregunta por documentos que no ha visto:"
        )
        lineas += ["", "```", ", ".join(inventados), "```"]
    else:
        lineas.append("Ninguno: no cito ningun codigo inexistente.")
    (destino / f"tres-estados-{args.tenant}.md").write_text(
        "\n".join(lineas) + "\n", encoding="utf-8"
    )

    print()
    ancho = max(len(m) for m in resumenes["rag"])
    print(f"  {'':<{ancho}}  " + "".join(f"{ETIQUETAS[k]:>14}" for k in claves))
    for metrica in resumenes["rag"]:
        print(f"  {metrica:<{ancho}}  " + "".join(
            f"{resumenes[k].get(metrica, 'n/a'):>14}" for k in claves
        ))
    print(f"\n  codigos inventados sin RAG: {len(inventados)}")
    print(f"  docs/tres-estados-{args.tenant}.{{json,md}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
