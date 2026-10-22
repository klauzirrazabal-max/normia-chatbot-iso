-- Cierre del ciclo CAPA y consentimiento antes de derivar.
--
-- Dos huecos que un auditor encuentra enseguida:
--
--  1. `findings` guardaba solo la descripcion. La clausula 10.2 pide ademas la
--     naturaleza de la no conformidad (severidad) y donde ocurrio (area), y sin
--     eso un hallazgo no se puede priorizar ni asignar.
--
--  2. El asistente anunciaba la derivacion a Calidad sin preguntar, asi que la
--     bandeja se llenaba de consultas que nadie pidio derivar. La escalacion
--     nace ahora como 'propuesta' y solo pasa a 'pendiente' si el usuario
--     acepta. Las propuestas no entran en la bandeja, pero se conservan: son
--     la lista de preguntas que el SGC no sabe responder.

ALTER TABLE findings
    ADD COLUMN IF NOT EXISTS severity TEXT NOT NULL DEFAULT 'menor',
    ADD COLUMN IF NOT EXISTS area     TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'findings_severity_check'
    ) THEN
        ALTER TABLE findings ADD CONSTRAINT findings_severity_check
            CHECK (severity IN ('observacion', 'menor', 'mayor'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_findings_tenant_status
    ON findings (tenant_id, status, created_at DESC);

-- Un hallazgo abierto no puede tener dos acciones correctivas identicas: es el
-- mismo texto repetido porque alguien reintento, no dos acciones distintas.
CREATE UNIQUE INDEX IF NOT EXISTS idx_capa_sin_duplicados
    ON capa_actions (finding_id, md5(description));

ALTER TABLE escalations DROP CONSTRAINT IF EXISTS escalations_status_check;
ALTER TABLE escalations ADD CONSTRAINT escalations_status_check
    CHECK (status IN ('propuesta', 'pendiente', 'en_revision', 'resuelta', 'descartada'));

-- El indice de la bandeja apuntaba a todas las pendientes. Ahora la bandeja son
-- las aceptadas, y el indice parcial deja fuera el resto.
DROP INDEX IF EXISTS idx_escalations_pendientes;
CREATE INDEX IF NOT EXISTS idx_escalations_bandeja
    ON escalations (tenant_id, created_at DESC)
    WHERE status IN ('pendiente', 'en_revision');
