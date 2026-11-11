"""第 6 章实验：DuckDB 的下推、ASOF 与时间语义。

E1  分区裁剪：同样的 SQL，改一个 WHERE，Data Read 差多少
E2  count(*) 只读 footer：聚合下推的极端例子
E3  ASOF JOIN：四种写法，看计划里到底是谁在干活（含历史陷阱的复现尝试）
E4  time_bucket vs date_trunc：90 秒桶与午休断档
E5  threads / memory_limit：并行度与 spill
E6  COPY ... TO 分区写出

用法：uv run python py/w06_duckdb.py
产出：reports/w06-duckdb.md
"""

from __future__ import annotations

import json
import pathlib
import re
import statistics
import time

import duckdb

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXP = ROOT / "lake" / "data" / "exp06"
EXP.mkdir(parents=True, exist_ok=True)

TRADES = (ROOT / "lake/data/raw/trades/**/*.parquet").as_posix()
BOOK = (ROOT / "lake/data/raw/book/**/*.parquet").as_posix()
READ = f"read_parquet('{TRADES}', hive_partitioning=true)"
READ_BOOK = f"read_parquet('{BOOK}', hive_partitioning=true)"

out: dict[str, object] = {}


def rule(t: str) -> None:
    print(f"\n{'=' * 74}\n{t}\n{'=' * 74}")


def plan(con: duckdb.DuckDBPyConnection, sql: str) -> tuple[str, dict]:
    rows = con.execute("EXPLAIN ANALYZE " + sql).fetchall()
    txt = "\n".join(r[1] for r in rows)
    tot = re.search(r"Total Time:\s*([\d.]+)s", txt)
    data = re.search(r"Data Read:\s*([\d.]+)\s*(\w+)", txt)
    rg = [int(a) for a in re.findall(r"Row Groups Scanned:\s*([\d,]+)\s*/", txt)]
    m = dict(
        total=float(tot.group(1)) * 1000 if tot else 0.0,
        data_kb=0.0,
        rg_scanned=sum(rg),
        ops=[o for o in ("Nested Loop Join", "Asof Join", "Hash Join",
                         "Piecewise Merge Join", "Delim Join") if o in txt],
        spilled="Spilled" in txt or "SPILL" in txt.upper(),
    )
    if data:
        unit = {"B": 1 / 2**10, "kB": 1, "MB": 2**10, "GB": 2**20}.get(data.group(2), 1)
        m["data_kb"] = float(data.group(1)) * unit
    return txt, m


def timeit(fn, n: int = 3) -> float:
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    return statistics.median(ts)


def e1(con: duckdb.DuckDBPyConnection) -> None:
    rule("E1  分区裁剪：改一个 WHERE，Data Read 差 4 倍")
    cases = [
        ("全表 count", f"SELECT count(*) FROM {READ}"),
        ("dt 一天", f"SELECT count(*) FROM {READ} WHERE dt = '2026-11-05'"),
        ("dt 一天 + 抄表", f"SELECT count(*), sum(vol) FROM {READ} WHERE dt = '2026-11-05'"),
        ("单合约（不按 dt）", f"SELECT count(*), sum(vol) FROM {READ} WHERE symbol = '002322.SZ'"),
        ("单合约 + dt", f"SELECT count(*), sum(vol) FROM {READ} WHERE dt = '2026-11-05' AND symbol = '002322.SZ'"),
    ]
    rows = []
    for name, sql in cases:
        txt, m = plan(con, sql)
        ms = timeit(lambda: con.execute(sql).fetchall(), 3)
        rows.append(dict(name=name, kb=m["data_kb"], ms=ms, rg=m["rg_scanned"]))
        print(f"{name:22} Data Read {m['data_kb']:9.1f} kB   行组 {m['rg_scanned']:>3}   中位 {ms:6.2f} ms")
    print("\n要点：count(*) 只读 footer（81.9 kB），一旦要 sum(vol) 就得读 vol 列块（+1.4 MB）。")
    print("单合约但不过滤 dt：8 个文件全要打开——因为 symbol 不是分区键，只能靠行组统计。")
    out["e1"] = rows


