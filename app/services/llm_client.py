"""
Cliente de LLM contra cualquier backend con API compatible con OpenAI.

Un solo cliente cubre Ollama (local, por defecto), Groq, vLLM, OpenRouter, etc.
Cambiar de proveedor es cambiar LLM_BASE_URL / LLM_MODEL / LLM_API_KEY en el .env,
sin tocar una linea de codigo. El resto del sistema no sabe con quien habla.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Los modelos de razonamiento (Qwen3, DeepSeek-R1, ...) emiten su cadena de
# pensamiento dentro de <think>...</think>. Nunca debe llegar al usuario final
# ni al guardrail de citas: se elimina antes de devolver el contenido.
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_ORPHAN_THINK_RE = re.compile(r"</?think>", re.IGNORECASE)

# Codigos que vale la pena reintentar: rate limit y fallos transitorios del servidor.
_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    """El proveedor de LLM fallo de forma no recuperable."""


class CircuitBreaker:
    """
    Corta las llamadas cuando el backend esta caido.

    Sin esto, con Ollama detenido CADA consulta pagaba el timeout completo por
    cada reintento: medido, hasta 12 minutos de espera antes de ver el mensaje de
    error, y una vez por usuario. Tras unos pocos fallos seguidos no tiene sentido
    seguir esperando: se falla rapido durante un enfriamiento y se reintenta una
    sola llamada al terminar.

    No es thread-safe con precision quirurgica a proposito: un contador
    ligeramente desfasado entre hilos no cambia el comportamiento util, y un
    candado por peticion si costaria.
    """

    def __init__(self, max_failures: int, cooldown: float) -> None:
        self.max_failures = max_failures
        self.cooldown = cooldown
        self._failures = 0
        self._open_until = 0.0

    @property
    def is_open(self) -> bool:
        """True si hay que rechazar sin intentar."""
        if self._open_until and time.monotonic() < self._open_until:
            return True
        if self._open_until:
            # Enfriamiento terminado: se deja pasar una llamada de prueba.
            self._open_until = 0.0
            self._failures = self.max_failures - 1
        return False

    @property
    def retry_in(self) -> int:
        return max(0, int(self._open_until - time.monotonic()))

    def record_success(self) -> None:
        self._failures = 0
        self._open_until = 0.0

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.max_failures:
            self._open_until = time.monotonic() + self.cooldown
            logger.error(
                "llm.circuit_open",
                extra={"failures": self._failures, "cooldown_s": self.cooldown},
            )


def strip_reasoning(text: str) -> str:
    """Quita los bloques de razonamiento interno del modelo."""
    cleaned = _THINK_BLOCK_RE.sub("", text)
    cleaned = _ORPHAN_THINK_RE.sub("", cleaned)
    return cleaned.strip()


def _to_native_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Adapta el historial al dialecto de la API nativa de Ollama.

    Dos diferencias frente al formato OpenAI, ambas encontradas por prueba:

    1. `tool_calls[].function.arguments` debe ser un OBJETO, no un string JSON.
       Enviarlo como string hace que Ollama intente parsearlo y responda
       "Value looks like object, but can't find closing '}' symbol" -- un error
       que no menciona los argumentos y manda a buscar en el lugar equivocado.

    2. El resultado de una herramienta se identifica con `tool_name`, no con
       `tool_call_id` + `name`.

    El resto del sistema sigue hablando el formato OpenAI; la traduccion vive
    aqui y no se filtra al orquestador.
    """
    adaptados: list[dict[str, Any]] = []

    for message in messages:
        nuevo = dict(message)

        if nuevo.get("role") == "tool":
            nombre = nuevo.pop("name", None)
            nuevo.pop("tool_call_id", None)
            if nombre:
                nuevo["tool_name"] = nombre

        llamadas = nuevo.get("tool_calls")
        if llamadas:
            convertidas = []
            for call in llamadas:
                funcion = dict(call.get("function", {}))
                argumentos = funcion.get("arguments")
                if isinstance(argumentos, str):
                    try:
                        funcion["arguments"] = json.loads(argumentos) if argumentos else {}
                    except json.JSONDecodeError:
                        funcion["arguments"] = {}
                convertidas.append({"function": funcion})
            nuevo["tool_calls"] = convertidas
            nuevo.setdefault("content", "")

        adaptados.append(nuevo)

    return adaptados


