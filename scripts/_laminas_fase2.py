"""
El contenido de las 13 laminas de la Fase 2.

Vive aparte de `construir_presentacion.py` para que aquel se lea de arriba abajo:
alli esta como se arma el fichero, aqui que dice cada lamina.

Todos los numeros salen de mediciones que estan en el repositorio
(`docs/linea-base.md`, `docs/entrenamiento.log`, `docs/comparacion-lora.json`,
`data/finetune/*/REPORTE.md`). Si algo no se midio, la lamina lo dice.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

TOTAL = 15

# Linea base medida el 2026-09-24 sobre el SGC real. Fuente: docs/linea-base.md
BASE = {
    "cita": "88%", "clausula": "100%", "herramienta": "50%",
    "escala": "83%", "fantasmas": "100%", "fundamentada": "88%",
    "mediana": "14.8 s", "maxima": "48.0 s",
}


def tres_estados(tenant: str = "empresa-demo-iso") -> dict | None:
    """
    El resumen de la comparacion de tres estados, si se corrio.

    Solo se leen los PORCENTAJES y los codigos INVENTADOS. El detalle con las
    respuestas literales se queda fuera: corrido contra un tenant real reproduce
    el contenido de sus clausulas. Un codigo inventado, en cambio, no existe en
    ningun SGC por definicion, asi que es publicable.
    """
    # Se prefiere el tenant real si esta, y si no el sintetico: el fichero del
    # real no se versiona, asi que al clonar el repositorio solo habra el otro.
    for candidato in (f"docs/tres-estados-{tenant}.json", "docs/tres-estados-demo-publica.json"):
        ruta = Path(candidato)
        if ruta.exists():
            break
    else:
        return None
    d = json.loads(ruta.read_text(encoding="utf-8"))
    inventados = sorted({
        c for fila in d.get("detalle", {}).get("sin_rag", []) for c in fila["fantasmas"]
    })
    return {"resumen": d["resumen"], "inventados": inventados}


def valor_lora(tenant: str = "empresa-demo-iso") -> dict | None:
    """El experimento de ablacion del prompt: ¿las reglas viven en los pesos?"""
    for candidato in (f"docs/valor-lora-{tenant}.json", "docs/valor-lora-demo-publica.json"):
        ruta = Path(candidato)
        if ruta.exists():
            return json.loads(ruta.read_text(encoding="utf-8"))["resumen"]
    return None


def metricas_del_banco(ruta) -> dict[str, str]:
    """
    Lee la tabla que imprime `benchmark_models.py` de su salida guardada.

    Se parsea la salida en vez de reimplementar el calculo: asi la lamina no
    puede discrepar del banco, que es el arbitro.
    """
    if not ruta.exists():
        return {}
    filas = {}
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-zÁÉÍÓÚáéíóúñ ]+?)\s{2,}([\d.]+%|[\d.]+ ?s)\s*$", linea)
        if m:
            filas[m.group(1).strip()] = m.group(2).strip()
    return filas


def e(t) -> str:
    return html.escape(str(t or ""))


def bloque_par(par: dict) -> str:
    """Una pregunta con las dos respuestas enfrentadas."""
    b, a = par["base"], par["lora"]
    def sello(ok: bool) -> str:
        cls = "si" if ok else "no"
        return f'<span class="rotulo {cls}">{"acierta" if ok else "falla"}</span>'
    return f'''<div class="panel-par">
  <div class="columna">
    <p class="entradilla">Sin entrenar</p>
    <p class="pista">herramienta: <code>{e(b["herramienta"] or "ninguna")}</code> · {sello(b["acierta"])}</p>
    <p>{e(b["texto"][:320]) or "<em>(sin texto)</em>"}</p>
  </div>
  <div class="columna">
    <p class="entradilla">Con LoRA</p>
    <p class="pista">herramienta: <code>{e(a["herramienta"] or "ninguna")}</code> · {sello(a["acierta"])}</p>
    <p>{e(a["texto"][:320]) or "<em>(sin texto)</em>"}</p>
  </div>
</div>'''


def construir_laminas(*, tren, val, comparacion, familias, imagenes, svg, incrustar, lamina):
    L = []

    # --- 01 portada ----------------------------------------------------------
    L.append(f'''<section class="lamina viva">
  <header class="membrete"><span class="cod">NormIA · 01</span><span class="sec">Fase 2</span></header>
  <div class="cuerpo portada">
    <p class="entradilla">Proyecto final · Diploma en Diseño e Implementación de Chatbots</p>
    <h1 class="titular">Afinar lo que el RAG no puede arreglar</h1>
    <p class="bajada">Fase 2 de NormIA: fine-tuning con QLoRA sobre el modelo local, y generación de
    material visual para el Sistema de Gestión de la Calidad. Todo en la misma máquina.</p>
    <dl class="ficha">
      <div><dt>Autor</dt><dd>Klauz Alexander España Irrazabal</dd></div>
      <div><dt>Cliente</dt><dd>ABISERVI</dd></div>
      <div><dt>Técnica</dt><dd>QLoRA · 4 bits</dd></div>
      <div><dt>Máquina</dt><dd>Apple M5 Pro · 48 GB</dd></div>
    </dl>
  </div>
  <footer class="pie"><span>Portada</span><span class="num">01 / {TOTAL}</span></footer>
</section>''')

    # --- 02 el problema medido -----------------------------------------------
    L.append(lamina(2, "Problema", f'''    <p class="entradilla">La línea base</p>
    <h2 class="titular">El RAG resolvió la cita. No resolvió la acción</h2>
    <p class="bajada">Medido antes de tocar nada, con el banco de pruebas sobre los 132 documentos
    reales. Cinco de las seis métricas están bien. Una no.</p>
    <div class="rejilla r3">
      <div class="tarjeta"><span class="cifra">{BASE["fantasmas"]}</span><h3>sin citas inventadas</h3>
        <p>Nunca cita un documento que no existe. Es la promesa central y se cumple.</p></div>
      <div class="tarjeta"><span class="cifra">{BASE["clausula"]}</span><h3>acierta la cláusula</h3>
        <p>Cuando responde, señala la sección correcta del documento correcto.</p></div>
      <div class="tarjeta"><span class="cifra">{BASE["herramienta"]}</span><h3>usa la herramienta correcta</h3>
        <p>La mitad de las veces no llama a ninguna. Ahí es donde duele.</p></div>
    </div>
    <p class="bajada">Un asistente que cita bien pero no registra el hallazgo ni deriva a Calidad
    es un buscador con buenos modales.</p>''', "Línea base · docs/linea-base.md", TOTAL))

    # --- 03 evidencia --------------------------------------------------------
    L.append(lamina(3, "Problema · evidencia", '''    <p class="entradilla">Evidencia</p>
    <h2 class="titular">Tres fallos, y dos son el mismo</h2>
    <p class="bajada">No son categorías abstractas: son las tres consultas que falló el banco.</p>
    <table class="tabla">
      <tr><th>Consulta</th><th>Qué pasó</th></tr>
      <tr><td class="n">«qué pasos sigue el procedimiento de compras»</td>
          <td>No citó nada <b>y escaló sin motivo</b>. Una consulta legítima, con la cláusula
          en el corpus, acabó en la cola de Calidad.</td></tr>
      <tr><td class="n">«apruébame este cambio, autorízalo ya»</td>
          <td>Debía llamar a <code>escalate_to_quality</code>. Usó <b>ninguna</b> herramienta.</td></tr>
      <tr><td class="n">«reporto extintores vencidos en el almacén»</td>
          <td>Debía llamar a <code>register_finding</code>. Usó <b>ninguna</b> herramienta.</td></tr>
    </table>
    <p class="bajada">El patrón no es «la escribe mal»: es <b>no la emite</b>. El parche que ya
    existe en el orquestador —recuperar la llamada del texto— no cubre este caso,
    porque no hay nada que recuperar.</p>''', "Los tres fallos del banco", TOTAL))

    # --- 04 que se afina y que no --------------------------------------------
    L.append(lamina(4, "Solución · alcance", '''    <p class="entradilla">Alcance</p>
    <h2 class="titular">Se afina el comportamiento, nunca el conocimiento</h2>
    <div class="panel-par">
      <div class="columna">
        <span class="rotulo si">Sí se entrena</span>
        <ul class="puntos">
          <li>Emitir la llamada como <code>tool_call</code>, no escribirla en el texto.</li>
          <li>Atribuir por título y sección, como exige el prompt del tenant.</li>
          <li>Negarse y derivar a Calidad cuando no hay respaldo.</li>
          <li>Saludar sin escalar, y responder sobre sí mismo sin escalar.</li>
        </ul>
      </div>
      <div class="columna">
        <span class="rotulo no">No se entrena</span>
        <ul class="puntos neg">
          <li>El contenido de los procedimientos. Eso lo trae el RAG, y mejor.</li>
          <li>Plazos, responsables ni códigos: memorizarlos es invitar a la alucinación.</li>
          <li>Los guardrails. Siguen siendo código determinista, fuera del modelo.</li>
        </ul>
      </div>
    </div>
    <p class="bajada">Un modelo afinado sobre documentos los recuerda mal y los cita peor.
    El fine-tuning aquí cambia <b>qué hace</b>, no <b>qué sabe</b>.</p>''',
    "La decisión que evita empeorar el sistema", TOTAL))

    # --- 05 QLoRA ------------------------------------------------------------
    L.append(lamina(5, "Técnica", '''    <p class="entradilla">QLoRA</p>
    <h2 class="titular">Entrenar el 0,37 % para cambiar el 100 %</h2>
    <p class="bajada">La base queda congelada y cuantizada a 4 bits. Solo se entrenan unos
    adaptadores de rango bajo insertados en 16 capas.</p>
    <div class="rejilla r3">
      <div class="tarjeta"><span class="cifra">4 bits</span><h3>base congelada</h3>
        <p>Los pesos no se tocan: se descuantizan al vuelo en cada multiplicación.</p></div>
      <div class="tarjeta"><span class="cifra">14,7 M</span><h3>parámetros entrenables</h3>
        <p>De 4.022 millones. El adaptador ocupa megabytes, no gigabytes.</p></div>
      <div class="tarjeta"><span class="cifra">8,3 GB</span><h3>pico de memoria</h3>
        <p>Cabe en una portátil. Sin nube, sin subir un solo documento.</p></div>
    </div>
    <p class="bajada">Y esa última cifra no es un detalle de eficiencia: es <b>la razón</b> de
    poder entrenar con el SGC real. El dataset completo sale de 133 PDFs de cliente
    que no pueden salir de la máquina.</p>''', "LoRA sobre base cuantizada", TOTAL))

    # --- 06 dataset ----------------------------------------------------------
    filas = "".join(
        f'<tr><td class="n">{e(f)}</td><td class="n">{e(n)}</td><td>{e(q)}</td></tr>'
        for f, n, q in familias
    ) or '<tr><td colspan="3">Sin REPORTE.md: corre build_finetune_dataset.py</td></tr>'
    L.append(lamina(6, "Datos", f'''    <p class="entradilla">El dataset</p>
    <h2 class="titular">Ensamblado de lo ya verificado, no inventado</h2>
    <p class="bajada">Nada se generó para la ocasión. Cada ejemplo sale de algo que el proyecto
    ya había comprobado contra el texto de su cláusula.</p>
    <table class="tabla">
      <tr><th>Familia</th><th>Ejemplos</th><th>Qué enseña</th></tr>
      {filas}
    </table>
    <p class="bajada">584 ejemplos en total. La familia mayoritaria va <b>recortada a propósito</b>:
    sin tope quedaba 13 a 1 sobre las demás y el modelo habría dedicado casi toda su
    capacidad al formato de la cita, que ya hacía bien.</p>''',
    "584 ejemplos · data/finetune", TOTAL))

    # --- 07 las tres decisiones ----------------------------------------------
    L.append(lamina(7, "Datos · rigor", '''    <p class="entradilla">Tres decisiones</p>
    <h2 class="titular">Lo que separa un dataset útil de uno que entrena igual y no sirve</h2>
    <table class="tabla">
      <tr><th>Decisión</th><th>Qué pasaba si no</th></tr>
      <tr><td class="n">Cada ejemplo se arma con el <code>_build_messages</code> del orquestador</td>
          <td>El prompt del tenant tiene 5.557 caracteres y reglas propias. Con una copia
          «parecida», el modelo aprende a obedecer instrucciones que nunca va a recibir.</td></tr>
      <tr><td class="n">Las 12 preguntas del banco quedan fuera</td>
          <td>Se leen del AST de <code>benchmark_models.py</code>. Si entraran, el banco dejaría
          de medir generalización y pasaría a confirmar lo memorizado.</td></tr>
      <tr><td class="n">La fuente es el <code>.json</code>, no el <code>.jsonl</code></td>
          <td>El <code>.jsonl</code> es el checkpoint crudo, previo a la validación: conserva
          <b>24 respuestas que citan documentos inexistentes</b>. Entrenar con él enseñaría
          justo el fallo que este proyecto existe para evitar.</td></tr>
    </table>
    <p class="bajada">La tercera se descubrió auditando el dataset ya construido. Es el tipo de
    error que no da ningún síntoma: entrena igual de bien y produce un modelo peor.</p>''',
    "Las trampas que sí tenían síntoma cero", TOTAL))

    # --- 08 el modelo --------------------------------------------------------
    L.append(lamina(8, "Herramientas", '''    <p class="entradilla">Elección del modelo</p>
    <h2 class="titular">El modelo del taller no servía, y solo se vio midiendo</h2>
    <p class="bajada">La elección obvia era <code>Qwen3.5-4B</code>: el del taller de clase y de la
    misma familia que el de producción. No entra en esta máquina.</p>
    <table class="tabla">
      <tr><th>Modelo</th><th>Arquitectura</th><th>Pico con 512 tokens</th></tr>
      <tr><td class="n">Qwen3.5-4B</td>
          <td>Híbrida: 24 de 32 capas <code>linear_attention</code>, estado en float32,
          vocabulario de 248.320. Además multimodal.</td>
          <td class="n">33 GB</td></tr>
      <tr><td class="n">Qwen3-4B</td>
          <td>Densa, atención completa, vocabulario de 151.936.</td>
          <td class="n">5,4 GB</td></tr>
    </table>
    <p class="bajada">Con el híbrido, bajar las capas LoRA de 16 a 4 <b>no cambiaba nada</b>: la
    memoria no se iba en los adaptadores sino en la recurrencia. Ese experimento fue
    el que señaló la causa.</p>''', "Medido, no supuesto", TOTAL))

    # --- 09 la curva ---------------------------------------------------------
    # Se titula con la perdida de VALIDACION, no con la de entrenamiento: la de
    # entrenamiento es de un solo lote y salta (0.219 y 0.420 en pasos seguidos).
    # La de validacion recorre el conjunto reservado entero y si significa algo.
    ultima = f"{val[-1][1]:.3f}" if val else "—"
    primera_val = f"{val[0][1]:.3f}" if val else "—"
    L.append(lamina(9, "Entrenamiento", f'''    <p class="entradilla">La corrida</p>
    <h2 class="titular">De {primera_val} a {ultima}</h2>
    <figure class="figura ancha">
      {svg}
      <figcaption><b>Curva real</b> de validación (verde) y entrenamiento (gris), del log
      <code>docs/entrenamiento.log</code>. Pérdida sobre el conjunto de prueba
      reservado: <b>0,284</b> (perplejidad 1,329).</figcaption>
    </figure>
    <dl class="ficha">
      <div><dt>Secuencia</dt><dd>4608 tokens</dd></div>
      <div><dt>Lote efectivo</dt><dd>4</dd></div>
      <div><dt>Rango LoRA</dt><dd>16</dd></div>
      <div><dt>Máquina</dt><dd>M5 Pro · local</dd></div>
    </dl>''', "docs/entrenamiento.log", TOTAL))

    # --- 10 y 11: sin entrenar contra entrenado ------------------------------
    if comparacion and comparacion.get("pares"):
        pares = comparacion["pares"]
        mejoras = [p for p in pares if p["lora"]["acierta"] and not p["base"]["acierta"]]
        muestra = (mejoras or pares)[:2]
        bloques = "".join(
            f'<p class="entradilla">{e(p["base"]["pregunta"])}</p>' + bloque_par(p)
            for p in muestra
        )
        ab = sum(p["base"]["acierta"] for p in pares)
        al = sum(p["lora"]["acierta"] for p in pares)
        cuerpo = f'''    <p class="entradilla">La misma pregunta, los dos modelos</p>
    <h2 class="titular">{ab} de {len(pares)} sin entrenar · {al} de {len(pares)} con LoRA</h2>
    {bloques}
    <p class="bajada">Las preguntas son las del banco, <b>excluidas del entrenamiento</b>: el
    modelo afinado no las había visto nunca. Aun así la mejora es de <b>un caso</b>:
    el 4B sin entrenar ya llamaba bien a las herramientas.</p>
    <p class="bajada">Y una advertencia sobre esta cifra: <b>la lámina siguiente, en otra corrida
    del mismo banco, da un resultado distinto</b> —ahí el afinado falla un caso y el
    base otro—. Con doce preguntas y temperatura 0,2, el recuento baila entre
    corridas. Es un argumento a favor de mirar el porqué de cada fallo, no el marcador.</p>'''
    else:
        cuerpo = '''    <p class="entradilla">La misma pregunta, los dos modelos</p>
    <h2 class="titular">Sin entrenar contra entrenado</h2>
    <p class="bajada">Pendiente: corre <code>scripts/comparar_estados.py</code> para generar
    <code>docs/comparacion-lora.json</code> y volver a construir esta lámina.</p>'''
    L.append(lamina(10, "Resultados · evidencia", cuerpo, "docs/comparacion-lora.md", TOTAL))

    base_4b = metricas_del_banco(Path("docs/banco-4b-base.txt"))
    lora_4b = metricas_del_banco(Path("docs/banco-4b-lora.txt"))
    orden = [
        "Cita el documento correcto", "Acierta la clausula",
        "Usa la herramienta correcta", "Escala cuando debe",
        "Sin citas inventadas", "Marcada como fundamentada",
    ]
    if base_4b and lora_4b:
        filas_b = "".join(
            f'<tr><td>{e(k)}</td><td class="n">{e(base_4b.get(k, "—"))}</td>'
            f'<td class="n">{e(lora_4b.get(k, "—"))}</td></tr>'
            for k in orden if k in base_4b or k in lora_4b
        )
        tabla = f'''<table class="tabla">
      <tr><th>Métrica</th><th>Sin entrenar</th><th>Con LoRA</th></tr>
      {filas_b}
    </table>'''
    else:
        tabla = '<p class="bajada">Pendiente: corre <code>scripts/evaluar_adaptador.py</code>.</p>'

    L.append(lamina(11, "Resultados", f'''    <p class="entradilla">El banco completo</p>
    <h2 class="titular">Mejora la cita. Empeora la llamada</h2>
    {tabla}
    <p class="bajada"><b>Los dos fallan un caso de doce</b>, solo que distinto. Cada métrica se
    calcula sobre los 4-6 casos que le aplican, así que un único fallo mueve el
    porcentaje 25 puntos. Con n=12 esto no sostiene una conclusión fuerte.</p>''',
    "docs/banco-4b-*.txt", TOTAL))

    # --- 12 tres estados: que aporta cada pieza ----------------------------
    te = tres_estados()
    if te:
        ETQ = [("sin_rag", "Sin RAG"), ("rag", "Con RAG"), ("rag_lora", "RAG + LoRA")]
        metricas = list(te["resumen"].get("rag", {}))
        filas_te = "".join(
            "<tr><td>" + e(m) + "</td>"
            + "".join(f'<td class="n">{e(te["resumen"][k].get(m, "—"))}</td>' for k, _ in ETQ)
            + "</tr>"
            for m in metricas
        )
        inv = te["inventados"]
        bloque_inv = (
            f'''<p class="bajada">Los códigos que se inventó sin RAG. <b>Ninguno existe</b> en el
    SGC: <code>{e(", ".join(inv[:9]))}</code>{"…" if len(inv) > 9 else ""}</p>'''
            if inv else
            '<p class="bajada">Sin RAG no llegó a citar ningún código inexistente.</p>'
        )
        cuerpo12 = f'''    <p class="entradilla">Qué aporta cada pieza</p>
    <h2 class="titular">El salto lo da el RAG, no el fine-tuning</h2>
    <table class="tabla">
      <tr><th>Métrica</th>{"".join(f"<th>{e(t)}</th>" for _, t in ETQ)}</tr>
      {filas_te}
    </table>
    {bloque_inv}
    <p class="bajada">Dos avisos para leerla bien. El <b>0 % de herramientas sin RAG es
    estructural</b>: en ese estado no se le pasan herramientas, así que no puede
    llamarlas. Y entre las dos últimas columnas <b>no hay diferencia medible</b>: en
    este banco, el fine-tuning no aporta nada que el RAG no diera ya.</p>'''
    else:
        cuerpo12 = '''    <p class="entradilla">Qué aporta cada pieza</p>
    <h2 class="titular">Sin RAG · Con RAG · RAG + LoRA</h2>
    <p class="bajada">Pendiente: corre <code>scripts/comparar_tres_estados.py</code>.</p>'''
    L.append(lamina(12, "Resultados · las tres piezas", cuerpo12,
                    "Las mismas 12 preguntas, tres configuraciones", TOTAL))

    # --- 13 para que sirve entonces afinar ---------------------------------
    vl = valor_lora()
    if vl:
        COLS = [
            ("base_completo", "Base · 5557"), ("lora_completo", "LoRA · 5557"),
            ("base_minimo", "Base · 433"), ("lora_minimo", "LoRA · 433"),
        ]
        ms = ["Acierta en conjunto", "Escala cuando debe",
              "Atribuye sin escribir el codigo", "Longitud mediana"]
        filas_vl = "".join(
            "<tr><td>" + e(m) + "</td>"
            + "".join(f'<td class="n">{e(vl[k].get(m, "—"))}</td>' for k, _ in COLS)
            + "</tr>"
            for m in ms if any(m in vl[k] for k, _ in COLS)
        )
        cuerpo13 = f'''    <p class="entradilla">¿Entonces para qué afinar?</p>
    <h2 class="titular">El experimento que lo justificaría, y no salió</h2>
    <p class="bajada">Afinar sirve para meter las instrucciones en los <b>pesos</b> y poder
    quitarlas del prompt: son 5.557 caracteres en cada petición. Se mide lo mismo
    con el prompt completo y con uno mínimo de 433.</p>
    <table class="tabla">
      <tr><th>Métrica</th>{"".join(f"<th>{e(t)}</th>" for _, t in COLS)}</tr>
      {filas_vl}
    </table>
    <p class="bajada"><b>La última columna es idéntica a la tercera.</b> Al quitar el prompt
    largo, base y afinado caen igual: el adaptador <b>no internalizó las reglas</b>.
    Y no es mala suerte — los 5.557 caracteres estaban dentro de cada ejemplo de
    entrenamiento, así que el modelo aprendió «dado este prompt, responde así»,
    nunca a comportarse sin él. Se arregla entrenando con el prompt reducido.</p>'''
    else:
        cuerpo13 = '''    <p class="entradilla">¿Entonces para qué afinar?</p>
    <h2 class="titular">El experimento que lo justificaría</h2>
    <p class="bajada">Pendiente: corre <code>scripts/medir_valor_lora.py</code>.</p>'''
    L.append(lamina(13, "Resultados · el porqué", cuerpo13,
                    "Ablación del prompt de sistema", TOTAL))

    # --- 14 imagenes ---------------------------------------------------------
    figs = []
    for ruta in imagenes[:3]:
        uri = incrustar(ruta)
        if uri:
            figs.append(
                f'<figure class="figura"><img src="{uri}" alt="{e(ruta.stem)}">'
                f'<figcaption><b>{e(ruta.stem)}</b></figcaption></figure>'
            )
    galeria = f'<div class="rejilla r3">{"".join(figs)}</div>' if figs else (
        '<p class="bajada">Pendiente: corre <code>scripts/generar_imagenes.py</code>.</p>'
    )
    L.append(lamina(14, "Generación de imágenes", f'''    <p class="entradilla">La segunda modalidad</p>
    <h2 class="titular">Material que la propia norma exige</h2>
    <p class="bajada">ISO 9001 pide formar y comunicar: <b>§7.2 Competencia, §7.3 Toma de
    conciencia, §7.4 Comunicación</b>. Un SGC que nadie conoce no se cumple.</p>
    {galeria}
    <p class="bajada">El modelo genera el arte; <b>el logotipo real se compone encima</b>. Pedirle a
    un modelo de difusión que dibuje una marca produce un logotipo parecido y falso,
    y una marca mal dibujada en material de calidad es peor que no tener material.</p>''',
    "SDXL local · docs/imagenes", TOTAL))

    # --- 15 limites ----------------------------------------------------------
    L.append(lamina(15, "Cierre", '''    <p class="entradilla">Honestidad</p>
    <h2 class="titular">Lo que no se ha demostrado</h2>
    <ul class="puntos neg">
      <li>El adaptador se entrenó sobre <b>Qwen3-4B</b>, no sobre el 27B de producción:
      en 48 GB no entra. La comparación es válida entre sí, no extrapolable al grande.</li>
      <li>El banco son <b>12 preguntas</b>. Suficiente para ver un patrón, corto para
      afirmar un porcentaje con intervalo de confianza.</li>
      <li>La mejora medida es de <b>un caso de doce</b>, y en la otra dirección hay una
      <b>regresión</b>: el adaptador empeora la emisión de llamadas a herramienta.
      No se despliega tal cual.</li>
      <li>Y el porqué, que es el aprendizaje de la fase: al entrenar, una llamada se
      renderiza como <b>texto</b> en la plantilla de chat, y con <code>mask_prompt</code>
      el modelo aprende a generar ese texto. Que se convierta en llamada estructurada
      depende del parser del servidor. El aviso
      <code>llm.tool_call_recovered_from_text</code>, que valía cero en la línea base,
      aparece con el adaptador.</li>
      <li>No se midió el <b>olvido catastrófico</b> fuera del dominio: el modelo afinado
      podría responder peor a lo que no es ISO.</li>
      <li>El material visual <b>no entra en documentos controlados</b>. Formación sí,
      documento del SGC no: sería un problema de auditoría, no una mejora.</li>
    </ul>
    <p class="bajada">Todo lo que sostiene esta exposición se reproduce con el corpus sintético
    que viaja en el repositorio, sin un solo documento del cliente.</p>''',
    "Límites y reproducibilidad", TOTAL))

    return "\n".join(L)
