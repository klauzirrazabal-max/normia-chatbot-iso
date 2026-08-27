from typing import Literal

from pydantic import BaseModel, Field

Channel = Literal["whatsapp", "web"]


class IncomingMessage(BaseModel):
    """Formato interno comun, sin importar el canal de origen."""

    tenant_id: str
    channel: Channel
    external_user_id: str  # numero de whatsapp o session_id del widget
    text: str


class Citation(BaseModel):
    """
    Una fuente citada, con lo que necesita una persona y lo que necesita una
    auditoria.

    El TITULO es lo que hace utilizable la referencia: "STI-PR-01 v4" no le dice
    nada a quien pregunta, "Atencion de Solicitudes Tecnologicas" si. Y la FECHA
    DE VIGENCIA es la garantia real de cumplimiento -- no solo de donde salio la
    respuesta, sino que esa es la version en vigor.

    Los dos los arma el servidor desde la base, no el modelo: asi la ficha es
    correcta aunque el modelo se equivoque al escribir.
    """

    code: str
    version: str
    section: str | None = None
    title: str | None = None
    effective_date: str | None = None

    def label(self) -> str:
        base = f"{self.code} {self.version}"
        return f"{base}, seccion {self.section}" if self.section else base


class Suggestion(BaseModel):
    """
    Opcion de seguimiento que la UI ofrece como boton.

    `label` es lo que se ve; `message` es lo que se envia al pulsarlo. Se separan
    porque el titulo de una clausula ("Registro del hallazgo") se lee bien como
    boton pero no como pregunta.
    """

    label: str
    message: str


class BotResponse(BaseModel):
    """Formato interno de salida, antes de que el adapter lo formatee por canal."""

    text: str
    citations: list[Citation] = Field(default_factory=list)
    escalate: bool = False
    grounded: bool = True
    suggestions: list[Suggestion] = Field(default_factory=list)


class WebChatRequest(BaseModel):
    tenant_id: str
    session_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)


class WebChatResponse(BaseModel):
    reply: str
    citations: list[Citation] = Field(default_factory=list)
    escalate: bool = False
    grounded: bool = True
    suggestions: list[Suggestion] = Field(default_factory=list)
