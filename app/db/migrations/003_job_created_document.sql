-- Un job que resubio un documento YA existente no debe poder borrarlo al
-- descartarse: "Quitar archivo" retiraria el documento original del SGC.
-- Esta bandera distingue "yo cree este documento" de "yo actualice uno que ya estaba".
ALTER TABLE upload_jobs
    ADD COLUMN IF NOT EXISTS created_document BOOLEAN NOT NULL DEFAULT false;
