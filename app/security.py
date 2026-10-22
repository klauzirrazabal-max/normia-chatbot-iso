"""
Lo que hace falta para exponer NormIA fuera de localhost.

Las tres protecciones vienen APAGADAS por defecto, y es deliberado: en desarrollo
estorban y la ausencia de configuracion no debe cambiar el comportamiento
conocido. Se encienden poniendo su variable en el `.env`, que es justo lo que se
hace antes de abrir un tunel.

El agujero que motivo esto: `tenant_id` llegaba en el cuerpo de la peticion, asi
que cualquiera podia pedir el SGC de otro cliente sabiendo su identificador. Y
`/api/admin/*` respondia sin credenciales, incluidos los DELETE.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

from fastapi import Header, HTTPException, Request, status

from app.config import settings

logger = logging.getLogger(__name__)


def resolve_tenant(pedido: str) -> str:
    """
    Decide contra que tenant se responde.

    Con `PUBLIC_TENANT_ID` puesto, el valor que mande el cliente se IGNORA: el
    servidor sirve siempre ese tenant y ninguno mas. Es la diferencia entre una
    demo publica y una fuga, porque el identificador del cliente no es un secreto
    -- aparece en la configuracion y en cualquier captura.

    Sin la variable, se respeta lo pedido y todo sigue como en local.
    """
    fijado = settings.public_tenant_id.strip()
    if not fijado:
        return pedido
    if pedido and pedido != fijado:
        logger.warning(
            "security.tenant_override",
            extra={"pedido": pedido[:64], "servido": fijado},
        )
    return fijado


def require_admin_key(x_admin_key: str | None = Header(default=None)) -> None:
    """
    Protege /api/admin/*, que puede borrar documentos y marcarlos obsoletos.

    Comparacion en tiempo constante para no filtrar la clave por el tiempo de
    respuesta. Si no hay clave configurada no se exige nada: en local se trabaja
    sin friccion.
    """
    esperada = settings.admin_api_key.strip()
    if not esperada:
        return
    import hmac

    if not x_admin_key or not hmac.compare_digest(x_admin_key, esperada):
        logger.warning("security.admin_key_rechazada")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credencial de administracion invalida o ausente.",
        )


class VentanaDeslizante:
    """
    Limitador por IP, en memoria y por ventana deslizante.

    En memoria basta porque hay un solo proceso; con varias replicas haria falta
    Redis. No se pretende frenar un ataque serio: se pretende que una consulta
    -- que cuesta segundos de GPU -- no se pueda disparar en bucle desde una
    pestaña.
    """

    def __init__(self, por_minuto: int) -> None:
        self.por_minuto = por_minuto
        self._visitas: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def permite(self, clave: str) -> bool:
        if self.por_minuto <= 0:
            return True
        ahora = time.monotonic()
        corte = ahora - 60.0
        with self._lock:
            cola = self._visitas.setdefault(clave, deque())
            while cola and cola[0] < corte:
                cola.popleft()
            if len(cola) >= self.por_minuto:
                return False
            cola.append(ahora)
            # Sin esto el diccionario crece sin limite con IPs que ya no vuelven.
            if len(self._visitas) > 4096:
                for k in [k for k, v in self._visitas.items() if not v or v[-1] < corte]:
                    self._visitas.pop(k, None)
            return True


_limitador = VentanaDeslizante(settings.rate_limit_per_minute)


def ip_del_cliente(request: Request) -> str:
    """
    IP real del cliente, para repartir el limite por quien consulta.

    Detras de un tunel o un balanceador, la IP de la conexion es la del proxy:
    sin mirar una cabecera de reenvio todo el trafico compartiria un solo cubo y
    el limite castigaria a todo el mundo a la vez.

    PERO esa cabecera la escribe el cliente. `X-Forwarded-For` se tomaba
    entera y se usaba su PRIMER valor, que es precisamente el que cualquiera
    puede inventar. Medido: 60 peticiones seguidas desde la misma maquina
    cambiando la cabecera en cada una, 0 bloqueadas. El limite no existia para
    quien supiera eso, y la generacion de imagenes es publica y consume la GPU
    casi un minuto por pieza.

    Ahora la cabecera solo se lee si `TRUSTED_PROXY` declara detras de quien
    estamos, y se lee la que ESE proveedor garantiza reescribir. Sin la
    variable se usa la IP de la conexion y nada mas, que es lo correcto cuando
    no hay proxy delante.
    """
    proveedor = settings.trusted_proxy.strip().lower()
    directa = request.client.host if request.client else "desconocida"
    if not proveedor:
        return directa

    if proveedor == "cloudflare":
        # Cloudflare SOBRESCRIBE esta cabecera en cada peticion, asi que lo que
        # mande el cliente en ella se pierde. `X-Forwarded-For` no sirve: alli
        # Cloudflare ANADE al final y conserva lo que viniera delante.
        cf = request.headers.get("cf-connecting-ip", "").strip()
        if cf:
            return cf
        return directa

    # Proxy generico (nginx, un balanceador propio): el ultimo salto de
    # X-Forwarded-For es el que anadio el proxy en el que confiamos. Los
    # anteriores los pudo escribir el cliente, asi que se descartan.
    reenviada = request.headers.get("x-forwarded-for", "")
    if reenviada:
        saltos = [p.strip() for p in reenviada.split(",") if p.strip()]
        if saltos:
            return saltos[-1]
    return directa


def require_admin_key_salvo_demo(x_admin_key: str | None = Header(default=None)) -> None:
    """
    Clave solo cuando lo servido son datos del cliente.

    El catalogo de clausulas lo necesita la pagina de generacion para que la
    pieza cite algo que existe. Pedirle la clave de administracion a quien solo
    va a generar un cartel es la friccion que convirtio una demo en un dialogo
    del navegador pidiendo credenciales.

    Con `PUBLIC_TENANT_ID` configurado lo que se sirve es el corpus publico, que
    no es secreto: pasa sin clave. Sin esa variable se serviria el SGC real del
    cliente, y entonces si se exige.
    """
    if settings.public_tenant_id.strip():
        return
    require_admin_key(x_admin_key)


def check_rate_limit(request: Request) -> None:
    if _limitador.permite(ip_del_cliente(request)):
        return
    logger.warning("security.rate_limit", extra={"ip": ip_del_cliente(request)})
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Demasiadas consultas seguidas. Espera un momento e intentalo de nuevo.",
    )
