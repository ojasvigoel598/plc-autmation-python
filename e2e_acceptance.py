"""End-to-end acceptance run against the live server.

Verifies the full chain the browser exercises:
  WS config/state/trends -> REST start -> RUNNING -> setpoint change
  -> disturbance -> alarm -> E-stop -> unsafe command rejected -> recovery.
"""
import asyncio
import json

import httpx
import websockets

BASE = "http://127.0.0.1:8000"
WS = "ws://127.0.0.1:8000/ws"

ok = True


def check(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not cond:
        ok = False


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=10) as c:
        # 1) static plant config served
        r = await c.get("/api/plant_config")
        check("plant_config 200", r.status_code == 200)
        cfg = r.json()
        check("config has tanks/valves/pump/layout", 
              all(k in cfg for k in ("tanks", "valves", "pump", "reservoir", "loops", "rates")))

        # 2) WebSocket telemetry: config + state + trends on connect
        async with websockets.connect(WS) as ws:
            types = set()
            for _ in range(3):
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                types.add(m["type"])
            check("WS sends config/state/trends", {"config", "state", "trends"} <= types, str(types))

            # Keep draining the socket for the rest of the run so the client
            # never applies backpressure to the server's broadcast loop.
            async def drain():
                try:
                    async for _ in ws:
                        pass
                except Exception:
                    pass
            drainer = asyncio.create_task(drain())

            # 3) start the plant
            r = await c.post("/api/control/start")
            check("start ok", r.status_code == 200 and r.json().get("ok"))
            await asyncio.sleep(3.0)

            # 4) observe state transitions to RUNNING via REST
            r = await c.get("/api/state")
            st = r.json()
            check("state == RUNNING", st["state"] == "RUNNING", st["state"])
            check("pump running", st["pump"]["running"])

            # 5) setpoint change propagates (SP register updated)
            r = await c.post("/api/control/setpoint", json={"tag": "LIC-101", "value": 1.1})
            check("setpoint ok", r.status_code == 200)
            r = await c.get("/api/state")
            check("setpoint applied", abs(r.json()["pids"]["LIC-101"]["sp"] - 1.1) < 1e-9)

            # 6) persistent leak: inject -> mass-balance detect -> event -> resolve
            r = await c.post("/api/faults/leak", json={"tank": "TK-102", "flow_m3s": 0.005})
            check("leak inject ok", r.status_code == 200)
            await asyncio.sleep(10.0)   # > LEAK_DETECT_WINDOW
            r = await c.get("/api/state")
            check("leak detected in snapshot", "TK-102" in r.json()["leaks"]["active"])
            r = await c.get("/api/leaks/latest")
            ev = r.json()["event"]
            check("latest leak is TK-102", ev is not None and ev["tank"] == "TK-102", str(ev)[:80])
            check("leak event ACTIVE", ev["status"] == "ACTIVE")
            r = await c.post("/api/faults/leak", json={"tank": "TK-102", "flow_m3s": 0.0})
            await asyncio.sleep(18.0)   # resolution can lag two detection windows
            r = await c.get("/api/leaks/latest")
            check("leak resolved in history", r.json()["event"]["status"] == "RESOLVED")

            # 6b) disturbance -> alarm path (timed leak on TK-102)
            r = await c.post("/api/faults/disturbance", json={"tank": "TK-102", "flow_m3s": 0.006, "duration": 20})
            check("disturbance ok", r.status_code == 200)
            await asyncio.sleep(2.0)
            r = await c.get("/api/state")
            check("disturbance active", r.json()["faults"]["disturbance_active"])

            # 7) E-stop: highest authority, forces fail-safe
            r = await c.post("/api/control/estop", json={"active": True})
            check("estop ok", r.status_code == 200)
            await asyncio.sleep(0.7)
            r = await c.get("/api/state")
            st = r.json()
            check("state == E_STOPPED", st["state"] == "E_STOPPED", st["state"])
            check("estop alarm raised", any(a["tag"] == "ESTOP" for a in st["alarms"]))
            # Actuators physically slew to fail-safe (0.5 stroke/s -> ~2 s).
            await asyncio.sleep(3.5)
            r = await c.get("/api/state")
            st = r.json()
            check("pump eff at fail-safe", abs(st["pump"]["eff"]) < 1e-6, str(st["pump"]["eff"]))

            # 8) unsafe action while E-stopped: manual valve command is accepted
            #    at the register level but the PLC forces the effective position
            #    to fail-safe (0) -- the UI shows cmd vs eff + interlock reason.
            r = await c.post("/api/control/manual_valve", json={"tag": "XV-102", "position": 80})
            check("manual valve register accepted", r.status_code == 200)
            await asyncio.sleep(0.7)
            r = await c.get("/api/state")
            v = r.json()["valves"]["XV-102"]
            check("operator request recorded", str(v["manual"]) == "80.0", str(v["manual"]))
            check("valve output forced 0 (interlock)", abs(v["cmd"]) < 1e-6, str(v["cmd"]))
            check("valve eff at fail-safe (interlock)", abs(v["eff"]) < 1e-6, str(v["eff"]))

            # 9) recovery: release E-stop then reset
            r = await c.post("/api/control/estop", json={"active": False})
            r = await c.post("/api/control/reset")
            await asyncio.sleep(0.5)
            r = await c.get("/api/state")
            check("recovered to IDLE", r.json()["state"] == "IDLE", r.json()["state"])

            drainer.cancel()

    print("\nRESULT:", "ALL PASS" if ok else "FAILURES PRESENT")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
