# Guion de exposición · NormIA

**Duración objetivo: 12 minutos.** 13 láminas, ~55 s de media. Las láminas 3, 6 y 7
son las que más pesan; las de arquitectura se explican solas mientras hablas.

No leas esto en voz alta. Son las ideas y el orden; las palabras ponlas tú.

---

## Antes de entrar

- [ ] Servicios levantados: Postgres, Ollama, la API en `:8000`
- [ ] Una pregunta ya probada, para no improvisar en vivo
- [ ] El PDF de la presentación en el USB, por si falla el navegador
- [ ] La URL pública abierta en otra pestaña, si vas a demostrar en vivo

---

## 01 · Portada  *(20 s)*

Preséntate y di la frase que resume todo:

> «NormIA responde preguntas sobre documentación ISO 9001 **sin inventar**,
> citando siempre documento, versión y sección.»

Señala la captura: es el asistente dentro de la intranet de una empresa. No es
una maqueta, está funcionando.

---

## 02 · El problema  *(60 s)*

El dolor, con una situación concreta:

> «Alguien necesita saber cuánto tiempo tiene para atender un requerimiento. Para
> responder eso hay que saber tres cosas **antes de leer nada**: en qué documento
> está, cuál es la versión vigente y en qué sección.»

Las tres cifras: **132 documentos**, **1043 cláusulas**, y el riesgo real —
actuar sobre una versión derogada **es una no conformidad en auditoría**, no un
despiste.

Cierra: hoy eso se resuelve preguntándole a una persona de Calidad. Cuando esa
persona no está, se resuelve adivinando.

---

## 03 · La evidencia  *(75 s — lámina fuerte, no la corras)*

> «Esto no son hipótesis. Salió al ingerir el sistema de calidad real, comparando
> cada PDF contra la Lista Maestra.»

No leas las seis. Cuenta **dos** bien:

- `CAL-IN-01`: la Lista Maestra dice v1, el documento imprime v2 en su membrete.
- Cinco formatos `CSST-FO-…` referenciados por procedimientos vigentes que **no
  están en la Lista Maestra**.

Y remata con la frase que define el alcance:

> «NormIA **detecta y avisa; no corrige**. Modificar un documento controlado es
> competencia del responsable de Calidad.»

Si te preguntan «¿y esto lo encontró la IA?»: lo encontró el proceso de ingesta
al contrastar contra la Lista Maestra, no el modelo.

---

## 04 · Por qué este problema  *(45 s)*

Cuatro ideas, una frase cada una:

- **Impacto**: la consulta ocurre a diario y consume tiempo de quien menos debería.
- **Viabilidad**: un SGC ya está escrito en cláusulas numeradas con código y
  versión. El documento viene partido en piezas con sentido propio.
- **Valor**: trazabilidad. Cada respuesta cita, y cada turno queda auditado.
- **Restricción**: son procedimientos internos. **Nada puede salir a una API de
  terceros.** Por eso el modelo corre en local.

Ese último punto es tu decisión de arquitectura más importante. Dilo con énfasis.

---

## 05 · Qué hace y qué no  *(60 s)*

Pasa rápido por la columna izquierda. **Detente en la derecha.**

> «Esta lista no son limitaciones pendientes. Son el producto. Un asistente sobre
> documentación controlada vale por lo que se niega a decir.»

Si solo recuerdan una frase tuya, que sea esa.

---

## 06 · Los cuatro guardrails  *(90 s — el corazón de la exposición)*

> «Cada uno de estos cuatro existe porque el sistema falló de una forma concreta.»

Cuenta **dos** con su anécdota:

- **El umbral**: sin él, la búsqueda por parecido siempre devuelve algo. Aunque no
  tenga nada que ver.
- **El código citado**: el modelo citó `PROC-CAL-04`, un procedimiento que no
  existe. Se lo había copiado al ejemplo de formato de sus propias instrucciones.
  Ese fue el más incómodo de encontrar.

Y la idea que los une:

> «Cualquiera de los cuatro puede **descartar una respuesta ya redactada**. Esa es
> la diferencia entre un buscador y un asistente auditable.»

---

## 07 · Las capas que no se ven  *(60 s)*

> «Los cuatro anteriores descartan. Estos ocho evitan llegar ahí.»

Menciona **dos**, las más contundentes:

- **Antes de buscar**: la consulta SQL filtra por documento vigente. Un obsoleto no
  puede aparecer aunque sea el más parecido. Es el control más barato y el que más
  importa en auditoría.
- **Correr no es encontrar**: una herramienta que devuelve lista vacía no cuenta
  como respaldo. Sin esto, el bot prometía derivar a Calidad y no registraba nada.

