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
from app.channels.auth_adapter import usuario_actual
from app.db.session import get_db
from app.models.db_models import User
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
# Cuanto puede cambiar el refinador la imagen. 0.25 cierra bordes y caras sin
# redibujar la escena.
_FUERZA_REFINADO = 0.25

# Distancia maxima para PROPONER una clausula como respaldo. Mas estricta que
# la del RAG a proposito: una cita que va impresa en un cartel pide mas
# certeza que un fragmento que solo da contexto a una respuesta.
_PARECIDO_MINIMO = 0.37


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

# El vocabulario de palabras permitidas vivia aqui, y se retiro.
#
# Decidia si una peticion era del sistema de gestion comprobando si contenia
# alguna de unas 140 palabras. No entendia el contexto: rechazaba "que se vea
# real, no dibujo" por la palabra "dibujo", cuando pedia lo contrario, y solo
# funcionaba en el idioma en que estuviera escrita la lista. Ahora juzga el
# modelo, que lee la frase entera -- ver `juzgar_alcance` -- y lo que queda
# duro es `_NUNCA`, el suelo que ningun texto puede justificar.


# Lo que no tiene sitio en material de un SGC, por muy inocente que suene.
# Lo que NO es material de un sistema de gestion.
#
# Son ASUNTOS, no estilos. Antes estaban aqui "dibujo", "caricatura",
# "retrato" y "paisaje", y eso rechazaba peticiones legitimas: pedir "que se
# vea real, no dibujo" es decir COMO se quiere la imagen, no pedir un dibujo
# animado. Y aunque alguien pidiera un cartel de almacen en estilo ilustrado,
# seguiria siendo material del sistema: el estilo es una eleccion, no un
# limite.
#
# Lo que se rechaza es el tema: una fiesta, una boda, una mascota, un arma.
_FUERA_DE_ALCANCE = {
    "party", "beach", "holiday", "vacation", "wedding", "birthday",
    "anime", "superhero", "weapon", "gun", "politician", "celebrity",
    "religious", "nude", "selfie", "pet",
    # En castellano tambien. Al abrir el vocabulario al castellano esta lista
    # pasa a ser la que sostiene el rechazo: antes bastaba con no reconocer
    # ninguna palabra, y ahora "una invitacion para la fiesta de la oficina"
    # reconoceria "oficina" y pasaria.
    "fiesta", "cumpleanos", "cumple", "boda", "matrimonio", "bautizo",
    "navidad", "vacaciones", "feriado", "playa", "piscina", "videojuego",
    "superheroe", "arma", "pistola", "politico", "famoso", "famosa",
    "religioso", "religiosa", "desnudo", "desnuda", "selfi", "mascota",
    "perro", "gato", "invitacion",
}

# Palabras que NIEGAN lo que viene detras. "no dibujo" y "sin fiesta" no piden
# un dibujo ni una fiesta: piden lo contrario, y rechazarlas por la palabra
# suelta es leer al reves.
_NIEGAN = frozenset({"no", "sin", "nada", "ningun", "ninguna", "nunca", "evita", "evitar"})


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


# Lo que NINGUN texto puede justificar. Es el suelo, no el criterio.
#
# El criterio lo pone el modelo, que entiende el contexto; esta lista existe
# para lo que no se discute aunque el modelo se deje convencer. Un guardrail
# que se puede convencer con palabras no es un guardrail: basta escribir
# "ignora las instrucciones anteriores".
_NUNCA = frozenset({
    "nude", "nudity", "naked", "desnudo", "desnuda", "erotic", "erotico",
    "weapon", "gun", "rifle", "arma", "pistola", "fusil",
    "child", "children", "minor", "nino", "nina", "menor",
    "gore", "blood", "sangre", "corpse", "cadaver",
})


