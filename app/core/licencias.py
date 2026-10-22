"""
Que modelo usa el sistema, bajo que licencia, y que obliga esa licencia.

Esto no es documentacion: es una obligacion contractual que el software tiene
que cumplir en tiempo de ejecucion.

La OpenRAIL++ de SDXL permite el uso comercial y el hosting como SaaS de forma
explicita -- "You may host for Third Party remote access purposes (e.g.
software-as-a-service)" -- pero lo condiciona:

    "Use-based restrictions as referenced in paragraph 5 MUST be included as an
     enforceable provision by You in any type of legal agreement governing the
     use and/or distribution of the Model [...] and You shall give notice to
     subsequent users You Distribute to"

Es decir: subir esto a produccion sin mostrar las restricciones de uso a quien
genera las imagenes incumple la licencia. Mostrarlas, si cumple. Por eso el
registro vive en el codigo y se sirve por API, en vez de quedarse en un README
que nadie despliega.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Componente:
    nombre: str
    papel: str
    licencia: str
    uso_comercial: bool
    obligaciones: tuple[str, ...] = ()
    nota: str = ""


# Las restricciones del Anexo A de la OpenRAIL++, en castellano. Son las que hay
# que ensenarle al usuario antes de que genere una imagen.
RESTRICCIONES_OPENRAIL: tuple[str, ...] = (
    "Incumplir cualquier ley o reglamento aplicable.",
    "Explotar o dañar a menores de edad.",
    "Generar o difundir información falsa con intención de dañar a terceros.",
    "Generar o difundir datos personales que puedan usarse para dañar a alguien.",
    "Difamar, denigrar u hostigar a terceros.",
    "Tomar decisiones totalmente automatizadas que afecten derechos legales de "
    "una persona o creen obligaciones vinculantes.",
    "Discriminar o dañar a personas o grupos por su comportamiento social o por "
    "características personales reales o inferidas.",
    "Explotar vulnerabilidades de un grupo por su edad o condición física o "
    "mental para distorsionar su conducta y causarle daño.",
    "Discriminar por características o categorías legalmente protegidas.",
    "Dar consejo médico o interpretar resultados médicos.",
    "Generar información destinada a administración de justicia, aplicación de "
    "la ley, inmigración o asilo, como predecir que alguien cometerá un delito.",
)


COMPONENTES: tuple[Componente, ...] = (
    Componente(
        nombre="Qwen3",
        papel="Modelo de lenguaje que redacta las respuestas",
        licencia="Apache 2.0",
        uso_comercial=True,
        obligaciones=("Conservar el aviso de copyright y la licencia.",),
    ),
    Componente(
        nombre="BAAI/bge-m3",
        papel="Embeddings para la búsqueda semántica",
        licencia="MIT",
        uso_comercial=True,
        obligaciones=("Conservar el aviso de copyright y la licencia.",),
    ),
    Componente(
        nombre="PostgreSQL 16 + pgvector",
        papel="Almacén de documentos y vectores",
        licencia="PostgreSQL License",
        uso_comercial=True,
    ),
    Componente(
        nombre="stabilityai/stable-diffusion-xl-base-1.0",
        papel="Generación de material visual del sistema de gestión",
        licencia="CreativeML Open RAIL++-M",
        uso_comercial=True,
        obligaciones=(
            "Mostrar las restricciones de uso del Anexo A a quien use el servicio.",
            "Incluir esas restricciones como cláusula exigible en los términos de uso.",
            "Entregar copia de la licencia a quien reciba el modelo o un derivado.",
        ),
        nota=(
            "Permite el uso comercial y el alojamiento como servicio. No es una "
            "licencia aprobada por la OSI: impone restricciones de uso que hay que "
            "propagar aguas abajo. Por eso este sistema las muestra antes de generar."
        ),
    ),
    Componente(
        nombre="madebyollin/sdxl-vae-fp16-fix",
        papel="Decodificador VAE reescalado para fp16",
        licencia="MIT",
        uso_comercial=True,
    ),
)


# Alternativa evaluada y por que no esta desplegada todavia. Se deja escrito
# porque la pregunta "¿y por que no usan el de licencia mas limpia?" tiene una
# respuesta concreta y conviene no improvisarla.
ALTERNATIVA_IMAGEN: dict[str, str] = {
    "modelo": "black-forest-labs/FLUX.1-schnell",
    "licencia": "Apache 2.0",
    "ventaja": (
        "Sin restricciones de uso que propagar, y con mejor representación de texto "
        "dentro de la imagen, que es la debilidad medida de SDXL."
    ),
    "impedimento": (
        "El repositorio está restringido (gated): sin credencial devuelve HTTP 401, "
        "así que un despliegue no puede descargarlo por su cuenta. La licencia "
        "Apache 2.0 sí permite redistribuirlo, de modo que el camino es descargarlo "
        "una vez con cuenta propia y servirlo desde almacenamiento propio."
    ),
}


def inventario() -> dict:
    """El registro completo, para servirlo por API y para la defensa del proyecto."""
    return {
        "componentes": [
            {
                "nombre": c.nombre,
                "papel": c.papel,
                "licencia": c.licencia,
                "uso_comercial": c.uso_comercial,
                "obligaciones": list(c.obligaciones),
                "nota": c.nota,
            }
            for c in COMPONENTES
        ],
        "restricciones_de_uso": list(RESTRICCIONES_OPENRAIL),
        "alternativa_imagen": ALTERNATIVA_IMAGEN,
        "todos_aptos_para_produccion": all(c.uso_comercial for c in COMPONENTES),
    }
