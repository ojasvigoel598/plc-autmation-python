"""
Engineering configuration for the multi-tank pilot plant simulation.

All physical quantities use SI units.  Levels are expressed in metres,
flows in m^3/s (the SCADA layer presents them in L/s for operator
readability), pressures/heads in metres of water column.

Process topology (ISA-5.1 style tag naming):

    [reservoir] --P-101--> TK-101 --XV-101--> TK-102 --XV-102--> TK-103 --XV-103--> drain
                         (LIC-101)          (LIC-102)

  * P-101   : variable-speed feed pump (reservoir -> TK-101)
  * XV-101  : motorised transfer valve (TK-101 -> TK-102)
  * XV-102  : motorised transfer valve (TK-102 -> TK-103)
  * XV-103  : motorised discharge valve (TK-103 -> drain)
  * LIC-101 : level controller, PV = LT-101 (TK-101 level), MV = P-101 speed
  * LIC-102 : level controller, PV = LT-102 (TK-102 level), MV = XV-101 opening
  * LT-10x  : level transmitters, 0..100 % of tank height

Reference concepts:
  * ISA-5.1   instrumentation symbology and tag naming
  * IEC 61131-3 function blocks (TON/TOF/CTU/SR/RS ...)
  * ISA-88 / PackML operating state machine (influence from PLC2Skill)
"""

# ---------------------------------------------------------------------------
# Gravity
# ---------------------------------------------------------------------------
G = 9.81  # m/s^2

# ---------------------------------------------------------------------------
# Tank geometry (pilot-plant scale)
# ---------------------------------------------------------------------------
# Each tank: cross-section area (m^2), height (m) and bottom elevation (m)
# above the common drain datum.  The differing base elevations provide the
# static head that drives gravity flow down the cascade.
TANK_HEIGHT = 2.0      # m

TANKS = {
    "TK-101": {"area": 0.60, "height": TANK_HEIGHT, "z_base": 1.50},
    "TK-102": {"area": 0.60, "height": TANK_HEIGHT, "z_base": 1.00},
    "TK-103": {"area": 0.50, "height": TANK_HEIGHT, "z_base": 0.50},
}

# Initial levels (m) - chosen near a plausible steady operating point.
INITIAL_LEVELS = {"TK-101": 1.00, "TK-102": 0.80, "TK-103": 0.50}

# ---------------------------------------------------------------------------
# Pump P-101
# ---------------------------------------------------------------------------
# Ideal flow at 100 % speed.  We model a lightly drooping centrifugal
# characteristic so that the pump cannot sustain full delivery against a
# tall head (see Plant._pump_flow).
PUMP_MAX_FLOW = 0.020   # m^3/s = 20 L/s
PUMP_DROOP = 0.10       # fractional flow loss per metre of discharge head

# ---------------------------------------------------------------------------
# Reservoir (finite supply, mass-balanced)
# ---------------------------------------------------------------------------
# The reservoir is a finite source: the pump draws it down and a constant
# makeup flow can refill it.  When it runs empty the pump starves (flow 0).
# Sized so a normal demo session (minutes) is unaffected by depletion.
RESERVOIR_AREA = 6.0          # m^2
RESERVOIR_HEIGHT = 3.0        # m
INITIAL_RESERVOIR_LEVEL = 3.0 # m
RESERVOIR_MAKEUP_FLOW = 0.0   # m^3/s constant refill (0 = finite, drains)

# ---------------------------------------------------------------------------
# Motorised valves (orifice model)
# ---------------------------------------------------------------------------
# Q = u * KV * sqrt(max(0, head))   [m^3/s]
# KV bundles Cd * A_orifice * sqrt(2g) into a single conductance.
VALVE_CD = 0.62
VALVE_ORIFICE_AREA = 0.0060     # m^2 (fully-open equivalent orifice)
_VALVE_KV = VALVE_CD * VALVE_ORIFICE_AREA * (2.0 * G) ** 0.5

VALVES = {
    "XV-101": {"kv": _VALVE_KV, "upstream": "TK-101", "downstream": "TK-102", "init_open": 0.50},
    "XV-102": {"kv": _VALVE_KV, "upstream": "TK-102", "downstream": "TK-103", "init_open": 0.50},
    "XV-103": {"kv": _VALVE_KV, "upstream": "TK-103", "downstream": "drain",  "init_open": 0.50},
}

# ---------------------------------------------------------------------------
# PID loops
# ---------------------------------------------------------------------------
# Parallel (ISA) form:  u = Kp*e + Ki*int(e) + Kd*de/dt
# MV is expressed as a percentage (0..100) of the actuator range.
PID_LOOPS = {
    "LIC-101": {
        "pv_tag": "LT-101",
        "mv_tag": "P-101",
        "setpoint": 1.00,          # m
        "kp": 200.0,               # % / m
        "ki": 50.0,                # % / (m*s)
        "kd": 0.0,                 # % * s / m
        "mv_min": 0.0,             # %
        "mv_max": 100.0,           # %
        "deriv_filter": 0.1,       # first-order derivative filter time constant (s)
        "direct": True,            # increasing PV error -> increasing MV
        "init_mv": 52.0,           # initial integrator state near steady MV (%)
    },
    "LIC-102": {
        "pv_tag": "LT-102",
        "mv_tag": "XV-101",
        "setpoint": 0.80,          # m
        "kp": 200.0,               # % / m
        "ki": 50.0,                # % / (m*s)
        "kd": 0.0,                 # % * s / m
        "mv_min": 0.0,             # %
        "mv_max": 100.0,           # %
        "deriv_filter": 0.1,       # s
        "direct": True,
        "init_mv": 57.0,           # initial integrator state near steady MV (%)
    },
}

