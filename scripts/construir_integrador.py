"""
La presentacion del Proyecto Integrador: la solucion COMPLETA, en 14 laminas.

Cuenta las dos mitades del sistema juntas -- el RAG con sus guardrails y el
fine-tuning con la generacion de imagenes -- con las secciones que pide el
enunciado: problema, objetivos, arquitectura, tecnologias, resultados,
limitaciones y proximos pasos.

    uv run python scripts/construir_integrador.py

Reutiliza la hoja de estilo de `presentacion.html` para que todo el material del
proyecto se vea como una sola cosa.
"""

from __future__ import annotations

import base64
import html
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

DOCS = RAIZ / "docs"
ORIGEN = DOCS / "presentacion.html"
DESTINO = DOCS / "presentacion-integrador.html"
TOTAL = 14


def e(t) -> str:
    return html.escape(str(t or ""))


def uri(ruta: Path) -> str | None:
    if not ruta.exists():
        return None
    tipo = {"png": "png", "jpg": "jpeg", "jpeg": "jpeg"}[ruta.suffix.lstrip(".").lower()]
    return f"data:image/{tipo};base64,{base64.b64encode(ruta.read_bytes()).decode()}"


def figura(ruta: Path, pie: str, clase: str = "") -> str:
    u = uri(ruta)
    if not u:
        return ""
    return (
        f'<figure class="figura{clase}"><img src="{u}" alt="{e(pie)}">'
        f"<figcaption>{pie}</figcaption></figure>"
    )


def lamina(n: int, seccion: str, cuerpo: str, pie: str) -> str:
    return f'''<section class="lamina">
  <header class="membrete"><span class="cod">NormIA {n:02d}</span><span class="sec">{e(seccion)}</span></header>
  <div class="cuerpo">
{cuerpo}
  </div>
  <footer class="pie"><span>{e(pie)}</span><span class="num">{n:02d} / {TOTAL}</span></footer>
</section>'''