---

## 08 · Arquitectura  *(45 s)*

Recorre el diagrama de arriba abajo: cliente → API → núcleo → modelos y base.

La frase clave, señalando la caja de modelos:

> «**Nada sale de la máquina.** El modelo corre en Ollama y los embeddings en
> local. PostgreSQL guarda documentos, cláusulas, conversaciones y la traza de
> auditoría.»

---

## 09 · Flujo de una consulta  *(60 s)*

Sigue la línea con el dedo o el puntero:

> «La pregunta se compara **a la vez** con las preguntas frecuentes y con las
> cláusulas vigentes. Desde ahí, cuatro puntos de control. En cualquiera de ellos
> la respuesta se descarta y la consulta pasa a la cola de Calidad.»

Señala el rojo y el verde: son las dos únicas salidas posibles.

---

## 10 · Herramientas  *(50 s)*

No leas la tabla. Di tres cosas:

- **Modelo elegido midiendo, no por fama**: hay un banco de pruebas que compara
  candidatos sobre los documentos reales.
- **Índice HNSW**, no el otro: el índice se crea con la tabla vacía.
- **Todo local**, sin nube.

Y el detalle que se recuerda: la base **ignora los acentos**. Sin eso, buscar
«auditoria» no encontraba «auditoría», y devolvía cero documentos **en silencio**.

---

## 11 · Datos  *(75 s — aquí te van a preguntar por el chunking)*

Empieza por la cadena: **132 PDFs → 1043 cláusulas → 730 entradas de FAQ**.

Luego el troceado, que es lo que interesa (detalle completo abajo).

Y la validación:

> «La revisión es con **reglas fijas**, no pidiéndole a otra IA que juzgue:
> heredaría los mismos errores de quien las escribió.»

Cierra con lo que te distingue en una entrega académica:

> «El repositorio **no incluye ni un solo documento del cliente**. Viaja con un SGC
> sintético de seis documentos que imita la estructura real, para que cualquiera
> pueda ejecutarlo.»

---

## 12 · En funcionamiento  *(60 s)*

Dos capturas, dos frases:

- **Flujo feliz**: el dato en negrita, y debajo la referencia con el título por
  delante. El código y la versión van como respaldo, no como protagonistas.
- **Fuera de alcance**: preguntando por vacaciones, tema que no está en el SGC. No
  inventa, no responde a medias: lo dice, deriva a Calidad y marca la respuesta
  como no verificada.

Si demuestras en vivo, hazlo aquí. **Una sola pregunta**, ya probada.

---

## 13 · Resultados  *(45 s)*

- **11,1 s** de mediana, un 38 % más rápido que la primera versión.
- **45 s** de techo garantizado. Antes un cuelgue hacía esperar hasta 12 minutos.
- **195 tests**. Los de los guardrails son la tesis: si pasan a rojo, deja de ser
  auditable.

Cierra con los **seis hallazgos**:

> «El sistema encontró seis inconsistencias reales que nadie había visto. Ese fue
> el primer valor entregado, antes de responder una sola pregunta.»

Gracias, y abre a preguntas.

---
---

# Preguntas que te van a hacer

## «¿Cómo hiciste el chunking?»

La respuesta larga, por si aprietan:

**1. Extracción.** `pdfplumber`. Las tablas se extraen aparte y se reconstruyen
preservando qué valor pertenece a qué columna. Sin eso, el bot daba el «tiempo de
solución» cuando le preguntaban por el «tiempo de respuesta»: al aplanar la tabla,
los números quedaban pegados sin su cabecera.

**2. Limpieza.** Se reparan los saltos que mete el PDF: `CAL-FO- 09` vuelve a ser
`CAL-FO-09`, y las palabras cortadas con guion al final de línea se recomponen.
Y se elimina lo que aparece en **más del 60 % de las páginas** —membrete y pie de
confidencialidad—, porque si no se cuela en cada fragmento, diluye el embedding y
gasta contexto.

**3. Troceado, y aquí está la decisión.** No es por tamaño fijo: se detecta el
**encabezado de cláusula numerado**, hasta cuatro niveles (`6.2.1.3`), con esta
expresión:

```
^\s*(\d{1,2}(?:\.\d{1,2}){0,3})\.?\s+([A-ZÁÉÍÓÚÑ][^\n]{0,120})\s*$
```

El punto tras el número es **opcional**, y eso importa: sus documentos escriben
`1. OBJETIVO` y la primera versión exigía espacio sin punto. Detectaba cero
cláusulas y el sistema caía al respaldo por párrafos.

