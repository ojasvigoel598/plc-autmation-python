"""Physical plant model tests: orifice flow, conservation of mass, limits."""

import math

import pytest

from scada import config
from scada.process import Plant


def test_orifice_flow_proportional_to_sqrt_head():
    plant = Plant()
    plant.levels = {"TK-101": 1.0, "TK-102": 0.5, "TK-103": 0.3}
    valve = plant.valves["XV-101"]
    valve.open_frac = 1.0
    valve.target = 1.0
    valve.blocked = False

    flow = plant._valve_flow(valve, plant.levels)
    head = (1.5 + 1.0) - (1.0 + 0.5)  # z1+h1 - (z2+h2) = 1.0 m
    expected = valve.kv * math.sqrt(head)
    assert flow == pytest.approx(expected, rel=1e-9)


def test_no_flow_against_adverse_head():
    plant = Plant()
    # Downstream tank higher than upstream: gravity flow is zero.
    plant.levels = {"TK-101": 0.2, "TK-102": 1.9, "TK-103": 0.3}
    valve = plant.valves["XV-101"]
    valve.open_frac = 1.0
    valve.target = 1.0
    assert plant._valve_flow(valve, plant.levels) == 0.0


def test_no_flow_when_valve_closed():
    plant = Plant()
    valve = plant.valves["XV-101"]
    valve.open_frac = 0.0
    valve.target = 0.0
    assert plant._valve_flow(valve, plant.levels) == 0.0


def test_mass_balance_conserved():
    """Total tank volume change equals net inflow/outflow over time."""
    plant = Plant()
    plant.set_actuators(pump_speed=0.6,
                        valve_open={"XV-101": 0.6, "XV-102": 0.5, "XV-103": 0.4})

    v0 = sum(t.area * plant.levels[t.tag] for t in plant.tanks.values())
    net = 0.0
    dt = config.PLC_SCAN_DT
    for _ in range(500):  # 50 s
        plant.step(dt)
        # v103 and overflow leave the system; pump enters it.
        net += (plant.flows["P-101"] - plant.flows["XV-103"]
                - plant.flows["overflow"]) * dt
        net -= sum(plant.leaks.values()) * dt

    v1 = sum(t.area * plant.levels[t.tag] for t in plant.tanks.values())
    # RK4 with 10 sub-steps keeps the balance far tighter than 1% of throughput.
    assert abs((v1 - v0) - net) < 0.002


def test_overflow_clamps_and_reports_spill():
    plant = Plant()
    plant.set_actuators(pump_speed=1.0,
                        valve_open={"XV-101": 0.0, "XV-102": 0.0, "XV-103": 0.0})
    for _ in range(2000):  # pump full into TK-101 with all outlets shut
        plant.step(0.1)
    assert plant.levels["TK-101"] == pytest.approx(config.TANK_HEIGHT)
    assert plant.overflow_volume["TK-101"] > 0.0


def test_empty_tank_never_goes_negative():
    plant = Plant()
    plant.levels = {"TK-101": 0.0, "TK-102": 0.0, "TK-103": 0.0}
    plant.set_actuators(pump_speed=0.0,
                        valve_open={"XV-101": 1.0, "XV-102": 1.0, "XV-103": 1.0})
    for _ in range(200):
        plant.step(0.1)
    for level in plant.levels.values():
        assert level >= 0.0


def test_valve_slew_rate_limits_travel():
    plant = Plant()
    plant.valves["XV-103"].open_frac = 0.0
    plant.valves["XV-103"].target = 1.0
    plant.step(0.1)   # one scan = 0.1 s, slew 0.5/s -> max +0.05
    assert plant.valves["XV-103"].open_frac <= 0.05 + 1e-9
