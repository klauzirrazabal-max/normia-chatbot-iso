"""
Entrar, salir y saber quien eres.

    POST /api/auth/login    correo y contrasena -> cookie de sesion
    POST /api/auth/logout   retira la cookie
    GET  /api/auth/yo       quien soy y que puedo hacer

La cookie va `httpOnly`, de modo que el JavaScript de la pagina no puede
leerla. Antes la credencial vivia en `localStorage`, que es exactamente lo
contrario: cualquier script de la pagina podia cogerla.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import (
    Rol,
    firmar_sesion,
    leer_sesion,
    puede_administrar,
    puede_gestionar,
    verificar,
)
from app.db.session import get_db
from app.models.db_models import User
from app.security import check_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE = "normia_sesion"


class Credenciales(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)


def usuario_actual(request: Request, db: Session = Depends(get_db)) -> User | None:
    """El usuario de la cookie, o None. No exige nada: solo resuelve."""
    sesion = leer_sesion(request.cookies.get(COOKIE))
    if sesion is None:
        return None
    usuario = db.get(User, sesion.user_id)
    return usuario if usuario is not None and usuario.activo else None


def exigir_usuario(usuario: User | None = Depends(usuario_actual)) -> User:
    if usuario is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Necesitas iniciar sesión.")
    return usuario


def exigir_calidad(usuario: User = Depends(exigir_usuario)) -> User:
    """
    El panel es de Calidad. Un operario que llega aqui recibe 403, no 401: la
    diferencia importa, porque 401 le haria volver a la pantalla de acceso a
    intentarlo otra vez con la misma cuenta.
    """
    if not puede_gestionar(usuario.rol):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Esta sección es del Responsable de Calidad.",
        )
    return usuario


def exigir_admin(usuario: User = Depends(exigir_usuario)) -> User:
    if not puede_administrar(usuario.rol):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Solo un administrador.")
    return usuario


@router.post("/login")
def login(
    credenciales: Credenciales,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    check_rate_limit(request)
    correo = credenciales.email.strip().lower()
    usuario = db.query(User).filter(User.email == correo).one_or_none()

    # MISMA respuesta para "no existe" y "contrasena mala". Distinguirlas
    # convierte el endpoint en un comprobador de correos validos.
    if (
        usuario is None
        or not usuario.activo
        or not verificar(credenciales.password, usuario.password)
    ):
        logger.warning("auth.login_fallido", extra={"email": correo[:60]})
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Correo o contraseña incorrectos."
        )

    usuario.last_login = datetime.now(UTC)
    db.commit()

    response.set_cookie(
        COOKIE,
        firmar_sesion(usuario.id),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 12,
        path="/",
    )
    logger.info("auth.login", extra={"user_id": usuario.id, "rol": usuario.rol})
    return _perfil(usuario)


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/yo")
def yo(usuario: User | None = Depends(usuario_actual)) -> dict:
    """
    Quien soy. Devuelve 200 con `autenticado: false` en vez de 401 cuando no
    hay sesion: la pagina lo consulta al cargar para decidir que ensenar, y un
    401 ahi llenaria la consola de errores que no son errores.
    """
    if usuario is None:
        return {"autenticado": False}
    return {"autenticado": True, **_perfil(usuario)}


def _perfil(usuario: User) -> dict:
    return {
        "id": usuario.id,
        "nombre": usuario.nombre,
        "email": usuario.email,
        "rol": usuario.rol,
        "tenant_id": usuario.tenant_id,
        "puede_gestionar": puede_gestionar(usuario.rol),
        "puede_administrar": puede_administrar(usuario.rol),
        "roles": [r.value for r in Rol],
    }
