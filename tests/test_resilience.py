"""
Tests de escalacion registrada y de resiliencia ante fallos.

Dos huecos que la auditoria del sistema destapo:

1. La escalacion era una promesa vacia. `escalate_to_quality` solo escribia una
   linea de log: el bot le decia al usuario "lo derive al Responsable de
   Calidad" y nadie era derivado a ninguna parte. Se contaron 39 escalaciones
   sin un solo registro. En cumplimiento eso es PEOR que declararse incapaz,
   porque deja un rastro falso de que algo se hizo.

2. El fallo era lento y opaco. Con timeout de 120 s y 2 reintentos, y dos
   llamadas al LLM por turno, un cuelgue hacia esperar hasta 12 minutos antes de
   mostrar "estoy con problemas tecnicos" -- y una vez por cada usuario.
"""

import contextlib
import time

from app.core.agents.tools import escalate_to_quality
from app.services.llm_client import CircuitBreaker, LLMError, OpenAICompatibleClient


class TestEscalacionSinBaseDeDatos:
    """
    Sin sesion no se puede registrar. El camino degradado no debe romper el
    turno, pero tampoco pasar inadvertido: la promesa queda incumplida.
    """

    def test_no_rompe_sin_sesion(self):
        resultado = escalate_to_quality("motivo cualquiera")
        assert resultado["escalated"] is True
        assert "escalation_id" not in resultado

    def test_conserva_el_motivo(self):
        resultado = escalate_to_quality("pide aprobar un cambio")
        assert "aprobar" in resultado["reason"]


class TestReintentos:
    """
    Reintentar un timeout duplica la espera sin mejorar nada: si el modelo colgo
    una vez, colgara otra. Los reintentos son para fallos transitorios.
    """

    def test_un_timeout_no_se_reintenta(self):
        import httpx

        cliente = OpenAICompatibleClient(
            base_url="http://10.255.255.1/v1", timeout=0.4, max_retries=3
        )
        inicio = time.perf_counter()
        with contextlib.suppress(LLMError, httpx.HTTPError):
            cliente.generate([{"role": "user", "content": "x"}])
        transcurrido = time.perf_counter() - inicio

        # Con 3 reintentos y backoff seria >7s; sin reintentar, ~un timeout.
        assert transcurrido < 3, f"tardo {transcurrido:.1f}s: parece que reintento"


class TestCortacircuitos:
    def test_abre_tras_los_fallos_configurados(self):
        cb = CircuitBreaker(max_failures=3, cooldown=60)
        assert cb.is_open is False

        cb.record_failure()
        cb.record_failure()
        assert cb.is_open is False, "aun no llego al limite"

        cb.record_failure()
        assert cb.is_open is True

    def test_un_exito_reinicia_el_contador(self):
        cb = CircuitBreaker(max_failures=2, cooldown=60)
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        assert cb.is_open is False, "el exito debio limpiar el fallo previo"

    def test_deja_pasar_una_prueba_tras_el_enfriamiento(self):
        cb = CircuitBreaker(max_failures=1, cooldown=0)
        cb.record_failure()
        assert cb.is_open is False, "con enfriamiento cero se reintenta de inmediato"

    def test_informa_cuanto_falta(self):
        cb = CircuitBreaker(max_failures=1, cooldown=60)
        cb.record_failure()
        assert 0 < cb.retry_in <= 60

    def test_falla_rapido_con_el_circuito_abierto(self):
        """
        Es el punto de todo esto: con el backend caido, la respuesta debe ser
        inmediata en vez de que cada usuario pague el timeout completo.
        """
        cliente = OpenAICompatibleClient(
            base_url="http://localhost:59999/v1", timeout=2, max_retries=0
        )
        cliente.circuit.max_failures = 1

        with contextlib.suppress(LLMError):
            cliente.generate([{"role": "user", "content": "x"}])

        inicio = time.perf_counter()
        try:
            cliente.generate([{"role": "user", "content": "x"}])
            raise AssertionError("deberia haber fallado")
        except LLMError as exc:
            transcurrido = time.perf_counter() - inicio
            assert transcurrido < 0.1, f"tardo {transcurrido:.2f}s en vez de fallar rapido"
            assert "no responde" in str(exc)


class TestConfiguracionDeTiempos:
    def test_el_timeout_no_puede_hacer_esperar_minutos(self):
        """
        El peor caso es un cuelgue: dos llamadas al LLM, una por turno con tool
        call, cada una agotando su timeout. Los timeouts NO se reintentan, asi
        que el techo es 2 x timeout.

        Con la configuracion original -- 120 s y 2 reintentos -- eran 12 minutos.
        """
        from app.config import settings

        peor_caso = settings.llm_timeout_seconds * 2
        assert peor_caso <= 120, f"el peor caso serian {peor_caso:.0f}s de espera"
