"""
Genera un SGC de muestra para que el proyecto se pueda clonar y ejecutar.

Los documentos reales de un cliente son informacion controlada y confidencial:
procedimientos internos, organigramas, politicas. Nada de eso entra a un
repositorio. Es la misma razon por la que el modelo corre en local -- si los
documentos no salen de la maquina, tampoco salen por git.

Pero un repositorio que no se puede ejecutar sin datos que no estan tampoco
sirve. Estos documentos sinteticos imitan la estructura de un SGC real
(numeracion de clausulas, membrete con codigo y version, tablas de tiempos,
referencias cruzadas entre documentos) para que el pipeline completo se pueda
probar de punta a punta.

Uso:
    uv run python scripts/generate_demo_corpus.py
    uv run python scripts/seed_demo_tenant.py --tenant demo-publica --name "Empresa Demo"
    uv run python scripts/ingest_docs.py --tenant demo-publica
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@dataclass(frozen=True)
class DocumentoDemo:
    code: str
    version: str
    title: str
    area: str
    effective_date: str
    body: str


MEMBRETE = """{title}
CODIGO : {code}
VERSION : {version_num}
REVISION : {effective_date}
"""


DOCUMENTOS = [
    DocumentoDemo(
        code="STI-PR-01",
        version="v4",
        title="Atencion de Solicitudes Tecnologicas",
        area="PROCEDIMIENTOS",
        effective_date="2025-07-08",
        body="""1. OBJETIVO
Describir el procedimiento de atencion de solicitudes tecnologicas, con el fin de
garantizar un estandar de calidad en el soporte y la satisfaccion de los usuarios.

2. ALCANCE
Aplica a todas las solicitudes de soporte tecnologico interno de la organizacion.

3. DOCUMENTOS DE REFERENCIA
CAL-PR-01 Control de Informacion Documentada
STI-PO-01 Politica de Seguridad de la Informacion

4. RESPONSABILIDAD
El Jefe de Tecnologia de la Informacion es responsable del cumplimiento de este
procedimiento.

5. ABREVIATURAS Y DEFINICIONES
TI: Tecnologia de la Informacion.
NBD: Next Business Day, siguiente dia habil.
IMAC: Install, Move, Add, Change.

6. CONDICIONES GENERALES
El flujo de atencion aplica tanto de forma presencial como remota.

6.1. Soporte nivel 1: Se guia al usuario via telefonica o acceso remoto para
atender la solicitud reportada.

6.2. Soporte nivel 2: Atencion de forma presencial, cuando el soporte nivel 1 no
resuelve la solicitud reportada.

7.1. La solicitud de atencion se realiza a traves del Formulario de Solicitudes
Tecnologicas, dentro del horario laboral.

7.2. Identificados los requisitos de la solicitud, se brinda prioridad segun el
tiempo de respuesta establecido en el Cuadro N 01.

7.6. El Jefe de TI informa al solicitante el estado de la solicitud, asi como las
acciones a tomar o pendientes.
Cuadro N 01
TIPO DE SOLICITUD | TIEMPO MAXIMO DE RESPUESTA | TIEMPO MAXIMO DE SOLUCION
INCIDENCIA: Afecta a un solo usuario | 01 hora | 05 horas
INCIDENCIA: Afecta a toda la empresa | 30 minutos | Segun disponibilidad de proveedor
REQUERIMIENTO: 1 IMAC | Segun acuerdo con solicitante | Segun disponibilidad de proveedor
REQUERIMIENTO: Mas de 1 IMAC | NBD | Segun disponibilidad de proveedor

7.8. PARA MANTENIMIENTOS PREVENTIVOS
El mantenimiento preventivo de los equipos de computo se realiza, como minimo,
una vez al ano.

8. REGISTROS
STI-FO-01 Dashboard de Solicitudes Tecnologicas
""",
    ),
    DocumentoDemo(
        code="CAL-PR-03",
        version="v2",
        title="No Conformidad y Acciones Correctivas",
        area="PROCEDIMIENTOS",
        effective_date="2025-04-08",
        body="""1. OBJETIVO
Establecer la metodologia para el tratamiento de las no conformidades detectadas
en el sistema de gestion de la calidad.

2. ALCANCE
Aplica a todos los procesos del sistema de gestion de la calidad.

3. DOCUMENTOS DE REFERENCIA
CAL-MN-01 Manual de la Calidad

