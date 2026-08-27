-- NormIA: escalaciones al Responsable de Calidad
--
-- Antes de esta tabla, `escalate_to_quality` solo escribia una linea de log:
-- el bot le decia al usuario "lo derive al Responsable de Calidad" y nadie era
-- derivado a ninguna parte. Se contaron 39 escalaciones sin un solo registro.
--
-- En un sistema de cumplimiento eso es PEOR que decir "no se": genera un
-- registro falso de accion. Si en una auditoria preguntan que paso con una
-- consulta escalada, no habia respuesta.
--
-- Ademas la lista de escalaciones es la mejor fuente de mejora del SGC: son las
-- preguntas reales que la documentacion no cubre.

CREATE TABLE IF NOT EXISTS escalations (
    id              SERIAL PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    conversation_id INTEGER REFERENCES conversations(id) ON DELETE SET NULL,
    channel         TEXT,
    question        TEXT NOT NULL,     -- la pregunta original del usuario
    reason          TEXT NOT NULL,     -- por que se escalo
    trigger         TEXT NOT NULL,     -- sin_contexto | fuera_de_alcance | error
    status          TEXT NOT NULL DEFAULT 'pendiente',
    assigned_to     TEXT,
    resolution      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at     TIMESTAMPTZ,
    CONSTRAINT escalations_status_check CHECK (
        status IN ('pendiente', 'en_revision', 'resuelta', 'descartada')
    ),
    CONSTRAINT escalations_trigger_check CHECK (
        trigger IN ('sin_contexto', 'fuera_de_alcance', 'error')
    )
);

CREATE INDEX IF NOT EXISTS idx_escalations_pendientes
    ON escalations (tenant_id, status, created_at DESC);


-- Incidencias tecnicas
--
-- Un fallo del LLM o de la base dejaba al usuario con "estoy con problemas
-- tecnicos" y nada que reportar, y al operador con una linea de log. Cada
-- incidencia recibe un identificador corto que el usuario puede citar y que
-- correlaciona con el log.

CREATE TABLE IF NOT EXISTS incidents (
    id              SERIAL PRIMARY KEY,
    reference       TEXT NOT NULL UNIQUE,   -- lo que se le muestra al usuario
    tenant_id       TEXT REFERENCES tenants(id),
    conversation_id INTEGER REFERENCES conversations(id) ON DELETE SET NULL,
    kind            TEXT NOT NULL,          -- llm | database | ingestion | unknown
    detail          TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_incidents_recientes
    ON incidents (created_at DESC);
