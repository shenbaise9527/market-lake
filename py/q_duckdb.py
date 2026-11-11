"""DuckDB 查询层：直接对着 Parquet 目录跑 SQL，不建表。

五个必练查询：
  1. 分区裁剪是否真的生效（EXPLAIN ANALYZE 的扫描量）
  2. tick → 分钟 bar
  3. 去重（重复回报 / 重传）
  4. ASOF JOIN（成交对齐盘口）
  5. 脏数据检测

用法：python py/q_duckdb.py
"""

from __future__ import annotations

import duckdb

TRADES = "lake/data/raw/trades/**/*.parquet"
BOOK = "lake/data/raw/book/**/*.parquet"
DAY = "2026-11-05"


def rule(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def main() -> None:
    con = duckdb.connect()
    con.execute("SET threads=4")
    con.execute("SET enable_progress_bar=false")

    rule("1. 文件清单与分区裁剪")
    print(
        con.execute(
            f"""
            SELECT dt, count(DISTINCT filename) AS files, count(*) AS rows
            FROM read_parquet('{TRADES}', hive_partitioning=true, filename=true)
            GROUP BY dt ORDER BY dt
            """
        ).df().to_string(index=False)
    )
    # 只读某一天：DuckDB 只应打开该分区下的文件。看 "Data Read" 那一行。
    plan = con.execute(
        f"""
        EXPLAIN ANALYZE
        SELECT count(*) FROM read_parquet('{TRADES}', hive_partitioning=true)
        WHERE dt = '{DAY}'
        """
    ).fetchall()
    text = "\n".join(r[1] for r in plan)
    for line in text.splitlines():
        if any(k in line for k in ("Total Time", "Data Read", "Rows", "File Filters")):
            print(line.strip()[:160])

    rule("2. tick → 分钟 bar（含成交量加权均价）")
    print(
        con.execute(
            f"""
            SELECT symbol,
                   ts // 60000000000 * 60             AS minute_start_s,
                   count(*)                           AS trades,
                   sum(vol)                           AS volume,
                   round(sum(px * vol) / sum(vol), 4) AS vwap,
                   min(px) AS lo, max(px) AS hi
            FROM read_parquet('{TRADES}', hive_partitioning=true)
            WHERE dt = '{DAY}'
            GROUP BY 1, 2
            ORDER BY 1, 2
            LIMIT 10
            """
        ).df().to_string(index=False)
    )

    rule("3. 去重：原始行数 vs 按 (symbol, ts, seq) 去重后行数")
    print(
        con.execute(
            f"""
            SELECT count(*) AS raw,
                   count(DISTINCT (symbol, ts, seq)) AS dedup,
                   count(*) - count(DISTINCT (symbol, ts, seq)) AS dup_rows
            FROM read_parquet('{TRADES}', hive_partitioning=true)
            WHERE dt = '{DAY}'
            """
        ).df().to_string(index=False)
    )

    rule("4. ASOF JOIN：每笔成交对齐到最近的盘口快照")
    # 注意：这里先把两侧物化成临时表。
    # 直接对 read_parquet + WHERE symbol=... 做 ASOF，DuckDB 的基数估计会失真，
    # 退化成嵌套循环（几万 × 几万，跑几分钟）；物化后 0.02 秒。
    # 这是真实存在的优化器坑，S3 任务卡里会让你复现并记录。
    sym = con.execute(
        f"SELECT min(symbol) FROM read_parquet('{TRADES}', hive_partitioning=true)"
    ).fetchone()[0]
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE t AS
        SELECT symbol, ts, px, vol FROM read_parquet('{TRADES}', hive_partitioning=true)
        WHERE dt = '{DAY}' AND symbol = '{sym}'
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE b AS
        SELECT symbol, ts, bid_px1, ask_px1 FROM read_parquet('{BOOK}', hive_partitioning=true)
        WHERE dt = '{DAY}' AND symbol = '{sym}'
        """
    )
    print(
        con.execute(
            """
            SELECT t.ts, t.px, b.bid_px1, b.ask_px1,
                   round(b.ask_px1 - b.bid_px1, 4) AS spread
            FROM t ASOF LEFT JOIN b
              ON t.symbol = b.symbol AND t.ts >= b.ts
            ORDER BY t.ts LIMIT 8
            """
        ).df().to_string(index=False)
    )

    rule("5. 脏数据检测：价格跳变 5 倍以上的记录")
    print(
        con.execute(
            f"""
            WITH s AS (
                SELECT symbol, ts, px,
                       avg(px) OVER (PARTITION BY symbol) AS mean_px
                FROM read_parquet('{TRADES}', hive_partitioning=true)
                WHERE dt = '{DAY}'
            )
            SELECT symbol, count(*) AS outliers
            FROM s WHERE px > mean_px * 5 GROUP BY 1 ORDER BY 2 DESC
            """
        ).df().to_string(index=False)
    )


if __name__ == "__main__":
    main()