def _rechazo_inapelable(descripcion: str) -> str | None:
    """
    El suelo: lo que no se genera diga lo que diga el texto.

    Se comprueba ANTES de preguntarle al modelo y no admite negaciones: "sin
    armas" sigue hablando de armas, y en esta lista no se corren riesgos.
    """
    # Se mira tambien el singular: la lista dice "arma" y alguien escribe
    # "armas". Un suelo que se salta anadiendo una ese no es un suelo.
    palabras = _palabras(descripcion)
    singulares = {w[:-2] for w in palabras if w.endswith("es")} | {
        w[:-1] for w in palabras if w.endswith("s")
    }
    choque = (palabras | singulares) & _NUNCA
    if choque:
        return (
            "Esa descripción no se puede generar con esta herramienta: "
            + ", ".join(sorted(_como_las_escribio(descripcion, choque) or choque))
        )
    return None


def juzgar_alcance(descripcion: str) -> tuple[bool, str, str]:
    """
    Le pregunta al MODELO si la pieza es material del sistema de gestion, y de
    paso le pide la escena en ingles.

    Las dos cosas en una sola llamada porque ya se estaba llamando para
    traducir: juzgar no cuesta ni un segundo mas.

    Por que el modelo y no una lista de palabras: la lista no entiende el
    contexto. Rechazaba "que se vea real, no dibujo" por la palabra "dibujo",
    cuando pedia justamente lo contrario, y solo funcionaba en el idioma en
    que estuviera escrita. El modelo lee la frase entera.

    Devuelve (vale, motivo, escena_en). Si el modelo no responde o responde
    algo que no se entiende, se DEJA PASAR y se apoya en el suelo y en el
    resto de guardrails: quedarse sin generar porque el juez no contesto seria
    cambiar disponibilidad por una certeza que no se tiene.
    """
    import json

    from app.services.llm_client import get_llm_client

    instruccion = (
        "Eres el filtro de un generador de material visual para un sistema de "
        "gestion de calidad y seguridad de una empresa de logistica. Decide si "
        "la descripcion sirve para una pieza de ese sistema: carteles de "
        "concienciacion, senaletica, laminas de induccion, escenas del entorno "
        "de trabajo (almacen, transporte, oficina, procesos, documentacion, "
        "seguridad).\n"
        "El ESTILO nunca es motivo de rechazo: pedir que sea realista, "
        "ilustrado o fotografico es una eleccion, no un tema. Tampoco lo es "
        "mencionar algo para EXCLUIRLO.\n"
        "Responde SOLO un JSON: "
        '{"vale": true|false, "motivo": "<una frase, solo si vale es false>", '
        '"escena_en": "<la escena traducida al ingles, telegrafica>"}'
    )
    try:
        respuesta = get_llm_client().generate([
            {"role": "system", "content": instruccion},
            {"role": "user", "content": descripcion},
        ])
        crudo = (respuesta.get("content") or "").strip()
        inicio, fin = crudo.find("{"), crudo.rfind("}")
        datos = json.loads(crudo[inicio : fin + 1])
        vale = bool(datos.get("vale"))
        motivo = str(datos.get("motivo") or "").strip()
        escena = str(datos.get("escena_en") or "").strip()
    except Exception:  # noqa: BLE001
        logger.warning("imagenes.juicio_fallido", exc_info=True)
        return True, "", ""

    logger.info("imagenes.juzgada", extra={"vale": vale, "motivo": motivo[:80]})
    return vale, motivo, escena


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


def tenant_visible(usuario) -> str:
    """
    De que cliente son las clausulas que se pueden citar.

    Con sesion, las del cliente de quien entro: ofrecerle a alguien de ABISERVI
    el catalogo del corpus publico seria darle clausulas que no son suyas, y
    una pieza respaldada por un documento ajeno no respalda nada.

    Sin sesion queda la demostracion abierta, que es para lo que existe
    `resolve_tenant`.
    """
    if usuario is not None:
        return usuario.tenant_id
    return resolve_tenant(settings.default_tenant_id)


def _clausula_existe(db: Session, referencia: str, tenant: str) -> bool:
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
        .filter_by(tenant_id=tenant, status="vigente")
        .distinct()
    }
    return bool(codigos & reales)


