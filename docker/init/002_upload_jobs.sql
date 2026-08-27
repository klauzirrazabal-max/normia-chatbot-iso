-- NormIA: cola de carga de documentos
--
-- Subir un PDF y procesarlo son cosas distintas: leer el membrete, cruzarlo
-- contra lo ya registrado, extraer el texto y calcular embeddings toma
-- segundos por documento. Bloquear la respuesta HTTP mientras tanto haria que
-- subir 20 archivos se sienta roto.
--
-- Esta tabla es la cola: el upload responde de inmediato con un job por
-- archivo, y el procesamiento en segundo plano va actualizando su estado y
-- dejando ahi los avisos que el usuario debe revisar.

CREATE TABLE IF NOT EXISTS upload_jobs (
    id                SERIAL PRIMARY KEY,
    tenant_id         TEXT NOT NULL REFERENCES tenants(id),
    original_filename TEXT NOT NULL,
    stored_path       TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pendiente',
    resolved_code     TEXT,
    resolved_version  TEXT,
    title             TEXT,
    area              TEXT,
    document_id       INTEGER REFERENCES documents(id) ON DELETE SET NULL,
    advices           JSONB NOT NULL DEFAULT '[]'::jsonb,
    error             TEXT,
    chunks_created    INTEGER,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT upload_jobs_status_check CHECK (
        status IN ('pendiente', 'procesando', 'requiere_revision', 'listo', 'error')
    )
);

CREATE INDEX IF NOT EXISTS idx_upload_jobs_tenant_status
    ON upload_jobs (tenant_id, status, created_at DESC);
