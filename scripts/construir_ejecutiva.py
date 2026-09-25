"""
La presentacion EJECUTIVA: 10 laminas, fine-tuning y generacion de imagenes.

Es un entregable distinto de `presentacion-fase2.html`, que es tecnica y tiene
16. El enunciado pide un maximo de 10 y un enfoque de negocio: impacto
empresarial, arquitectura, resultados y ROI.

Aqui NO se habla de RAG. La Fase 1 ya se presento; esta va de lo que se
construyo despues.

    uv run python scripts/construir_ejecutiva.py

Reutiliza la hoja de estilo de `presentacion.html` para que las tres
presentaciones del proyecto se vean como una sola.
"""

from __future__ import annotations

import base64
import html
import json
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

DOCS = RAIZ / "docs"
ORIGEN = DOCS / "presentacion.html"
DESTINO = DOCS / "presentacion-ejecutiva.html"
LOGO = RAIZ / "data" / "marca" / "logo_abiservi.png"
TOTAL = 10


def e(t) -> str:
    return html.escape(str(t or ""))


def uri(ruta: Path) -> str | None:
    if not ruta.exists():
        return None
    tipo = {"png": "png", "jpg": "jpeg", "jpeg": "jpeg"}[ruta.suffix.lstrip(".").lower()]
    return f"data:image/{tipo};base64,{base64.b64encode(ruta.read_bytes()).decode()}"


def tabla_banco() -> str:
    """
    El banco completo, con recuperacion documental.

    Es la medicion VALIDA para el adaptador entrenado con contexto: se le evalua
    en las mismas condiciones en que se le enseno. Medirlo sin recuperacion es
    pedirle algo que nunca vio.
    """
    filas = {}
    for clave, ruta in (("base", "banco-4b-base.txt"), ("lora", "banco-4b-lora.txt")):
        p = DOCS / ruta
        if not p.exists():
            return '<p class="bajada">Pendiente: <code>evaluar_adaptador.py</code>.</p>'
        for linea in p.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^([A-Za-zA-Za-zAEIOUaeiouNn ]+?)\s{2,}([\d.]+%|[\d.]+ ?s)\s*$", linea)
            if m:
                filas.setdefault(m.group(1).strip(), {})[clave] = m.group(2).strip()

    orden = ["Cita el documento correcto", "Acierta la clausula",
             "Usa la herramienta correcta", "Escala cuando debe",
             "Sin citas inventadas", "Marcada como fundamentada", "Latencia mediana"]
    cuerpo = "".join(
        f'<tr><td>{e(k)}</td><td class="n">{e(filas[k].get("base", "—"))}</td>'
        f'<td class="n">{e(filas[k].get("lora", "—"))}</td></tr>'
        for k in orden if k in filas
    )
    return f'''<table class="tabla">
      <tr><th>Métrica</th><th>Sin afinar</th><th>Con fine-tuning</th></tr>
      {cuerpo}
    </table>'''


def tabla_tres_modelos() -> str:
    ruta = DOCS / "tres-modelos-empresa-demo-iso.json"
    if not ruta.exists():
        return '<p class="bajada">Pendiente: <code>comparar_tres_modelos.py</code>.</p>'
    d = json.loads(ruta.read_text(encoding="utf-8"))["resumen"]
    cols = [("base", "Sin entrenar"), ("instruct", "Entrenado"), ("lora", "Con fine-tuning")]
    filas = "".join(
        "<tr><td>" + e(m) + "</td>"
        + "".join(f'<td class="n">{e(d[k].get(m, "—"))}</td>' for k, _ in cols)
        + "</tr>"
        for m in d["instruct"]
    )
    return f'''<table class="tabla">
      <tr><th>Métrica</th>{"".join(f"<th>{e(t)}</th>" for _, t in cols)}</tr>
      {filas}
    </table>'''


def lamina(n: int, seccion: str, cuerpo: str, pie: str, clase: str = "") -> str:
    return f'''<section class="lamina{clase}">
  <header class="membrete"><span class="cod">ABISERVI · {n:02d}</span><span class="sec">{e(seccion)}</span></header>
  <div class="cuerpo">
{cuerpo}
  </div>
  <footer class="pie"><span>{e(pie)}</span><span class="num">{n:02d} / {TOTAL}</span></footer>
</section>'''


