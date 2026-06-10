"""
Plant model equations defining the physical environment dynamics.
"""
from config import TANK_HEIGHT, TANK_AREA, PUMP_FLOW, DRAIN_FLOW

def tank_physics(level, pump_cmd, dt=0.1):
    """
    Simulates real-world water tank level continuity using conservation of fluid volume.
    
    Physics Model Formulation:
        Q_in  = PUMP_FLOW * pump_cmd (clamped by physical height availability)
        Q_out = DRAIN_FLOW (active as long as head level > 0)
        dh/dt = (Q_in - Q_out) / TANK_AREA
        h_new = clamp(h_old + dh)
    """
    # Evaluate input volumetric flow rates
    flow_in = PUMP_FLOW * pump_cmd if level < TANK_HEIGHT else 0.0
    
    # Evaluate output gravity drain flow rates
    flow_out = DRAIN_FLOW if level > 0 else 0.0
    
    # Delta calculations derived using continuous integrations over the cycle time delta
    d_level = (flow_in - flow_out) * dt / TANK_AREA
    new_level = level + d_level
    
    # Saturate values at hardware limits
    return max(0.0, min(TANK_HEIGHT, new_level))