"""
Da de alta a una persona en el sistema.

    uv run python scripts/crear_usuario.py \
        --email calidad@abiservi.pe --nombre "Eduardo Ojeda" --rol calidad

La contrasena se pide por teclado y no se hace eco, para que no acabe en el
historial del terminal ni en una captura de pantalla.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.core.auth import Rol, cifrar  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.db_models import Tenant, User  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--nombre", required=True)
    parser.add_argument("--rol", default=Rol.OPERARIO, choices=[r.value for r in Rol])
    parser.add_argument("--tenant", default=settings.default_tenant_id)
    parser.add_argument(
        "--password",
        help="Solo para sembrar entornos de prueba. Sin esto se pide por teclado.",
    )
    args = parser.parse_args()

    password = args.password or getpass.getpass("Contraseña: ")
    if len(password) < 8:
        print("  La contraseña necesita al menos 8 caracteres.")
        return 1

    db = SessionLocal()
    try:
        if db.get(Tenant, args.tenant) is None:
            print(f"  No existe el cliente '{args.tenant}'.")
            return 1

        correo = args.email.strip().lower()
        usuario = db.query(User).filter(User.email == correo).one_or_none()
        if usuario is not None:
            usuario.nombre = args.nombre
            usuario.rol = args.rol
            usuario.password = cifrar(password)
            usuario.activo = True
            accion = "actualizado"
        else:
            db.add(
                User(
                    tenant_id=args.tenant,
                    email=correo,
                    nombre=args.nombre,
                    password=cifrar(password),
                    rol=args.rol,
                )
            )
            accion = "creado"
        db.commit()
        print(f"  Usuario {accion}: {correo} ({args.rol}) en '{args.tenant}'")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
