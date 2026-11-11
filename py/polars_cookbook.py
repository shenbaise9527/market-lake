"""Polars 常用方法示例集：文档 site/docs/appendix/polars-cookbook.md 里每个代码块都在这里跑过。

跑法：
    uv run python py/polars_cookbook.py            # 全部
    uv run python py/polars_cookbook.py --only 5   # 只跑第 5 节

设计约束（与站点其它章一致）：
  * 每个示例的输出都真实打印，文档里的数字照抄，不手写"约等于"。
  * 尽量用仓库里真实的 trades 数据，而不是编造的 3 行小表。
  * 每个小节只打印前几行——文档要的是"形状对不对"，不是全表。
"""

from __future__ import annotations

import pathlib
import sys
import time

import polars as pl

TRADES = "lake/data/lake/trades/**/*.parquet"
ONE = "lake/data/lake/trades/dt=2026-11-04/symbol=002322.SZ/part-0.parquet"
OUT = pathlib.Path("lake/data/exp/cookbook")
OUT.mkdir(parents=True, exist_ok=True)

pl.Config.set_tbl_rows(6)
pl.Config.set_tbl_cols(10)
pl.Config.set_fmt_str_lengths(24)
pl.Config.set_tbl_width_chars(100)


def title(n: str, text: str) -> None:
    print(f"\n{'=' * 78}\n## {n}  {text}\n{'=' * 78}")


def show(x, n: int = 5) -> None:
    """打印 LazyFrame / DataFrame / Series 的前 n 行。"""
    if isinstance(x, pl.LazyFrame):
        x = x.collect()
    print(x if isinstance(x, pl.Series) else x.head(n))
    if isinstance(x, pl.DataFrame):
        print(f"[shape: {x.height} 行 × {x.width} 列]")


def sec_read() -> None:
    title("B1", "scan_parquet：惰性扫描 + 谓词下推")

    lf = pl.scan_parquet(TRADES, hive_partitioning=True)
    q = (
        lf.filter((pl.col("dt") == "2026-11-04") & (pl.col("symbol") == "002322.SZ"))
        .select("ts", "px", "vol")
    )
    print(q.explain())          # 看下推结果，不执行
    t0 = time.perf_counter()
    out = q.collect()
    print(f"结果 {out.height} 行，耗时 {(time.perf_counter() - t0) * 1000:.1f} ms")
    show(out, 3)

    title("B2", "read_parquet 与 scan_parquet 的差别（同一查询）")

    t0 = time.perf_counter()
    eager = (
        pl.read_parquet(TRADES, hive_partitioning=True)
        .filter((pl.col("dt") == "2026-11-04") & (pl.col("symbol") == "002322.SZ"))
        .select("ts", "px", "vol")
    )
    t_eager = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    lazy = q.collect()
    t_lazy = (time.perf_counter() - t0) * 1000
    print(f"read_parquet 全读再筛：{eager.height} 行 / {t_eager:.1f} ms（含进程首次读盘，看趋势不看绝对值）")
    print(f"scan_parquet 下推：  {lazy.height} 行 / {t_lazy:.1f} ms")
    print(f"两者结果相同：{eager.equals(lazy)}")

    title("B3", "hive 分区键：路径里的 dt / symbol 会被读成列")
    print(pl.scan_parquet(TRADES, hive_partitioning=True).collect_schema())

    title("B4", "sink_parquet：过滤 + 投影后流式写出")
    tgt = OUT / "px_only.parquet"
    (
        pl.scan_parquet(TRADES, hive_partitioning=True)
        .filter(pl.col("symbol") == "002322.SZ")
        .select("ts", "px", "vol")
        .sink_parquet(tgt)
    )
    print(f"写出 {tgt}，{tgt.stat().st_size / 1024:.0f} KB")
    print(pl.read_parquet(tgt).head(2))

    title("B5", "CSV / NDJSON / IPC 之间的转换")
    csv = OUT / "small.csv"
    pl.read_parquet(ONE).head(5).select("symbol", "ts", "px", "vol").write_csv(csv)
    print("写出的 CSV 前 3 行：")
    print("\n".join(csv.read_text(encoding="utf-8").splitlines()[:3]))
    back = pl.scan_csv(csv).collect()
    print(f"再读回来：{back.height} 行，dtypes={back.dtypes}")


