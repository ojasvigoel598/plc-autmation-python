"""Tests for the additional process units: heat exchanger, pressure vessel,
conveyor belt, and their integration with the runtime/PLC."""

import math

import pytest

from scada import config
from scada.process_units import HeatExchanger, PressureVessel, ConveyorBelt
from scada.runtime import Runtime


# ---------------------------------------------------------------------------
# Heat Exchanger
# ---------------------------------------------------------------------------
class TestHeatExchanger:
    def test_cold_outlet_warms_with_hot_flow(self):
        hx = HeatExchanger()
        T_cold_before = hx.cold_out
        for _ in range(300):  # 30 seconds at 0.1s step
            hx.step(0.1)
        assert hx.cold_out > T_cold_before, "cold outlet must gain heat"

    def test_fouling_reduces_heat_transfer(self):
        hx_clean = HeatExchanger(fouling=0.0)
        hx_foul = HeatExchanger(fouling=0.5)
        for _ in range(300):
            hx_clean.step(0.1)
            hx_foul.step(0.1)
        # Fouled unit should have a colder cold outlet (less heat transfer).
        assert hx_foul.cold_out < hx_clean.cold_out

    def test_slower_transfer_when_flows_low(self):
        """With lower flows, the rate of temperature change should be slower."""
        hx_high = HeatExchanger(flow_hot=0.002, flow_cold=0.001)
        hx_low = HeatExchanger(flow_hot=0.0005, flow_cold=0.0002)
        for _ in range(100):
            hx_high.step(0.1)
            hx_low.step(0.1)
        # Low-flow unit should have smaller temperature change (less energy input).
        assert abs(hx_low.cold_out - 298.0) < abs(hx_high.cold_out - 298.0)

    def test_read_snapshot_keys(self):
        hx = HeatExchanger()
        d = hx.read()
        assert "T_hot_in" in d and "heat_transfer_W" in d


# ---------------------------------------------------------------------------
# Pressure Vessel
# ---------------------------------------------------------------------------
class TestPressureVessel:
    def test_pressure_rises_with_inlet_no_outlet(self):
        pk = PressureVessel(inlet_flow=0.001, valve_position=0.0)
        P0 = pk.pressure
        for _ in range(100):
            pk.step(0.1)
        assert pk.pressure > P0

    def test_pressure_drops_with_open_valve(self):
        pk = PressureVessel(inlet_flow=0.0, valve_position=1.0, P_initial=110000)
        for _ in range(100):
            pk.step(0.1)
        assert pk.pressure < 110000

    def test_pressure_bar_conversion(self):
        pk = PressureVessel()
        assert pk.pressure_bar == pytest.approx(pk.pressure / 100000.0)

    def test_read_snapshot_keys(self):
        pk = PressureVessel()
        d = pk.read()
        assert "pressure_Pa" in d and "valve_position" in d


# ---------------------------------------------------------------------------
# Conveyor Belt
# ---------------------------------------------------------------------------
class TestConveyorBelt:
    def test_motor_starts_and_reaches_speed(self):
        cv = ConveyorBelt()
        cv.target_speed = 2.0
        cv.start()
        for _ in range(50):
            cv.step(0.1)
        assert cv.speed > 1.0, "conveyor should accelerate toward target"
        assert cv.running is True

    def test_motor_stops(self):
        cv = ConveyorBelt()
        cv.start()
        cv.target_speed = 2.0
        for _ in range(50):
            cv.step(0.1)
        cv.stop()
        for _ in range(50):
            cv.step(0.1)
        assert cv.speed < 0.5

    def test_jam_detection(self):
        cv = ConveyorBelt()
        cv.start()
        cv.target_speed = 2.0
        # Jam the motor.
        cv.motor_fault = True
        for _ in range(50):
            cv.step(0.1)
        assert cv.speed == 0.0
        assert cv.running is True  # still "running" in PLC sense

    def test_read_snapshot_keys(self):
        cv = ConveyorBelt()
        d = cv.read()
        assert "speed" in d and "running" in d and "jammed" in d


# ---------------------------------------------------------------------------
# Runtime integration
# ---------------------------------------------------------------------------
class TestRuntimeProcessUnits:
    def test_process_units_step_included(self):
        rt = Runtime()
        rt.plc.pulse_start()
        for _ in range(10):
            rt.step()
        pu = rt.snapshot()["process_units"]
        assert "heat_exchanger" in pu
        assert "pressure_vessel" in pu
        assert "conveyor" in pu

    def test_temperature_transmitter_in_plc(self):
        rt = Runtime()
        rt.plc.pulse_start()
        for _ in range(5):
            rt.step()
        # TT-101 should be populated from the heat exchanger.
        assert "TT-101" in rt.plc.ai

    def test_pressure_transmitter_in_plc(self):
        rt = Runtime()
        rt.plc.pulse_start()
        for _ in range(5):
            rt.step()
        assert "PT-101" in rt.plc.ai

    def test_tic101_aq_in_snapshot(self):
        rt = Runtime()
        rt.plc.pulse_start()
        for _ in range(5):
            rt.step()
        s = rt.snapshot()
        assert "FV-101" in s["plc"]["aq"]
        assert "PCV-101" in s["plc"]["aq"]


# ---------------------------------------------------------------------------
# Temperature and pressure alarms
# ---------------------------------------------------------------------------
class TestProcessAlarms:
    def test_high_temperature_alarm(self):
        rt = Runtime()
        rt.plc.pulse_start()
        for _ in range(5):
            rt.step()
        # Force a high temperature reading.
        rt.plc.ai["TT-101"] = config.TEMP_HI + 10.0
        rt.plc.scan(0.1)
        assert any(a.tag == "TSH-101" for a in rt.plc.alarms.active_alarms())

    def test_high_pressure_alarm(self):
        rt = Runtime()
        rt.plc.pulse_start()
        for _ in range(5):
            rt.step()
        rt.plc.ai["PT-101"] = config.PRESSURE_HI + 1000
        rt.plc.scan(0.1)
        assert any(a.tag == "PSH-101" for a in rt.plc.alarms.active_alarms())