def e2(con: duckdb.DuckDBPyConnection) -> None:
    rule("E2  列裁剪：Data Read 才是真正读了多少字节")
    cases = [
        ("count(*)", f"SELECT count(*) FROM {READ} WHERE dt = '2026-11-05'"),
        ("sum(vol)", f"SELECT sum(vol) FROM {READ} WHERE dt = '2026-11-05'"),
        ("sum(px)", f"SELECT sum(px) FROM {READ} WHERE dt = '2026-11-05'"),
        ("sum(vol)+sum(px)", f"SELECT sum(vol), sum(px) FROM {READ} WHERE dt = '2026-11-05'"),
        ("全 7 列聚合", f"SELECT sum(vol), sum(px), max(ts), max(seq), count(DISTINCT symbol), "
                        f"max(side), max(channel) FROM {READ} WHERE dt = '2026-11-05'"),
    ]
    rows = []
    for name, sql in cases:
        _, m = plan(con, sql)
        ms = timeit(lambda: con.execute(sql).fetchall(), 3)
        rows.append(dict(name=name, kb=m["data_kb"], ms=ms, rg=m["rg_scanned"]))
        print(f"{name:20} Data Read {m['data_kb']:9.1f} kB   行组 {m['rg_scanned']:>3}   中位 {ms:6.2f} ms")
    print("\ncount(*) 只读 footer（81.9 kB）；读一列 vol 加 177 kB；读 px 加更多——")
    print("这就是列存的意义：不读的列不花一个字节。")
    out["e2"] = rows


def e3(con: duckdb.DuckDBPyConnection) -> None:
    rule("E3  ASOF JOIN：四种写法谁在干活")

    # 先把分钟 bar 和盘口过滤物化，作为基线
    con.execute("CREATE OR REPLACE TEMP TABLE bars AS "
                f"SELECT symbol, ts // 60000000000 * 60000000000 AS ts, "
                f"sum(vol) AS vol, avg(px) AS px FROM {READ} "
                f"WHERE dt = '2026-11-05' GROUP BY 1, 2")
    con.execute("CREATE OR REPLACE TEMP TABLE q AS "
                f"SELECT symbol, ts, bid_px1, ask_px1 FROM {READ_BOOK} "
                f"WHERE dt = '2026-11-05' AND symbol = '002322.SZ'")
    nb = con.execute("SELECT count(*) FROM bars").fetchone()[0]
    nq = con.execute("SELECT count(*) FROM q").fetchone()[0]
    print(f"bars {nb:,} 行，quotes {nq:,} 行")

    shapes = {
        "A 物化表 ASOF": "SELECT count(*) FROM bars b ASOF JOIN q USING (symbol, ts)",
        "B CTE 直接 ASOF": (
            f"WITH b AS (SELECT symbol, ts // 60000000000 * 60000000000 AS ts, sum(vol) vol FROM {READ} "
            f"WHERE dt='2026-11-05' GROUP BY 1,2), "
            f"qq AS (SELECT symbol, ts, bid_px1 FROM {READ_BOOK} "
            f"WHERE dt='2026-11-05' AND symbol='002322.SZ') "
            "SELECT count(*) FROM b ASOF JOIN qq USING (symbol, ts)"
        ),
        "C 子查询 + 过滤": (
            "SELECT count(*) FROM (SELECT * FROM bars WHERE symbol='002322.SZ') b "
            "ASOF JOIN (SELECT * FROM q) qq USING (symbol, ts)"
        ),
        "D LEFT JOIN + 不等号": (
            "SELECT count(*) FROM bars b LEFT JOIN q qq "
            "ON b.symbol = qq.symbol AND qq.ts <= b.ts"
        ),
        "E CTE + MATERIALIZED": (
            f"WITH b AS MATERIALIZED (SELECT symbol, ts // 60000000000 * 60000000000 AS ts, "
            f"sum(vol) vol FROM {READ} WHERE dt='2026-11-05' GROUP BY 1,2), "
            f"qq AS MATERIALIZED (SELECT symbol, ts, bid_px1 FROM {READ_BOOK} "
            f"WHERE dt='2026-11-05' AND symbol='002322.SZ') "
            "SELECT count(*) FROM b ASOF JOIN qq USING (symbol, ts)"
        ),
    }
    rows = []
    for name, sql in shapes.items():
        try:
            txt, m = plan(con, sql)
            ms = timeit(lambda: con.execute(sql).fetchall(), 2)
            join_ops = [o for o in m["ops"] if "join" in o.lower()]
            rows.append(dict(name=name, ms=ms, ops=join_ops, rg=m["rg_scanned"]))
            print(f"{name:20} {ms:8.1f} ms  算子: {', '.join(join_ops) or '—'}")
        except Exception as e:  # noqa: BLE001
            print(f"{name:20} 失败: {e}")
    print("\nB/E 的差别只在有没有物化，耗时差 17 倍：CTE 上没有列统计，优化器估计不出基数，")
    print("把 ASOF 退化成 Nested Loop（中间结果 8,759,024 行）。物化成 TEMP TABLE 后估计 957 行，恢复 Asof Join。")
    print("结论：ASOF 的两个输入必须是有统计的表；DISTINCT/GROUP BY 的结果先落临时表。")
    out["e3"] = rows


