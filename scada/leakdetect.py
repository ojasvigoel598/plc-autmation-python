"""
Mass-balance leak detection.

For each tank we integrate, over a sliding window, the volume change that the
*measured* flows explain and compare it with the volume change the level
actually shows.  A sustained unexplained loss is the signature of a leak:
the plant's `leaks[tag]` term removes volume from the tank without appearing
in any measured flow, so the balance closes with a positive residual.

This mirrors a real volume-balance / line-balance leak detector (used in
tank farms and pipelines), adapted to the discrete scan cycle.  The residual
is:

    unexplained_loss = explained - observed
    explained        = (Q_in - Q_out) * dt - (overflow volume change)
    observed         = (level - level_prev) * area

A leak is reported only once the cumulative unexplained loss exceeds both a
rate and a volume threshold over the window, which rejects short transients.

The detector uses the *process* level (true state) rather than the noisy
transmitter reading, so it is deterministic and cannot false-trip on sensor
noise.  A real installation would instead use field instruments and therefore
need proportionally larger thresholds; that difference is a documented
approximation.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import config


@dataclass
class _Balance:
    explained: float = 0.0       # m^3 : measured net volume change
    observed: float = 0.0        # m^3 : level-derived volume change
    prev_level: float = 0.0      # m
    prev_overflow: float = 0.0   # m^3
    window_start: float = 0.0    # s
    last_rate: float = 0.0       # m^3/s : latest unexplained-loss estimate


class MassBalanceLeakDetector:
    """One rolling balance accumulator per tank."""

    def __init__(self) -> None:
        self._bal: dict[str, _Balance] = {}

    def update(self, tag: str, *, level: float, q_in: float, q_out: float,
               overflow_volume: float, dt: float, t: float, area: float) -> float:
        """Advance the balance for one tank by one scan and return the current
        estimated leak rate (m^3/s).  The estimate is refreshed once per
        window; between refreshes the previous estimate is returned."""
        b = self._bal.get(tag)
        if b is None:
            self._bal[tag] = _Balance(prev_level=level,
                                      prev_overflow=overflow_volume,
                                      window_start=t)
            return 0.0

        b.explained += (q_in - q_out) * dt - (overflow_volume - b.prev_overflow)
        b.observed += (level - b.prev_level) * area
        b.prev_level = level
        b.prev_overflow = overflow_volume

        elapsed = t - b.window_start
        if elapsed >= config.LEAK_DETECT_WINDOW and elapsed > 0.0:
            b.last_rate = max(0.0, (b.explained - b.observed) / elapsed)
            b.explained = 0.0
            b.observed = 0.0
            b.window_start = t
        return b.last_rate
