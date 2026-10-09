"""End-to-end load test: synthetic sensors -> connectors (HTTP) -> Redis stream -> worker -> ClickHouse.

Measures what the system actually does, not what it could do on paper:
  * accepted events/s at the connectors and HTTP latency (p50/p95/p99);
  * events/s the worker wrote to ClickHouse until the stream drained, and the peak backlog;
  * a ClickHouse row count check that nothing was lost.

It registers N synthetic climate sensors (load-0001...) and a dedicated API key with a high rate limit,
and cleans up afterwards: sensors are disabled (their data stays, marked by the load-* prefix), the key revoked.
Values stay inside the normal range, so the test does not flood the alert feed.

    python tools/loadtest.py --sensors 200 --duration 30 --concurrency 16 --batch 100
"""

import argparse
import asyncio
import os
import random
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

API = os.environ.get("API_URL", "http://127.0.0.1:8000")
CONNECTORS = os.environ.get("CONNECTORS_URL", "http://127.0.0.1:8001")
REPORT = Path(__file__).resolve().parents[1] / "docs" / "perf.md"
DAY_MS = 86_400_000


def pct(values: list[float], q: float) -> float:
    return statistics.quantiles(values, n=100)[int(q) - 1] if len(values) >= 2 else (values[0] if values else 0.0)


async def admin_client() -> httpx.AsyncClient:
    c = httpx.AsyncClient(base_url=API, timeout=30)
    r = await c.post("/auth/login", json={"username": os.environ.get("LOAD_USER", "admin"),
                                          "password": os.environ.get("LOAD_PASSWORD", "demo")})
    r.raise_for_status()
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return c


async def register(api: httpx.AsyncClient, ids: list[str]) -> None:
    existing = {s["id"] for s in (await api.get("/sensors", params={"type": "climate"})).json()}
    new = [i for i in ids if i not in existing]
    if new:
        csv = "id,type,name\n" + "".join(f"{i},climate,Нагрузочный тест {i}\n" for i in new)
        r = await api.post("/sensors/bulk", params={"dry_run": False}, files={"file": ("load.csv", csv, "text/csv")})
        r.raise_for_status()
        if r.json()["created"] != len(new):
            raise SystemExit(f"could not register sensors: {r.json()['errors'][:3]}")
    for i in ids:  # re-enable sensors left disabled by a previous run
        if i in existing:
            await api.patch(f"/sensors/{i}", json={"enabled": True})


async def worker_stats(api: httpx.AsyncClient) -> dict:
    # worker publishes its counters to Redis; the API does not expose them, so read them through the stack's Redis
    import redis.asyncio as aioredis

    r = aioredis.Redis.from_url(os.environ.get("REDIS_URL", f"redis://127.0.0.1:{os.environ.get('REDIS_PORT', '6379')}/0"),
                                decode_responses=True)
    try:
        # one hash per worker replica: sum the counters, the lag is the group's and the same in all
        stats = [await r.hgetall(k) async for k in r.scan_iter("worker:stats:*")]
        return {"stored": sum(int(s.get("stored_total", 0)) for s in stats),
                "lag": max((int(float(s.get("lag", 0))) for s in stats), default=0),
                "replicas": len(stats)}
    finally:
        await r.aclose()


def clickhouse_rows(since: datetime) -> int:
    """Rows this test sent that really landed in ClickHouse (deduplicated).

    The simulator also feeds every enabled climate sensor, the synthetic ones included, with ts = now; the test's
    own events are measured a day before this run's start, which is how they are told apart from it and from
    earlier runs (the received_at window allows a few seconds of host vs container clock skew)."""
    import clickhouse_connect

    ch = clickhouse_connect.get_client(host=os.environ.get("CLICKHOUSE_HOST", "127.0.0.1"),
                                       port=int(os.environ.get("CLICKHOUSE_HTTP_PORT", "8123")),
                                       username=os.environ.get("CLICKHOUSE_USER", "scada"),
                                       password=os.environ.get("CLICKHOUSE_PASSWORD", "scada"),
                                       database=os.environ.get("CLICKHOUSE_DB", "scada"))
    return ch.query("SELECT count() FROM telemetry FINAL WHERE startsWith(sensor_id, 'load-') "
                    "AND received_at >= {s:DateTime64(3)} - INTERVAL 5 SECOND "
                    "AND ts BETWEEN {s:DateTime64(3)} - INTERVAL 1 DAY - INTERVAL 5 SECOND AND {s:DateTime64(3)} - INTERVAL 12 HOUR",
                    parameters={"s": since}).result_rows[0][0]


