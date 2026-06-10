# PLC Core Concepts

## What is a PLC?

A **Programmable Logic Controller** is an industrial computer designed to:
- Read digital/analog inputs (sensors)
- Execute control logic
- Drive digital/analog outputs (actuators)
- Repeat this cycle 10-1000 times per second

## The Scan Cycle

Every PLC follows this sequence repeatedly:

```
┌─────────────────────────────────┐
│   1. INPUT SCAN                 │
│   Read all physical sensors     │ ← I0.0, I0.1, etc
│   into Input Image Table        │
├─────────────────────────────────┤
│   2. PROGRAM SCAN               │
│   Execute ladder logic          │ ← Your control program
│   (rung by rung)                │
├─────────────────────────────────┤
│   3. OUTPUT UPDATE              │
│   Write all outputs             │ ← Q0.0, Q0.1, etc
│   to physical devices           │
├─────────────────────────────────┤
│   4. INTER-SCAN DELAY           │
│   Wait (typically 10-100ms)     │
└─────────────────────────────────┘
        Repeat infinitely
```

## Key Differences from Regular Programming

### ❌ Computer Thinking
```python
# Variables can change anytime
pump_on = True
# ... 1000 lines of code later ...
pump_on = False  # Could happen anywhere, anytime
```

### ✅ PLC Thinking
```python
# Inputs only change at INPUT SCAN
i_low_sensor = read_input(I0.0)      # Read once per cycle
# ... logic uses this STABLE value ...
# Output only changes at OUTPUT UPDATE
write_output(Q0.0, pump_command)     # Write once per cycle
```

## Memory Types in PLCs

### Input (I) - Physical Sensors
- **Read-only** during program execution
- Updated at start of each scan
- Cannot be written to from logic
- Example: `I0.0` = pressure sensor input

### Output (Q) - Physical Actuators
- **Write-only** from within logic
- Sent at end of each scan
- Cannot be read back in same scan
- Example: `Q0.0` = pump relay output

### Memory (M) - Internal Flags
- **Read/Write** at any time
- Persist between scans (state memory)
- **NOT** connected to physical hardware
- Example: `M0.7` = pump running flag

### Data Registers (D/V)
- Store 16-bit or 32-bit numbers
- Counters, timers, calculations
- Example: `D100` = accumulated pump hours

## Hysteresis (Dead Band)

The secret to stable control:

```
Level = 3m (between LOW=2m and HIGH=8m)
├─ Pump OFF previously
├─ Neither condition met
└─ → Pump stays OFF (hysteresis)

Next scan: Level = 2.5m
├─ Below LOW sensor now
├─ Level < 2m → TRUE
└─ → Pump starts

Next scan: Level = 7m
├─ Pump is running
├─ Level < HIGH=8m still
├─ Between LOW and HIGH
└─ → Pump stays ON (hysteresis prevents oscillation)
```

**Without hysteresis:** Pump turns ON/OFF every scan = buzzing relay = burnt-out coil

**With hysteresis:** Smooth operation, safe motor control

## SR Flip-Flop (Set-Reset)

The foundation of PLC memory:

```
S (Set)    R (Reset)    Q (Output)
─────────────────────────────────────
0          0           Q_previous   (Hold)
0          1           0            (Reset)
1          0           1            (Set)
1          1           0            (Reset dominant!)
```

Example: Self-holding pump start button

```
Start_PB = 0  (button released)
Stop_PB = 0   (button released)
─────────────────────────────────
Pump stays ON ← Self-holding (latching)

Start_PB = 1  (button pressed again)
Stop_PB = 0   
─────────────────────────────────
Pump stays ON ← Already set, nothing changes

Start_PB = 0  (button released)
Stop_PB = 1   (button pressed)
─────────────────────────────────
Pump turns OFF ← Reset clears the memory
```

## Safety Concepts

### Hard Override (Interlock)

```python
# Emergency stop ALWAYS wins, regardless of other signals
if emergency_stop:
    pump = False
```

### Fault Detection

```python
# Detect impossible conditions
if sensor_low AND sensor_high:  # Can't be at two heights!
    set_fault_alarm()
    shutdown()
```

### Manual Override

```python
if mode == AUTO:
    # Use sensor-based logic
    pump = plc_logic(sensors)
elif mode == MANUAL:
    # Use operator buttons directly
    pump = start_button
```

## Real-World Example: Tank Control

| Condition | Action | Logic |
|-----------|--------|-------|
| Level < 2m (LOW sensor OFF) | Start pump | `IF NOT I0.0 THEN pump = ON` |
| Level > 8m (HIGH sensor ON) | Stop pump | `IF I0.2 THEN pump = OFF` |
| 2m < Level < 8m (Both OFF/ON mixed) | Hold state | `ELSE pump = pump` |
| Level > 9.5m (OVERFLOW) | STOP NOW | `IF I0.3 THEN pump = OFF` (override) |

## Module Mapping in This Project

- **Module 1**: Why NOT to use time-based control
- **Module 2**: How sensors work (discrete ON/OFF only)
- **Module 3**: Correct closed-loop approach
- **Module 4**: SR flip-flop memory mechanisms
- **Module 5**: Actual PLC scan cycle implementation
- **Module 6**: Fault injection + emergency stop + manual modes
- **Module 7**: Delay timers (TON, TOF, TP)
- **Module 8**: Counters for maintenance tracking
- **Module 9**: Everything integrated
- **Module 10**: Visual ladder diagram

---

**Key Takeaway:** A PLC doesn't think continuously - it thinks in discrete 10ms snapshots. Input → Logic → Output → Repeat.