class OpenAICompatibleClient:
    """Cliente sincrono. FastAPI lo ejecuta en threadpool (endpoints `def`, no `async def`)."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
        disable_thinking: bool | None = None,
    ) -> None:
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.model = model or settings.llm_model
        self.api_key = api_key or settings.llm_api_key
        self.timeout = timeout if timeout is not None else settings.llm_timeout_seconds
        self.max_retries = max_retries if max_retries is not None else settings.llm_max_retries
        self.temperature = (
            temperature if temperature is not None else settings.llm_temperature
        )
        self.reasoning_effort = (
            reasoning_effort
            if reasoning_effort is not None
            else settings.llm_reasoning_effort
        )
        self.disable_thinking = (
            disable_thinking
            if disable_thinking is not None
            else settings.llm_disable_thinking
        )
        self.circuit = CircuitBreaker(
            settings.llm_circuit_failures, settings.llm_circuit_cooldown_seconds
        )

    @property
    def _endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    @property
    def is_ollama(self) -> bool:
        """El modo rapido usa la API nativa de Ollama, asi que solo aplica ahi."""
        return self.base_url.rstrip("/").endswith("/v1") and "11434" in self.base_url

    @property
    def _native_endpoint(self) -> str:
        """`http://host:11434/v1` -> `http://host:11434/api/chat`."""
        raiz = self.base_url.rstrip("/")
        if raiz.endswith("/v1"):
            raiz = raiz[: -len("/v1")]
        return f"{raiz}/api/chat"

    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Devuelve {"content": str, "tool_calls": list, "raw": dict}.

        `tool_calls` viene en formato OpenAI: cada elemento tiene
        id / type / function{name, arguments(JSON string)}.
        """
        if self.disable_thinking and self.is_ollama:
            return self._generate_ollama_native(messages, tools)

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        # Ollama IGNORA reasoning_effort en su endpoint compatible con OpenAI
        # (medido: la latencia no cambia). Se envia porque otros proveedores si
        # lo respetan; para desactivar el razonamiento en Ollama hay que usar su
        # API nativa -- ver _generate_ollama_native.
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        data = self._post_with_retry(self._endpoint, payload, headers)

        choice = data["choices"][0]["message"]
        raw_content = choice.get("content") or ""
        return {
            "content": strip_reasoning(raw_content),
            "tool_calls": choice.get("tool_calls") or [],
            "raw": choice,
        }

    def _generate_ollama_native(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Modo rapido: API nativa de Ollama con el razonamiento apagado.

        Existe porque el endpoint compatible con OpenAI de Ollama no expone forma
        de desactivar el "thinking" del modelo -- ni `reasoning_effort` ni
        `think` en el body surten efecto ahi (los dos medidos). Solo `/api/chat`
        con `think: false` lo consigue, y la diferencia es grande: 1.3 s frente a
        18 s en la misma pregunta.

        El precio es atarse a Ollama para esta funcion, asi que es opcional y va
        detras de una bandera. La forma del valor devuelto es identica a la del
        camino OpenAI, para que el orquestador no sepa cual se uso.
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": _to_native_messages(messages),
            "think": False,
            "stream": False,
            "options": {"temperature": self.temperature},
        }
        if tools:
            payload["tools"] = tools

        data = self._post_with_retry(
            self._native_endpoint, payload, {"Content-Type": "application/json"}
        )

        message = data.get("message") or {}
        raw_content = message.get("content") or ""

        # Ollama nativo entrega los tool calls con `arguments` ya como dict; el
        # resto del sistema espera el formato OpenAI (string JSON con id y type).
        tool_calls = []
        for i, call in enumerate(message.get("tool_calls") or []):
            function = call.get("function", {})
            arguments = function.get("arguments")
            if isinstance(arguments, dict):
                arguments = json.dumps(arguments, ensure_ascii=False)
            tool_calls.append(
                {
                    "id": call.get("id") or f"ollama_{i}_{function.get('name', 'tool')}",
                    "type": "function",
                    "function": {
                        "name": function.get("name", ""),
                        "arguments": arguments or "{}",
                    },
                }
            )

        return {
            "content": strip_reasoning(raw_content),
            "tool_calls": tool_calls,
            "raw": {
                "role": "assistant",
                "content": raw_content,
                **({"tool_calls": tool_calls} if tool_calls else {}),
            },
        }

    def _post_with_retry(
        self, url: str, payload: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        if self.circuit.is_open:
            raise LLMError(
                f"El modelo no responde (varios fallos seguidos). Nuevo intento en "
                f"{self.circuit.retry_in} s."
            )

        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(url, headers=headers, json=payload)

                if resp.status_code in _RETRYABLE_STATUS:
                    raise httpx.HTTPStatusError(
                        f"{resp.status_code} desde el proveedor de LLM",
                        request=resp.request,
                        response=resp,
                    )
                resp.raise_for_status()
                self.circuit.record_success()
                return resp.json()

            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                last_error = exc

                # Un TIMEOUT no se reintenta: si el modelo colgo 45 s, volvera a
                # colgar, y el unico efecto es duplicar la espera del usuario.
                # Los reintentos valen para 429 y 5xx, que si son transitorios,
                # y para una conexion rechazada (backend reiniciandose), que
                # falla en milisegundos.
                es_timeout = isinstance(exc, httpx.TimeoutException)
                estado_no_reintentable = (
                    isinstance(exc, httpx.HTTPStatusError)
                    and exc.response.status_code not in _RETRYABLE_STATUS
                )
                if es_timeout or estado_no_reintentable or attempt == self.max_retries:
                    break

                backoff = 2**attempt
                logger.warning(
                    "llm.retry",
                    extra={
                        "attempt": attempt + 1,
                        "max_retries": self.max_retries,
                        "backoff_s": backoff,
                        "error": str(exc),
                    },
                )
                time.sleep(backoff)

        self.circuit.record_failure()
        raise LLMError(
            f"No se pudo obtener respuesta de {self.base_url} tras "
            f"{self.max_retries + 1} intento(s): {last_error}"
        ) from last_error

    def health(self) -> bool:
        """True si el backend responde. Se usa en GET /health."""
        try:
            with httpx.Client(timeout=5.0) as client:
                headers = (
                    {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
                )
                resp = client.get(f"{self.base_url}/models", headers=headers)
            return resp.status_code < 500
        except httpx.HTTPError:
            return False


_client: OpenAICompatibleClient | None = None


def get_llm_client() -> OpenAICompatibleClient:
    global _client
    if _client is None:
        _client = OpenAICompatibleClient()
        logger.info(
            "llm.client_ready",
            extra={"provider": settings.llm_provider, "model": _client.model,
                   "base_url": _client.base_url},
        )
    return _client