def sec_select() -> None:
    df = pl.read_parquet(ONE).head(200)

    title("C1", "select：按名、按表达式、按正则")
    show(df.select("symbol", "px", "vol"), 3)
    show(df.select(pl.col("px") * pl.col("vol")).head(3), 3)
    print("正则批量选列（book 数据才有意义）：")
    show(df.select(pl.col("^s.*$")), 3)
    show(df.drop("channel", "side"), 3)

    title("C2", "filter：条件、多条件、集合、区间")
    print("单个条件 >")
    show(df.filter(pl.col("px") > 71), 3)
    print("两个条件 &，注意括号一个都不能少")
    show(df.filter((pl.col("px") > 71) & (pl.col("side") == 1)), 3)
    print("集合：is_in")
    show(df.filter(pl.col("channel").is_in([0, 2])), 3)
    print("区间：is_between")
    show(df.filter(pl.col("vol").is_between(100, 200)), 3)
    print("取反：~")
    show(df.filter(~pl.col("channel").is_in([0])), 3)

    title("C3", "按位置取行")
    show(df.head(2), 2)
    show(df.tail(2), 2)
    show(df.slice(10, 3), 3)
    print("按行号列表 gather（不是 loc/iloc，Polars 用 gather）")
    show(df.gather([0, 5, 9]), 3)


def sec_cols() -> None:
    df = pl.read_parquet(ONE).head(200)

    title("D1", "with_columns：新增/覆盖列，一律返回新表")
    show(
        df.with_columns(
            (pl.col("px") * pl.col("vol")).alias("notional"),
            (pl.col("px") - pl.col("px").shift(1)).alias("dpx"),
            pl.col("ts").cast(pl.Datetime("ns")).alias("t"),
        ).select("px", "notional", "dpx", "t"),
        4,
    )
    print("原 df 未被修改（Polars 没有 inplace）：", "notional" in df.columns)

    title("D2", "cast 与类型严格性")
    print(df.select(pl.col("vol").cast(pl.Float64).alias("vol_f")).dtypes)
    print("float -> int 会截断，不是四舍五入：")
    print(pl.DataFrame({"x": [1.9, 2.5, -1.9]}).with_columns(pl.col("x").cast(pl.Int64)))

    title("D3", "when / then / otherwise：向量化 if-else")
    show(
        df.with_columns(
            pl.when(pl.col("side") == 1).then(pl.lit("买"))
            .when(pl.col("side") == -1).then(pl.lit("卖"))
            .otherwise(pl.lit("未知")).alias("side_cn")
        ).select("side", "side_cn"),
        5,
    )

    title("D4", "字符串：str 命名空间")
    s = pl.DataFrame({"code": ["2322", "600267", "1", "SZ000001"]})
    show(
        s.with_columns(
            pl.col("code").str.zfill(6).alias("补齐6位"),
            pl.col("code").str.len_chars().alias("长度"),
            pl.col("code").str.contains("SZ").alias("含SZ"),
            pl.col("code").str.slice(0, 2).alias("前两位"),
            pl.col("code").str.to_uppercase().alias("大写"),
        ),
        4,
    )
    print("正则提取：str.extract")
    print(
        pl.DataFrame({"s": ["600000.SH", "000001.SZ", "IF2412.CFE"]}).with_columns(
            pl.col("s").str.extract(r"^(\d+)\.", 1).alias("code"),
            pl.col("s").str.extract(r"\.(\w+)$", 1).alias("exch"),
        )
    )

    title("D5", "时间：dt 命名空间（int64 纳秒 -> Datetime -> 各种拆解）")
    show(
        df.with_columns(pl.col("ts").cast(pl.Datetime("ns")).alias("t"))
        .select(
            "t",
            pl.col("t").dt.truncate("1m").alias("分钟桶"),
            pl.col("t").dt.hour().alias("时"),
            pl.col("t").dt.minute().alias("分"),
            pl.col("t").dt.strftime("%H:%M:%S%.3f").alias("格式化"),
        ),
        4,
    )
    print("epoch 纳秒直接用 pl.from_epoch 也是同一条路：")
    print(
        pl.DataFrame({"ts": [1793755800010095596]}).select(
            pl.from_epoch("ts", time_unit="ns").alias("t")
        )
    )


