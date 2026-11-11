"""第 2 章实验：同一份数据、不同编码组合的体积对比。

用法:
    uv run python py/w02_encoding.py
输出:
    reports/w02-encoding.md   （正文直接引用）
"""
from __future__ import annotations

import pathlib
import sys

import pyarrow as pa
import pyarrow.parquet as pq

SRC = pathlib.Path("lake/data/raw/trades/dt=2026-11-05/exchange=SH/part-600267.SH.parquet")
OUT = pathlib.Path("reports/w02-encoding.md")
EXP = pathlib.Path("lake/data/exp")
COLS = ["symbol", "ts", "seq", "px", "vol", "side", "channel"]


def column_sizes(path: pathlib.Path) -> dict[str, int]:
    """返回 {列名: 压缩后字节数}，以及总字节数。"""
    md = pq.ParquetFile(path).metadata
    out: dict[str, int] = {}
    for rg in range(md.num_row_groups):
        g = md.row_group(rg)
        for c in range(g.num_columns):
            cc = g.column(c)
            out[cc.path_in_schema] = out.get(cc.path_in_schema, 0) + cc.total_compressed_size
    return out


def encodings(path: pathlib.Path) -> dict[str, str]:
    md = pq.ParquetFile(path).metadata
    g = md.row_group(0)
    res = {}
    for c in range(g.num_columns):
        cc = g.column(c)
        enc = "+".join(sorted({e for e in cc.encodings if e in {
            "PLAIN", "RLE", "RLE_DICTIONARY", "DELTA_BINARY_PACKED",
            "DELTA_BYTE_ARRAY", "BYTE_STREAM_SPLIT", "PLAIN_DICTIONARY",
        }}))
        res[cc.path_in_schema] = enc
    return res


def write_case(name: str, table: pa.Table, **kw) -> dict:
    p = EXP / f"{name}.parquet"
    pq.write_table(table, p, compression="zstd", **kw)
    sizes = column_sizes(p)
    return {
        "name": name,
        "file_bytes": p.stat().st_size,
        "columns": sizes,
        "encodings": encodings(p),
        "params": kw,
    }


def main() -> int:
    if not SRC.exists():
        print(f"缺少源文件 {SRC}，先跑 py/gen_tick.py", file=sys.stderr)
        return 1
    EXP.mkdir(parents=True, exist_ok=True)
    table = pq.read_table(SRC)

    cases = []
    # A 全默认（pyarrow 对每列都开字典）
    cases.append(write_case("w02-a-default", table))
    # B 时间戳/序号走 DELTA_BINARY_PACKED：递增整数的最优编码
    cases.append(write_case(
        "w02-b-delta", table,
        use_dictionary=["symbol", "px", "vol", "side", "channel"],
        column_encoding={"ts": "DELTA_BINARY_PACKED", "seq": "DELTA_BINARY_PACKED"},
    ))
    # C 在 B 基础上价格列改 BYTE_STREAM_SPLIT
    cases.append(write_case(
        "w02-c-delta-bss", table,
        use_dictionary=["symbol", "vol", "side", "channel"],
        column_encoding={
            "ts": "DELTA_BINARY_PACKED", "seq": "DELTA_BINARY_PACKED",
            "px": "BYTE_STREAM_SPLIT",
        },
    ))
    # D 只关字典，全部 PLAIN（对照组：看字典本身值多少）
    cases.append(write_case(
        "w02-d-plain", table,
        use_dictionary=False,
        column_encoding={
            "ts": "DELTA_BINARY_PACKED", "seq": "DELTA_BINARY_PACKED",
            "px": "BYTE_STREAM_SPLIT",
        },
    ))
    # E 最优组合 + page index（page 级剪枝）
    cases.append(write_case(
        "w02-e-pageindex", table,
        use_dictionary=["symbol", "vol", "side", "channel"],
        column_encoding={
            "ts": "DELTA_BINARY_PACKED", "seq": "DELTA_BINARY_PACKED",
            "px": "BYTE_STREAM_SPLIT",
        },
        write_page_index=True,
    ))

    base = cases[0]["file_bytes"]

    # F/G 乱序对照组：证明"排序比编码更重要"
    import numpy as np
    rng = np.random.default_rng(7)
    shuffled = table.take(rng.permutation(table.num_rows))
    cases.append(write_case("w02-f-shuffled-delta", shuffled,
                            use_dictionary=["symbol", "px", "vol", "side", "channel"],
                            column_encoding={"ts": "DELTA_BINARY_PACKED",
                                             "seq": "DELTA_BINARY_PACKED"}))
    cases.append(write_case("w02-g-shuffled-dict", shuffled))

    lines = []
    lines.append("# 第 2 章实验：编码组合对体积的影响")
    lines.append("")
    lines.append(f"源文件：`{SRC.name}` · {table.num_rows:,} 行 · 7 列 · zstd(3) · 单个行组")
    lines.append("")
    lines.append("| 方案 | 说明 | 文件体积 | 相对 A | ts | seq | px | vol |")
    lines.append("|---|---|---|---|---|---|---|---|")
    desc = {
        "w02-a-default": "全默认（每列都开字典）",
        "w02-b-delta": "ts/seq 换 DELTA_BINARY_PACKED",
        "w02-c-delta-bss": "再加 px 换 BYTE_STREAM_SPLIT",
        "w02-d-plain": "再关全部字典（全 PLAIN+DICT 对比）",
        "w02-e-pageindex": "C 的配置 + page index",
        "w02-f-shuffled-delta": "**行序打乱** + B 的编码",
        "w02-g-shuffled-dict": "**行序打乱** + 全默认字典",
    }
    for c in cases:
        cols = c["columns"]
        lines.append(
            f"| {c['name'].split('-', 1)[1]} | {desc[c['name']]} | "
            f"{c['file_bytes'] / 1024:.1f} KB | {c['file_bytes'] / base:.2f}x | "
            f"{cols.get('ts', 0) / 1024:.1f} | {cols.get('seq', 0) / 1024:.1f} | "
            f"{cols.get('px', 0) / 1024:.1f} | {cols.get('vol', 0) / 1024:.1f} |"
        )
    lines.append("")
    lines.append("## 各方案的列编码")
    lines.append("")
    for c in cases:
        enc = c["encodings"]
        lines.append(f"**{c['name'].split('-', 1)[1]}**：" +
                     "；".join(f"`{k}`={v}" for k, v in enc.items() if k in ("ts", "seq", "px", "symbol")))
        lines.append("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
