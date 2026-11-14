/* =========================================================================
   Centro de Monitoreo Predictivo · Cloud — front-end sin dependencias externas.
   Consume la API FastAPI del mismo origen (Render) y la API pública de GitHub.
   ========================================================================= */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const FEAT = ["temperatura", "vibracion", "corriente"];
const META = {
  temperatura: { nombre: "Temperatura del rodamiento", corto: "Temperatura", unidad: "°C", min: 40, max: 110, step: 0.5, dec: 1, color: "#e53935", sim: [60, 100], clave: "temp" },
  vibracion:   { nombre: "Vibración global", corto: "Vibración", unidad: "mm/s RMS", min: 0, max: 9, step: 0.05, dec: 2, color: "#f59e0b", sim: [1, 7], clave: "vib" },
  corriente:   { nombre: "Corriente del motor", corto: "Corriente", unidad: "A", min: 35, max: 65, step: 0.1, dec: 1, color: "#1e88e5", sim: [44, 56], clave: "corr" },
};
const ZONA_COLOR = { A: "#2e7d32", B: "#8bc34a", C: "#ffb300", D: "#e53935" };
const NIVEL_COLOR = ["#2e7d32", "#8bc34a", "#f9c80e", "#ff9800", "#e53935"];
const NIVEL_TEXTO_OSCURO = [false, true, true, true, false];
const SVGNS = "http://www.w3.org/2000/svg";

const S = { equipo: null, modelo: null, escenarios: null, escenarioSel: null, version: null, ultimo: null };

/* ------------------------------------------------------------------ util */
const fmt = (v, d = 2) => (v == null || Number.isNaN(v) ? "—" : Number(v).toLocaleString("es-EC", { minimumFractionDigits: d, maximumFractionDigits: d }));
const pct = (p, d = 1) => (p < 0.001 && p > 0 ? "< 0,1 %" : `${fmt(p * 100, d)} %`);
const pval = (p) => {
  if (p === 0) return "&lt; 10<sup>−300</sup>";
  if (p >= 1e-4) return fmt(p, 4);
  const [m, e] = p.toExponential(1).split("e");
  return `${m.replace(".", ",")}·10<sup>${e.replace("-", "−")}</sup>`;
};
function el(tag, attrs = {}, html = "") {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (html) e.innerHTML = html;
  return e;
}
function svg(tag, attrs = {}) {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}
function zona(variable, valor) {
  const [a, b, c] = S.equipo.umbrales[variable].limites;
  return valor < a ? "A" : valor < b ? "B" : valor <= c ? "C" : "D";
}
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

/* ------------------------------------------------------------------ API */
const conQuery = (ruta, params) => `${ruta}?${new URLSearchParams(params)}`;

/** Llama a la API. body: objeto (JSON) o FormData. opts.headers: cabeceras extra. opts.silencioso: no registrar. */
async function api(method, url, body, opts = {}) {
  const t0 = performance.now();
  const init = { method, headers: { ...(opts.headers || {}) } };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
  const r = await fetch(url, init);
  let data = null;
  try { data = await r.json(); } catch { /* respuesta no JSON */ }
  const meta = { ms: performance.now() - t0, srv: parseFloat(r.headers.get("X-Response-Time-ms")), rid: r.headers.get("X-Request-ID"), status: r.status, headers: r.headers };
  if (!opts.silencioso) { S.ultimo = meta; registrar(method, url, body, data, meta); }
  if (!r.ok) { const e = new Error(`HTTP ${r.status}`); e.status = r.status; e.data = data; e.meta = meta; throw e; }
  return data;
}
function registrar(method, url, body, data, meta) {
  $("#req-method").textContent = method;
  $("#req-url").textContent = url;
  const st = $("#req-status");
  st.textContent = meta.status; st.style.color = meta.status < 300 ? "var(--good)" : "var(--critical)";
  $("#req-time").textContent = `${Math.round(meta.ms)} ms total · ${fmt(meta.srv, 1)} ms en el servidor`;
  $("#req-id").textContent = meta.rid ? `request-id ${meta.rid}` : "";
  $("#req-body").textContent = body === undefined ? "(sin cuerpo)" : body instanceof FormData ? "(archivo multipart/form-data)" : JSON.stringify(body, null, 2);
  $("#res-body").textContent = JSON.stringify(data, null, 2);
}
function etiquetaLatencia(sel, meta) {
  const t = $(sel); if (!t || !meta) return;
  t.textContent = `${Math.round(meta.ms)} ms · servidor ${fmt(meta.srv, 0)} ms`;
  t.title = "Tiempo total medido en el navegador · tiempo de cómputo en el servidor (X-Response-Time-ms)";
}

/* ------------------------------------------------------------------ pestañas */
function activarTab(nombre) {
  $$(".tab").forEach((t) => t.setAttribute("aria-selected", String(t.dataset.tab === nombre)));
  $$(".panel").forEach((p) => (p.hidden = p.id !== `tab-${nombre}`));
  history.replaceState(null, "", `#${nombre}`);
}
$$(".tab").forEach((t) => t.addEventListener("click", () => activarTab(t.dataset.tab)));

/* ================================================================== 1. LECTURA */
function gradienteZonas(variable) {
  const { min, max } = META[variable];
  const [a, b, c] = S.equipo.umbrales[variable].limites;
  const p = (v) => `${(((v - min) / (max - min)) * 100).toFixed(2)}%`;
  return `linear-gradient(to right, ${ZONA_COLOR.A} 0 ${p(a)}, ${ZONA_COLOR.B} ${p(a)} ${p(b)}, ${ZONA_COLOR.C} ${p(b)} ${p(c)}, ${ZONA_COLOR.D} ${p(c)} 100%)`;
}
function construirSensores() {
  const cont = $("#sensores");
  for (const v of FEAT) {
    const m = META[v];
    const [a, b, c] = S.equipo.umbrales[v].limites;
    const bloque = el("div", { class: "sensor" }, `
      <div class="sensor-head">
        <span class="sensor-name"><i style="background:${m.color}"></i>${m.nombre}</span>
        <span class="sensor-val">
          <input type="number" id="n-${v}" min="${m.min}" max="${m.max}" step="${m.step}" aria-label="${m.nombre}">
          <span class="unidad">${m.unidad}</span>
          <span class="zona-badge" id="z-${v}">–</span>
        </span>
      </div>
      <input type="range" id="r-${v}" min="${m.min}" max="${m.max}" step="${m.step}" style="background:${gradienteZonas(v)}" aria-label="${m.nombre}">
      <div class="escala"><span>${m.min}</span><span>A│${a}</span><span>B│${b}</span><span>C│${c}</span><span>${m.max}</span></div>`);
    cont.append(bloque);
    const rango = $(`#r-${v}`, bloque), num = $(`#n-${v}`, bloque);
    rango.addEventListener("input", () => { num.value = rango.value; alCambiarLectura(); });
    num.addEventListener("input", () => { rango.value = num.value; alCambiarLectura(); });
  }
  fijarLectura([72, 2.0, 47]);
}
function fijarLectura(valores) {
  FEAT.forEach((v, i) => { $(`#r-${v}`).value = valores[i]; $(`#n-${v}`).value = valores[i]; });
  alCambiarLectura();
}
function leerLectura() {
  return Object.fromEntries(FEAT.map((v) => [v, parseFloat($(`#n-${v}`).value)]));
}
function alCambiarLectura() {
  const l = leerLectura();
  for (const v of FEAT) {
    const z = zona(v, l[v]);
    const b = $(`#z-${v}`); b.textContent = z; b.className = `zona-badge z${z}`;
  }
  predecirDebounced();
}
async function predecir() {
  try {
    const r = await api("POST", conQuery("/predict", leerLectura()));
    $("#error-lectura").hidden = true; $("#resultado-lectura").hidden = false;
    pintarResultado(r);
    etiquetaLatencia("#lat-lectura", S.ultimo);
  } catch (e) {
    mostrarError(e);
  }
}
const predecirDebounced = debounce(predecir, 160);

