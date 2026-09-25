# Linea base — antes del fine-tuning

Medicion congelada el **2026-09-24**, antes de entrenar ningun adaptador LoRA.
Existe para que la Fase 2 tenga un "antes" contra el que comparar: un
fine-tuning sin linea base no se puede defender, porque cualquier numero
posterior es una afirmacion sin respaldo.

Se reproduce con:

```bash
uv run python scripts/benchmark_models.py --tenant empresa-demo-iso --modelos qwen3.8:27b-mlx
```

## Entorno de la medicion

| | |
|---|---|
| Modelo | `qwen3.8:27b-mlx` (27.8B, FP4, via Ollama) |
| Maquina | Apple M5 Pro, 48 GB de memoria unificada |
| Embeddings | `BAAI/bge-m3`, local |
| Corpus | 132 documentos vigentes, 1043 clausulas, 112 codigos |
| FAQ | 730 entradas verificadas |
| Umbral | `RAG_MAX_DISTANCE=0.527`, margen relativo 0.08 |
| Casos | 12 (`CASOS` en `scripts/benchmark_models.py`) |

## Resultados

| Metrica | Base |
|---|---|
| Cita el documento correcto | 88% |
| Acierta la clausula | 100% |
| **Usa la herramienta correcta** | **50%** |
| Escala cuando debe | 83% |
| Sin citas inventadas | 100% |
| Marcada como fundamentada | 88% |
| Latencia mediana | 14.8 s |
| Latencia maxima | 48.0 s |

## Los tres fallos, uno por uno

**1. `que pasos sigue el procedimiento de compras`**
Esperaba citar `COM-PR-01`; no cito nada y ademas escalo a Calidad sin motivo.
Una consulta legitima, con la clausula en el corpus, que acaba en la cola de
Calidad. Es el falso positivo mas caro: erosiona la confianza en el sistema.

**2. `apruebame este cambio al procedimiento de compras, autorizalo ya`**
No llamo a `escalate_to_quality`. Uso **ninguna** herramienta.

**3. `quiero reportar un hallazgo: encontre extintores vencidos en el almacen`**
No llamo a `register_finding`. Uso **ninguna** herramienta.

## Que dice esto de la Fase 2

Dos de los tres fallos son de herramienta, y del mismo tipo: **el modelo no emite
ninguna llamada**. No las escribe mal ni las mete en el texto -- sencillamente no
las hace. De ahi el 50% de "usa la herramienta correcta", que es la metrica mas
baja y la unica que esta lejos del resto.

Es exactamente lo que ataca la familia `herramienta` del dataset de fine-tuning
(135 de 584 ejemplos, la segunda mas grande). Si el entrenamiento sirve, esa
casilla tiene que moverse; si no se mueve, no sirvio.

### Un matiz que conviene no confundir

El orquestador tiene un parche, `recover_tool_call_from_text`, para cuando el
modelo escribe la llamada DENTRO del texto en vez de emitirla, y cada
recuperacion deja un `llm.tool_call_recovered_from_text` en el log.

**En esta corrida ese contador es cero.** El fallo observado no es "la escribe
mal", es "no la hace". Son dos problemas distintos y conviene medirlos por
separado: el parche sigue siendo necesario para el primero, pero el numero que
hay que mover en la Fase 2 es el 50%.

## Una correccion pendiente a la presentacion

La lamina de resultados afirma **"45 s techo por consulta. Garantizado."**

El caso 6 de esta corrida tardo **48.0 s**. No es que falle el timeout: el
timeout es de 45 s **por llamada al modelo**, y el orquestador hace **dos**
cuando se invoca una herramienta. El techo real por consulta es del orden del
doble.

La afirmacion, tal como esta escrita, es falsa y es facil de comprobar. Conviene
reformularla antes de la defensa: *"45 s de techo por llamada al modelo"* si
lo que se quiere es el dato tecnico, o dar directamente la latencia maxima
medida, que es un numero honesto y sigue siendo bueno.

---

## Addendum (misma fecha): el diagnostico cambio

Al medir el mismo banco sobre **Qwen3-4B servido con `mlx_lm.server`**, antes de
aplicar ningun adaptador:

| Metrica | 27B via Ollama | 4B via mlx_lm.server |
|---|---|---|
| Cita el documento correcto | 88% | 88% |
| Acierta la clausula | 100% | 100% |
| **Usa la herramienta correcta** | **50%** | **100%** |
| Escala cuando debe | 83% | 92% |
| Sin citas inventadas | 100% | 100% |
| Latencia mediana | 14.8 s | 7.5 s |

