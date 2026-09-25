"""
Un comando. Entrena un adaptador QLoRA y ensena la diferencia.

    uv run python scripts/probar_finetuning.py

Hace todo lo necesario y en orden: prepara el corpus sintetico que viaja en el
repositorio, entrena el adaptador y mide los tres estados del modelo. No hace
falta ningun documento del cliente -- no hay ninguno en este repositorio.

Unos 40 minutos en un Mac con chip de la serie M. Es reanudable: si un paso ya
esta hecho, lo salta. Se puede parar con Ctrl-C y volver a lanzarlo.

    --rapido      la mitad de pasos de entrenamiento (~15 min), para ver la
                  cadena funcionar sin esperar la corrida completa.
    --desde PASO  empieza en ese paso, para repetir solo una parte.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PY = sys.executable
TENANT = "demo-publica"
ADAPTADOR = RAIZ / "adapters" / "qwen3-4b-demo"
BASE = "mlx-community/Qwen3-4B-4bit"


def titulo(n: int, total: int, texto: str) -> None:
    print(f"\n\033[1m[{n}/{total}] {texto}\033[0m", flush=True)


def correr(orden: list[str], descripcion: str) -> None:
    print(f"      $ {' '.join(orden[1:])}", flush=True)
    r = subprocess.run(orden, cwd=RAIZ)  # noqa: S603 - ordenes construidas aqui
    if r.returncode != 0:
        print(f"\n  FALLO: {descripcion}", file=sys.stderr)
        raise SystemExit(r.returncode)


def hay_docker() -> bool:
    return subprocess.run(  # noqa: S603
        ["docker", "info"], capture_output=True  # noqa: S607
    ).returncode == 0


def base_responde() -> bool:
    try:
        sys.path.insert(0, str(RAIZ))
        from app.db.session import SessionLocal

        with SessionLocal() as db:
            db.execute(__import__("sqlalchemy").text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 - cualquier fallo significa "no lista"
        return False


def esperar_base(intentos: int = 30) -> bool:
    for _ in range(intentos):
        if base_responde():
            return True
        time.sleep(2)
    return False


def comprobar_requisitos() -> None:
    faltan = []
    if not shutil.which("docker") or not hay_docker():
        faltan.append(
            "Docker no responde. Abre Docker Desktop y vuelve a lanzarlo."
        )
    if not (RAIZ / "data" / "faq" / f"faq_{TENANT}.json").exists():
        faltan.append(f"falta data/faq/faq_{TENANT}.json (deberia venir en el repositorio)")
    if faltan:
        print("\n  No se puede continuar:\n")
        for f in faltan:
            print(f"    - {f}")
        raise SystemExit(1)

    try:
        import mlx  # noqa: F401
    except ImportError:
        print("\n  Falta MLX. Instalalo con:\n\n    uv sync --group finetune\n")
        raise SystemExit(1) from None


def puerto_libre(puerto: int) -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{puerto}", timeout=1)  # noqa: S310
        return False
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rapido", action="store_true")
    parser.add_argument("--desde", type=int, default=1)
    parser.add_argument(
        "--rehacer", action="store_true", help="repite los pasos aunque ya esten hechos"
    )
    args = parser.parse_args()

    print(__doc__.split("Uso")[0].strip() if "Uso" in __doc__ else "")
    comprobar_requisitos()
    total = 5

    if args.desde <= 1:
        titulo(1, total, "Base de datos")
        puerto = "5432" if puerto_libre(5432) else "5433"
        if puerto == "5433":
            print("      el 5432 esta ocupado; se usa el 5433", flush=True)
        correr(
            ["docker", "compose", "-f", "docker/docker-compose.yml", "up", "-d"],
            "levantar Postgres",
        )
        if not esperar_base():
            print(
                "\n  La base no responde. Comprueba DATABASE_URL en .env:"
                f"\n    ...@localhost:{puerto}/normia_db\n",
                file=sys.stderr,
            )
            return 1

    if args.desde <= 2:
        titulo(2, total, "Corpus sintetico (6 documentos, ningun dato de cliente)")
        correr([PY, "scripts/seed_demo_tenant.py", "--tenant", TENANT], "crear el tenant")
        correr([PY, "scripts/ingest_docs.py", "--tenant", TENANT], "ingerir los PDFs")
        correr(
            [PY, "scripts/load_faq.py", "--tenant", TENANT,
             "--archivo", f"data/faq/faq_{TENANT}.json"],
            "cargar el FAQ",
        )

    if args.desde <= 3:
        titulo(3, total, "Dataset de entrenamiento")
        dataset = RAIZ / "data" / "finetune" / TENANT / "train.jsonl"
        if dataset.exists() and not args.rehacer:
            print("      ya esta hecho; --rehacer para rehacerlo", flush=True)
        else:
            correr(
                [PY, "scripts/build_finetune_dataset.py", "--tenant", TENANT],
                "construir el dataset",
            )

    if args.desde <= 4:
        titulo(4, total, f"Entrenar el adaptador QLoRA{' (rapido)' if args.rapido else ''}")
        if (ADAPTADOR / "adapters.safetensors").exists() and not args.rehacer:
            print("      ya existe el adaptador; --rehacer para reentrenar", flush=True)
        else:
            orden = [PY, "scripts/train_lora.py", "-c", "scripts/lora_demo-publica.yaml"]
            if args.rapido:
                orden += ["--iters", "60"]
            correr(orden, "entrenar")

    if args.desde <= 5:
        titulo(5, total, "Medir: sin entrenamiento / con entrenamiento / con fine-tuning")
        if not ADAPTADOR.exists():
            print(f"  No existe {ADAPTADOR}: repite el paso 4.", file=sys.stderr)
            return 1
        correr(
            [PY, "scripts/comparar_tres_estados.py",
             "--modelo", BASE, "--adaptador", str(ADAPTADOR), "--tenant", TENANT],
            "comparar",
        )

    print(f"""
\033[1mListo.\033[0m

  El adaptador          adapters/qwen3-4b-demo/
  La tabla comparativa  docs/tres-estados-{TENANT}.md
  Las respuestas        docs/tres-estados-{TENANT}.json

  Para servirlo y hablar con el:

    uv run python -m mlx_lm server --model {BASE} \\
        --adapter-path adapters/qwen3-4b-demo --port 8090

  y en .env:  LLM_BASE_URL=http://127.0.0.1:8090/v1
              LLM_MERGE_SYSTEM_MESSAGES=true
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