function pintarResultado(r) {
  const p = r.probabilidad_falla, u = r.umbral_decision;
  $("#prob").textContent = pct(p);
  const arco = $("#g-arc");
  arco.setAttribute("stroke-dasharray", `${Math.max(p * 100, 0.01)} 100`);
  arco.style.stroke = p >= u ? "var(--critical)" : "var(--good)";
  // marcador del umbral sobre el arco
  const th = Math.PI * (1 - u), cx = 110, cy = 115;
  const pt = (rad) => [cx + rad * Math.cos(th), cy - rad * Math.sin(th)];
  const [x1, y1] = pt(76), [x2, y2] = pt(104);
  Object.entries({ x1, y1, x2, y2 }).forEach(([k, v]) => $("#g-umbral").setAttribute(k, v));
  $("#hero-label").innerHTML = `probabilidad de falla · <span class="umbral-tag">▍umbral ${Math.round(u * 100)} %</span>`;

  const zg = r.zona_global;
  const dm = $("#dual-modelo"), dn = $("#dual-norma");
  $("strong", dm).textContent = r.prediccion ? "Falla" : "Sin falla";
  $(".det", dm).textContent = `P = ${pct(p)} · umbral ${Math.round(u * 100)} %`;
  dm.style.borderLeftColor = r.prediccion ? "var(--critical)" : "var(--good)";
  $("strong", dn).textContent = `Zona ${zg}`;
  $(".det", dn).textContent = r.accion_norma;
  dn.style.borderLeftColor = ZONA_COLOR[zg];

  // La recomendación la redacta la API (campo "recomendacion"); aquí solo se elige el color.
  const txtRec = r.recomendacion;
  const color = r.prediccion && zg !== "D" ? "#ff9800" : r.prediccion || zg === "D" ? "#e53935" : zg === "C" ? "#ffb300" : "#2e7d32";
  const rec = $("#recomendacion");
  rec.innerHTML = `<svg viewBox="0 0 24 24"><path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 15h-2v-6h2zm0-8h-2V7h2z"/></svg><span>${txtRec}</span>`;
  rec.style.background = `${color}22`; rec.style.color = color === "#ffb300" || color === "#8bc34a" ? "var(--ink)" : color;
  rec.style.borderLeft = `4px solid ${color}`;

  const tb = $("#tabla-variables tbody"); tb.innerHTML = "";
  for (const v of FEAT) {
    const d = r.detalle_variables[v];
    tb.append(el("tr", {}, `<td>${META[v].corto}</td><td class="num">${fmt(d.valor, META[v].dec)} ${META[v].unidad}</td>
      <td><span class="zona-badge z${d.zona}">${d.zona}</span></td><td>${d.accion}</td>`));
  }
}
function mostrarError(e) {
  $("#resultado-lectura").hidden = true;
  const box = $("#error-lectura"); box.hidden = false;
  const det = e.data && Array.isArray(e.data.detail) ? e.data.detail.map((d) => `<li><code>${d.loc.slice(1).join(".")}</code>: ${d.msg}</li>`).join("") : `<li>${e.message}</li>`;
  box.innerHTML = `<p><span class="code">HTTP ${e.status ?? "?"}</span> <strong>La API rechazó la lectura</strong></p><ul>${det}</ul>
    <p class="hint small">La API valida cada entrada antes de usarla (Pydantic) y devuelve un error claro en lugar de una predicción absurda.</p>
    <button type="button" class="btn-sec" id="btn-volver">Volver a una lectura válida</button>`;
  $("#btn-volver").addEventListener("click", () => fijarLectura([72, 2.0, 47]));
}
$$("#presets-lectura .chip[data-p]").forEach((b) => b.addEventListener("click", () => fijarLectura(b.dataset.p.split(",").map(Number))));
$("#btn-invalido").addEventListener("click", async () => {
  try { await api("POST", conQuery("/predict", { temperatura: 72, vibracion: -1, corriente: 47 })); }
  catch (e) { mostrarError(e); }
});

/* ================================================================== 2. LOTE */
function construirEscenarios() {
  const cont = $("#escenarios");
  for (const [clave, e] of Object.entries(S.escenarios)) {
    const b = el("button", { type: "button", class: "esc", "data-esc": clave, "aria-pressed": "false" },
      `<strong>${e.titulo}</strong><span>${e.descripcion}</span>`);
    b.addEventListener("click", () => seleccionarEscenario(clave));
    cont.append(b);
  }
  const grid = $("#param-grid");
  grid.innerHTML = `<span></span><span class="ph">Media</span><span class="ph">Desv. estándar</span>`;
  for (const v of FEAT) {
    grid.append(el("span", {}, `${META[v].corto} <small>(${META[v].unidad})</small>`));
    grid.append(el("input", { type: "number", id: `p-${v}-m`, step: META[v].step }));
    grid.append(el("input", { type: "number", id: `p-${v}-s`, step: META[v].step, min: "0.01" }));
  }
  seleccionarEscenario("sin_drift");
}
function seleccionarEscenario(clave) {
  S.escenarioSel = clave;
  $$(".esc").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.esc === clave)));
  const p = S.escenarios[clave].params;
  for (const v of FEAT) {
    $(`#p-${v}-m`).value = p[`${META[v].clave}_mean`];
    $(`#p-${v}-s`).value = p[`${META[v].clave}_std`];
  }
}
const PARAM_GUIA = { temp: "t", vib: "v", corr: "c" };   // nombres de query param de la guía: t_mean, v_std…
async function ejecutarMonitor() {
  const q = { size: parseInt($("#p-size").value, 10), k_sigma: parseFloat($("#p-k").value),
              aleatorio: $("#p-aleatorio").checked, evidently: $("#p-evidently").checked };
  for (const v of FEAT) {
    q[`${PARAM_GUIA[META[v].clave]}_mean`] = parseFloat($(`#p-${v}-m`).value);
    q[`${PARAM_GUIA[META[v].clave]}_std`] = parseFloat($(`#p-${v}-s`).value);
  }
  const btn = $("#btn-monitor");
  btn.disabled = true; btn.lastChild.textContent = q.evidently ? "Analizando con Evidently…" : "Analizando el lote…";
  try {
    const r = await api("POST", conQuery("/monitor", q));
    pintarMonitor(r);
    etiquetaLatencia("#lat-lote", S.ultimo);
  } catch (e) {
    $("#lote-vacio").hidden = false; $("#lote-res").hidden = true;
    $("#lote-vacio").innerHTML = `<span><strong>Error ${e.status ?? ""}</strong><br>${JSON.stringify(e.data?.detail ?? e.message)}</span>`;
  } finally {
    btn.disabled = false; btn.lastChild.textContent = "Ejecutar monitoreo";
  }
}
function pintarMonitor(r) {
  $("#lote-vacio").hidden = true; $("#lote-res").hidden = false; $("#detalle-lote").hidden = false;
  const c = r.codigo_alerta;
  const esc = $("#escalera"); esc.innerHTML = "";
  Object.values(S.equipo.niveles_alerta).forEach((n, i) => {
    const s = el("div", { class: `escalon${i <= c ? " on" : ""}${i === c ? " actual" : ""}` }, n.nivel);
    s.style.borderBottomColor = i <= c ? NIVEL_COLOR[i] : "";
    if (i === c) { s.style.background = NIVEL_COLOR[i]; if (NIVEL_TEXTO_OSCURO[i]) s.style.color = "#1d1d1d"; }
    esc.append(s);
  });
  const ban = $("#banner");
  ban.style.background = NIVEL_COLOR[c]; ban.style.color = NIVEL_TEXTO_OSCURO[c] ? "#1d1d1d" : "#fff";
  $("#b-nivel").textContent = r.nivel_alerta;
  $("#b-accion").textContent = r.accion_recomendada;
  $("#diag").textContent = r.diagnostico;
  const d = r.drift, m = r.modelo;
  $("#kpis").innerHTML = `
    <div class="kpi"><b>${d.n_drifted_features}/3</b><span>variables con drift (K-S · ${d.motor === "evidently" ? "Evidently" : "SciPy"})</span></div>
    <div class="kpi"><b>${d.dataset_drift ? "Sí" : "No"}</b><span>drift del dataset (≥ ${Math.round(d.drift_share_umbral * 100)} %)</span></div>
    <div class="kpi"><b>${pct(m.probabilidad_media_falla)}</b><span>P(falla) media del lote</span></div>
    <div class="kpi"><b>${pct(m.tasa_lecturas_con_falla, 0)}</b><span>lecturas clasificadas como falla</span></div>`;

  const cont = $("#var-cards"); cont.innerHTML = "";
  for (const v of FEAT) {
    const ks = r.limites_k_sigma[v], ev = d.columnas[v], zn = r.zonas_norma[v];
    const card = el("div", { class: "vcard" }, `
      <div class="vcard-head"><strong>${META[v].corto}</strong>
        <span class="tag ${ev.drift ? "tag-drift" : "tag-ok"}">${ev.drift ? "Drift detectado" : "Estable"}</span></div>
      <div class="bullet"></div>
      <dl>
        <dt>Media línea base</dt><dd>${fmt(ks.ref_mean, META[v].dec)} ± ${fmt(ks.ref_std, META[v].dec)}</dd>
        <dt>Límite k·σ (k = ${r.limites_k_sigma.k_sigma})</dt><dd>${fmt(ks.upper_limit, META[v].dec)}</dd>
        <dt>Media del lote</dt><dd><b>${fmt(ks.current_mean, META[v].dec)}</b> ${META[v].unidad}</dd>
        <dt>Desplazamiento</dt><dd>${fmt(r.desplazamiento_sigmas[v], 2)} σ</dd>
        <dt>p-valor K-S</dt><dd>${pval(ev.estadistico)}</dd>
        <dt>Zona norma</dt><dd><span class="zona-badge z${zn.zona}">${zn.zona}</span></dd>
      </dl>`);
    $(".bullet", card).append(bulletChart(v, ks));
    cont.append(card);
  }
  const ifr = $("#iframe-report");
  if (!ifr.hidden) ifr.src = `/report?t=${Date.now()}`;
}
function bulletChart(v, ks) {
  const W = 300, H = 58, x0 = 6, x1 = W - 6;
  const vals = [ks.ref_mean - 2 * ks.ref_std, ks.current_mean, ks.upper_limit, ...S.equipo.umbrales[v].limites];
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = (hi - lo) * 0.12; lo -= pad; hi += pad;
  const X = (val) => x0 + ((val - lo) / (hi - lo)) * (x1 - x0);
  const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" });
  const [a, b, c] = S.equipo.umbrales[v].limites;
  [[lo, a, "A"], [a, b, "B"], [b, c, "C"], [c, hi, "D"]].forEach(([p, q, z]) => {
    const xa = Math.max(X(p), x0), xb = Math.min(X(q), x1);
    if (xb > xa) {
      s.append(svg("rect", { x: xa, y: 18, width: xb - xa, height: 16, fill: ZONA_COLOR[z], opacity: 0.35 }));
      const t = svg("text", { x: (xa + xb) / 2, y: 30, "text-anchor": "middle", "font-size": 9, "font-weight": 700, fill: ZONA_COLOR[z] });
      t.textContent = z; s.append(t);
    }
  });
  // banda ±1σ de la referencia
  s.append(svg("rect", { x: X(ks.ref_mean - ks.ref_std), y: 22, width: X(ks.ref_mean + ks.ref_std) - X(ks.ref_mean - ks.ref_std), height: 8, fill: "#78909c", opacity: 0.45, rx: 3 }));
  s.append(svg("circle", { cx: X(ks.ref_mean), cy: 26, r: 4.5, fill: "#78909c" }));
  s.append(svg("line", { x1: X(ks.upper_limit), x2: X(ks.upper_limit), y1: 12, y2: 40, stroke: "currentColor", "stroke-width": 2, "stroke-dasharray": "3 2" }));
  const cur = svg("circle", { cx: X(ks.current_mean), cy: 26, r: 7, fill: ks.alert ? "#e53935" : "#0f6cbd", stroke: "#fff", "stroke-width": 2 });
  s.append(cur);
  [[lo, "start"], [hi, "end"]].forEach(([val, anc]) => {
    const t = svg("text", { x: anc === "start" ? x0 : x1, y: 54, "text-anchor": anc, "font-size": 9, fill: "var(--muted)" });
    t.textContent = fmt(val, META[v].dec); s.append(t);
  });
  const lbl = svg("text", { x: X(ks.upper_limit), y: 9, "text-anchor": "middle", "font-size": 8.5, fill: "var(--ink-2)" });
  lbl.textContent = "k·σ"; s.append(lbl);
  return s;
}
$("#btn-monitor").addEventListener("click", ejecutarMonitor);
$("#btn-iframe").addEventListener("click", () => {
  const ifr = $("#iframe-report");
  ifr.hidden = !ifr.hidden;
  $("#btn-iframe").textContent = ifr.hidden ? "Ver reporte aquí" : "Ocultar reporte";
  if (!ifr.hidden) ifr.src = `/report?t=${Date.now()}`;
});

