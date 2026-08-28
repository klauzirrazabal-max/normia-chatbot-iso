from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuracion de la aplicacion, leida de variables de entorno / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_env: str = "local"
    app_port: int = 8000
    log_level: str = "INFO"

    # Base de datos
    database_url: str

    # LLM: cualquier backend con API compatible OpenAI (ollama, groq, etc.)
    llm_provider: str = "ollama"
    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = "qwen3.8:27b-mlx"
    llm_api_key: str = "ollama"
    # Medido: el modelo responde en 9-20 s. Un timeout de 120 s con 2 reintentos
    # hacia que, ante un cuelgue, el usuario esperara hasta 12 minutos (el
    # orquestador hace dos llamadas) antes de ver el mensaje de error.
    llm_timeout_seconds: float = 45.0
    llm_max_retries: int = 1
    # Cortacircuitos: tras N fallos consecutivos se falla de inmediato durante
    # el enfriamiento, en vez de que CADA usuario pague el timeout completo.
    llm_circuit_failures: int = 3
    llm_circuit_cooldown_seconds: float = 60.0
    llm_temperature: float = 0.2
    # Profundidad de razonamiento. OJO: Ollama IGNORA este campo en su endpoint
    # compatible con OpenAI (medido: no cambia la latencia). Se conserva porque
    # otros proveedores si lo respetan.
    llm_reasoning_effort: str = ""
    # Modo rapido: desactiva el razonamiento del modelo usando la API NATIVA de
    # Ollama (/api/chat con think=false), no el endpoint compatible con OpenAI,
    # que ignora la instruccion. Medido: ~10x mas rapido. Solo aplica a Ollama.
    llm_disable_thinking: bool = False

    # Embeddings (locales)
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024

    # RAG
    rag_top_k: int = 4
    rag_max_distance: float = 0.49
    rag_relative_margin: float = 0.08

    # Widget web
    widget_allowed_origins: str = "http://localhost:5500"

    # Tenant
    default_tenant_id: str = "empresa-demo-iso"

    # --- Exponer fuera de localhost. Vacio = apagado, que es lo correcto en local. ---
    # Con esto puesto, /api/chat ignora el tenant que mande el cliente y sirve solo este.
    public_tenant_id: str = ""
    # Con esto puesto, /api/admin/* exige la cabecera X-Admin-Key.
    admin_api_key: str = ""
    # Consultas por minuto y por IP. 0 = sin limite.
    rate_limit_per_minute: int = 0

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.widget_allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """Instancia unica de configuracion. Cacheada para no releer el .env en cada import."""
    return Settings()


settings = get_settings()
