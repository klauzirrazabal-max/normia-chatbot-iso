/**
 * El armazon comun de las tres pantallas.
 *
 * Antes cada pagina llevaba su propia copia del mismo script de navegacion, y
 * ninguna sabia quien habia entrado. Esto lo centraliza: pinta la barra,
 * resuelve la sesion, ensena solo lo que el rol permite y deja el perfil en
 * `window.NORMIA.usuario` para quien lo necesite.
 */
(function () {
  // La navegacion del PRODUCTO. `demo.html` no esta aqui a proposito: es la
  // intranet simulada donde se ensena el asistente incrustado, un argumento
  // de venta y no una parte del sistema. Tenerla como "Asistente" hacia que
  // el producto no tuviera casa propia y que no se distinguiera lo que es
  // NormIA de lo que es decorado.
  const VISTAS = [
    { href: "inicio.html", texto: "Inicio", rol: null },
    { href: "asistente.html", texto: "Asistente", rol: null },
    { href: "generar.html", texto: "Material visual", rol: null },
    { href: "admin.html", texto: "Gestión documental", rol: "gestionar" },
  ];

  const ESTILOS = `
    .vistas {
      display: flex; gap: 2px; align-items: center; flex-wrap: wrap;
      background: var(--superficie); border-bottom: 1px solid var(--borde); padding: 0 20px;
      font-family: inherit;
    }
    .vistas a {
      padding: 11px 14px; font-size: 13.5px; font-weight: 600;
      color: var(--tinta-suave); text-decoration: none; border-bottom: 2px solid transparent;
    }
    .vistas a:hover { color: var(--marca); }
    .vistas a[aria-current="page"] { color: var(--marca); border-bottom-color: var(--marca); }
    .vistas .marca { font-weight: 700; color: var(--tinta); margin-right: 14px; font-size: 14px; }
    .vistas .yo {
      margin-left: auto; display: flex; align-items: center; gap: 10px;
      font-size: 13px; color: var(--tinta-media);
    }
    .vistas .rol {
      background: var(--marca-suave); color: var(--marca-oscura); border-radius: var(--radio-pil);
      padding: 2px 9px; font-size: 11.5px; font-weight: 600; text-transform: uppercase;
      letter-spacing: .03em;
    }
    .vistas .salir {
      background: none; border: none; color: var(--tinta-suave); font-size: 13px;
      cursor: pointer; text-decoration: underline; padding: 4px 0; font-family: inherit;
    }
    .vistas .salir:hover { color: var(--riesgo); }
    @media (max-width: 720px) {
      .vistas { padding: 0 12px; }
      .vistas .yo { width: 100%; margin-left: 0; padding: 0 2px 8px; }
    }
  `;

  const API = window.NORMIA_API || "";

  function el(tag, cls, texto) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (texto != null) n.textContent = texto;
    return n;
  }

  function pintar(usuario) {
    const tenant = new URLSearchParams(location.search).get("tenant");
    const aqui = location.pathname.split("/").pop() || "inicio.html";

    const estilo = document.createElement("style");
    estilo.textContent = ESTILOS;
    document.head.appendChild(estilo);

    const barra = el("nav", "vistas");
    barra.setAttribute("aria-label", "Vistas de NormIA");
    barra.appendChild(el("span", "marca", "NormIA"));

    VISTAS.forEach((v) => {
      // La gestion documental no se le ensena a un operario. Esconderla no es
      // la proteccion -- esa esta en el servidor -- pero ofrecer una puerta
      // que da 403 es una forma rara de tratar a alguien.
      if (v.rol === "gestionar" && usuario && !usuario.puede_gestionar) return;
      const a = el("a", null, v.texto);
      a.href = tenant ? `${v.href}?tenant=${encodeURIComponent(tenant)}` : v.href;
      if (v.href === aqui) a.setAttribute("aria-current", "page");
      barra.appendChild(a);
    });

    const yo = el("div", "yo");
    if (usuario) {
      yo.appendChild(el("span", null, usuario.nombre));
      yo.appendChild(el("span", "rol", usuario.rol));
      const salir = el("button", "salir", "Salir");
      salir.type = "button";
      salir.onclick = async () => {
        await fetch(`${API}/api/auth/logout`, { method: "POST" });
        location.href = "acceso.html";
      };
      yo.appendChild(salir);
    } else {
      const entrar = el("a", null, "Iniciar sesión");
      entrar.href = "acceso.html";
      yo.appendChild(entrar);
    }
    barra.appendChild(yo);

    document.body.insertBefore(barra, document.body.firstChild);
  }

  async function arrancar() {
    let usuario = null;
    try {
      const r = await fetch(`${API}/api/auth/yo`, { credentials: "same-origin" });
      const d = await r.json();
      if (d.autenticado) usuario = d;
    } catch {
      // Sin respuesta del servidor se pinta la barra sin usuario: la pagina
      // sigue siendo navegable y el error real ya lo cuenta cada vista.
    }

    window.NORMIA = window.NORMIA || {};
    window.NORMIA.usuario = usuario;

    const exige = document.body.dataset.exigeRol;
    if (exige === "gestionar" && (!usuario || !usuario.puede_gestionar)) {
      const destino = encodeURIComponent(location.pathname.split("/").pop());
      location.replace(`acceso.html?volver=${destino}`);
      return;
    }

    pintar(usuario);
    document.dispatchEvent(new CustomEvent("normia:sesion", { detail: usuario }));
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", arrancar);
  } else {
    arrancar();
  }
})();