def sec_agg() -> None:
    df = pl.read_parquet(ONE)

    title("E1", "group_by + agg：一次算多个指标")
    bars = (
        df.with_columns(pl.col("ts").cast(pl.Datetime("ns")).alias("t"))
        .with_columns(pl.col("t").dt.truncate("1m"))
        .group_by("t")
        .agg(
            pl.len().alias("trades"),
            pl.col("vol").sum().alias("volume"),
            pl.col("px").mean().alias("avg_px"),
            pl.col("px").min().alias("low"),
            pl.col("px").max().alias("high"),
            pl.col("px").first().alias("open"),
            pl.col("px").last().alias("close"),
            (pl.col("px") * pl.col("vol")).sum().alias("notional"),
        )
        .with_columns((pl.col("notional") / pl.col("volume")).alias("vwap"))
        .sort("t")
    )
    show(bars, 5)
    print(f"共 {bars.height} 个分钟桶")

    title("E2", "分组键多列 + 组内排序取首尾")
    show(
        df.group_by("side").agg(
            pl.len().alias("n"),
            pl.col("vol").sum().alias("vol_sum"),
            pl.col("vol").median().alias("vol_median"),
            pl.col("vol").quantile(0.95).alias("vol_p95"),
            pl.col("vol").std().alias("vol_std"),
            pl.col("symbol").n_unique().alias("symbols"),
        ).sort("n", descending=True),
        4,
    )

    title("E3", "agg 里返回列表（implode），以及取组内第一条")
    show(
        df.group_by(pl.col("ts").cast(pl.Datetime("ns")).dt.truncate("1m").alias("t"))
        .agg(pl.col("px").head(3).alias("前3笔"), pl.col("px").len().alias("笔数"))
        .sort("t")
        .head(3),
        3,
    )

    title("E4", "分组后按组内顺序取 top-k：sort + group_by + head")
    show(
        df.group_by("side")
        .agg(pl.col("vol").top_k(3).alias("最大3笔量"))
        .sort("side"),
        3,
    )


def sec_window() -> None:
    df = (
        pl.read_parquet(ONE)
        .with_columns(pl.col("ts").cast(pl.Datetime("ns")).alias("t"))
        .sort("t")
        .head(5000)
    )

    title("F1", "over：组内聚合再广播回原行（替代 pandas transform）")
    show(
        df.with_columns(
            pl.col("t").dt.truncate("1m").alias("m"),
        )
        .with_columns(
            pl.col("vol").sum().over("m").alias("m_volume"),
            (pl.col("vol") / pl.col("vol").sum().over("m")).round(4).alias("share"),
        )
        .select("t", "vol", "m_volume", "share"),
        5,
    )

    title("F2", "over + 排名 / 累计 / 差分")
    show(
        df.with_columns(
            pl.col("vol").rank("ordinal", descending=True).over("side").alias("组内量排名"),
            pl.col("vol").cum_sum().over("side").alias("组内累计量"),
            (pl.col("px") - pl.col("px").shift(1).over("side")).alias("与上一笔价差"),
        ).select("side", "px", "vol", "组内量排名", "组内累计量", "与上一笔价差"),
        6,
    )

    title("F3", "rolling：滚动窗口（必须先排序）")
    show(
        df.with_columns(
            pl.col("px").rolling_mean(window_size=100).alias("ma100"),
            pl.col("px").rolling_std(window_size=100).alias("sd100"),
        ).select("px", "ma100", "sd100").tail(5),
        5,
    )


