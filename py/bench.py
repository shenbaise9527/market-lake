"""基准测试矩阵：编码 × 压缩 × 行组大小 × 是否排序。

输出一张 markdown 表，直接贴进你的实验记录。这是 S5 的核心工具，
也是整个计划里唯一"用数字说话"的地方。

用法：
    python py/bench.py --src lake/data/raw/trades --out reports/bench.md
"""

from __future__ import annotations

import argparse
import pathlib
import time

import duckdb
import pyarrow.parquet as pq


def timed(fn, repeat: int = 3) -> float:
    best = float("inf")
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best * 1000  # ms


def pick_one(src: pathlib.Path) -> pathlib.Path:
    files = sorted(src.rglob("*.parquet"))
    if not files:
        raise SystemExit(f"{src} 下没有 parquet 文件，先跑 py/gen_tick.py")
    return files[len(files) // 2]


def write_variant(table, path, codec, rg, enc, sort):
    t = table.sort_by([("symbol", "ascending"), ("ts", "ascending")]) if sort else table
    kwargs = dict(
        compression=codec,
        row_group_size=rg,
        data_page_version="2.0",
        version="2.6",
    )
    if enc == "bss":
        kwargs.update(
            use_dictionary=False,
            column_encoding={"px": "BYTE_STREAM_SPLIT"},
        )
    else:
        kwargs.update(use_dictionary=["symbol"])
    pq.write_table(t, path, **kwargs)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="lake/data/raw/trades")
    ap.add_argument("--out", default="reports/bench.md")
    ap.add_argument("--codes", default="none,snappy,zstd")
    ap.add_argument("--row-groups", default="65536,1048576")
    ap.add_argument("--encodings", default="plain,bss")
    args = ap.parse_args()

    src = pick_one(pathlib.Path(args.src))
    table = pq.read_table(src)
    symbols = table.column("symbol").to_pylist()
    sym = symbols[0]
    ts = table.column("ts").to_pylist()
    t_mid = sorted(ts)[len(ts) // 2]

    if table.num_rows <= max(int(x) for x in args.row_groups.split(",")):
        print(f"提示：数据只有 {table.num_rows} 行，大于行数的 row_group 设置不会产生行组切分，"
              f"结果会重复。先用 py/gen_tick.py 生成更多数据。\n")

    out_dir = pathlib.Path("lake/data/exp")
    out_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()

    rows, cols = [], []
    raw_rows = [
        "| 方案 | 行组 | 排序 | 体积(MB) | 字节/行 | 全扫(ms) | 点查(ms) | 分钟聚合(ms) |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for codec in args.codes.split(","):
        for rg in [int(x) for x in args.row_groups.split(",")]:
            for enc in args.encodings.split(","):
                for sort in (True, False):
                    tag = f"{codec}-rg{rg}-{enc}-{'s' if sort else 'u'}"
                    p = out_dir / f"{tag}.parquet"
                    write_variant(table, p, codec, rg, enc, sort)

                    q_full = f"SELECT count(*), sum(vol), avg(px) FROM read_parquet('{p}')"
                    q_point = (
                        f"SELECT count(*) FROM read_parquet('{p}') "
                        f"WHERE symbol='{sym}' AND ts BETWEEN {t_mid} AND {t_mid + 60_000_000_000}"
                    )
                    q_agg = (
                        f"SELECT symbol, ts // 60000000000 AS m, count(*) AS n "
                        f"FROM read_parquet('{p}') GROUP BY 1, 2"
                    )

                    size = p.stat().st_size / 1e6
                    full_ms = timed(lambda: con.execute(q_full).fetchall())
                    point_ms = timed(lambda: con.execute(q_point).fetchall())
                    agg_ms = timed(lambda: con.execute(q_agg).fetchall())

                    line = (
                        f"| {codec}-{enc} | {rg} | {'是' if sort else '否'} | {size:.2f} | "
                        f"{p.stat().st_size / table.num_rows:.2f} | {full_ms:.0f} | "
                        f"{point_ms:.1f} | {agg_ms:.0f} |"
                    )
                    raw_rows.append(line)
                    print(line, flush=True)

    report = "\n".join(raw_rows) + "\n"
    pathlib.Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(args.out).write_text(report, encoding="utf-8")
    print(f"\n写入 {args.out}，共 {len(raw_rows) - 2} 个方案")


if __name__ == "__main__":
    main()
