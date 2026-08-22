# Comparative analysis: this project vs the open ecosystem

**Scope.** A research-based comparison of the Multi-Tank PLC/SCADA simulator
with similar open-source projects, commercial tools, and published research
on PLC simulation and digital twins.  Sources were surveyed in August 2026;
links are listed so claims can be checked.

The purpose is honest positioning: what this project is, what it does
better than comparable work, and exactly what separates it from
production-grade virtual commissioning tooling.

---

## 1. Ecosystem scan

### 1.1 Open-source / GitHub projects

| Project | What it is | How it relates to this repo |
|---|---|---|
| **OpenPLC** ([OpenPLC_v3](https://github.com/thiagoralves/OpenPLC_v3), succeeded by v4) | The de-facto open-source **soft PLC runtime**: full IEC 61131-3 (Ladder/ST/FBD), Modbus TCP/RTU, DNP3, EtherCAT, runs on Raspberry Pi, Docker, and bare Linux; used widely in ICS cyber-security research because the entire source is available | The most direct "real PLC" analogue. It is a *runtime*, not a process simulator — it has no plant physics and no built-in SCADA twin. This project is the inverse: a full process + SCADA twin, but a software-only "PLC" in Python. A production path is to couple both: our physics/UI layer talking to OpenPLC over Modbus. |
| **Beremiz** ([beremiz.org](https://beremiz.org/)) | Open-source PLCopen IEC 61131-3 IDE that compiles to C (via matiec) | An IDE/compiler, not a simulator. Confirms the ecosystem keeps IEC 61131-3 as the lingua franca — this repo deliberately mirrors IEC 61131-3 concepts (TON/CTU/RS, scan discipline) for the same reason. |
| **OpenCommissioning** ([GitHub](https://github.com/OpenCommissioning)) | Open platform for **virtual commissioning** of PLC code, built on Unity + TwinCAT | The closest architectural cousin: a 3D virtual plant fed by a real PLC runtime. It assumes Beckhoff hardware/TwinCAT; this repo needs no vendor runtime, at the cost of realism. |
| **awesome-digital-twins** ([GitHub](https://github.com/bulentsoykan/awesome-digital-twins)) | Curated index of digital-twin resources | Useful map; confirms the field's focus on asset hierarchies, OPC UA, and simulation-vs-reality sync — the three areas where this project is intentionally light. |
| **Open Industry Project**, **realvirtual-WEB** (game4automation) | Unity/WebGL industrial metaverse tools (evaluated earlier in this repo) | Both are Unity-based; integrating them would mean rebuilding the plant inside Unity and duplicating the model. Rejected here for a reason documented in the README. |

### 1.2 Companies / commercial tools

| Vendor / tool | Capability | Gap this project fills / uses |
|---|---|---|
| CODESYS | SoftPLC runtime + IEC 61131-3 IDE, OPC UA server | The reference "software PLC" ecosystem; this project's engine is a didactic re-implementation of the same concepts. |
| Beckhoff TwinCAT | Real-time IEC 61131 + motion + HMI | Powers most serious virtual-commissioning demos (incl. OpenCommissioning). Requires a licensed runtime and Windows/CE. |
| Siemens SIMIT / PLCSIM | Dedicated virtual-commissioning suite (process + logic) | The gold standard for "plant simulation for control testing" — expensive, closed. This repo is the free, open, browser-based analogue. |
| Visual Components | Discrete-manufacturing virtual commissioning | Focused on robotics/discrete; this repo targets continuous process (tanks/valves/level control). |
| NVIDIA Omniverse | 3D simulation + physics integration | The rendering/co-simulation layer behind many 2024-2026 VC papers; overkill for an educational twin but the direction for "real-scale 3D". |
| OPC Foundation (OPC UA) | Standard for machine-to-machine data exchange; official digital-twin guidance | The industry-standard wire protocol this project currently does **not** speak — its biggest interop gap. |

### 1.3 Research papers

| Paper | Takeaway for this project |
|---|---|
| "Digital twin-based virtual commissioning for evaluation and validation of a reconfigurable process line", *IET Cyber-Physical Systems: Theory & Applications* (2024) | The core VC idea: test control logic against a simulated process **before** touching hardware. This repo demonstrates exactly that loop (PLC -> plant -> telemetry) in a browser, at educational scale. |
| "The Current and Future Challenges for Virtual Commissioning and Digital Twins of Production Lines" (2022, ResearchGate) | Identifies twin-vs-reality **synchronisation and data fidelity** as the central challenges. This repo's "3D is a pure consumer of backend state" architecture is a direct, honest answer to the sync problem. |
| "A Comprehensive Survey on Digital Twin for Future Networks and Systems" (HAL, 2026) | Confirms the field's core requirements: a virtual representation **connected to real-time data**, with a defined data flow. The data-flow diagram in this repo's README/guide formalises exactly that. |
| "Leveraging OPC UA for Digital Twin Realization", OPC Foundation (2024) | Argues OPC UA is the backbone for DT connectivity. Reinforces this project's main interop gap (no OPC UA/Modbus yet). |
| "Virtual Commissioning of Distributed Systems in the Industrial Internet of Things", *Sensors* (2023) | Edge/IIoT-oriented VC; supports the "one authoritative sim, many viewers" topology used here. |

---

## 2. Where this project sits

| Dimension | This project | OpenPLC + Unity-style VC tools | Commercial (SIMIT/TwinCAT) |
|---|---|---|---|
| Process physics | Real ODE model (RK4, mass conservation) | Depends on the twin implementation | Very high fidelity |
| Control logic | Software PLC engine (IEC 61131-3 concepts) | **Real** IEC 61131-3 runtime | Real, certified runtimes |
| 3D / SCADA | Browser (Three.js), data-driven from one source of truth | Unity/WebGL twin | Vendor HMI suites |
| Hardware I/O | None (simulation only) | Modbus/DNP3/EtherCAT | Full fieldbus support |
| Cost / access | Free, open, browser | Free-ish, heavy toolchain | Expensive, closed |
| Maturity | Educational digital-twin prototype | Production-grade runtime | Production/certified |

---

## 3. Top 3 strengths

1. **One source of truth; the 3D twin is a pure consumer.**
   There is exactly one simulation, owned by Python. The browser renders
   only WebSocket state and commands go only through validated REST; the PLC
   remains authoritative and rejections are shown with reasons. This is the
   exact failure mode the digital-twin literature warns about (frontend
   state that diverges from the asset) and this repo solves it by
   construction.

2. **Engineering-faithful control architecture, not a toy.**
   Input-image -> program -> output-image scan cycle, real IEC 61131-3
   blocks (TON/CTU/RS/R_TRIG), ISA-88 operating states, interlock
   authority (E-stop > HIHI > latches > loop outputs), run-feedback and
   travel-deviation watchdogs, bumpless manual/auto transfer, and
   anti-windup PID. A control engineer can reason about the logic exactly
   as if reading a PLC program; most educational simulators skip all of
   this.

3. **Determinism and verification discipline.**
   RK4 with a mass-conservation test suite, mass-balance leak detection
   with measured-vs-estimated labeling, 68 passing tests, an end-to-end
   acceptance script executed 10 consecutive times with zero failures
   (including a hard restart), and one commit per meaningful change. The
   repository *proves* its claims instead of asserting them.

## 4. Top 3 improvements for real scale

1. **Speak the industrial protocols.** Expose the simulated PLC over
   **Modbus TCP and/or OPC UA** (or drive OpenPLC v4 as the runtime). This
   single change turns the tool from "a simulation with its own HMI" into
   "a virtual plant that real SCADA, historians, and PLCs can attach to" —
   the definition of virtual commissioning. The 3D/SCADA layer would then
   have two equivalent consumers (browser + any OPC UA client).

2. **Persist telemetry and add identity.** A time-series historian
   (InfluxDB/Timescale) behind the trends, an ISA-95-style asset/tag
   registry generated from the config, plus authentication and an audit
   log of every register-write command. Required before any multi-user or
   plant-wide use; the event-store pattern already points the way.

3. **Fidelity and deployment maturity.** Extend physics (pressure/
   temperature states, real valve Cv curves, leaks on pipes/valves,
   redundancy), multi-unit plant configs, containerised deployment with
   health checks, and a SIL-style test harness (structured control-logic
   scenario sets — exactly what the virtual-commissioning papers advocate
   for validating logic before commissioning).

## 5. Three honest weaknesses

1. **No hardware/field interface.** No Modbus/OPC UA, no real I/O; the
   software PLC is a single-threaded Python loop with no real-time
   guarantee. It can validate *logic concepts* but can never drive or
   validate real hardware as-is.

2. **Single-user and memory-only telemetry.** No authentication, no
   telemetry persistence (only the leak store survives restarts), no
   audit trail. Fine for education and demos; a blocker for teams.

3. **Simplified physics and sensing.** Ideal incompressible fluid, no
   pressure/temperature dynamics, tank-only leaks, and one transmitter per
   tank (so the "redundant trend check" is impossible by design — the
   removed dead config documented that). The math is sound but the fidelity
   ceiling is low compared to SIMIT-class tools.

## 6. Innovation and creativity

- **"3D as pure consumer" architecture.** The most creative and defensible
  decision in the repo: no second simulation in the browser, geometry
  derived from `/api/plant_config`, every visual traced to a Python
  variable, and a STALE indicator when the link lies. This is the
  anti-pattern-avoiding answer to the #1 hobbyist-digital-twin failure.
- **Mass-balance leak detection with measured-vs-derived labeling.** The
  leak investigation UI separates recorded facts from estimates and names
  the detection source — an honest, engineer-minded UX that most tools
  (and papers!) skip.
- **Config-driven 3D and a scripted auto-demo that runs through the real
  command path** — the demo is not a mockup; it drives the same REST/PLC
  pipeline an operator uses, so what you see in a demo is exactly what
  happens on the real system.
- **Honest maturity positioning.** The README and the engineering guide
  both state plainly that this is an educational digital-twin prototype,
  not a certified controller. In a field full of overclaiming demos, that
  is itself a differentiator.

## 7. What it would take to match OpenPLC-grade tooling

A concrete gap list, in dependency order:

1. Modbus TCP server exposing the PLC I/O image (hours-to-days of work;
   the image tables already exist).
2. OPC UA server + information model (the config is already the seed of
   an address space).
3. Telemetry historian + `/api/trends` served from it.
4. AuthN/AuthZ + audit log on the control endpoints.
5. Containerised deployment (Dockerfile for backend + built frontend) and
   a CI pipeline running pytest + e2e.
6. Optional: swap the Python engine for OpenPLC v4 and keep this repo's
   process/physics + 3D/SCADA layers as the twin around it.

Steps 1-5 are additive and do not change the existing architecture; step
6 is the "real virtual commissioning" endgame and is the natural
sequel to this repository.

---

*Generated as part of an engineering review of the repository; claims
about third-party projects are based on the linked public sources and
may age.*
