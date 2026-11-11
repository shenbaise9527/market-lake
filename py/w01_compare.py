"""W1 对照实验参照实现 —— 建议先自己写一遍，卡住了再看这里。

测三个查询目标（它们的最优配置是相反的，W9 会反复回到这张表）：
  a) 全扫聚合 : sum(vol), avg(px)                 -> 比 IO 吞吐
  b) 点查     : WHERE symbol=? AND ts BETWEEN ?   -> 比元数据剪枝
  c) 窄列扫描 : avg(px)                           -> 比列裁剪

用法: uv run python py/w01_compare.py
"""

from __future__ import annotations

import pathlib
import time

import duckdb

ROOT = pathlib.Path(__file__).resolve().parent.parent
DAY = "2026-11-05"
TRADES_DIR = ROOT / "lake" / "data" / "raw" / "trades"
PARQUET_SRC = (
    f"(SELECT * FROM read_parquet('{TRADES_DIR / '**' / '*.parquet'}', hive_partitioning = true) "
    f"WHERE dt = '{DAY}')"
)
CSV_DIR = ROOT / "lake" / "data" / "exp" / "csv"
CSV_PATH = CSV_DIR / f"trades-{DAY}.csv"
CSV_SRC = f"read_csv_auto('{CSV_PATH}')"


def export_csv(con: duckdb.DuckDBPyConnection) -> None:
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    if CSV_PATH.exists():
        print(f"CSV 已存在，跳过导出（想重测就删掉它）: {CSV_PATH}")
        return
    t0 = time.perf_counter()
    con.execute(f"COPY {PARQUET_SRC} TO '{CSV_PATH}' (HEADER, DELIMITER ',')")
    print(f"导出 CSV 用时 {time.perf_counter() - t0:.2f}s")


def best_of(con: duckdb.DuckDBPyConnection, sql: str, n: int = 3) -> tuple[float, int]:
    """跑 n 次取最小值（避开冷启动和调度抖动），返回 (毫秒, EXPLAIN 里的 Data Read 行)。"""
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        con.execute(sql).fetchall()
        times.append((time.perf_counter() - t0) * 1000)
    plan = con.execute("EXPLAIN ANALYZE " + sql).fetchall()[0][1]
    return min(times), plan


def data_read(plan: str) -> str:
    for line in plan.splitlines():
        if "Data Read" in line:
            return " ".join(line.split())
    return "—"


def main() -> None:
    con = duckdb.connect()
    con.execute("SET threads = 4")
    con.execute("SET enable_progress_bar = false")

    export_csv(con)

    rows, lo, hi = con.execute(f"SELECT count(*), min(ts), max(ts) FROM {PARQUET_SRC}").fetchone()
    win_lo, win_hi = lo + (hi - lo) // 2, lo + (hi - lo) // 2 + 600 * 10**9  # 中间 10 分钟

    pq_size = sum(p.stat().st_size for p in (TRADES_DIR / f"dt={DAY}").rglob("*.parquet"))
    csv_size = CSV_PATH.stat().st_size

    print(
        f"\n{DAY} 单日成交 {rows:,} 行 | Parquet {pq_size / 1e6:.2f} MB vs "
        f"CSV {csv_size / 1e6:.2f} MB | 体积比 {csv_size / pq_size:.2f}x\n"
    )

    queries = {
        "a 全扫聚合": "SELECT sum(vol), avg(px) FROM {src}",
        "b 点查": f"SELECT count(*), sum(vol) FROM {{src}} WHERE symbol = '600267.SH' AND ts BETWEEN {win_lo} AND {win_hi}",
        "c 窄列扫描": "SELECT avg(px) FROM {src}",
    }

    print(f"{'查询目标':<12}{'Parquet':>11}{'CSV':>11}{'倍数':>8}   {'Data Read(CSV 侧)':<12}")
    print("-" * 76)
    for name, tpl in queries.items():
        tp, plan_p = best_of(con, tpl.format(src=PARQUET_SRC))
        tc, plan_c = best_of(con, tpl.format(src=CSV_SRC))
        print(f"{name:<12}{tp:>9.1f}ms{tc:>9.1f}ms{tc / tp:>7.1f}x   {data_read(plan_c)}")
        # 想看 Parquet 侧到底读了多少字节，把下面这行取消注释
        print("    Parquet:", data_read(plan_p))

    print("\n提示：c 查询最能说明问题——CSV 依然要读完整行，Parquet 只读 px 一列。")
    print("自己动手：把上面 print 注释那行打开，对比两边的 Data Read 数字。")


if __name__ == "__main__":
    main()
