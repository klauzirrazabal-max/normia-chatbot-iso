"""
Lanza `mlx_lm lora` subiendo antes el techo de memoria de Metal.

POR QUE HACE FALTA ESTE ENVOLTORIO
----------------------------------
MLX limita las asignaciones al "working set recomendado" que declara Metal, que
en esta maquina son 40.2 GB de los 51.5 GB fisicos. Entrenar NormIA se pasa de
ahi, y no por el modelo -- un 4B en 4 bits ocupa 2.4 GB -- sino por los LOGITS:

    secuencia 4608 x vocabulario 151.936 x 4 bytes = 2.8 GB por tensor

y la entropia cruzada materializa varias copias entre el forward, el softmax y
el backward. Se comprobo que bajar `num_layers` de 16 a 4 no cambia nada: la
memoria no se va en las capas LoRA, se va en la cabeza de salida.

Subir el techo por encima del recomendado es seguro mientras quede por debajo de
la memoria fisica: lo que Metal desaconseja es que el sistema empiece a paginar,
y el margen que se deja abajo es justamente para eso.

Uso:
    uv run python scripts/train_lora.py -c scripts/lora_qwen3.5-4b.yaml
    uv run python scripts/train_lora.py -c scripts/lora_qwen3.5-4b.yaml --iters 50

Cualquier argumento se pasa tal cual a `mlx_lm lora`.
"""

from __future__ import annotations

import sys

import mlx.core as mx

# Cuanta memoria fisica dejamos para el sistema operativo y el resto de procesos.
# Con menos de esto macOS empieza a paginar y el entrenamiento se vuelve mas
# lento que si no cupiera.
RESERVA_SISTEMA_GB = 8.0


def main() -> int:
    info = mx.device_info()
    fisica = info["memory_size"]
    recomendado = info["max_recommended_working_set_size"]
    techo = max(recomendado, fisica - RESERVA_SISTEMA_GB * 1e9)

    mx.set_memory_limit(int(techo))
    # La cache de Metal retiene los buffers liberados. Con secuencias de largo
    # variable eso fragmenta y acaba provocando un OOM con memoria de sobra.
    mx.set_cache_limit(int(4e9))

    print(
        f"memoria: fisica {fisica / 1e9:.1f} GB · "
        f"recomendada {recomendado / 1e9:.1f} GB · "
        f"techo fijado {techo / 1e9:.1f} GB",
        flush=True,
    )

    from mlx_lm import lora

    sys.argv = ["mlx_lm.lora", *sys.argv[1:]]
    lora.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
