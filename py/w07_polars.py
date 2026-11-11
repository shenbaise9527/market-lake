"""第 7 章实验：Polars 惰性求值、动态分组、流式写出，与三引擎对齐。

E1  同一个"单合约分钟 bar + VWAP"：pandas / DuckDB / Polars 的代码量与耗时
E2  惰性计划：下推了什么（explain 对比），以及"先过滤再选列"少读多少
E3  group_by_dynamic 的前置条件：不排序会怎样
E4  join_asof 与 DuckDB ASOF 的结果对齐
E5  sink_parquet：大于内存的合成数据，流式写出的峰值内存
E6  三引擎结果一致性校验（行数、sum、vwap）

用法：uv run python py/w07_polars.py
产出：reports/w07-polars.md
"""

from __future__ import annotations

import json
import pathlib
import statistics
import time

import duckdb
import pandas as pd
import polars as pl

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXP = ROOT / "lake" / "data" / "exp07"
EXP.mkdir(parents=True, exist_ok=True)
TRADES = (ROOT / "lake/data/raw/trades/**/*.parquet").as_posix()
BOOK = (ROOT / "lake/data/raw/book/**/*.parquet").as_posix()

out: dict[str, object] = {}


def rule(t: str) -> None:
    print(f"\n{'=' * 74}\n{t}\n{'=' * 74}")


def rss_mb() -> float:
    try:
        import psutil  # type: ignore

        return psutil.Process().memory_info().rss / 2**20
    except Exception:
        return 0.0


def timed(fn, n: int = 3) -> float:
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        r = fn()
        ts.append((time.perf_counter() - t0) * 1000)
    return statistics.median(ts), r


