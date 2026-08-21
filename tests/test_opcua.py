"""
OPC UA gateway tests (integration against a live asyncua server).

Skipped automatically when asyncua is not installed, so the suite stays
green in dependency-light environments; CI with `requirements-opcua.txt`
exercises these for real.
"""

import asyncio
import threading

import pytest

from scada import config
from scada.opcua_gateway import NS, create_gateway

pytest.importorskip("asyncua")

from asyncua import Client, ua  # noqa: E402
from asyncua.common.subscription import SubscriptionHandler  # noqa: E402


@pytest.fixture()
def gateway_and_url():
    """Start a gateway on an ephemeral port bound to a fresh runtime."""
    from scada.runtime import Runtime

    runtime = Runtime()
    gateway = create_gateway(lambda: runtime, host="127.0.0.1", port=0)
    # Port 0 -> asyncua picks an ephemeral port; we need the actual one.
    # Recreate with a fixed free port instead for determinism.
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    gateway = create_gateway(lambda: runtime, host="127.0.0.1", port=port,
                             lock=threading.RLock())
    assert gateway.start() is True
    url = f"opc.tcp://127.0.0.1:{port}"
    yield runtime, url, gateway
    gateway.stop()


def test_gateway_browse_and_read(gateway_and_url):
    """Browse the namespace and read live telemetry nodes."""
    _runtime, url, _gateway = gateway_and_url

    async def run():
        async with Client(url) as client:
            await client.connect()
            objects = client.nodes.objects
            tk = await objects.get_child([f"{NS}:Plant", f"{NS}:Tanks", f"{NS}:TK-101"])
            val = await tk.read_value()
            assert isinstance(val, float)
            # Advance the sim a little and confirm the node tracks it.
            _runtime.step(config.PLC_SCAN_DT)
            await asyncio.sleep(0.15)   # refresh cadence
            val2 = await tk.read_value()
            assert val2 == val or abs(val2 - val) < 0.05
    asyncio.run(run())


def test_gateway_write_setpoint_validated(gateway_and_url):
    """A valid setpoint write is accepted and routed to the PLC."""
    runtime, url, _gateway = gateway_and_url

    async def run():
        async with Client(url) as client:
            await client.connect()
            sp = await client.nodes.objects.get_child(
                [f"{NS}:Control", f"{NS}:PIDLoops", f"{NS}:LIC-101", f"{NS}:Setpoint"])
            await sp.set_value(1.2)
            await asyncio.sleep(0.05)
            assert runtime.plc.pids["LIC-101"].setpoint == pytest.approx(1.2)
    asyncio.run(run())


def test_gateway_write_setpoint_rejected(gateway_and_url):
    """An out-of-range setpoint write is refused with BadOutOfRange and the
    PLC value is unchanged."""
    runtime, url, _gateway = gateway_and_url
    before = runtime.plc.pids["LIC-101"].setpoint

    async def run():
        async with Client(url) as client:
            await client.connect()
            sp = await client.nodes.objects.get_child(
                [f"{NS}:Control", f"{NS}:PIDLoops", f"{NS}:LIC-101", f"{NS}:Setpoint"])
            with pytest.raises(ua.uaerrors.BadOutOfRange):
                await sp.set_value(9.9)   # above tank height
    asyncio.run(run())
    assert runtime.plc.pids["LIC-101"].setpoint == before


def test_gateway_start_stop_commands(gateway_and_url):
    """Operator start/stop pulses route into the PLC state machine."""
    runtime, url, _gateway = gateway_and_url

    async def run():
        async with Client(url) as client:
            await client.connect()
            start = await client.nodes.objects.get_child(
                [f"{NS}:Control", f"{NS}:Operator", f"{NS}:Start"])
            await start.set_value(True)
            # A PLC processes the start pulse on its next scan, so advance
            # the simulation (as the real sim loop would).
            for _ in range(12):
                runtime.step(config.PLC_SCAN_DT)
            assert runtime.plc.state in ("STARTING", "RUNNING")
            stop = await client.nodes.objects.get_child(
                [f"{NS}:Control", f"{NS}:Operator", f"{NS}:Stop"])
            await stop.set_value(True)
            for _ in range(12):
                runtime.step(config.PLC_SCAN_DT)
            assert runtime.plc.state in ("STOPPING", "IDLE")
    asyncio.run(run())


def test_gateway_subscription(gateway_and_url):
    """A subscribed client receives data-change notifications as telemetry
    refreshes."""
    runtime, url, _gateway = gateway_and_url
    got = []

    class Handler:
        def datachange_notification(self, node, val, data):
            got.append(val)

    async def run():
        async with Client(url) as client:
            await client.connect()
            tk = await client.nodes.objects.get_child(
                [f"{NS}:Plant", f"{NS}:Tanks", f"{NS}:TK-101"])
            sub = await client.create_subscription(100, Handler())
            handle = await sub.subscribe_data_change(tk)
            # Force several refresh ticks by stepping the sim.
            for _ in range(4):
                runtime.step(config.PLC_SCAN_DT)
                await asyncio.sleep(0.15)
            await sub.unsubscribe(handle)
            await sub.delete()
    asyncio.run(run())
    assert len(got) >= 1


def test_gateway_estop_fencing_state(gateway_and_url):
    """E-stop write is applied and the node read-back reflects it."""
    runtime, url, _gateway = gateway_and_url

    async def run():
        async with Client(url) as client:
            await client.connect()
            estop = await client.nodes.objects.get_child(
                [f"{NS}:Control", f"{NS}:Operator", f"{NS}:Estop"])
            await estop.set_value(True)
            await asyncio.sleep(0.05)
            assert runtime.plc.i["I0.0_ESTOP"] is True
            assert await estop.read_value() is True
    asyncio.run(run())
