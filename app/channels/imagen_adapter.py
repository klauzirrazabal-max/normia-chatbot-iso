"""
Generacion de material visual del SGC, en su propio flujo.

POR QUE NO ES UNA HERRAMIENTA DEL CHAT
---------------------------------------
Generar una imagen tarda unos 40 segundos, y el timeout del LLM son 45. Metida
dentro del turno, la consulta se iria de largo, saltaria el cortacircuitos, y el
usuario se quedaria mirando un chat mudo cuarenta segundos. Un asistente que se
calla asi no se usa.

Aqui va por su propia pagina y su propio endpoint: se pide, se espera viendo que
pasa algo, y se descarga. El chat sigue respondiendo en segundos.

LA TRAZABILIDAD SE MANTIENE
---------------------------
Cada pieza puede citar la clausula que la respalda, igual que las respuestas del
asistente. Un cartel que dice "usa tu EPP" sin decir de donde sale es
propaganda; con la clausula al pie es material del sistema de gestion.
"""

from __future__ import annotations

import base64
import io
import logging
import re
import threading
import unicodedata
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.models.db_models import Document, DocumentChunk
from app.security import (
    check_rate_limit,
    require_admin_key_salvo_demo,
    resolve_tenant,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/imagenes", tags=["imagenes"])

# El pipeline de difusion pesa ~7 GB y tarda ~30 s en cargar. Se carga UNA vez y
# se reutiliza: sin esto, cada peticion pagaria la carga entera y la primera
# imagen tardaria 70 s en vez de 40.
_pipeline: Any = None
_candado = threading.Lock()

# El candado de arriba solo protege la CARGA del pipeline. La inferencia corria
# sin ninguno, asi que dos peticiones simultaneas lanzaban dos difusiones a la
# vez sobre la misma GPU. En una Mac eso no da un error limpio: da memoria
# agotada a mitad, o dos imagenes degradadas, justo durante la demostracion.
#
# Es un candado de UNA plaza y no se espera en cola: quien llegue mientras hay
# una pieza en curso recibe un 429 inmediato. Una pieza tarda cerca de un
# minuto; encolar peticiones solo convierte la espera en un tiempo de espera
# agotado del navegador.
_generando = threading.Lock()

# El refinador es un segundo paso sobre la misma imagen. La base resuelve la
# composicion y el refinador cierra el detalle, que es justo donde fallaba:
# caras fundidas, manos de mas dedos, bordes sucios. Comparte el codificador
# de texto 2 y el VAE con la base, asi que no son dos modelos enteros en
# memoria sino uno y medio.
_refinador = None
_FRACCION_BASE = 0.8  # la base trabaja hasta el 80%; el ultimo 20% lo refina


# --- Guardrail de alcance -----------------------------------------------------
#
# El chat se niega a lo que esta fuera del SGC. Si el generador aceptara
# cualquier descripcion seria una puerta trasera al mismo sistema: una
# herramienta de gestion de calidad que produce imagenes de cualquier cosa.
#
# Dos condiciones, y las dos son obligatorias:
#
#   1. RESPALDO DOCUMENTAL. Hay que citar una clausula, y tiene que existir de
#      verdad -- se comprueba contra la base, no se cree lo que manda el cliente.
#      Es el mismo criterio que `classify_citations` aplica a las respuestas.
#   2. TEMA DENTRO DEL SISTEMA. La descripcion tiene que hablar del entorno de
#      trabajo: seguridad, almacen, procesos, documentacion, formacion.
#
# Sin las dos, no se genera. Un cartel sin respaldo es propaganda.

_VOCABULARIO = {
    # entorno y operacion
    "warehouse", "logistics", "pallet", "pallets", "forklift", "shelf", "shelves",
    "storage", "inventory", "loading", "dock", "shipment", "cargo", "truck",
    "office", "workplace", "workshop", "工場", "factory", "supplier", "delivery",
    # seguridad y salud
    "safety", "helmet", "hard", "vest", "gloves", "goggles", "protective", "ppe",
    "emergency", "exit", "extinguisher", "hazard", "signage", "first", "aid",
    "harness", "ladder", "spill", "fire", "evacuation",
    # calidad, procesos y documentacion
    "quality", "audit", "process", "procedure", "checklist", "clipboard",
    "document", "documents", "folder", "folders", "filing", "cabinet", "form",
    "inspection", "compliance", "standard", "iso", "certificate", "label",
    "training", "induction", "meeting", "presentation", "diagram", "flowchart",
    "worker", "workers", "employee", "staff", "supervisor", "team", "manager",
    "calibration", "measurement", "maintenance", "cleaning", "temperature",
    # --- y lo mismo en castellano ----------------------------------------
    #
    # Faltaba entero. La pagina del generador pide la escena en ingles porque
    # el modelo de difusion rinde mejor asi, pero por el CHAT la gente escribe
    # en castellano y el modelo redacta la descripcion en castellano. Medido:
    # de cuatro peticiones legitimas, solo paso la que por casualidad llevaba
    # la palabra "pallets". "Senaletica de seguridad para el area de carga" se
    # rechazaba por estar fuera de alcance, siendo material del SGC de manual.
    # entorno y operacion
    "almacen", "almacenes", "deposito", "logistica", "paleta", "paletas",
    "montacargas", "estanteria", "estanterias", "anaquel", "inventario",
    "carga", "descarga", "muelle", "despacho", "envio", "camion", "camiones",
    "oficina", "planta", "taller", "fabrica", "proveedor", "proveedores",
    "entrega", "transporte", "ruta", "operario", "operarios", "personal",
    # seguridad y salud
    "seguridad", "casco", "chaleco", "reflectivo", "guantes", "gafas", "lentes",
    "botas", "epp", "proteccion", "emergencia", "salida", "extintor",
    "extintores", "peligro", "riesgo", "riesgos", "senaletica", "senal",
    "senales", "botiquin", "primeros", "auxilios", "arnes", "escalera",
    "derrame", "incendio", "evacuacion", "altura", "alturas",
    # calidad, procesos y documentacion
    "calidad", "auditoria", "auditorias", "proceso", "procesos",
    "procedimiento", "procedimientos", "lista", "verificacion", "documento",
    "documentos", "carpeta", "archivo", "formato", "formatos", "registro",
    "registros", "inspeccion", "cumplimiento", "norma", "certificado",
    "etiqueta", "rotulo", "capacitacion", "induccion", "formacion", "charla",
    "reunion", "presentacion", "diagrama", "flujograma", "cartel", "afiche",
    "lamina", "trabajador", "trabajadores", "empleado", "empleados",
    "equipo", "jefe", "calibracion", "medicion", "mantenimiento",
    "limpieza", "orden", "temperatura", "conformidad",
}

# Lo que no tiene sitio en material de un SGC, por muy inocente que suene.
_FUERA_DE_ALCANCE = {
    "party", "beach", "holiday", "vacation", "wedding", "birthday", "game",
    "cartoon", "anime", "superhero", "weapon", "gun", "politician", "celebrity",
    "religious", "nude", "portrait", "selfie", "pet", "landscape", "sunset",
    # En castellano tambien. Al abrir el vocabulario al castellano esta lista
    # pasa a ser la que sostiene el rechazo: antes bastaba con no reconocer
    # ninguna palabra, y ahora "una invitacion para la fiesta de la oficina"
    # reconoceria "oficina" y pasaria.
    "fiesta", "cumpleanos", "boda", "matrimonio", "bautizo", "navidad",
    "vacaciones", "feriado", "playa", "piscina", "videojuego", "juego",
    "caricatura", "dibujo", "superheroe", "arma", "pistola", "politico",
    "famoso", "famosa", "religioso", "religiosa", "desnudo", "desnuda",
    "retrato", "selfi", "mascota", "perro", "gato", "paisaje", "atardecer",
    "invitacion", "cumple",
}


def _palabras(texto: str) -> set[str]:
    """
    Palabras normalizadas, sin acentos.

    `[a-z]+` sobre el texto crudo partia "senaletica" en "se" y "tica" y
    "almacen" en "almac" y "n": ninguna de las dos coincidia con nada, y la
    peticion se rechazaba por estar fuera de alcance.
    """
    import re
    import unicodedata

    plano = unicodedata.normalize("NFKD", texto.lower())
    plano = "".join(c for c in plano if not unicodedata.combining(c))
    return set(re.findall(r"[a-z]+", plano))


def _fuera_de_alcance(descripcion: str) -> str | None:
    """
    Devuelve el motivo del rechazo, o None si la descripcion vale.

    El motivo lo LEE una persona mientras escribe, asi que va con tildes, y las
    palabras senaladas se devuelven TAL COMO LAS ESCRIBIO: la comparacion se
    hace sin acentos, pero decirle a alguien que ha escrito "cumpleanos" cuando
    escribio "cumpleaños" parece una falta del sistema.
    """
    palabras = _palabras(descripcion)

    prohibidas = palabras & _FUERA_DE_ALCANCE
    if prohibidas:
        return (
            "La descripción menciona algo ajeno al sistema de gestión: "
            + ", ".join(sorted(_como_las_escribio(descripcion, prohibidas)))
        )
    if not palabras & _VOCABULARIO:
        return (
            "Esto no parece material del sistema de gestión. Describe algo del "
            "entorno de trabajo: seguridad, almacén, procesos, documentación o "
            "formación."
        )
    return None


def _como_las_escribio(texto: str, normalizadas: set[str]) -> set[str]:
    """Recupera la grafia original de unas palabras ya normalizadas."""
    originales: set[str] = set()
    for bruta in re.findall(r"[^\W\d_]+", texto, flags=re.UNICODE):
        plano = "".join(
            c for c in unicodedata.normalize("NFKD", bruta.lower())
            if not unicodedata.combining(c)
        )
        if plano in normalizadas:
            originales.add(bruta.lower())
    return originales or normalizadas


# Palabras que hacen que el modelo dibuje TEXTO. No son temas prohibidos: son
# FORMAS que llevan letras dentro. Pedir una "infografia de los 4 pilares"
# produce paneles llenos de garabatos que imitan palabras, y una pieza asi no se
# puede colgar en ninguna pared. Se retiran de la descripcion en vez de
# rechazarla: lo que el usuario quiere -- una escena sobre ese tema -- si se
# puede dar.
_INDUCEN_TEXTO = {
    # Artefactos que EXISTEN para llevar texto. Un infografico sin letras no es
    # un infografico: el modelo rellenara el hueco con garabatos.
    "infographic", "infographics", "chart", "charts", "graph", "graphs",
    "diagram", "diagrams", "poster", "posters", "banner", "banners",
    "signage", "billboard", "slide", "slides", "presentation",
    "label", "labels", "caption", "captions", "headline", "heading",
    "title", "titles", "subtitle", "text", "typography", "lettering",
    "bullet", "bullets", "checklist", "infocard", "leaflet", "brochure",
}
# OJO con lo que NO esta aqui, y es deliberado: "table", "board", "screen",
# "document", "panel", "column", "steps" y "pillars" tienen un significado
# fisico perfectamente inocente -- una mesa, una pared, una escalera. Estaban en
# la primera version y rechazaban "colleagues around a table", que es justo la
# clase de escena que queremos. Una lista demasiado amplia no protege: estorba.


def pide_texto(descripcion: str) -> list[str]:
    """
    Palabras de la descripcion que harian al modelo dibujar TEXTO.

    No son temas prohibidos: son FORMAS que llevan letras dentro. Pedir una
    "infografia de los 4 pilares" produce paneles llenos de garabatos que imitan
    palabras, y una pieza asi no se puede colgar en ninguna pared.

    Se RECHAZA en vez de limpiar la frase. Se probo quitar las palabras y lo que
    quedaba era "with four showing the of quality management": una descripcion
    rota genera una imagen rota. Es mejor decir que hace falta describir una
    escena, que es lo que el modelo sabe dibujar.
    """
    import re

    return sorted({
        p.lower() for p in re.findall(r"[A-Za-z]+", descripcion)
        if p.lower() in _INDUCEN_TEXTO
    })


# El modelo de difusion entiende ingles. Su codificador de texto se entreno en
# ingles, y una escena descrita en castellano sale peor: menos detalle, objetos
# que faltan, a veces nada parecido a lo pedido.
#
# La solucion NO es pedirle al usuario que escriba en ingles. Quien usa esto es
# un jefe de almacen en Lima, y la pista "En ingles funciona mejor" convertia la
# herramienta en algo para quien sabe ingles. Traduce el sistema, que para eso
# tiene un modelo de lenguaje corriendo al lado.
_INDICIOS_CASTELLANO = frozenset({
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "en",
    "con", "sin", "sobre", "para", "por", "que", "y", "o", "su", "sus", "al",
    "este", "esta", "junto", "mientras", "donde", "dos", "tres",
})


def _parece_castellano(texto: str) -> bool:
    """
    Heuristica barata: tildes, enes, o varias palabras funcionales castellanas.

    Una sola coincidencia no basta -- "a" y "o" existen en los dos idiomas --
    asi que se exigen dos. Equivocarse hacia el lado de NO traducir es el lado
    barato del error: la descripcion llega tal cual, como antes.
    """
    bajo = texto.lower()
    if any(c in bajo for c in "áéíóúñ¿¡"):
        return True
    palabras = set(re.findall(r"[a-záéíóúñ]+", bajo))
    return len(palabras & _INDICIOS_CASTELLANO) >= 2


def _a_ingles(descripcion: str) -> str:
    """
    Traduce la escena al ingles para el modelo de difusion.

    Si la traduccion falla por lo que sea -- modelo caido, respuesta rara --
    se devuelve el original: una pieza peor es mejor que ninguna pieza, y el
    usuario no tiene por que enterarse de que hay un paso intermedio.
    """
    if not _parece_castellano(descripcion):
        return descripcion

    from app.services.llm_client import get_llm_client

    try:
        respuesta = get_llm_client().generate([
            {
                "role": "system",
                "content": (
                    "Traduce al ingles la descripcion de una escena para un generador "
                    "de imagenes. Devuelve SOLO la traduccion, en una linea, sin "
                    "comillas, sin explicaciones y sin anadir nada que no este en el "
                    "original. Conserva el estilo telegrafico de un prompt."
                ),
            },
            {"role": "user", "content": descripcion},
        ])
        traducida = (respuesta.get("content") or "").strip().strip('"').split("\n")[0]
    except Exception:  # noqa: BLE001 - traducir es una mejora, no un requisito
        logger.warning("imagenes.traduccion_fallida", exc_info=True)
        return descripcion

    # Una respuesta vacia o absurdamente larga es el modelo divagando.
    if not traducida or len(traducida) > len(descripcion) * 3 + 60:
        logger.warning("imagenes.traduccion_descartada", extra={"largo": len(traducida)})
        return descripcion

    logger.info("imagenes.traducida", extra={"de": descripcion[:60], "a": traducida[:60]})
    return traducida


def _clausula_existe(db: Session, referencia: str) -> bool:
    """
    La referencia tiene que corresponder a un documento VIGENTE de la base.

    No se confia en lo que manda el navegador: quien llame al endpoint podria
    escribir cualquier cosa, y una pieza que cita un documento inexistente es
    exactamente el fallo que este proyecto existe para evitar.
    """
    from app.core.guardrails.grounding_check import extract_cited_codes

    codigos = extract_cited_codes(referencia)
    if not codigos:
        return False
    # EL MISMO tenant que sirve el catalogo. Se quedo apuntando a
    # `default_tenant_id` cuando el catalogo paso a respetar el tenant publico,
    # y el resultado fue que el desplegable ofrecia clausulas que el validador
    # rechazaba: la pieza era imposible de generar eligiendo cualquier opcion.
    reales = {
        c.split(" (")[0].upper()
        for (c,) in db.query(Document.code)
        .filter_by(tenant_id=resolve_tenant(settings.default_tenant_id), status="vigente")
        .distinct()
    }
    return bool(codigos & reales)


class PeticionImagen(BaseModel):
    descripcion: str = Field(min_length=8, max_length=400)
    titular: str = Field(min_length=3, max_length=120)
    clausula: str = Field(min_length=3, max_length=120)
    vertical: bool = True
    # 30 pasos dejaban la anatomia a medio resolver. A 42 el coste sube unos 8 s
    # y las manos y las caras se cierran. Por encima de 50 no se nota.
    pasos: int = Field(default=42, ge=8, le=50)
    # La escena ya traducida al ingles, si el navegador la pidio mientras el
    # usuario elegia la clausula. Es solo un ahorro de tiempo: el servidor la
    # vuelve a validar y, si no viene, traduce el mismo.
    escena_en: str | None = Field(default=None, max_length=600)


class RespuestaImagen(BaseModel):
    imagen: str  # data URI, para que el navegador la pinte sin fichero temporal
    titular: str
    clausula: str
    segundos: float


def _cargar_pipeline():
    """
    Carga perezosa y bajo candado.

    El candado no es paranoia: dos peticiones simultaneas cargarian dos copias
    de 7 GB y la memoria unificada no lo aguanta.
    """
    global _pipeline
    with _candado:
        if _pipeline is None:
            import torch
            from diffusers import AutoencoderKL, StableDiffusionXLPipeline

            logger.info("imagenes.cargando_pipeline")
            # El VAE original desborda en float16 y devuelve NaN: la imagen sale
            # completamente negra, sin error. Medido: 4 de 5 piezas asi.
            vae = AutoencoderKL.from_pretrained(
                "madebyollin/sdxl-vae-fp16-fix", torch_dtype=torch.float16
            )
            _pipeline = StableDiffusionXLPipeline.from_pretrained(
                "stabilityai/stable-diffusion-xl-base-1.0",
                vae=vae, torch_dtype=torch.float16,
                variant="fp16", use_safetensors=True,
            ).to("mps")
            _pipeline.enable_attention_slicing()
            logger.info("imagenes.pipeline_listo")
    return _pipeline


def _cargar_refinador():
    """
    Carga el refinador reutilizando las piezas de la base.

    Devuelve None si no esta descargado: el refinado es una mejora, no un
    requisito, y quedarse sin pieza por no tener un modelo opcional seria
    cambiar calidad por disponibilidad.
    """
    global _refinador
    if _refinador is not None:
        return _refinador

    with _candado:
        if _refinador is None:
            try:
                import torch
                from diffusers import StableDiffusionXLImg2ImgPipeline

                base = _cargar_pipeline()
                logger.info("imagenes.cargando_refinador")
                _refinador = StableDiffusionXLImg2ImgPipeline.from_pretrained(
                    "stabilityai/stable-diffusion-xl-refiner-1.0",
                    text_encoder_2=base.text_encoder_2,
                    vae=base.vae,
                    torch_dtype=torch.float16,
                    variant="fp16",
                    use_safetensors=True,
                ).to("mps")
                _refinador.enable_attention_slicing()
                logger.info("imagenes.refinador_listo")
            except Exception:  # noqa: BLE001
                logger.warning("imagenes.sin_refinador", exc_info=True)
                _refinador = False
    return _refinador or None


@router.get("/clausulas")
def clausulas_disponibles(
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_key_salvo_demo),
) -> list[dict[str, str]]:
    """
    El catalogo de cláusulas vigentes, para que la pieza cite una que existe.

    Se sirve desde la base y no se deja escribir a mano: es el mismo criterio
    que en el chat -- el sistema nunca cita un documento que no existe.
    """
    # El MISMO criterio que el chat: con tenant publico configurado se sirve ese
    # y ninguno otro. Antes se servia siempre `default_tenant_id`, el SGC real
    # del cliente, y por eso hacia falta la clave.
    tenant = resolve_tenant(settings.default_tenant_id)

    filas = (
        db.query(Document.code, Document.version, Document.title, DocumentChunk.section)
        .join(DocumentChunk, DocumentChunk.document_id == Document.id)
        .filter(Document.tenant_id == tenant, Document.status == "vigente")
        .order_by(Document.code, DocumentChunk.section)
        .limit(400)
        .all()
    )
    return [
        {
            # La referencia se IMPRIME en la pieza, debajo del titular: ahi
            # "seccion" sin tilde se ve, y lo que se reparte por la empresa no
            # puede llevar una falta.
            "etiqueta": f"{c} {v}" + (f", sección {s}" if s else "") + f" - {t}",
            "referencia": f"{c} {v}" + (f", sección {s}" if s else ""),
        }
        for c, v, t, s in filas
    ]


