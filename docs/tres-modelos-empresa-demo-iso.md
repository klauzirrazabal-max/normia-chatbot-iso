# Los tres estados del modelo

Las mismas 12 preguntas, **sin RAG** en los tres casos, como en el taller.
Asi se ve lo que aporta el modelo por si mismo.

| Metrica | Sin entrenamiento | Con entrenamiento | Con fine-tuning |
|---|---|---|---|
| Atribuye una fuente | 17% | 67% | 83% |
| Y es la correcta | 0% | 38% | 62% |
| Sin citas inventadas | 100% | 100% | 100% |
| Longitud mediana | 895 | 1014 | 160 |

## Una respuesta de cada estado

**Sin entrenamiento** — «me puedes decir el tiempo de atencion de un requerimiento a TI»

> El tiempo de atención a un requerimiento de TI puede variar dependiendo de varios factores, como la complejidad del problema, la disponibilidad de recursos, la prioridad del requerimiento y la eficiencia del equipo de TI. Aquí hay algunos puntos clave que pueden influir en el tiempo de atención:
> 
> 1. **Prioridad del Requerimiento**: Los requerimientos de alta prioridad suelen ser atendidos más rápi

**Con entrenamiento** — «me puedes decir el tiempo de atencion de un requerimiento a TI»

> <think>
> Okay, the user is asking about the time of attention for a TI requirement. Let me check the provided documents.
> 
> Looking at the fragments, there's a mention of "el tiempo de respuesta es de 1 hora" in the procedure for "Atencion de Solicitudes Tecnologicas, seccion 7.6". That seems to be the relevant part. The user is asking for the time, so the answer should directly state that the respon

**Con fine-tuning** — «me puedes decir el tiempo de atencion de un requerimiento a TI»

> <think>
> 
> </think>
> 
> El tiempo de atención se establece en el procedimiento Atención de Solicitudes Tecnológicas, seccion 7.6. Esta en el procedimiento Atención de Solicitudes Tecnológicas, seccion 7.6.

