-- NormIA: registro de documentos controlados (Lista Maestra)
--
-- En un SGC ISO la Lista Maestra de Documentos Internos es la fuente
-- autoritativa de que documentos existen, en que version y desde cuando. Hasta
-- ahora se parseaba en tiempo de script para generar el metadata.csv y se
-- descartaba; el sistema solo conocia los documentos INGESTADOS.
--
-- Esa diferencia causaba falsos positivos: los formatos INV-FO-02, 06, 11, 13,
-- 16, 18, 21 y GTH-FO-14 estan registrados en la Lista Maestra, pero sus PDFs
-- vienen agrupados en archivos combinados y nunca se ingestaron por separado.
-- El verificador de citas los marcaba como codigos inexistentes y descartaba
-- respuestas correctas.
--
-- Con el registro en la base, "existe en el SGC" y "esta indexado" pasan a ser
-- dos preguntas distintas -- que es lo que son. Y la diferencia entre ambas es
-- una metrica util: cuanta parte del SGC cubre realmente el asistente.

CREATE TABLE IF NOT EXISTS document_registry (
    id              SERIAL PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    code            TEXT NOT NULL,
    version         TEXT NOT NULL,
    title           TEXT,
    process         TEXT,
    doc_type        TEXT,
    effective_date  DATE,
    source          TEXT NOT NULL DEFAULT 'lista_maestra',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, code)
);

CREATE INDEX IF NOT EXISTS idx_registry_tenant ON document_registry (tenant_id);
