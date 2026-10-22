"""
Validacion de un documento al momento de subirlo.

La idea de fondo: no le pidas al cliente que ordene bien las carpetas. Ordenar
a mano es exactamente donde se cuelan los errores de control documental -- en
el SGC real que motivo este modulo, un instructivo impreso como v2 estaba
registrado como v1 en la lista maestra, y nadie lo noto durante mas de un ano.

Asi que el sistema lee el membrete del propio PDF (CODIGO / VERSION / REVISION),
lo cruza contra lo que ya hay registrado, y ACONSEJA.

Nunca decide solo: sugiere la accion y el usuario confirma. Marcar un documento
como obsoleto es una decision de Calidad, no del software.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.rag.doc_header import (
    ApprovalBlock,
    DocumentHeader,
    code_from_filename,
    read_approval,
    read_header,
)
from app.models.db_models import Document, RegistryEntry


class Severity(StrEnum):
    ERROR = "error"      # no se puede ingestar tal cual
    WARNING = "warning"  # se puede, pero hay algo que decidir
    INFO = "info"        # solo para que el usuario lo sepa


class Action(StrEnum):
    """Accion sugerida, para que la UI ofrezca un boton concreto en vez de un texto."""

    NONE = "none"
    MARK_PREVIOUS_OBSOLETE = "mark_previous_obsolete"
    REPLACE_EXISTING = "replace_existing"
    RENAME_FILE = "rename_file"
    CONFIRM_CODE = "confirm_code"
    REVIEW_VERSION = "review_version"
    CONFIRM_APPROVAL = "confirm_approval"


@dataclass(frozen=True)
class Advice:
    severity: Severity
    code: str      # identificador estable del tipo de aviso
    message: str   # texto para la persona
    action: Action = Action.NONE
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "severity": str(self.severity),
            "code": self.code,
            "message": self.message,
            "action": str(self.action),
            "detail": self.detail,
        }


@dataclass
class UploadAssessment:
    """Lo que el sistema dedujo del archivo, mas lo que aconseja hacer."""

    filename: str
    header: DocumentHeader
    resolved_code: str | None
    resolved_version: str | None
    advices: list[Advice] = field(default_factory=list)
    approval: ApprovalBlock = field(default_factory=ApprovalBlock)

    @property
    def blocking(self) -> bool:
        """True si no se puede ingestar sin intervencion del usuario."""
        return any(a.severity is Severity.ERROR for a in self.advices)

    @property
    def needs_review(self) -> bool:
        return any(a.severity in (Severity.ERROR, Severity.WARNING) for a in self.advices)

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "detected": {
                "code_in_document": self.header.code,
                "version_in_document": self.header.version,
                "revision_date": (
                    self.header.revision_date.isoformat() if self.header.revision_date else None
                ),
                "code_in_filename": code_from_filename(self.filename),
                "aprobado_por": self.approval.aprobado_por,
                "revisado_por": self.approval.revisado_por,
                "elaborado_por": self.approval.elaborado_por,
            },
            "resolved_code": self.resolved_code,
            "resolved_version": self.resolved_version,
            "blocking": self.blocking,
            "advices": [a.to_dict() for a in self.advices],
        }


# Tipos de documento que deben declarar quien los aprobo.
#
# No todos lo hacen, y no es un descuido: un FORMATO es una plantilla que se
# rellena, y lo aprobado es el procedimiento que lo invoca. Lo comprobe sobre
# el SGC real: los 30 procedimientos, los 3 manuales, los 5 instructivos y los
# 2 planes llevan su bloque de firmas; de los 84 formatos, solo 14. Exigirselo
# a un formato produciria 70 avisos falsos y el usuario dejaria de leerlos.
TIPOS_CON_APROBACION = frozenset({"PR", "MN", "IN", "PL"})


def tipo_de_documento(code: str | None) -> str | None:
    """'CAL-PR-03' -> 'PR'. El tipo va en el segmento del medio del codigo."""
    if not code:
        return None
    partes = code.split("-")
    return partes[1].upper() if len(partes) >= 2 else None


def version_number(version: str | None) -> int:
    """'v3' -> 3. Devuelve 0 si no hay numero, para que las comparaciones no exploten."""
    if not version:
        return 0
    digits = "".join(ch for ch in version if ch.isdigit())
    return int(digits) if digits else 0


def assess_upload(
    db: Session, tenant_id: str, pdf_path: Path, original_name: str
) -> UploadAssessment:
    """Lee el documento, lo compara con lo ya registrado y devuelve los avisos."""
    header = read_header(pdf_path)
    approval = read_approval(pdf_path)
    name_code = code_from_filename(original_name)

    advices: list[Advice] = []

    # --- 1. Identidad del documento -------------------------------------------
    # El membrete manda sobre el nombre del archivo: lo primero lo imprime el
    # sistema documental, lo segundo lo escribe una persona.
    resolved_code = header.code or name_code
    resolved_version = header.version

    if header.code and name_code and header.code != name_code:
        advices.append(
            Advice(
                Severity.WARNING,
                "code_mismatch",
                f"El nombre del archivo dice {name_code}, pero el documento está codificado "
                f"como {header.code} en su membrete. Se usará {header.code}; conviene "
                "renombrar el archivo para que coincidan.",
                Action.RENAME_FILE,
                {"in_filename": name_code, "in_document": header.code},
            )
        )

    if not resolved_code:
        advices.append(
            Advice(
                Severity.ERROR,
                "no_code",
                "No pude determinar el código del documento: no aparece en el membrete ni "
                "al inicio del nombre del archivo. Indícalo manualmente o corrige el archivo.",
                Action.CONFIRM_CODE,
            )
        )
        return UploadAssessment(original_name, header, None, None, advices, approval)

    if not resolved_version:
        advices.append(
            Advice(
                Severity.WARNING,
                "no_version",
                "El documento no declara versión en su membrete. Se asumirá v1 salvo que "
                "indiques otra.",
                Action.REVIEW_VERSION,
            )
        )
        resolved_version = "v1"

    # --- 2. Evidencia de aprobacion -------------------------------------------
    #
    # La clausula 7.5.2 pide que la informacion documentada se revise y se
    # apruebe antes de emitirse. Hasta ahora no se comprobaba nada de eso: el
    # sistema validaba QUE documento es y en que version, pero no si alguien lo
    # habia aprobado. Un procedimiento sin aprobar que el asistente cita como
    # vigente es precisamente la no conformidad que este producto existe para
    # evitar.
    #
    # Son dos evidencias distintas y las dos hacen falta:
    #   - el documento declara QUIEN lo aprobo, en su bloque de firmas;
    #   - la Lista Maestra declara CUANDO se aprobo.
    tipo = tipo_de_documento(resolved_code)

    if tipo in TIPOS_CON_APROBACION and not approval.tiene_aprobacion:
        advices.append(
            Advice(
                Severity.WARNING,
                "sin_bloque_de_aprobacion",
                "El documento no declara quién lo aprobó: no encontré el bloque "
                "ELABORADO / REVISADO / APROBADO POR en su última página. La cláusula "
                "7.5.2 pide que esté revisado y aprobado antes de emitirse. Confirma "
                "que es la versión aprobada antes de publicarlo.",
                Action.CONFIRM_APPROVAL,
                {"tipo": tipo},
            )
        )

    registro = (
        db.query(RegistryEntry)
        .filter(RegistryEntry.tenant_id == tenant_id, RegistryEntry.code == resolved_code)
        .one_or_none()
    )

    if registro is None:
        advices.append(
            Advice(
                Severity.WARNING,
                "fuera_de_lista_maestra",
                f"{resolved_code} no figura en la Lista Maestra de Documentos Internos. "
                f"Un documento controlado que el asistente va a citar debería estar "
                f"registrado ahí, con su versión y su fecha de aprobación.",
                Action.CONFIRM_APPROVAL,
            )
        )
    else:
        if registro.effective_date is None:
            advices.append(
                Advice(
                    Severity.WARNING,
                    "sin_fecha_de_aprobacion",
                    f"{resolved_code} figura en la Lista Maestra pero sin fecha de "
                    f"aprobación. Sin esa fecha no hay evidencia de cuándo entró en "
                    f"vigor.",
                    Action.CONFIRM_APPROVAL,
                )
            )
        if registro.version and registro.version != resolved_version:
            # Este es el error que motivo el modulo entero: un instructivo
            # impreso como v2 registrado como v1, y nadie lo noto en mas de un
            # ano. Aqui salta en el momento de subirlo.
            advices.append(
                Advice(
                    Severity.WARNING,
                    "version_discrepa_de_lista_maestra",
                    f"El documento se presenta como {resolved_version}, pero la Lista "
                    f"Maestra registra {resolved_code} {registro.version}. Una de las dos "
                    f"está desactualizada: confirma cuál antes de publicarlo.",
                    Action.REVIEW_VERSION,
                    {"en_documento": resolved_version, "en_lista_maestra": registro.version},
                )
            )

    # --- 3. Comparacion con lo ya registrado ----------------------------------
    existing = (
        db.query(Document)
        .filter(Document.tenant_id == tenant_id, Document.code == resolved_code)
        .all()
    )

    if not existing:
        advices.append(
            Advice(
                Severity.INFO,
                "new_document",
                f"Documento nuevo: {resolved_code} {resolved_version}. No hay versiones "
                "previas registradas.",
            )
        )
        return UploadAssessment(
            original_name, header, resolved_code, resolved_version, advices, approval
        )

    nueva = version_number(resolved_version)
    misma_version = [d for d in existing if d.version == resolved_version]
    vigentes = [d for d in existing if d.status == "vigente"]

    if misma_version:
        advices.append(
            Advice(
                Severity.WARNING,
                "same_version_exists",
                f"Ya existe {resolved_code} {resolved_version} en el sistema. Si es una "
                "corrección del mismo archivo, reemplázalo; si es una revisión nueva, "
                "debería subir de versión.",
                Action.REPLACE_EXISTING,
                {"document_ids": [d.id for d in misma_version]},
            )
        )

    anteriores_vigentes = [
        d for d in vigentes if d.version != resolved_version and version_number(d.version) < nueva
    ]
    if anteriores_vigentes:
        listado = ", ".join(f"{d.code} {d.version}" for d in anteriores_vigentes)
        advices.append(
            Advice(
                Severity.WARNING,
                "supersedes_previous",
                f"Esta versión reemplaza a {listado}, que sigue marcada como vigente. Dos "
                "versiones vigentes del mismo documento es una no conformidad de control de "
                "información documentada: marca la anterior como obsoleta para que el "
                "asistente deje de citarla.",
                Action.MARK_PREVIOUS_OBSOLETE,
                {"document_ids": [d.id for d in anteriores_vigentes]},
            )
        )

    mas_nuevas = [d for d in existing if version_number(d.version) > nueva]
    if mas_nuevas:
        listado = ", ".join(f"{d.code} {d.version}" for d in mas_nuevas)
        advices.append(
            Advice(
                Severity.WARNING,
                "older_than_registered",
                f"Estás subiendo {resolved_version}, pero el sistema ya tiene {listado}. Si "
                "es una versión histórica, márcala como obsoleta para conservar la "
                "trazabilidad sin que el asistente la cite.",
                Action.REVIEW_VERSION,
                {"document_ids": [d.id for d in mas_nuevas]},
            )
        )

    return UploadAssessment(
        original_name, header, resolved_code, resolved_version, advices, approval
    )