4. RESPONSABILIDAD
El Responsable de Proceso donde se detecta la no conformidad.

6. CONDICIONES GENERALES
Toda no conformidad detectada se registra dentro de los 3 dias habiles
siguientes a su deteccion.

7.1. REGISTRO DEL HALLAZGO
Se describe el hallazgo en el formato Acciones Correctivas CAL-FO-09,
codificandolo y enumerandolo segun corresponda.

7.2. ACCIONES INMEDIATAS
La accion inmediata para contener la no conformidad se registra en el formato
Acciones Correctivas CAL-FO-09.

7.3. ANALISIS DE CAUSAS
El Responsable de Proceso realiza el analisis necesario para determinar la causa
raiz, la misma que se registra en el formato Acciones Correctivas CAL-FO-09.

7.4. PROPUESTA DE ACCIONES CORRECTIVAS
El Responsable de Proceso define las acciones correctivas y su fecha de
implementacion.

7.6. VERIFICACION DE LA EFICACIA
La verificacion de la eficacia se realiza a los 30 dias calendario de
implementada la accion correctiva.

8. REGISTROS
CAL-FO-09 Acciones Correctivas
""",
    ),
    DocumentoDemo(
        code="COM-PR-01",
        version="v3",
        title="Compras",
        area="PROCEDIMIENTOS",
        effective_date="2025-05-29",
        body="""1. OBJETIVO
Establecer la metodologia para la adquisicion de bienes y servicios que cumplan
los requisitos de la organizacion.

2. ALCANCE
Aplica a todas las compras de bienes y servicios.

4. RESPONSABILIDAD
El Coordinador de Compras es responsable del cumplimiento de este procedimiento.

6.1. Las compras se realizan unicamente a proveedores incluidos en la Lista de
Proveedores Aprobados COM-FO-01.

7.1. VERIFICACION DE REQUERIMIENTOS
El Coordinador de Compras verifica los requerimientos diarios recibidos por
correo electronico o por el formulario interno.

7.2. SOLICITUD DE COTIZACIONES
Se solicitan como minimo 3 cotizaciones para compras superiores a 1000 soles.

7.3. GENERACION DE LA ORDEN DE COMPRA
Aprobada la cotizacion, se emite la Orden de Compra COM-FO-02.

7.4. RECEPCION Y CONFORMIDAD
La conformidad de la recepcion se registra dentro de las 48 horas siguientes a
la entrega.

8. REGISTROS
COM-FO-01 Lista de Proveedores Aprobados
COM-FO-02 Orden de Compra
""",
    ),
    DocumentoDemo(
        code="CAL-MN-01",
        version="v3",
        title="Manual de la Calidad",
        area="MANUALES",
        effective_date="2026-01-02",
        body="""1. OBJETIVO
Describir el Sistema de Gestion de la Calidad de la organizacion, que sirva de
referencia para su aplicacion.

3. DOCUMENTOS DE REFERENCIA
ISO 9000:2015 Sistemas de Gestion de la Calidad. Fundamentos y Vocabulario
ISO 9001:2015 Sistemas de Gestion de la Calidad. Requisitos

4. RESPONSABILIDADES
El Coordinador de Calidad es responsable de mantener actualizada la informacion
documentada contenida en este Manual.

6. APLICABILIDAD
En el SGC no aplica el requisito 7.1.5.2 Trazabilidad de las mediciones, debido
a que la organizacion no utiliza equipos de seguimiento y medicion que deban ser
calibrados.

9. POLITICA DE LA CALIDAD
La Alta Direccion ha establecido los compromisos que son el marco de referencia
para los Objetivos de la Calidad.

14.2. NUESTROS PROCESOS
PROCESO | DESCRIPCION
GESTION GERENCIAL | Establece y verifica la adecuacion de la Politica de la Calidad, asegura que se establecen los objetivos y analiza los resultados de los indicadores.
GESTION DE LA CALIDAD | Controla la informacion documentada, gestiona las no conformidades, las auditorias internas y las acciones correctivas.
COMPRAS | Gestiona la adquisicion de bienes y servicios y la evaluacion de proveedores.
SOPORTE DE TECNOLOGIA DE LA INFORMACION | Atiende las solicitudes tecnologicas y asegura la continuidad de los sistemas.

