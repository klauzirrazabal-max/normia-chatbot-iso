"""
¿Para que sirve el fine-tuning aqui, si el RAG ya llega al 100 %?

Esa es la pregunta honesta, y el banco no la responde: mide CORRECCION, y la
correccion la arregla la recuperacion. El fine-tuning ataca otra cosa --
obediencia a las instrucciones -- y eso solo se ve cuando las instrucciones
dejan de estar en el prompt.

EL EXPERIMENTO
--------------
El prompt de sistema del tenant son 5.557 caracteres (~1.400 tokens) que viajan
en CADA peticion. Ahi estan las reglas de comportamiento: empieza por la
respuesta, atribuye por titulo y no por codigo, no apruebes nada. Mientras ese
texto este delante, manda el, y el adaptador apenas se nota.

Afinar sirve para meter esas reglas en los PESOS y poder quitarlas del prompt.
Asi que se mide lo mismo cuatro veces:

    prompt COMPLETO (5557 car.)  ×  {sin adaptador, con adaptador}
    prompt MINIMO   ( 433 car.)  ×  {sin adaptador, con adaptador}

Lo que se busca es la esquina inferior derecha. Si con el prompt corto el
modelo afinado aguanta y el base cae, el fine-tuning se paga en latencia y en
tokens. Si caen los dos, no aprendio las reglas -- y eso tambien hay que
decirlo.

    uv run python scripts/medir_valor_lora.py \\
        --modelo mlx-community/Qwen3-4B-4bit \\
        --adaptador adapters/qwen3-4b-normia
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

TIMEOUT_LLM = "180"

# Regla 2 del prompt del tenant: "EMPIEZA POR LA RESPUESTA". Estos arranques son
# justo lo que pide no hacer.
PREAMBULO = re.compile(
    r"^\s*(seg[uú]n|de acuerdo|conforme|tal como|bas[aá]ndome|en el documento|"
    r"el documento \w+-\w+-\d)",
    re.I,
)


def esperar(url: str, intentos: int = 120) -> bool:
    for _ in range(intentos):
        try:
            with urllib.request.urlopen(url, timeout=3) as r:  # noqa: S310 - localhost
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(2)
    return False


def lote(puerto: int, modelo: str, tenant: str, etiqueta: str, minimo: bool) -> list[dict]:
    orden = [
        sys.executable, str(RAIZ / "scripts" / "_preguntar_lote.py"),
        "--tenant", tenant, "--etiqueta", etiqueta,
    ]
    if minimo:
        orden.append("--prompt-minimo")
    proc = subprocess.run(  # noqa: S603
        orden, cwd=RAIZ, capture_output=True, text=True,
        env={
            **os.environ,
            "LLM_BASE_URL": f"http://127.0.0.1:{puerto}/v1",
            "LLM_API_KEY": "mlx",
            "LLM_MODEL": modelo,
            "LLM_TIMEOUT_SECONDS": TIMEOUT_LLM,
            "LLM_MERGE_SYSTEM_MESSAGES": "true",
        },
    )
    if proc.returncode != 0:
        print(proc.stdout + proc.stderr, file=sys.stderr)
        raise RuntimeError(f"fallo el lote {etiqueta}")
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
            raise RuntimeError("el servidor no respondio")
        return fn()
    finally:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=30)


def resumen(filas: list[dict]) -> dict[str, str]:
    """Correccion y OBEDIENCIA. La segunda es la que el fine-tuning puede mover."""
    utiles = [f for f in filas if f["texto"] and not f["texto"].startswith("(error")]
    if not utiles:
        return {}

    def pct(vals):
        v = [x for x in vals if x is not None]
        return f"{100 * sum(v) / len(v):.0f}%" if v else "n/a"

    con_codigo = sum(1 for f in utiles if f["codigos"])
    preambulo = sum(1 for f in utiles if PREAMBULO.match(f["texto"]))
    return {
        "Acierta en conjunto": pct([f["acierta"] for f in filas]),
        "Sin citas inventadas": pct([f["sin_fantasmas"] for f in filas]),
        "Escala cuando debe": pct([f["escalacion_correcta"] for f in filas]),
        "Atribuye sin escribir el codigo": f"{100 * (len(utiles) - con_codigo) / len(utiles):.0f}%",
        "Empieza por la respuesta": f"{100 * (len(utiles) - preambulo) / len(utiles):.0f}%",
        "Longitud mediana": f"{statistics.median(len(f['texto']) for f in utiles):.0f}",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modelo", required=True)
    parser.add_argument("--adaptador", required=True)
    parser.add_argument("--tenant", default="empresa-demo-iso")
    parser.add_argument("--puerto", type=int, default=8092)
    args = parser.parse_args()

    celdas = {}

    def dos_lotes(sufijo: str):
        print(f"── {sufijo}: prompt completo ──", flush=True)
        celdas[f"{sufijo}_completo"] = lote(
            args.puerto, args.modelo, args.tenant, f"{sufijo}-comp", minimo=False
        )
        print(f"── {sufijo}: prompt minimo ──", flush=True)
        celdas[f"{sufijo}_minimo"] = lote(
            args.puerto, args.modelo, args.tenant, f"{sufijo}-min", minimo=True
        )

    con_servidor(args.modelo, None, args.puerto, lambda: dos_lotes("base"))
    con_servidor(args.modelo, args.adaptador, args.puerto, lambda: dos_lotes("lora"))

    resumenes = {k: resumen(v) for k, v in celdas.items()}
    orden = [
        ("base_completo", "Base · prompt 5557"),
        ("lora_completo", "LoRA · prompt 5557"),
        ("base_minimo", "Base · prompt 433"),
        ("lora_minimo", "LoRA · prompt 433"),
    ]

    destino = RAIZ / "docs" / f"valor-lora-{args.tenant}"
    destino.with_suffix(".json").write_text(
        json.dumps(
            {"modelo": args.modelo, "adaptador": args.adaptador,
             "resumen": resumenes, "detalle": celdas},
            ensure_ascii=False, indent=1,
        ),
        encoding="utf-8",
    )

    metricas = list(resumenes["base_completo"])
    lineas = [
        "# ¿Para que sirve el fine-tuning aqui?",
        "",
        "El banco mide correccion, y la correccion la arregla el RAG. El",
        "fine-tuning ataca la OBEDIENCIA a las instrucciones, y eso solo se ve",
        "cuando las instrucciones dejan de estar en el prompt.",
        "",
        "| Metrica | " + " | ".join(t for _, t in orden) + " |",
        "|---|" + "---|" * len(orden),
    ]
    for m in metricas:
        lineas.append(
            f"| {m} | " + " | ".join(resumenes[k].get(m, "n/a") for k, _ in orden) + " |"
        )
    destino.with_suffix(".md").write_text("\n".join(lineas) + "\n", encoding="utf-8")

    ancho = max(len(m) for m in metricas)
    print()
    print(f"  {'':<{ancho}}" + "".join(f"{t:>21}" for _, t in orden))
    for m in metricas:
        print(f"  {m:<{ancho}}" + "".join(f"{resumenes[k].get(m, 'n/a'):>21}" for k, _ in orden))
    print(f"\n  {destino}.{{json,md}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
