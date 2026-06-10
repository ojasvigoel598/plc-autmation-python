"""
Object-Oriented full scale PLC Scan Controller Implementations.
"""
from config import TANK_HEIGHT, TANK_AREA, PUMP_FLOW, DRAIN_FLOW, SENSOR_LOW, SENSOR_MID, SENSOR_HIGH, SENSOR_OVERFLOW, DT
from plc_logic import sr_flip_flop

class RealPLCTankController:
    """
    Simulates a running 5-Phase architecture PLC framework engine execution layer.
    """
    def __init__(self):
        self.tank_height = TANK_HEIGHT
        self.tank_area = TANK_AREA
        self.pump_flow = PUMP_FLOW
        self.drain_flow = DRAIN_FLOW
        
        self.sensor_low = SENSOR_LOW
        self.sensor_mid = SENSOR_MID
        self.sensor_high = SENSOR_HIGH
        self.sensor_overflow = SENSOR_OVERFLOW
        
        self.level = 0.0
        self.m_pump_running = False    # M0.7 - Internal Motor Latch Flag
        self.m_auto_mode = True        # M0.4 - Automation State Register
        self.m_fault = False           # M0.3 - General Fault Bit Register
        
        self.i_low = False
        self.i_high = False
        self.i_overflow = False
        self.q_pump = False            # Q0.0 - Digital Relay Actuator Terminal
        self.emergency_stop = False    # I0.4 - Hardware Safety input line

    def read_inputs(self):
        """Phase 1: Input Scan Routine Process mapping."""
        self.i_low = self.level >= self.sensor_low
        self.i_high = self.level >= self.sensor_high
        self.i_overflow = self.level >= self.sensor_overflow

    def execute_logic(self):
        """Phase 2: Ladder program calculation cycle execution."""
        S = (not self.i_low) and (not self.i_overflow) and (not self.emergency_stop)
        R = self.i_high or self.i_overflow or self.emergency_stop
        self.m_pump_running = sr_flip_flop(S, R, self.m_pump_running)
        
        if self.i_overflow or self.emergency_stop:
            self.m_pump_running = False
            
        self.q_pump = self.m_pump_running

    def write_outputs(self):
        """Phase 3: Synchronize updated control registers to field devices."""
        return self.q_pump

    def update_physics(self, pump_cmd):
        """Phase 4 & 5: Simulation environmental timeline calculation responses."""
        flow_in = self.pump_flow * pump_cmd
        flow_out = self.drain_flow if self.level > 0 else 0.0
        d_level = ((flow_in - flow_out) * DT) / self.tank_area
        self.level += d_level
        self.level = max(0.0, min(self.tank_height, self.level))

    def scan_cycle(self):
        """Executes a full discrete scan sequence loop iteration."""
        self.read_inputs()
        self.execute_logic()
        pump_cmd = self.write_outputs()
        self.update_physics(pump_cmd)
        return pump_cmd


class PLCTankControllerAdvanced:
    """
    Advanced Industrial Controller implementation featuring manual interventions, 
    diagnostics, alarm systems, and error processing states.
    """
    def __init__(self):
        self.tank_height = TANK_HEIGHT
        self.tank_area = TANK_AREA
        self.pump_flow = PUMP_FLOW
        self.drain_flow = DRAIN_FLOW
        
        self.sensor_low = SENSOR_LOW
        self.sensor_mid = SENSOR_MID
        self.sensor_high = SENSOR_HIGH
        self.sensor_overflow = SENSOR_OVERFLOW
        
        self.level = 0.0
        self.m_pump_running = False    # M0.7
        self.m_fault = False           # M0.3
        self.m_ovf_flag = False        # M0.2
        
        self.emergency_stop = False    # I0.4
        self.mode_manual = False       # I0.5
        self.manual_start = False      # I0.6
        self.manual_stop = False       # I0.7
        
        self.q_pump = False            # Q0.0
        self.q_alarm_horn = False      # Q0.1
        self.q_alarm_light = False     # Q0.2
        self.q_status_light = True     # Q0.3
        
        self.fault_high_stuck = False
        self.i_low = False
        self.i_high = False
        self.i_overflow = False

    def read_inputs(self):
        self.i_low = self.level >= self.sensor_low
        self.i_high = self.level >= self.sensor_high
        self.i_overflow = self.level >= self.sensor_overflow
        
        # Inject custom operational line simulation faults
        if self.fault_high_stuck:
            self.i_high = True

    def execute_logic(self):
        auto_mode = not self.mode_manual
        
        # Rung 3: Automated logic monitoring pathways
        S_auto = auto_mode and (not self.i_low) and (not self.i_overflow) and (not self.emergency_stop)
        R_auto = self.i_high or self.i_overflow or self.emergency_stop
        
        # Rung 5: Safety check triggers
        if self.i_overflow:
            self.m_ovf_flag = True
            R_auto = True
            
        # Rung 6: Manual interventions processing loops
        S_manual = self.mode_manual and self.manual_start and (not self.emergency_stop)
        R_manual = self.mode_manual and self.manual_stop
        
        S = S_auto or S_manual
        R = R_auto or R_manual or self.m_fault
        
        # Rungs 7-8: Latching memory configurations
        if R:
            self.m_pump_running = False
        elif S:
            self.m_pump_running = True
            
        # Rung 9: Integrity checking algorithms (Logical impossibilities verification)
        if self.i_high and (not self.i_low):
            self.m_fault = True
        if self.i_overflow and (not self.i_high):
            self.m_fault = True
            
        if self.m_fault:
            self.m_pump_running = False
            
        # Rung 10: Map drive operations out with interlock blocks applied
        self.q_pump = self.m_pump_running and (not self.m_ovf_flag) and (not self.m_fault)
        
        # Rung 11: Set alert system warning lights and signals
        alarm = self.m_ovf_flag or self.m_fault or self.emergency_stop
        self.q_alarm_horn = alarm
        self.q_alarm_light = alarm
        self.q_status_light = not alarm

    def update_physics(self):
        flow_in = self.pump_flow * self.q_pump
        flow_out = self.drain_flow if self.level > 0 else 0.0
        d_level = ((flow_in - flow_out) * DT) / self.tank_area
        self.level += d_level
        self.level = max(0.0, min(self.tank_height, self.level))

    def scan_cycle(self):
        self.read_inputs()
        self.execute_logic()
        self.update_physics()
        return self.q_pump