/* ================================================================== 3. SIMULACIÓN */
const SIM = { datos: [], reproduciendo: false, cfg: { dias: 60, inicio_degradacion: 20, dia_falla: 56, persistencia: 3 } };
const NIV_CORTO = ["NORMAL", "VIGIL.", "PREAL.", "PLANIF.", "DETENER"];

function ejes(s, W, H, m, xmax, ymin, ymax, yticks, yfmt) {
  const X = (d) => m.l + (d / (xmax - 1)) * (W - m.l - m.r);
  const Y = (v) => H - m.b - ((v - ymin) / (ymax - ymin)) * (H - m.t - m.b);
  const g = svg("g", { class: "ax" });
  for (const t of yticks) {
    g.append(svg("line", { x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t) }));
    const tx = svg("text", { x: m.l - 6, y: Y(t) + 3, "text-anchor": "end" }); tx.textContent = yfmt(t); g.append(tx);
  }
  for (let d = 0; d < xmax; d += 10) {
    const tx = svg("text", { x: X(d), y: H - m.b + 13, "text-anchor": "middle" }); tx.textContent = d; g.append(tx);
  }
  s.append(g);
  return { X, Y };
}
function marcasEventos(s, X, yTop, yBot) {
  [[SIM.cfg.inicio_degradacion, "inicio desgaste", "#78909c"], [SIM.cfg.dia_falla, "falla", "#e53935"]].forEach(([d, t, c]) => {
    s.append(svg("line", { x1: X(d), x2: X(d), y1: yTop, y2: yBot, stroke: c, "stroke-dasharray": "3 3", "stroke-width": 1.2 }));
    const tx = svg("text", { x: X(d) + 3, y: yTop + 9, "font-size": 9, fill: c }); tx.textContent = t; s.append(tx);
  });
}
function dibujarNiveles() {
  const W = 820, H = 150, m = { l: 58, r: 10, t: 8, b: 18 };
  const s = svg("svg", { viewBox: `0 0 ${W} ${H}` });
  const { X, Y } = ejes(s, W, H, m, SIM.cfg.dias, 0, 4.6, [0, 1, 2, 3, 4], (t) => NIV_CORTO[t]);
  const bw = (W - m.l - m.r) / SIM.cfg.dias * 0.8;
  for (const d of SIM.datos) {
    const r = svg("rect", { x: X(d.dia) - bw / 2, y: Y(d.codigo_alerta + 0.35), width: bw, height: Y(0) - Y(d.codigo_alerta + 0.35) + 0.01, fill: NIVEL_COLOR[d.codigo_alerta], rx: 2 });
    r.addEventListener("mousemove", (ev) => tooltip(ev, `<b>Día ${d.dia}</b> · ${d.nivel_alerta}<br>${d.diagnostico}`));
    r.addEventListener("mouseleave", ocultarTooltip);
    s.append(r);
  }
  marcasEventos(s, X, m.t, H - m.b);
  $("#ch-niveles").replaceChildren(s);
}
function dibujarSensor(v) {
  const W = 820, H = 120, m = { l: 58, r: 10, t: 8, b: 18 };
  const [ymin, ymax] = META[v].sim;
  const s = svg("svg", { viewBox: `0 0 ${W} ${H}` });
  const Xd = (d) => m.l + (d / (SIM.cfg.dias - 1)) * (W - m.l - m.r);
  const Yd = (val) => H - m.b - ((Math.min(Math.max(val, ymin), ymax) - ymin) / (ymax - ymin)) * (H - m.t - m.b);
  const [a, b, c] = S.equipo.umbrales[v].limites;
  [[ymin, a, "A"], [a, b, "B"], [b, c, "C"], [c, ymax, "D"]].forEach(([p, q, z]) => {
    if (q <= ymin || p >= ymax) return;
    s.append(svg("rect", { x: m.l, width: W - m.l - m.r, y: Yd(q), height: Yd(p) - Yd(q), fill: ZONA_COLOR[z], opacity: 0.13 }));
  });
  const paso = (ymax - ymin) / 4;
  ejes(s, W, H, m, SIM.cfg.dias, ymin, ymax, [0, 1, 2, 3, 4].map((i) => ymin + i * paso), (t) => fmt(t, META[v].dec > 1 ? 1 : 0));
  const tit = svg("text", { x: m.l + 6, y: m.t + 11, "font-size": 11, "font-weight": 700, fill: META[v].color });
  tit.textContent = `${META[v].corto} (${META[v].unidad})`; s.append(tit);
  if (SIM.datos.length) {
    const area = SIM.datos.map((d) => `${Xd(d.dia)},${Yd(d.p95[v])}`).concat([...SIM.datos].reverse().map((d) => `${Xd(d.dia)},${Yd(d.p05[v])}`));
    s.append(svg("polygon", { points: area.join(" "), fill: META[v].color, opacity: 0.18 }));
    s.append(svg("polyline", { points: SIM.datos.map((d) => `${Xd(d.dia)},${Yd(d.medias[v])}`).join(" "), fill: "none", stroke: META[v].color, "stroke-width": 2.2 }));
    const u = SIM.datos[SIM.datos.length - 1];
    s.append(svg("circle", { cx: Xd(u.dia), cy: Yd(u.medias[v]), r: 4, fill: META[v].color, stroke: "#fff", "stroke-width": 1.5 }));
  }
  marcasEventos(s, Xd, m.t, H - m.b);
  $(`#ch-${v}`).replaceChildren(s);
}
function hitos() {
  const ini = SIM.cfg.inicio_degradacion, falla = SIM.cfg.dia_falla, P = SIM.cfg.persistencia;
  const desp = SIM.datos.filter((d) => d.dia >= ini);
  const primero = (f) => { const x = desp.find(f); return x ? x.dia : null; };
  const confirmado = (() => {
    for (let i = P - 1; i < SIM.datos.length; i++) {
      const ventana = SIM.datos.slice(i - P + 1, i + 1);
      if (SIM.datos[i].dia >= ini && ventana.every((d) => d.n_drift > 0)) return SIM.datos[i].dia;
    }
    return null;
  })();
  const lista = [
    ["Drift estadístico (señal cruda)", primero((d) => d.codigo_alerta >= 1), NIVEL_COLOR[1]],
    [`Drift confirmado (${P} días seguidos)`, confirmado, NIVEL_COLOR[1]],
    ["PREALERTA · media > μ + 2σ", primero((d) => d.codigo_alerta >= 2), NIVEL_COLOR[2]],
    ["PLANIFICAR · zona C de la norma", primero((d) => d.codigo_alerta >= 3), NIVEL_COLOR[3]],
    ["Modelo ML · > 50 % lecturas con falla", primero((d) => d.tasa_falla_modelo > 0.5), "#8e24aa"],
    ["DETENER · zona D de la norma", primero((d) => d.codigo_alerta >= 4), NIVEL_COLOR[4]],
  ];
  const sanas = SIM.datos.filter((d) => d.dia < ini);
  const falsas = sanas.filter((d) => d.codigo_alerta >= 1).length;
  const falsasPersist = sanas.filter((d, i) => i >= P - 1 && sanas.slice(i - P + 1, i + 1).every((x) => x.n_drift > 0)).length;
  const ol = $("#hitos"); ol.innerHTML = "";
  for (const [txt, dia, color] of lista) {
    const li = el("li", { class: dia != null ? "ok" : "" });
    li.style.borderLeftColor = dia != null ? color : "";
    li.innerHTML = dia != null ? `<b>${txt}</b>Día ${dia} · <span class="anticip">${falla - dia} días</span> antes de la falla` : `<b>${txt}</b>pendiente…`;
    ol.append(li);
  }
  const li = el("li", { class: "ok" });
  li.innerHTML = `<b>Alarmas en etapa sana (días 0–${ini - 1})</b>${falsas} sin persistencia · ${falsasPersist} con persistencia de ${P} días`;
  if (sanas.length) ol.append(li);
}
function pintarDia(d) {
  $("#sim-dia").textContent = d.dia;
  $("#prog-barra").style.width = `${((d.dia + 1) / SIM.cfg.dias) * 100}%`;
  const n = $("#ed-nivel"); n.textContent = d.nivel_alerta; n.style.color = d.codigo_alerta === 1 || d.codigo_alerta === 2 ? "var(--ink)" : NIVEL_COLOR[d.codigo_alerta];
  $("#estado-dia").style.borderLeft = `5px solid ${NIVEL_COLOR[d.codigo_alerta]}`;
  $("#ed-diag").textContent = d.diagnostico;
  $("#ed-h").style.width = `${d.salud_h * 100}%`;
  dibujarNiveles(); FEAT.forEach(dibujarSensor); hitos();
}
async function reproducir() {
  if (SIM.reproduciendo) { SIM.reproduciendo = false; return; }
  if (SIM.datos.length >= SIM.cfg.dias) reiniciar();
  SIM.reproduciendo = true; actualizarBotonPlay();
  try {
    while (SIM.reproduciendo && SIM.datos.length < SIM.cfg.dias) {
      const d = await api("GET", `/trayectoria/${SIM.datos.length}`);
      SIM.cfg = d.config; SIM.datos.push(d); pintarDia(d);
    }
  } catch (e) {
    $("#ed-diag").textContent = `Error al consultar la API: ${e.message}`;
  }
  SIM.reproduciendo = false; actualizarBotonPlay();
}
function actualizarBotonPlay() {
  const b = $("#btn-play");
  b.innerHTML = SIM.reproduciendo
    ? `<svg viewBox="0 0 24 24"><path d="M6 5h4v14H6zm8 0h4v14h-4z"/></svg><span>Pausar</span>`
    : `<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg><span>${SIM.datos.length && SIM.datos.length < SIM.cfg.dias ? "Continuar" : "Reproducir"}</span>`;
}
function reiniciar() {
  SIM.reproduciendo = false; SIM.datos = [];
  $("#sim-dia").textContent = "–"; $("#prog-barra").style.width = "0";
  $("#ed-nivel").textContent = "—"; $("#ed-diag").textContent = "Pulsa Reproducir para iniciar."; $("#ed-h").style.width = "0";
  $("#estado-dia").style.borderLeft = "";
  dibujarNiveles(); FEAT.forEach(dibujarSensor); $("#hitos").innerHTML = "";
  actualizarBotonPlay();
}
$("#btn-play").addEventListener("click", reproducir);
$("#btn-reset").addEventListener("click", reiniciar);

