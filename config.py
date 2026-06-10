"""
Configuration settings and engineering constants for the PLC Tank Automation System.
Conforms to standard ISO 1219 industrial specs and IEC 61131-3 memory address models.
"""

# Tank Physical Specifications
TANK_HEIGHT = 10.0  # m (Maximum safe tank capacity constraint)
TANK_AREA = 5.0     # m² (Cross-sectional surface area)
PUMP_FLOW = 2.0     # m³/s (Centrifugal pump volumetric discharge rating)
DRAIN_FLOW = 0.8    # m³/s (Constant gravity drain output)

# PLC Discrete Sensor Level Mappings (Digital Input Thresholds)
SENSOR_LOW = 2.0        # I0.0 - Activates filling routine when breached downward
SENSOR_MID = 5.0        # I0.1 - Intermediate baseline trigger
SENSOR_HIGH = 8.0       # I0.2 - Cuts off filling routine when reached upward
SENSOR_OVERFLOW = 9.5   # I0.3 - Emergency hard interlock loop

# Execution Timing
DT = 0.1  # Core PLC cycle scan interval (100ms standard operating loop time)