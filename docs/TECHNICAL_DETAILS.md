# Technical Details

This document covers the process model, validation, and limitations in detail. See [README.md](../README.md) for a quick overview.

## Process Model

### Tank Dynamics

Each tank is an integrator of net volumetric flow:

```
A_i * dh_i/dt = sum(Q_in) - sum(Q_out) - Q_leak
```

The ODE is integrated with classical RK4 at a fixed sub-step (PROCESS_DT = 10ms). The time constants are tens of seconds, so even explicit Euler at 10Hz would be stable. RK4 was chosen for accuracy and robustness against sqrt nonlinearities.

### Valve Flow

Sharp-edged orifice equation:

```
Q = u * Cd * A_orifice * sqrt(2 * g * dh)
```

where `dh` is the hydraulic head between the two nodes (including each tank's base elevation `z_base`). Flow is zero against an adverse head.

### Pump Flow

Centrifugal characteristic with droop:

```
Q_pump = u * Q_max * max(0, 1 - DROOP * head)
```

### Overflow

Any level above tank height over-tops to drain and is reported as overflow flow.

## Timing Hierarchy

| Rate | Period | Purpose |
|------|--------|---------|
| Process integration | 10ms | RK4 sub-step for plant ODE |
| PLC scan / control | 100ms | Input scan, program, PID, outputs |
| Sensor update | 100ms | Transmitter sampling |
| UI refresh | 100ms | WebSocket push to SCADA clients |

## Model Validation

The simulator is validated against closed-form solutions:

### Torricelli Draining

Tank TK-103 draining to zero head through XV-103:
```
A * dh/dt = -u * kv * sqrt(H)
```
Exact solution: `H(t) = (sqrt(H0) - (u*kv/(2A))*t)^2`

The simulation matches to <0.002m error over 15 seconds.

### Steady-State Orifice Chain

At steady state, every valve flow satisfies `Q = u * kv * sqrt(dh)` and mass is conserved: `Q_P101 = Q_XV101 = Q_XV102 = Q_XV103`.

### Pump Droop Characteristic

`Q = u * Qmax * max(0, 1 - droop*head)` — verified against the model equation.

### Heat Exchanger Energy Balance

At steady state, heat leaving the hot stream equals heat entering the cold stream (first law), and both equal `UA * LMTD` using the true log-mean temperature difference.

### Pressure Vessel

Ideal gas ODE: `dP/dt = (Qin - Qout) * P / V`. At steady state, `P = Patm + (Qin / (pos*Cv))^2`.

### RK4 Convergence Order

The integrator shows genuine 4th-order convergence (error ratios ≈16 per step halving) when tested against an exponential trajectory (pump droop dynamics). The draining tank's quadratic solution is integrated exactly by RK4, so it cannot expose the method's order.

### Reservoir Mass Balance

The finite reservoir depletes under pump draw. When empty, the pump starves (zero flow).

## Interlocks and Permissives

- **E-stop**: highest authority, pump stopped, all valves closed
- **High-high level**: trips feed into that tank
- **Pump trip**: run feedback lost while commanded → latches FAULTED
- **Valve travel fault**: command/feedback deviation → latches FAULTED
- **Sensor fault**: fails affected loop to safe manual output (0%)

Faults are latched until operator reset, mirroring real safety systems.

## Leak Detection

Mass-balance detector compares measured flows against level-derived volume change. When the unexplained loss exceeds a threshold and sustains for a window, a leak event is recorded.

**Tank leaks**: `plant.leaks[tag]` — uncontrolled outflow from the tank volume.

**Valve/pipe leaks**: `plant.valve_leaks[tag]` — uncontrolled loss from the valve's upstream node.

**Detection**: compares measured inflow/outflow against level change over LEAK_DETECT_WINDOW (8s). If sustained loss > LEAK_DETECT_MIN_RATE (0.3 L/s), raises alarm.

**Persistence**: leak events stored in `data/leaks.json`, accessible via `/api/leaks`.

## Sensor Fault Models

Level transmitters can fail in several modes:
- **OK**: normal operation with optional noise
- **STUCK**: reads the value at the time of injection
- **FAIL_HIGH**: reads full scale
- **FAIL_LOW**: reads zero
- **DRIFT**: linearly drifts from true value
- **NAN**: returns NaN (propagates to PLC plausibility check)

## Process Units

### Heat Exchanger (HX-101)

Two-stream counter-flow with thermal inertia:
- Hot side: shell, fluid loses heat
- Cold side: tube, fluid gains heat
- True log-mean temperature difference (not arithmetic mean)
- Energy balance in mass flow (m_dot = rho * Q)
- Fouling parameter reduces effective UA

### Pressure Vessel (PK-101)

Pressurised gas volume with inlet flow and modulating outlet:
- Ideal gas dynamics: `dP/dt = (Qin - Qout) * P / V`
- Orifice equation across outlet valve
- PID-controlled outlet valve position
- Pressure alarms (HI, HIHI, LO)

### Conveyor (CV-101)

Belt conveyor with AC motor drive:
- Motor model: `J * dw/dt = T_motor - T_load - T_friction`
- Variable speed via VFD
- Jam detection (sustained low speed)
- Runtime accumulation

## High Availability

When `SCADA_HA=1`, the server runs in HA mode:
- **Lease-based leader election** via SQLite
- **Fencing**: state writes require valid lease token
- **Checkpoints**: periodic state snapshots for failover
- **Standby**: serves read-only until lease expires, then takes over

Requires multi-process (uvicorn --workers N). See `scada/ha.py`.

## OPC UA Gateway

Optional OPC UA server mirroring the PLC I/O image:
- Requires `asyncua` package
- Enable with `OPCUA_ENABLED=1`
- Exposes nodes for all PLC I/O points
- Accepts validated writes (routed through PLC setters)
- Username/password authentication

## Security Controls

- **API authentication**: Bearer token via `SCADA_API_TOKEN`
- **Rate limiting**: per-IP sliding window (120 requests/min)
- **WebSocket cap**: max 50 concurrent clients
- **Modbus allowlist**: restrict client IPs via `MODBUS_ALLOWED_CLIENTS`
- **Input validation**: Pydantic models on all POST endpoints
- **Audit log**: JSONL file of all control actions
