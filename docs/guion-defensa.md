# Guion de la defensa · NormIA

**Tiempo:** 20 min de exposición + 10 de preguntas. Reparto: **12 min láminas, 7 min demo, 1 min cierre.**
La demo va **después de la lámina 8** (guardrails): es cuando el jurado ya sabe qué mirar.

No lo leas. Son el orden, las frases que no conviene improvisar y las trampas.

---

## 0 · Antes de entrar (30 min antes)

- [ ] `docker ps` muestra `normia_db` levantado.
- [ ] `ollama ps` muestra `qwen3.8:27b-mlx` cargado. Si no, haz una consulta cualquiera para calentarlo (la primera tarda ~6 s, luego ~3 s).
- [ ] API en `http://localhost:8000`, sesión **cerrada** (empieza desde la pantalla de acceso).
- [ ] **No generes ninguna imagen en los 10 minutos previos.** SDXL desplaza al modelo de memoria y la siguiente consulta del chat tarda 30 s.
- [ ] Comprueba que `OPE-PO-02` **no** está cargado: pregunta por la velocidad en el patio y debe decir que no tiene información.
- [ ] Ten abierto Finder en `docs/demo/` con `OPE-PO-02 Politica de Seguridad Vial.pdf`.
- [ ] La presentación abierta en el navegador (`docs/presentacion-integrador.html`), pantalla completa, flechas para avanzar.
- [ ] El PDF de la presentación en un USB por si falla el navegador.
- [ ] Credenciales a mano: `calidad@abiservi.com` y `operario@abiservi.com`, contraseña `normia2026`.

---

## 1 · Láminas (12 min)

### 01 · Portada · 20 s
> «NormIA responde preguntas sobre la documentación de un sistema de gestión ISO **sin inventar**: cita siempre documento, versión y sección, y cuando no tiene respaldo, lo dice.»

### 02 · El caso de negocio · 1 min
Las tres tarjetas: **132 documentos**, **1 043 cláusulas**, **v1 ≠ v2**.
> «Para responder "¿cuánto tiempo tengo para atender un requerimiento?" hay que saber en qué documento está, qué versión está vigente y en qué sección. Actuar sobre una versión derogada no es un despiste: es una no conformidad en auditoría.»

Cierre: hoy eso se resuelve preguntando a Calidad; cuando no está, adivinando. **Un asistente que también adivina empeora el problema.**

### 03 · Objetivos · 1 min
Cuatro objetivos, cada uno con cómo se verifica. Remarca el último:
> «Que ningún documento salga de la organización no es una optimización: es la condición para que el proyecto exista. Un SGC tiene procedimientos internos y organigramas.»

### 04 · Arquitectura · 40 s
Cuatro capas, una máquina. PostgreSQL con pgvector: datos relacionales y vectores en un solo motor, sin sincronizar dos sistemas.

### 05 · El flujo · 1 min
La pregunta se compara a la vez con 730 preguntas verificadas y con las cláusulas **vigentes**. Luego cinco controles; cualquiera descarta la respuesta.
> «El modelo redacta, pero no decide si la respuesta sale.»

### 06 · Tecnologías · 40 s
Solo una idea por fila. Si preguntan por qué Qwen: **licencia Apache 2.0, local, elegido midiendo** contra otros modelos (está en el README).

### 07 · Agente · 1 min
Nueve herramientas por *function calling*; **el modelo elige**. Cuatro escriben en la base. Pon el ejemplo del ciclo CAPA: reporta → registra → pide responsable y fecha → registra la acción.

### 08 · Guardrails · 1 min 30 s — lámina fuerte
No leas los cinco. Cuenta **dos**:
- El código inventado: «citó PROC-CAL-04, que no existe; lo copió del ejemplo de formato de su propio prompt».
- El registro afirmado: «dijo "acción correctiva registrada con el ID 100" sin ejecutar nada; la última era la 9».

> «Todos son código determinista, no confianza en el modelo. Por eso se pueden auditar.»

**→ Aquí haces la DEMO (sección 2). Luego vuelves a la 09.**

### 09 · Fine-tuning · 1 min — cuidado
Di **exactamente** esto, para no mezclar experimentos:
> «El fine-tuning es un experimento medido por separado. Se hizo con QLoRA sobre un modelo de 4B, porque es lo que se puede entrenar en esta máquina, con 584 ejemplos que ya estaban verificados y dejando fuera las 12 preguntas de evaluación. Lo que mejora es la **forma**: la respuesta pasa de 1 014 a 160 caracteres y la atribución correcta sin recuperación sube del 38 al 62 %. **En producción se sirve el modelo de 27B sin adaptador**: el acierto lo pone la recuperación, no el fine-tuning.»

