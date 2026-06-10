"""
IEC 61131-3 logical function blocks, memory flip-flops, and low-level combinatorics.
"""
from config import SENSOR_LOW, SENSOR_MID, SENSOR_HIGH, SENSOR_OVERFLOW

def read_sensors(level):
    """
    Input Scan Routine: Simulates Analog-to-Digital field bus conversions
    by updating specific memory addresses according to continuous telemetry values.
    """
    return {
        'I0.0': level >= SENSOR_LOW,      # LOW level indicator
        'I0.1': level >= SENSOR_MID,      # MID level indicator
        'I0.2': level >= SENSOR_HIGH,     # HIGH level indicator
        'I0.3': level >= SENSOR_OVERFLOW, # Emergency OVERFLOW indicator
    }

def plc_logic(sensors, pump_state, mode='AUTO'):
    """
    Functional implementation of hysteresis control loops using raw sensory feedback matrices.
    """
    i_low = sensors['I0.0']
    i_high = sensors['I0.2']
    i_overflow = sensors['I0.3']
    
    if mode == 'AUTO':
        if not i_low and not i_overflow:
            pump_cmd = True
        elif i_high or i_overflow:
            pump_cmd = False
        else:
            pump_cmd = pump_state
    else:
        pump_cmd = pump_state
        
    if i_overflow:
        pump_cmd = False
        
    return pump_cmd

def sr_flip_flop(S, R, Q_prev):
    """
    Simulates hardware SR Latch execution.
    Features a Reset-Dominant condition configuration for strict process safety.
    """
    if R:
        return False
    elif S:
        return True
    else:
        return Q_prev

def plc_logic_sr(sensors, pump_state, mode='AUTO'):
    """
    Functional implementation utilizing structured SR Flip-Flops for cleaner trace tracks.
    """
    i_low = sensors['I0.0']
    i_high = sensors['I0.2']
    i_overflow = sensors['I0.3']
    
    S = (not i_low) and (not i_overflow)
    if mode == 'MANUAL':
        S = False
        
    R = i_high or i_overflow
    pump_cmd = sr_flip_flop(S, R, pump_state)
    
    if i_overflow:
        pump_cmd = False
        
    return pump_cmd