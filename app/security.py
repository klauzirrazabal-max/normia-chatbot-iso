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
    IP real detras del tunel.

    Cloudflare y ngrok reescriben la IP de origen, asi que sin mirar la cabecera
    de reenvio todo el trafico compartiria un solo cubo y el limite castigaria a
    todo el mundo a la vez.
    """
    reenviada = request.headers.get("x-forwarded-for", "")
    if reenviada:
        return reenviada.split(",")[0].strip()
    return request.client.host if request.client else "desconocida"


def check_rate_limit(request: Request) -> None:
    if _limitador.permite(ip_del_cliente(request)):
        return
    logger.warning("security.rate_limit", extra={"ip": ip_del_cliente(request)})
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Demasiadas consultas seguidas. Espera un momento e intentalo de nuevo.",
    )