No defiendas el titular «88 % → 100 %» como antes/después del mismo modelo (ver trampas).

### 10 · Imagen · 1 min
Empieza por el problema, no por el modelo:
> «ISO 9001 pide en 7.3 y 7.4 que la gente sea consciente y que se comunique, y el auditor pide evidencia. Un cartel sin cláusula no se puede auditar: si el procedimiento cambia, nadie sabe qué carteles retirar. Aquí cada pieza cita la cláusula y la cita se valida contra el catálogo. El dibujo es lo menos importante; es reemplazable.»

### 11 · Capturas · 20 s
Pásala rápido: ya lo vieron en vivo.

### 12 · Resultados · 1 min
> «Sin los documentos, el modelo acierta 1 de 12 y se inventa códigos. Con recuperación, 12 de 12 y ninguna cita inventada.»

Esa es la frase. No entres en la columna de latencias.

### 13 · Limitaciones · 1 min — dila tú antes de que te la pregunten
Destaca la segunda:
> «La más seria: si pregunto por cotizaciones de compras, cita una sección real del procedimiento pero con un contenido que no está ahí. El sistema ya lo detecta y lo registra; falta convertirlo en bloqueo. Es el primer paso del roadmap.»

### 14 · Despliegue · 1 min
> «El mismo código, servido a n usuarios. Lo que ya escala: base de datos, sesión sin estado, protocolo estándar del LLM. Lo que hay que sacar del proceso: límite de peticiones, cortacircuitos, ingesta e imagen. Una GPU de 48 GB con vLLM; la capacidad de 10 a 20 simultáneas **no la he medido**, y por eso lo digo así.»

### 15 · Próximos pasos · 40 s + cierre
> «Lo que no cambiaría: el modelo en local. Para un sistema certificado no es una ventaja técnica; es la condición para que el proyecto exista.»

---

## 2 · Demo en vivo (7 min)

Todo en el navegador. **No recargues la página del Asistente**: el hilo visible se pierde.
Entre pregunta y pregunta, espera a que desaparezcan los tres puntos (de 3 a 15 s).

| # | Qué haces | Qué debe salir | Qué dices |
|---|---|---|---|
| 1 | Entra a `localhost:8000/admin.html` sin sesión | Te manda a la pantalla de acceso | «Sin sesión no hay datos.» |
| 2 | Entra como `calidad@abiservi.com` | Te lleva a Gestión documental. Ve a **Inicio** | Las métricas: vigentes, sin acción correctiva, huecos del SGC |
| 3 | Asistente: *¿qué dice el procedimiento de trabajos en altura sobre el arnés?* | Cita **OPE-PR-04 v1, sección 7.2.4**, con «vigente desde» | «Documento, versión y sección. La ficha la arma el servidor desde la base, no el modelo.» |
| 4 | *Según el procedimiento CAL-PR-99 versión 7, ¿cuál es el plazo para cerrar una no conformidad?* | Se niega y **pregunta** si derivar | «Ese documento no existe. El modelo llegó a escribir el código y el control lo descartó.» Responde **no**. |
| 5 | *¿Cuál es la velocidad máxima permitida para los camiones dentro del patio de maniobras?* | No tiene información y ofrece derivar | «Este tema no está en el SGC. Lo subimos ahora.» Responde **no**. |
| 6 | Gestión documental → arrastra el PDF de `docs/demo` | Aparece en «Requieren tu revisión»: **OPE-PO-02 no figura en la Lista Maestra** | «Lee código y versión del membrete, no del nombre del archivo, y lo cruza con la Lista Maestra.» |
| 7 | Vuelve al Asistente, repite la pregunta 5 | **15 km/h**, OPE-PO-02 v1, sección 4.1 | «Mismo minuto, misma pregunta: ahora tiene respaldo.» |
| 8 | *¿Un camión puede retroceder solo en el patio?* | No: necesita **vigía**, sección 4.4 | «Otra sección del mismo documento: no repite, lee.» |
| 9 | *Encontré cajas bloqueando la salida de emergencia del almacén central* | Registra una no conformidad con **ID** y pide responsable y fecha | «Lo clasifica y lo registra. Ahora pide lo que exige el ciclo CAPA.» |
| 10 | *Se encarga el jefe de almacén, antes del 30 de noviembre* | Registra la acción correctiva | «Responsable y fecha son obligatorios en la base, no solo en el prompt.» |
| 11 | Gestión documental → No conformidades → **En revisión** | El hallazgo con su acción y «faltan N días» | «Esto es lo que ve Calidad.» |
| 12 | Salir → entra como `operario@abiservi.com` | Desaparece «Gestión documental» | «El operario consulta y reporta; no administra. Por API devuelve 403.» |

