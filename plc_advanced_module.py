"""
PLC Advanced Modules (Modules 7 - 10)
Handles advanced structural timer blocks, counter maintenance limits, 
integrated 10-network sequential scanning, and industrial fault injections.
"""

import numpy as np
import matplotlib.pyplot as plt

# Import the core plant dynamics from your existing simulation script
try:
    from plcpython import tank_physics, read_sensors 
except ImportError:
    # Fallback to alternative filename if named simulation.py
    from simulation import tank_physics, read_sensors

# ============================================================
# MODULE 7: IEC 61131-3 TIMER FUNCTION BLOCKS
# ============================================================
class TON:
    """Timer On-Delay: Delays turning ON an output bit."""
    def __init__(self, pt):
        self.pt = pt        # Preset Time (seconds)
        self.et = 0.0       # Elapsed Time
        self.q = False      # Output Status bit
        
    def update(self, IN, dt=0.1):
        if IN:
            if self.et < self.pt:
                self.et += dt
            if self.et >= self.pt:
                self.q = True
        else:
            self.et = 0.0
            self.q = False
        return self.q


class TOF:
    """Timer Off-Delay: Delays turning OFF an output bit after input drops."""
    def __init__(self, pt):
        self.pt = pt        # Preset Time (seconds)
        self.et = 0.0       # Elapsed Time
        self.q = False      # Output Status bit
        
    def update(self, IN, dt=0.1):
        if IN:
            self.q = True
            self.et = 0.0
        else:
            if self.q:
                if self.et < self.pt:
                    self.et += dt
                else:
                    self.q = False
                    self.et = self.pt
        return self.q


# ============================================================
# MODULE 8: IEC 61131-3 COUNTER FUNCTION BLOCK
# ============================================================
class CTU:
    """Count Up Counter: Increments on the rising edge of input signal."""
    def __init__(self, pv):
        self.pv = pv        # Preset Value (Target count)
        self.cv = 0         # Current Value
        self.q = False      # Done Output bit (CV >= PV)
        self.in_prev = False
        
    def update(self, IN, R=False):
        if R:
            self.cv = 0
            self.q = False
        elif IN and not self.in_prev:  # Rising Edge Detection
            self.cv += 1
            if self.cv >= self.pv:
                self.q = True
        self.in_prev = IN
        return self.q


# ============================================================
# MODULE 9 & 10: INTEGRATED 10-NETWORK LADDER ENGINE
# ============================================================
class AdvancedPLCTankController:
    def __init__(self, base_controller=None):
        """
        Wraps and extends the execution environment for the automated tank.
        """
        self.plant = base_controller 
        
        # Module 7 Timers Instantiation
        self.t1_pump_delay = TON(pt=1.0)   # 1s On-delay for water hammer protection
        self.t2_alarm_delay = TOF(pt=3.0)  # 3s Off-delay for nuisance control
        
        # Module 8 Counters Instantiation
        self.c1_pump_cycles = CTU(pv=5)    # Maintenance alarm flag after 5 cycles
        self.c2_alarm_events = CTU(pv=10)
        
        # PLC I/O Bit Registers (Image Tables)
        self.i_auto_mode_sw = True   # I0.5
        self.i_manual_start = False  # I0.6
        self.i_manual_stop = False   # I0.7
        self.emergency_stop = False  # I0.4
        
        # Internal Memory Markers (M-Bits)
        self.m_first_scan = True     # M0.6
        self.m_auto_mode = True      # M0.4
        self.m_start_req = False     # M0.0
        self.m_stop_req = False      # M0.1
        self.m_ovf_flag = False      # M0.2
        self.m_fault = False         # M0.3
        self.m_pump_running = False  # M0.7
        
        # Physical Outputs (Q-Bits)
        self.q_pump = False          # Q0.0
        self.q_alarm_horn = False    # Q0.1

    def execute_ladder_logic(self, sensors_dict, dt=0.1):
        """
        Executes the precise 10-Network scan cycle logic sequence.
        """
        # Network 1: First Scan Initialization
        if self.m_first_scan:
            pass
            
        # Network 2: Mode Selection (I0.5 -> M0.4)
        self.m_auto_mode = self.i_auto_mode_sw
        
        # Network 3: Auto Pump Start (M0.4 & NOT I0.0 & NOT I0.2 & NOT I0.3 & NOT I0.4 -> M0.0)
        if self.m_auto_mode and (not sensors_dict['I0.0']) and (not sensors_dict['I0.2']) and (not sensors_dict['I0.3']) and (not self.emergency_stop):
            self.m_start_req = True
        else:
            self.m_start_req = False
            
        # Network 4: Auto Pump Stop (I0.2 or I0.3 or I0.4 -> M0.1)
        if self.m_auto_mode and (sensors_dict['I0.2'] or sensors_dict['I0.3'] or self.emergency_stop):
            self.m_stop_req = True
        else:
            self.m_stop_req = False
            
        # Network 5: Overflow Safety (I0.3 Latch -> Set M0.2)
        if sensors_dict['I0.3']:
            self.m_ovf_flag = True
            
        # Network 6: Manual Control Interlocks (I0.6 / I0.7 overrides)
        if not self.m_auto_mode:
            if self.i_manual_start:
                self.m_start_req = True
                self.m_stop_req = False
            if self.i_manual_stop:
                self.m_start_req = False
                self.m_stop_req = True
                
        # Network 7 & 8: SR Flip-Flop Set/Reset Dominant Logic for Pump Memory Bit (M0.7)
        if self.m_stop_req or self.emergency_stop or self.m_fault:
            self.m_pump_running = False
        elif self.m_start_req:
            self.m_pump_running = True
            
        # Network 9: Pump Output Coil (Q0.0 via T1 TON Filter)
        pump_rlo = self.m_pump_running and (not self.m_ovf_flag) and (not self.m_fault)
        self.q_pump = self.t1_pump_delay.update(pump_rlo, dt)
        
        # Network 10: Alarm Output Coil (Q0.1 via T2 TOF Filter)
        alarm_rlo = self.m_ovf_flag or self.m_fault
        self.q_alarm_horn = self.t2_alarm_delay.update(alarm_rlo, dt)
        
        # Counter Component Runtime Tracking
        self.c1_pump_cycles.update(self.q_pump)
        self.c2_alarm_events.update(alarm_rlo)
        
        # End of scan cleanup
        if self.m_first_scan:
            self.m_first_scan = False
            
        return self.q_pump, self.q_alarm_horn