def construir() -> str:
    # EL LOGO NO VA EN PORTADA. Su isotipo -- un hexagono con nodos unidos por
    # lineas -- se lee como un icono de "inteligencia artificial", y en la
    # primera lamina transmite justo lo contrario de lo que este trabajo
    # defiende: que la tecnica esta al servicio de un sistema de gestion, no al
    # reves. Aparece donde de verdad importa, dentro de las piezas generadas,
    # que es ademas donde demuestra que la marca se compone y no se dibuja.
    piezas = [p for p in sorted((DOCS / "imagenes").glob("*.png"))][:3]
    galeria = "".join(
        f'<figure class="figura"><img src="{uri(p)}" alt="{e(p.stem)}"></figure>'
        for p in piezas if uri(p)
    )

    L = []

    # 01 --------------------------------------------------------------------
    L.append(f'''<section class="lamina viva">
  <header class="membrete"><span class="cod">ABISERVI · 01</span><span class="sec">Portada</span></header>
  <div class="cuerpo portada">
    <p class="entradilla">ABISERVI · Sistema de Gestión de la Calidad</p>
    <h1 class="titular">Un modelo propio para la documentación de la casa</h1>
    <p class="bajada">Afinar un modelo de lenguaje sobre los procedimientos de ABISERVI y
    producir el material de concienciación que la norma exige. Sin que un solo documento
    salga de la organización.</p>
    <dl class="ficha">
      <div><dt>Autor</dt><dd>Klauz A. España Irrazabal</dd></div>
      <div><dt>Técnica</dt><dd>QLoRA · 4 bits</dd></div>
      <div><dt>Dataset</dt><dd>584 ejemplos</dd></div>
      <div><dt>Imagen</dt><dd>SDXL · Diffusers</dd></div>
    </dl>
  </div>
  <footer class="pie"><span>Portada</span><span class="num">01 / {TOTAL}</span></footer>
</section>''')

    # 02 impacto empresarial -------------------------------------------------
    L.append(lamina(2, "Impacto empresarial", '''    <p class="entradilla">El problema de negocio</p>
    <h2 class="titular">Consultar un procedimiento cuesta más que cumplirlo</h2>
    <p class="bajada">ABISERVI opera con 132 documentos controlados y 1043 cláusulas. Cada
    consulta —«¿qué plazo tengo?», «¿qué formato uso?»— interrumpe a alguien de Calidad.</p>
    <div class="rejilla r3">
      <div class="tarjeta"><span class="cifra">132</span><h3>documentos controlados</h3>
        <p>En 11 áreas. La respuesta casi nunca es «el documento»: es una cláusula concreta.</p></div>
      <div class="tarjeta"><span class="cifra">v1 ≠ v2</span><h3>el riesgo real</h3>
        <p>Actuar sobre una versión derogada es una no conformidad en auditoría.</p></div>
      <div class="tarjeta"><span class="cifra">§7.2–7.4</span><h3>lo que la norma exige</h3>
        <p>Competencia, toma de conciencia y comunicación. Material que hoy se encarga fuera.</p></div>
    </div>
    <p class="bajada">Dos necesidades distintas: <b>responder</b> sobre la documentación y
    <b>comunicarla</b>. Esta fase ataca las dos con modelos propios.</p>''',
    "El caso de negocio"))

    # 03 arquitectura --------------------------------------------------------
    L.append(lamina(3, "Arquitectura técnica", '''    <p class="entradilla">Cómo está hecho</p>
    <h2 class="titular">Dos modelos, una misma máquina</h2>
    <table class="tabla">
      <tr><th>Pieza</th><th>Elección</th><th>Por qué</th></tr>
      <tr><td class="n">Modelo de lenguaje</td><td>Qwen3-4B, QLoRA 4 bits</td>
          <td>Base congelada y cuantizada; solo se entrena el <b>0,37 %</b> de los parámetros.
          El adaptador ocupa megabytes.</td></tr>
      <tr><td class="n">Entrenamiento</td><td>Unsloth · LoRA rango 16</td>
          <td>3 épocas sobre 584 ejemplos. Cabe en una GPU de gama de entrada.</td></tr>
      <tr><td class="n">Generación de imagen</td><td>SDXL vía Diffusers</td>
          <td>El modelo dibuja la escena; el logotipo real se <b>compone encima</b>.</td></tr>
      <tr><td class="n">Infraestructura</td><td>Propia, sin nube</td>
          <td>Los procedimientos internos no salen de la organización. Es un requisito,
          no una preferencia.</td></tr>
    </table>
    <p class="bajada">El modelo de texto decide <b>qué</b> material hace falta y con qué
    cláusula se respalda; el de difusión solo dibuja. Uno alimenta al otro.</p>''',
    "Stack y decisiones"))

    # 04 el dataset ----------------------------------------------------------
    L.append(lamina(4, "Datos", '''    <p class="entradilla">El dataset</p>
    <h2 class="titular">584 ejemplos, ninguno inventado para la ocasión</h2>
    <p class="bajada">Se ensambló de lo que la organización ya tenía verificado, no de un
    corpus fabricado para entrenar.</p>
    <div class="rejilla r3">
      <div class="tarjeta"><span class="cifra">730</span><h3>respuestas verificadas</h3>
        <p>Cada una comprobada contra el texto de su cláusula, con reglas fijas.</p></div>
      <div class="tarjeta"><span class="cifra">14</span><h3>rechazos documentados</h3>
        <p>Con su motivo. Son los ejemplos negativos, y casi nadie los tiene.</p></div>
      <div class="tarjeta"><span class="cifra">7</span><h3>esquemas de herramientas</h3>
        <p>Registrar hallazgos, consultar acciones, derivar a Calidad.</p></div>
    </div>
    <p class="bajada">El reparto en entrenamiento, validación y prueba se hizo <b>por cláusula
    de origen</b>, no al azar: dos preguntas de la misma cláusula comparten casi todo el
    prompt, y separarlas convertiría la validación en una medida de memorización.</p>''',
    "Trazable hasta su origen"))

    # 05 resultados cuantitativos -------------------------------------------
    L.append(lamina(5, "Resultados · cuantitativos", f'''    <p class="entradilla">El banco completo</p>
    <h2 class="titular">Cita el documento correcto el 100 % de las veces</h2>
    {tabla_banco()}
    <p class="bajada">Doce consultas reales sobre el sistema de gestión, evaluadas con el modelo
    <b>en las mismas condiciones en que se entrenó</b>: con la documentación recuperada
    delante, que es como funciona en producción.</p>
    <p class="bajada">La cita correcta sube de <b>88 % a 100 %</b> y la fundamentación también.
    El afinado responde además un <b>83 % más corto</b>: menos tokens es menos latencia
    y menos coste por consulta.</p>''',
    "12 consultas · mismo corpus · mismo día"))

    # 06 los tres estados ---------------------------------------------------
    L.append(lamina(6, "Resultados · los tres estados", f'''    <p class="entradilla">El eje de la comparación</p>
    <h2 class="titular">Sin entrenar · entrenado · afinado</h2>
    {tabla_tres_modelos()}
    <p class="bajada">Las mismas 12 preguntas, <b>sin documentación delante</b>, para ver qué
    aporta cada nivel de entrenamiento por sí mismo. La atribución correcta progresa de
    <b>0 % a 38 % y a 62 %</b>.</p>
    <p class="bajada">«¿Qué hago si detecto una no conformidad?» — sin entrenar divaga sobre
    generalidades; entrenado conversa bien pero nombra el documento equivocado; <b>afinado
    responde citando el procedimiento de No Conformidad y Acciones Correctivas</b>, que es el
    que toca. Y en 160 caracteres en vez de 1014.</p>''',
    "Mismas preguntas · tres modelos"))

    # 07 imagenes ------------------------------------------------------------
    L.append(lamina(7, "Generación de imágenes", f'''    <p class="entradilla">La segunda modalidad</p>
    <h2 class="titular">Material que la propia norma exige</h2>
    <p class="bajada">ISO 9001 pide formar y comunicar —<b>§7.2, §7.3 y §7.4</b>—. El material
    de concienciación no es decoración: da servicio a tres cláusulas del estándar.</p>
    <div class="rejilla r3">{galeria}</div>
    <p class="bajada">El modelo genera el arte; <b>el logotipo real se compone encima</b>. Pedirle
    a un modelo de difusión que dibuje una marca produce un logotipo parecido y falso, y una
    marca mal dibujada en material de calidad es peor que no tener material.</p>''',
    "Generadas en infraestructura propia"))

    # 08 guardrails ----------------------------------------------------------
    L.append(lamina(8, "Control", '''    <p class="entradilla">Lo que no hace</p>
    <h2 class="titular">Un generador sin límites es un riesgo, no una función</h2>
    <table class="tabla">
      <tr><th>Control</th><th>Qué impide</th></tr>
      <tr><td class="n">Respaldo documental obligatorio</td>
          <td>Toda pieza cita una cláusula, y <b>se comprueba contra la base</b>. No se cree
          lo que envía el cliente: una pieza que cita un documento inexistente es el peor
          fallo posible en cumplimiento.</td></tr>
      <tr><td class="n">Alcance cerrado al sistema</td>
          <td>Lista blanca además de negra. «Una invitación de cumpleaños» se rechaza por las
          palabras prohibidas; «un bol de fruta» porque no tiene <b>ninguna</b> del vocabulario
          del sistema. Con solo una lista negra, cualquier tema nuevo se cuela.</td></tr>
      <tr><td class="n">No se promete lo que no se hace</td>
          <td>Si el modelo anuncia un cartel que nadie está generando, la respuesta se
          sustituye. Prometer y no cumplir es peor que declararse incapaz.</td></tr>
    </table>''',
    "Los tres guardrails, probados"))

    # 09 ROI -----------------------------------------------------------------
    L.append(lamina(9, "Retorno", '''    <p class="entradilla">Impacto estimado</p>
    <h2 class="titular">Dónde se recupera la inversión</h2>
    <table class="tabla">
      <tr><th>Concepto</th><th>Antes</th><th>Con el modelo propio</th></tr>
      <tr><td>Consulta a un procedimiento</td><td class="n">interrumpe a Calidad</td>
          <td class="n">autoservicio</td></tr>
      <tr><td>Longitud de respuesta</td><td class="n">895 car.</td>
          <td class="n">150 car. · <b>−83 %</b></td></tr>
      <tr><td>Instrucciones por llamada</td><td class="n">~1.400 tokens</td>
          <td class="n">prescindibles</td></tr>
      <tr><td>Material de concienciación</td><td class="n">encargo externo</td>
          <td class="n">minutos, en casa</td></tr>
      <tr><td>Coste por consulta</td><td class="n">API de terceros</td>
          <td class="n">0 · infraestructura propia</td></tr>
    </table>
    <p class="bajada">La inversión es el entrenamiento: <b>una GPU durante horas</b>, no meses de
    desarrollo. El adaptador ocupa megabytes y se reentrena cuando cambia la documentación.</p>
    <p class="bajada">Y el retorno que no se mide en horas: <b>ningún documento sale de la
    organización</b>. Para un sistema de gestión certificado, eso no es un ahorro — es la
    condición para que el proyecto pueda existir.</p>''',
    "ROI e impacto"))

    # 10 cierre --------------------------------------------------------------
    L.append(lamina(10, "Cierre", '''    <p class="entradilla">Honestidad</p>
    <h2 class="titular">Lo que está demostrado, y lo que no</h2>
    <div class="panel-par">
      <div class="columna">
        <span class="rotulo si">Demostrado</span>
        <ul class="puntos">
          <li>Con documentación delante, la <b>cita correcta sube de 88 % a 100 %</b> y la
          respuesta es un <b>83 % más corta</b>.</li>
          <li>Sin documentación, la atribución correcta progresa <b>0 % → 38 % → 62 %</b>:
          el modelo internaliza contenido, no solo forma.</li>
          <li>La generación de imágenes funciona <b>en infraestructura propia</b>, con la marca
          real y respaldo documental.</li>
          <li>Todo es <b>reproducible</b>: un notebook y un comando.</li>
        </ul>
      </div>
      <div class="columna">
        <span class="rotulo no">No demostrado</span>
        <ul class="puntos neg">
          <li>Hicieron falta <b>dos datasets</b>: uno enseña formato y se evalúa con
          recuperación; el otro memoriza y se evalúa sin ella. Medir uno en el escenario del
          otro no significa nada, y costó dos tablas tiradas descubrirlo.</li>
          <li>El afinado aún <b>repite la atribución</b> al final de la respuesta. Se corrige
          con más épocas o penalizando la repetición.</li>
          <li>El conjunto de evaluación es <b>pequeño</b>: sirve para ver un patrón, no para
          afirmar un porcentaje con intervalo de confianza.</li>
          <li>El ROI son <b>estimaciones</b>, no medidas de producción.</li>
        </ul>
      </div>
    </div>
    <p class="bajada">Saber dónde está el límite de una técnica vale más que afirmar que no lo
    tiene.</p>''',
    "Límites declarados"))

    fuente = ORIGEN.read_text(encoding="utf-8")
    estilo = re.search(r"<style>(.*?)</style>", fuente, re.S).group(1)
    guion = re.search(r"<script>(.*?)</script>", fuente, re.S).group(1)

    return f'''<!doctype html>
<html lang="es" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>ABISERVI · Modelo propio para el sistema de gestión</title>
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
