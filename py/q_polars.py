"""Polars 查询层：惰性求值 + 下推，和 DuckDB 做同一组事。

对照点：
  - scan_parquet 是惰性的，explain() 能看到下推后的计划
  - group_by_dynamic 直接做 bar 聚合，不用手算时间桶
  - join_asof 对应 DuckDB 的 ASOF JOIN
  - sink_parquet 可以流式写出，内存放不下时靠它

用法：python py/q_polars.py
"""

from __future__ import annotations

import time

import polars as pl

TRADES = "lake/data/raw/trades/**/*.parquet"
BOOK = "lake/data/raw/book/**/*.parquet"
DAY = "2026-11-05"
DAY_D = pl.date(2026, 11, 5)  # polars 会把 hive 分区键识别为 Date 类型


def rule(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def main() -> None:
    rule("1. 惰性计划：下推到了哪一步")
    lf = (
        pl.scan_parquet(TRADES, hive_partitioning=True)
        .filter(pl.col("dt") == DAY_D)
        .select(["symbol", "ts", "px", "vol"])
    )
    print(lf.explain())

    rule("2. tick → 分钟 bar")
    t0 = time.perf_counter()
    bars = (
        pl.scan_parquet(TRADES, hive_partitioning=True)
        .filter(pl.col("dt") == DAY_D)
        .with_columns(pl.from_epoch(pl.col("ts"), time_unit="ns").alias("t"))
        .sort(["symbol", "t"])  # group_by_dynamic 要求按索引列有序，而原始数据是乱序的
        .group_by_dynamic("t", every="1m", group_by="symbol")
        .agg(
            pl.len().alias("trades"),
            pl.col("vol").sum().alias("volume"),
            ((pl.col("px") * pl.col("vol")).sum() / pl.col("vol").sum()).alias("vwap"),
            pl.col("px").min().alias("lo"),
            pl.col("px").max().alias("hi"),
        )
        .sort(["symbol", "t"])
        .collect()
    )
    print(bars.head(8))
    print(f"耗时 {time.perf_counter() - t0:.3f}s，行数 {bars.height}")

    rule("3. ASOF JOIN：成交对齐盘口（单合约）")
    sym = bars["symbol"][0]
    t0 = time.perf_counter()
    trades = (
        pl.scan_parquet(TRADES, hive_partitioning=True)
        .filter((pl.col("dt") == DAY_D) & (pl.col("symbol") == sym))
        .select(["symbol", "ts", "px"])
        .sort("ts")
        .collect()
    )
    book = (
        pl.scan_parquet(BOOK, hive_partitioning=True)
        .filter((pl.col("dt") == DAY_D) & (pl.col("symbol") == sym))
        .select(["symbol", "ts", "bid_px1", "ask_px1"])
        .sort("ts")
        .collect()
    )
    joined = trades.join_asof(book, on="ts", by="symbol", strategy="backward")
    print(joined.head(6))
    print(f"耗时 {time.perf_counter() - t0:.3f}s")

    rule("4. 流式写出（内存放不下时）")
    out = "lake/data/derived/bars_1m.parquet"
    (
        pl.scan_parquet(TRADES, hive_partitioning=True)
        .with_columns(pl.from_epoch(pl.col("ts"), time_unit="ns").alias("t"))
        .sort(["symbol", "t"])
        .group_by_dynamic("t", every="1m", group_by=["symbol", "dt"])
        .agg(pl.len().alias("trades"), pl.col("vol").sum().alias("volume"))
        .sink_parquet(out, compression="zstd")
    )
    print(f"已写出 {out}")


if __name__ == "__main__":
    main()
