"""
Validacion de una entrada de FAQ contra su clausula de origen.

Un FAQ de cumplimiento con una cifra inventada es peor que no tener FAQ: la
gente lo lee sin abrir el documento, y una respuesta que dice "el plazo es de 5
dias" cuando el procedimiento dice 3 se convierte en una no conformidad con
apariencia oficial.

La verificacion es DETERMINISTA a proposito. Pedirle a un modelo que juzgue si
otro modelo acerto es util como segunda opinion, pero no como garantia: los dos
comparten los mismos sesgos. Comparar cifras contra el texto fuente no opina,
comprueba.

Tres chequeos, de mas a menos grave:

  1. CIFRAS      -> todo numero de la respuesta debe estar en la clausula.
  2. CODIGOS     -> todo codigo de documento citado debe estar en la clausula
                    o ser el documento mismo.
  3. INCERTIDUMBRE -> la respuesta no debe hablar en condicional ni admitir que
                    no sabe; eso significa que la clausula no la respondia.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

# Numeros de la respuesta y del texto fuente. Se comparan normalizados, porque
# el modelo escribe "1 hora" donde la tabla dice "01 hora".
_NUMERO_RE = re.compile(r"\d+(?:[.,]\d+)*")

# Codigo de documento controlado: AREA-TIPO-NUMERO.
_CODIGO_RE = re.compile(r"\b[A-Z]{2,5}-[A-Z]{2,5}-\d{1,3}\b")

# Un FAQ afirma; no especula. Estas frases delatan que la clausula no contenia
# la respuesta y el modelo la completo o se excuso.
_FRASES_DE_INCERTIDUMBRE = (
    "no se especifica",
    "no se indica",
    "no se menciona",
    "no esta claro",
    "no queda claro",
    "podria ser",
    "probablemente",
    "se asume",
    "asumiendo",
    "no se detalla",
    "el texto no",
    "el fragmento no",
    "la clausula no",
    "no proporciona",
)

# Numeros que aparecen en cualquier texto sin ser un dato: capitulos, ISO 9001,
# el ano de la norma. No se exige que esten en la clausula.
_NUMEROS_IGNORADOS = {"9001", "9000", "14001", "45001", "2015", "2018"}

# Caracteres a los que se trunca cada palabra al comparar preguntas.
LONGITUD_RAIZ = 6

MIN_CHARS_RESPUESTA = 20
MAX_CHARS_RESPUESTA = 600


class Rechazo(StrEnum):
    CIFRA_INVENTADA = "cifra_inventada"
    CODIGO_INVENTADO = "codigo_inventado"
    CODIGO_INEXISTENTE = "codigo_inexistente"
    INCERTIDUMBRE = "incertidumbre"
    LONGITUD = "longitud"
    PREGUNTA_VACIA = "pregunta_vacia"


@dataclass(frozen=True)
class ResultadoValidacion:
    valida: bool
    motivo: Rechazo | None = None
    detalle: str = ""


def _numeros(texto: str) -> set[str]:
    """
    Numeros normalizados: sin ceros a la izquierda y con la coma decimal
    unificada, para que "01 hora" y "1 hora" cuenten como el mismo dato.
    """
    encontrados = set()
    for m in _NUMERO_RE.finditer(texto or ""):
        crudo = m.group(0).replace(",", ".")
        entero = crudo.split(".")[0].lstrip("0") or "0"
        encontrados.add(entero if "." not in crudo else f"{entero}.{crudo.split('.', 1)[1]}")
    return encontrados


def _codigos(texto: str) -> set[str]:
    return {m.group(0).upper() for m in _CODIGO_RE.finditer(texto or "")}


def validar_entrada(
    pregunta: str,
    respuesta: str,
    clausula: str,
    codigo_documento: str,
    codigos_conocidos: set[str] | None = None,
) -> ResultadoValidacion:
    """
    Comprueba que la respuesta se sostenga sobre el texto de la clausula.

    `codigos_conocidos` son los codigos registrados del SGC. Sirve para el caso
    que la comparacion contra la clausula NO cubre: que el documento fuente
    contenga un codigo erroneo. Paso de verdad -- CAL-PR-03 §7.4 cita
    "CTC-FO-09" donde las otras seis menciones del mismo documento dicen
    "CAL-FO-09", y ese codigo no existe. Validar solo contra la clausula habria
    copiado la errata al FAQ con apariencia de dato verificado.
    """
    pregunta = (pregunta or "").strip()
    respuesta = (respuesta or "").strip()

    if len(pregunta) < 10 or not pregunta.endswith("?"):
        return ResultadoValidacion(False, Rechazo.PREGUNTA_VACIA, pregunta[:60])

    if not MIN_CHARS_RESPUESTA <= len(respuesta) <= MAX_CHARS_RESPUESTA:
        return ResultadoValidacion(
            False, Rechazo.LONGITUD, f"{len(respuesta)} caracteres"
        )

    minuscula = respuesta.lower()
    for frase in _FRASES_DE_INCERTIDUMBRE:
        if frase in minuscula:
            return ResultadoValidacion(False, Rechazo.INCERTIDUMBRE, frase)

    # --- El chequeo que de verdad importa ---
    en_fuente = _numeros(clausula)
    inventadas = _numeros(respuesta) - en_fuente - _NUMEROS_IGNORADOS
    if inventadas:
        return ResultadoValidacion(
            False, Rechazo.CIFRA_INVENTADA, ", ".join(sorted(inventadas))
        )

    base = codigo_documento.split(" (")[0].upper()
    citados = _codigos(respuesta)

    inventados = citados - _codigos(clausula) - {base}
    if inventados:
        return ResultadoValidacion(
            False, Rechazo.CODIGO_INVENTADO, ", ".join(sorted(inventados))
        )

    if codigos_conocidos is not None:
        inexistentes = citados - {c.upper() for c in codigos_conocidos} - {base}
        if inexistentes:
            return ResultadoValidacion(
                False, Rechazo.CODIGO_INEXISTENTE, ", ".join(sorted(inexistentes))
            )

    return ResultadoValidacion(True)


def clave_de_deduplicacion(pregunta: str) -> str:
    """
    Forma normalizada de una pregunta, para detectar repeticiones.

    Clausulas distintas del mismo procedimiento generan preguntas casi iguales
    ("Quien registra el hallazgo?" / "Quien debe registrar el hallazgo?"). En un
    FAQ eso se lee como descuido.
    """
    import unicodedata

    sin_acentos = "".join(
        c
        for c in unicodedata.normalize("NFD", (pregunta or "").lower())
        if not unicodedata.combining(c)
    )
    palabras = re.findall(r"\w+", sin_acentos)
    vacias = {
        "que", "quien", "cual", "cuales", "como", "cuando", "donde", "por", "para",
        "el", "la", "los", "las", "un", "una", "de", "del", "en", "se", "debe",
        "y", "o", "a", "al", "es", "son", "mi", "me", "lo", "su", "sus",
    }
    # Truncar a la raiz para que la conjugacion no separe dos preguntas iguales:
    # "quien registra el hallazgo" y "quien debe registrar el hallazgo" son la
    # misma. Es un stemmer tosco -- colapsa "auditoria" y "auditor" -- pero para
    # detectar duplicados eso es aceptable, y un stemmer de verdad seria una
    # dependencia nueva para un problema pequeno.
    return " ".join(sorted({p[:LONGITUD_RAIZ] for p in palabras if p not in vacias}))