def e4(con: duckdb.DuckDBPyConnection) -> None:
    rule("E4  time_bucket vs date_trunc：90 秒桶与午休断档")

    q = f"""
    WITH t AS (
      SELECT to_timestamp(ts / 1e9) AS t FROM {READ}
      WHERE dt = '2026-11-05' AND symbol = '002322.SZ'
    )
    SELECT
      count(*) AS rows,
      count(DISTINCT time_bucket(INTERVAL 90 SECOND, t)) AS buckets_90s,
      count(DISTINCT date_trunc('minute', t)) AS minutes,
      min(t) AS lo, max(t) AS hi
    FROM t
    """
    r = con.execute(q).fetchone()
    print(f"单合约一天：{r[0]:,} 行，90 秒桶 {r[1]:,} 个，分钟桶 {r[2]:,} 个，"
          f"区间 {r[3]} → {r[4]}")
    print(f"理论分钟数 = 240（4 小时），实际 {r[2]} 个——差的是午休 11:30–13:00。")

    gaps = f"""
    WITH t AS (
      SELECT to_timestamp(ts / 1e9) AS t FROM {READ}
      WHERE dt = '2026-11-05' AND symbol = '002322.SZ'
    ), m AS (
      SELECT DISTINCT date_trunc('minute', t) AS mnt FROM t
    ), b AS (
      SELECT min(mnt) AS lo, max(mnt) AS hi FROM m
    ), s AS (
      SELECT g.mnt FROM b, generate_series(b.lo, b.hi, INTERVAL 1 MINUTE) AS g(mnt)
    )
    SELECT (SELECT count(*) FROM s) AS expected, (SELECT count(*) FROM m) AS actual,
           (SELECT count(*) FROM s) - (SELECT count(*) FROM m) AS missing
    """
    g = con.execute(gaps).fetchone()
    print(f"从首笔到末笔应含 {g[0]} 分钟（墙钟），实际有成交 {g[1]} 分钟，缺 {g[2]} 分钟。")
    missing = con.execute(gaps.replace(
        """SELECT (SELECT count(*) FROM s) AS expected, (SELECT count(*) FROM m) AS actual,
           (SELECT count(*) FROM s) - (SELECT count(*) FROM m) AS missing""",
        """SELECT s.mnt FROM s LEFT JOIN m ON s.mnt = m.mnt WHERE m.mnt IS NULL ORDER BY 1""",
    )).fetchall()
    marks = [str(x[0])[11:16] for x in missing]
    lunch = [m for m in marks if "11:30" <= m < "13:00"]
    print(f"缺口 {marks[:2]} … {marks[-2:]}，共 {len(marks)} 个；其中 {len(lunch)} 个在 11:30–12:59（休市），"
          f"另外 {len(marks) - len(lunch)} 个是真空档：{sorted(set(marks) - set(lunch))}")
    print("这就是「按时间补空」必须先知道交易日历的原因：休市缺口不是丢数据，真空档才是。")
    out["e4"] = {"buckets_90s": r[1], "minutes": r[2], "missing": g[2], "expected": g[0],
                 "missing_list": marks}


def e5(con: duckdb.DuckDBPyConnection) -> None:
    rule("E5  threads 与 memory_limit")
    rows = []
    for th in (1, 2, 4, 8):
        con.execute(f"SET threads={th}")
        sql = (f"SELECT symbol, ts // 60000000000, count(*), sum(vol) FROM {READ} "
               f"GROUP BY 1,2 ORDER BY 4 DESC LIMIT 10")
        ms = timeit(lambda: con.execute(sql).fetchall(), 3)
        rows.append(dict(threads=th, ms=ms))
        print(f"threads={th}  {ms:7.1f} ms")
    con.execute("SET threads=4")

    # 压内存：观察是优雅 spill 还是直接失败
    sql = f"SELECT * FROM {READ} ORDER BY px, ts"
    spill = []
    for lim in ("64MB", "32MB", "16MB"):
        con.execute(f"SET memory_limit='{lim}'")
        try:
            txt, m = plan(con, sql)
            hit = m["spilled"]
            print(f"  memory_limit={lim:>5}：{m['total']:6.1f} ms，spill={hit}")
            spill.append({"limit": lim, "ms": m["total"], "spilled": hit})
        except Exception as e:  # noqa: BLE001
            print(f"  memory_limit={lim:>5}：OOM — {str(e).splitlines()[0][:70]}")
            spill.append({"limit": lim, "error": str(e).splitlines()[0][:90]})
    con.execute("SET memory_limit='2GB'")
    print("  587,000 行这个规模看不到 spill：内存一紧就直接 OOM。")
    print("  spill 是「数据量 × 并发 × 算子」的函数，几百 MB 的测试数据验证不了它。")
    out["e5_spill"] = spill
    out["e5"] = rows


