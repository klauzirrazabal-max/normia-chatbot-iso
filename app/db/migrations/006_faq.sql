-- NormIA: entradas de FAQ como fuente de recuperacion
--
-- El FAQ NO es un cache de respuestas. Servir una respuesta enlatada sin pasar
-- por el orquestador saltaria toda la capa de verificacion -- citas, versiones,
-- grounding -- y seria justo el camino mas rapido, o sea el mas usado. El modelo
-- decide siempre.
--
-- Lo que aporta es resolver el DESAJUSTE DE VOCABULARIO. La clausula dice "El
-- Jefe de TI informa al solicitante el estado de la solicitud" y la persona
-- pregunta "quien me avisa como va mi solicitud?": lenguaje de procedimiento
-- contra lenguaje de persona, semanticamente lejos. La entrada del FAQ esta
-- escrita como pregunta, asi que buscar pregunta-contra-pregunta acierta donde
-- pregunta-contra-clausula falla.
--
-- La invalidacion es estructural, no un proceso aparte: la entrada cuelga del
-- documento y del chunk. Si el documento se borra, sus entradas se van con el;
-- si pasa a obsoleto, el filtro de vigencia deja de recuperarlas.

CREATE TABLE IF NOT EXISTS faq_entries (
    id              SERIAL PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    document_id     INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_id        INTEGER REFERENCES document_chunks(id) ON DELETE SET NULL,
    section         TEXT,
    question        TEXT NOT NULL,
    answer          TEXT NOT NULL,
    -- Embedding de la PREGUNTA, no de la respuesta: se compara contra lo que
    -- escribe el usuario, que tambien es una pregunta.
    embedding       vector(1024) NOT NULL,
    -- Solo Calidad marca esto. Distingue lo validado por una persona de lo que
    -- genero el modelo, para que el asistente sepa a que darle mas peso.
    reviewed        BOOLEAN NOT NULL DEFAULT false,
    reviewed_by     TEXT,
    reviewed_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, document_id, question)
);

CREATE INDEX IF NOT EXISTS idx_faq_embedding_hnsw
    ON faq_entries USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_faq_tenant_reviewed
    ON faq_entries (tenant_id, reviewed);
