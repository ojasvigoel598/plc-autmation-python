# Quick Start Guide

## Installation (5 minutes)

### Option 1: Using pip (Easiest)

```bash
# Navigate to project directory
cd plc-automation-python

# Create virtual environment
python -m venv .venv

# Activate it
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # macOS/Linux

# Install dependencies
pip install -r requirements.txt

# Run!
python plcpython.py
```

### Option 2: Without virtual environment

```bash
cd plc-automation-python
pip install matplotlib numpy
python plcpython.py
```

## Understanding the Output

### Console Output (Text)

```
MODULE 1 plot created.
PROBLEM: Pump runs for fixed 20s regardless of actual level!
If drain was slower, tank would overflow.
```

This shows the issue with time-based control.

```
SENSOR TRUTH TABLE:
   Level |   I0.0 LOW |   I0.1 MID |  I0.2 HIGH |   I0.3 OVF
     0.5 |      False |      False |      False |      False
     3.0 |       True |      False |      False |      False
```

This shows how the PLC only sees 4 digital states (on/off), not continuous levels.

```
MODULE 5 plot created.

VERIFICATION: Is this really closed-loop?
✓ Pump turns ON only when I0.0 goes OFF (level < 2m)
✓ Pump turns OFF only when I0.2 goes ON (level > 8m)
✓ Between 2m-8m: Pump holds previous state (hysteresis)
```

This confirms the closed-loop control works correctly.

### Graph Output (Visual)

When the script completes, matplotlib displays multiple graphs:

#### Graph 1: Tank Level Over Time
- **Blue line**: Water level in meters
- **Dashed lines**: Sensor thresholds (LOW, HIGH, OVERFLOW)
- **Shows**: How the level stabilizes between sensors

#### Graph 2: Pump Command
- **Green area**: When pump is ON
- **Shows**: Pump only turns ON/OFF at sensor transitions

#### Graph 3: Input States
- **I0.0 (LOW)**: Orange circles
- **I0.2 (HIGH)**: Red squares
- **I0.3 (OVERFLOW)**: Dark red triangles
- **Shows**: What the PLC actually "sees"

## Running Specific Modules

To run only certain modules, you can modify `plcpython.py`:

### Run only Module 1-5 (Basics)

Comment out lines after Module 5:
```python
# In the code, find and comment from line ~941 onward
# This leaves Modules 1-5 running
```

### Run only Module 7 (Timers)

Find and run just the timer section - it's self-contained.

## Modifying Parameters

### Change Tank Size

Line 5-9 in `plcpython.py`:
```python
tank_height = 10.0  # Change this to 20.0 for bigger tank
tank_area = 5.0     # Change this for different area
pump_flow = 2.0     # Faster pump?
drain_flow = 0.8    # Slower drain?
```

### Change Sensor Thresholds

Line 73-76:
```python
SENSOR_LOW = 2.0      # Trigger pump at 1.5m instead?
SENSOR_MID = 5.0      # Optional mid-level sensor
SENSOR_HIGH = 8.0     # Stop pump at 7m instead?
SENSOR_OVERFLOW = 9.5 # Safety limit
```

### Change Simulation Duration

Find `range(2000)` and adjust:
```python
for step in range(2000):  # 2000 steps = 200 seconds
    # Change to range(1000) for 100 seconds
    # Change to range(5000) for 500 seconds
```

## Troubleshooting

### "ModuleNotFoundError: No module named 'matplotlib'"

```bash
pip install matplotlib
# Or if using pip3:
pip3 install matplotlib
```

### "No module named numpy"

```bash
pip install numpy
```

### Virtual environment not activating

**Windows:**
```bash
.venv\Scripts\activate.bat
# or
.venv\Scripts\Activate.ps1
```

**macOS/Linux:**
```bash
source .venv/bin/activate
```

### Graphs not displaying

Make sure matplotlib can display:
```python
# If in WSL or SSH, you might need:
import matplotlib
matplotlib.use('Agg')  # Add this at top of script
plt.savefig('output.png')  # Instead of plt.show()
```

### Script hangs after displaying graphs

This is normal - close the matplotlib window to return to prompt.

## Understanding the Code Structure

### Core Components

```python
# 1. Physics simulation (Module 1)
def tank_physics(level, pump_cmd, dt=0.1):
    # Real-world dynamics

# 2. Sensor reading (Module 2)
def read_sensors(level):
    # Convert continuous level to discrete inputs

# 3. PLC logic (Modules 3-9)
def plc_logic(sensors, pump_state, mode='AUTO'):
    # The control program

# 4. SR Flip-Flop (Module 4)
def sr_flip_flop(S, R, Q_prev):
    # Memory circuit

# 5. Complete PLC (Module 5)
class RealPLCTankController:
    # Full scan cycle: input → logic → output → physics
```

### Execution Flow for Each Module

1. **Initialize** system state
2. **Create simulation loop** (typically 100-2000 iterations)
3. **Execute for each iteration**:
   - Read sensors
   - Run PLC logic
   - Update physics
   - Store results
4. **Plot results** using matplotlib
5. **Print summary** to console

## Learning Path

### Day 1: Understand the Problem
- Run the script
- Look at Module 1 graph (failures)
- Read the console output

### Day 2: Learn Sensors
- Look at Module 2 truth table
- Understand discrete vs continuous
- Try changing SENSOR thresholds

### Day 3: Closed-Loop Control
- Study Module 3 graph
- Compare to Module 1
- Understand hysteresis

### Day 4: Memory & State
- Study Module 4 (SR flip-flop)
- Learn self-holding circuits
- Understand state persistence

### Day 5: Real PLC Operation
- Study Module 5 scan cycle
- Learn input/output sequencing
- Understand timing

### Day 6+: Advanced Features
- Modules 6-9 cover timers, counters, faults, integration
- Try modifying parameters
- Experiment with different scenarios

## Next Steps

### Modify the Code
- Add a third sensor (MID level at 5m)
- Implement a different control strategy
- Add a flow rate display

### Extend the Project
- Add a data logger (save to CSV)
- Create an HTML dashboard
- Build a web UI with Flask

### Learn More
- Study IEC 61131-3 standard
- Get a real PLC simulator (TIA Portal, CODESYS)
- Try actual PLC hardware (Arduino-based PLCs)

