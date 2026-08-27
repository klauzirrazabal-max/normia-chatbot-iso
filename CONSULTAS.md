# Cómo ver la data de NormIA

Tres formas, de la más cómoda a la más directa.

## 1. Adminer — explorador web, sin instalar nada

```
http://localhost:8081
```

| Campo | Valor |
|---|---|
| Motor | PostgreSQL |
| Servidor | `db` |
| Usuario | `normia` |
| Contraseña | `normia_pass` |
| Base de datos | `normia_db` |

Ya viene levantado con `docker compose up -d`. Puedes navegar tablas, ordenar,
filtrar y correr SQL desde el navegador.

## 2. Panel de gestión documental

```
http://localhost:5500/admin.html
```

Vista de negocio, no de base de datos: documentos por área, vigentes vs obsoletos,
fragmentos por documento, conflictos de vigencia y la cola de carga.

## 3. psql desde la terminal

```bash
docker exec -it normia_db psql -U normia -d normia_db
```

Dentro de psql: `\dt` lista tablas, `\d nombre_tabla` describe una, `\q` sale.

Para una consulta suelta sin entrar:

```bash
docker exec normia_db psql -U normia -d normia_db -c "SELECT count(*) FROM documents;"
```

---

# Consultas útiles

## Panorama general

```sql
SELECT
    (SELECT count(*) FROM documents)                              AS documentos,
    (SELECT count(*) FROM documents WHERE status = 'vigente')     AS vigentes,
    (SELECT count(*) FROM document_chunks)                        AS fragmentos,
    (SELECT count(*) FROM conversations)                          AS conversaciones,
    (SELECT count(*) FROM messages)                               AS mensajes,
    (SELECT count(*) FROM findings)                               AS hallazgos;
```

## Documentos por área

```sql
SELECT area,
       count(*)                                       AS documentos,
       count(*) FILTER (WHERE status = 'vigente')     AS vigentes,
       sum((SELECT count(*) FROM document_chunks c WHERE c.document_id = d.id)) AS fragmentos
FROM documents d
GROUP BY area
ORDER BY documentos DESC;
```

## Qué documentos tienen pocos fragmentos

Un documento con 1 o 2 fragmentos suele ser un PDF escaneado sin OCR, o un
formato en blanco sin prosa. El asistente casi no podrá responder sobre él.

```sql
SELECT d.code, d.version, d.title, count(c.id) AS fragmentos
FROM documents d
LEFT JOIN document_chunks c ON c.document_id = d.id
GROUP BY d.id, d.code, d.version, d.title
HAVING count(c.id) <= 2
ORDER BY fragmentos, d.code;
```

## Ver el texto que realmente indexó un documento

Esto es lo que ve el modelo. Útil cuando una respuesta sale rara: casi siempre
el problema está aquí, no en el modelo.

```sql
SELECT c.section, left(c.content, 300) AS contenido
FROM document_chunks c
JOIN documents d ON d.id = c.document_id
WHERE d.code = 'STI-PR-01'
ORDER BY c.id;
```

## Fragmentos que contienen tablas

```sql
SELECT d.code, c.section, left(c.content, 200)
FROM document_chunks c
JOIN documents d ON d.id = c.document_id
WHERE c.content LIKE '%|%'
ORDER BY d.code
LIMIT 30;
```

---

# Auditoría de conversaciones

Esta es la parte que distingue a NormIA de un chatbot cualquiera: cada turno
guarda **qué se recuperó, a qué distancia, qué herramienta corrió y si la
respuesta quedó fundamentada**. Permite demostrar que el bot no inventó, en vez
de solo afirmarlo.

## Últimas respuestas con su traza

```sql
SELECT
    m.created_at,
    left(m.content, 90)                                  AS respuesta,
    m.retrieval_debug -> 'grounded'                      AS fundamentada,
    m.retrieval_debug -> 'escalate'                      AS escalada,
    m.retrieval_debug -> 'tools'                         AS herramientas,
    m.retrieval_debug -> 'elapsed_ms'                    AS ms
FROM messages m
WHERE m.role = 'assistant'
ORDER BY m.created_at DESC
LIMIT 20;
```