15. PLANIFICACION DEL SISTEMA
Se ha definido una metodologia para identificar, analizar y dar tratamiento a
los riesgos y oportunidades del SGC.
""",
    ),
    DocumentoDemo(
        code="STI-PO-01",
        version="v2",
        title="Politica de Seguridad de la Informacion",
        area="POLITICAS",
        effective_date="2026-04-09",
        body="""1. OBJETIVO
Establecer los lineamientos de seguridad de la informacion, respaldo y
proteccion de datos de la organizacion.

4. RESPONSABILIDAD
El Jefe de Tecnologia de la Informacion.

6.1. RESPALDO DE LA INFORMACION
El respaldo de la informacion critica se realiza de forma diaria y se conserva
por un periodo de 90 dias.

6.2. CONTRASENAS
Las contrasenas deben tener una longitud minima de 12 caracteres y se renuevan
cada 90 dias.

7.4.4. INCUMPLIMIENTO
Ante la deteccion del incumplimiento de esta politica, el personal de TI da
parte a la Gerencia General, quien toma las medidas administrativas que
correspondan.
""",
    ),
    DocumentoDemo(
        code="STI-PO-02",
        version="v1",
        title="Politica de Uso de Equipos y Dispositivos",
        area="POLITICAS",
        effective_date="2026-04-09",
        body="""1. OBJETIVO
Establecer las condiciones de uso de los equipos y dispositivos asignados al
personal.

4.6.1. OBLIGACIONES DEL USUARIO
Cumplir esta politica, custodiar adecuadamente el equipo asignado, no instalar
software no autorizado ni permitir su uso por terceros, y reportar oportunamente
cualquier incidente.

7.6.1. SOFTWARE NO AUTORIZADO
Esta PROHIBIDA la descarga o instalacion de software no autorizado por el area
de TI, en equipos corporativos o personales utilizados para fines laborales.
""",
    ),
]


def texto_completo(doc: DocumentoDemo) -> str:
    """Documento con su membrete, como lo emite un sistema documental real."""
    encabezado = MEMBRETE.format(
        title=doc.title.upper(),
        code=doc.code,
        version_num=doc.version.lstrip("v").zfill(2),
        effective_date=doc.effective_date,
    )
    return f"{encabezado}\n{doc.body}"


def escribir_pdf(texto: str, destino: Path) -> None:
    """
    Texto a PDF usando cupsfilter, que viene con macOS.

    Se evita a proposito anadir una dependencia (reportlab, fpdf) solo para
    generar datos de ejemplo: el repositorio no deberia cargar con una libreria
    que la aplicacion no usa.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(texto)
        origen = f.name

    with open(destino, "wb") as salida:
        subprocess.run(
            ["cupsfilter", origen], stdout=salida, stderr=subprocess.DEVNULL, check=True
        )
    Path(origen).unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description="Genera un SGC de muestra")
    parser.add_argument("--tenant", default="demo-publica")
    parser.add_argument("--destino", default="data/knowledge_base")
    args = parser.parse_args()

    if sys.platform != "darwin":
        print(
            "Este generador usa `cupsfilter`, que solo existe en macOS.\n"
            "En Linux puedes usar: sudo apt install cups-filters, o convertir los\n"
            "textos a PDF con la herramienta que prefieras."
        )
        return 1

    destino = Path(args.destino) / args.tenant
    destino.mkdir(parents=True, exist_ok=True)

    filas = []
    for doc in DOCUMENTOS:
        nombre = f"{doc.code.lower().replace('-', '_')}_{doc.title.lower().replace(' ', '_')}.pdf"
        escribir_pdf(texto_completo(doc), destino / nombre)
        filas.append(
            {
                "filename": nombre,
                "code": doc.code,
                "version": doc.version,
                "title": doc.title,
                "area": doc.area,
                "effective_date": doc.effective_date,
                "status": "vigente",
            }
        )
        print(f"  {doc.code:12} {doc.title}")

    columnas = ["filename", "code", "version", "title", "area", "effective_date", "status"]
    with open(destino / "metadata.csv", "w", encoding="utf-8", newline="") as f:
        escritor = csv.DictWriter(f, fieldnames=columnas)
        escritor.writeheader()
        escritor.writerows(filas)

    print()
    print(f"{len(DOCUMENTOS)} documentos en {destino}")
    print()
    print("Siguiente paso:")
    print(f"  uv run python scripts/seed_demo_tenant.py --tenant {args.tenant}")
    print(f"  uv run python scripts/ingest_docs.py --tenant {args.tenant}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
