"""
Un saludo no es una consulta al SGC.

"Hola" respondia "no tengo informacion suficiente en los documentos vigentes...
lo derivo al Responsable de Calidad". Es lo primero que escribe cualquiera que
abre el chat, asi que era la peor primera impresion posible, y encima metia
basura en la cola de Calidad.

Se resuelve en el servidor y antes del RAG, no por prompt: pedirselo al modelo
fallaba la mitad de las veces -- escribia el saludo y el guardrail bloqueante lo
descartaba por no tener contexto en que apoyarse.
"""

from __future__ import annotations

import pytest

from app.core.routing import (
    RESPUESTA_CORTESIA,
    RESPUESTA_DESPEDIDA,
    RESPUESTA_SALUDO,
    respuesta_social as _mensaje_social,
)


@pytest.mark.parametrize(
    "texto",
    ["Hola", "hola", "HOLA", "hola!", "¡Hola!", "buenos dias", "Buenos días",
     "buenas tardes", "que tal", "Hey", "  hola  "],
)
def test_los_saludos_se_responden_sin_pasar_por_el_rag(texto):
    assert _mensaje_social(texto) == RESPUESTA_SALUDO


@pytest.mark.parametrize("texto", ["gracias", "Gracias!", "muchas gracias", "ok", "perfecto"])
def test_la_cortesia_tambien(texto):
    assert _mensaje_social(texto) == RESPUESTA_CORTESIA


@pytest.mark.parametrize("texto", ["adios", "hasta luego", "chao", "nos vemos"])
def test_y_las_despedidas(texto):
    assert _mensaje_social(texto) == RESPUESTA_DESPEDIDA


# --- Lo que NO debe capturar. Un falso positivo aqui se saltaria el RAG en una
#     consulta real, que es mucho peor que responder despacio a un saludo. ---

@pytest.mark.parametrize(
    "texto",
    [
        "hola, cual es el plazo de atencion?",
        "gracias por el procedimiento de compras",
        "buenos dias, necesito la politica de seguridad",
        "cual es la politica de vacaciones?",
        "ok pero que dice la seccion 6.2?",
        "",
        "   ",
    ],
)
def test_una_consulta_real_sigue_su_camino(texto):
    assert _mensaje_social(texto) is None


def test_un_mensaje_largo_nunca_es_social():
    # El tope de longitud es la ultima red: aunque empiece por "hola", si trae
    # contenido no puede saltarse la recuperacion.
    assert _mensaje_social("hola " * 20) is None


def test_la_respuesta_al_saludo_invita_a_preguntar():
    # Si no invita, el usuario se queda sin saber que hacer con el chat.
    assert "?" in RESPUESTA_SALUDO
    assert "NormIA" in RESPUESTA_SALUDO
