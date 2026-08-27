from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    TIMESTAMP,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.config import settings


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[object] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", "version"),
        CheckConstraint("status IN ('vigente', 'obsoleto')", name="documents_status_check"),
        Index("idx_documents_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.id"), nullable=False)
    code: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str | None] = mapped_column(String)
    area: Mapped[str | None] = mapped_column(String)
    effective_date: Mapped[object | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String, nullable=False, default="vigente")
    source_filename: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[object] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.id"), nullable=False)
    section: Mapped[str | None] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(settings.embedding_dim), nullable=False
    )
    created_at: Mapped[object] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    document: Mapped["Document"] = relationship(back_populates="chunks")


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("tenant_id", "channel", "external_user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.id"), nullable=False)
    channel: Mapped[str] = mapped_column(String, nullable=False)
    external_user_id: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[object] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="messages_role_check"),
        Index("idx_messages_conversation", "conversation_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    retrieved_chunk_ids: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))
    retrieval_debug: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[object] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )


class Finding(Base):
    __tablename__ = "findings"
    __table_args__ = (
        CheckConstraint(
            "status IN ('abierto', 'en_revision', 'cerrado')", name="findings_status_check"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.id"), nullable=False)
    conversation_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("conversations.id")
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="abierto")
    created_at: Mapped[object] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )


class CapaAction(Base):
    __tablename__ = "capa_actions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pendiente', 'en_progreso', 'completado')", name="capa_status_check"
        ),
        Index("idx_capa_finding", "finding_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    finding_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("findings.id", ondelete="CASCADE"), nullable=False
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    responsible: Mapped[str | None] = mapped_column(String)
    due_date: Mapped[object | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pendiente")


class UploadJob(Base):
    """
    Un archivo subido, esperando o terminando su procesamiento.

    Existe porque procesar un PDF (membrete, validacion, texto, embeddings)
    toma segundos: el upload responde de inmediato con un job por archivo y la
    UI consulta el estado, en vez de bloquear la request.
    """

    __tablename__ = "upload_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pendiente', 'procesando', 'requiere_revision', 'listo', 'error')",
            name="upload_jobs_status_check",
        ),
        Index("idx_upload_jobs_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.id"), nullable=False)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pendiente")
    resolved_code: Mapped[str | None] = mapped_column(String)
    resolved_version: Mapped[str | None] = mapped_column(String)
    title: Mapped[str | None] = mapped_column(Text)
    area: Mapped[str | None] = mapped_column(String)
    document_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("documents.id", ondelete="SET NULL")
    )
    advices: Mapped[list | None] = mapped_column(JSONB, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    chunks_created: Mapped[int | None] = mapped_column(Integer)
    # True solo si ESTE job creo el documento. Si actualizo uno preexistente,
    # descartar el job no debe borrar el documento original del SGC.
    created_document: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[object] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[object] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Escalation(Base):
    """
    Una consulta derivada al Responsable de Calidad.

    Antes de esta tabla la escalacion era solo una linea de log: el bot afirmaba
    haber derivado la consulta y nadie la recibia. En cumplimiento, prometer una
    accion y no ejecutarla es peor que declararse incapaz.
    """

    __tablename__ = "escalations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pendiente', 'en_revision', 'resuelta', 'descartada')",
            name="escalations_status_check",
        ),
        CheckConstraint(
            "trigger IN ('sin_contexto', 'fuera_de_alcance', 'error')",
            name="escalations_trigger_check",
        ),
        Index("idx_escalations_pendientes", "tenant_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.id"), nullable=False)
    conversation_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("conversations.id", ondelete="SET NULL")
    )
    channel: Mapped[str | None] = mapped_column(String)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    trigger: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pendiente")
    assigned_to: Mapped[str | None] = mapped_column(String)
    resolution: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[object] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    resolved_at: Mapped[object | None] = mapped_column(TIMESTAMP(timezone=True))


class Incident(Base):
    """
    Un fallo tecnico, con una referencia corta que el usuario puede citar.

    Sin esto, un fallo dejaba al usuario con "estoy con problemas tecnicos" y
    nada que reportar, y al operador con una linea de log imposible de
    correlacionar con la queja.
    """

    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reference: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    tenant_id: Mapped[str | None] = mapped_column(String, ForeignKey("tenants.id"))
    conversation_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("conversations.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[object] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )


class FaqEntry(Base):
    """
    Una pregunta frecuente derivada de una clausula.

    NO es un cache de respuestas: servir una respuesta enlatada sin pasar por el
    orquestador saltaria toda la capa de verificacion -- citas, versiones,
    grounding -- y seria justo el camino mas rapido, o sea el mas usado.

    Lo que aporta es cerrar el desajuste de vocabulario. La clausula habla en
    lenguaje de procedimiento ("El Jefe de TI informa al solicitante el estado")
    y la persona pregunta en el suyo ("quien me avisa como va mi solicitud?").
    Buscar pregunta-contra-pregunta acierta donde pregunta-contra-clausula falla.
    """

    __tablename__ = "faq_entries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "document_id", "question"),
        Index("idx_faq_tenant_reviewed", "tenant_id", "reviewed"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.id"), nullable=False)
    document_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("document_chunks.id", ondelete="SET NULL")
    )
    section: Mapped[str | None] = mapped_column(String)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(settings.embedding_dim), nullable=False
    )
    reviewed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reviewed_by: Mapped[str | None] = mapped_column(String)
    reviewed_at: Mapped[object | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[object] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )


class RegistryEntry(Base):
    """
    Una fila de la Lista Maestra de Documentos Internos.

    Es el registro autoritativo del SGC: que documentos existen, en que version
    y desde cuando. Distinto de `documents`, que es lo que el asistente tiene
    INDEXADO.

    Separarlos importa. Los formatos INV-FO-02, 06, 11, 13, 16, 18, 21 y
    GTH-FO-14 estan registrados pero sus PDFs vienen agrupados en archivos
    combinados, asi que nunca se ingestaron por separado. Validando contra
    `documents`, el verificador los daba por inexistentes y descartaba respuestas
    correctas.

    "Existe en el SGC" y "esta indexado" son dos preguntas distintas, y su
    diferencia es una metrica util: cuanta parte del SGC cubre el asistente.
    """

    __tablename__ = "document_registry"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code"),
        Index("idx_registry_tenant", "tenant_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.id"), nullable=False)
    code: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    process: Mapped[str | None] = mapped_column(String)
    doc_type: Mapped[str | None] = mapped_column(String)
    effective_date: Mapped[object | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String, nullable=False, default="lista_maestra")
    created_at: Mapped[object] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[object] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )
