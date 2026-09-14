"""Throughput/latency benchmark for `build_martin_wrapper_app` (app/martin_wrapper.py).

Not a correctness test — `test_martin_wrapper.py` already covers that. This
drives `get_tile` requests through the sub-app's `TestClient` (in-process,
no real socket) to get a repeatable read on the wrapper's own overhead:

1. 100 warm-up requests, discarded, so the timed run doesn't pay for the
   TestClient/anyio startup cost or any lazy first-call work.
2. 1000 timed requests, from which mean/median/p95/p99 latency and an
   overall requests-per-second figure are computed and printed.

Every request targets a random `{z}/{x}/{y}` (with `0 <= x, y < 2**z`, the
only constraint martin-py's routing enforces) against the same single-point
fixture source `test_martin_wrapper.py` uses, so most tiles come back empty
of features but every one is a legitimate, independently-resolved tile
request — this measures the wrapper's per-request path, not one cached
response served over and over.

There is no hard latency assertion: wall-clock speed is CPU- and
load-dependent, so pinning a threshold here would make the suite flaky on
slower CI runners. The test's pass/fail signal is correctness (every one of
the 1100 requests must resolve, not error) — the timing is reported, not
graded. Run `pytest -s tests/test_martin_wrapper_benchmark.py` to see it.
"""

from __future__ import annotations

import random
import statistics
import time
from dataclasses import dataclass

import pytest
from starlette.testclient import TestClient

from tests.test_martin_wrapper import FIXTURE_GEOJSON, _write_martin_config

pytest.importorskip(
    "martin_py", reason="the /martin-wrapper benchmark needs the `martin_wrapper` extra"
)

WARMUP_REQUESTS = 100
BENCHMARK_REQUESTS = 1000
SOURCE_ID = "wrapper_source"
# Deep enough to exercise a realistic tile pyramid, shallow enough that
# 2**z stays a sane upper bound for random x/y at every level tried.
MAX_ZOOM = 14


@dataclass(frozen=True)
class _BenchmarkStats:
    count: int
    total_seconds: float
    latencies_ms: list[float]

    @property
    def requests_per_second(self) -> float:
        return self.count / self.total_seconds if self.total_seconds else float("inf")

    @property
    def mean_ms(self) -> float:
        return statistics.mean(self.latencies_ms)

    @property
    def median_ms(self) -> float:
        return statistics.median(self.latencies_ms)

    @property
    def p95_ms(self) -> float:
        return statistics.quantiles(self.latencies_ms, n=100)[94]

    @property
    def p99_ms(self) -> float:
        return statistics.quantiles(self.latencies_ms, n=100)[98]

    def report(self) -> str:
        return (
            f"{self.count} requests in {self.total_seconds:.3f}s "
            f"({self.requests_per_second:.1f} req/s) — "
            f"latency mean={self.mean_ms:.3f}ms median={self.median_ms:.3f}ms "
            f"p95={self.p95_ms:.3f}ms p99={self.p99_ms:.3f}ms "
            f"min={min(self.latencies_ms):.3f}ms max={max(self.latencies_ms):.3f}ms"
        )


def _random_tile(rng: random.Random) -> tuple[int, int, int]:
    """A random, routing-valid `(z, x, y)` — `0 <= x, y < 2**z`."""
    z = rng.randint(0, MAX_ZOOM)
    span = max(2**z, 1)
    return z, rng.randint(0, span - 1), rng.randint(0, span - 1)


def _run_tiles(client: TestClient, rng: random.Random, count: int) -> _BenchmarkStats:
    latencies_ms: list[float] = []
    start = time.perf_counter()
    for _ in range(count):
        z, x, y = _random_tile(rng)
        request_start = time.perf_counter()
        response = client.get(f"/{SOURCE_ID}/{z}/{x}/{y}")
        latencies_ms.append((time.perf_counter() - request_start) * 1000)
        assert response.status_code == 200, (
            f"tile {z}/{x}/{y} failed: {response.status_code} {response.text}"
        )
        assert response.headers["content-type"] == "application/x-protobuf"
    total_seconds = time.perf_counter() - start
    return _BenchmarkStats(count=count, total_seconds=total_seconds, latencies_ms=latencies_ms)


def test_build_martin_wrapper_app_get_tile_benchmark(tmp_path, capsys):
    """Warm up with 100 random tiles, then benchmark 1000 more."""
    from app.martin_wrapper import build_martin_wrapper_app

    config_path = _write_martin_config(tmp_path, source_id=SOURCE_ID)
    sub_app = build_martin_wrapper_app(str(config_path))
    rng = random.Random(1234)  # ruff: ignore[suspicious-non-cryptographic-random-usage] - reproducible benchmark

    with TestClient(sub_app) as client:
        _run_tiles(client, rng, WARMUP_REQUESTS)
        stats = _run_tiles(client, rng, BENCHMARK_REQUESTS)

    with capsys.disabled():
        print(f"\n[martin-wrapper benchmark] {FIXTURE_GEOJSON.name}: {stats.report()}")

    assert stats.count == BENCHMARK_REQUESTS