**Por qué por cláusula:** en un documento controlado la unidad de sentido ya viene
delimitada por el propio documento. Y es lo que permite citar «sección 6.2» con
trazabilidad, que es el requisito de auditoría.

**4. Descartes.** Los índices (líneas que acaban en puntos y un número) y las
cláusulas que **anuncian sin entregar** — menos de 250 caracteres y terminan en
dos puntos, porque el contenido real está en una imagen que la extracción no
alcanza. Esas se marcan para avisar, no se ingieren en silencio.

**5. Cláusulas largas.** Si una pasa de **3000 caracteres** se parte por párrafo
doble, **conservando su número de sección**, para que la cita siga siendo exacta.

**6. Sin solapamiento.** Si te lo preguntan —y es probable—:

> «No uso overlap. El overlap existe para mitigar cortes arbitrarios que parten una
> idea a la mitad. Aquí el corte cae en una frontera semántica: el final de una
> cláusula. Y cuando parto una cláusula larga, lo hago por párrafo y mantengo la
> etiqueta de sección, así que ningún fragmento pierde su procedencia.»

**Resultado**: 132 documentos → 1043 cláusulas. Mínimo 80 caracteres por
fragmento.

---

## «¿Por qué no usaste LangChain o LlamaIndex?»

No lo menciones tú. Si preguntan:

> «Porque lo que da valor a este proyecto no es un primitivo de una librería. El
> troceado por cláusula no es ningún splitter estándar, y la verificación de que
> el código citado existe no está en ninguna. Con un framework habría entregado
> antes y **entendido menos**, y necesitaba poder explicar por qué el umbral vale
> 0.527.»

Si insisten en que LangChain también tiene umbral (`similarity_score_threshold`,
y es cierto): concédelo y reconduce al argumento fuerte:

> «Sí lo tiene. El argumento no es que no se pueda, es la depurabilidad. Cuando 47
> de 132 documentos quedaron con cero fragmentos, lo encontré porque el pipeline
> es código plano que se lee de arriba abajo. Dentro de un framework habría sido
> un problema silencioso de calidad de datos.»

Y si preguntan por la contrapartida, reconócela — queda mejor que defenderte:

> «Cambiar de proveedor de LLM cuesta más que en LangChain. Y reimplementé
> reintentos y timeouts, y los hice mal al principio: un cuelgue costaba hasta 12
> minutos. Eso una librería madura ya lo tiene resuelto.»

---

## «¿Por qué ese modelo?»

Qwen3.8 27B, 27.8B parámetros, cuantización FP4, vía Ollama. Elegido **midiendo**
sobre los documentos reales: acierto de cláusula, de sección, uso correcto de
herramienta, escalación cuando toca, y cero citas inventadas.

Si preguntan por la etiqueta `qwen3.8:27b-mlx`: el campo `architecture` reporta
`qwen3_5`, que es la familia de arquitectura, no la versión. Y la cuantización
real es FP4, no MLX pese al nombre. Es una etiqueta del empaquetado.

---

## «¿Y el embedding, por qué bge-m3?»

Aquí sé honesto, porque es la incoherencia real del proyecto:

> «El modelo de lenguaje lo elegí midiendo. El embedding lo elegí por reputación:
> multilingüe, fuerte en español técnico. No lo medí. Tengo el set de validación
> para hacerlo —730 pares de pregunta y cita esperada— y es lo siguiente.»

Reconocer eso vale más que improvisar una justificación.

---

## «¿Y si el documento cambia de versión?»

La búsqueda filtra por `status = 'vigente'` en SQL, antes del vector. Marcar un
documento como obsoleto lo saca de todas las respuestas de inmediato. Y las
entradas de FAQ cuelgan del documento por clave foránea: si el documento se borra
o pasa a obsoleto, sus preguntas dejan de recuperarse sin proceso aparte.

---

## «¿Puede alucinar?»

> «Puede escribir algo incorrecto, como cualquier modelo. Lo que no puede es citar
> un documento que no existe ni una versión equivocada: eso se verifica contra la
> base y la respuesta se descarta. Y si no hay respaldo documental, no responde:
> deriva a Calidad. Prefiero un asistente que calla a uno que rellena.»

---

## «¿Cuánta gente lo puede usar a la vez?»

Fase 1 corre en una máquina, con un modelo de 18 GB. Está pensado para un equipo
de calidad, no para toda la empresa. Escalar significa servidor con GPU, y eso es
Fase 2 junto con WhatsApp y multi-tenant real.

---

## Si algo falla en la demo

No improvises con el sistema. Ten el PDF listo y di:

> «Prefiero no improvisar en vivo con un modelo local; en el repositorio está todo
> para reproducirlo, y aquí tengo las capturas de estos mismos casos.»
