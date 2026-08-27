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

from app.core.rag.doc_header import DocumentHeader, code_from_filename, read_header
from app.models.db_models import Document


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
            },
            "resolved_code": self.resolved_code,
            "resolved_version": self.resolved_version,
            "blocking": self.blocking,
            "advices": [a.to_dict() for a in self.advices],
        }


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
                f"El nombre del archivo dice {name_code}, pero el documento esta codificado "
                f"como {header.code} en su membrete. Se usara {header.code}; conviene "
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
                "No pude determinar el codigo del documento: no aparece en el membrete ni "
                "al inicio del nombre del archivo. Indicalo manualmente o corrige el archivo.",
                Action.CONFIRM_CODE,
            )
        )
        return UploadAssessment(original_name, header, None, None, advices)

    if not resolved_version:
        advices.append(
            Advice(
                Severity.WARNING,
                "no_version",
                "El documento no declara version en su membrete. Se asumira v1 salvo que "
                "indiques otra.",
                Action.REVIEW_VERSION,
            )
        )
        resolved_version = "v1"

    # --- 2. Comparacion con lo ya registrado ----------------------------------
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
        return UploadAssessment(original_name, header, resolved_code, resolved_version, advices)

    nueva = version_number(resolved_version)
    misma_version = [d for d in existing if d.version == resolved_version]
    vigentes = [d for d in existing if d.status == "vigente"]

    if misma_version:
        advices.append(
            Advice(
                Severity.WARNING,
                "same_version_exists",
                f"Ya existe {resolved_code} {resolved_version} en el sistema. Si es una "
                "correccion del mismo archivo, reemplazalo; si es una revision nueva, "
                "deberia subir de version.",
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
                f"Esta version reemplaza a {listado}, que sigue marcada como vigente. Dos "
                "versiones vigentes del mismo documento es una no conformidad de control de "
                "informacion documentada: marca la anterior como obsoleta para que el "
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
                f"Estas subiendo {resolved_version}, pero el sistema ya tiene {listado}. Si "
                "es una version historica, marcala como obsoleta para conservar la "
                "trazabilidad sin que el asistente la cite.",
                Action.REVIEW_VERSION,
                {"document_ids": [d.id for d in mas_nuevas]},
            )
        )

    return UploadAssessment(original_name, header, resolved_code, resolved_version, advices)