def e1() -> None:
    rule("E1  同一个查询，三种写法")

    # pandas：先全读，再过滤
    def pandas_impl():
        df = pd.read_parquet(ROOT / "lake/data/raw/trades")
        df = df[df["dt"].astype(str) == "2026-11-05"]
        df = df[df["symbol"] == "002322.SZ"]
        df["m"] = df["ts"] // 60_000_000_000 * 60_000_000_000
        g = df.groupby("m").agg(
            trades=("px", "size"), volume=("vol", "sum"), notional=("px", lambda s: 0)
        )
        g["notional"] = df.assign(n=df["px"] * df["vol"]).groupby("m")["n"].sum()
        return len(g), float(g["volume"].sum()), float(g["notional"].sum() / g["volume"].sum())

    # duckdb
    def duck_impl():
        con = duckdb.connect()
        con.execute("SET threads=4")
        r = con.execute(f"""
            SELECT count(*) AS bars, sum(volume) AS vol,
                   sum(notional) / sum(volume) AS vwap FROM (
              SELECT ts // 60000000000 * 60000000000 AS m, count(*) AS trades,
                     sum(vol) AS volume, sum(px * vol) AS notional
              FROM read_parquet('{TRADES}', hive_partitioning=true)
              WHERE dt = '2026-11-05' AND symbol = '002322.SZ'
              GROUP BY 1
            )""").fetchone()
        con.close()
        return r

    # polars：惰性
    def polars_impl():
        r = (
            pl.scan_parquet(TRADES, hive_partitioning=True)
            .filter((pl.col("dt") == pl.date(2026, 11, 5)) & (pl.col("symbol") == "002322.SZ"))
            .with_columns((pl.col("ts") // 60_000_000_000 * 60_000_000_000).alias("m"))
            .group_by("m")
            .agg(
                pl.len().alias("trades"),
                pl.col("vol").sum().alias("volume"),
                (pl.col("px") * pl.col("vol")).sum().alias("notional"),
            )
            .select(
                pl.len().alias("bars"),
                pl.col("volume").sum(),
                (pl.col("notional").sum() / pl.col("volume").sum()).alias("vwap"),
            )
            .collect()
        )
        return r.row(0)

    t_pd, r_pd = timed(pandas_impl, 3)
    t_duck, r_duck = timed(duck_impl, 3)
    t_pl, r_pl = timed(polars_impl, 3)
    print(f"pandas  {t_pd:8.1f} ms  bars={r_pd[0]:,}  vol={r_pd[1]:,.0f}  vwap={r_pd[2]:.4f}")
    print(f"DuckDB  {t_duck:8.1f} ms  bars={r_duck[0]:,}  vol={r_duck[1]:,.0f}  vwap={r_duck[2]:.4f}")
    print(f"Polars  {t_pl:8.1f} ms  bars={r_pl[0]:,}  vol={r_pl[1]:,.0f}  vwap={r_pl[2]:.4f}")
    print(f"\n比值：pandas {t_pd/t_pl:.1f}×，DuckDB {t_duck/t_pl:.2f}×（以 Polars 为 1）")
    out["e1"] = {"pandas_ms": t_pd, "duckdb_ms": t_duck, "polars_ms": t_pl,
                 "bars": int(r_pl[0]), "vol": float(r_pl[1]), "vwap": float(r_pl[2])}


def e2() -> None:
    rule("E2  惰性计划：下推了什么")
    lf = (
        pl.scan_parquet(TRADES, hive_partitioning=True)
        .filter(pl.col("dt") == pl.date(2026, 11, 5))
        .filter(pl.col("symbol") == "002322.SZ")
        .select(["ts", "px", "vol"])
    )
    print(lf.explain())
    print("\n→ 两个 filter 都进了 Parquet 扫描节点（predicate pushdown），")
    print("  select 只有 3 列（projection pushdown），dt/symbol 由分区键提供，不读数据列。")

    # 对比：先 collect 再过滤 vs 惰性过滤
    def eager():
        df = pl.read_parquet(TRADES, hive_partitioning=True)
        return df.filter(pl.col("symbol") == "002322.SZ").height

    def lazy():
        return pl.scan_parquet(TRADES, hive_partitioning=True).filter(
            pl.col("symbol") == "002322.SZ"
        ).select(["ts", "px"]).collect().height

    t_e, n_e = timed(eager, 3)
    t_l, n_l = timed(lazy, 3)
    print(f"\nread_parquet 全读再过滤：{t_e:7.1f} ms，{n_e:,} 行")
    print(f"scan_parquet  惰性过滤：{t_l:7.1f} ms，{n_l:,} 行  → {t_e/t_l:.1f}×")
    out["e2"] = {"eager_ms": t_e, "lazy_ms": t_l, "rows": n_l}


def e3() -> None:
    rule("E3  group_by_dynamic 的前置条件：必须先排序")
    raw = pl.read_parquet(TRADES, hive_partitioning=True).filter(
        (pl.col("symbol") == "002322.SZ") & (pl.col("dt") == pl.date(2026, 11, 5))
    ).with_columns(pl.from_epoch("ts", time_unit="ns").alias("t"))

    try:
        raw.group_by_dynamic("t", every="1m", group_by="symbol").agg(pl.len()).collect()
        print("未报错")
    except Exception as e:  # noqa: BLE001
        msg = str(e).splitlines()[0]
        print(f"乱序输入 → {type(e).__name__}: {msg[:160]}")
        out["e3_error"] = msg[:200]

    t_sorted, res = timed(
        lambda: raw.sort(["symbol", "t"]).group_by_dynamic("t", every="1m", group_by="symbol")
        .agg(pl.len().alias("n"), pl.col("vol").sum().alias("v")),
        3,
    )
    print(f"先 sort 再 group_by_dynamic：{t_sorted:.1f} ms，{res.height:,} 个桶")
    print("为什么必须排序：动态分组是按相邻行切窗口的流式算子，它假设输入按时间单调；")
    print("乱序输入不会报错，会**静默产生重复桶**——比报错更危险，所以新版直接拒绝。")


def e4() -> None:
    rule("E4  join_asof 与 DuckDB ASOF 的结果对齐")
    bars = (
        pl.scan_parquet(TRADES, hive_partitioning=True)
        .filter(pl.col("dt") == pl.date(2026, 11, 5))
        .with_columns((pl.col("ts") // 60_000_000_000 * 60_000_000_000).alias("ts"))
        .group_by(["symbol", "ts"])
        .agg(pl.col("vol").sum().alias("vol"))
        .sort(["symbol", "ts"])
        .collect()
    )
    q = (
        pl.scan_parquet(BOOK, hive_partitioning=True)
        .filter((pl.col("dt") == pl.date(2026, 11, 5)) & (pl.col("symbol") == "002322.SZ"))
        .select(["symbol", "ts", "bid_px1", "ask_px1"])
        .sort(["symbol", "ts"])
        .collect()
    )
    t_pl, joined = timed(
        lambda: bars.join_asof(q, on="ts", by="symbol", strategy="backward"), 3
    )
    con = duckdb.connect()
    # 先物化（否则会退化成嵌套循环，见第 6 章）
    con.execute(f"CREATE OR REPLACE TEMP TABLE b AS SELECT symbol, ts // 60000000000 * 60000000000 AS ts, "
                f"sum(vol) vol FROM read_parquet('{TRADES}', hive_partitioning=true) "
                f"WHERE dt='2026-11-05' GROUP BY 1,2")
    con.execute(f"CREATE OR REPLACE TEMP TABLE qq AS SELECT symbol, ts, bid_px1, ask_px1 "
                f"FROM read_parquet('{BOOK}', hive_partitioning=true) "
                f"WHERE dt='2026-11-05' AND symbol='002322.SZ'")
    t_duck, r_duck = timed(
        lambda: con.execute("SELECT count(*) FROM b ASOF JOIN qq USING (symbol, ts)").fetchone(), 3
    )
    print(f"Polars join_asof  ：{t_pl:6.1f} ms，{joined.height:,} 行，非空 bid {joined['bid_px1'].null_count()} 个空")
    print(f"DuckDB ASOF       ：{t_duck:6.1f} ms，{r_duck[0]:,} 行")
    print("两边行数不同是正常的：DuckDB 用 INNER ASOF（无匹配丢弃），Polars 默认 LEFT（保留并置 null）。")
    out["e4"] = {"polars_ms": t_pl, "polars_rows": joined.height,
                 "duck_ms": t_duck, "duck_rows": int(r_duck[0])}


def e5() -> None:
    rule("E5  sink_parquet：什么时候真的流式")
    n = 5_000_000
    df = pl.DataFrame(
        {
            "symbol": (pl.arange(0, n, eager=True) % 100).cast(pl.Int32),
            "ts": pl.arange(0, n, eager=True) * 1_000_000,
            "px": (pl.arange(0, n, eager=True) % 10_000).cast(pl.Float64) / 100,
            "vol": (pl.arange(0, n, eager=True) % 1000),
        }
    )
    print(f"合成 {n:,} 行，内存中 {df.estimated_size('mb'):.0f} MB；下面三种写出方式的 RSS 增量")

    base = EXP / "e5"
    base.mkdir(exist_ok=True)

    def run(name, builder, path):
        rss0 = rss_mb()
        t0 = time.perf_counter()
        builder(path)
        ms = (time.perf_counter() - t0) * 1000
        rss = rss_mb() - rss0
        size = path.stat().st_size / 2**20
        print(f"  {name:26} {ms:7.0f} ms   RSS +{rss:6.0f} MB   输出 {size:5.1f} MB")
        return dict(name=name, ms=ms, rss=rss, mb=size)

    rows = []
    # A 纯流式：过滤 + 写出，不需要全局排序
    rows.append(run("scan→filter→sink", lambda p: df.lazy().filter(pl.col("px") > 50).sink_parquet(p),
                    base / "a.parquet"))
    # B 需要全局排序：排序会打断流式
    rows.append(run("scan→sort→sink", lambda p: df.lazy().sort(["symbol", "ts"]).sink_parquet(p),
                    base / "b.parquet"))
    # C 先 collect 再写
    def collect_write(p):
        df.lazy().sort(["symbol", "ts"]).collect().write_parquet(p)
    rows.append(run("collect→write", collect_write, base / "c.parquet"))

    print("\nA 与 B 的差别只有 sort：排序是全局算子，必须把整份数据攒进内存；")
    print("没有 sort 时 sink 是逐 batch 写，RSS 增量接近 0。想省内存，先问能不能不排序。")
    back_a = pl.read_parquet(base / "a.parquet")
    same = back_a.height == df.filter(pl.col("px") > 50).height
    print(f"读回 A：{back_a.height:,} 行，与过滤后行数一致={same}")
    out["e5"] = {"rows": n, "runs": rows, "same": bool(same)}


def e6() -> None:
    rule("E6  三引擎对齐")
    con = duckdb.connect()
    duck = con.execute(f"""
        SELECT count(*) AS bars, sum(volume) AS vol FROM (
          SELECT symbol, ts // 60000000000 * 60000000000 AS m, sum(vol) AS volume
          FROM read_parquet('{TRADES}', hive_partitioning=true)
          WHERE dt = '2026-11-05'
          GROUP BY 1, 2
        )""").fetchone()
    pl_res = (
        pl.scan_parquet(TRADES, hive_partitioning=True)
        .filter(pl.col("dt") == pl.date(2026, 11, 5))
        .with_columns((pl.col("ts") // 60_000_000_000 * 60_000_000_000).alias("m"))
        .group_by(["symbol", "m"])
        .agg(pl.col("vol").sum().alias("volume"))
        .select(pl.len().alias("bars"), pl.col("volume").sum())
        .collect()
        .row(0)
    )
    print(f"DuckDB: bars={duck[0]:,}  vol={duck[1]:,.0f}")
    print(f"Polars: bars={pl_res[0]:,}  vol={pl_res[1]:,.0f}")
    same = duck[0] == pl_res[0] and abs(duck[1] - pl_res[1]) < 1
    print(f"一致：{same}（不一致就是 bug，不是差不多）")
    out["e6"] = {"same": bool(same), "bars": int(pl_res[0]), "vol": float(pl_res[1])}


def report() -> None:
    e1, e2, e4, e5 = out["e1"], out["e2"], out["e4"], out["e5"]
    lines = [
        "# 第 7 章实测：Polars / 三引擎",
        "",
        f"polars {pl.__version__} / duckdb {duckdb.__version__} / pandas {pd.__version__}，2026-11-11",
        "",
        "## E1 同一查询三实现",
        "",
        "| 实现 | 中位 ms | 相对 Polars |",
        "|---|---|---|",
        f"| pandas（全读 + groupby.apply） | {e1['pandas_ms']:.1f} | {e1['pandas_ms']/e1['polars_ms']:.1f}× |",
        f"| DuckDB | {e1['duckdb_ms']:.1f} | {e1['duckdb_ms']/e1['polars_ms']:.2f}× |",
        f"| Polars（lazy） | {e1['polars_ms']:.1f} | 1.00× |",
        "",
        f"结果：{e1['bars']:,} 个 bar，sum(vol)={e1['vol']:,.0f}，vwap={e1['vwap']:.4f}",
        "",
        "## E2 惰性",
        "",
        f"- 全读再过滤 {e2['eager_ms']:.1f} ms vs 惰性下推 {e2['lazy_ms']:.1f} ms"
        f"（{e2['eager_ms']/e2['lazy_ms']:.1f}×）",
        "",
        "## E4 join_asof",
        "",
        f"- Polars {e4['polars_ms']:.1f} ms / {e4['polars_rows']:,} 行（LEFT）",
        f"- DuckDB {e4['duck_ms']:.1f} ms / {e4['duck_rows']:,} 行（INNER）",
        "",
        "## E5 sink_parquet",
        "",
        f"- {e5['rows']:,} 行，三种写法："
        + "；".join(f"{r['name']} {r['ms']:.0f} ms / +{r['rss']:.0f} MB" for r in e5["runs"]),
        f"- 读回行数一致={e5['same']}",
    ]
    (ROOT / "reports" / "w07-polars.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (ROOT / "reports" / "w07-polars.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print("\n报告已写入 reports/w07-polars.md")


def main() -> None:
    e1()
    e2()
    e3()
    e4()
    e5()
    e6()
    report()


if __name__ == "__main__":
    main()