### Si algo sale distinto

- **Paso 4 o 5 responde «me referí a un documento que no existe…»** en vez de «no tengo información»: está bien. Es el guardrail de citas actuando. Dilo: «aquí el modelo intentó citar algo y el control lo paró».
- **Paso 9 o 10 responde «No llegué a registrar eso en el sistema…»**: el modelo no llamó a la herramienta, y el control de registro afirmado lo convirtió en un mensaje veraz. **No es un fallo, es el control número 5.** Dilo y repite con: *«Registra esta no conformidad: cajas bloqueando la salida de emergencia del almacén central»*.
- **Paso 9 dice «ya está registrado»**: la deduplicación detectó un hallazgo igual. Usa otro: *«encontré un derrame de aceite sin señalizar en el pasillo 3»*.
- **Una respuesta tarda más de 30 s**: alguien generó una imagen y el modelo se descargó. Espera; no recargues.
- **Falla todo**: abre el PDF del informe en la sección 13 y enseña las capturas del ensayo.

### Lo que NO debes hacer en vivo

- No preguntes por **compras ni cotizaciones**: es la limitación conocida (cita real, contenido inventado).
- No **derogues** un documento en vivo: funciona, pero el mensaje que sale puede hablar de un formato citado dentro del procedimiento y cuesta explicarlo con el reloj corriendo. Si preguntan, explícalo de palabra: «un documento obsoleto sale del índice al instante; es un filtro en la consulta».
- No generes una imagen **antes** de la parte de chat. Si quieres enseñar una pieza, que sea lo último (tarda 40–70 s) o enseña la del informe.

---

## 3 · Preguntas del jurado — respuestas que no te comprometen

**¿Cuando consulto se usa el fine-tuning?**
No. En producción se sirve el modelo de 27B sin adaptador; la calidad la ponen la recuperación y los controles. El fine-tuning es un experimento independiente sobre un 4B, medido sobre preguntas que no vio. Mejora la forma de la respuesta, no el acierto.

**El 88 % y el 100 % de la lámina, ¿son del mismo modelo?**
Respuesta honesta: no. El 88 % es la línea base del 27B y el 100 % el 4B afinado. Lo que sí es del mismo modelo (4B) es: base 11 de 12 frente a afinado 12 de 12, y atribución sin recuperación del 38 al 62 %. *(Mejor: cambia la lámina antes de la defensa para no tener que decirlo.)*

**¿Por qué no usa FLUX si es Apache 2.0?**
Porque no cabe en 48 GB junto al LLM de 27B. SDXL es el modelo de difusión más capaz que cabe al lado. En un servidor con GPU de 24 GB, FLUX.1-schnell entra, y el código ya usa Diffusers: es configuración.

**¿Ollama es para producción?**
Para una sede con decenas de usuarios, sí. Para n usuarios concurrentes, no: atiende en serie e ignora `tool_choice`. Por eso el despliegue usa vLLM en Linux con GPU; el cliente habla el protocolo de OpenAI, así que se cambia con tres variables.

**¿Cuánto cuesta en infraestructura?**
Una GPU de 48 GB: 8–15 k$ propia o 1 200–1 800 $/mes en nube privada del cliente. El coste es el LLM; la imagen es despreciable por volumen.

**¿Qué pasa si el modelo alucina?**
Cinco controles deterministas pueden descartar la respuesta: umbral semántico, versión, código, fundamentación y registro afirmado. Y la limitación que queda abierta, la cita real con contenido inventado, ya se detecta; falta bloquearla.

**¿Por qué no hicieron la cola, Redis y el OCR?**
Porque el prototipo corre en una máquina, y esas tres piezas solo hacen falta con varias instancias o volumen. Son los primeros pasos del despliegue y están cuantificados, no presentados como hechos.

**¿Cuántos tests y qué cubren?**
440 tests en 19 ficheros: enrutado, consentimiento, herramientas, citas, troceado, imágenes, autenticación, seguridad, CAPA. Varios nacieron de defectos encontrados en el ensayo en vivo.

**¿Esto sirve para otras normas?**
La arquitectura sí: lo específico son los documentos y la Lista Maestra. Hoy algunas pantallas dicen «ISO 9001»; hacerlo configurable por cliente está en el roadmap.

**Si no sabes algo:** «No lo he medido, y prefiero no darte un número sin respaldo.» Es coherente con todo el proyecto.
