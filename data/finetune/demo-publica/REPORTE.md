# Dataset de fine-tuning — demo-publica

Generado por `scripts/build_finetune_dataset.py`. No se edita a mano:
se vuelve a generar.

## Reparto

| Particion | Ejemplos |
|---|---|
| train | 78 |
| valid | 12 |
| test | 11 |
| **total** | **101** |

El reparto es por GRUPO (clausula de origen), no por ejemplo: dos preguntas
de la misma clausula comparten casi todo el prompt y separarlas convertiria
la validacion en una medida de memorizacion.

## Familias

| Familia | Ejemplos | Que ensena |
|---|---|---|
| `herramienta` | 41 | Emitir la llamada como tool_call, no escribirla en el texto |
| `escalacion` | 31 | Negarse y llamar a escalate_to_quality cuando no hay respaldo |
| `cita` | 22 | Responder con respaldo y citar codigo, version y seccion |
| `social` | 4 | Saludar sin escalar a Calidad |
| `fantasma` | 3 | No citar un documento que no existe (los 14 rechazos del FAQ) |

## Excluido del entrenamiento

**12 preguntas del banco de pruebas** (`scripts/benchmark_models.py`)
quedan fuera de las tres particiones. Si entraran, el benchmark dejaria de
medir generalizacion y pasaria a confirmar lo memorizado.

## Descartes durante la construccion (0)

Ninguno.