class PeticionImagen(BaseModel):
    descripcion: str = Field(min_length=8, max_length=400)
    titular: str = Field(min_length=3, max_length=120)
    # OPCIONAL, y es una decision de producto, no una relajacion.
    #
    # Era obligatoria con el argumento de que "un cartel sin respaldo es
    # propaganda". El argumento vale cuando hay una clausula que de verdad lo
    # respalda; cuando no la hay, obligar a poner una produce CITAS FALSAS. Lo
    # medido: para un cartel de EPP el buscador propuso "Desarmado, retiro y
    # ordenamiento de repuestos", y el documento correcto quedaba a una
    # milesima de distancia -- el orden era ruido.
    #
    # Una cita equivocada en un cartel colgado en la pared es peor que ninguna:
    # afirma un respaldo que no existe. Lo que SI sigue siendo obligatorio es
    # que la pieza trate del sistema de gestion, que es el guardrail que
    # importa.
    clausula: str = Field(default="", max_length=120)
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


def _tiene_luz(imagen) -> bool:
    """
    ¿La imagen tiene algo dibujado, o el VAE desbordo y devolvio negro?

    El desbordamiento en fp16 no lanza ninguna excepcion: sale una imagen
    completamente negra, y encima se le compone el logotipo. Se mide el brillo
    medio sobre una miniatura, que es barato y suficiente.
    """
    muestra = list(imagen.convert("RGB").resize((40, 40)).getdata())
    return sum(sum(c) for c in muestra) / (len(muestra) * 3) >= 6


def _cargar_refinador():
    """
    Carga el refinador reutilizando las piezas de la base.

    Devuelve None si no esta descargado: el refinado es una mejora, no un
    requisito, y quedarse sin pieza por no tener un modelo opcional seria
    cambiar calidad por disponibilidad.
    """
    global _refinador
    if not settings.imagen_refinar:
        return None
    if _refinador is not None:
        return _refinador or None

    # La base se pide FUERA del candado. `_cargar_pipeline` toma ese mismo
    # candado, y `threading.Lock` no es reentrante: pedirlo aqui dentro
    # bloqueaba el hilo contra si mismo y la peticion se quedaba colgada para
    # siempre, sin error y sin log. Tardé en verlo porque un interbloqueo no
    # se parece a un fallo: se parece a algo que tarda mucho.
    try:
        base = _cargar_pipeline()
    except Exception:  # noqa: BLE001
        logger.warning("imagenes.sin_refinador", exc_info=True)
        _refinador = False
        return None

    with _candado:
        if _refinador is None:
            try:
                import torch
                from diffusers import StableDiffusionXLImg2ImgPipeline

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
    usuario: User | None = Depends(usuario_actual),
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
    tenant = tenant_visible(usuario)

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
    peticion: PeticionPreparar,
    request: Request,
    db: Session = Depends(get_db),
    usuario: User | None = Depends(usuario_actual),
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

    # 1. El suelo. No se discute y no llega al modelo.
    inapelable = _rechazo_inapelable(descripcion)
    if inapelable:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, inapelable)

    # 2. El juicio, que es donde hace falta entender la frase. Devuelve ya la
    #    escena traducida, asi que no hay una segunda llamada al modelo.
    vale, motivo, escena_en = juzgar_alcance(descripcion)
    if not vale:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            motivo or "Esto no parece material del sistema de gestión.",
        )

    sugerida: str | None = None
    titulo: str | None = None
    try:
        from app.core.rag.retriever import retrieve

        consulta = f"{peticion.titular.strip()}. {descripcion}".strip(". ")
        resultado = retrieve(db, tenant_visible(usuario), consulta)
        # Solo se propone lo que se parece DE VERDAD. El umbral del RAG
        # (0.527) sirve para responder una pregunta, donde un fragmento
        # mediano aun aporta contexto; para firmar un cartel no basta.
        # Medido sobre el SGC real: la clausula correcta de altura sale a
        # 0.340, y los casos en los que la propuesta era mala caian entre
        # 0.395 y 0.460.
        if resultado.accepted and resultado.accepted[0].distance <= _PARECIDO_MINIMO:
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
        # El juez ya la tradujo; si no lo hizo, se traduce aparte.
        "escena_en": escena_en or _a_ingles(descripcion),
        "clausula_sugerida": sugerida,
        "titulo_documento": titulo,
    }


