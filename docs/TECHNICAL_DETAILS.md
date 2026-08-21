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