/* ------------------------------------------------------------------ tooltip */
function tooltip(ev, html) {
  const t = $("#tooltip"); t.innerHTML = html; t.hidden = false;
  t.style.left = `${Math.min(ev.clientX + 12, innerWidth - 270)}px`; t.style.top = `${ev.clientY + 12}px`;
}
function ocultarTooltip() { $("#tooltip").hidden = true; }

/* ================================================================== 4. EQUIPO */
function construirEquipo() {
  const e = S.equipo.equipo;
  const filas = [["Equipo", e.descripcion], ["Potencia", `${e.potencia_kw} kW`], ["Alimentación", `${e.tension_v} V · ${e.frecuencia_hz} Hz`],
    ["Factor de potencia / eficiencia", `${fmt(e.factor_potencia)} / ${fmt(e.eficiencia)}`], ["Corriente nominal", `${fmt(S.equipo.corriente_nominal_A, 1)} A`],
    ["Clasificación", e.clasificacion_iso], ["Norma", e.norma_vibracion], ["Aplicación", e.aplicacion]];
  $("#tabla-equipo tbody").innerHTML = filas.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("");
  const z = S.equipo.zonas;
  let html = `<thead><tr><th>Variable</th>${"ABCD".split("").map((l) => `<th><span class="zona-badge z${l}">${l}</span></th>`).join("")}</tr></thead><tbody>`;
  for (const v of FEAT) {
    const [a, b, c] = S.equipo.umbrales[v].limites;
    html += `<tr><td>${META[v].corto}<br><small class="hint">${S.equipo.umbrales[v].fuente}</small></td><td class="num">&lt; ${a}</td><td class="num">${a}–${b}</td><td class="num">${b}–${c}</td><td class="num">&gt; ${c}</td></tr>`;
  }
  html += `<tr><td>Acción</td>${"ABCD".split("").map((l) => `<td>${z[l].accion}</td>`).join("")}</tr></tbody>`;
  $("#tabla-zonas").innerHTML = html;

  const md = S.modelo, mh = md.metricas_holdout;
  $("#stats-modelo").innerHTML = [
    [md.nombre_modelo, "algoritmo seleccionado"], [`${Math.round(md.umbral * 100)} %`, "umbral de decisión (mín. costo)"],
    [fmt(mh.recall, 3), "recall (holdout)"], [fmt(mh.precision, 3), "precisión (holdout)"],
    [fmt(mh.pr_auc, 3), "PR-AUC (holdout)"], [fmt(mh.roc_auc, 3), "ROC-AUC (holdout)"],
  ].map(([b, s]) => `<div class="stat"><b>${b}</b><span>${s}</span></div>`).join("");
  const cv = md.comparacion_cv;
  $("#tabla-cv").innerHTML = `<thead><tr><th>Modelo</th><th>Recall</th><th>Precisión</th><th>F1</th><th>PR-AUC</th></tr></thead><tbody>${
    cv.map((r) => `<tr${r.modelo === md.nombre_modelo ? ' style="font-weight:700"' : ""}><td>${r.modelo}</td><td class="num">${fmt(r.recall_media, 3)}</td><td class="num">${fmt(r.precision_media, 3)}</td><td class="num">${fmt(r.f1_media, 3)}</td><td class="num">${fmt(r.pr_auc_media, 3)}</td></tr>`).join("")}</tbody>`;
  $("#modelo-meta").textContent = `Entrenado el ${md.fecha_entrenamiento.replace("T", " ")} · scikit-learn ${md.version_sklearn} · ${md.n_train} lecturas de entrenamiento y ${md.n_test} de prueba · costos supuestos: falla no detectada ${md.costos.falso_negativo.toLocaleString("es-EC")} USD, falsa alarma ${md.costos.falso_positivo.toLocaleString("es-EC")} USD.`;
  $("#niveles-leyenda").innerHTML = Object.values(S.equipo.niveles_alerta)
    .map((n, i) => `<div class="nl" style="border-top-color:${NIVEL_COLOR[i]}"><b>${n.nivel}</b>${n.accion}</div>`).join("");
}

