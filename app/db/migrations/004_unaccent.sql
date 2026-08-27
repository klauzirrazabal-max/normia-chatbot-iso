-- Busqueda insensible a acentos.
--
-- Sin esto la busqueda falla en las DOS direcciones:
--
--   * Los titulos vienen de nombres de archivo de macOS, que usa Unicode NFD
--     ("i" + acento combinante). Los literales del codigo son NFC. Bytes
--     distintos: buscar "auditoria" no encontraba "Auditorias".
--
--   * Y al contrario, la gente escribe sin acentos: "atencion de solicitudes"
--     no encontraba "Atencion de Solicitudes Tecnologicas".
--
-- En un asistente de cumplimiento el fallo es grave y silencioso: devuelve cero
-- resultados, o sea el bot dice "no tengo esa informacion" sobre un documento
-- que si existe -- justo lo que el sistema debe evitar.
--
-- unaccent() normaliza y quita diacriticos, asi que las dos formas coinciden.

CREATE EXTENSION IF NOT EXISTS unaccent;

-- Indices funcionales para que la busqueda sin acentos no haga scan completo.
-- IMMUTABLE es requisito del indice; unaccent() con el diccionario por defecto
-- lo es en la practica, y este wrapper lo declara para poder indexarlo.
CREATE OR REPLACE FUNCTION normia_unaccent(text)
    RETURNS text
    LANGUAGE sql
    IMMUTABLE
    PARALLEL SAFE
    STRICT
AS $$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$;

CREATE INDEX IF NOT EXISTS idx_documents_title_unaccent
    ON documents (lower(normia_unaccent(title)));

CREATE INDEX IF NOT EXISTS idx_documents_area_unaccent
    ON documents (lower(normia_unaccent(area)));
