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
import threading
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

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
}

# Lo que no tiene sitio en material de un SGC, por muy inocente que suene.
_FUERA_DE_ALCANCE = {
    "party", "beach", "holiday", "vacation", "wedding", "birthday", "game",
    "cartoon", "anime", "superhero", "weapon", "gun", "politician", "celebrity",
    "religious", "nude", "portrait", "selfie", "pet", "landscape", "sunset",
}


def _palabras(texto: str) -> set[str]:
    import re

    return set(re.findall(r"[a-z]+", texto.lower()))


def _fuera_de_alcance(descripcion: str) -> str | None:
    """Devuelve el motivo del rechazo, o None si la descripcion vale."""
    palabras = _palabras(descripcion)

    prohibidas = palabras & _FUERA_DE_ALCANCE
    if prohibidas:
        return (
            "La descripcion menciona algo ajeno al sistema de gestion: "
            + ", ".join(sorted(prohibidas))
        )
    if not palabras & _VOCABULARIO:
        return (
            "La descripcion no parece material del sistema de gestion. Debe hablar "
            "del entorno de trabajo: seguridad, almacen, procesos, documentacion o "
            "formacion. Escribela en ingles, que es como funciona mejor el modelo."
        )
    return None


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


def _clausula_existe(db: Session, referencia: str) -> bool:
    """
    La referencia tiene que corresponder a un documento VIGENTE de la base.

    No se confia en lo que manda el navegador: quien llame al endpoint podria
    escribir cualquier cosa, y una pieza que cita un documento inexistente es
    exactamente el fallo que este proyecto existe para evitar.
    """
    from app.config import settings
    from app.core.guardrails.grounding_check import extract_cited_codes

    codigos = extract_cited_codes(referencia)
    if not codigos:
        return False
    reales = {
        c.split(" (")[0].upper()
        for (c,) in db.query(Document.code)
        .filter_by(tenant_id=settings.default_tenant_id, status="vigente")
        .distinct()
    }
    return bool(codigos & reales)


class PeticionImagen(BaseModel):
    descripcion: str = Field(min_length=8, max_length=400)
    titular: str = Field(min_length=3, max_length=120)
    clausula: str = Field(min_length=3, max_length=120)
    vertical: bool = True
    pasos: int = Field(default=30, ge=8, le=50)


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
    from app.config import settings

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
            "etiqueta": f"{c} {v}" + (f", seccion {s}" if s else "") + f" - {t}",
            "referencia": f"{c} {v}" + (f", seccion {s}" if s else ""),
        }
        for c, v, t, s in filas
    ]


# SIN clave de administracion, a proposito: lo llama el widget, que es publico.
# La proteccion real son los dos guardrails de abajo -- tema dentro del sistema y
# clausula que existe -- mas el limite de peticiones. Exigir la clave aqui
# obligaria a pegarla en el chat de cualquier usuario, que es absurdo.
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

    prompt = f"{peticion.descripcion.strip()}, {MARCA}"
    logger.info("imagenes.generando", extra={"titular": peticion.titular[:60]})

    if not _generando.acquire(blocking=False):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Hay otra pieza generandose en este momento. Son unos 40 segundos: "
            "vuelve a intentarlo cuando termine.",
        )
    try:
        imagen = pipe(
            prompt=prompt,
            negative_prompt=NEGATIVO,
            num_inference_steps=peticion.pasos,
            guidance_scale=7.0,
            height=1024 if peticion.vertical else 768,
            width=768 if peticion.vertical else 1024,
            generator=torch.Generator("cpu").manual_seed(int(time.time()) % 100000),
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