# SIN clave de administracion, a proposito: lo llama el widget, que es publico.
# La proteccion real son los dos guardrails de abajo -- tema dentro del sistema y
# clausula que existe -- mas el limite de peticiones. Exigir la clave aqui
# obligaria a pegarla en el chat de cualquier usuario, que es absurdo.
class PeticionPreparar(BaseModel):
    descripcion: str = Field(min_length=8, max_length=400)
    # El titular es el MENSAJE de la pieza, y es mejor pista que la escena para
    # encontrar la clausula: "El equipo de proteccion no es opcional" apunta al
    # procedimiento; "un operario con casco en un pasillo" describe un dibujo.
    titular: str = Field(default="", max_length=120)


@router.post("/preparar")
def preparar(
    peticion: PeticionPreparar, request: Request, db: Session = Depends(get_db)
) -> dict[str, str | None]:
    """
    Prepara la pieza mientras el usuario sigue rellenando: traduce y busca el
    respaldo.

    Resuelve dos problemas de una sola llamada.

    El primero es de TIEMPO. Traducir tarda unos 30 s con el modelo que corre
    en esta maquina y generar otros 27: encadenados son casi un minuto mirando
    un recuadro vacio. Pidiendolo en cuanto el usuario sale del campo de la
    escena, ocurre mientras elige la orientacion, y al pulsar Generar solo
    queda la difusion.

    El segundo es de CARGA MENTAL. Toda pieza tiene que citar una clausula --
    un cartel sin respaldo es propaganda -- pero elegirla de una lista de 60 en
    un desplegable obliga a saber que el EPP es "SST-PR-02 v2, seccion 5.1".
    Quien va a usar esto es un jefe de almacen. El requisito se queda; el
    trabajo lo hace el buscador, que para eso esta. El usuario confirma o
    cambia, que es una decision de un segundo en vez de una busqueda.
    """
    check_rate_limit(request)
    descripcion = peticion.descripcion.strip()

    motivo = _fuera_de_alcance(descripcion)
    if motivo:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, motivo)

    sugerida: str | None = None
    titulo: str | None = None
    try:
        from app.core.rag.retriever import retrieve

        consulta = f"{peticion.titular.strip()}. {descripcion}".strip(". ")
        resultado = retrieve(db, resolve_tenant(settings.default_tenant_id), consulta)
        if resultado.accepted:
            mejor = resultado.accepted[0]
            sugerida = mejor.citation_label.replace("seccion", "sección")
            # El TITULO es lo que permite ver si la propuesta tiene sentido.
            # Con solo el codigo, confirmar es un sello: nadie distingue si
            # "SST-PR-02 v2, seccion 7.1" respalda un cartel sobre documentos
            # en oficina. Con "Uso de Equipos de Proteccion Personal" delante,
            # el error salta a la vista.
            titulo = mejor.title
    except Exception:  # noqa: BLE001 - sugerir es una ayuda, no un requisito
        logger.warning("imagenes.sugerencia_fallida", exc_info=True)

    return {
        "escena_en": _a_ingles(descripcion),
        "clausula_sugerida": sugerida,
        "titulo_documento": titulo,
    }


