"""
SCADA backend: FastAPI application exposing REST control endpoints and a
WebSocket telemetry channel.

The simulation advances in an asyncio task at the PLC scan rate, paced by
SIM_SPEED.  Each tick computes one full scan cycle (sensor -> PLC -> actuator
-> process) and broadcasts the resulting plant/PLC state to all connected
SCADA clients.

All operator actions are validated server-side (range checks and mode checks)
before being written into the PLC data registers, so the UI cannot put the
system into an impossible state.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from . import config
from .historian import TrendStore
from .modbus_server import ModbusServer
from .runtime import Runtime

STATIC_DIR = Path(__file__).parent / "static"
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

logger = logging.getLogger("scada.server")

_audit_lock = threading.Lock()


def _audit_path() -> Path:
    """Operator-action audit log path (overridable for tests)."""
    env = os.environ.get("SCADA_AUDIT_LOG")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / config.AUDIT_LOG_FILE


def _write_audit(entry: dict) -> None:
    """Append one JSON line to the audit log (best-effort, never breaks
    control even if the log cannot be written)."""
    try:
        path = _audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _audit_lock:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass


class _GuardMiddleware(BaseHTTPMiddleware):
    """Protect and audit every operator action (POST /api/control/* and
    /api/faults/*).  When ``config.API_TOKEN`` is set, requests must carry
    ``Authorization: Bearer <token>``; the default (no token) keeps the
    unauthenticated behaviour for local/demo use."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if request.method == "POST" and (
                path.startswith("/api/control/") or path.startswith("/api/faults/")):
            body = await request.body()
            client = request.client.host if request.client else "unknown"
            _write_audit({
                "ts": time.time(),
                "method": request.method,
                "path": path,
                "client": client,
                "body": body.decode("utf-8", "replace"),
            })
            if config.API_TOKEN:
                auth = request.headers.get("authorization", "")
                if auth != f"Bearer {config.API_TOKEN}":
                    return JSONResponse({"detail": "unauthorized"},
                                        status_code=401)
        return await call_next(request)


# Module-level singletons (the server owns one live simulation).
runtime = Runtime()
clients: set[WebSocket] = set()
# Per-client outbound queues decouple the sim loop from slow consumers: a
# stalled browser must never freeze the plant.
client_queues: dict[WebSocket, asyncio.Queue] = {}
# Sender task per client so a dropped client's task can be cancelled.
client_tasks: dict[WebSocket, asyncio.Task] = {}
MAX_QUEUE = 128
loop_task: asyncio.Task | None = None
modbus_server: ModbusServer | None = None
trend_store: TrendStore | None = None


# ---------------------------------------------------------------------------
# Request schemas (server-side validation)
# ---------------------------------------------------------------------------
class SetpointRequest(BaseModel):
    tag: Literal["LIC-101", "LIC-102"]
    value: float = Field(ge=0.0, le=config.TANK_HEIGHT)


class TuningRequest(BaseModel):
    tag: Literal["LIC-101", "LIC-102"]
    kp: Optional[float] = Field(default=None, ge=0.0, le=1e6)
    ki: Optional[float] = Field(default=None, ge=0.0, le=1e6)
    kd: Optional[float] = Field(default=None, ge=0.0, le=1e6)


class ModeRequest(BaseModel):
    tag: Literal["LIC-101", "LIC-102"]
    mode: Literal["AUTO", "MANUAL"]
    manual_mv: Optional[float] = Field(default=None, ge=0.0, le=100.0)


class ManualValveRequest(BaseModel):
    tag: Literal["XV-102", "XV-103"]
    position: float = Field(ge=0.0, le=100.0)


class ManualPumpRequest(BaseModel):
    speed: float = Field(ge=0.0, le=100.0)


class EstopRequest(BaseModel):
    active: bool


class AckRequest(BaseModel):
    tag: Optional[str] = None   # None => acknowledge all


class SensorFaultRequest(BaseModel):
    tag: Literal["LT-101", "LT-102", "LT-103"]
    fault: Literal["OK", "STUCK", "FAIL_HIGH", "FAIL_LOW", "DRIFT", "NAN"]


class ValveRequest(BaseModel):
    tag: Literal["XV-101", "XV-102", "XV-103"]


class SensorClearRequest(BaseModel):
    tag: Literal["LT-101", "LT-102", "LT-103"]


class DisturbanceRequest(BaseModel):
    tank: Literal["TK-101", "TK-102", "TK-103"] = "TK-102"
    flow_m3s: float = Field(default=0.004, ge=0.0, le=0.05)
    duration: float = Field(default=30.0, ge=1.0, le=300.0)


class LeakRequest(BaseModel):
    tank: Literal["TK-101", "TK-102", "TK-103"]
    flow_m3s: float = Field(ge=0.0, le=0.05)


class ValveLeakRequest(BaseModel):
    tag: Literal["XV-101", "XV-102", "XV-103"]
    flow_m3s: float = Field(ge=0.0, le=0.05)


# ---------------------------------------------------------------------------
# Simulation loop
# ---------------------------------------------------------------------------
async def _client_sender(ws: WebSocket, queue: asyncio.Queue) -> None:
    """Drain one client's outbound queue.  Runs as its own task so a slow or
    dead client only backs up its own queue, never the sim loop."""
    try:
        while True:
            payload = await queue.get()
            await ws.send_json(payload)
    except Exception:
        pass
    finally:
        clients.discard(ws)
        client_queues.pop(ws, None)
        client_tasks.pop(ws, None)


async def _broadcast(payload: dict) -> None:
    """Non-blocking fan-out.  Clients that cannot keep up are dropped (their
    sender task cancelled) rather than stalling the simulation loop."""
    for ws, queue in list(client_queues.items()):
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            logger.warning("dropping slow WebSocket client (queue full)")
            clients.discard(ws)
            client_queues.pop(ws, None)
            task = client_tasks.pop(ws, None)
            if task is not None:
                task.cancel()


async def _sim_loop() -> None:
    period = config.PLC_SCAN_DT / max(0.01, config.SIM_SPEED)
    next_tick = time.monotonic()
    trend_counter = 0
    while True:
        try:
            snapshot = runtime.step()
            await _broadcast({"type": "state", "data": snapshot})

            trend_counter += 1
            if trend_counter >= 5:
                trend_counter = 0
                await _broadcast({"type": "trends", "data": runtime.trends(400)})
        except Exception:
            # One bad tick must never silently kill the simulation: log and
            # continue on the next schedule slot.
            logger.exception("simulation tick failed; continuing")

        next_tick += period
        delay = next_tick - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        else:
            # Fell behind wall-clock; resynchronise rather than spiral.
            next_tick = time.monotonic()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global loop_task, modbus_server, trend_store
    trend_store = TrendStore()
    runtime.historian = trend_store
    # Durable alarm journal: every RAISE/ACK/CLEAR transition is persisted.
    runtime.plc.alarms.set_event_callback(
        lambda action, alarm, t: trend_store.record_alarm_event(
            alarm.tag, alarm.message, alarm.priority, action, t))
    loop_task = asyncio.create_task(_sim_loop())
    if config.MODBUS_ENABLED:
        modbus_server = ModbusServer(lambda: runtime.plc,
                                     host=config.MODBUS_HOST,
                                     port=config.MODBUS_PORT,
                                     lock=runtime.field_lock)
        try:
            modbus_server.start()
        except OSError as exc:
            # A busy/privileged port must not take the whole app down: the
            # browser HMI still works, only the field interface is absent.
            logger.warning("Modbus TCP server failed to bind: %s", exc)
            modbus_server = None
    try:
        yield
    finally:
        if modbus_server is not None:
            modbus_server.stop()
            modbus_server = None
        runtime.flush_historian()
        runtime.historian = None
        if trend_store is not None:
            trend_store.close()
            trend_store = None
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass


def create_app(start_loop: bool = True) -> FastAPI:
    app = FastAPI(title="Multi-Tank PLC/SCADA Simulator", version="1.0.0",
                  lifespan=lifespan if start_loop else None)
    app.add_middleware(_GuardMiddleware)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Serve the built React 3D digital twin (npm run build -> frontend/dist)
    # at /app.  Hashed assets are served from /app/assets; any other /app path
    # returns the SPA shell so client-side routes (/app/leaks, /app/leaks/latest,
    # /app/leaks/{id}) survive a page refresh.  The legacy 2D P&ID dashboard
    # stays at / when the dist is absent.
    if FRONTEND_DIST.exists():
        app.mount("/app/assets",
                  StaticFiles(directory=str(FRONTEND_DIST / "assets")),
                  name="app3d_assets")

        @app.get("/app", include_in_schema=False)
        @app.get("/app/", include_in_schema=False)
        @app.get("/app/{path:path}", include_in_schema=False)
        async def app_spa(path: str = "") -> FileResponse:
            return FileResponse(str(FRONTEND_DIST / "index.html"))

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "index.html"))

    # ---- Telemetry ----------------------------------------------------
    @app.get("/api/state")
    async def get_state() -> JSONResponse:
        return JSONResponse(runtime.snapshot())

    @app.get("/api/trends")
    async def get_trends() -> JSONResponse:
        return JSONResponse(runtime.trends(400))

    @app.get("/api/trends/history/series")
    async def get_history_series() -> JSONResponse:
        store = runtime.historian
        return JSONResponse({"series": store.series() if store else []})

    @app.get("/api/alarms/history")
    async def get_alarm_history(limit: int = 200) -> JSONResponse:
        store = runtime.historian
        if store is None:
            return JSONResponse({"events": []})
        return JSONResponse({"events": store.alarm_history(limit)})

    @app.get("/api/trends/history")
    async def get_history(series: str, t0: float | None = None,
                          t1: float | None = None,
                          max_points: int = 400) -> JSONResponse:
        store = runtime.historian
        if store is None:
            return JSONResponse({"series": series, "range": None, "points": []})
        rng = store.time_range(series)
        if rng is None:
            return JSONResponse({"series": series, "range": None, "points": []})
        lo = rng[0] if t0 is None else t0
        hi = rng[1] if t1 is None else t1
        points = store.range(series, lo, hi, max(1, min(20000, max_points)))
        return JSONResponse({"series": series, "range": rng,
                             "points": [[t, v] for t, v in points]})

    @app.get("/api/plant_config")
    async def get_plant_config() -> JSONResponse:
        return JSONResponse(runtime.plant_config())

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        queue: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUE)
        clients.add(ws)
        client_queues[ws] = queue
        sender = asyncio.create_task(_client_sender(ws, queue))
        client_tasks[ws] = sender
        # Initial burst so the HMI paints instantly (ordered through the queue).
        await queue.put({"type": "config", "data": runtime.plant_config()})
        await queue.put({"type": "state", "data": runtime.snapshot()})
        await queue.put({"type": "trends", "data": runtime.trends(400)})
        try:
            while True:
                # Keep the connection alive; pushes come from the sim loop.
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            sender.cancel()
            clients.discard(ws)
            client_queues.pop(ws, None)
            client_tasks.pop(ws, None)

    # ---- Operating state commands --------------------------------------
    @app.post("/api/control/start")
    async def control_start() -> dict:
        runtime.plc.pulse_start()
        return {"ok": True, "state": runtime.plc.state}

    @app.post("/api/control/stop")
    async def control_stop() -> dict:
        runtime.plc.pulse_stop()
        return {"ok": True, "state": runtime.plc.state}

    @app.post("/api/control/reset")
    async def control_reset() -> dict:
        runtime.plc.pulse_reset()
        return {"ok": True, "state": runtime.plc.state}

    @app.post("/api/control/estop")
    async def control_estop(req: EstopRequest) -> dict:
        runtime.plc.set_estop(req.active)
        return {"ok": True, "estop": req.active}

    @app.post("/api/control/ack")
    async def control_ack(req: AckRequest) -> dict:
        if req.tag is None:
            n = runtime.plc.acknowledge_all()
        else:
            n = 1 if runtime.plc.acknowledge(req.tag) else 0
        return {"ok": True, "acknowledged": n}

    # ---- Loop / tuning commands ----------------------------------------
    @app.post("/api/control/setpoint")
    async def set_setpoint(req: SetpointRequest) -> dict:
        runtime.plc.set_setpoint(req.tag, req.value)
        return {"ok": True, "tag": req.tag, "setpoint": req.value}

    @app.post("/api/control/tuning")
    async def set_tuning(req: TuningRequest) -> dict:
        runtime.plc.set_tuning(req.tag, kp=req.kp, ki=req.ki, kd=req.kd)
        return {"ok": True, "tag": req.tag}

    @app.post("/api/control/mode")
    async def set_mode(req: ModeRequest) -> dict:
        runtime.plc.set_loop_mode(req.tag, req.mode, req.manual_mv)
        return {"ok": True, "tag": req.tag, "mode": req.mode}

    @app.post("/api/control/manual_valve")
    async def set_manual_valve(req: ManualValveRequest) -> dict:
        runtime.plc.set_manual_valve(req.tag, req.position)
        return {"ok": True, "tag": req.tag, "position": req.position}

    @app.post("/api/control/manual_pump")
    async def set_manual_pump(req: ManualPumpRequest) -> dict:
        runtime.plc.set_manual_pump(req.speed)
        return {"ok": True, "speed": req.speed}

    # ---- Fault injection ------------------------------------------------
    @app.post("/api/faults/sensor")
    async def sensor_fault(req: SensorFaultRequest) -> dict:
        runtime.set_sensor_fault(req.tag, req.fault)
        return {"ok": True, "tag": req.tag, "fault": req.fault}

    @app.post("/api/faults/sensor/clear")
    async def sensor_fault_clear(req: SensorClearRequest) -> dict:
        runtime.clear_sensor_fault(req.tag)
        return {"ok": True, "tag": req.tag, "fault": "OK"}

    @app.post("/api/faults/pump/trip")
    async def pump_trip() -> dict:
        runtime.trip_pump()
        return {"ok": True}

    @app.post("/api/faults/pump/reset")
    async def pump_reset() -> dict:
        runtime.reset_pump()
        return {"ok": True}

    @app.post("/api/faults/valve/stick")
    async def valve_stick(req: ValveRequest) -> dict:
        runtime.stick_valve(req.tag)
        return {"ok": True, "tag": req.tag}

    @app.post("/api/faults/valve/unstick")
    async def valve_unstick(req: ValveRequest) -> dict:
        runtime.unstick_valve(req.tag)
        return {"ok": True, "tag": req.tag}

    @app.post("/api/faults/block")
    async def block_outlet(req: ValveRequest) -> dict:
        runtime.block_outlet(req.tag)
        return {"ok": True, "tag": req.tag}

    @app.post("/api/faults/unblock")
    async def unblock_outlet(req: ValveRequest) -> dict:
        runtime.unblock_outlet(req.tag)
        return {"ok": True, "tag": req.tag}

    @app.post("/api/faults/disturbance")
    async def disturbance(req: DisturbanceRequest) -> dict:
        runtime.inject_disturbance(req.tank, req.flow_m3s, req.duration)
        return {"ok": True, "tank": req.tank}

    # ---- Leak investigation ---------------------------------------------
    @app.get("/api/leaks")
    async def list_leaks() -> dict:
        events = runtime.leak_store.recent(config.LEAK_HISTORY_LIMIT)
        return {"count": runtime.leak_store.count(),
                "events": [e.as_dict() for e in events]}

    @app.get("/api/leaks/latest")
    async def latest_leak() -> dict:
        ev = runtime.leak_store.latest()
        return {"count": runtime.leak_store.count(),
                "event": ev.as_dict() if ev else None}

    @app.get("/api/leaks/{leak_id}")
    async def get_leak(leak_id: str) -> JSONResponse:
        ev = runtime.leak_store.get(leak_id)
        if ev is None:
            return JSONResponse(status_code=404,
                                content={"detail": "leak not found"})
        return JSONResponse(ev.as_dict())

    @app.post("/api/faults/leak")
    async def set_leak(req: LeakRequest) -> dict:
        runtime.set_leak(req.tank, req.flow_m3s)
        return {"ok": True, "tank": req.tank, "flow_m3s": req.flow_m3s}

    @app.post("/api/faults/valve_leak")
    async def valve_leak(req: ValveLeakRequest) -> dict:
        runtime.set_valve_leak(req.tag, req.flow_m3s)
        return {"ok": True, "tag": req.tag, "flow_m3s": req.flow_m3s}

    return app


app = create_app()