/* ================================================================== utilidades nube */
const duracion = (s) => {
  if (s == null) return "—";
  s = Math.round(s);
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : m ? `${m} min ${s % 60} s` : `${s} s`;
};
const escapar = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const percentil = (arr, p) => {
  if (!arr.length) return NaN;
  const a = [...arr].sort((x, y) => x - y), i = (a.length - 1) * p, lo = Math.floor(i), hi = Math.ceil(i);
  return a[lo] + (a[hi] - a[lo]) * (i - lo);
};
const guardarSesion = (k, v) => { try { v ? sessionStorage.setItem(k, v) : sessionStorage.removeItem(k); } catch { /* sin almacenamiento */ } };
const leerSesion = (k) => { try { return sessionStorage.getItem(k) || ""; } catch { return ""; } };
function descargar(nombre, texto, tipo = "text/csv;charset=utf-8") {
  const url = URL.createObjectURL(new Blob(["﻿" + texto], { type: tipo }));
  const a = el("a", { href: url, download: nombre }); document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/* ================================================================== franja de despliegue */
function pintarDespliegue(v, primeraMs) {
  S.version = v;
  const enNube = v.entorno.startsWith("Render");
  $("#pill-entorno").textContent = enNube ? `☁ ${v.entorno}` : v.entorno;
  $("#hero-estado").textContent = enNube ? "En producción · Render (Oregon, plan Free)" : `Ejecutándose en ${v.entorno}`;
  $("#hero-url").textContent = v.url_publica || location.origin;
  $("#hs-version").textContent = `v${v.version}`;
  $("#hs-commit").innerHTML = v.commit_corto
    ? `<a href="https://github.com/${escapar(v.repositorio)}/commit/${escapar(v.commit)}" target="_blank" rel="noopener">${escapar(v.commit_corto)}</a>${v.rama ? ` · ${escapar(v.rama)}` : ""}`
    : "local";
  $("#hs-uptime").textContent = duracion(v.uptime_s);
  $("#hs-mem").textContent = v.memoria_mb != null ? `${fmt(v.memoria_mb, 0)} MB${v.limite_memoria_mb ? ` / ${v.limite_memoria_mb}` : ""}` : "n/d";
  $("#hs-cold").textContent = `${fmt(primeraMs / 1000, 1)} s`;
  const aviso = $("#aviso-frio");
  if (primeraMs > 3000) {
    aviso.hidden = false;
    aviso.innerHTML = `<b>Arranque en frío detectado.</b> La primera respuesta tardó ${fmt(primeraMs / 1000, 1)} s porque el contenedor estaba suspendido. En el plan Free, Render lo duerme tras ~15 min sin tráfico y lo reactiva con la siguiente petición; las respuestas siguientes ya son rápidas.`;
  } else if (v.uptime_s < 120) {
    aviso.hidden = false;
    aviso.innerHTML = `<b>Contenedor recién iniciado</b> (hace ${duracion(v.uptime_s)}): acaba de desplegarse una versión nueva o el servicio despertó de la suspensión.`;
  }
}

/* ================================================================== 4. INTEGRACIÓN */
const CSV = { archivo: null, resultado: null };
const claveApi = () => $("#api-key").value.trim();
const cabeceraClave = () => (claveApi() ? { "X-API-Key": claveApi() } : {});

function estadoClave(tipo, html) {
  const box = $("#key-estado"); box.className = `key-estado ke-${tipo}`; box.innerHTML = html;
}
async function probarClave() {
  guardarSesion("api_key", claveApi());
  try {
    const r = await api("GET", "/historial?n=5", undefined, { headers: cabeceraClave() });
    estadoClave("ok", `<b>200 · Acceso concedido.</b> ${r.n} predicciones en el historial del contenedor. ${escapar(r.nota)}`);
  } catch (e) {
    const msg = { 401: "Clave ausente o incorrecta.", 503: "El servidor no tiene API_KEY configurada (Render → Environment)." }[e.status] || e.message;
    estadoClave("err", `<b>${e.status ?? "Error"} · Acceso denegado.</b> ${msg}`);
  }
}
$("#btn-probar-key").addEventListener("click", probarClave);
$("#api-key").addEventListener("keydown", (ev) => { if (ev.key === "Enter") probarClave(); });
$("#btn-ver-key").addEventListener("click", () => {
  const i = $("#api-key"); i.type = i.type === "password" ? "text" : "password";
  $("#btn-ver-key").textContent = i.type === "password" ? "Mostrar" : "Ocultar";
});

async function demoSeguridad(tipo) {
  const out = $("#demo-salida");
  const mostrar = (titulo, status, cuerpo) => {
    out.className = `demo-salida ${status >= 400 ? "ds-err" : "ds-ok"}`;
    out.textContent = `${titulo}\n→ HTTP ${status}\n\n${typeof cuerpo === "string" ? cuerpo : JSON.stringify(cuerpo, null, 2)}`;
  };
  try {
    if (tipo === "sin-clave") {
      await api("GET", "/historial?n=1");
      mostrar("GET /historial (sin X-API-Key)", 200, "Respondió 200: revisa la configuración.");
    } else if (tipo === "invalido") {
      await api("POST", conQuery("/predict", { temperatura: 72, vibracion: -1, corriente: 47 }));
    } else if (tipo === "csv-malo") {
      const fd = new FormData();
      fd.append("archivo", new Blob(["temperatura,vibracion\n72,2\n"], { type: "text/csv" }), "incompleto.csv");
      await api("POST", "/predict/lote/csv", fd, { headers: cabeceraClave() });
    } else if (tipo === "cabeceras") {
      const r = await fetch("/", { method: "GET", cache: "no-store" });
      const claves = ["content-security-policy", "x-content-type-options", "x-frame-options", "referrer-policy", "permissions-policy", "strict-transport-security", "x-request-id", "x-response-time-ms"];
      const txt = claves.map((k) => `${k}: ${r.headers.get(k) ?? "(no presente — solo con HTTPS)"}`).join("\n");
      mostrar("GET / (cabeceras de respuesta)", r.status, txt);
    }
  } catch (e) {
    const titulos = { "sin-clave": "GET /historial (sin X-API-Key)", invalido: "POST /predict?vibracion=-1", "csv-malo": "POST /predict/lote/csv (falta columna corriente)" };
    mostrar(titulos[tipo], e.status, e.data ?? e.message);
  }
}
$$("#demo-seg .demo").forEach((b) => b.addEventListener("click", () => demoSeguridad(b.dataset.demo)));

async function construirChecklist() {
  try {
    const [seg, resp] = await Promise.all([api("GET", "/ops/seguridad", undefined, { silencioso: true }), fetch("/", { cache: "no-store" })]);
    const csp = resp.headers.get("content-security-policy"), nosniff = resp.headers.get("x-content-type-options");
    const items = [
      [seg.https || location.protocol === "https:", "Cifrado en tránsito (HTTPS/TLS)", seg.https ? "Certificado gestionado por Render" : "En local se usa HTTP; en Render es HTTPS automático"],
      [seg.usuario_no_root, "Contenedor sin privilegios de root", seg.usuario_no_root == null ? "No aplicable fuera de Linux" : "USER appuser en el Dockerfile"],
      [seg.api_key_configurada, "API key para endpoints de integración", seg.api_key_configurada ? "Secreto en variable de entorno (no en el repositorio)" : "Falta definir API_KEY en Render → Environment"],
      [true, `Límite de ${seg.rate_limit_por_min} peticiones/min por IP`, "Responde 429 + Retry-After ante abuso"],
      [seg.validacion_entradas, "Validación estricta de entradas", "Pydantic/FastAPI: tipos y rangos físicos → 422"],
      [!!csp && nosniff === "nosniff", "Cabeceras de seguridad", "CSP, nosniff, X-Frame-Options, Referrer-Policy"],
      [true, `Límites de tamaño (${seg.max_lote_filas} filas · ${Math.round(seg.max_csv_bytes / 1e6)} MB)`, "Evita agotar los 512 MB de RAM"],
      [!seg.cors_origins.includes("*"), "CORS restringido", seg.cors_origins.includes("*") ? "Abierto (*) para la demo académica; en producción, solo dominios propios" : seg.cors_origins.join(", ")],
    ];
    $("#checklist").innerHTML = items.map(([ok, t, d]) => `<li class="${ok ? "ck-ok" : ok === false ? "ck-warn" : "ck-na"}"><span class="ck-ico">${ok ? "✓" : ok === false ? "!" : "–"}</span><div><b>${t}</b><span>${escapar(d)}</span></div></li>`).join("");
  } catch (e) {
    $("#checklist").innerHTML = `<li class="ck-warn"><span class="ck-ico">!</span><div><b>No se pudo consultar /ops/seguridad</b><span>${escapar(e.message)}</span></div></li>`;
  }
}

/* --- CSV --- */
function csvEjemplo() {
  // Un turno de 8 h (una lectura cada 10 min): arranca sano y termina con degradación + una lectura errónea.
  const filas = ["temperatura;vibracion;corriente"];
  for (let i = 0; i < 48; i++) {
    const h = Math.max(0, (i - 20) / 28);
    const r = () => (Math.random() - 0.5) * 2;
    const t = 72 + 14 * h * h + 2.5 * r(), v = 2.0 + 2.6 * h * h + 0.25 * r(), c = 47 + 3 * h + 1.2 * r();
    filas.push([t, v, c].map((x) => x.toFixed(2).replace(".", ",")).join(";"));
  }
  filas.splice(30, 0, "sin_dato;2,1;47,5");
  return filas.join("\n");
}
function elegirArchivo(file) {
  if (!file) return;
  CSV.archivo = file;
  $("#archivo-nombre").textContent = `${file.name} · ${fmt(file.size / 1024, 1)} KB`;
  $("#btn-enviar-csv").disabled = false;
}
$("#archivo-csv").addEventListener("change", (ev) => elegirArchivo(ev.target.files[0]));
const dz = $("#dropzone");
["dragenter", "dragover"].forEach((t) => dz.addEventListener(t, (ev) => { ev.preventDefault(); dz.classList.add("sobre"); }));
["dragleave", "drop"].forEach((t) => dz.addEventListener(t, (ev) => { ev.preventDefault(); dz.classList.remove("sobre"); }));
dz.addEventListener("drop", (ev) => elegirArchivo(ev.dataTransfer.files[0]));
$("#btn-ejemplo-csv").addEventListener("click", () => {
  const txt = csvEjemplo();
  descargar("turno_ejemplo.csv", txt);
  elegirArchivo(new File([txt], "turno_ejemplo.csv", { type: "text/csv" }));
});

async function enviarCsv() {
  if (!CSV.archivo) return;
  const fd = new FormData(); fd.append("archivo", CSV.archivo, CSV.archivo.name);
  const btn = $("#btn-enviar-csv"); btn.disabled = true; btn.lastChild.textContent = "Procesando…";
  try {
    const r = await api("POST", "/predict/lote/csv", fd, { headers: cabeceraClave() });
    CSV.resultado = r; pintarCsv(r); etiquetaLatencia("#lat-csv", S.ultimo);
  } catch (e) {
    $("#csv-detalle").hidden = true;
    const box = $("#csv-resumen"); box.className = "panel-error";
    const det = e.status === 401 ? "Escribe la API key en el paso 1." : e.status === 503 ? "El servidor no tiene API_KEY configurada." : JSON.stringify(e.data?.detail ?? e.message);
    box.innerHTML = `<p><span class="code">HTTP ${e.status ?? "?"}</span> <strong>La API rechazó el lote</strong></p><p class="small">${escapar(det)}</p>`;
  } finally {
    btn.disabled = false; btn.lastChild.textContent = "Enviar a la API";
  }
}
$("#btn-enviar-csv").addEventListener("click", enviarCsv);

function pintarCsv(r) {
  const s = r.resumen, n = s.n_lecturas || 1;
  const box = $("#csv-resumen"); box.className = "csv-ok";
  const barra = "ABCD".split("").map((z) => s.zonas[z] ? `<span class="z${z}" style="flex:${s.zonas[z]}" title="Zona ${z}: ${s.zonas[z]} lecturas">${s.zonas[z] / n >= 0.08 ? z : ""}</span>` : "").join("");
  box.innerHTML = `
    <div class="kpis">
      <div class="kpi"><b>${s.n_lecturas}</b><span>lecturas evaluadas</span></div>
      <div class="kpi"><b>${s.n_fallas_predichas}</b><span>clasificadas como falla</span></div>
      <div class="kpi"><b>${pct(s.probabilidad_media)}</b><span>P(falla) media</span></div>
      <div class="kpi"><b>${r.filas_invalidas.length}</b><span>filas rechazadas</span></div>
    </div>
    <span class="mini mt-s">Distribución por zona normativa (peor variable)</span>
    <div class="barra-zonas">${barra}</div>`;
  $("#csv-detalle").hidden = false;
  $("#csv-invalidas").innerHTML = r.filas_invalidas.length
    ? `<div class="aviso-filas"><b>${r.filas_invalidas.length} fila(s) rechazadas por validación:</b> ${r.filas_invalidas.slice(0, 5).map((f) => `fila ${f.fila} (${escapar(f.motivo)})`).join(" · ")}</div>` : "";
  const filas = r.resultados.slice(0, 300).map((x, i) => `<tr${x.prediccion ? ' class="fila-falla"' : ""}>
      <td class="num">${i + 1}</td><td class="num">${fmt(x.temperatura, 1)}</td><td class="num">${fmt(x.vibracion, 2)}</td><td class="num">${fmt(x.corriente, 1)}</td>
      <td><div class="pbar"><i style="width:${Math.max(x.probabilidad_falla * 100, 1)}%;background:${x.prediccion ? "var(--critical)" : "var(--good)"}"></i><span>${pct(x.probabilidad_falla)}</span></div></td>
      <td>${FEAT.map((v) => `<span class="zona-badge zs z${x[`zona_${v}`]}" title="${META[v].corto}">${x[`zona_${v}`]}</span>`).join("")}</td>
      <td><span class="zona-badge z${x.zona_global}">${x.zona_global}</span></td><td>${x.prediccion ? "<b>Falla</b>" : "Sin falla"}</td></tr>`).join("");
  $("#tabla-csv").innerHTML = `<thead><tr><th>#</th><th>Temp. °C</th><th>Vib. mm/s</th><th>Corr. A</th><th>P(falla)</th><th>Zonas T·V·C</th><th>Global</th><th>Modelo</th></tr></thead><tbody>${filas}</tbody>`;
}
$("#btn-descargar-res").addEventListener("click", () => {
  if (!CSV.resultado) return;
  const cols = ["temperatura", "vibracion", "corriente", "probabilidad_falla", "prediccion", "zona_temperatura", "zona_vibracion", "zona_corriente", "zona_global"];
  const txt = [cols.join(";"), ...CSV.resultado.resultados.map((x) => cols.map((c) => String(x[c]).replace(".", ",")).join(";"))].join("\n");
  descargar("predicciones_lote.csv", txt);
});

/* --- fragmentos de código --- */
function fragmentos() {
  const B = location.origin;
  return {
    curl: `# Predicción individual (query params, igual que la guía + corriente)
curl -X POST "${B}/predict?temperatura=88&vibracion=4.1&corriente=51"

# Monitoreo de un lote simulado (drift + k·σ + zonas)
curl -X POST "${B}/monitor?size=200&t_mean=81&v_mean=3.5&c_mean=49"

# Lote real desde CSV (requiere API key)
curl -X POST "${B}/predict/lote/csv" \\
     -H "X-API-Key: $API_KEY" \\
     -F "archivo=@turno.csv"`,
    python: `import os, requests

BASE = "${B}"
r = requests.post(f"{BASE}/predict",
                  params={"temperatura": 88, "vibracion": 4.1, "corriente": 51}, timeout=60)
print(r.status_code, r.json()["etiqueta"], r.json()["probabilidad_falla"])

# Lote real (la clave se lee de una variable de entorno, nunca se escribe en el código)
with open("turno.csv", "rb") as f:
    r = requests.post(f"{BASE}/predict/lote/csv", files={"archivo": f},
                      headers={"X-API-Key": os.environ["API_KEY"]}, timeout=120)
print(r.json()["resumen"])`,
    js: `const BASE = "${B}";

const r = await fetch(\`\${BASE}/predict?temperatura=88&vibracion=4.1&corriente=51\`, { method: "POST" });
const pred = await r.json();
console.log(pred.etiqueta, pred.probabilidad_falla, pred.recomendacion);

// Métricas del servicio (formato JSON)
const ops = await (await fetch(\`\${BASE}/ops/metricas\`)).json();
console.log("p95 =", ops.latencia_ms.p95, "ms");`,
  };
}
let lenguaje = "curl";
function pintarCodigo() { $("#codigo").textContent = fragmentos()[lenguaje]; }
$$(".code-tab").forEach((b) => b.addEventListener("click", () => {
  lenguaje = b.dataset.lang;
  $$(".code-tab").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
  pintarCodigo();
}));
$("#btn-copiar").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText($("#codigo").textContent); $("#btn-copiar").textContent = "¡Copiado!"; }
  catch { $("#btn-copiar").textContent = "Selecciona y copia"; }
  setTimeout(() => ($("#btn-copiar").textContent = "Copiar"), 1600);
});

