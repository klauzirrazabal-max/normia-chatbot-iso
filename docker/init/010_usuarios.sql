-- Quien eres y que puedes hacer.
--
-- Hasta ahora no habia usuarios: solo una clave de administracion que el
-- navegador pedia con un dialogo. Eso basta para una demostracion y no basta
-- para un SGC, porque marcar un documento como obsoleto o cerrar una
-- escalacion son actos controlados y la norma pregunta QUIEN los hizo.
--
-- Tres roles:
--   operario  consulta y reporta hallazgos. No entra al panel.
--   calidad   el panel entero: documentos, hallazgos, escalaciones.
--   admin     ademas, da de alta usuarios.

CREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    tenant_id   TEXT NOT NULL REFERENCES tenants(id),
    email       TEXT NOT NULL,
    nombre      TEXT NOT NULL,
    -- scrypt de la biblioteca estandar: 'scrypt$n$r$p$sal$hash'. Se evita a
    -- proposito anadir una dependencia de criptografia para esto.
    password    TEXT NOT NULL,
    rol         TEXT NOT NULL DEFAULT 'operario',
    activo      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login  TIMESTAMPTZ,
    CONSTRAINT users_rol_check CHECK (rol IN ('operario', 'calidad', 'admin')),
    UNIQUE (email)
);

CREATE INDEX IF NOT EXISTS idx_users_tenant ON users (tenant_id, activo);

-- AUTORIA de los actos controlados. Un documento que pasa a obsoleto sin
-- constar quien lo decidio es una no conformidad de control documental.
ALTER TABLE documents    ADD COLUMN IF NOT EXISTS actualizado_por TEXT;
ALTER TABLE documents    ADD COLUMN IF NOT EXISTS actualizado_en  TIMESTAMPTZ;
ALTER TABLE escalations  ADD COLUMN IF NOT EXISTS resuelta_por    TEXT;
ALTER TABLE findings     ADD COLUMN IF NOT EXISTS registrado_por  TEXT;
ALTER TABLE capa_actions ADD COLUMN IF NOT EXISTS creada_por      TEXT;
