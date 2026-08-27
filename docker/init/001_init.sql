-- NormIA: esquema inicial
-- Nota: la dimension de `embedding` debe coincidir con EMBEDDING_DIM del .env.
--       bge-m3 -> 1024. Si cambias de modelo de embeddings, cambia aqui tambien
--       y recrea la base (la app valida esto al arrancar y falla rapido si no cuadra).

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS tenants (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    system_prompt   TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS documents (
    id              SERIAL PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    code            TEXT NOT NULL,          -- ej. PROC-CAL-04
    version         TEXT NOT NULL,          -- ej. v3
    title           TEXT,
    area            TEXT,
    effective_date  DATE,
    status          TEXT NOT NULL DEFAULT 'vigente',
    source_filename TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, code, version),
    CONSTRAINT documents_status_check CHECK (status IN ('vigente', 'obsoleto'))
);

-- El retriever filtra SIEMPRE por (tenant_id, status='vigente'): indice compuesto.
CREATE INDEX IF NOT EXISTS idx_documents_tenant_status
    ON documents (tenant_id, status);

CREATE TABLE IF NOT EXISTS document_chunks (
    id              SERIAL PRIMARY KEY,
    document_id     INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    section         TEXT,                   -- ej. "5.2"
    content         TEXT NOT NULL,
    embedding       vector(1024) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- HNSW en vez de ivfflat: ivfflat necesita entrenar centroides sobre datos ya
-- cargados, y aqui el indice se crea con la tabla vacia (init de Postgres), lo
-- que degradaria el recall. HNSW no requiere entrenamiento previo.
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw
    ON document_chunks USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_chunks_document
    ON document_chunks (document_id);

CREATE TABLE IF NOT EXISTS conversations (
    id               SERIAL PRIMARY KEY,
    tenant_id        TEXT NOT NULL REFERENCES tenants(id),
    channel          TEXT NOT NULL,          -- whatsapp | web
    external_user_id TEXT NOT NULL,          -- numero de whatsapp o session_id del widget
    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, channel, external_user_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id              SERIAL PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,          -- user | assistant
    content         TEXT NOT NULL,
    retrieved_chunk_ids INTEGER[],
    -- Traza de auditoria: que se recupero, con que distancia, que herramienta
    -- se llamo y si la respuesta quedo fundamentada. Permite DEMOSTRAR que el
    -- bot no invento, en vez de solo afirmarlo.
    retrieval_debug JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT messages_role_check CHECK (role IN ('user', 'assistant'))
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages (conversation_id, created_at);

CREATE TABLE IF NOT EXISTS findings (
    id              SERIAL PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    conversation_id INTEGER REFERENCES conversations(id),
    description     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'abierto',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT findings_status_check CHECK (status IN ('abierto', 'en_revision', 'cerrado'))
);

CREATE TABLE IF NOT EXISTS capa_actions (
    id              SERIAL PRIMARY KEY,
    finding_id      INTEGER NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
    description     TEXT NOT NULL,
    responsible     TEXT,
    due_date        DATE,
    status          TEXT NOT NULL DEFAULT 'pendiente',
    CONSTRAINT capa_status_check CHECK (status IN ('pendiente', 'en_progreso', 'completado'))
);

CREATE INDEX IF NOT EXISTS idx_capa_finding ON capa_actions (finding_id);