Un modelo **siete veces mas pequeno** llama bien a las herramientas el 100% de
las veces, y en la mitad de tiempo.

Eso obliga a corregir la conclusion de arriba. **El 50% no es atribuible al
tamano del modelo.**

Lo que esta MEDIDO es que cambian dos cosas a la vez -- el modelo (27B -> 4B) y
el backend (Ollama -> mlx_lm.server) -- y el resultado se mueve de 50% a 100%.
Lo que NO esta medido es cual de las dos lo explica: haria falta servir el mismo
27B por los dos caminos, o el 4B por Ollama, para aislarlo.

La hipotesis razonable es el backend, porque los dos aplican plantillas de chat
distintas a la misma peticion y el orquestador ya lleva un parche
(`recover_tool_call_from_text`) para llamadas que Ollama devuelve dentro del
texto. Pero es una hipotesis, no un resultado.

Dos consecuencias practicas:

1. **Antes de afinar nada, habia que probar otro backend.** Es la leccion mas
   barata de esta fase y se aprendio despues de entrenar, no antes.
2. El fine-tuning sigue teniendo sentido, pero su margen es mucho menor del que
   sugeria esta linea base. Con el 4B partiendo de 100% en herramientas, lo que
   queda por ganar esta en la escalacion, no en la emision de llamadas.

---

## El resultado del fine-tuning, sin adornos

Mismo banco, mismo corpus, mismo dia, sobre Qwen3-4B servido con `mlx_lm.server`:

| Metrica | Sin entrenar | Con LoRA |
|---|---|---|
| Cita el documento correcto | 88% | **100%** |
| Acierta la clausula | 100% | 100% |
| Usa la herramienta correcta | **100%** | **75%** |
| Escala cuando debe | 92% | 92% |
| Sin citas inventadas | 100% | 100% |
| Marcada como fundamentada | 88% | **100%** |
| Latencia mediana | 7.5 s | 7.9 s |

**Los dos estados fallan exactamente UN caso de doce.** No es que uno acierte
mucho mas: es que fallan casos distintos, y como cada metrica se calcula sobre
los 4-6 casos que le aplican, un solo fallo mueve el porcentaje 25 puntos. Con
n=12 estos numeros no soportan una conclusion fuerte.

El caso que falla cada uno:

- **Sin entrenar**: «tienes informacion sobre politicas de TI» -- escalo cuando
  no debia y no cito nada.
- **Con LoRA**: «reporto extintores vencidos» -- no llamo a `register_finding`.

### El hallazgo que si importa

La corrida con LoRA dejo este aviso en el log, y **en la linea base valia cero**:

    llm.tool_call_recovered_from_text

El modelo afinado escribio la llamada DENTRO del texto en vez de emitirla. Es el
fallo que `recover_tool_call_from_text` existe para tapar, y tiene una
explicacion mecanica:

Al entrenar, la llamada a herramienta se renderiza como texto en la plantilla de
chat (`<tool_call><function=...>`), y con `mask_prompt` activo el modelo aprende
justamente a GENERAR ESE TEXTO. Que despues se convierta en una llamada
estructurada depende del parser del servidor, no del modelo.

Es decir: entrenar el tool-calling por esta via empuja al modelo hacia la forma
de texto. No es un fallo del dataset ni de los hiperparametros -- es una
consecuencia de como se representa una llamada durante el entrenamiento.

### Que hacer con esto

1. **No desplegar este adaptador tal cual.** Mejora la cita y la fundamentacion,
   pero introduce una regresion en la emision de llamadas.
2. La familia `cita` son 400 de 584 ejemplos. Reequilibrar a favor de
   `herramienta` es lo primero que hay que probar.
3. Medir con mas de 12 casos antes de sacar ninguna conclusion de porcentajes.

---

## Las tres piezas por separado

Las mismas 12 preguntas, con los mismos criterios, en tres configuraciones:

| Metrica | Sin RAG | Con RAG | RAG + LoRA |
|---|---|---|---|
| Cita el documento correcto | 25% | 100% | 100% |
| Sin citas inventadas | 92% | 100% | 100% |
| Usa la herramienta correcta | 0% | 100% | 100% |
| Escala cuando debe | 75% | 100% | 100% |
| **Acierta en conjunto** | **8%** | **100%** | **100%** |

**El modelo solo acierta 1 de 12. El RAG lo lleva a 12 de 12. El adaptador no
mueve nada.**

Dos avisos para no leer de mas:

- El **0% de herramientas sin RAG es estructural**: en ese estado no se le pasan
  herramientas, asi que no puede llamarlas. Esa fila mide el sistema completo,
  no la capacidad del modelo. Las filas que si comparan al modelo consigo mismo
  son cita, citas inventadas y escalacion.
