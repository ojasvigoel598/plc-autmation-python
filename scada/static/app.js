/* Multi-tank SCADA dashboard - client logic. */
"use strict";

const state = { snapshot: null, trends: null };
let ws = null;
let wsRetry = 0;

const $ = (id) => document.getElementById(id);
const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v)) ? "--" : Number(v).toFixed(d);
const LperS = (v) => fmt((v || 0) * 1000, 1);

/* ------------------------------------------------------------------ */
/* API helpers                                                         */
/* ------------------------------------------------------------------ */
async function post(url, body) {
  try {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body || {}),
    });
    if (!r.ok) {
      const text = await r.text();
      console.warn("POST failed", url, r.status, text);
    }
    return r.ok;
  } catch (e) {
    console.warn("POST error", url, e);
    return false;
  }
}

/* ------------------------------------------------------------------ */
/* Charts                                                              */
/* ------------------------------------------------------------------ */
class TrendChart {
  constructor(canvasId, series, yMin, yMax) {
    this.canvas = $(canvasId);
    this.ctx = this.canvas.getContext("2d");
    this.series = series;   // [{key,label,color,dash,axis,scale}]
    this.yMin = yMin;
    this.yMax = yMax;
    this._resize();
    window.addEventListener("resize", () => { this._resize(); this._draw(); });
  }

  _resize() {
    const dpr = window.devicePixelRatio || 1;
    const rect = this.canvas.parentElement.getBoundingClientRect();
    this.w = Math.max(200, rect.width - 2);
    this.canvas.width = this.w * dpr;
    this.canvas.height = (this.canvas.getAttribute("height") || 150) * dpr;
    this.canvas.style.width = this.w + "px";
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.h = parseInt(this.canvas.getAttribute("height") || "150", 10);
  }

  setData(t, map) {
    this.t = t || [];
    this.map = map || {};
    this._draw();
  }

  _draw() {
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.w, this.h);
    if (!this.t || this.t.length < 2) return;

    const padL = 44, padR = 46, padT = 10, padB = 18;
    const plotW = this.w - padL - padR;
    const plotH = this.h - padT - padB;
    const t0 = this.t[0], t1 = this.t[this.t.length - 1];
    const x = (v) => padL + (v - t0) / Math.max(1e-6, t1 - t0) * plotW;

    // grid + axes
    ctx.strokeStyle = "#1c2430";
    ctx.fillStyle = "#8b98a9";
    ctx.font = "10px monospace";
    ctx.lineWidth = 1;
    const nGrid = 4;
    for (let i = 0; i <= nGrid; i++) {
      const gy = padT + plotH - (i / nGrid) * plotH;
      ctx.beginPath();
      ctx.moveTo(padL, gy);
      ctx.lineTo(padL + plotW, gy);
      ctx.stroke();
      const val = this.yMin + (i / nGrid) * (this.yMax - this.yMin);
      ctx.fillText(val.toFixed(1), 4, gy + 3);
    }
    // time axis labels
    ctx.fillText(fmt(t0, 0) + "s", padL, this.h - 5);
    ctx.fillText(fmt(t1, 0) + "s", padL + plotW - 34, this.h - 5);

    for (const s of this.series) {
      const vals = this.map[s.key];
      if (!vals || vals.length < 2) continue;
      const scale = s.scale || 1;
      const yMin = s.axis === 2 ? 0 : this.yMin;
      const yMax = s.axis === 2 ? (s.max || 100) : this.yMax;
      const y = (v) => padT + plotH - (v * scale - yMin) / (yMax - yMin) * plotH;

      ctx.strokeStyle = s.color;
      ctx.lineWidth = s.width || 1.5;
      ctx.setLineDash(s.dash || []);
      ctx.beginPath();
      for (let i = 0; i < vals.length; i++) {
        const px = x(this.t[i]);
        const py = y(vals[i]);
        if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
      }
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // legend
    let lx = padL;
    ctx.font = "10px sans-serif";
    for (const s of this.series) {
      ctx.fillStyle = s.color;
      ctx.fillRect(lx, 4, 9, 3);
      ctx.fillStyle = "#9fb0c3";
      ctx.fillText(s.label, lx + 12, 8);
      lx += 14 + ctx.measureText(s.label).width;
    }
  }
}