def sec_sort() -> None:
    df = pl.read_parquet(ONE)

    title("G1", "sort：多键、升降序分别指定")
    show(df.sort(["side", "vol"], descending=[False, True]), 4)

    title("G2", "unique：按子集去重，keep 决定留哪条")
    dup = pl.DataFrame(
        {
            "symbol": ["A", "A", "A", "B"],
            "ts": [1, 1, 2, 3],
            "seq": [0, 1, 0, 0],
            "px": [10.0, 10.5, 11.0, 20.0],
        }
    )
    print("原始：")
    show(dup, 4)
    print("按 (symbol, ts) 去重，keep='last'（用 seq 大的那条）：")
    show(dup.unique(subset=["symbol", "ts"], keep="last", maintain_order=True), 4)
    print("keep='any' 不保证留哪条，别用它做对账：")
    show(dup.unique(subset=["symbol", "ts"], keep="any", maintain_order=True), 3)

    title("G3", "top_k 与 arg_sort")
    print(df.select(pl.col("vol").top_k(5)).to_series().to_list())
    print("arg_sort 拿行号：", df.select(pl.col("vol").arg_sort(descending=True)).head(3).to_series().to_list())


def sec_join() -> None:
    left = pl.DataFrame({"symbol": ["A", "B", "C"], "v": [1, 2, 3]})
    right = pl.DataFrame({"symbol": ["A", "B", "D"], "w": [10, 20, 40]})

    title("H1", "join 的六种 how")
    for how in ("inner", "left", "full", "semi", "anti"):
        show(left.join(right, on="symbol", how=how), 4)
        print(f"  ↑ how={how!r}")
    print("cross join 不传 on（笛卡尔积）：")
    show(left.join(right, how="cross"), 6)

    title("H2", "同名非键列冲突：用 suffix 区分")
    l2 = pl.DataFrame({"symbol": ["A"], "px": [1.0]})
    r2 = pl.DataFrame({"symbol": ["A"], "px": [2.0]})
    print(l2.join(r2, on="symbol", suffix="_book"))

    title("H3", "join_asof：把成交对齐到最近的快照（Polars 默认 LEFT）")
    trades = (
        pl.read_parquet(ONE)
        .select("symbol", pl.col("ts").cast(pl.Datetime("ns")).alias("t"), "px")
        .sort("t")
        .head(50)
    )
    snaps = (
        pl.read_parquet(ONE)
        .select(pl.col("ts").cast(pl.Datetime("ns")).alias("t"))
        .sort("t")
        .gather_every(20)
        .with_columns(pl.lit(3000).alias("bid_vol"))
    )
    print(f"trades {trades.height} 行、snaps {snaps.height} 行")
    show(trades.join_asof(snaps, on="t", strategy="backward"), 5)
    print("strategy='forward' 取的是下一笔快照：")
    show(trades.join_asof(snaps, on="t", strategy="forward"), 3)


def sec_time() -> None:
    df = (
        pl.read_parquet(ONE)
        .with_columns(pl.col("ts").cast(pl.Datetime("ns")).alias("t"))
        .sort("t")
    )

    title("I1", "group_by_dynamic：动态时间窗（输入必须有序）")
    bars = df.group_by_dynamic("t", every="5m", period="5m", closed="left").agg(
        pl.len().alias("trades"),
        pl.col("px").first().alias("open"),
        pl.col("px").last().alias("close"),
        pl.col("vol").sum().alias("volume"),
    )
    show(bars, 5)
    print(f"5 分钟桶共 {bars.height} 个")

    print("\n乱序输入会直接报错（这是保护，不是限制）：")
    try:
        df.sample(fraction=1.0, shuffle=True, seed=1).group_by_dynamic(
            "t", every="5m"
        ).agg(pl.len())
    except Exception as e:  # noqa: BLE001
        print(f"  {type(e).__name__}: {str(e)[:90]}")

    title("I2", "offset：平移桶边界（开盘不是整点时用）")
    print("默认：桶边界落在整 5 分钟")
    show(df.group_by_dynamic("t", every="5m").agg(pl.len().alias("n")).head(3), 3)
    print("offset='-3m'：整个网格平移 3 分钟")
    show(
        df.group_by_dynamic("t", every="5m", offset="-3m").agg(pl.len().alias("n")).head(3),
        3,
    )

    title("I3", "upsample：把稀疏序列补成规整网格")
    sparse = pl.DataFrame(
        {"t": pl.datetime_range(
            pl.datetime(2026, 11, 4, 9, 30), pl.datetime(2026, 11, 4, 9, 34),
            interval="1m", eager=True),
         "px": [10.0, 10.2, 10.1, 10.4, 10.3]}
    )
    show(sparse.upsample(time_column="t", every="30s").select(
        "t", pl.col("px").forward_fill()), 6)


