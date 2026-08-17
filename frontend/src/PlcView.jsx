import React from "react";

/* IEC-61131 style I/O image + function-block HMI.  Everything rendered comes
   straight from state.plc (the live snapshot), never invented client-side. */

const DI_LABELS = {
  "I0.0_ESTOP": "E-stop",
  "I0.1_START": "Start button",
  "I0.2_STOP": "Stop button",
  "I0.3_RESET": "Reset button",
  "I0.4_PUMP_FB": "Pump run feedback",
};
const DQ_LABELS = {
  "Q0.0_PUMP": "Pump run coil",
  "Q0.1_HORN": "Alarm horn",
  "Q0.2_LIGHT": "Alarm light",
  "Q0.3_STATUS": "Status light",
};
const AQ_LABELS = {
  "P-101": "P-101 speed",
  "XV-101": "XV-101 (LIC-102 MV)",
  "XV-102": "XV-102 (manual)",
  "XV-103": "XV-103 (manual)",
};
const PROGRAM = [
  "1. Sensor plausibility — NaN / implausible slew-rate detection",
  "2. Level alarms — HIHI / HI / LO from the input image",
  "3. ISA-88 operating state machine — IDLE/STARTING/RUNNING/STOPPING/FAULTED/E_STOPPED",
  "4. PID loops — LIC-101 (TK-101→P-101), LIC-102 (TK-102→XV-101)",
  "5. Interlocks & output mapping — E-stop > HIHI > loop outputs",
  "6. Watchdogs — pump run-feedback (2 s), valve travel deviation (2 s)",
  "7. Alarm coil mapping — horn (HIGH+), light (WARNING+)",
];

function Lamp({ on, danger }) {
  return <span className={`dot ${on ? (danger ? "red" : "green") : "gray"}`} />;
}

function Row({ label, value, tone, lamp, lampDanger }) {
  return (
    <div className="kv">
      <span className="k">
        {lamp !== undefined && <Lamp on={lamp} danger={lampDanger} />}
        {label}
      </span>
      <span className={`v ${tone || ""}`}>{value}</span>
    </div>
  );
}

function FbRow({ name, et, pt, on, danger }) {
  const frac = pt ? Math.min(100, ((et || 0) / pt) * 100) : 0;
  return (
    <div className="kv">
      <span className="k"><Lamp on={!!on} danger={danger} />{name}</span>
      <span className="v">{(et || 0).toFixed(1)} / {pt} s
        <span className="fb-bar"><i style={{ width: `${frac}%` }} /></span>
      </span>
    </div>
  );
}