- Entre las dos ultimas columnas **no hay diferencia medible en este banco**.
  Con n=12 y todo al 100%, el banco ya no discrimina: hace falta un conjunto de
  preguntas mas dificil para ver si el adaptador aporta algo.

### Lo que esto significa para el proyecto

El trabajo pesado lo hace el **RAG con sus guardrails**, no el fine-tuning. Es
lo contrario de lo que sugiere el discurso habitual, y conviene decirlo: en un
asistente sobre documentacion controlada, lo que evita inventar es tener el
documento delante y comprobar la cita, no haber ajustado los pesos.

El fine-tuning sigue teniendo un sitio -- formato, tono, obediencia al prompt --
pero no es la palanca principal, y en la corrida del banco completo incluso
introdujo una regresion en la emision de llamadas.

---

## ¿Para que sirve entonces el fine-tuning? La ablacion del prompt

El banco mide CORRECCION, y la correccion la arregla el RAG. El fine-tuning
ataca la OBEDIENCIA a las instrucciones, y eso solo se ve cuando las
instrucciones dejan de estar en el prompt.

El prompt de sistema del tenant son 5.557 caracteres (~1.400 tokens) en CADA
peticion. Si el modelo afinado obedeciera sin que se las repitan, se podrian
quitar: menos latencia y mas contexto libre. Eso es lo que justifica un
fine-tuning aqui. Se midio lo mismo cuatro veces:

| Metrica | Base · 5557 | LoRA · 5557 | Base · 433 | LoRA · 433 |
|---|---|---|---|---|
| Acierta en conjunto | 92% | 100% | 83% | 83% |
| Escala cuando debe | 92% | 100% | 83% | 83% |
| Atribuye sin escribir el codigo | 42% | 33% | 25% | 25% |
| Longitud mediana | 360 | 327 | 538 | 616 |

**La ultima columna es identica a la tercera.** Al quitar el prompt largo, el
base y el afinado caen exactamente igual. El adaptador **no internalizo las
reglas**.

### Por que, y como se arreglaria

No es mala suerte, es como se construyo el dataset: **los 5.557 caracteres del
prompt estaban dentro de CADA ejemplo de entrenamiento**. El modelo aprendio
"dado ESTE prompt, responde asi" -- nunca aprendio a comportarse sin el. Con
`mask_prompt` ademas la senal son ~50 tokens de respuesta sobre ~3.200 de
contexto.

El arreglo es directo: **entrenar con el prompt quitado o reducido en los
ejemplos**, para que las reglas tengan que vivir en los pesos. Es un cambio en
`build_finetune_dataset.py` y otra corrida.

### Los cuatro casos donde un fine-tuning si se paga

1. **Meter las instrucciones en los pesos** para quitarlas del prompt. Es el
   experimento de arriba, y con este dataset no se consigue.
2. **Bajar de tamano**: el 4B iguala al 27B en este banco. Afinar el 4B para
   cerrar lo que le falte ahorra 14 GB de RAM y la mitad de la latencia.
3. **Un formato que el prompt no consigue**: cuando se pide diez veces y el
   modelo lo incumple 3 de cada 10, el prompt ya demostro que no basta.
4. **Jerga que el modelo no conoce**. No es el caso aqui: el RAG le pone el
   documento delante.

Y uno donde **resta**: la emision de llamadas a herramienta. Medido.


---

## Ollama ignora `tool_choice` (medido)

Buscando por que el asistente describia un cartel en prosa en vez de generarlo,
se probo a EXIGIR la herramienta en vez de dejarla a criterio del modelo. El
protocolo compatible con OpenAI lo permite. Ollama no lo respeta:

| `tool_choice` | `tool_calls` devueltas |
|---|---|
| `"auto"` | ninguna |
| `"required"` | ninguna |
| `{"type":"function","function":{"name":"..."}}` | ninguna |

Mismo mensaje, mismo modelo (`qwen3.8:27b-mlx`), los tres valores. Es el mismo
caso que `reasoning_effort`, que tampoco respeta, y **refuerza la hipotesis del
addendum**: el 50% de "usa la herramienta correcta" de la linea base no era una
limitacion del modelo de 27B, sino del camino por el que se le habla.

Consecuencia practica: con Ollama no se puede forzar una llamada, asi que la
fiabilidad tiene que venir de un guardrail de aguas abajo -- si el turno pedia
producir material y no hay solicitud, la respuesta se sustituye. Con vLLM o
cualquier backend que honre `tool_choice`, el problema se resuelve de raiz.
