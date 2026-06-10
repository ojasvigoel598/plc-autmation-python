"""
Simulation test runner classes preserving all original structural visualization outputs.
"""
import matplotlib.pyplot as plt
from config import TANK_HEIGHT, SENSOR_LOW, SENSOR_HIGH, SENSOR_OVERFLOW
from physics import tank_physics
from plc_logic import read_sensors, plc_logic, sr_flip_flop
from controllers import RealPLCTankController, PLCTankControllerAdvanced

def run_module_1():
    print("\n--- Running Module 1: Open-Loop Time-Based Control ---")
    level = 0.0
    history = []
    for step in range(500):
        t = step * 0.1
        pump_cmd = 1.0 if step < 200 else 0
        level = tank_physics(level, pump_cmd)
        history.append((t, level, pump_cmd))
        
    t_vals, l_vals, p_vals = [h[0] for h in history], [h[1] for h in history], [h[2] for h in history]
    
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 5), sharex=True)
    ax1.plot(t_vals, l_vals, 'b-', label='Water Level')
    ax1.axhline(y=TANK_HEIGHT, color='r', linestyle='--', label='Tank Overflow (10m)')
    ax1.set_ylabel('Level (m)')
    ax1.set_title('MODULE 1: Open-Loop Time-Based Control (Unreliable)')
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    
    ax2.fill_between(t_vals, 0, p_vals, alpha=0.3, color='green', label='Pump Energized')
    ax2.set_xlabel('Time (s)')
    ax2.set_ylabel('Pump State')
    ax2.set_ylim(-0.1, 1.2)
    ax2.legend()
    ax2.grid(True, alpha=0.3)
    plt.tight_layout()

def run_module_2():
    print("\n--- Running Module 2: Discrete Level Sensors ---")
    print("SENSOR TRUTH TABLE - Input Scan Results:")
    print("=" * 50)
    print(f"{'Level':>8} | {'I0.0 LOW':>10} | {'I0.1 MID':>10} | {'I0.2 HIGH':>10} | {'I0.3 OVF':>10}")
    print("-" * 50)
    for test_level in [0.5, 1.5, 3.0, 6.0, 8.5, 9.8]:
        sensors = read_sensors(test_level)
        print(f"{test_level:8.1f} | {str(sensors['I0.0']):>10} | {str(sensors['I0.1']):>10} | {str(sensors['I0.2']):>10} | {str(sensors['I0.3']):>10}")

def run_module_3():
    print("\n--- Running Module 3: Closed-Loop Sensor-Based Control ---")
    level = 0.0
    pump_running = False
    history = []
    for step in range(2000):
        t = step * 0.1
        sensors = read_sensors(level)
        pump_cmd = plc_logic(sensors, pump_running, mode='AUTO')
        pump_running = pump_cmd
        level = tank_physics(level, pump_running)
        history.append((t, level, pump_running))
        
    t_vals, l_vals, p_vals = [h[0] for h in history], [h[1] for h in history], [h[2] for h in history]
    
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 5), sharex=True)
    ax1.plot(t_vals, l_vals, 'b-', label='Level')
    ax1.axhline(y=SENSOR_LOW, color='orange', linestyle='--', label='LOW (2m)')
    ax1.axhline(y=SENSOR_HIGH, color='orange', linestyle='--', label='HIGH (8m)')
    ax1.axhline(y=SENSOR_OVERFLOW, color='red', linestyle='-', label='OVERFLOW (9.5m)')
    ax1.set_ylabel('Level (m)')
    ax1.set_title('MODULE 3: CLOSED-LOOP (Sensor-Based)')
    ax1.legend(loc='upper right', fontsize=8)
    ax1.grid(True)
    
    ax2.fill_between(t_vals, 0, p_vals, alpha=0.3, color='green', label='Pump ON')
    ax2.set_xlabel('Time (s)')
    ax2.set_ylabel('Pump State')
    ax2.set_ylim(-0.1, 1.2)
    ax2.grid(True)
    plt.tight_layout()