const charts = {};
function initCharts() {
  charts.levels = new TrendChart("cv-levels", [
    { key: "h1", label: "TK-101", color: "#58a6ff" },
    { key: "h2", label: "TK-102", color: "#3fb950" },
    { key: "h3", label: "TK-103", color: "#e3b341" },
  ], 0, 2.0);

  charts.lic101 = new TrendChart("cv-lic101", [
    { key: "lic101_sp", label: "SP", color: "#8b98a9", dash: [5, 4] },
    { key: "lic101_pv", label: "PV", color: "#58a6ff" },
    { key: "lic101_mv", label: "MV%", color: "#3fb950", axis: 2 },
  ], 0, 2.0);

  charts.lic102 = new TrendChart("cv-lic102", [
    { key: "lic102_sp", label: "SP", color: "#8b98a9", dash: [5, 4] },
    { key: "lic102_pv", label: "PV", color: "#58a6ff" },
    { key: "lic102_mv", label: "MV%", color: "#3fb950", axis: 2 },
  ], 0, 2.0);

  charts.flows = new TrendChart("cv-flows", [
    { key: "pump_flow", label: "P-101", color: "#58a6ff", scale: 1000 },
    { key: "v101_flow", label: "XV-101", color: "#3fb950", scale: 1000 },
    { key: "v102_flow", label: "XV-102", color: "#e3b341", scale: 1000 },
    { key: "v103_flow", label: "XV-103", color: "#ff7b72", scale: 1000 },
  ], 0, 20);
}

/* ------------------------------------------------------------------ */
/* Rendering                                                           */
/* ------------------------------------------------------------------ */
function renderHeader(s) {
  $("stat-clock").textContent = "t = " + fmt(s.t, 1) + " s";
  const st = $("stat-state");
  st.textContent = s.state;
  st.dataset.state = s.state;
  const es = $("stat-estop");
  es.dataset.active = s.estop ? "1" : "0";
  es.textContent = s.estop ? "E-STOP!" : "E-STOP";
  $("stat-ws").dataset.up = "1";
}

function setFill(id, pct) {
  const el = $(id);
  const maxH = 226, baseY = 434;
  const h = Math.max(0, Math.min(100, pct)) / 100 * maxH;
  el.setAttribute("y", baseY - h);
  el.setAttribute("height", h);
}

function renderSchematic(s) {
  setFill("fill-101", s.levels_pct["TK-101"]);
  setFill("fill-102", s.levels_pct["TK-102"]);
  setFill("fill-103", s.levels_pct["TK-103"]);

  $("lv-101").textContent = "LT-101 " + fmt(s.levels["TK-101"], 2) + " m";
  $("lv-102").textContent = "LT-102 " + fmt(s.levels["TK-102"], 2) + " m";
  $("lv-103").textContent = "LT-103 " + fmt(s.levels["TK-103"], 2) + " m";

  // pump
  const pump = $("pump-101");
  pump.classList.toggle("running", s.pump.running && !s.faults.pump_tripped);
  pump.classList.toggle("tripped", s.faults.pump_tripped);
  $("pump-speed").textContent = fmt(s.pump.eff, 0) + " %";

  // valves
  for (const [tag, id] of [["XV-101", "valve-101"], ["XV-102", "valve-102"], ["XV-103", "valve-103"]]) {
    const v = s.valves[tag];
    const el = $(id);
    el.classList.toggle("open", v.eff > 2);
    el.classList.toggle("fault", s.faults.valve_stuck[tag]);
    el.classList.toggle("blocked", v.blocked);
  }
  $("xv101-pos").textContent = fmt(s.valves["XV-101"].eff, 0) + " %";
  $("xv102-pos").textContent = fmt(s.valves["XV-102"].eff, 0) + " %";
  $("xv103-pos").textContent = fmt(s.valves["XV-103"].eff, 0) + " %";

  // LIC blocks
  renderLic("LIC-101", "lic101", s);
  renderLic("LIC-102", "lic102", s);

  // alarm dots
  const alarmTags = new Set((s.alarms || []).map((a) => a.tag));
  $("dot-101").className = "alarm-dot" + (alarmTags.has("LSHH-101") ? " hihi" : alarmTags.has("LSH-101") ? " hi" : "");
  $("dot-102").className = "alarm-dot" + (alarmTags.has("LSHH-102") ? " hihi" : alarmTags.has("LSH-102") ? " hi" : "");
  $("dot-103").className = "alarm-dot" + (alarmTags.has("LSHH-103") ? " hihi" : alarmTags.has("LSH-103") ? " hi" : "");

  // flow animation on the main transfer line + tank risers
  const flowing = (s.flows["P-101"] || 0) > 0.0005;
  const main = $("pipe-main");
  main.classList.toggle("flowing", flowing);
  const segs = {
    "g-tk101": [s.flows["P-101"], s.flows["XV-101"]],
    "g-tk102": [s.flows["XV-101"], s.flows["XV-102"]],
    "g-tk103": [s.flows["XV-102"], s.flows["XV-103"]],
  };
  for (const [gid, flows] of Object.entries(segs)) {
    const lines = document.querySelectorAll("#" + gid + " > line.pipe");
    lines.forEach((line, i) => {
      const f = flows[i] || 0;
      line.classList.toggle("flowing", f > 0.0005);
    });
  }
}