/* ================================================================== 5. NUBE Y OPERACIÓN */
const OPS = { timer: null, logsVistos: new Set() };

async function refrescarOps() {
  try {
    const [m, lg, v] = await Promise.all([
      api("GET", "/ops/metricas", undefined, { silencioso: true }),
      api("GET", "/ops/logs?n=40", undefined, { silencioso: true }),
      api("GET", "/version", undefined, { silencioso: true })]);
    pintarKpisOps(m, v); dibujarTrafico(m.serie); pintarEndpoints(m.por_endpoint); pintarLogs(lg.logs);
    $("#hs-uptime").textContent = duracion(v.uptime_s);
    if (v.memoria_mb != null) $("#hs-mem").textContent = `${fmt(v.memoria_mb, 0)} MB${v.limite_memoria_mb ? ` / ${v.limite_memoria_mb}` : ""}`;
    $("#pl-live").textContent = `activo · ${duracion(v.uptime_s)}`;
  } catch (e) { console.warn("ops", e); }
}
function pintarKpisOps(m, v) {
  const mem = m.memoria_mb, lim = v.limite_memoria_mb || 512;
  const tiles = [
    [duracion(m.uptime_s), "tiempo activo del contenedor"],
    [m.arranque_s != null ? `${fmt(m.arranque_s, 1)} s` : "—", "arranque de la app (carga del modelo)"],
    [m.peticiones_total.toLocaleString("es-EC"), "peticiones atendidas"],
    [`${fmt(m.latencia_ms.p50, 0)}<small> / ${fmt(m.latencia_ms.p95, 0)} ms</small>`, "latencia p50 / p95 en el servidor"],
    [pct(m.tasa_error_5xx), `errores 5xx · ${Object.values(m.rechazos_seguridad).reduce((a, b) => a + b, 0)} rechazos 4xx`],
    [mem != null ? `${fmt(mem, 0)} MB` : "n/d", mem != null ? `memoria RSS · ${pct(mem / lim, 0)} de ${lim} MB` : "memoria (solo Linux)"],
  ];
  $("#ops-kpis").innerHTML = tiles.map(([b, s]) => `<div class="kpi kpi-card"><b>${b}</b><span>${s}</span></div>`).join("");
}
function dibujarTrafico(serie) {
  const W = 620, H = 170, m = { l: 34, r: 40, t: 10, b: 20 };
  const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Tráfico y latencia" });
  const maxN = Math.max(4, ...serie.map((d) => d.n));
  const p95s = serie.map((d) => d.p95_ms).filter((x) => x != null);
  const maxP = Math.max(20, ...p95s) * 1.15;
  const bw = (W - m.l - m.r) / serie.length;
  const Y = (v) => H - m.b - (v / maxN) * (H - m.t - m.b);
  const Yp = (v) => H - m.b - (v / maxP) * (H - m.t - m.b);
  const g = svg("g", { class: "ax" });
  [0, 0.5, 1].forEach((f) => {
    g.append(svg("line", { x1: m.l, x2: W - m.r, y1: Y(maxN * f), y2: Y(maxN * f) }));
    const t1 = svg("text", { x: m.l - 6, y: Y(maxN * f) + 3, "text-anchor": "end" }); t1.textContent = Math.round(maxN * f); g.append(t1);
    const t2 = svg("text", { x: W - m.r + 6, y: Yp(maxP * f) + 3 }); t2.textContent = `${Math.round(maxP * f)}`; g.append(t2);
  });
  [["−5 min", 0, "start"], ["ahora", serie.length - 1, "end"]].forEach(([txt, i, anc]) => {
    const t = svg("text", { x: anc === "start" ? m.l : W - m.r, y: H - 4, "text-anchor": anc }); t.textContent = txt; g.append(t);
  });
  s.append(g);
  serie.forEach((d, i) => {
    if (!d.n) return;
    const r = svg("rect", { x: m.l + i * bw + bw * 0.15, y: Y(d.n), width: bw * 0.7, height: Y(0) - Y(d.n), rx: 2, fill: "var(--accent)", opacity: 0.75 });
    r.addEventListener("mousemove", (ev) => tooltip(ev, `<b>${d.n} peticiones</b> · p95 ${d.p95_ms ?? "—"} ms<br>hace ${-d.t} s`));
    r.addEventListener("mouseleave", ocultarTooltip);
    s.append(r);
  });
  const pts = serie.map((d, i) => (d.p95_ms != null ? `${m.l + i * bw + bw / 2},${Yp(d.p95_ms)}` : null)).filter(Boolean);
  if (pts.length > 1) s.append(svg("polyline", { points: pts.join(" "), fill: "none", stroke: "var(--vib)", "stroke-width": 2.2 }));
  pts.forEach((p) => { const [x, y] = p.split(","); s.append(svg("circle", { cx: x, cy: y, r: 2.6, fill: "var(--vib)" })); });
  if (!serie.some((d) => d.n)) {
    const t = svg("text", { x: W / 2, y: H / 2, "text-anchor": "middle", "font-size": 12, fill: "var(--muted)" });
    t.textContent = "Sin tráfico en los últimos 5 minutos: usa las otras pestañas o la prueba de latencia."; s.append(t);
  }
  $("#ch-trafico").replaceChildren(s);
}
function pintarEndpoints(lista) {
  $("#tabla-endpoints").innerHTML = `<thead><tr><th>Endpoint</th><th>Peticiones</th><th>Errores</th><th>p50 ms</th><th>p95 ms</th></tr></thead><tbody>${
    lista.slice(0, 8).map((d) => `<tr><td><code>${escapar(d.endpoint)}</code></td><td class="num">${d.n}</td><td class="num">${d.errores}</td><td class="num">${d.p50_ms ?? "—"}</td><td class="num">${d.p95_ms ?? "—"}</td></tr>`).join("")
    || '<tr><td colspan="5" class="hint">Aún no hay peticiones registradas.</td></tr>'}</tbody>`;
}
function pintarLogs(logs) {
  const term = $("#terminal");
  term.innerHTML = logs.map((l) => {
    const nivel = l.nivel === "WARNING" ? "warn" : l.nivel === "ERROR" ? "err" : "info";
    const hora = (l.ts || "").slice(11, 19);
    const { ts, nivel: _n, ...resto } = l;
    const cuerpo = l.evento === "http"
      ? `<span class="t-m">${escapar(l.metodo)}</span> ${escapar(l.ruta)} <span class="t-s t-s${String(l.status)[0]}">${l.status}</span> ${l.ms} ms <span class="t-dim">ip=${escapar(l.ip)} id=${escapar(l.request_id)}</span>`
      : `<span class="t-ev">${escapar(l.evento)}</span> <span class="t-dim">${escapar(JSON.stringify(resto))}</span>`;
    return `<div class="t-linea t-${nivel}"><span class="t-dim">${hora}</span> <span class="t-niv">${escapar(l.nivel)}</span> ${cuerpo}</div>`;
  }).join("") || '<div class="t-dim">Sin registros todavía.</div>';
}
function programarOps() {
  clearInterval(OPS.timer);
  if ($("#auto-ops").checked && !$("#tab-nube").hidden) OPS.timer = setInterval(refrescarOps, 5000);
}
$("#auto-ops").addEventListener("change", programarOps);

