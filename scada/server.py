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
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config
from .runtime import Runtime

STATIC_DIR = Path(__file__).parent / "static"
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

# Module-level singletons (the server owns one live simulation).
runtime = Runtime()
clients: set[WebSocket] = set()
# Per-client outbound queues decouple the sim loop from slow consumers: a
# stalled browser must never freeze the plant.
client_queues: dict[WebSocket, asyncio.Queue] = {}
MAX_QUEUE = 128
loop_task: asyncio.Task | None = None


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


async def _broadcast(payload: dict) -> None:
    """Non-blocking fan-out.  Clients that cannot keep up are dropped rather
    than stalling the simulation loop."""
    for ws, queue in list(client_queues.items()):
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            clients.discard(ws)
            client_queues.pop(ws, None)


async def _sim_loop() -> None:
    period = config.PLC_SCAN_DT / max(0.01, config.SIM_SPEED)
    next_tick = time.monotonic()
    trend_counter = 0
    while True:
        snapshot = runtime.step()
        await _broadcast({"type": "state", "data": snapshot})

        trend_counter += 1
        if trend_counter >= 5:
            trend_counter = 0
            await _broadcast({"type": "trends", "data": runtime.trends(400)})

        next_tick += period
        delay = next_tick - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        else:
            # Fell behind wall-clock; resynchronise rather than spiral.
            next_tick = time.monotonic()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global loop_task
    loop_task = asyncio.create_task(_sim_loop())
    try:
        yield
    finally:
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass


def create_app(start_loop: bool = True) -> FastAPI:
    app = FastAPI(title="Multi-Tank PLC/SCADA Simulator", version="1.0.0",
                  lifespan=lifespan if start_loop else None)

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

    return app


app = create_app()