function renderLic(loopTag, prefix, s) {
  const p = s.pids[loopTag];
  if (!p) return;
  $(prefix + "-sp").textContent = "SP " + fmt(p.sp, 2) + " m";
  $(prefix + "-pv").textContent = "PV " + fmt(p.pv, 2) + " m";
  $(prefix + "-mv").textContent = "MV " + fmt(p.mv, 0) + " %";
  const modeEl = $(prefix + "-mode");
  modeEl.textContent = p.mode + (p.saturated ? " (SAT)" : "");
  modeEl.classList.toggle("manual", p.mode !== "AUTO");
  const box = $(prefix === "lic101" ? "lic-101" : "lic-102");
  box.classList.toggle("manual", p.mode !== "AUTO");
}

function renderControls(s) {
  for (const loop of ["LIC-101", "LIC-102"]) {
    const n = loop === "LIC-101" ? "101" : "102";
    const p = s.pids[loop];
    const modeBtn = $("mode-" + n);
    modeBtn.textContent = p.mode;
    modeBtn.classList.toggle("manual", p.mode !== "AUTO");
    $("readout-" + n).textContent =
      "PV " + fmt(p.pv, 2) + " m \u00b7 SP " + fmt(p.sp, 2) + " m \u00b7 MV " + fmt(p.mv, 0) + " %";
    syncInput("sp-" + n, p.sp);
    syncInput("kp-" + n, p.kp);
    syncInput("ki-" + n, p.ki);
    syncInput("kd-" + n, p.kd);
    if (loop === "LIC-101") {
      syncInput("mmv-101", s.manual_pump);
      $("mmv-101-o").textContent = fmt(s.manual_pump, 0);
    } else {
      syncInput("mmv-102", p.mv);
      $("mmv-102-o").textContent = fmt(p.mv, 0);
    }
    // manual MV slider only meaningful in MANUAL mode
    const slider = $(loop === "LIC-101" ? "mmv-101" : "mmv-102");
    slider.disabled = s.loop_mode[loop] !== "MANUAL";
  }
  syncInput("xv-102", s.valves["XV-102"].cmd);
  syncInput("xv-103", s.valves["XV-103"].cmd);
  $("xv-102-o").textContent = fmt(s.valves["XV-102"].cmd, 0);
  $("xv-103-o").textContent = fmt(s.valves["XV-103"].cmd, 0);

  // fault panel reflects current state
  $("f-pump-trip").textContent = s.faults.pump_tripped ? "Pump tripped" : "Pump trip";
  $("f-pump-trip").disabled = s.faults.pump_tripped;
  $("f-xv102-stick").textContent = s.faults.valve_stuck["XV-102"] ? "XV-102 stuck" : "Stick XV-102";
}

function syncInput(id, value) {
  const el = $(id);
  if (document.activeElement === el) return;   // don't clobber typing
  el.value = Number(value).toFixed(el.step && el.step.includes(".") ? 2 : 0);
}

function renderAlarms(s) {
  const alarms = s.alarms || [];
  const count = $("alarm-count");
  count.textContent = alarms.length + " active";
  count.classList.toggle("has-alarm", alarms.length > 0);

  const body = $("alarm-body");
  if (alarms.length === 0) {
    body.innerHTML = '<tr><td colspan="6" style="color:#5b6b7d">No active alarms</td></tr>';
  } else {
    body.innerHTML = alarms.map((a) => {
      const ack = a.state === "ACTIVE"
        ? `<button class="ack-btn" data-ack="${a.tag}">ACK</button>` : "";
      return `<tr>
        <td><span class="badge ${a.state}">${a.state}</span></td>
        <td style="font-family:monospace">${a.tag}</td>
        <td class="pri-${a.priority}">${a.priority}</td>
        <td>${a.message}</td>
        <td style="font-family:monospace">${fmt(a.t_raise, 1)}s</td>
        <td>${ack}</td>
      </tr>`;
    }).join("");
  }
  body.querySelectorAll("[data-ack]").forEach((b) =>
    b.addEventListener("click", () => post("/api/control/ack", { tag: b.dataset.ack })));

  const history = (s.alarm_history || []).slice().reverse();
  $("event-log").innerHTML = history.map((a) =>
    `<div><b style="color:${a.priority === "CRITICAL" ? "#ff7b72" : a.priority === "HIGH" ? "#e3b341" : "#8b98a9"}">${a.tag}</b> ${a.message} <span style="opacity:.6">(${a.state} @ ${fmt(a.t_raise, 1)}s)</span></div>`
  ).join("");
}

function render(s) {
  renderHeader(s);
  renderSchematic(s);
  renderControls(s);
  renderAlarms(s);
}