## Qué fuentes respaldaron cada respuesta, con su distancia

```sql
SELECT
    left(m.content, 60) AS respuesta,
    jsonb_array_elements(m.retrieval_debug -> 'retrieval' -> 'accepted') AS fuente
FROM messages m
WHERE m.role = 'assistant'
ORDER BY m.created_at DESC
LIMIT 20;
```

Cada `fuente` trae `code`, `version`, `section` y `distance`.

## Respuestas NO fundamentadas — las que hay que revisar

```sql
SELECT m.created_at,
       left(m.content, 120) AS respuesta,
       m.retrieval_debug -> 'retrieval' -> 'accepted' AS fuentes
FROM messages m
WHERE m.role = 'assistant'
  AND m.retrieval_debug ->> 'grounded' = 'false'
ORDER BY m.created_at DESC;
```

## Preguntas que el bot no pudo responder

Oro puro para saber qué documentación falta: son las consultas reales de tu
gente que el SGC no cubre.

```sql
SELECT u.created_at, u.content AS pregunta
FROM messages u
JOIN messages a
  ON a.conversation_id = u.conversation_id
 AND a.id = u.id + 1
 AND a.role = 'assistant'
WHERE u.role = 'user'
  AND a.retrieval_debug ->> 'escalate' = 'true'
ORDER BY u.created_at DESC;
```

## Una conversación completa

```sql
SELECT m.role, m.content, m.created_at
FROM messages m
JOIN conversations c ON c.id = m.conversation_id
WHERE c.external_user_id = 'PON_AQUI_EL_SESSION_ID'
ORDER BY m.created_at;
```

## Distancia promedio de lo recuperado

Si sube con el tiempo, el corpus se está quedando corto frente a lo que la gente
pregunta.

```sql
SELECT round(avg((f ->> 'distance')::numeric), 4) AS distancia_promedio,
       count(*)                                   AS fuentes_usadas
FROM messages m,
     jsonb_array_elements(m.retrieval_debug -> 'retrieval' -> 'accepted') f
WHERE m.role = 'assistant';
```

---

# Hallazgos y acciones correctivas

```sql
SELECT f.id, f.description, f.status, f.created_at,
       count(a.id) AS acciones
FROM findings f
LEFT JOIN capa_actions a ON a.finding_id = f.id
GROUP BY f.id
ORDER BY f.created_at DESC;
```

Para probar el flujo de CAPA, crea una acción a mano sobre un hallazgo existente:

```sql
INSERT INTO capa_actions (finding_id, description, responsible, due_date, status)
VALUES (1, 'Reemplazar extintores vencidos del almacen central',
        'Jefe de Operaciones', '2026-09-30', 'en_progreso');
```

Luego pregúntale al bot: *"¿cuál es el estado de las acciones correctivas del hallazgo 1?"*

---

# Cola de carga de documentos

```sql
SELECT id, original_filename, status, resolved_code, resolved_version,
       chunks_created, jsonb_array_length(advices) AS avisos, created_at
FROM upload_jobs
ORDER BY created_at DESC;
```

Ver los avisos de un job:

```sql
SELECT jsonb_array_elements(advices) FROM upload_jobs WHERE id = 1;
```

---

# Control documental

## Dos versiones vigentes del mismo documento

Es una no conformidad: el asistente podría citar cualquiera de las dos.

```sql
SELECT code, array_agg(version ORDER BY version) AS versiones_vigentes
FROM documents
WHERE status = 'vigente'
GROUP BY code
HAVING count(*) > 1;
```

## Documentos obsoletos (el bot NO los cita)

```sql
SELECT code, version, title, effective_date
FROM documents
WHERE status = 'obsoleto'
ORDER BY code;
```

## Marcar o reactivar a mano

```sql
UPDATE documents SET status = 'obsoleto' WHERE code = 'CAL-IN-01' AND version = 'v1';
```

El cambio surte efecto en la siguiente pregunta, sin reiniciar nada.
