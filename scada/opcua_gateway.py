"""
OPC UA gateway exposing the simulation to standard OPC UA clients.

Runs an asyncua server in its own background thread (like the Modbus TCP
server), publishing the *authoritative* plant/PLC state as OPC UA nodes and
accepting operator commands through validated, fenced writes.

Design principles
-----------------
1. **Single source of truth.**  The gateway holds no plant state of its own:
   every telemetry node is refreshed from ``runtime.snapshot()`` on a cadence,
   so a client can never observe state that diverges from the simulation.
   Commands are routed straight into the PLC's validated setters (the same
   ones the REST API uses); the gateway does not keep a second copy.

2. **Fenced, validated writes.**  A client writing to a command node is
   validated server-side (range / mode / tag checks) exactly like the REST
   layer.  Invalid writes are rejected with ``BadOutOfRange`` /
   ``BadNotWritable`` status codes — the client sees a refusal, not a silent
   no-op.  With HA enabled, writes are refused when this process is not the
   ACTIVE lease holder (the same fencing the REST layer applies).

3. **Subscriptions.**  asyncua gives subscriptions/monitored items natively;
   telemetry nodes change each refresh tick, so a subscribed client receives
   live data-change notifications with no extra code.

4. **Optional dependency.**  ``asyncua`` is *not* a hard dependency: the
   gateway is only active when the package is installed AND
   ``OPCUA_ENABLED=1`` (default off keeps the project dependency-light).
   Importing this module without asyncua is safe.

Security: anonymous access is allowed by default (demo parity with the REST
API).  Set ``OPCUA_USERNAME`` / ``OPCUA_PASSWORD`` to require authentication.
TLS is not configured here: like Modbus, this interface is meant for a
private/field network; document that before exposing it publicly.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Callable

from . import config

logger = logging.getLogger("scada.opcua")

try:
    from asyncua import Server, ua
    from asyncua.server.user_managers import User, UserManager, UserRole
    _HAS_ASYNCUA = True

    class _ConfiguredUserManager(UserManager):
        """Require OPCUA_USERNAME/OPCUA_PASSWORD when set; else permissive."""

        def __init__(self, username: str | None, password: str | None) -> None:
            self._username = username
            self._password = password

        def get_user(self, iserver, username=None, password=None,
                     certificate=None):
            if self._username is None:
                return User(role=UserRole.Admin, name="anonymous")
            if username == self._username and password == self._password:
                return User(role=UserRole.Admin, name=username)
            return None

except ImportError:  # pragma: no cover - exercised when asyncua is absent
    Server = None  # type: ignore[assignment]
    ua = None      # type: ignore[assignment]
    _HAS_ASYNCUA = False

NS_URI = "urn:plc-scada-sim"

# Browse-name prefix per namespace index (asyncua namespaces start at 2).
NS = 2


class OpcUaGateway:
    """OPC UA server mirroring the runtime, in its own thread."""

    def __init__(self, runtime_getter: Callable, host: str = "0.0.0.0",
                 port: int = 4840, lock: threading.Lock | None = None) -> None:
        self._runtime_getter = runtime_getter
        self._lock = lock
        self.host = host
        self.port = port
        self._server: Server | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: threading.Event | None = None
        # nodeid -> (kind, tag) for refresh & write routing
        self._telemetry: dict = {}
        self._commands: dict = {}
        # keep a reference to the refresh task so shutdown can cancel it
        self._refresh_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def start(self, wait_ready: float = 5.0) -> bool:
        """Start the gateway thread.  Returns False (and logs) if asyncua is
        not installed, so callers can degrade gracefully.  Optionally blocks
        until the TCP endpoint answers (for deterministic tests)."""
        if self._thread is not None:
            return True
        if not _HAS_ASYNCUA:
            logger.warning("OPC UA gateway disabled: asyncua is not installed. "
                           "pip install asyncua to enable it.")
            return False
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name="opcua-gw",
                                        daemon=True)
        self._thread.start()
        if wait_ready:
            self._wait_ready(wait_ready)
        return True

    def _wait_ready(self, timeout: float) -> None:
        """Poll until the TCP endpoint accepts connections (the asyncua
        server binds inside its own event-loop thread)."""
        import socket
        import time
        deadline = time.monotonic() + timeout
        port = self.port
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((self.host, port), timeout=0.5):
                    return
            except OSError:
                time.sleep(0.05)
        raise TimeoutError(
            f"OPC UA gateway on {self.host}:{port} did not become ready "
            f"within {timeout}s")

    @property
    def bound_port(self) -> int | None:
        """The actual TCP port once the server has bound (None before)."""
        if self._server is None:
            return None
        try:
            eps = self._server.iserver.get_endpoints()
            if eps:
                return int(eps[0].EndpointUrl.rsplit(":", 1)[-1])
        except Exception:
            pass
        return self.port

    def stop(self) -> None:
        if self._thread is None:
            return
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._shutdown_async)
        self._thread.join(timeout=10.0)
        self._thread = None
        self._server = None

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        except Exception:
            logger.exception("OPC UA gateway thread exited with error")
        finally:
            self._loop.close()
            self._loop = None

    async def _shutdown_async(self) -> None:
        try:
            if self._refresh_task is not None:
                self._refresh_task.cancel()
            if self._server is not None:
                await self._server.stop()
        except Exception:
            logger.exception("OPC UA gateway shutdown error")

    # ------------------------------------------------------------------
    # server construction
    # ------------------------------------------------------------------
    async def _serve(self) -> None:
        server = Server()
        self._server = server
        await server.init()
        server.set_endpoint(f"opc.tcp://{self.host}:{self.port}")

        # Authentication (permissive by default).
        username = os_environ_or_none("OPCUA_USERNAME")
        password = os_environ_or_none("OPCUA_PASSWORD")
        server.iserver.set_user_manager(
            _ConfiguredUserManager(username, password))

        idx = await server.register_namespace(NS_URI)
        objects = server.nodes.objects
        await self._build_address_space(objects, idx)

        async with server:
            logger.info("OPC UA gateway listening on opc.tcp://%s:%s",
                        self.host, self.port)
            self._refresh_task = asyncio.create_task(self._refresh_loop())
            try:
                while True:
                    await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                pass

    async def _build_address_space(self, objects, idx: int) -> None:
        """Create the node tree from the authoritative state.  Telemetry
        nodes are read-only and refreshed; command nodes are writable and
        fenced/validated on write."""
        plant = await objects.add_folder(idx, "Plant")
        plc = await objects.add_folder(idx, "PLC")
        units = await objects.add_folder(idx, "ProcessUnits")
        cmd = await objects.add_folder(idx, "Control")

        # --- Plant: tanks / valves / pump / flows -----------------------
        tanks = await plant.add_folder(idx, "Tanks")
        for t in ("TK-101", "TK-102", "TK-103"):
            node = await tanks.add_variable(idx, t, 0.0)
            self._telemetry[node.nodeid] = ("level", t)

        valves = await plant.add_folder(idx, "Valves")
        for v in ("XV-101", "XV-102", "XV-103"):
            node = await valves.add_variable(idx, v, 0.0)
            self._telemetry[node.nodeid] = ("valve", v)

        pump = await plant.add_folder(idx, "Pump")
        pnode = await pump.add_variable(idx, "Speed", 0.0)
        self._telemetry[pnode.nodeid] = ("pump_speed", None)
        rnode = await pump.add_variable(idx, "ReservoirLevel", 0.0)
        self._telemetry[rnode.nodeid] = ("reservoir", None)

        flows = await plant.add_folder(idx, "Flows")
        for f in ("P-101", "XV-101", "XV-102", "XV-103"):
            node = await flows.add_variable(idx, f, 0.0)
            self._telemetry[node.nodeid] = ("flow", f)

        # --- PLC: state + alarms ----------------------------------------
        snode = await plc.add_variable(idx, "State", "IDLE")
        self._telemetry[snode.nodeid] = ("plc_state", None)
        scnode = await plc.add_variable(idx, "ScanCount", 0)
        self._telemetry[scnode.nodeid] = ("scan_count", None)
        anode = await plc.add_variable(idx, "ActiveAlarms", 0)
        self._telemetry[anode.nodeid] = ("alarms_active", None)

        # --- Process units ----------------------------------------------
        hx = await units.add_folder(idx, "HeatExchanger")
        for name in ("TColdOut", "THotOut", "HeatTransferW"):
            node = await hx.add_variable(idx, name, 0.0)
            self._telemetry[node.nodeid] = ("hx", name)
        pk = await units.add_folder(idx, "PressureVessel")
        for name in ("PressurePa", "ValvePosition"):
            node = await pk.add_variable(idx, name, 0.0)
            self._telemetry[node.nodeid] = ("pk", name)
        cv = await units.add_folder(idx, "Conveyor")
        for name in ("Speed", "Running", "Jammed"):
            node = await cv.add_variable(idx, name, 0.0)
            self._telemetry[node.nodeid] = ("cv", name)

        # --- Control commands (validated, fenced writes) ----------------
        loops = await cmd.add_folder(idx, "PIDLoops")
        for tag in ("LIC-101", "LIC-102", "TIC-101", "PIC-101"):
            folder = await loops.add_folder(idx, tag)
            sp = await folder.add_variable(idx, "Setpoint", 0.0)
            await sp.set_writable(True)
            self._commands[sp.nodeid] = ("setpoint", tag)
            mv = await folder.add_variable(idx, "MV", 0.0)
            self._telemetry[mv.nodeid] = ("pid_mv", tag)

        op = await cmd.add_folder(idx, "Operator")
        mode = await op.add_variable(idx, "LoopMode", "AUTO")
        await mode.set_writable(True)
        self._commands[mode.nodeid] = ("mode", "LIC-101")
        start = await op.add_variable(idx, "Start", False)
        await start.set_writable(True)
        self._commands[start.nodeid] = ("start", None)
        stop = await op.add_variable(idx, "Stop", False)
        await stop.set_writable(True)
        self._commands[stop.nodeid] = ("stop", None)
        estop = await op.add_variable(idx, "Estop", False)
        await estop.set_writable(True)
        self._commands[estop.nodeid] = ("estop", None)
        reset = await op.add_variable(idx, "Reset", False)
        await reset.set_writable(True)
        self._commands[reset.nodeid] = ("reset", None)
        ack = await op.add_variable(idx, "AckAll", False)
        await ack.set_writable(True)
        self._commands[ack.nodeid] = ("ack", None)

        # Register a write setter per command node: validate + route to the
        # PLC, reject invalid writes with an OPC UA status code.  The setter
        # is responsible for storing the accepted value (asyncua calls it in
        # place of the default store) and for raising to refuse a write.
        for nodeid, (kind, tag) in self._commands.items():
            self._server.iserver.aspace.set_attribute_value_setter(
                nodeid, ua.AttributeIds.Value,
                self._make_setter(nodeid, kind, tag))

    # ------------------------------------------------------------------
    # refresh loop: publish authoritative state (single source of truth)
    # ------------------------------------------------------------------
    async def _refresh_loop(self) -> None:
        period = max(config.UI_REFRESH_DT, 0.05)
        while True:
            try:
                snap = self._runtime_getter().snapshot()
                await self._publish(snap)
            except Exception:
                logger.exception("OPC UA refresh tick failed")
            await asyncio.sleep(period)

    async def _publish(self, snap: dict) -> None:
        if self._server is None:   # shutdown race: stop() nulled it
            return
        for nodeid, (kind, tag) in self._telemetry.items():
            try:
                value = self._telemetry_value(snap, kind, tag)
            except KeyError:
                continue
            await self._server.iserver.write_attribute_value(
                nodeid, ua.AttributeIds.Value,
                ua.DataValue(ua.Variant(value, ua.VariantType.Double)
                             if not isinstance(value, bool) and not isinstance(value, str)
                             else ua.Variant(value)))

    @staticmethod
    def _telemetry_value(snap: dict, kind: str, tag: str | None):
        if kind == "level":
            return snap["levels"][tag]
        if kind == "valve":
            return snap["valves"][tag]["eff"]
        if kind == "pump_speed":
            return snap["pump"]["eff"]
        if kind == "reservoir":
            return snap["reservoir_level"]
        if kind == "flow":
            return snap["flows"][tag]
        if kind == "plc_state":
            return snap["state"]
        if kind == "scan_count":
            return snap["scan_count"]
        if kind == "alarms_active":
            return len(snap["alarms"])
        if kind == "pid_mv":
            return snap["pids"][tag]["mv"]
        if kind == "hx":
            u = snap["process_units"]["heat_exchanger"]
            return {"TColdOut": u["T_cold_out"], "THotOut": u["T_hot_out"],
                    "HeatTransferW": u["heat_transfer_W"]}[tag]
        if kind == "pk":
            u = snap["process_units"]["pressure_vessel"]
            return {"PressurePa": u["pressure_Pa"],
                    "ValvePosition": u["valve_position"]}[tag]
        if kind == "cv":
            u = snap["process_units"]["conveyor"]
            return {"Speed": u["speed"], "Running": u["running"],
                    "Jammed": u["jammed"]}[tag]
        raise KeyError(kind)

    # ------------------------------------------------------------------
    # command routing (validated, fenced)
    # ------------------------------------------------------------------
    def _make_setter(self, nodeid, kind: str, tag: str | None):
        def setter(node, attr, value):
            return self._route_command(node, nodeid, kind, tag, value)
        return setter

    def _route_command(self, node, nodeid, kind: str, tag: str | None,
                       value) -> None:
        """Validate + apply a client write.  Raises a UaStatusCodeError to
        reject invalid writes (client sees the refusal).  On success the
        accepted value is stored into the node (the setter replaces the
        default store in asyncua)."""
        runtime = self._runtime_getter()
        plc = runtime.plc

        # Fencing: in HA mode only the ACTIVE lease holder may operate.
        from .server import ha_state, ha_token, ha_role
        if config.HA_ENABLED and (ha_role != "ACTIVE" or ha_state is None
                                  or ha_token is None
                                  or not ha_state.is_leader(ha_token)):
            raise ua.uaerrors.BadNotWritable(
                "plant is read-only (not the active process)")

        def _store(v) -> None:
            # Persist the accepted value so a client read-back reflects it.
            node.attributes[ua.AttributeIds.Value].value = ua.DataValue(
                ua.Variant(v))
            node.attributes[ua.AttributeIds.Value].value_callback = None

        lock = self._lock if self._lock is not None else _nullcontext()
        with lock:
            val = value.Value.Value if hasattr(value, "Value") else value
            if kind == "setpoint":
                v = float(val)
                if not (0.0 <= v <= config.TANK_HEIGHT):
                    raise ua.uaerrors.BadOutOfRange()
                plc.set_setpoint(tag, v)
                _store(v)
                return
            if kind == "mode":
                v = str(val).upper()
                if v not in ("AUTO", "MANUAL"):
                    raise ua.uaerrors.BadOutOfRange()
                plc.set_loop_mode(tag, v)
                _store(v)
                return
            if kind == "start":
                if bool(val):
                    plc.pulse_start()
                _store(False)   # edge-triggered; node reads back released
                return
            if kind == "stop":
                if bool(val):
                    plc.pulse_stop()
                _store(False)
                return
            if kind == "reset":
                if bool(val):
                    plc.pulse_reset()
                _store(False)
                return
            if kind == "estop":
                plc.set_estop(bool(val))
                _store(bool(val))
                return
            if kind == "ack":
                if bool(val):
                    plc.acknowledge_all()
                _store(False)
                return
        raise ua.uaerrors.BadNotWritable()


class _nullcontext:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def os_environ_or_none(key: str) -> str | None:
    import os
    v = os.environ.get(key, "").strip()
    return v or None


def create_gateway(runtime_getter: Callable, host: str | None = None,
                   port: int | None = None,
                   lock: threading.Lock | None = None) -> OpcUaGateway:
    """Factory honouring config/env (OPCUA_HOST, OPCUA_PORT)."""
    return OpcUaGateway(runtime_getter,
                        host=host or config.OPCUA_HOST,
                        port=port if port is not None else config.OPCUA_PORT,
                        lock=lock)