export default function PlcView({ state }) {
  const plc = state?.plc;
  if (!plc) return <div className="tc-empty">Waiting for PLC data…</div>;
  const { ai = {}, di = {}, aq = {}, dq = {}, fbs = {} } = plc;
  const il = state?.interlocks || {};
  const sensorLatch = fbs.sensor_fault_latches || {};

  return (
    <div>
      <div className="panel">
        <h3>Scan</h3>
        <Row label="Operating state" value={plc.state} lamp={plc.state === "RUNNING"} lampDanger={plc.state === "FAULTED" || plc.state === "E_STOPPED"} />
        <Row label="Scan count" value={plc.scan_count} />
        <Row label="Scan cycle" value="100 ms" />
        <Row label="Loop modes" value={`LIC-101 ${plc.loop_mode?.["LIC-101"]} · LIC-102 ${plc.loop_mode?.["LIC-102"]}`} />
      </div>

      <div className="panel">
        <h3>Digital inputs — I</h3>
        {Object.entries(DI_LABELS).map(([tag, label]) => (
          <Row key={tag} label={`${tag} · ${label}`} value={di[tag] ? "ON" : "off"}
            lamp={!!di[tag]} lampDanger={tag === "I0.0_ESTOP"} />
        ))}
      </div>

      <div className="panel">
        <h3>Analog inputs — AI</h3>
        <Row label="LT-101 · TK-101 level" value={ai["LT-101"] != null ? `${ai["LT-101"].toFixed(3)} m` : "—"} />
        <Row label="LT-102 · TK-102 level" value={ai["LT-102"] != null ? `${ai["LT-102"].toFixed(3)} m` : "—"} />
        <Row label="LT-103 · TK-103 level" value={ai["LT-103"] != null ? `${ai["LT-103"].toFixed(3)} m` : "—"} />
        <Row label="XV-101 feedback" value={ai["XV-101_FB"] != null ? `${ai["XV-101_FB"].toFixed(1)} %` : "—"} />
        <Row label="XV-102 feedback" value={ai["XV-102_FB"] != null ? `${ai["XV-102_FB"].toFixed(1)} %` : "—"} />
        <Row label="XV-103 feedback" value={ai["XV-103_FB"] != null ? `${ai["XV-103_FB"].toFixed(1)} %` : "—"} />
      </div>

      <div className="panel">
        <h3>Digital outputs — Q</h3>
        {Object.entries(DQ_LABELS).map(([tag, label]) => (
          <Row key={tag} label={`${tag} · ${label}`} value={dq[tag] ? "ON" : "off"} lamp={!!dq[tag]} />
        ))}
      </div>

      <div className="panel">
        <h3>Analog outputs — AQ</h3>
        {Object.entries(AQ_LABELS).map(([tag, label]) => (
          <Row key={tag} label={`${tag} · ${label}`} value={aq[tag] != null ? `${aq[tag].toFixed(1)} %` : "—"} />
        ))}
      </div>

      <div className="panel">
        <h3>Function blocks</h3>
        <FbRow name="TON · start delay" et={fbs.start_timer_et} pt={1.0} on={fbs.start_timer_q} />
        <FbRow name="TON · stop delay" et={fbs.stop_timer_et} pt={1.0} />
        <FbRow name="TON · pump-trip watchdog" et={fbs.pump_trip_timer_et} pt={2.0} on={!!fbs.trip_latch} danger />
        <FbRow name="TON · valve-fault watchdog" et={fbs.valve_fault_timer_et} pt={2.0} on={!!fbs.valve_fault_latch} danger />
        <Row label="CTU · pump start cycles" value={fbs.pump_cycles} />
        <Row label="RS · pump trip latch" value={fbs.trip_latch ? "SET" : "clear"} lamp={!!fbs.trip_latch} lampDanger />
        <Row label="RS · valve fault latch" value={fbs.valve_fault_latch ? "SET" : "clear"} lamp={!!fbs.valve_fault_latch} lampDanger />
        {Object.entries(sensorLatch).map(([tag, v]) => (
          <Row key={tag} label={`RS · ${tag} fault latch`} value={v ? "SET" : "clear"} lamp={!!v} lampDanger />
        ))}
      </div>

      <div className="panel">
        <h3>Interlocks (active)</h3>
        <Row label="E-stop" value={il.estop ? "ACTIVE" : "clear"} lamp={!!il.estop} lampDanger />
        <Row label="Pump trip" value={il.pump_trip ? "ACTIVE" : "clear"} lamp={!!il.pump_trip} lampDanger />
        <Row label="Valve fault" value={il.valve_fault ? "ACTIVE" : "clear"} lamp={!!il.valve_fault} lampDanger />
        {Object.entries(il.hihi || {}).map(([t, v]) => (
          <Row key={t} label={`${t} HIGH-HIGH`} value={v ? "TRIP" : "clear"} lamp={!!v} lampDanger />
        ))}
        {Object.entries(il.sensor_fault || {}).map(([t, v]) => (
          <Row key={t} label={`${t} transmitter fault`} value={v ? "ACTIVE" : "clear"} lamp={!!v} lampDanger />
        ))}
      </div>

      <div className="panel">
        <h3>Program order (scan cycle)</h3>
        <div className="history">{PROGRAM.map((p, i) => <div key={i}>{p}</div>)}</div>
      </div>
    </div>
  );
}
