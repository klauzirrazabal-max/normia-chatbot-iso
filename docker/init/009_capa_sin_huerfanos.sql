-- Una accion correctiva sin responsable o sin plazo no es una accion: es una
-- intencion. La regla de negocio es estricta -- no puede haber CAPAs huerfanos --
-- y hasta ahora la herramienta los creaba igual, limitandose a avisar de lo que
-- faltaba. En la base habia uno.
--
-- Se arregla en los dos sitios: la herramienta se niega y pide los datos, y la
-- base lo impide aunque alguien escriba por otro camino.

DELETE FROM capa_actions WHERE responsible IS NULL OR due_date IS NULL;

ALTER TABLE capa_actions ALTER COLUMN responsible SET NOT NULL;
ALTER TABLE capa_actions ALTER COLUMN due_date    SET NOT NULL;