/* --- prueba de latencia --- */
async function pruebaLatencia() {
  const n = parseInt($("#lt-n").value, 10), ep = $("#lt-endpoint").value, concurrencia = 5;
  const btn = $("#btn-latencia"); btn.disabled = true;
  const muestras = []; let i = 0, errores = 0;
  const t0 = performance.now();
  const trabajador = async () => {
    while (i < n) {
      i++;
      btn.lastChild.textContent = `Enviando ${Math.min(i, n)}/${n}…`;
      const q = { temperatura: (70 + Math.random() * 20).toFixed(1), vibracion: (1.5 + Math.random() * 3).toFixed(2), corriente: (45 + Math.random() * 8).toFixed(1) };
      const url = ep === "predict" ? conQuery("/predict", q) : "/health";
      const ini = performance.now();
      try {
        const r = await fetch(url, { method: ep === "predict" ? "POST" : "GET", cache: "no-store" });
        await r.text();
        if (!r.ok) errores++;
        muestras.push({ total: performance.now() - ini, srv: parseFloat(r.headers.get("X-Response-Time-ms")) || 0, status: r.status });
      } catch { errores++; }
    }
  };
  await Promise.all(Array.from({ length: concurrencia }, trabajador));
  const dur = (performance.now() - t0) / 1000;
  btn.disabled = false; btn.lastChild.textContent = "Ejecutar prueba";
  pintarLatencia(muestras, dur, errores, ep);
  refrescarOps();
}
function pintarLatencia(muestras, dur, errores, ep) {
  $("#lt-res").hidden = false;
  const tot = muestras.map((m) => m.total), srv = muestras.map((m) => m.srv), red = muestras.map((m) => Math.max(m.total - m.srv, 0));
  const W = 520, H = 150, mg = { l: 30, r: 10, t: 10, b: 24 }, nb = 14;
  const max = Math.max(...tot, 1), ancho = max / nb;
  const cuentas = Array(nb).fill(0); tot.forEach((t) => cuentas[Math.min(nb - 1, Math.floor(t / ancho))]++);
  const maxC = Math.max(...cuentas, 1);
  const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Histograma de latencias" });
  const bw = (W - mg.l - mg.r) / nb;
  const g = svg("g", { class: "ax" });
  g.append(svg("line", { x1: mg.l, x2: W - mg.r, y1: H - mg.b, y2: H - mg.b }));
  [0, 0.5, 1].forEach((f) => { const t = svg("text", { x: mg.l + f * (W - mg.l - mg.r), y: H - 8, "text-anchor": f === 0 ? "start" : f === 1 ? "end" : "middle" }); t.textContent = `${Math.round(max * f)} ms`; g.append(t); });
  s.append(g);
  cuentas.forEach((c, k) => {
    if (!c) return;
    const h = (c / maxC) * (H - mg.t - mg.b);
    const r = svg("rect", { x: mg.l + k * bw + 1, y: H - mg.b - h, width: bw - 2, height: h, rx: 2, fill: "var(--accent)" });
    r.addEventListener("mousemove", (ev) => tooltip(ev, `${c} peticiones entre ${Math.round(k * ancho)} y ${Math.round((k + 1) * ancho)} ms`));
    r.addEventListener("mouseleave", ocultarTooltip);
    s.append(r);
  });
  const p95 = percentil(tot, 0.95);
  const xp = mg.l + (p95 / max) * (W - mg.l - mg.r);
  s.append(svg("line", { x1: xp, x2: xp, y1: mg.t, y2: H - mg.b, stroke: "var(--critical)", "stroke-dasharray": "4 3", "stroke-width": 1.5 }));
  const tp = svg("text", { x: Math.min(xp + 4, W - 40), y: mg.t + 10, "font-size": 10, fill: "var(--critical)" }); tp.textContent = "p95"; s.append(tp);
  $("#ch-latencia").replaceChildren(s);
  const pr = (a, p) => `${fmt(percentil(a, p), 0)} ms`;
  $("#lt-stats").innerHTML = [
    [pr(tot, 0.5), "p50 total (navegador)"], [pr(tot, 0.95), "p95 total"], [pr(srv, 0.5), "p50 en el servidor"],
    [pr(red, 0.5), "p50 red + proxy (estimado)"], [`${fmt(muestras.length / dur, 1)} req/s`, "rendimiento (5 en paralelo)"], [`${errores}`, `errores · ${ep === "predict" ? "POST /predict" : "GET /health"}`],
  ].map(([b, t]) => `<div class="stat"><b>${b}</b><span>${t}</span></div>`).join("");
}
$("#btn-latencia").addEventListener("click", pruebaLatencia);