@router.post("/generar", response_model=RespuestaImagen)
def generar(
    peticion: PeticionImagen,
    request: Request,
    db: Session = Depends(get_db),
) -> RespuestaImagen:
    check_rate_limit(request)
    import time

    motivo = _fuera_de_alcance(peticion.descripcion)
    if motivo:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, motivo)
    if not _clausula_existe(db, peticion.clausula):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"«{peticion.clausula}» no es un documento vigente del SGC. El material "
            "visual tiene que citar una clausula que exista.",
        )

    try:
        import torch
    except ImportError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Falta el grupo de dependencias de imagen: uv sync --group imagen",
        ) from None

    from scripts.generar_imagenes import MARCA, NEGATIVO, Pieza, componer

    inicio = time.perf_counter()
    pipe = _cargar_pipeline()

    induce = pide_texto(peticion.descripcion)
    if induce:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"La descripcion pide elementos con texto ({', '.join(induce)}) y el modelo "
            "de difusion escribe letras ilegibles. Describe una ESCENA: quien aparece, "
            "que hace y donde. El titular ya va aparte, como texto real.",
        )

    # Si el navegador ya la tradujo, se aprovecha -- pero se valida con el mismo
    # criterio que la original. Lo que llega del cliente no se cree: sin esto,
    # bastaria mandar una descripcion inocente en castellano y la escena que se
    # quisiera en ingles.
    adelantada = (peticion.escena_en or "").strip()
    if adelantada and not _fuera_de_alcance(adelantada) and not pide_texto(adelantada):
        escena = adelantada
    else:
        escena = _a_ingles(peticion.descripcion.strip())
    prompt = f"{escena}, {MARCA}"
    logger.info("imagenes.generando", extra={"titular": peticion.titular[:60]})

    if not _generando.acquire(blocking=False):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Hay otra pieza generandose en este momento. Son unos 40 segundos: "
            "vuelve a intentarlo cuando termine.",
        )
    try:
        semilla = torch.Generator("cpu").manual_seed(int(time.time()) % 100000)
        refinador = _cargar_refinador()

        comunes = {
            "prompt": prompt,
            "negative_prompt": NEGATIVO,
            "num_inference_steps": peticion.pasos,
            "guidance_scale": 7.0,
            "generator": semilla,
        }

        if refinador is None:
            imagen = pipe(
                **comunes,
                height=1024 if peticion.vertical else 768,
                width=768 if peticion.vertical else 1024,
            ).images[0]
        else:
            # Dos pasos sobre la MISMA imagen. La base resuelve la composicion
            # y se detiene al 80%; el refinador toma el latente a medio hacer y
            # cierra el ultimo 20%, que es donde estaba el problema: caras
            # fundidas, manos con dedos de mas, bordes sucios.
            #
            # `denoising_end` y `denoising_start` tienen que sumar el corte
            # exacto, o el refinador empieza desde un ruido que no corresponde
            # y la imagen cambia de contenido en vez de afinarse.
            latente = pipe(
                **comunes,
                height=1024 if peticion.vertical else 768,
                width=768 if peticion.vertical else 1024,
                denoising_end=_FRACCION_BASE,
                output_type="latent",
            ).images
            imagen = refinador(
                **comunes,
                image=latente,
                denoising_start=_FRACCION_BASE,
            ).images[0]
    finally:
        _generando.release()

    # Un desbordamiento del VAE no lanza excepcion: devuelve una imagen negra.
    # Mas vale un 500 con causa que un cartel negro con el logotipo encima.
    muestra = list(imagen.convert("RGB").resize((40, 40)).getdata())
    if sum(sum(c) for c in muestra) / (len(muestra) * 3) < 6:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "La generacion devolvio una imagen vacia. Vuelve a intentarlo.",
        )

    # Se compone con el MISMO codigo que el script de linea de comandos, para que
    # la pieza que sale de la web sea identica a la que sale del terminal.
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        arte = Path(tmp) / "arte.png"
        final = Path(tmp) / "final.png"
        imagen.save(arte)
        componer(
            arte,
            Pieza(
                nombre="web", clausula=peticion.clausula,
                titular=peticion.titular, prompt=prompt, semilla=0,
            ),
            final,
        )
        datos = final.read_bytes()

    buffer = io.BytesIO(datos)
    return RespuestaImagen(
        imagen="data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode(),
        titular=peticion.titular,
        clausula=peticion.clausula,
        segundos=round(time.perf_counter() - inicio, 1),
    )
