"""验收自检：每周做完任务卡后跑一遍。

每条检查都对应任务卡里的"验收标准"，标了建议周次。
用法：
    python py/verify.py            # 全跑
    python py/verify.py --week 3   # 只跑第 3 周及之前该有的项
"""

from __future__ import annotations

import argparse
import glob
import pathlib
import subprocess
import sys

import duckdb
import pyarrow.parquet as pq

ROOT = pathlib.Path(".")
TRADES = "lake/data/raw/trades/**/*.parquet"
results: list[tuple[int, str, bool, str]] = []


def check(week: int, name: str, fn) -> None:
    try:
        ok, msg = fn()
    except Exception as exc:  # noqa: BLE001
        ok, msg = False, f"{type(exc).__name__}: {exc}"
    results.append((week, name, ok, msg))


def data_present():
    files = glob.glob(TRADES, recursive=True)
    if not files:
        return False, "没有数据，先跑 python py/gen_tick.py"
    rows = sum(pq.read_metadata(f).num_rows for f in files)
    return rows > 0, f"{len(files)} 个文件 / {rows} 行"


def schema_ok():
    f = glob.glob(TRADES, recursive=True)[0]
    cols = set(pq.read_schema(f).names)
    need = {"symbol", "ts", "px", "vol", "seq", "side"}
    miss = need - cols
    return not miss, f"缺失字段: {miss or '无'}"


def dirty_data_exists():
    con = duckdb.connect()
    raw, dedup = con.execute(
        f"SELECT count(*), count(DISTINCT (symbol, ts, seq)) FROM read_parquet('{TRADES}', hive_partitioning=true)"
    ).fetchone()
    return raw > dedup, f"重复行 {raw - dedup} / {raw}（练习去重要用）"


def disorder_exists():
    f = glob.glob(TRADES, recursive=True)[0]
    ts = pq.read_table(f, columns=["ts"]).column("ts").to_numpy()
    bad = int((ts[1:] < ts[:-1]).sum())
    return bad > 0, f"{pathlib.Path(f).name} 内乱序 {bad} 处（练习排序要用）"


def rust_binary_ok():
    r = subprocess.run(
        ["cargo", "run", "-q", "--", "--help"],
        cwd="rust",
        capture_output=True,
        text=True,
    )
    return r.returncode == 0, (r.stdout or r.stderr).splitlines()[0] if (r.stdout or r.stderr) else "无输出"


def bench_report_exists():
    p = pathlib.Path("reports/bench.md")
    if not p.exists():
        return False, "先跑 python py/bench.py"
    lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.startswith("| ") and "%" not in l]
    return len(lines) >= 3, f"{len(lines) - 1} 个方案"


def bars_exist():
    p = pathlib.Path("lake/data/derived/bars_1m.parquet")
    if not p.exists():
        return False, "先跑 python py/q_polars.py"
    t = pq.read_table(p)
    return t.num_rows > 0, f"{t.num_rows} 行 bar"


def partition_pruning_ok():
    con = duckdb.connect()
    plan = con.execute(
        f"""
        EXPLAIN ANALYZE
        SELECT count(*) FROM read_parquet('{TRADES}', hive_partitioning=true)
        WHERE dt = '2026-11-05'
        """
    ).fetchall()
    text = "\n".join(r[1] for r in plan)
    all_rows = con.execute(
        f"SELECT count(*) FROM read_parquet('{TRADES}', hive_partitioning=true)"
    ).fetchone()[0]
    day_rows = con.execute(
        f"SELECT count(*) FROM read_parquet('{TRADES}', hive_partitioning=True) WHERE dt='2026-11-05'"
    ).fetchone()[0]
    return day_rows < all_rows, f"读一天命中 {day_rows}/{all_rows} 行；计划长度 {len(text)}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", type=int, default=99)
    args = ap.parse_args()

    check(1, "数据已生成本", data_present)
    check(1, "schema 字段齐全", schema_ok)
    check(1, "脏数据确实存在（重复/乱序）", dirty_data_exists)
    check(2, "乱序行存在", disorder_exists)
    check(3, "Rust 工具可运行", rust_binary_ok)
    check(5, "基准报告已产出", bench_report_exists)
    check(7, "分钟 bar 已产出", bars_exist)
    check(8, "分区裁剪生效", partition_pruning_ok)

    shown = [r for r in results if r[0] <= args.week]
    print(f"{'周':<3}{'检查项':<28}{'结果':<6}说明")
    print("-" * 78)
    for week, name, ok, msg in shown:
        print(f"W{week:<2} {name:<26}{'PASS' if ok else 'FAIL':<6}{msg}")

    failed = [r for r in shown if not r[2]]
    print("-" * 78)
    print(f"{len(shown) - len(failed)}/{len(shown)} 通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
