import { useCallback, useEffect, useRef, useState } from "react";

// The frontend always talks to its own origin.  In dev, Vite proxies /api and
// /ws to the FastAPI backend; in production the built assets are served by
// FastAPI itself, so this works unchanged.
const WS_URL =
  (window.location.protocol === "https:" ? "wss://" : "ws://") +
  window.location.host + "/ws";

/**
 * Single live connection to the simulation.  Telemetry flows in over the
 * WebSocket (config / state / trends); commands go out over a separate REST
 * pathway so the Python PLC remains the authority for every control action.
 */
export function useSimulation() {
  const [config, setConfig] = useState(null);
  const [state, setState] = useState(null);
  const [trends, setTrends] = useState(null);
  const [connected, setConnected] = useState(false);
  const [lastActivity, setLastActivity] = useState(Date.now());
  const [commandLog, setCommandLog] = useState([]);
  const wsRef = useRef(null);

  useEffect(() => {
    let disposed = false;
    let retryTimer = null;
    let retries = 0;

    const connect = () => {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        retries = 0;
        if (!disposed) setConnected(true);
      };

      ws.onmessage = (ev) => {
        let msg;
        try {
          msg = JSON.parse(ev.data);
        } catch {
          return; // malformed message: ignore, keep last known state
        }
        if (!msg || typeof msg.type !== "string") return;
        setLastActivity(Date.now());
        if (msg.type === "config") setConfig(msg.data);
        else if (msg.type === "state") setState(msg.data);
        else if (msg.type === "trends") setTrends(msg.data);
      };

      ws.onclose = () => {
        if (disposed) return;
        setConnected(false);
        retries += 1;
        // exponential backoff, capped at 10 s
        retryTimer = setTimeout(connect, Math.min(1000 * 2 ** Math.min(retries, 5), 10000));
      };

      ws.onerror = () => {
        try {
          ws.close();
        } catch {
          /* ignore */
        }
      };
    };

    connect();
    return () => {
      disposed = true;
      if (retryTimer) clearTimeout(retryTimer);
      try {
        if (wsRef.current) wsRef.current.close();
      } catch {
        /* ignore */
      }
    };
  }, []);

  const sendCommand = useCallback(async (path, body) => {
    const entry = { path, body, time: Date.now(), ok: null, status: null, data: null, error: null };
    try {
      const r = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: body === undefined ? "{}" : JSON.stringify(body || {}),
      });
      let data = {};
      try {
        data = await r.json();
      } catch {
        /* non-JSON response */
      }
      entry.ok = r.ok;
      entry.status = r.status;
      entry.data = data;
      setCommandLog((prev) => [entry, ...prev].slice(0, 25));
      return { ok: r.ok, status: r.status, ...data };
    } catch (e) {
      entry.ok = false;
      entry.error = String(e);
      setCommandLog((prev) => [entry, ...prev].slice(0, 25));
      return { ok: false, error: String(e) };
    }
  }, []);

  return { config, state, trends, connected, lastActivity, commandLog, sendCommand };
}

/** Human-readable reason an actuator command is being overridden by the PLC. */
export function interlockReason(state) {
  if (!state) return null;
  const il = state.interlocks || {};
  if (state.estop || il.estop) return "E-STOP active — all actuators forced to fail-safe";
  if (state.state === "E_STOPPED") return "PLC is E_STOPPED";
  if (state.state === "FAULTED") return "PLC is FAULTED — reset required";
  if (state.state === "IDLE") return "PLC is IDLE — start the plant first";
  if (il.pump_trip) return "P-101 pump trip interlock";
  if (il.valve_fault) return "motorised valve travel fault interlock";
  const hihi = Object.entries(il.hihi || {}).find(([, v]) => v);
  if (hihi) return `${hihi[0]} HIGH-HIGH level trip`;
  const sf = Object.entries(il.sensor_fault || {}).find(([, v]) => v);
  if (sf) return `${sf[0]} transmitter fault — loop forced to safe output`;
  return null;
}
