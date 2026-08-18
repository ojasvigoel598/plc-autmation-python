"""SQLite historian tests: store, runtime flush, and REST endpoints."""

from fastapi.testclient import TestClient

from scada import server as server_mod
from scada.historian import TrendStore
from scada.runtime import Runtime


def make_store(tmp_path):
    return TrendStore(path=tmp_path / "trends.sqlite3")


def test_insert_and_roundtrip(tmp_path):
    store = make_store(tmp_path)
    n = store.insert([
        {"t": 0.1, "h1": 1.0, "h2": 0.8},
        {"t": 0.2, "h1": 0.99, "h2": 0.81},
    ])
    assert n == 4  # two records x two series
    assert store.series() == ["h1", "h2"]
    assert store.range("h1", 0.0, 1.0) == [(0.1, 1.0), (0.2, 0.99)]


def test_time_range_and_latest(tmp_path):
    store = make_store(tmp_path)
    store.insert([{"t": 5.0, "h1": 0.5}, {"t": 10.0, "h1": 0.4}])
    assert store.time_range("h1") == (5.0, 10.0)
    assert store.latest("h1") == (10.0, 0.4)


def test_range_downsampling(tmp_path):
    store = make_store(tmp_path)
    store.insert([{"t": float(i), "h1": float(i)} for i in range(100)])
    points = store.range("h1", 0.0, 100.0, max_points=10)
    assert len(points) == 10
    assert points[0][0] < points[-1][0]


def test_clear(tmp_path):
    store = make_store(tmp_path)
    store.insert([{"t": 1.0, "h1": 0.5}])
    store.clear()
    assert store.count() == 0


def test_runtime_flushes_in_batches(tmp_path):
    store = make_store(tmp_path)
    rt = Runtime(historian=store)
    for _ in range(12):
        rt.step()
    # 10 scans flush automatically; the final 2 wait for an explicit flush.
    assert store.count() == 10 * 14
    rt.flush_historian()
    assert store.count() == 12 * 14


def test_runtime_without_historian_is_side_effect_free():
    rt = Runtime()  # historian defaults to None
    rt.step()
    rt.flush_historian()  # must be a no-op, not an AttributeError


def test_history_endpoint_returns_persisted_points(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    store.insert([{"t": 1.0, "h1": 0.5}, {"t": 2.0, "h1": 0.6}])
    monkeypatch.setattr(server_mod.runtime, "historian", store)
    with TestClient(server_mod.create_app(start_loop=False)) as client:
        r = client.get("/api/trends/history", params={"series": "h1"})
        assert r.status_code == 200
        assert r.json()["points"] == [[1.0, 0.5], [2.0, 0.6]]


def test_history_series_endpoint_lists_series(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    store.insert([{"t": 1.0, "h1": 0.5, "h2": 0.8}])
    monkeypatch.setattr(server_mod.runtime, "historian", store)
    with TestClient(server_mod.create_app(start_loop=False)) as client:
        r = client.get("/api/trends/history/series")
        assert r.status_code == 200
        assert r.json()["series"] == ["h1", "h2"]


def test_history_endpoint_degrades_without_store():
    with TestClient(server_mod.create_app(start_loop=False)) as client:
        r = client.get("/api/trends/history", params={"series": "h1"})
        assert r.status_code == 200
        assert r.json() == {"series": "h1", "range": None, "points": []}