def construir() -> str:
    cap = DOCS / "capturas"
    dia = DOCS / "diagramas"
    img = DOCS / "imagenes"
    L = []

    # 01 portada --------------------------------------------------------------
    L.append(f'''<section class="lamina viva">
  <header class="membrete"><span class="cod">NormIA 01</span><span class="sec">Portada</span></header>
  <div class="cuerpo portada">
    <p class="entradilla">Proyecto Integrador, Diploma AI Engineer, DMC Institute</p>
    <h1 class="titular">NormIA: asistente de documentación y cumplimiento ISO 9001</h1>
    <p class="bajada">Sistema conversacional con recuperación aumentada, agente de herramientas
    y generación multimodal, sobre el sistema de gestión de la calidad de una empresa de
    logística. Ejecutado íntegramente en infraestructura propia.</p>
    <dl class="ficha">
      <div><dt>Autor</dt><dd>Klauz A. España Irrazabal</dd></div>
      <div><dt>Cliente</dt><dd>ABISERVI, logística</dd></div>
      <div><dt>Bloques</dt><dd>Chatbot, agentes, generativa</dd></div>
      <div><dt>Entrega</dt><dd>22 de octubre de 2026</dd></div>
    </dl>
  </div>
  <footer class="pie"><span>Portada</span><span class="num">01 / {TOTAL}</span></footer>
</section>''')

    # 02 problema -------------------------------------------------------------
    L.append(lamina(2, "Problema", '''    <p class="entradilla">El caso de negocio</p>
    <h2 class="titular">Consultar un procedimiento exige conocer documento, versión y cláusula</h2>
    <p class="bajada">Para responder «¿cuánto tiempo tengo para atender un requerimiento?» hay
    que saber tres cosas antes de leer nada: en qué documento está, cuál es la versión vigente
    y en qué sección.</p>
    <div class="rejilla r3">
      <div class="tarjeta"><span class="cifra">132</span><h3>documentos controlados</h3>
        <p>En 11 áreas: manuales, políticas, procedimientos, instructivos y formatos.</p></div>
      <div class="tarjeta"><span class="cifra">1043</span><h3>cláusulas numeradas</h3>
        <p>La respuesta casi nunca es «el documento». Es una cláusula concreta dentro de él.</p></div>
      <div class="tarjeta"><span class="cifra">v1 ≠ v2</span><h3>el riesgo real</h3>
        <p>Actuar sobre una versión derogada es una no conformidad en auditoría, no un despiste.</p></div>
    </div>
    <p class="bajada">Hoy eso se resuelve preguntando a Calidad. Cuando esa persona no está, se
    resuelve adivinando, y un asistente que también adivine <b>empeora el problema</b>.</p>''',
    "El problema que justifica el proyecto"))

    # 03 objetivos ------------------------------------------------------------
    L.append(lamina(3, "Objetivos", '''    <p class="entradilla">Alcance y verificación</p>
    <h2 class="titular">Cuatro objetivos, cada uno con su criterio de verificación</h2>
    <table class="tabla">
      <tr><th>Objetivo</th><th>Cómo se verifica</th></tr>
      <tr><td class="n">Responder sobre documentación vigente, citando siempre la fuente</td>
          <td>Banco de 12 consultas reales con la respuesta verificada leyendo el documento</td></tr>
      <tr><td class="n">No inventar nunca un documento</td>
          <td>Cada código citado se comprueba contra la base. Si no existe, la respuesta se
          descarta</td></tr>
      <tr><td class="n">Producir el material de concienciación que la norma exige</td>
          <td>las cláusulas 7.2, 7.3 y 7.4 de ISO 9001: competencia, toma de conciencia y comunicación</td></tr>
      <tr><td class="n"><b>Que ningún documento salga de la organización</b></td>
          <td>Modelos locales. Es una <b>restricción de partida</b>, no una optimización</td></tr>
    </table>
    <p class="bajada">El último condiciona todo lo demás: un SGC contiene procedimientos internos
    y organigramas, y eso no puede viajar a una API de terceros.</p>''',
    "Objetivos y criterios de verificación"))

    # 04 arquitectura ---------------------------------------------------------
    L.append(lamina(4, "Arquitectura", f'''    <p class="entradilla">Vista general</p>
    <h2 class="titular">Arquitectura en cuatro capas, ejecutada en una sola máquina</h2>
    {figura(dia / "01-arquitectura.png", "<b>Nada sale de la máquina.</b> El modelo de lenguaje corre en local y los embeddings también; PostgreSQL guarda documentos, cláusulas, conversaciones, hallazgos y la traza de auditoría.", " ancha")}''',
    "Vista general del sistema"))

    # 05 flujo ----------------------------------------------------------------
    L.append(lamina(5, "Arquitectura: el flujo", f'''    <p class="entradilla">Paso a paso</p>
    <h2 class="titular">Cuatro controles pueden descartar una respuesta ya redactada</h2>
    {figura(dia / "02-flujo-consulta-horizontal.png", "La pregunta se compara <b>a la vez</b> con las preguntas frecuentes y con las cláusulas vigentes. Desde ahí, cuatro guardrails: en cualquiera de ellos la respuesta se descarta y la consulta pasa a la cola de Calidad.", " ancha")}''',
    "En rojo escala, en verde responde"))

    # 06 tecnologias ----------------------------------------------------------
    L.append(lamina(6, "Tecnologías", '''    <p class="entradilla">Stack y licencias</p>
    <h2 class="titular">Seis decisiones técnicas, con la licencia verificada en cada una</h2>
    <table class="tabla">
      <tr><th>Capa</th><th>Elección</th><th>Por qué esa</th></tr>
      <tr><td class="n">Backend</td><td>Python 3.12 y FastAPI</td>
          <td>Estándar, fácil de probar. <b>283 tests</b> cubren guardrails y herramientas.</td></tr>
      <tr><td class="n">Base de datos</td><td>PostgreSQL 16 con pgvector</td>
          <td>Integridad relacional (vigente/obsoleto) <b>y</b> búsqueda vectorial en un solo
          motor. Con dos, habría que sincronizarlos.</td></tr>
      <tr><td class="n">LLM</td><td>Qwen3, Apache 2.0, en local</td>
          <td>Licencia comercial limpia y sin coste por consulta. Elegido <b>midiendo</b>, no
          por ser el más nuevo.</td></tr>
      <tr><td class="n">Embeddings</td><td>BAAI/bge-m3, licencia MIT</td>
          <td>1024 dimensiones, corre en local sin clave de API.</td></tr>
      <tr><td class="n">Fine-tuning</td><td>QLoRA en 4 bits</td>
          <td>Base congelada; se entrena el <b>0,37 %</b> de los parámetros.</td></tr>
      <tr><td class="n">Imagen</td><td>SDXL con Diffusers</td>
          <td>El modelo dibuja la escena; el logotipo real se <b>compone encima</b>. Licencia
          <b>OpenRAIL++</b>, la única con restricciones de uso: en producción se sustituiría por
          FLUX.1, que es Apache 2.0.</td></tr>
    </table>''', "Stack y justificación técnica"))

    # 07 agente ---------------------------------------------------------------
    L.append(lamina(7, "Agente", '''    <p class="entradilla">Bloque de agentes inteligentes</p>
    <h2 class="titular">El agente expone ocho herramientas y el modelo selecciona</h2>
    <table class="tabla">
      <tr><th>Herramienta</th><th>Qué hace</th></tr>
      <tr><td class="n">buscar_documentos</td><td>«¿Tienen políticas de TI?», consulta el catálogo</td></tr>
      <tr><td class="n">leer_documento</td><td>Resume un documento y ofrece navegar sus secciones</td></tr>
      <tr><td class="n">register_finding y ampliar_hallazgo</td><td><b>Escriben en la base:</b> registran una no conformidad</td></tr>
      <tr><td class="n">get_capa_status</td><td>Estado de las acciones correctivas de un hallazgo</td></tr>
      <tr><td class="n">escalate_to_quality</td><td>Deriva al responsable humano y <b>lo registra</b></td></tr>
      <tr><td class="n">describir_capacidades</td><td>Responde sobre sí mismo sin consultar el SGC</td></tr>
      <tr><td class="n">generar_material_visual</td><td>Prepara un cartel con respaldo documental</td></tr>
    </table>
    <p class="bajada">No es un enrutador de intenciones: las herramientas se exponen por
    <i>function calling</i> y <b>el modelo elige</b>. Tres de ellas tienen efecto real sobre la
    base de datos.</p>''', "El sistema actúa, no solo responde"))

    # 08 guardrails -----------------------------------------------------------
    L.append(lamina(8, "Diferencial", '''    <p class="entradilla">Guardrails</p>
    <h2 class="titular">Cuatro controles derivados de fallos observados en producción</h2>
    <table class="tabla">
      <tr><th>Control</th><th>El fallo que lo motivó</th></tr>
      <tr><td class="n">1. Umbral semántico</td>
          <td>Sin un mínimo de parecido, la búsqueda <b>siempre</b> devuelve algo, aunque no
          tenga nada que ver.</td></tr>
      <tr><td class="n">2. Versión citada</td>
          <td>Escribió «STI-PO-01 v1» cuando el documento está en v2, contradiciendo su propia
          ficha de fuentes.</td></tr>
      <tr><td class="n">3. Código citado</td>
          <td>Citó <code>PROC-CAL-04</code>, que no existe: lo copió del <b>ejemplo de formato</b>
          de su propio prompt.</td></tr>
      <tr><td class="n">4. Fundamentación</td>
          <td>Prometía derivar a Calidad sin que se registrara nada. Prometer y no cumplir es
          peor que declararse incapaz.</td></tr>
    </table>
    <p class="bajada">Los cuatro pueden <b>descartar una respuesta ya redactada</b>. Otros ocho
    controles evitan llegar ahí. Todos son <b>código determinista</b>, no confianza en el modelo:
    por eso se pueden auditar.</p>''', "Cuatro que descartan, ocho que previenen"))

    # 09 generativa -----------------------------------------------------------
    L.append(lamina(9, "Generativa: texto", '''    <p class="entradilla">Fine-tuning con QLoRA</p>
    <h2 class="titular">QLoRA eleva la cita correcta del 88 % al 100 % sin penalizar la latencia</h2>
    <p class="bajada">584 ejemplos ensamblados de lo que el proyecto <b>ya tenía verificado</b>:
    730 respuestas comprobadas contra el texto de su cláusula, los 14 rechazos documentados y
    los esquemas de las herramientas.</p>
    <table class="tabla">
      <tr><th>Métrica</th><th>Sin afinar</th><th>Con fine-tuning</th></tr>
      <tr><td>Cita el documento correcto</td><td class="n">88 %</td><td class="n">100 %</td></tr>
      <tr><td>Marcada como fundamentada</td><td class="n">88 %</td><td class="n">100 %</td></tr>
      <tr><td>Sin citas inventadas</td><td class="n">100 %</td><td class="n">100 %</td></tr>
      <tr><td>Latencia mediana</td><td class="n">7,5 s</td><td class="n">7,9 s</td></tr>
    </table>
    <p class="bajada">Medido sobre el sistema completo, con la documentación recuperada delante,
    que es como funciona en producción.</p>
    <p class="bajada">En una segunda medición <b>sin recuperación</b>, para aislar qué aporta el
    modelo por sí mismo, la respuesta pasa de <b>1014 a 160 caracteres</b> y la atribución
    correcta sube del 38 % al 62 %. Son dos experimentos distintos y no deben mezclarse.</p>''', "QLoRA sobre 584 ejemplos verificados"))

    # 10 imagenes -------------------------------------------------------------
    piezas = [p for p in sorted(img.glob("*.png"))][:3]
    galeria = "".join(figura(p, "") for p in piezas)
    L.append(lamina(10, "Generativa: imagen", f'''    <p class="entradilla">Difusión con SDXL</p>
    <h2 class="titular">Difusión local para el material que exigen las cláusulas 7.2 a 7.4</h2>
    <p class="bajada">ISO 9001 pide formar y comunicar en sus <b>cláusulas 7.2, 7.3 y 7.4</b>. El material de
    concienciación no es decoración: da servicio a tres cláusulas del estándar.</p>
    <div class="rejilla r3">{galeria}</div>
    <p class="bajada">El modelo genera el arte; <b>el logotipo real se compone encima</b>. Y
    hereda el guardrail del asistente: <b>no se genera material sin respaldo documental</b>. Un
    cartel sin cita es propaganda; con ella es material del sistema de gestión.</p>''',
    "SDXL en local, con el logotipo real"))

    # 11 demo -----------------------------------------------------------------
    L.append(lamina(11, "En funcionamiento", f'''    <p class="entradilla">Capturas reales</p>
    <h2 class="titular">El widget se integra en la intranet sin heredar ni imponer estilos</h2>
    <div class="panel-par">
      {figura(cap / "02-respuesta-con-cita.png", "<b>Respuesta fundamentada.</b> El dato en negrita; debajo la referencia con el título por delante, y el código y la versión como respaldo.")}
      {figura(cap / "03-guardrail-fuera-de-alcance.png", "<b>El guardrail.</b> Preguntando por vacaciones, tema ausente del SGC: no inventa, lo dice, deriva a Calidad y marca la respuesta como no verificada.")}
    </div>''', "Capturas del sistema real"))

    # 12 resultados -----------------------------------------------------------
    L.append(lamina(12, "Resultados", '''    <p class="entradilla">Qué aporta cada pieza</p>
    <h2 class="titular">La recuperación eleva el acierto del 8 % al 100 %</h2>
    <table class="tabla">
      <tr><th>Métrica</th><th>Modelo solo</th><th>Con RAG</th><th>RAG + fine-tuning</th></tr>
      <tr><td>Acierta en conjunto</td><td class="n">8 %</td><td class="n">100 %</td><td class="n">100 %</td></tr>
      <tr><td>Cita el documento correcto</td><td class="n">25 %</td><td class="n">88 %</td><td class="n">100 %</td></tr>
      <tr><td>Sin citas inventadas</td><td class="n">92 %</td><td class="n">100 %</td><td class="n">100 %</td></tr>
      <tr><td>Latencia mediana</td><td class="n">n/a</td><td class="n">7,5 s</td><td class="n">7,9 s</td></tr>
    </table>
    <p class="bajada"><b>El modelo solo acierta 1 de 12.</b> La recuperación lo lleva a 12 de 12,
    y el fine-tuning cierra la cita y acorta la respuesta.</p>
    <p class="bajada">Esa primera columna es la razón de ser del proyecto: un modelo sin los
    documentos delante responde con seguridad y <b>se inventa el código del procedimiento</b>.</p>''',
    "12 consultas sobre el mismo corpus"))

    # 13 limitaciones ---------------------------------------------------------
    L.append(lamina(13, "Limitaciones", '''    <p class="entradilla">Lo que no se demostró</p>
    <h2 class="titular">Cinco limitaciones identificadas y medidas</h2>
    <ul class="puntos neg">
      <li>El banco son <b>12 consultas</b>. Suficiente para ver un patrón, corto para afirmar
      un porcentaje con intervalo de confianza.</li>
      <li>El fine-tuning <b>empeoró la emisión de llamadas</b> a herramienta, del 100 % al 75 %.
      Entrenar una llamada la empuja hacia su forma de texto.</li>
      <li><b>24 cláusulas del SGC son imágenes</b> y una está escaneada: hoy se descartan en la
      ingesta. Harían falta OCR y un modelo de visión.</li>
      <li>El material visual <b>no entra en documentos controlados</b>. Formación sí; documento
      del SGC no: sería un problema de auditoría, no una mejora.</li>
      <li>Sin <b>pruebas de carga</b>: no se sabe cuántos usuarios concurrentes aguanta.</li>
    </ul>''', "Lo que un tribunal preguntaría primero"))

    # 14 proximos pasos -------------------------------------------------------
    L.append(lamina(14, "Próximos pasos", '''    <p class="entradilla">De prototipo a producto</p>
    <h2 class="titular">Cinco pasos para llevarlo a producción</h2>
    <table class="tabla">
      <tr><th>Paso</th><th>Por qué</th></tr>
      <tr><td class="n">Servir con vLLM en vez de Ollama</td>
          <td><b>Medido:</b> Ollama ignora <code>tool_choice</code> con los tres valores. No se
          puede forzar una llamada, y no es un servidor de carga.</td></tr>
      <tr><td class="n">Cola para lo lento</td>
          <td>Generar una imagen bloquea un hilo 29 s. La ingesta de PDFs, igual.</td></tr>
      <tr><td class="n">Estado compartido en Redis</td>
          <td>El cortacircuitos y el límite de peticiones viven en memoria del proceso: con dos
          instancias, cada una lleva su cuenta.</td></tr>
      <tr><td class="n">OCR y visión para lo escaneado</td>
          <td>Recupera las 24 cláusulas que hoy se pierden.</td></tr>
      <tr><td class="n">LoRA de marca</td>
          <td>18 piezas ya construidas. Daría identidad visual sin componer el logotipo.</td></tr>
    </table>
    <p class="bajada">Lo que <b>no</b> cambiaría: el modelo en local. Para un sistema de gestión
    certificado eso no es una ventaja técnica: es la condición para que el proyecto exista.</p>''',
    "Roadmap priorizado"))

    fuente = ORIGEN.read_text(encoding="utf-8")
    estilo = re.search(r"<style>(.*?)</style>", fuente, re.S).group(1)
    guion = re.search(r"<script>(.*?)</script>", fuente, re.S).group(1)

    return f'''<!doctype html>
<html lang="es" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>NormIA, Proyecto Integrador, Diploma AI Engineer</title>
<style>{estilo}</style>
</head>
<body>
<div class="solo-lector" id="anuncio" aria-live="polite"></div>
{chr(10).join(L)}
<div class="barra" id="barra"></div>
<nav class="nav">
  <button id="ant" aria-label="Lámina anterior">&#8249;</button>
  <button id="sig" aria-label="Lámina siguiente">&#8250;</button>
</nav>
<script>{guion}</script>
</body>
</html>'''


def main() -> int:
    if not ORIGEN.exists():
        print(f"falta {ORIGEN}: de ahi sale la hoja de estilo")
        return 1
    salida = construir()
    DESTINO.write_text(salida, encoding="utf-8")
    print(f"\n  {DESTINO}")
    print(f"  {TOTAL} laminas · {len(salida) / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
