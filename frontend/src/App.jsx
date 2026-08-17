import React, { Component, useMemo, useState } from "react";
import { useSimulation, interlockReason } from "./sim.js";
import PlantScene from "./PlantScene.jsx";
import TrendChart from "./TrendChart.jsx";

/* Keeps a single panel's failure from blanking the whole HMI. */
class ErrorBoundary extends Component {
  state = { error: null };
  static getDerivedStateFromError(error) {
    return { error };
  }
  componentDidCatch(error, info) {
    console.error("Panel error:", error, info);
  }
  render() {
    if (this.state.error) {
      return (
        <div className="tc-empty">
          Panel error: {String(this.state.error?.message || this.state.error)}
        </div>
      );
    }
    return this.props.children;
  }
}

const TANK_ORDER = ["TK-101", "TK-102", "TK-103"];
const VALVE_ORDER = ["XV-101", "XV-102", "XV-103"];
const LO = 10, HI = 85, HIHI = 95;

const L_PER_S = (m3s) => ((m3s || 0) * 1000).toFixed(2);

function stateClass(state) {
  if (state === "RUNNING") return "run";
  if (state === "STARTING" || state === "STOPPING") return "warn";
  if (state === "FAULTED" || state === "E_STOPPED") return "fault";
  return "idle";
}

function fmtTime(t) {
  if (t == null) return "--:--";
  const m = Math.floor(t / 60);
  const s = Math.floor(t % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function levelColor(pct) {
  if (pct >= HIHI) return "crit";
  if (pct >= HI) return "warn";
  if (pct <= LO) return "warn";
  return "";
}

/* A number input that commits on blur / Enter rather than every keystroke. */
function CommitField({ label, value, step = "any", min, max, onCommit, disabled }) {
  const [draft, setDraft] = useState(null);
  const editing = draft !== null;
  const display = editing ? draft : value;
  return (
    <label className="field">
      <span>{label}</span>
      <input
        type="number"
        step={step}
        min={min}
        max={max}
        value={display ?? ""}
        disabled={disabled}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => {
          const n = parseFloat(draft);
          if (draft !== null && draft !== "" && !Number.isNaN(n)) onCommit(n);
          setDraft(null);
        }}
        onKeyDown={(e) => { if (e.key === "Enter") e.target.blur(); }}
      />
    </label>
  );
}

function Dot({ tone }) {
  return <span className={`dot ${tone || "gray"}`} />;
}

/* ============================================================ */
/* Top bar                                                       */
/* ============================================================ */
function TopBar({ connected, state, t, scan }) {
  const plc = state?.state || "IDLE";
  const hasAlarm = (state?.alarms || []).some((a) => a.state === "ACTIVE");
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark">⛭</span>
        <div>
          <h1>Multi-Tank PLC Digital Twin</h1>
          <p className="sub">3-tank cascade · ISA-88 state machine · IEC 61131-3 · RK4 process model</p>
        </div>
      </div>
      <span className={`chip ${connected ? "ok" : "bad"}`}>
        {connected ? "● LINK OK" : "○ RECONNECTING…"}
      </span>
      <span className={`chip ${stateClass(plc)}`}>{plc}</span>
      <span className={`chip ${hasAlarm ? "fault" : "ok"}`}>
        {hasAlarm ? "ALARMS" : "NO ALARM"}
      </span>
      <span className="chip">T+{fmtTime(t)}</span>
      <span className="chip">SCAN {scan ?? 0}</span>
    </header>
  );
}