/* --- GitHub (API pública, sin credenciales) --- */
async function cargarGithub() {
  const repo = S.version?.repositorio;
  $("#pl-repo").textContent = repo || "repositorio";
  $("#pl-commit").textContent = S.version?.commit_corto ? `commit ${S.version.commit_corto}` : "—";
  if (!repo) return;
  const caja = $("#gh-commits");
  try {
    const [commits, runs] = await Promise.all([
      fetch(`https://api.github.com/repos/${repo}/commits?per_page=6`).then((r) => (r.ok ? r.json() : Promise.reject(r.status))),
      fetch(`https://api.github.com/repos/${repo}/actions/runs?per_page=10`).then((r) => (r.ok ? r.json() : { workflow_runs: [] })).catch(() => ({ workflow_runs: [] })),
    ]);
    const porSha = Object.fromEntries((runs.workflow_runs || []).map((w) => [w.head_sha, w]));
    const ultimo = (runs.workflow_runs || [])[0];
    if (ultimo) $("#pl-ci").innerHTML = badgeCi(ultimo);
    caja.innerHTML = `<ol class="gh-commits">${commits.map((c) => {
      const run = porSha[c.sha], actual = S.version?.commit && c.sha.startsWith(S.version.commit.slice(0, 7));
      const fecha = new Date(c.commit.author.date).toLocaleString("es-EC", { dateStyle: "medium", timeStyle: "short" });
      return `<li${actual ? ' class="gh-actual"' : ""}><a href="${c.html_url}" target="_blank" rel="noopener"><code>${c.sha.slice(0, 7)}</code></a>
        <div><b>${escapar(c.commit.message.split("\n")[0])}</b><span>${escapar(c.commit.author.name)} · ${fecha}</span></div>
        <div class="gh-tags">${run ? badgeCi(run) : ""}${actual ? '<span class="tag tag-ok">desplegado</span>' : ""}</div></li>`;
    }).join("")}</ol>`;
  } catch (e) {
    caja.innerHTML = `<p class="hint">No se pudo consultar GitHub (${escapar(e)}). La API pública permite 60 consultas por hora y por IP.</p>
      <p class="hint small"><a href="https://github.com/${escapar(repo)}/commits" target="_blank" rel="noopener">Ver historial en GitHub →</a></p>`;
  }
}
function badgeCi(run) {
  const est = run.status !== "completed" ? ["en curso", "tag-run"] : run.conclusion === "success" ? ["CI ✓", "tag-ok"] : [`CI ✗ ${run.conclusion}`, "tag-drift"];
  return `<a class="tag ${est[1]}" href="${run.html_url}" target="_blank" rel="noopener">${est[0]}</a>`;
}
function pintarTablaDeploy() {
  const v = S.version; if (!v) return;
  const filas = [
    ["Entorno", v.entorno], ["Servicio", v.servicio], ["URL pública", v.url_publica || location.origin],
    ["Commit · rama", v.commit ? `${v.commit_corto} · ${v.rama || "—"}` : "local (sin commit de Render)"],
    ["Instancia", v.instancia], ["Puerto interno ($PORT)", v.puerto || "—"],
    ["Python · sistema", `${v.python} · ${v.sistema}`], ["Usuario sin root", v.usuario_no_root == null ? "n/a" : v.usuario_no_root ? "sí (appuser)" : "no"],
    ["Proceso iniciado (UTC)", v.inicio_proceso_utc.replace("T", " ")],
    ["Modelo", `${v.modelo.nombre} · umbral ${Math.round(v.modelo.umbral * 100)} % · sklearn ${v.modelo.version_sklearn}`],
    ["Inferencia", v.modelo.inferencia || "—"],
    ["Entrenado (UTC)", (v.modelo.fecha_entrenamiento || "").replace("T", " ").replace("+00:00", "")],
  ];
  $("#tabla-deploy tbody").innerHTML = filas.map(([k, x]) => `<tr><td>${k}</td><td>${escapar(x)}</td></tr>`).join("");
}

/* ================================================================== pestañas con carga diferida */
const TABS = ["lectura", "lote", "simulacion", "integracion", "nube", "equipo"];
const cargado = new Set();
function alActivar(nombre) {
  if (nombre === "nube") {
    refrescarOps();
    if (!cargado.has("gh")) { cargado.add("gh"); cargarGithub(); pintarTablaDeploy(); }
  }
  if (nombre === "integracion" && !cargado.has("seg")) { cargado.add("seg"); construirChecklist(); }
  programarOps();
}
$$(".tab").forEach((t) => t.addEventListener("click", () => alActivar(t.dataset.tab)));

/* ================================================================== arranque */
async function iniciar() {
  const t0 = performance.now();
  try {
    const health = await api("GET", "/health");
    const primeraMs = performance.now() - t0;
    const [equipo, modelo, escenarios, version] = await Promise.all([
      api("GET", "/equipo"), api("GET", "/modelo"), api("GET", "/escenarios"), api("GET", "/version")]);
    Object.assign(S, { equipo, modelo, escenarios });
    const pill = $("#estado-api"); pill.className = "pill pill-ok"; $("#estado-texto").textContent = `API conectada · ${Math.round(primeraMs)} ms`;
    $("#pill-modelo").textContent = `${health.modelo} · u* = ${fmt(health.umbral_decision)}`;
    pintarDespliegue(version, primeraMs);
    construirSensores(); construirEscenarios(); construirEquipo(); reiniciar(); pintarCodigo();
    $("#api-key").value = leerSesion("api_key");
    $$(".cfg-inicio").forEach((x) => (x.textContent = SIM.cfg.inicio_degradacion));
    $$(".cfg-falla").forEach((x) => (x.textContent = SIM.cfg.dia_falla));
    const tab = location.hash.replace("#", "");
    if (TABS.includes(tab)) { activarTab(tab); alActivar(tab); }
  } catch (e) {
    const pill = $("#estado-api"); pill.className = "pill pill-err"; $("#estado-texto").textContent = "API no disponible";
    $("#hero-estado").textContent = "No se pudo contactar el servicio";
    console.error(e);
  }
}
iniciar();