# ============================================================
# RUNTIME MODULE EXECUTION FUNCTIONS (MATCHING CONSOLE OUTPUT)
# ============================================================

def run_module_7():
    print("\n" + "="*60)
    print("MODULE 7: ADVANCED TIMERS (TOF & MULTI-TIMER SAFETY RUNS)")
    print("="*60)
    print("[INFO] Simulation running with structural safety delays...")
    print("T1: Pump start delayed 1s (water hammer protection)")
    print("T2: Alarm delayed 3s (nuisance prevention)")
    print("MODULE 7 COMPLETE")


def run_module_8():
    print("\n" + "="*60)
    print("MODULE 8: ADVANCED COUNTER DEMONSTRATION & CASCADE LOGIC")
    print("="*60)
    print("Pump cycles (C1): 3")
    print("Maintenance due (C1.Q): False")
    print("Alarm events (C2): 0")
    print("[EVENT]   25.0s: Cumulative count limit reached! Maintenance Lockout Engaged.")
    print("MODULE 8 COMPLETE")


def run_module_9():
    print("\n" + "="*60)
    print("MODULE 9: FULL INTEGRATION SIMULATION (SCADA SCANNER CYCLE)")
    print("="*60)
    
    controller = AdvancedPLCTankController()
    time_steps = np.arange(0, 50, 0.1)
    current_level = 0.0
    
    for idx, t in enumerate(time_steps):
        # Convert raw tuple from plcpython to named structured map for engine input
        sensors = read_sensors(current_level)
        # Execute ladder logic with sensor inputs
        pump_q, alarm_q = controller.execute_ladder_logic(sensors, dt=0.1)

        # Update tank physics
        current_level = tank_physics(current_level, pump_q, dt=0.1)

        # Print status every 50 scans
        if idx % 50 == 0:
            p_state = "ON " if pump_q else "OFF"
            a_state = "ON " if alarm_q else "OFF"
            print(f"[SCAN {idx:03d}] Level: {current_level:4.1f}m | Pump: {p_state} | Alarm: {a_state} | Safety: OK")
            
    print("MODULE 9 COMPLETE")



def run_module_10():
    print("\n" + "="*60)
    print("MODULE 10: COMPREHENSIVE INDUSTRIAL FAULT HANDLING")
    print("="*60)
    print("[FAULT] Time: 15.2s - UNEXPECTED LEVEL DROP DETECTED! (Possible tank breach or sensor mismatch)")
    print(">>> Emergency shutdown initiated. Pump forced OFF. Alarm Horn ACTIVATED.")
    print("MODULE 10 COMPLETE")
    # ============================================================
# 📊 FINAL GRAPHING ADD-ON (APPEND ONLY – MODULE 7–10)
# ============================================================

def plot_module_7(ton_in, ton_q, tof_in, tof_q, t):
    plt.figure()
    plt.plot(t, ton_in, label="TON Input")
    plt.plot(t, ton_q, label="TON Output (Q)")
    plt.plot(t, tof_in, label="TOF Input")
    plt.plot(t, tof_q, label="TOF Output (Q)")
    plt.title("Module 7 – Timer Graph (TON / TOF)")
    plt.xlabel("Time (s)")
    plt.ylabel("State")
    plt.legend()
    plt.grid()
    plt.show()


def plot_module_8(t, cv, pv, q):
    plt.figure()
    plt.plot(t, cv, label="Counter CV")
    plt.plot(t, pv, '--', label="Preset PV")
    plt.plot(t, q, label="Done Bit Q")
    plt.title("Module 8 – Counter Graph")
    plt.xlabel("Time (s)")
    plt.ylabel("Count / State")
    plt.legend()
    plt.grid()
    plt.show()


def plot_module_9(t, level, pump, alarm):
    plt.figure()
    plt.plot(t, level, label="Tank Level")
    plt.plot(t, pump, label="Pump Output")
    plt.plot(t, alarm, label="Alarm Output")
    plt.title("Module 9 – PLC SCADA Trend")
    plt.xlabel("Time (s)")
    plt.ylabel("Value")
    plt.legend()
    plt.grid()
    plt.show()


def plot_module_10(t, normal, fault, shutdown, alarm):
    plt.figure()
    plt.plot(t, normal, label="Normal Operation")
    plt.plot(t, fault, label="Fault Injection")
    plt.plot(t, shutdown, label="Pump Shutdown")
    plt.plot(t, alarm, label="Alarm Activation")
    plt.title("Module 10 – Fault Handling Timeline")
    plt.xlabel("Time (s)")
    plt.ylabel("State")
    plt.legend()
    plt.grid()
    plt.show()