async def blast(key: str, ids: list[str], duration: float, concurrency: int, batch: int) -> dict:
    latencies: list[float] = []
    accepted = rejected = errors = 0
    deadline = time.perf_counter() + duration
    # Unique, increasing measurement times per sensor, a day in the past. Event ids are derived from
    # (device, measurement time), so a reading that coincides to the millisecond with a live simulator reading of
    # the same sensor would be the *same* event and be dropped as a repeat: yesterday these sensors didn't exist.
    # Late events are stored and aggregated but don't move the live map or fire rules, so the test stays quiet.
    clock = {i: int(time.time() * 1000) - DAY_MS for i in ids}

    async def one(client: httpx.AsyncClient) -> None:
        nonlocal accepted, rejected, errors
        while time.perf_counter() < deadline:
            items = []
            for _ in range(batch):
                sid = random.choice(ids)
                clock[sid] += 1
                items.append({"device": sid, "temperature": round(random.uniform(16, 20), 2),
                              "humidity": round(random.uniform(45, 60), 1), "ts_ms": clock[sid]})
            t0 = time.perf_counter()
            try:
                r = await client.post("/ingest/climate", json=items)
                latencies.append(time.perf_counter() - t0)
                body = r.json()
                accepted += body.get("accepted", 0)
                rejected += len(body.get("rejected", []))
                if r.status_code >= 400 and not body.get("rejected"):
                    errors += 1
            except httpx.HTTPError:
                errors += 1

    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(base_url=CONNECTORS, headers={"X-API-Key": key}, timeout=30, limits=limits) as client:
        started = time.perf_counter()
        await asyncio.gather(*(one(client) for _ in range(concurrency)))
        elapsed = time.perf_counter() - started
    return {"accepted": accepted, "rejected": rejected, "errors": errors, "elapsed": elapsed, "latencies": latencies}


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sensors", type=int, default=200)
    ap.add_argument("--duration", type=float, default=30, help="seconds of sending")
    ap.add_argument("--concurrency", type=int, default=16, help="parallel HTTP clients")
    ap.add_argument("--batch", type=int, default=100, help="events per request")
    ap.add_argument("--no-report", action="store_true")
    args = ap.parse_args()

    api = await admin_client()
    ids = [f"load-{n:04d}" for n in range(1, args.sensors + 1)]
    key = None
    try:  # cleanup must cover everything from the first registered sensor on
        print(f"registering {len(ids)} synthetic sensors...")
        await register(api, ids)
        r = await api.post("/connectors/keys", json={"name": "loadtest", "sensor_types": ["climate"],
                                                     "rate_limit_per_min": 1_000_000})  # the API's maximum, ~16.7k/s
        r.raise_for_status()
        key = r.json()
        await asyncio.sleep(6)  # connectors reload the registry on a miss at most every 5 s
        before = await worker_stats(api)
        test_start = datetime.now(UTC)
        print(f"sending for {args.duration:.0f} s: {args.concurrency} clients x {args.batch} events/request...")
        res = await blast(key["key"], ids, args.duration, args.concurrency, args.batch)
        peak_lag, drained_at = 0, None
        t0 = time.perf_counter()
        target = before["stored"] + res["accepted"]
        while time.perf_counter() - t0 < 300:
            s = await worker_stats(api)
            peak_lag = max(peak_lag, s["lag"])
            if s["stored"] >= target and s["lag"] == 0:
                drained_at = time.perf_counter()
                break
            await asyncio.sleep(0.5)
        after = await worker_stats(api)
    finally:
        if key:
            await api.delete(f"/connectors/keys/{key['id']}")
        for i in ids:
            await api.patch(f"/sensors/{i}", json={"enabled": False})
        await api.aclose()

    stored = after["stored"] - before["stored"]
    in_clickhouse = clickhouse_rows(test_start)
    total_time = res["elapsed"] + ((drained_at - t0) if drained_at else 300)
    lat = [x * 1000 for x in res["latencies"]]
    result = {
        "when": datetime.now(UTC).astimezone().strftime("%Y-%m-%d %H:%M"),
        "params": f"{len(ids)} датчиков, {args.concurrency} клиентов × {args.batch} событий/запрос, {args.duration:.0f} с, "
                  f"worker × {after.get('replicas', 1)}",
        "ingest_eps": res["accepted"] / res["elapsed"],
        "accepted": res["accepted"], "rejected": res["rejected"], "errors": res["errors"],
        "p50": pct(lat, 50), "p95": pct(lat, 95), "p99": pct(lat, 99),
        "stored": stored, "e2e_eps": stored / total_time, "drain_s": (drained_at - t0) if drained_at else None,
        "peak_lag": peak_lag, "in_clickhouse": in_clickhouse, "lost": res["accepted"] - in_clickhouse,
    }
    print("\n".join(f"  {k}: {round(v, 1) if isinstance(v, float) else v}" for k, v in result.items()))
    if not args.no_report:
        write_report(result)


def write_report(r: dict) -> None:
    REPORT.parent.mkdir(exist_ok=True)
    header = (
        "# Нагрузочный тест\n\n"
        "Замер `tools/loadtest.py` (`python dev.py load`): синтетические датчики климата шлют пачки через коннекторы "
        "по HTTP, worker пишет в ClickHouse. Всё на одной машине разработчика в Docker Desktop, по одному экземпляру "
        "каждого сервиса, без тюнинга.\n\n"
        "| Когда | Параметры | Принято, соб/с | HTTP p50 / p95 / p99, мс | Записано в ClickHouse, соб/с (до разбора очереди) | Пик очереди | Потеряно |\n"
        "|---|---|---|---|---|---|---|\n"
    )
    text = REPORT.read_text(encoding="utf-8") if REPORT.exists() else header
    def n(v: float) -> str:  # thousands separated by a space, as written in Russian
        return f"{v:,.0f}".replace(",", " ")

    drain = f", очередь разобрана за {r['drain_s']:.1f} с" if r["drain_s"] is not None else ", очередь не разобрана за 5 мин"
    text += (f"| {r['when']} | {r['params']} | **{n(r['ingest_eps'])}** | {r['p50']:.0f} / {r['p95']:.0f} / {r['p99']:.0f} | "
             f"**{n(r['e2e_eps'])}**{drain} | {n(r['peak_lag'])} | {n(r['lost'])} из {n(r['accepted'])} |\n")
    REPORT.write_text(text, encoding="utf-8")
    print(f"report: {REPORT}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to a legacy code page
    asyncio.run(main())