/* ------------------------------------------------------------------ */
/* WebSocket                                                           */
/* ------------------------------------------------------------------ */
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => { wsRetry = 0; $("stat-ws").dataset.up = "1"; };
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type === "state") {
      state.snapshot = msg.data;
      render(msg.data);
    } else if (msg.type === "trends") {
      state.trends = msg.data;
      const td = msg.data;
      for (const c of Object.values(charts)) c.setData(td.t, td.series);
    }
  };
  ws.onclose = () => {
    $("stat-ws").dataset.up = "0";
    wsRetry = Math.min(wsRetry + 1, 10);
    setTimeout(connect, 1000 * wsRetry);
  };
  ws.onerror = () => ws.close();
}

/* ------------------------------------------------------------------ */
/* Control bindings                                                    */
/* ------------------------------------------------------------------ */
function bindLoop(n) {
  const loop = "LIC-" + n;
  $("mode-" + n).addEventListener("click", async () => {
    const cur = state.snapshot?.pids?.[loop]?.mode;
    const next = cur === "AUTO" ? "MANUAL" : "AUTO";
    const manualMv = n === "101" ? parseFloat($("mmv-101").value) : parseFloat($("mmv-102").value);
    await post("/api/control/mode", { tag: loop, mode: next, manual_mv: manualMv });
  });
  $("sp-" + n).addEventListener("change", () =>
    post("/api/control/setpoint", { tag: loop, value: parseFloat($("sp-" + n).value) }));
  $("kp-" + n).addEventListener("change", () =>
    post("/api/control/tuning", { tag: loop, kp: parseFloat($("kp-" + n).value) }));
  $("ki-" + n).addEventListener("change", () =>
    post("/api/control/tuning", { tag: loop, ki: parseFloat($("ki-" + n).value) }));
  $("kd-" + n).addEventListener("change", () =>
    post("/api/control/tuning", { tag: loop, kd: parseFloat($("kd-" + n).value) }));
  $("mmv-" + n).addEventListener("change", () =>
    post("/api/control/mode", { tag: loop, mode: "MANUAL", manual_mv: parseFloat($("mmv-" + n).value) }));
  $("mmv-" + n).addEventListener("input", () => $("mmv-" + n + "-o").textContent = $("mmv-" + n).value);
}

function bindControls() {
  $("btn-start").addEventListener("click", () => post("/api/control/start"));
  $("btn-stop").addEventListener("click", () => post("/api/control/stop"));
  $("btn-reset").addEventListener("click", () => post("/api/control/reset"));
  $("btn-ackall").addEventListener("click", () => post("/api/control/ack", {}));
  $("btn-estop").addEventListener("click", async () => {
    const active = $("btn-estop").dataset.active !== "1";
    $("btn-estop").dataset.active = active ? "1" : "0";
    $("btn-estop").textContent = active ? "E-STOP!" : "E-STOP";
    await post("/api/control/estop", { active });
  });

  bindLoop("101");
  bindLoop("102");

  $("xv-102").addEventListener("change", () => post("/api/control/manual_valve", { tag: "XV-102", position: parseFloat($("xv-102").value) }));
  $("xv-102").addEventListener("input", () => $("xv-102-o").textContent = $("xv-102").value);
  $("xv-103").addEventListener("change", () => post("/api/control/manual_valve", { tag: "XV-103", position: parseFloat($("xv-103").value) }));
  $("xv-103").addEventListener("input", () => $("xv-103-o").textContent = $("xv-103").value);

  for (const [sel, tag] of [["f-lt101", "LT-101"], ["f-lt102", "LT-102"], ["f-lt103", "LT-103"]]) {
    $(sel).addEventListener("change", async () => {
      const fault = $(sel).value;
      if (fault === "OK") await post("/api/faults/sensor/clear", { tag });
      else await post("/api/faults/sensor", { tag, fault });
    });
  }
  $("f-pump-trip").addEventListener("click", () => post("/api/faults/pump/trip"));
  $("f-pump-reset").addEventListener("click", () => post("/api/faults/pump/reset"));
  $("f-xv102-stick").addEventListener("click", () => post("/api/faults/valve/stick", { tag: "XV-102" }));
  $("f-xv102-unstick").addEventListener("click", () => post("/api/faults/valve/unstick", { tag: "XV-102" }));
  $("f-block").addEventListener("click", () => post("/api/faults/block", { tag: "XV-102" }));
  $("f-unblock").addEventListener("click", () => post("/api/faults/unblock", { tag: "XV-102" }));
  $("f-disturb").addEventListener("click", () => post("/api/faults/disturbance", { tank: "TK-102", flow_m3s: 0.005, duration: 40 }));
}

/* ------------------------------------------------------------------ */
initCharts();
bindControls();
connect();
