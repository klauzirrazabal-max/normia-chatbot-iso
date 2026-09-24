"""
Mide un modelo MLX con el banco de pruebas de NormIA, con o sin adaptador LoRA.

Levanta `mlx_lm.server` -- que habla el protocolo compatible con OpenAI, el
mismo que el cliente de NormIA -- espera a que responda, corre
`benchmark_models.py` contra el, y lo apaga. El "antes" y el "despues" salen del
MISMO banco y del MISMO corpus, que es lo unico que hace comparable la medicion.

    # antes
    uv run python scripts/evaluar_adaptador.py --modelo mlx-community/Qwen3-4B-4bit

    # despues
    uv run python scripts/evaluar_adaptador.py \\
        --modelo mlx-community/Qwen3-4B-4bit \\
        --adaptador adapters/qwen3-4b-normia

OJO CON LOS MENSAJES DE SISTEMA: el orquestador emite tres y la plantilla de
chat de Qwen que aplica mlx_lm.server exige uno solo al principio. Por eso este
script enciende `LLM_MERGE_SYSTEM_MESSAGES`, que esta apagado por defecto porque
con Ollama no hace falta.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# El modelo tarda bastante mas que Ollama en prompts de 3-4k tokens, y un
# timeout corto convertiria "lento" en "fallo" y ensuciaria la comparacion.
TIMEOUT_LLM = "180"


def esperar_servidor(url: str, intentos: int = 120) -> bool:
    for _ in range(intentos):
        try:
            with urllib.request.urlopen(url, timeout=3) as r:  # noqa: S310 - localhost
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(2)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modelo", required=True)
    parser.add_argument("--adaptador", default=None, help="ruta del adaptador LoRA")
    parser.add_argument("--tenant", default="empresa-demo-iso")
    parser.add_argument("--puerto", type=int, default=8090)
    parser.add_argument("--salida", type=Path, default=None, help="guarda la salida aqui")
    args = parser.parse_args()

    if args.adaptador and not Path(args.adaptador).exists():
        print(f"no existe el adaptador: {args.adaptador}", file=sys.stderr)
        return 1

    orden = [
        sys.executable, "-m", "mlx_lm", "server",
        "--model", args.modelo,
        "--port", str(args.puerto),
        "--log-level", "WARNING",
    ]
    if args.adaptador:
        orden += ["--adapter-path", args.adaptador]

    etiqueta = "con LoRA" if args.adaptador else "base"
    print(f"Levantando mlx_lm.server ({etiqueta}) en el puerto {args.puerto}...", flush=True)

    servidor = subprocess.Popen(  # noqa: S603 - orden construida aqui
        orden, cwd=RAIZ, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        if not esperar_servidor(f"http://127.0.0.1:{args.puerto}/v1/models"):
            print("el servidor no respondio a tiempo", file=sys.stderr)
            return 1
        print("servidor listo\n", flush=True)

        entorno = {
            **os.environ,
            "LLM_BASE_URL": f"http://127.0.0.1:{args.puerto}/v1",
            "LLM_API_KEY": "mlx",
            "LLM_TIMEOUT_SECONDS": TIMEOUT_LLM,
            # Sin esto la plantilla de Qwen rechaza cada consulta con
            # "System message must be at the beginning".
            "LLM_MERGE_SYSTEM_MESSAGES": "true",
        }
        banco = subprocess.run(  # noqa: S603
            [
                sys.executable, "scripts/benchmark_models.py",
                "--tenant", args.tenant,
                "--modelos", args.modelo,
            ],
            cwd=RAIZ, env=entorno, capture_output=True, text=True,
        )
        salida = banco.stdout + banco.stderr
        print(salida)

        if args.salida:
            cabecera = f"# {etiqueta} · {args.modelo}\n"
            if args.adaptador:
                cabecera += f"# adaptador: {args.adaptador}\n"
            args.salida.write_text(cabecera + salida, encoding="utf-8")
            print(f"guardado en {args.salida}")

        return banco.returncode
    finally:
        os.killpg(os.getpgid(servidor.pid), signal.SIGTERM)
        servidor.wait(timeout=30)


if __name__ == "__main__":
    raise SystemExit(main())
