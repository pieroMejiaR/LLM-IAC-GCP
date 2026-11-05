"use strict";
// Interfaz del agente IaC (modo evaluación). Todo el texto que viene del modelo o de las
// herramientas se inserta con textContent; nunca con innerHTML.

(() => {
  const CONTROLES = ["formato", "guard", "fmt", "init", "validate", "tflint", "checkov", "plan"];
  const NOMBRE_CONTROL = { formato: "formato", guard: "guard", fmt: "fmt", init: "init", validate: "validate",
    tflint: "tflint", checkov: "checkov", plan: "plan" };
  const DESCRIPCION_CONTROL = {
    formato: "La respuesta del modelo trae archivos .tf",
    guard: "Revisión estática previa: solo proveedores de Google y nada que ejecute código",
    fmt: "terraform fmt",
    init: "terraform init con los proveedores precargados",
    validate: "terraform validate",
    tflint: "TFLint con las reglas de Google",
    checkov: "Checkov: solo SSH o RDP abiertos a internet bloquean; el resto se informa",
    plan: "terraform plan con una cuenta de solo lectura",
  };
  const SERVICIO = { cloud_run: "Cloud Run", cloud_sql_postgres: "Cloud SQL PostgreSQL", cloud_storage: "Cloud Storage",
    vpc: "VPC", secret_manager: "Secret Manager", iam: "IAM" };
  const EJEMPLOS = [
    "Somos una startup y necesitamos publicar la página estática de nuestro producto. Debe ser accesible desde internet y lo más barata posible. Región: Santiago de Chile.",
    "Una API en un contenedor que use PostgreSQL. La base de datos no debe tener IP pública y la contraseña debe guardarse en Secret Manager. Lo más barato posible.",
    "Un bucket privado para respaldos diarios, al que solo pueda escribir una cuenta de servicio propia. Sin acceso público.",
  ];
  const EN_CURSO = ["analizando", "generando"];
  const MAX_REQUERIMIENTO = 4000;
  const MAX_CORRECCION = 2000;

  const estado = { info: null, sesion: null, archivo: null, sondeo: null, error: null, enviando: false, firma: "" };

  // ---------------------------------------------------------------- utilidades

  function el(tag, attrs, ...hijos) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const h of hijos.flat()) {
      if (h === null || h === undefined || h === false) continue;
      n.append(h instanceof Node ? h : document.createTextNode(String(h)));
    }
    return n;
  }

  const numero = (x, dec) => new Intl.NumberFormat("es", { minimumFractionDigits: dec, maximumFractionDigits: dec }).format(x || 0);
  const usd = (x) => `US$ ${numero(x, 4)}`;
  const segundos = (s) => `${numero(s, 0)} s`;

  async function api(ruta, cuerpo) {
    const opciones = cuerpo === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cuerpo) };
    const r = await fetch(ruta, opciones);
    let datos = null;
    try { datos = await r.json(); } catch { datos = null; }
    if (!r.ok) {
      const error = new Error((datos && datos.error) || `El servidor respondió ${r.status}.`);
      error.estado = r.status;
      throw error;
    }
    return datos;
  }

  // ---------------------------------------------------------------- flujo

  async function iniciar() {
    try {
      estado.info = await api("/api/estado");
    } catch (e) {
      estado.error = `No se pudo leer el estado del agente: ${e.message}`;
    }
    const guardada = sessionStorage.getItem("sesion");
    if (guardada) {
      try { estado.sesion = await api(`/api/sesiones/${guardada}`); } catch { sessionStorage.removeItem("sesion"); }
    }
    render();
    sondear();
  }

  function aplicar(s) {
    estado.sesion = s;
    estado.error = null;
    sessionStorage.setItem("sesion", s.id);
    render();
    sondear();
  }

  function sondear() {
    clearTimeout(estado.sondeo);
    const s = estado.sesion;
    if (!s || !EN_CURSO.includes(s.estado)) return;
    estado.sondeo = setTimeout(async () => {
      try {
        aplicar(await api(`/api/sesiones/${s.id}`));
      } catch (e) {
        if (e.estado === 404) {
          // El servidor se reinició y perdió la sesión: se vuelve al inicio.
          sessionStorage.removeItem("sesion");
          estado.sesion = null;
          estado.error = "La sesión se perdió porque el servidor se reinició. Escriba el requerimiento de nuevo.";
          render();
          return;
        }
        estado.error = e.message;
        render();
        estado.sondeo = setTimeout(sondear, 3000);
      }
    }, 1000);
  }

  async function enviar(accion) {
    if (estado.enviando) return;
    estado.enviando = true;
    estado.error = null;
    render();
    try {
      aplicar(await accion());
    } catch (e) {
      estado.error = e.message;
    } finally {
      estado.enviando = false;
      render();
    }
  }

  function nuevoRequerimiento() {
    clearTimeout(estado.sondeo);
    sessionStorage.removeItem("sesion");
    estado.sesion = null;
    estado.archivo = null;
    estado.error = null;
    render();
    const campo = document.getElementById("campo-requerimiento");
    if (campo) campo.focus();
  }

  // ---------------------------------------------------------------- render

  function render() {
    renderEntorno();
    const bajar = renderConversacion();
    renderRedactor();
    renderMesa();
    // Al final: el redactor cambia de alto y con él el área visible del registro.
    if (bajar) {
      const registro = document.getElementById("registro");
      registro.scrollTop = registro.scrollHeight;
    }
  }

  function renderEntorno() {
    const dl = document.getElementById("entorno");
    const i = estado.info;
    const s = estado.sesion;
    const items = [];
    if (i) {
      items.push(["Modelo", i.modelo.replace(/^gemini\//, "")]);
      items.push(["Plan", i.plan ? "con cuenta de solo lectura" : "omitido, falta configurar GCP", !i.plan]);
    }
    items.push(["Gasto de esta sesión", usd(s ? s.costo_usd : 0)]);
    dl.replaceChildren(...items.map(([dt, dd, apagado]) =>
      el("div", {}, el("dt", { text: dt }), el("dd", { class: apagado ? "apagado" : null, text: dd }))));
  }

  function renderConversacion() {
    const seccion = document.querySelector(".conversacion");
    const s = estado.sesion;
    seccion.classList.toggle("inicial", !s);
    const registro = document.getElementById("registro");
    if (!s) { registro.replaceChildren(); estado.firma = ""; return false; }

    const entradas = s.eventos.map((ev) => entrada(ev, s));
    if (EN_CURSO.includes(s.estado)) {
      entradas.push(el("div", { class: "entrada agente en-curso" },
        el("div", { class: "autor", text: "Agente" }),
        el("div", { class: "texto", text: `${s.etapa || "Trabajando"}…` })));
    }
    const firma = JSON.stringify([s.id, s.eventos.length, s.estado, s.etapa]);
    if (firma === estado.firma) return false;
    const abajo = registro.scrollHeight - registro.scrollTop - registro.clientHeight < 60;
    registro.replaceChildren(...entradas);
    estado.firma = firma;
    return abajo || entradas.length <= 2;
  }

  function entrada(ev, s) {
    let texto = ev.texto || "";
    let nota = null;
    let clase = ev.autor;
    if (ev.tipo === "requerimiento") nota = `Región ${ev.region}`;
    if (ev.tipo === "especificacion") {
      texto = ev.revision === "A"
        ? "Propuse la especificación (revisión A). Revísela en la mesa de trabajo: apruébela o escriba qué cambiar."
        : `Apliqué sus cambios en la revisión ${ev.revision}.`;
    }
    if (ev.tipo === "aviso") {
      texto = `No pude aplicar ese cambio; sigue vigente la revisión ${ev.revision}. Detalle: ${ev.texto}`;
      clase += " mal";
    }
    if (ev.tipo === "aprobacion") texto = `Apruebo la revisión ${ev.revision}. Genere el Terraform.`;
    if (ev.tipo === "error") { texto = `Me detuve por un error: ${ev.texto}`; clase += " mal"; }
    if (ev.tipo === "resultado" && s.resultado) {
      const r = s.resultado;
      const rep = r.reparaciones === 1 ? "1 reparación" : `${r.reparaciones} reparaciones`;
      if (r.ok) {
        texto = `El Terraform pasó todos los controles con ${rep}. ${
          r.plan === "omitido" ? "El plan quedó omitido porque falta configurar GCP. " : ""}Puede descargar los archivos.`;
        clase += " bien";
      } else {
        const fallo = r.controles.find((c) => !c.ok);
        texto = `El Terraform no pasó ${fallo ? NOMBRE_CONTROL[fallo.control] : "la validación"} después de ${rep}. El último error está en la mesa de trabajo.`;
        clase += " mal";
      }
    }
    return el("div", { class: `entrada ${clase}` },
      el("div", { class: "autor", text: ev.autor === "usted" ? "Usted" : "Agente" }),
      el("div", { class: "texto", text: texto }),
      nota ? el("div", { class: "nota", text: nota }) : null);
  }

  function renderRedactor() {
    const caja = document.getElementById("redactor");
    const s = estado.sesion;
    const activo = document.activeElement;
    const enfocado = activo && caja.contains(activo) ? activo.id : null;
    const borrador = (id) => { const c = document.getElementById(id); return c ? c.value : ""; };

    let contenido;
    if (!s) contenido = redactorInicial(borrador("campo-requerimiento"));
    else if (s.estado === "revision") contenido = redactorCorreccion(borrador("campo-correccion"));
    else if (EN_CURSO.includes(s.estado)) {
      contenido = [el("p", { class: "ocupado-texto", text: "El agente está trabajando. Puede seguir el avance en la mesa de trabajo." })];
    } else {
      contenido = [el("div", { class: "fila-acciones" },
        el("button", { class: "boton", type: "button", onclick: nuevoRequerimiento, text: "Nuevo requerimiento" }))];
    }
    if (estado.error) contenido.push(el("p", { class: "error-linea", role: "alert", text: estado.error }));
    caja.replaceChildren(...contenido);
    if (enfocado) {
      const c = document.getElementById(enfocado);
      if (c) { c.focus(); if (c.setSelectionRange && c.value) c.setSelectionRange(c.value.length, c.value.length); }
    }
  }

  function campoConContador(id, max, valor, attrs) {
    const campo = el("textarea", { id, class: "campo-texto", maxlength: String(max), ...attrs });
    campo.value = valor;
    const contador = el("span", { class: "contador", "aria-live": "off" });
    const actualizar = () => {
      contador.textContent = `${campo.value.length} de ${max}`;
      contador.classList.toggle("excedido", campo.value.length >= max);
    };
    campo.addEventListener("input", actualizar);
    actualizar();
    return { campo, contador };
  }

  function redactorInicial(valor) {
    const i = estado.info || { regiones: ["southamerica-west1"], region: "southamerica-west1" };
    const { campo, contador } = campoConContador("campo-requerimiento", MAX_REQUERIMIENTO, valor, {
      placeholder: "Por ejemplo: una API en contenedores con una base de datos PostgreSQL, accesible por HTTPS y lo más barata posible.",
      "aria-label": "Requerimiento",
      rows: "6",
    });
    const region = el("select", { id: "campo-region", "aria-label": "Región" },
      i.regiones.map((r) => el("option", { value: r, selected: r === i.region, text: r })));
    const boton = el("button", { class: "boton", type: "submit", disabled: estado.enviando, text: "Analizar requerimiento" });
    const formulario = el("form", { class: "formulario", onsubmit: (e) => {
      e.preventDefault();
      enviar(() => api("/api/sesiones", { requerimiento: campo.value, region: region.value }));
    } }, campo, el("div", { class: "fila-acciones" },
      el("label", { class: "selector" }, "Región", region), contador, boton));
    campo.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) formulario.requestSubmit();
    });

    return [
      el("h2", { class: "titular", text: "Describa la infraestructura que necesita" }),
      el("p", { class: "bajada", text: "El agente la convierte en Terraform para Google Cloud, lo valida en un sandbox sin acceso a internet y le entrega los archivos. No crea recursos en su cuenta." }),
      formulario,
      el("div", { class: "ejemplos" },
        el("p", { text: "O empiece con un ejemplo" }),
        el("ul", {}, EJEMPLOS.map((t) => el("li", {}, el("button", {
          class: "ejemplo", type: "button", text: t,
          onclick: () => { campo.value = t; campo.dispatchEvent(new Event("input")); campo.focus(); },
        }))))),
    ];
  }

  function redactorCorreccion(valor) {
    const s = estado.sesion;
    const { campo, contador } = campoConContador("campo-correccion", MAX_CORRECCION, valor, {
      placeholder: "Escriba qué cambiar, por ejemplo: la base de datos sin IP pública.",
      "aria-label": "Cambios a la especificación",
      rows: "3",
    });
    const pedir = el("button", { class: "boton secundario", type: "submit", disabled: estado.enviando, text: "Pedir cambios" });
    const aprobar = el("button", { class: "boton ancho", type: "button", disabled: estado.enviando,
      text: `Aprobar la revisión ${s.revision} y generar el Terraform`,
      onclick: () => enviar(() => api(`/api/sesiones/${s.id}/aprobar`, {})) });
    const formulario = el("form", { class: "formulario", onsubmit: (e) => {
      e.preventDefault();
      if (!campo.value.trim()) { estado.error = "Escriba qué quiere cambiar, o apruebe la revisión."; render(); return; }
      enviar(() => api(`/api/sesiones/${s.id}/correcciones`, { texto: campo.value }));
    } }, campo, el("div", { class: "fila-acciones" }, pedir, contador), aprobar);
    campo.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) formulario.requestSubmit();
    });
    return [formulario];
  }

  // ---------------------------------------------------------------- mesa de trabajo

  function renderMesa() {
    const mesa = document.getElementById("mesa");
    const s = estado.sesion;
    const scroll = mesa.scrollTop;
    if (!s) { mesa.replaceChildren(introduccion()); return; }
    // Lo que importa en cada momento va arriba; la especificación aprobada pasa al final.
    const partes = [];
    const enRevision = s.estado === "analizando" || s.estado === "revision";
    if (enRevision) partes.push(hojaEspecificacion(s, false));
    if (s.resultado) partes.push(veredicto(s));
    if (s.estado === "generando" || s.resultado || s.intentos.length) partes.push(lineaDeControles(s));
    if (s.resultado && Object.keys(s.resultado.archivos).length) partes.push(archivos(s));
    if (!enRevision && s.especificacion) partes.push(hojaEspecificacion(s, true));
    if (s.estado === "error" && !s.especificacion) {
      partes.push(el("div", { class: "aviso-despliegue" },
        "No se obtuvo una especificación válida. Reformule el requerimiento con más detalle y vuelva a intentarlo."));
    }
    mesa.replaceChildren(...partes);
    mesa.scrollTop = scroll;
  }

  function introduccion() {
    const pasos = [
      ["Analiza el requerimiento", "Lo convierte en una especificación: servicios, restricciones y los supuestos que tuvo que hacer. Usted la revisa, pide cambios y la aprueba."],
      ["Planifica los recursos", "Elige los tipos de recurso de Terraform desde un catálogo cerrado de seis servicios, sin usar el modelo."],
      ["Genera el Terraform", "Escribe los archivos con el esquema del proveedor de Google a la vista, para no inventar atributos."],
      ["Valida en un sandbox", "Pasa cada intento por la misma línea de controles, en un contenedor sin internet:"],
      ["Repara con los errores", `Si un control falla, le devuelve el error al modelo y vuelve a validar, hasta ${estado.info ? estado.info.max_reparaciones : 5} veces.`],
    ];
    const ejemploVia = via(Object.fromEntries(CONTROLES.map((c) => [c, "pendiente"])), false);
    return el("div", { class: "introduccion" },
      el("h2", { text: "Cómo trabaja el agente" }),
      el("p", { text: "Es el mismo proceso que se usa en el experimento del informe; aquí usted revisa la especificación antes de generar." }),
      el("ol", { class: "pasos" }, pasos.map(([t, d], i) => el("li", {},
        el("strong", { text: t }), el("p", { text: d }), i === 3 ? ejemploVia : null))),
      el("div", { style: null },
        el("h3", { class: "subtitulo", text: "Servicios que conoce" }),
        el("div", { class: "catalogo" }, Object.values(SERVICIO).map((n) => el("span", { text: n })))),
      avisoDespliegue());
  }

  function avisoDespliegue() {
    return el("p", { class: "aviso-despliegue" },
      "Esta interfaz nunca despliega. Para desplegar el caso real se usa ",
      el("code", { text: "desplegar" }),
      " en la terminal, que pide escribir el ID del proyecto antes de aplicar y otra vez antes de destruir.");
  }

  function hojaEspecificacion(s, plegada) {
    const spec = s.especificacion;
    const analizando = s.estado === "analizando";
    const estadoTexto = analizando ? (spec ? "Aplicando sus cambios…" : "Analizando el requerimiento…")
      : s.estado === "revision" ? "Esperando su aprobación"
        : `Revisión ${s.revision} aprobada`;
    const cabeza = el("div", { class: "hoja-cabeza" },
      el("h2", { class: "hoja-titulo", text: "Especificación" }),
      s.revision ? el("span", { class: `revision${s.estado === "revision" ? " nueva" : ""}`, title: `Revisión ${s.revision}`,
        "aria-label": `Revisión ${s.revision}`, text: s.revision }) : null,
      el("p", { class: "hoja-estado", text: estadoTexto }));
    if (!spec) return el("article", { class: "hoja" }, cabeza,
      el("div", { class: "hoja-cuerpo" }, el("p", { class: "vacio", text: "La especificación aparecerá aquí en unos segundos." })));

    const filas = spec.servicios.map((sv) => el("tr", {},
      el("td", {}, el("span", { class: "servicio-nombre", text: SERVICIO[sv.tipo] || sv.tipo })),
      el("td", { text: sv.nombre }),
      el("td", {}, el("span", { class: sv.publico ? "acceso-publico" : "acceso-privado", text: sv.publico ? "Público" : "Privado" })),
      el("td", {}, parametros(sv.parametros))));
    const cuerpo = el("div", { class: "hoja-cuerpo" },
      el("table", { class: "tabla" },
        el("thead", {}, el("tr", {}, ["Servicio", "Nombre", "Acceso", "Parámetros"].map((t) => el("th", { scope: "col", text: t })))),
        el("tbody", {}, filas)),
      el("div", { class: "dos-columnas" },
        bloqueLista("Restricciones del requerimiento", spec.restricciones, "No hay restricciones explícitas."),
        bloqueLista("Supuestos del agente", spec.supuestos, "El agente no tuvo que suponer nada.")),
      s.plan_archivos ? el("div", {},
        el("h3", { class: "subtitulo", text: "Recursos previstos" }),
        el("div", { class: "recursos" }, s.plan_archivos.tipos_recurso.map((t) => el("span", { text: t })))) : null,
      cajetin(s));
    if (!plegada) return el("article", { class: "hoja" }, cabeza, cuerpo);
    const abierta = estado.especAbierta === s.id;
    return el("article", { class: "hoja" }, cabeza,
      el("details", { class: "plegable", open: abierta,
        ontoggle: (e) => {
          const d = e.currentTarget;
          estado.especAbierta = d.open ? s.id : null;
          d.querySelector("summary").textContent = d.open ? "Ocultar la especificación" : "Mostrar la especificación";
        } },
        el("summary", { text: abierta ? "Ocultar la especificación" : "Mostrar la especificación" }), cuerpo));
  }

  function parametros(p) {
    const claves = Object.keys(p || {});
    if (!claves.length) return el("span", { class: "vacio", text: "Sin parámetros" });
    return el("ul", { class: "parametros" }, claves.map((k) => el("li", {},
      el("span", { class: "clave", text: `${k} ` }), String(p[k]))));
  }

  function bloqueLista(titulo, items, vacio) {
    return el("div", {},
      el("h3", { class: "subtitulo", text: titulo }),
      items && items.length ? el("ul", { class: "lista" }, items.map((t) => el("li", { text: t })))
        : el("p", { class: "vacio", text: vacio }));
  }

  function cajetin(s) {
    const celdas = [
      ["Corrida", s.escenario_id],
      ["Región", s.region],
      ["Revisión", s.revision || "–"],
      ["Gasto", usd(s.costo_usd)],
      ["Tiempo", segundos(s.tiempo_s)],
    ];
    return el("dl", { class: "cajetin" }, celdas.map(([dt, dd]) => el("div", {}, el("dt", { text: dt }), el("dd", { text: dd }))));
  }

  // Estado de cada control en un intento ya validado.
  function estadosDeIntento(intento, controlesFinales) {
    const r = Object.fromEntries(CONTROLES.map((c) => [c, "pendiente"]));
    if (controlesFinales) {
      r.formato = "ok";
      for (const c of controlesFinales) r[c.control] = c.omitido ? "omitido" : (c.ok ? "ok" : "falla");
      return r;
    }
    let antes = true;
    for (const c of CONTROLES) {
      if (intento.ok) r[c] = "ok";
      else if (c === intento.control_fallido) { r[c] = "falla"; antes = false; }
      else r[c] = antes ? "ok" : "pendiente";
    }
    return r;
  }

  const ESTADO_TEXTO = { ok: "pasó", falla: "falló", omitido: "omitido", pendiente: "sin correr" };

  // Una fila de la línea de controles. Con etiquetas solo en la explicación inicial; en la
  // validación los nombres van una vez, en la cabecera, alineados con las columnas.
  function via(estados, corriendo, conEtiquetas = true) {
    return el("div", { class: `via${corriendo ? " corriendo" : ""}`, role: "list" },
      CONTROLES.map((c) => el("div", { class: `nodo ${estados[c]}`, role: "listitem",
        title: `${NOMBRE_CONTROL[c]}: ${DESCRIPCION_CONTROL[c]} (${ESTADO_TEXTO[estados[c]]})`,
        "aria-label": `${NOMBRE_CONTROL[c]}: ${ESTADO_TEXTO[estados[c]]}` },
        el("span", { class: "punto", "aria-hidden": "true" }),
        conEtiquetas ? el("span", { class: "etiqueta-nodo", text: NOMBRE_CONTROL[c] }) : null)));
  }

  function lineaDeControles(s) {
    const llamadas = s.llamadas.filter((c) => c.etapa === "generar" || c.etapa === "reparar");
    const cabecera = el("div", { class: "fila-control cabecera", "aria-hidden": "true" },
      el("div", {}),
      el("div", { class: "via-etiquetas" }, CONTROLES.map((c) => el("span", { title: DESCRIPCION_CONTROL[c], text: NOMBRE_CONTROL[c] }))));
    const filas = s.intentos.map((it, i) => {
      const ultimo = i === s.intentos.length - 1;
      const estados = estadosDeIntento(it, ultimo && s.resultado ? s.resultado.controles : null);
      const llamada = llamadas[i];
      return el("div", { class: "fila-control" },
        nombreFila(it.numero, llamada ? `${segundos(llamada.duracion_s)}, ${usd(llamada.costo_usd)}` : null),
        via(estados, false, false),
        it.errores && it.errores.length ? el("pre", { class: "errores", text: it.errores.slice(0, 3).join("\n") }) : null);
    });
    if (s.estado === "generando") {
      const n = s.intentos.length;
      const validando = n < llamadas.length;
      filas.push(el("div", { class: "fila-control" },
        nombreFila(n, null),
        via(Object.fromEntries(CONTROLES.map((c) => [c, "pendiente"])), validando, false),
        el("p", { class: "fila-texto", text: validando ? "Validando este intento…"
          : n === 0 ? "El modelo está escribiendo el Terraform…" : "El modelo está corrigiendo el error anterior…" })));
    }
    return el("article", { class: "validacion" },
      el("div", { class: "hoja-cabeza" },
        el("h2", { class: "hoja-titulo", text: "Validación" }),
        el("p", { class: "hoja-estado", text: "Cada fila es un intento" })),
      el("div", { class: "hoja-cuerpo" }, cabecera, filas));
  }

  function nombreFila(numeroIntento, meta) {
    return el("div", { class: "fila-nombre" },
      numeroIntento === 0 ? "Generación" : `Reparación ${numeroIntento}`,
      el("small", { text: numeroIntento === 0 ? "primer intento" : `intento ${numeroIntento + 1}` }),
      meta ? el("small", { class: "fila-meta", text: meta }) : null);
  }

  function veredicto(s) {
    const r = s.resultado;
    const fallo = r.controles.find((c) => !c.ok);
    const titulo = r.ok ? "Pasó todos los controles" : `Se detuvo en ${fallo ? NOMBRE_CONTROL[fallo.control] : "la validación"}`;
    const detalle = r.ok
      ? (r.plan === "omitido"
        ? "El plan quedó omitido: falta configurar el proyecto de GCP y la cuenta de solo lectura."
        : "Incluye terraform plan contra el proyecto, con una cuenta que no puede crear recursos.")
      : (r.error_final || "Revise el último intento en la validación.");
    const cifras = [
      ["Reparaciones", String(r.reparaciones)],
      ["Hallazgos de Checkov", String(r.checkov_fallidas)],
      ["Plan", r.plan],
      ["Tipos de recurso", String(r.tipos_recurso.length)],
      ["Tiempo del agente", segundos(s.tiempo_s)],
      ["Gasto", usd(s.costo_usd)],
    ];
    const bloque = el("article", { class: "hoja" },
      el("div", { class: "hoja-cuerpo" },
        el("div", { class: "veredicto" },
          el("div", {},
            el("h2", { class: `veredicto-texto ${r.ok ? "bien" : "mal"}`, text: titulo }),
            el("p", { class: "veredicto-detalle", text: detalle })),
          el("dl", { class: "cifras" }, cifras.map(([dt, dd]) => el("div", {}, el("dt", { text: dt }), el("dd", { text: dd }))))),
        r.checkov.length ? hallazgos(r.checkov) : null));
    return bloque;
  }

  function hallazgos(lista) {
    return el("details", { class: "hallazgos" },
      el("summary", {}, `${lista.length} ${lista.length === 1 ? "hallazgo" : "hallazgos"} de Checkov `,
        el("span", { text: "(se informan; solo SSH o RDP abiertos bloquean)" })),
      lista.map((h) => el("div", { class: "hallazgo" },
        el("span", { class: "regla", text: h.regla || "" }),
        el("span", { text: h.mensaje }),
        el("span", { class: "donde", text: [h.tipo_recurso, h.archivo && `${h.archivo}${h.linea ? `:${h.linea}` : ""}`].filter(Boolean).join(", ") }))));
  }

  function archivos(s) {
    const nombres = Object.keys(s.resultado.archivos).sort((a, b) => orden(a) - orden(b) || a.localeCompare(b));
    if (!nombres.includes(estado.archivo)) estado.archivo = nombres.includes("main.tf") ? "main.tf" : nombres[0];
    const pestanas = el("div", { class: "pestanas", role: "tablist", "aria-label": "Archivos generados" },
      nombres.map((n) => el("button", {
        class: "pestana", type: "button", role: "tab", "aria-selected": n === estado.archivo ? "true" : "false",
        text: n, onclick: () => { estado.archivo = n; renderMesa(); },
      })));
    return el("article", { class: "archivos" },
      el("div", { class: "hoja-cabeza" },
        el("h2", { class: "hoja-titulo", text: "Archivos" }),
        el("p", { class: "hoja-estado", text: `${nombres.length} archivos tras terraform fmt` })),
      pestanas,
      codigo(s.resultado.archivos[estado.archivo] || ""),
      el("div", { class: "pie-archivos" },
        el("span", { text: s.carpeta ? `También quedó guardado en ${s.carpeta}` : "" }),
        el("a", { class: "boton", href: `/api/sesiones/${s.id}/proyecto.zip`, download: "" , text: "Descargar proyecto (.zip)" })));
  }

  function orden(nombre) {
    return ["main.tf", "variables.tf", "outputs.tf", "providers.tf", "versions.tf"].indexOf(nombre) + 1 || 99;
  }

  // Resaltado mínimo de HCL: construye nodos, sin HTML.
  const TOKENS = /(#.*$|\/\/.*$)|("(?:[^"\\]|\\.)*")|\b(resource|data|variable|output|provider|terraform|locals|module|required_providers|dynamic|content)\b|\b(true|false|null|\d+(?:\.\d+)?)\b/g;
  function codigo(texto) {
    const pre = el("pre", { class: "codigo", tabindex: "0", "aria-label": "Contenido del archivo" });
    for (const linea of texto.replace(/\n$/, "").split("\n")) {
      const l = el("span", { class: "l" });
      let resto = linea;
      const attr = /^(\s*)([A-Za-z_][\w-]*)(\s*=)/.exec(linea);
      if (attr) {
        l.append(attr[1], el("span", { class: "tok-a", text: attr[2] }), attr[3]);
        resto = linea.slice(attr[0].length);
      }
      let ultimo = 0;
      for (const m of resto.matchAll(TOKENS)) {
        if (m.index > ultimo) l.append(resto.slice(ultimo, m.index));
        const clase = m[1] ? "tok-c" : m[2] ? "tok-s" : m[3] ? "tok-k" : "tok-n";
        l.append(el("span", { class: clase, text: m[0] }));
        ultimo = m.index + m[0].length;
      }
      if (ultimo < resto.length) l.append(resto.slice(ultimo));
      if (!linea) l.append(" ");
      pre.append(l);
    }
    return pre;
  }

  document.addEventListener("DOMContentLoaded", iniciar);
})();