def run_module_4():
    print("\n--- Running Module 4: SR Flip-Flop Circuit Trace ---")
    Q = False
    for scan in range(5):
        Q = sr_flip_flop(False, False, Q)
        print(f"Scan {scan}: S=False, R=False, Q_new={Q}")
    print("Simulating Operator Starting Pump...")
    Q = True
    for scan in range(5):
        Q = sr_flip_flop(False, False, Q)
        print(f"Scan {scan}: S=False, R=False, Q_new={Q}")

def run_module_5():
    print("\n--- Running Module 5: Complete PLC Scan Cycle ---")
    plc = RealPLCTankController()
    history = []
    for step in range(2000):
        t = step * 0.1
        pump_cmd = plc.scan_cycle()
        history.append({'t': t, 'level': plc.level, 'pump': pump_cmd, 'i_low': plc.i_low, 'i_high': plc.i_high, 'i_ovf': plc.i_overflow})
        
    fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True)
    t_v, l_v, p_v = [h['t'] for h in history], [h['level'] for h in history], [h['pump'] for h in history]
    
    axes[0].plot(t_v, l_v, 'b-', label='Level')
    axes[0].axhline(y=SENSOR_LOW, color='orange', linestyle='--', label='LOW I0.0')
    axes[0].axhline(y=SENSOR_HIGH, color='orange', linestyle='--', label='HIGH I0.2')
    axes[0].set_ylabel('Level (m)')
    axes[0].legend(loc='upper right')
    axes[0].grid(True)
    
    axes[1].fill_between(t_v, 0, p_v, alpha=0.3, color='green', label='Q0.0 Pump Output')
    axes[1].set_ylabel('State')
    axes[1].set_ylim(-0.1, 1.2)
    axes[1].grid(True)
    
    low_v = [1 if h['i_low'] else 0 for h in history]
    high_v = [1 if h['i_high'] else 0 for h in history]
    axes[2].plot(t_v, low_v, color='orange', label='I0.0 LOW')
    axes[2].plot(t_v, high_v, color='red', label='I0.2 HIGH')
    axes[2].set_xlabel('Time (s)')
    axes[2].set_ylabel('Sensor Bit')
    axes[2].set_ylim(-0.1, 1.2)
    axes[2].legend()
    axes[2].grid(True)
    plt.tight_layout()

def run_module_6():
    print("\n--- Running Module 6: Advanced Control Actions ---")
    plc = PLCTankControllerAdvanced()
    history = []
    events = {
        30.0:  ("fault_high_on", None),    60.0:  ("fault_high_off", None),
        100.0: ("manual_mode", True),      120.0: ("manual_stop", None),
        140.0: ("auto_mode", None),        170.0: ("estop", True),
        190.0: ("estop", False),
    }
    
    for step in range(2500):
        t = step * 0.1
        if t in events:
            action, data = events[t]
            if action == "fault_high_on": plc.fault_high_stuck = True
            elif action == "fault_high_off": plc.fault_high_stuck = False; plc.m_fault = False
            elif action == "manual_mode": plc.mode_manual = True; plc.manual_start = True
            elif action == "manual_stop": plc.manual_start = False; plc.manual_stop = True
            elif action == "auto_mode": plc.mode_manual = False; plc.manual_stop = False
            elif action == "estop": plc.emergency_stop = data
              
        plc.scan_cycle()
        history.append({'t': t, 'level': plc.level, 'pump': plc.q_pump, 'alarm': plc.q_alarm_horn, 'manual': plc.mode_manual})
        
    fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True)
    t_s = [h['t'] for h in history]
    
    axes[0].plot(t_s, [h['level'] for h in history], 'b-', label='Level')
    axes[0].set_ylabel('Level (m)')
    axes[0].grid(True)
    
    axes[1].fill_between(t_s, 0, [1 if h['pump'] else 0 for h in history], alpha=0.3, color='green', label='Pump Output')
    axes[1].set_ylabel('Pump Q0.0')
    axes[1].set_ylim(-0.1, 1.2)
    axes[1].grid(True)
    
    axes[2].fill_between(t_s, 0, [1 if h['alarm'] else 0 for h in history], alpha=0.3, color='red', label='Alarm Active')
    axes[2].set_xlabel('Time (s)')
    axes[2].set_ylabel('Alarm Horn')
    axes[2].set_ylim(-0.1, 1.2)
    axes[2].grid(True)
    plt.tight_layout()
    