/* ============================================================ */
/* Overview tab                                                  */
/* ============================================================ */
function Overview({ config, state, selected, onSelect }) {
  const tanks = config?.tanks || [];
  const valves = config?.valves || [];
  const flow = state?.flows || {};
  const pump = state?.pump || {};

  return (
    <div>
      <div className="panel">
        <h3>Operating state</h3>
        <div className="kv"><span className="k">PLC state</span><span className="v">{state?.state || "—"}</span></div>
        <div className="kv"><span className="k">E-stop</span><span className="v crit">{state?.estop ? "ACTIVE" : "clear"}</span></div>
        <div className="kv"><span className="k">Total feed</span><span className="v">{L_PER_S(flow["P-101"])} L/s</span></div>
        <div className="kv"><span className="k">Discharge</span><span className="v">{L_PER_S(flow["XV-103"])} L/s</span></div>
        {interlockReason(state) && (
          <div className="override red">⛔ {interlockReason(state)}</div>
        )}
      </div>

      <div className="panel">
        <h3>Tanks</h3>
        <div className="equip-list">
          {tanks.map((tk) => {
            const pct = state?.levels_pct?.[tk.tag] ?? 0;
            const lvl = state?.levels?.[tk.tag] ?? 0;
            return (
              <div
                key={tk.tag}
                className={`equip-row ${selected === tk.tag ? "selected" : ""}`}
                onClick={() => onSelect(tk.tag)}
              >
                <span className="tag">{tk.tag}</span>
                <span className="val">{lvl.toFixed(2)} m</span>
                <div className="levelbar" style={{ width: "46%" }}>
                  <i className={levelColor(pct)} style={{ width: `${Math.max(0, Math.min(100, pct))}%` }} />
                </div>
                <span className="val">{pct.toFixed(0)} %</span>
              </div>
            );
          })}
        </div>
      </div>

      <div className="panel">
        <h3>Pumps &amp; valves</h3>
        <div className="equip-list">
          <div className={`equip-row ${selected === "P-101" ? "selected" : ""}`} onClick={() => onSelect("P-101")}>
            <Dot tone={pump.running ? "green" : (state?.faults?.pump_tripped ? "red" : "gray")} />
            <span className="tag">P-101</span>
            <span className="val">{pump.eff?.toFixed(0) ?? 0} %</span>
          </div>
          {valves.map((v) => {
            const vv = state?.valves?.[v.tag] || {};
            const stuck = state?.faults?.valve_stuck?.[v.tag];
            const tone = stuck ? "red" : vv.blocked ? "amber" : vv.eff > 5 ? "green" : "gray";
            return (
              <div
                key={v.tag}
                className={`equip-row ${selected === v.tag ? "selected" : ""}`}
                onClick={() => onSelect(v.tag)}
              >
                <Dot tone={tone} />
                <span className="tag">{v.tag}</span>
                <span className="val">{Math.round(vv.eff ?? 0)} %</span>
                <span className="val">{L_PER_S(flow[v.tag])} L/s</span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/* ============================================================ */
/* Controls tab                                                 */
/* ============================================================ */
function LoopFaceplate({ tag, pid, mode, disabled, send, config }) {
  const desc = { "LIC-101": "TK-101 level → P-101 speed", "LIC-102": "TK-102 level → XV-101" }[tag];
  const manual = mode === "MANUAL";
  return (
    <div className="faceplate">
      <div className="fp-head">
        <span className="fp-tag">{tag}</span>
        <span className="fp-desc">{desc}</span>
        <button
          className={`fp-mode ${manual ? "manual" : ""}`}
          disabled={disabled}
          onClick={() =>
            send("/api/control/mode", {
              tag,
              mode: manual ? "AUTO" : "MANUAL",
              manual_mv: pid?.mv ?? 0,
            })
          }
        >
          {manual ? "MAN" : "AUTO"}
        </button>
      </div>
      <div className="fp-big">
        <div className="cell"><div className="lbl">PV (m)</div><div className="pv">{pid?.pv?.toFixed(2) ?? "—"}</div></div>
        <div className="cell"><div className="lbl">SP (m)</div><div className="pv">{pid?.sp?.toFixed(2) ?? "—"}</div></div>
        <div className="cell"><div className="lbl">MV (%)</div><div className="mv">{pid?.mv?.toFixed(0) ?? "—"}</div></div>
      </div>
      <div className="fp-grid">
        <CommitField label="Setpoint (m)" value={pid?.sp?.toFixed(2)} step="0.05" min="0" max={config?.tank_height ?? 2}
          disabled={disabled} onCommit={(n) => send("/api/control/setpoint", { tag, value: n })} />
        <CommitField label="Kp" value={pid?.kp} disabled={disabled} onCommit={(n) => send("/api/control/tuning", { tag, kp: n })} />
        <CommitField label="Ki" value={pid?.ki} disabled={disabled} onCommit={(n) => send("/api/control/tuning", { tag, ki: n })} />
        <CommitField label="Kd" value={pid?.kd} disabled={disabled} onCommit={(n) => send("/api/control/tuning", { tag, kd: n })} />
      </div>
      {pid?.saturated && <div className="override">Output saturated at {Math.round(pid.mv)} %</div>}
    </div>
  );
}

function Controls({ config, state, send }) {
  const running = state?.state === "RUNNING" || state?.state === "STARTING";
  const plc = state?.state;
  const estop = !!state?.estop;
  const pids = state?.pids || {};
  const loopMode = state?.loop_mode || {};
  const pump = state?.pump || {};
  const valves = state?.valves || {};
  const reason = interlockReason(state);

  const lic101Manual = loopMode["LIC-101"] === "MANUAL";

  return (
    <div>
      <div className="panel">
        <h3>Plant commands</h3>
        <div className="row">
          <button className="btn start" disabled={running} onClick={() => send("/api/control/start")}>START</button>
          <button className="btn stop" disabled={!running} onClick={() => send("/api/control/stop")}>STOP</button>
          <button className="btn" disabled={!(plc === "FAULTED" || plc === "E_STOPPED")}
            onClick={() => send("/api/control/reset")}>RESET</button>
          <button
            className={`btn estop ${estop ? "on" : ""}`}
            onClick={() => send("/api/control/estop", { active: !estop })}
          >
            {estop ? "E-STOP ACTIVE — RELEASE" : "E-STOP"}
          </button>
        </div>
        {estop && <div className="override red">E-STOP active. Release E-stop, then press RESET to return to IDLE.</div>}
        {reason && <div className="override red">⛔ PLC interlock: {reason} — commands are overridden to fail-safe.</div>}
      </div>

      <div className="panel">
        <h3>PID loops</h3>
        <LoopFaceplate tag="LIC-101" pid={pids["LIC-101"]} mode={loopMode["LIC-101"]} config={config} disabled={estop} send={send} />
        <LoopFaceplate tag="LIC-102" pid={pids["LIC-102"]} mode={loopMode["LIC-102"]} config={config} disabled={estop} send={send} />
      </div>

      <div className="panel">
        <h3>Manual actuators</h3>
        <label className="field" style={{ marginBottom: 8 }}>
          <span>
            P-101 speed{" "}
            {lic101Manual
              ? `request ${Math.round(state?.manual_pump ?? 0)} % · output ${Math.round(pump.cmd ?? 0)} % · actual ${Math.round(pump.eff ?? 0)} %`
              : `(AUTO) output ${Math.round(pump.cmd ?? 0)} %`}
          </span>
          <input
            type="range" min="0" max="100" step="1"
            value={lic101Manual ? Math.round(state?.manual_pump ?? 0) : Math.round(pump.cmd ?? 0)}
            disabled={!lic101Manual || estop}
            onChange={(e) => send("/api/control/manual_pump", { speed: parseFloat(e.target.value) })}
          />
        </label>
        {["XV-102", "XV-103"].map((tag) => {
          const v = valves[tag] || {};
          const requested = v.manual ?? 50;
          const enforced = Math.round(v.cmd ?? 0);
          const actual = Math.round(v.eff ?? 0);
          return (
            <label className="field" key={tag} style={{ marginBottom: 8 }}>
              <span>
                {tag} request {Math.round(requested)} % · output {enforced} % · actual {actual} %{" "}
                {v.blocked ? "(BLOCKED)" : ""}
                {enforced !== Math.round(requested) ? " ⛔ interlocked" : ""}
              </span>
              <input
                type="range" min="0" max="100" step="1"
                value={Math.round(requested)}
                disabled={estop}
                onChange={(e) => send("/api/control/manual_valve", { tag, position: parseFloat(e.target.value) })}
              />
            </label>
          );
        })}
        <div className="kv"><span className="k">XV-101 (loop MV)</span><span className="v">{Math.round(valves["XV-101"]?.eff ?? 0)} %</span></div>
      </div>
    </div>
  );
}

/* ============================================================ */
/* Alarms tab                                                   */
/* ============================================================ */
function Alarms({ state, send }) {
  const alarms = state?.alarms || [];
  const history = state?.alarm_history || [];
  return (
    <div>
      <div className="panel">
        <h3>Active alarms</h3>
        <div className="row" style={{ marginBottom: 8 }}>
          <button className="btn small" onClick={() => send("/api/control/ack", {})}>ACKNOWLEDGE ALL</button>
        </div>
        {alarms.length === 0 ? (
          <div className="tc-empty">No active alarms</div>
        ) : (
          <table className="alarm">
            <thead><tr><th>Tag</th><th>Message</th><th>State</th><th></th></tr></thead>
            <tbody>
              {alarms.map((a) => (
                <tr key={a.tag}>
                  <td className={`pri-${a.priority}`}>{a.tag}</td>
                  <td>{a.message}</td>
                  <td><span className={`badge ${a.state}`}>{a.state}</span></td>
                  <td>
                    {a.state === "ACTIVE" && (
                      <button className="ack" onClick={() => send("/api/control/ack", { tag: a.tag })}>ACK</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <div className="panel">
        <h3>Alarm history</h3>
        <div className="history">
          {history.length === 0 ? "No history yet" :
            history.slice().reverse().map((a, i) => (
              <div key={i}>
                <span className={`pri-${a.priority}`}>{a.tag}</span> — {a.message}{" "}
                <span>({fmtTime(a.t_raise)} · {a.state})</span>
              </div>
            ))}
        </div>
      </div>
    </div>
  );
}

/* ============================================================ */
/* Trends tab                                                   */
/* ============================================================ */
function Trends({ trends }) {
  if (!trends || !trends.t || trends.t.length < 2) {
    return <div className="tc-empty">Waiting for trend data…</div>;
  }
  return (
    <div>
      <TrendChart
        title="Tank levels"
        rangeLabel="0 – 2.0 m"
        data={trends}
        yMin={0} yMax={2}
        spec={[
          { key: "h1", label: "TK-101", color: "#3fa9f5" },
          { key: "h2", label: "TK-102", color: "#3fb950" },
          { key: "h3", label: "TK-103", color: "#e3b341" },
        ]}
      />
      <TrendChart
        title="LIC-101 — TK-101 level"
        rangeLabel="m / %"
        data={trends}
        yMin={0} yMax={2}
        spec={[
          { key: "lic101_pv", label: "PV", color: "#3fa9f5" },
          { key: "lic101_sp", label: "SP", color: "#e3b341", dash: "6 3" },
          { key: "lic101_mv", label: "MV", color: "#3fb950", axis: 2 },
        ]}
      />
      <TrendChart
        title="LIC-102 — TK-102 level"
        rangeLabel="m / %"
        data={trends}
        yMin={0} yMax={2}
        spec={[
          { key: "lic102_pv", label: "PV", color: "#3fa9f5" },
          { key: "lic102_sp", label: "SP", color: "#e3b341", dash: "6 3" },
          { key: "lic102_mv", label: "MV", color: "#3fb950", axis: 2 },
        ]}
      />
      <TrendChart
        title="Flows"
        rangeLabel="0 – 25 L/s"
        data={trends}
        yMin={0} yMax={25}
        spec={[
          { key: "pump_flow", label: "P-101", color: "#3fa9f5", scale: 1000 },
          { key: "v101_flow", label: "XV-101", color: "#3fb950", scale: 1000 },
          { key: "v102_flow", label: "XV-102", color: "#e3b341", scale: 1000 },
          { key: "v103_flow", label: "XV-103", color: "#ff7b72", scale: 1000 },
        ]}
      />
    </div>
  );
}

/* ============================================================ */
/* Faults tab                                                   */
/* ============================================================ */
function Faults({ state, send }) {
  const sensors = state?.sensors || {};
  const faults = state?.faults || {};
  const FAULT_TYPES = ["STUCK", "FAIL_HIGH", "FAIL_LOW", "DRIFT", "NAN"];
  const [sensorTag, setSensorTag] = useState("LT-101");
  const [sensorType, setSensorType] = useState("STUCK");
  const [valveTag, setValveTag] = useState("XV-102");
  const [distTank, setDistTank] = useState("TK-102");
  const [distFlow, setDistFlow] = useState(4); // L/s

  return (
    <div>
      <div className="panel">
        <h3>Sensor faults</h3>
        {["LT-101", "LT-102", "LT-103"].map((t) => (
          <div className="kv" key={t}>
            <span className="k">{t}</span>
            <span className={`v ${sensors[t] !== "OK" ? "crit" : ""}`}>{sensors[t]}</span>
          </div>
        ))}
        <div className="fgrid" style={{ marginTop: 8 }}>
          <select value={sensorTag} onChange={(e) => setSensorTag(e.target.value)}>
            {["LT-101", "LT-102", "LT-103"].map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <select value={sensorType} onChange={(e) => setSensorType(e.target.value)}>
            {FAULT_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
        <div className="row" style={{ marginTop: 8 }}>
          <button className="btn small danger" onClick={() => send("/api/faults/sensor", { tag: sensorTag, fault: sensorType })}>
            Inject {sensorType}
          </button>
          <button className="btn small" onClick={() => send("/api/faults/sensor/clear", { tag: sensorTag })}>Clear</button>
        </div>
      </div>

      <div className="panel">
        <h3>Pump fault</h3>
        <div className="kv"><span className="k">P-101</span><span className={`v ${faults.pump_tripped ? "crit" : ""}`}>{faults.pump_tripped ? "TRIPPED" : "OK"}</span></div>
        <div className="row" style={{ marginTop: 8 }}>
          <button className="btn small danger" onClick={() => send("/api/faults/pump/trip")}>Trip P-101</button>
          <button className="btn small" onClick={() => send("/api/faults/pump/reset")}>Reset pump</button>
        </div>
      </div>

      <div className="panel">
        <h3>Valve faults</h3>
        {VALVE_ORDER.map((t) => {
          const stuck = faults.valve_stuck?.[t];
          const blocked = faults.blocked?.[t];
          return (
            <div className="kv" key={t}>
              <span className="k">{t}</span>
              <span className={`v ${stuck || blocked ? "crit" : ""}`}>
                {stuck ? "STUCK" : ""}{stuck && blocked ? " + " : ""}{blocked ? "BLOCKED" : (!stuck && !blocked ? "OK" : "")}
              </span>
            </div>
          );
        })}
        <div className="row" style={{ marginTop: 8 }}>
          <select value={valveTag} onChange={(e) => setValveTag(e.target.value)}>
            {VALVE_ORDER.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <button className="btn small danger" onClick={() => send("/api/faults/valve/stick", { tag: valveTag })}>Stick</button>
          <button className="btn small" onClick={() => send("/api/faults/valve/unstick", { tag: valveTag })}>Unstick</button>
          <button className="btn small warn" onClick={() => send("/api/faults/block", { tag: valveTag })}>Block</button>
          <button className="btn small" onClick={() => send("/api/faults/unblock", { tag: valveTag })}>Unblock</button>
        </div>
      </div>

      <div className="panel">
        <h3>Process disturbance (leak)</h3>
        <div className="fgrid">
          <select value={distTank} onChange={(e) => setDistTank(e.target.value)}>
            {TANK_ORDER.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <CommitField label="Leak (L/s)" value={distFlow} step="0.5" min="0" max="50"
            onCommit={(n) => setDistFlow(n)} />
        </div>
        <div className="row" style={{ marginTop: 8 }}>
          <button className="btn small warn" onClick={() => send("/api/faults/disturbance", { tank: distTank, flow_m3s: distFlow / 1000, duration: 60 })}>
            Inject leak 60 s
          </button>
        </div>
        {faults.disturbance_active && <div className="override">Disturbance in progress…</div>}
      </div>
    </div>
  );
}

/* ============================================================ */
/* App root                                                      */
/* ============================================================ */
export default function App() {
  const { config, state, trends, connected, sendCommand } = useSimulation();
  const [tab, setTab] = useState("Overview");
  const [selected, setSelected] = useState(null);
  const send = useMemo(() => sendCommand, [sendCommand]);

  const estop = !!state?.estop;

  return (
    <div className="app">
      <TopBar connected={connected} state={state} t={state?.t} scan={state?.scan_count} />
      <div className="main">
        <div className="viewport">
          {estop && <div className="banner">⚠ EMERGENCY STOP ACTIVE</div>}
          {!config ? (
            <div className="vp-hint">Connecting to simulation…</div>
          ) : (
            <PlantScene config={config} state={state} selected={selected} onSelect={setSelected} />
          )}
        </div>
        <aside className="sidebar">
          <div className="tabs">
            {["Overview", "Controls", "Alarms", "Trends", "Faults"].map((t) => (
              <button key={t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
                {t}
              </button>
            ))}
          </div>
          <div className="side-content">
            <ErrorBoundary key={tab}>
              {tab === "Overview" && (
                <Overview config={config} state={state} selected={selected} onSelect={setSelected} />
              )}
              {tab === "Controls" && <Controls config={config} state={state} send={send} />}
              {tab === "Alarms" && <Alarms state={state} send={send} />}
              {tab === "Trends" && <Trends trends={trends} />}
              {tab === "Faults" && <Faults state={state} send={send} />}
            </ErrorBoundary>
          </div>
        </aside>
      </div>
    </div>
  );
}
