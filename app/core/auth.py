"""
Identidad: quien eres, que puedes hacer y quien firmo cada acto.

Sin dependencias nuevas. `hashlib.scrypt` y `hmac` estan en la biblioteca
estandar, y anadir una libreria de criptografia para guardar unas contrasenas
seria el mismo exceso que anadir una de PDF para generar datos de ejemplo.

La sesion es una cookie FIRMADA, no cifrada: no lleva secretos, lleva el
identificador del usuario y una fecha de caducidad, y la firma impide que
alguien se cambie el rol editandola. Sin estado en el servidor, que para un
sistema de un solo proceso es lo que corresponde.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass
from enum import StrEnum

from app.config import settings

logger = logging.getLogger(__name__)

# Coste de scrypt. 2**14 tarda unos 60 ms en esta maquina: suficiente para que
# probar contrasenas a ciegas no compense, y poco para que entrar no se note.
_N, _R, _P = 2**14, 8, 1
_DURACION_SESION = 60 * 60 * 12  # doce horas: una jornada, y se vuelve a entrar


class Rol(StrEnum):
    OPERARIO = "operario"
    """Consulta la documentacion y reporta hallazgos. No entra al panel."""

    CALIDAD = "calidad"
    """El panel entero: documentos, hallazgos, escalaciones."""

    ADMIN = "admin"
    """Ademas, da de alta usuarios."""


# Que rol abre que puerta. Es jerarquico, pero se declara explicito en vez de
# comparar numeros: una comparacion ">= 2" no se lee, y los permisos hay que
# poder leerlos.
_PUEDE_GESTIONAR = frozenset({Rol.CALIDAD, Rol.ADMIN})
_PUEDE_ADMINISTRAR = frozenset({Rol.ADMIN})


def puede_gestionar(rol: str) -> bool:
    return rol in _PUEDE_GESTIONAR


def puede_administrar(rol: str) -> bool:
    return rol in _PUEDE_ADMINISTRAR


# --- Contrasenas -------------------------------------------------------------


def cifrar(password: str) -> str:
    """Devuelve 'scrypt$N$r$p$sal$hash', con la sal nueva en cada llamada."""
    sal = secrets.token_bytes(16)
    clave = hashlib.scrypt(password.encode(), salt=sal, n=_N, r=_R, p=_P, dklen=32)
    sal_b64 = base64.b64encode(sal).decode()
    hash_b64 = base64.b64encode(clave).decode()
    return f"scrypt${_N}${_R}${_P}${sal_b64}${hash_b64}"


def verificar(password: str, guardado: str) -> bool:
    """
    Comprueba la contrasena en tiempo constante.

    Un formato que no reconocemos devuelve False en vez de reventar: una fila
    corrupta no debe tumbar el acceso de todos los demas.
    """
    try:
        etiqueta, n, r, p, sal_b64, hash_b64 = guardado.split("$")
        if etiqueta != "scrypt":
            return False
        clave = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(sal_b64),
            n=int(n), r=int(r), p=int(p), dklen=32,
        )
    except (ValueError, TypeError):
        logger.warning("auth.hash_ilegible")
        return False
    return hmac.compare_digest(clave, base64.b64decode(hash_b64))


# --- Sesion ------------------------------------------------------------------


@dataclass(frozen=True)
class Sesion:
    user_id: int
    expira: int

    @property
    def caducada(self) -> bool:
        return time.time() > self.expira


def _secreto() -> bytes:
    """
    La clave con la que se firma la sesion.

    Se reutiliza `ADMIN_API_KEY` cuando existe para no anadir otra variable que
    configurar; si no hay ninguna, se genera una al arrancar y las sesiones
    mueren al reiniciar, que en desarrollo es lo correcto.
    """
    configurada = settings.admin_api_key.strip()
    if configurada:
        return configurada.encode()
    return _SECRETO_EFIMERO


_SECRETO_EFIMERO = secrets.token_bytes(32)


def firmar_sesion(user_id: int, duracion: int = _DURACION_SESION) -> str:
    cuerpo = f"{user_id}.{int(time.time()) + duracion}"
    firma = hmac.new(_secreto(), cuerpo.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{cuerpo}.{firma}"


def leer_sesion(cookie: str | None) -> Sesion | None:
    """
    Devuelve la sesion si la cookie es autentica y no ha caducado.

    Cualquier cosa rara -- firma que no cuadra, formato inesperado, fecha
    pasada -- es None. No se distingue entre ellas a proposito: al cliente le
    da igual por que no vale, y distinguirlo le diria a quien trastea cuanto
    se ha acercado.
    """
    if not cookie:
        return None
    try:
        user_id, expira, firma = cookie.rsplit(".", 2)
        cuerpo = f"{user_id}.{expira}"
        esperada = hmac.new(_secreto(), cuerpo.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(firma, esperada):
            return None
        sesion = Sesion(user_id=int(user_id), expira=int(expira))
    except (ValueError, TypeError):
        return None
    return None if sesion.caducada else sesion
