# PLC / SCADA Process Simulator

A real-time, interactive three-tank cascade process simulation with a software PLC engine, PID level control, fault injection, and a live 3D SCADA dashboard.

Every control action propagates through the full chain: plant → sensors → PLC → control program → actuators → process response → sensors. Nothing is cosmetic.

![Live 3D digital twin](docs/screenshots/digital-twin-3d.png)

## What This Is

A working PLC simulation that couples a physically consistent process model to real control logic. You can operate it, fault it, and recover it in a browser.

I started this as a matplotlib demo. It's now a multi-tank plant with:
- Three tanks connected by motorised valves
- A variable-speed pump feeding from a reservoir
- Two PID level controllers (LIC-101, LIC-102)
- An IEC 61131-3 PLC engine with function blocks
- E-stop, interlocks, and alarm handling
- A live 3D digital twin (React + Three.js)
- A 2D P&ID HMI (legacy, kept)

## Quick Start

```bash
pip install -r requirements.txt
python run_scada.py
```

- 3D digital twin: http://127.0.0.1:8000/app
- 2D P&ID HMI: http://127.0.0.1:8000/

For Docker:
```bash
docker compose up --build
```