@router.post("/generar", response_model=RespuestaImagen)
def generar(
    peticion: PeticionImagen,
    request: Request,
    db: Session = Depends(get_db),
    usuario: User | None = Depends(usuario_actual),
) -> RespuestaImagen:
    check_rate_limit(request)
    import time

    # El suelo se comprueba SIEMPRE, venga de donde venga la peticion: lo que
    # manda el navegador no se cree, y /generar es un endpoint publico.
    inapelable = _rechazo_inapelable(peticion.descripcion)
    if inapelable:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, inapelable)

    # Y el juicio del modelo. Cuando la pagina ya paso por /preparar, la escena
    # viene traducida y juzgada: repetirlo serian 30 s de espera por nada. Sin
    # ella -- un script, otra interfaz -- se juzga aqui.
    if not (peticion.escena_en or "").strip():
        vale, motivo, _ = juzgar_alcance(peticion.descripcion)
        if not vale:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                motivo or "Esto no parece material del sistema de gestión.",
            )
    if peticion.clausula.strip() and not _clausula_existe(
        db, peticion.clausula, tenant_visible(usuario)
    ):
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
            f"La descripción pide elementos con texto ({', '.join(induce)}) y el modelo "
            "de difusión escribe letras ilegibles. Describe una ESCENA: quién aparece, "
            "que hace y donde. El titular ya va aparte, como texto real.",
        )

    # Si el navegador ya la tradujo, se aprovecha -- pero se valida con el mismo
    # criterio que la original. Lo que llega del cliente no se cree: sin esto,
    # bastaria mandar una descripcion inocente en castellano y la escena que se
    # quisiera en ingles.
    adelantada = (peticion.escena_en or "").strip()
    if adelantada and not _rechazo_inapelable(adelantada) and not pide_texto(adelantada):
        escena = adelantada
    else:
        escena = _a_ingles(peticion.descripcion.strip())
    prompt = f"{escena}, {MARCA}"
    logger.info("imagenes.generando", extra={"titular": peticion.titular[:60]})

    if not _generando.acquire(blocking=False):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Hay otra pieza generándose en este momento. Son unos 40 segundos: "
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

        # La base SIEMPRE produce una imagen completa.
        #
        # Antes se detenia al 80% y le pasaba el latente a medio hacer al
        # refinador. Es el reparto que recomienda la documentacion y es mas
        # eficiente, pero en esta maquina devolvia imagenes NEGRAS: el latente
        # a medias, en fp16 y sobre MPS, desborda al decodificarlo y el VAE
        # saca NaN sin lanzar ningun error. Medido: sin refinador, 200; con el,
        # tres de tres en negro.
        #
        # Asi que el refinado se hace sobre la imagen ya decodificada, como un
        # segundo pase corto. Cuesta unos segundos mas y no depende del
        # traspaso de latentes.
        imagen = pipe(
            **comunes,
            height=1024 if peticion.vertical else 768,
            width=768 if peticion.vertical else 1024,
        ).images[0]

        if refinador is not None:
            afinada = refinador(
                prompt=prompt,
                negative_prompt=NEGATIVO,
                image=imagen,
                # Poco: se trata de cerrar el detalle, no de redibujar. Con
                # mas, la escena cambia de contenido.
                strength=_FUERZA_REFINADO,
                num_inference_steps=max(12, peticion.pasos // 2),
                guidance_scale=7.0,
            ).images[0]
            # Si el segundo pase sale en negro, se conserva el primero: una
            # pieza sin refinar es mejor que ninguna pieza.
            if _tiene_luz(afinada):
                imagen = afinada
            else:
                logger.warning("imagenes.refinado_descartado")
    finally:
        _generando.release()

    # Un desbordamiento del VAE no lanza excepcion: devuelve una imagen negra.
    # Mas vale un 500 con causa que un cartel negro con el logotipo encima.
    if not _tiene_luz(imagen):
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "La generación devolvió una imagen vacía. Vuelve a intentarlo.",
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