# ---------------------------------------------------------------------------
# Timing hierarchy (explicitly separated rates)
# ---------------------------------------------------------------------------
#   PROCESS_DT    : integration sub-step for the plant ODE (RK4)
#   PLC_SCAN_DT   : PLC scan / controller / sensor update period
#   UI_REFRESH_DT : how often the backend pushes state to the SCADA clients
#
# One PLC scan advances the plant by PLC_SCAN_DT seconds using
# PLC_SCAN_DT / PROCESS_DT RK4 sub-steps.  The controller and sensors are
# sampled once per scan, mirroring a real PLC's input-image / program-scan /
# output-image discipline.
PROCESS_DT = 0.01     # s
PLC_SCAN_DT = 0.10    # s  (10 Hz scan cycle)
UI_REFRESH_DT = 0.10  # s

# Real-time pacing multiplier for the live server (1.0 = wall-clock real time).
SIM_SPEED = 1.0

# ---------------------------------------------------------------------------
# Alarm / interlock thresholds (fractions of tank height, 0..1)
# ---------------------------------------------------------------------------
LEVEL_LO = 0.10        # low-level warning
LEVEL_HI = 0.85        # high-level warning
LEVEL_HIHI = 0.95      # high-high -> trip interlock

# Sensor fault detection thresholds
SENSOR_RATE_LIMIT = 0.50   # m/s : implausible rate-of-change trips a sensor fault


# ---------------------------------------------------------------------------
# Modbus TCP field interface
# ---------------------------------------------------------------------------
# A zero-dependency Modbus TCP server (scada.modbus_server) exposes the PLC
# I/O image so real SCADA/historian/PLC clients can attach.  Port 502 is the
# standard Modbus TCP port; it is privileged on some OSes, so the launch
# script lets an operator override it.
MODBUS_ENABLED = True
MODBUS_HOST = "0.0.0.0"
MODBUS_PORT = 502


# ---------------------------------------------------------------------------
# Leak detection & persistent event store
# ---------------------------------------------------------------------------
# Leaks only occur at tanks in this plant (the `plant.leaks[tag]` mass-balance
# term).  A mass-balance detector flags one when the measured level falls
# faster than the measured flow balance can explain, sustained over a window.
LEAK_STORE_FILE = "data/leaks.json"   # relative to the repository root
TREND_STORE_FILE = "data/trends.sqlite3"  # SQLite historian (relative to root)
LEAK_DETECT_WINDOW = 8.0      # s : integration window for the balance check
LEAK_DETECT_MIN_RATE = 0.0003  # m^3/s (0.3 L/s) : smallest reportable leak
                               # (a rate sustained over the window implies the
                               #  minimum reportable volume loss)
LEAK_CLEAR_RATE = 0.0001       # m^3/s : below this a leak is considered resolved
LEAK_HISTORY_LIMIT = 30        # latest N events surfaced to the frontend

# Historian flush cadence: full-resolution samples are buffered in memory and
# written to SQLite once per this many scans (10 scans == 1 s at 10 Hz) so the
# sim loop never blocks on a per-sample disk write.
HISTORIAN_FLUSH_SCANS = 10


# ---------------------------------------------------------------------------
# 3D scene layout (visualisation metadata, world units = metres)
# ---------------------------------------------------------------------------
# Positions the equipment in the 3D digital twin.  Tanks sit at y = z_base
# (so the cascade descends left-to-right); valves sit on the sloped transfer
# pipe between their upstream and downstream tanks; the pump lifts from the
# reservoir up to TK-101.  The frontend reads these positions, so the scene
# adapts if the plant topology is changed.
LAYOUT = {
    "reservoir": {"x": -9.0, "y": 0.0, "z": 0.0, "height": 2.5, "radius": 1.1},
    "P-101":     {"x": -6.0, "y": 0.6, "z": 0.0},
    "TK-101":    {"x": -2.0, "z": 0.0},
    "XV-101":    {"x":  1.0, "y": 1.25, "z": 0.0},
    "TK-102":    {"x":  4.0, "z": 0.0},
    "XV-102":    {"x":  7.0, "y": 0.75, "z": 0.0},
    "TK-103":    {"x": 10.0, "z": 0.0},
    "XV-103":    {"x": 13.0, "y": 0.25, "z": 0.0},
    "drain":     {"x": 15.0, "y": 0.0, "z": 0.0},
}