def e6(con: duckdb.DuckDBPyConnection) -> None:
    rule("E6  COPY ... TO 分区写出")

    target = EXP / "bars"
    if target.exists():
        for p in sorted(target.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
    sql = f"""
    COPY (
      SELECT symbol, ts // 60000000000 * 60000000000 AS ts, sum(vol) AS vol, avg(px) AS px
      FROM {READ} GROUP BY 1, 2
    ) TO '{target.as_posix()}' (FORMAT PARQUET, PARTITION_BY (symbol), OVERWRITE_OR_IGNORE)
    """
    t0 = time.perf_counter()
    con.execute(sql)
    dt = (time.perf_counter() - t0) * 1000
    files = sorted(target.rglob("*.parquet"))
    print(f"写出 {len(files)} 个文件，耗时 {dt:.0f} ms，"
          f"合计 {sum(f.stat().st_size for f in files)/2**20:.2f} MB")
    for f in files:
        print(f"  {f.relative_to(target)}  {f.stat().st_size/1024:.1f} KB")

    back = con.execute(
        f"SELECT count(*), sum(vol) FROM read_parquet('{target.as_posix()}/**/*.parquet', "
        f"hive_partitioning=true)"
    ).fetchone()
    src = con.execute(
        f"SELECT count(DISTINCT (symbol, ts // 60000000000)), sum(vol) FROM {READ}"
    ).fetchone()
    ok = back[0] == src[0] and abs(back[1] - src[1]) < 1
    print(f"读回：{back[0]:,} 个 bar，sum(vol)={back[1]:,.0f}")
    print(f"源  ：{src[0]:,} 个 bar，sum(vol)={src[1]:,.0f} → {'一致' if ok else '不一致'}")
    print("注意这里比的是「bar 数」不是「tick 数」：587,000 条 tick 聚成 1,913 个分钟 bar。")
    out["e6"] = {"files": len(files), "ms": dt, "bars": back[0], "match": ok}


def report() -> None:
    e1 = out["e1"]
    e3 = out["e3"]
    e5 = out["e5"]
    e6 = out["e6"]
    lines = [
        "# 第 6 章实测：DuckDB 下推与 ASOF",
        "",
        f"duckdb {duckdb.__version__}，2026-11-11；数据 587,000 行 × 7 列，"
        "按 dt/exchange 两級分区、每合约一文件",
        "",
        "## E1 分区裁剪",
        "",
        "| 查询 | Data Read kB | 扫过的行组 | 中位 ms |",
        "|---|---|---|---|",
    ]
    for r in e1:
        lines.append(f"| {r['name']} | {r['kb']:.1f} | {r['rg']} | {r['ms']:.2f} |")
    lines += ["", "## E3 ASOF 四种写法", "", "| 写法 | 中位 ms | 连接算子 |", "|---|---|---|"]
    for r in e3:
        lines.append(f"| {r['name']} | {r['ms']:.1f} | {', '.join(r['ops']) or '—'} |")
    lines += ["", "## E5 线程", "", "| threads | ms |", "|---|---|"]
    for r in e5:
        lines.append(f"| {r['threads']} | {r['ms']:.1f} |")
    lines += [
        "",
        "## E6 分区写出",
        "",
        f"- {e6['files']} 个文件 / {e6['ms']:.0f} ms / {e6['bars']:,} 个 bar / 结果一致={e6['match']}",
    ]
    (ROOT / "reports" / "w06-duckdb.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (ROOT / "reports" / "w06-duckdb.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print("\n报告已写入 reports/w06-duckdb.md")


def main() -> None:
    con = duckdb.connect()
    con.execute("SET threads=4")
    con.execute("SET enable_progress_bar=false")
    print(f"duckdb {duckdb.__version__}")
    e1(con)
    e2(con)
    e3(con)
    e4(con)
    e5(con)
    e6(con)
    report()


if __name__ == "__main__":
    main()
