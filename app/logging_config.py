"""
Logging estructurado en JSON.

Cada turno deja una linea con: que se recupero, con que distancia, que
herramienta se llamo y si la respuesta quedo fundamentada. Eso es lo que
permite DEMOSTRAR que el bot no alucino, en vez de solo afirmarlo -- que es
justo lo que pide una auditoria.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

# Atributos que trae todo LogRecord de serie; el resto son campos nuestros.
_STANDARD = set(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | {"asctime", "message", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        payload.update(
            {k: v for k, v in record.__dict__.items() if k not in _STANDARD}
        )
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Ruido de librerias que no aporta a la traza de auditoria.
    for noisy in ("httpx", "httpcore", "sentence_transformers", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