def sec_reshape() -> None:
    df = pl.read_parquet(ONE).head(3000)

    title("J1", "pivot：长转宽")
    wide = (
        df.with_columns(pl.col("ts").cast(pl.Datetime("ns")).dt.truncate("1m").alias("m"))
        .pivot(on="side", index="m", values="vol", aggregate_function="sum")
    )
    show(wide, 4)
    print("列名：", wide.columns)

    title("J2", "unpivot：宽转长")
    show(wide.unpivot(on=["1", "-1", "0"], variable_name="side", value_name="vol"), 4)

    title("J3", "explode：列表列展开成行")
    nested = pl.DataFrame(
        {"symbol": ["A", "B"], "pxs": [[10.0, 10.1, 10.2], [20.0]], "n": [3, 1]}
    )
    show(nested, 3)
    show(nested.explode("pxs"), 4)

    title("J4", "concat：纵向拼、放宽类型、横向拼")
    a = pl.DataFrame({"x": [1, 2]})
    b = pl.DataFrame({"x": [3.5]})
    try:
        print(pl.concat([a, b]))
    except Exception as e:  # noqa: BLE001
        print(f"  严格纵向拼接报错：{type(e).__name__}")
    print("用 vertical_relaxed 放宽类型：")
    print(pl.concat([a, b], how="vertical_relaxed"))
    print("横向（必须等高）：")
    print(pl.concat([a, pl.DataFrame({"y": [10, 20]})], how="horizontal"))


def sec_lazy() -> None:
    title("K1", "explain：看计划，不执行")
    lf = pl.scan_parquet(TRADES, hive_partitioning=True)
    q = (
        lf.filter(pl.col("symbol") == "002322.SZ")
        .group_by("dt")
        .agg(pl.len().alias("n"), pl.col("vol").sum().alias("v"))
    )
    print(q.explain())

    title("K2", "collect 的 engine 参数与 profile")
    t0 = time.perf_counter()
    out = q.collect(engine="in-memory")
    print(f"in-memory: {out.height} 行 / {(time.perf_counter() - t0) * 1000:.1f} ms")
    show(out, 4)


def sec_pandas_map() -> None:
    title("L1", "同一件事的两套写法（pandas → Polars）")
    df = pl.read_parquet(ONE).head(200)
    print("Polars:")
    show(
        df.filter(pl.col("px") > 71)
        .group_by("side")
        .agg(pl.col("vol").sum().alias("vol"))
        .sort("side"),
        3,
    )
    import pandas as pd

    pdf = df.to_pandas()
    print("pandas（等价）：")
    print(
        pdf[pdf["px"] > 71]
        .groupby("side", as_index=False)
        .agg(vol=("vol", "sum"))
        .sort_values("side")
    )


SECTIONS = {
    "B": sec_read, "C": sec_select, "D": sec_cols, "E": sec_agg,
    "F": sec_window, "G": sec_sort, "H": sec_join, "I": sec_time,
    "J": sec_reshape, "K": sec_lazy, "L": sec_pandas_map,
}


def main() -> None:
    only = None
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1].upper()
    for key, fn in SECTIONS.items():
        if only and key != only:
            continue
        fn()
    print(f"\n{'=' * 78}\n全部完成（polars {pl.__version__}）")


if __name__ == "__main__":
    main()
