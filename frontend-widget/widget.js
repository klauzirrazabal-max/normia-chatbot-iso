/**
 * NormIA - widget de chat embebible.
 *
 * Un solo archivo, sin build step ni dependencias. Se usa asi:
 *
 *   <script src="widget.js"></script>
 *   <normia-chat-widget api-url="http://localhost:8000" tenant-id="demo-publica">
 *   </normia-chat-widget>
 *
 * Todo el markup y el CSS viven dentro de un Shadow DOM: los estilos del sitio
 * anfitrion no pueden romper el widget, ni los del widget filtrarse al sitio.
 * Eso es lo que lo hace seguro de embeber en un WordPress o un React ajeno.
 */
(function () {
  "use strict";

  const STYLES = `
    :host {
      --n-accent: #2563eb;
      --n-accent-hover: #1d4ed8;
      --n-accent-soft: #eff6ff;
      --n-ok: #15803d;
      --n-bg: #ffffff;
      --n-bot-bubble: #f1f5f9;
      --n-text: #0f172a;
      --n-muted: #64748b;
      --n-border: #e2e8f0;
      --n-warning: #b45309;
      --n-warning-bg: #fffbeb;
      --n-radius: 12px;

      all: initial;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      position: fixed;
      right: 24px;
      bottom: 24px;
      z-index: 2147483000;
    }

    *, *::before, *::after { box-sizing: border-box; }

    /* Las reglas :host PIERDEN contra el documento anfitrion: un
       "* { font-family: X !important }" en el sitio del cliente alcanza al
       elemento <normia-chat-widget> (que vive en el light DOM) y todo lo de
       adentro hereda esa fuente. Por eso las propiedades heredables se fijan
       aqui, sobre elementos del shadow tree, donde el selector externo no llega. */
    .panel, .launcher {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                   "Helvetica Neue", Arial, sans-serif;
      font-size: 14px;
      font-weight: 400;
      font-style: normal;
      font-variant: normal;
      line-height: 1.5;
      letter-spacing: normal;
      word-spacing: normal;
      text-transform: none;
      text-align: left;
      text-indent: 0;
      color: var(--n-text);
    }

    button { font-family: inherit; font-size: inherit; cursor: pointer; }

    .launcher {
      width: 56px; height: 56px;
      border: none; border-radius: 50%;
      background: var(--n-accent); color: #fff;
      display: flex; align-items: center; justify-content: center;
      box-shadow: 0 4px 14px rgba(15, 23, 42, .25);
      transition: transform .15s ease, background .15s ease;
    }
    .launcher:hover { background: var(--n-accent-hover); transform: scale(1.05); }
    .launcher:focus-visible { outline: 3px solid var(--n-accent); outline-offset: 3px; }
    .launcher svg { width: 26px; height: 26px; fill: currentColor; }

    .panel {
      position: absolute; right: 0; bottom: 68px;
      width: 380px; max-width: calc(100vw - 32px);
      height: 560px; max-height: calc(100vh - 120px);
      background: var(--n-bg);
      border: 1px solid var(--n-border);
      border-radius: var(--n-radius);
      box-shadow: 0 12px 40px rgba(15, 23, 42, .18);
      display: none; flex-direction: column; overflow: hidden;
    }
    .panel[data-open="true"] { display: flex; }

    .header {
      padding: 14px 16px;
      border-bottom: 1px solid var(--n-border);
      display: flex; align-items: center; gap: 8px;
    }
    .dot {
      width: 8px; height: 8px; border-radius: 50%;
      background: #22c55e; flex: none;
    }
    .dot[data-status="offline"] { background: #ef4444; }
    .title { font-size: 14px; font-weight: 600; color: var(--n-text); }
    .subtitle { font-size: 12px; color: var(--n-muted); margin-left: auto; }

    .messages {
      flex: 1; overflow-y: auto; padding: 16px;
      display: flex; flex-direction: column; gap: 12px;
    }

    .row { display: flex; }
    .row[data-role="user"] { justify-content: flex-end; }

    .bubble {
      max-width: 82%;
      padding: 10px 13px;
      border-radius: var(--n-radius);
      font-size: 14px; line-height: 1.5;
      color: var(--n-text);
      word-wrap: break-word; overflow-wrap: anywhere;
    }
    .row[data-role="bot"] .bubble { background: var(--n-bot-bubble); border-bottom-left-radius: 4px; }
    .row[data-role="user"] .bubble {
      background: var(--n-accent); color: #fff; border-bottom-right-radius: 4px;
    }

    .bubble p { margin: 0 0 8px; }
    .bubble p:last-child { margin-bottom: 0; }
    .bubble ul { margin: 0 0 8px; padding-left: 18px; }
    .bubble ul:last-child { margin-bottom: 0; }
    .bubble li { margin-bottom: 3px; }
    .bubble strong { font-weight: 620; }

    /* Ficha de fuente: el titulo es lo que hace utilizable la referencia, y la
       fecha de vigencia es la garantia de cumplimiento -- no solo de donde salio
       la respuesta, sino que esa es la version en vigor. */
    .sources { margin-top: 10px; display: flex; flex-direction: column; gap: 6px; }
    .source {
      border-left: 2px solid var(--n-border);
      padding: 2px 0 2px 9px;
      font-size: 11.5px; line-height: 1.45;
    }
    .source .doc { color: var(--n-text); font-weight: 570; }
    .source .ref { color: var(--n-muted); }
    .source .vigente { color: var(--n-ok, #15803d); }
    .escalated, .unverified {
      margin-top: 8px; padding: 6px 9px;
      font-size: 11.5px; color: var(--n-warning);
      background: var(--n-warning-bg);
      border-left: 3px solid var(--n-warning); border-radius: 4px;
    }

    .suggestions {
      display: flex; flex-wrap: wrap; gap: 6px;
      margin-top: 10px; padding-left: 2px;
    }
    .chip {
      border: 1px solid var(--n-border); background: var(--n-bg);
      color: var(--n-accent); border-radius: 999px;
      padding: 5px 11px; font-size: 12.5px; line-height: 1.3;
      text-align: left; max-width: 100%;
      transition: background .12s, border-color .12s;
    }
    .chip:hover { background: var(--n-accent-soft, #eff6ff); border-color: var(--n-accent); }
    .chip:disabled { opacity: .5; cursor: not-allowed; }

    .typing { display: flex; gap: 4px; padding: 4px 2px; }
    .typing span {
      width: 6px; height: 6px; border-radius: 50%;
      background: var(--n-muted); animation: blink 1.3s infinite;
    }
    .typing span:nth-child(2) { animation-delay: .18s; }
    .typing span:nth-child(3) { animation-delay: .36s; }
    @keyframes blink { 0%, 60%, 100% { opacity: .25 } 30% { opacity: 1 } }

    .composer {
      border-top: 1px solid var(--n-border);
      padding: 10px; display: flex; gap: 8px; align-items: flex-end;
    }
    .composer textarea {
      flex: 1; resize: none;
      border: 1px solid var(--n-border); border-radius: 10px;
      padding: 9px 11px;
      font-family: inherit; font-size: 14px; line-height: 1.4; color: var(--n-text);
      max-height: 110px; min-height: 38px;
    }
    .composer textarea:focus { outline: none; border-color: var(--n-accent); }
    .send {
      width: 38px; height: 38px; flex: none;
      border: none; border-radius: 10px;
      background: var(--n-accent); color: #fff;
      display: flex; align-items: center; justify-content: center;
    }
    .send:hover:not(:disabled) { background: var(--n-accent-hover); }
    .send:disabled { opacity: .45; cursor: not-allowed; }
    .send svg { width: 17px; height: 17px; fill: currentColor; }

    @media (prefers-reduced-motion: reduce) {
      .launcher, .typing span { transition: none; animation: none; }
    }
  `;

  const ICON_CHAT =
    '<svg viewBox="0 0 24 24"><path d="M12 3C6.98 3 3 6.58 3 11c0 2.1.9 4 2.4 5.4L4.5 20l3.9-1.5c1.1.35 2.3.5 3.6.5 5.02 0 9-3.58 9-8s-3.98-8-9-8z"/></svg>';
  const ICON_CLOSE =
    '<svg viewBox="0 0 24 24"><path d="M19 6.4 17.6 5 12 10.6 6.4 5 5 6.4 10.6 12 5 17.6 6.4 19 12 13.4 17.6 19 19 17.6 13.4 12z"/></svg>';
  const ICON_SEND = '<svg viewBox="0 0 24 24"><path d="M2 21l21-9L2 3v7l15 2-15 2z"/></svg>';

  const GREETING =
    "Hola, soy NormIA. Respondo sobre los documentos ISO vigentes de la organizacion, " +
    "citando siempre el codigo y la version del documento. Que necesitas consultar?";

  class NormiaChatWidget extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      // El session_id vive solo en memoria de la pagina: sin localStorage,
      // sin cookies, sin nada que persista entre recargas.
      this.sessionId =
        (crypto.randomUUID && crypto.randomUUID()) ||
        "sess-" + Math.random().toString(36).slice(2);
      this.busy = false;
    }

    get apiUrl() {
      // Un api-url vacio significa "mismo origen": es lo que permite servir el
      // widget y la API detras del mismo tunel sin tocar nada.
      const attr = this.getAttribute("api-url");
      return (attr === null ? "http://localhost:8000" : attr).replace(/\/$/, "");
    }
    get tenantId() {
      return this.getAttribute("tenant-id") || "demo-publica";
    }

    connectedCallback() {
      this.render();
      this.checkHealth();
    }

    render() {
      const style = document.createElement("style");
      style.textContent = STYLES;

      const panel = document.createElement("div");
      panel.className = "panel";
      panel.setAttribute("data-open", "false");
      panel.innerHTML =
        '<div class="header">' +
        '<span class="dot" data-status="online"></span>' +
        '<span class="title">NormIA &middot; Calidad</span>' +
        '<span class="subtitle">Documentos vigentes</span>' +
        "</div>" +
        '<div class="messages" role="log" aria-live="polite"></div>' +
        '<form class="composer">' +
        '<textarea rows="1" placeholder="Escribe tu consulta..." aria-label="Mensaje"></textarea>' +
        '<button class="send" type="submit" aria-label="Enviar">' + ICON_SEND + "</button>" +
        "</form>";

      const launcher = document.createElement("button");
      launcher.className = "launcher";
      launcher.type = "button";
      launcher.setAttribute("aria-label", "Abrir chat de NormIA");
      launcher.innerHTML = ICON_CHAT;

      this.shadowRoot.append(style, panel, launcher);

      this.panel = panel;
      this.launcher = launcher;
      this.messagesEl = panel.querySelector(".messages");
      this.textarea = panel.querySelector("textarea");
      this.sendBtn = panel.querySelector(".send");
      this.statusDot = panel.querySelector(".dot");

      launcher.addEventListener("click", () => this.toggle());
      panel.querySelector(".composer").addEventListener("submit", (e) => {
        e.preventDefault();
        this.send();
      });

      this.textarea.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          this.send();
        }
      });
      this.textarea.addEventListener("input", () => {
        this.textarea.style.height = "auto";
        this.textarea.style.height = Math.min(this.textarea.scrollHeight, 110) + "px";
      });

      this.addBubble("bot", GREETING);
    }

    toggle() {
      const open = this.panel.getAttribute("data-open") === "true";
      this.panel.setAttribute("data-open", open ? "false" : "true");
      this.launcher.innerHTML = open ? ICON_CHAT : ICON_CLOSE;
      this.launcher.setAttribute("aria-label", open ? "Abrir chat de NormIA" : "Cerrar chat");
      if (!open) this.textarea.focus();
    }

    async checkHealth() {
      try {
        const res = await fetch(this.apiUrl + "/health");
        const data = await res.json();
        this.statusDot.setAttribute(
          "data-status",
          data.status === "ok" ? "online" : "offline"
        );
      } catch (_) {
        this.statusDot.setAttribute("data-status", "offline");
      }
    }

    /**
     * Markdown minimo: **negrita**, listas y saltos de parrafo.
     *
     * El modelo responde con listas y negritas, y pintarlas con textContent
     * dejaba los asteriscos a la vista. Se construye con createElement y
     * textContent -- nunca innerHTML -- para que nada de lo que venga del
     * backend pueda inyectar markup.
     */
    renderMarkdown(container, text) {
      const MARCA_ITEM = /^\s*(?:[-*\u2022]|\d+[.)])\s+/;
      const lineas = String(text || "")
        .split("\n")
        .map((l) => l.trim());

      // Se agrupa por TRAMOS, no por bloques: el modelo suele escribir una frase
      // introductoria y debajo la lista, todo junto. Exigir que el bloque entero
      // fuera lista dejaba los guiones a la vista.
      let lista = null;
      let parrafo = null;

      const cerrar = () => {
        if (lista) container.appendChild(lista);
        if (parrafo) container.appendChild(parrafo);
        lista = null;
        parrafo = null;
      };

      lineas.forEach((linea) => {
        if (!linea) {
          cerrar();
          return;
        }

        if (MARCA_ITEM.test(linea)) {
          if (parrafo) {
            container.appendChild(parrafo);
            parrafo = null;
          }
          if (!lista) lista = document.createElement("ul");
          const li = document.createElement("li");
          this.renderInline(li, linea.replace(MARCA_ITEM, ""));
          lista.appendChild(li);
          return;
        }

        if (lista) {
          container.appendChild(lista);
          lista = null;
        }
        if (!parrafo) {
          parrafo = document.createElement("p");
        } else {
          parrafo.appendChild(document.createElement("br"));
        }
        this.renderInline(parrafo, linea);
      });

      cerrar();
    }

    /** Negrita dentro de una linea. El resto se inserta como texto plano. */
    renderInline(parent, texto) {
      const partes = String(texto).split(/\*\*([^*]+)\*\*/g);
      partes.forEach((parte, i) => {
        if (!parte) return;
        if (i % 2 === 1) {
          const strong = document.createElement("strong");
          strong.textContent = parte;
          parent.appendChild(strong);
        } else {
          parent.appendChild(document.createTextNode(parte));
        }
      });
    }

    /**
     * Ficha de fuente.
     *
     * Antes decia "Fuente: STI-PR-01 v4, seccion 7.6" y eso no le dice nada a
     * quien pregunta. El TITULO hace utilizable la referencia; la FECHA DE
     * VIGENCIA es la garantia real de cumplimiento, porque responde la pregunta
     * que de verdad importa en una auditoria: no solo de donde salio el dato,
     * sino si esa es la version en vigor.
     *
     * Los datos los arma el servidor desde la base, no el modelo, asi que son
     * correctos aunque el modelo se equivoque al redactar.
     */
    renderSources(citations) {
      const box = document.createElement("div");
      box.className = "sources";

      citations.forEach((c) => {
        const item = document.createElement("div");
        item.className = "source";

        const doc = document.createElement("div");
        doc.className = "doc";
        doc.textContent =
          (c.title || c.code) + (c.section ? " · sección " + c.section : "");
        item.appendChild(doc);

        const ref = document.createElement("div");
        ref.className = "ref";
        ref.textContent = c.code + " " + c.version;
        if (c.effective_date) {
          const vig = document.createElement("span");
          vig.className = "vigente";
          vig.textContent = " · vigente desde " + this.formatDate(c.effective_date);
          ref.appendChild(vig);
        }
        item.appendChild(ref);

        box.appendChild(item);
      });

      return box;
    }

    /** ISO a formato local, sin arrastrar una libreria de fechas. */
    formatDate(iso) {
      const partes = String(iso).slice(0, 10).split("-");
      return partes.length === 3 ? `${partes[2]}/${partes[1]}/${partes[0]}` : iso;
    }

    addBubble(role, text) {
      const row = document.createElement("div");
      row.className = "row";
      row.setAttribute("data-role", role);

      const bubble = document.createElement("div");
      bubble.className = "bubble";
      if (role === "bot") {
        this.renderMarkdown(bubble, text);
      } else {
        bubble.textContent = text;
      }

      row.appendChild(bubble);
      this.messagesEl.appendChild(row);
      this.scrollToBottom();
      return bubble;
    }

    showTyping() {
      const row = document.createElement("div");
      row.className = "row";
      row.setAttribute("data-role", "bot");
      row.innerHTML = '<div class="bubble"><div class="typing"><span></span><span></span><span></span></div></div>';
      this.messagesEl.appendChild(row);
      this.scrollToBottom();
      return row;
    }

    scrollToBottom() {
      this.messagesEl.scrollTop = this.messagesEl.scrollHeight;
    }

    /**
     * Opciones de seguimiento como botones.
     *
     * Un "resumeme este procedimiento" devolvia las 20 clausulas de golpe y el
     * usuario tenia que leerlo todo para encontrar lo que buscaba. Ahora recibe
     * un resumen breve y elige por donde seguir con un clic.
     *
     * Solo se muestran las del ultimo turno: dejar las viejas en pantalla invita
     * a pulsar una opcion que ya no corresponde a la conversacion.
     */
    renderSuggestions(suggestions) {
      const previas = this.messagesEl.querySelector(".suggestions");
      if (previas) previas.remove();
      if (!suggestions || !suggestions.length) return;

      const box = document.createElement("div");
      box.className = "suggestions";

      suggestions.forEach((s) => {
        const chip = document.createElement("button");
        chip.className = "chip";
        chip.type = "button";
        chip.textContent = s.label;
        chip.addEventListener("click", () => {
          box.remove();
          this.textarea.value = s.message;
          this.send();
        });
        box.appendChild(chip);
      });

      this.messagesEl.appendChild(box);
      this.scrollToBottom();
    }

    setBusy(busy) {
      this.busy = busy;
      this.sendBtn.disabled = busy;
      this.textarea.disabled = busy;
    }

    async send() {
      const text = this.textarea.value.trim();
      if (!text || this.busy) return;

      const chipsPrevios = this.messagesEl.querySelector(".suggestions");
      if (chipsPrevios) chipsPrevios.remove();

      this.addBubble("user", text);
      this.textarea.value = "";
      this.textarea.style.height = "auto";
      this.setBusy(true);

      const typing = this.showTyping();

      try {
        const res = await fetch(this.apiUrl + "/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            tenant_id: this.tenantId,
            session_id: this.sessionId,
            message: text,
          }),
        });

        typing.remove();

        if (!res.ok) {
          this.addBubble("bot", "Hubo un problema al procesar tu consulta. Intenta de nuevo.");
          this.statusDot.setAttribute("data-status", "offline");
          return;
        }

        const data = await res.json();
        const bubble = this.addBubble("bot", data.reply);
        this.statusDot.setAttribute("data-status", "online");

        if (data.citations && data.citations.length) {
          bubble.appendChild(this.renderSources(data.citations));
        }

        if (data.escalate) {
          const note = document.createElement("div");
          note.className = "escalated";
          note.textContent = "Esta consulta fue derivada al Responsable de Calidad.";
          bubble.appendChild(note);
        }

        // El backend ya sabia si la respuesta quedo fundamentada, pero el
        // usuario no lo veia: una respuesta sin respaldo se leia igual de segura
        // que una citada. En cumplimiento esa diferencia es lo que importa.
        if (data.grounded === false && !data.escalate) {
          const aviso = document.createElement("div");
          aviso.className = "unverified";
          aviso.textContent =
            "No pude verificar esta respuesta contra los documentos vigentes. " +
            "Confírmala con Calidad antes de actuar.";
          bubble.appendChild(aviso);
        }

        this.renderSuggestions(data.suggestions);
        this.scrollToBottom();
      } catch (err) {
        typing.remove();
        this.addBubble("bot", "No pude conectar con el servidor. Revisa que el backend este corriendo.");
        this.statusDot.setAttribute("data-status", "offline");
      } finally {
        this.setBusy(false);
        this.textarea.focus();
      }
    }
  }

  if (!customElements.get("normia-chat-widget")) {
    customElements.define("normia-chat-widget", NormiaChatWidget);
  }
})();
