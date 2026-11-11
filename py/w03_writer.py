"""第 3 章实验：写入器参数矩阵。

五个实验，各自只动一个旋钮，其余固定：

  E1a 排序键 × 查询形状   feed 顺序 / (symbol, ts)  ×  时间窗 / 单合约 / 两者
  E1b 编码档              dict / delta / bss / plain
  E2  行组大小            32k / 128k / 512k / 2M 行
  E3  压缩算法            none / snappy / lz4 / zstd-1,3,9 / gzip-6
  E4  页大小 × page index  1 MB / 256 KB / 64 KB

统一数据集：lake/data/raw/trades 全部合约拼成一张全市场表（587,000 行 × 7 列）。

关键指标是"读放大"：用 footer 里的 min/max 模拟读端剪枝，算出真正必须读的
行组字节数，再除以"理论下限"（= 谓词选择率 × 数据字节）。这个指标是确定性的，
不受页缓存和调度噪声影响；ms 只作辅助参考。

用法：
    uv run python py/w03_writer.py
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import time

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "lake" / "data" / "raw" / "trades"
EXP = ROOT / "lake" / "data" / "exp03"
REPORT = ROOT / "reports" / "w03-writer.md"

RG_DEFAULT = 131_072
PAGE_DEFAULT = 1 << 20

ENCODINGS = {
    "dict": dict(),
    "delta": dict(
        use_dictionary=["symbol", "px", "vol", "side", "channel"],
        column_encoding={"ts": "DELTA_BINARY_PACKED", "seq": "DELTA_BINARY_PACKED"},
    ),
    "bss": dict(
        use_dictionary=["symbol", "vol", "side", "channel"],
        column_encoding={
            "ts": "DELTA_BINARY_PACKED",
            "seq": "DELTA_BINARY_PACKED",
            "px": "BYTE_STREAM_SPLIT",
        },
    ),
    "plain": dict(use_dictionary=False),
}


# ---------------------------------------------------------------- 数据

def load_feed() -> pa.Table:
    files = sorted(SRC.rglob("*.parquet"))
    if not files:
        raise SystemExit(f"{SRC} 下没有 parquet，先跑 py/gen_tick.py")
    tab = pa.concat_tables([pq.read_table(p) for p in files])
    return tab.sort_by([("ts", "ascending")])


def probe_queries(tab: pa.Table) -> list[dict]:
    """三个查询形状，选择率都在 5% 上下，方便横向比读放大。"""
    sym = tab.column("symbol")[7].as_py()
    ts = sorted(tab.column("ts").to_pylist())
    t0 = ts[len(ts) // 2]
    t1 = t0 + 900 * 10**9
    return [
        dict(tag="Q1 时间窗 15min", sym=None, t0=t0, t1=t1),
        dict(tag="Q2 单合约全时段", sym=sym, t0=None, t1=None),
        dict(tag="Q3 单合约 + 1min", sym=sym, t0=t0, t1=t0 + 60 * 10**9),
    ]


# ---------------------------------------------------------------- 写入与测量

def write_variant(tab: pa.Table, path: pathlib.Path, *, codec: str = "zstd",
                  rg: int = RG_DEFAULT, enc: str = "dict", page: int = PAGE_DEFAULT,
                  page_index: bool = False, level: int | None = None) -> float:
    kw = dict(
        compression=codec,
        row_group_size=rg,
        data_page_size=page,
        data_page_version="1.0",
        write_page_index=page_index,
        version="2.6",
        **ENCODINGS[enc],
    )
    if level is not None:
        kw["compression_level"] = level
    t0 = time.perf_counter()
    pq.write_table(tab, path, **kw)
    return (time.perf_counter() - t0) * 1000


def prune_probe(path: pathlib.Path, q: dict) -> dict:
    """按 footer 的 min/max 模拟行组剪枝，返回命中行长与字节。"""
    md = pq.ParquetFile(path).metadata
    hit_rg = hit_bytes = data_bytes = hit_rows = 0
    for g in range(md.num_row_groups):
        rg = md.row_group(g)
        chunk = sum(rg.column(c).total_compressed_size for c in range(rg.num_columns))
        data_bytes += chunk
        ok = True
        for c in range(rg.num_columns):
            cc = rg.column(c)
            st = cc.statistics
            if st is None:
                continue
            name = cc.path_in_schema
            if name == "symbol" and q["sym"] is not None:
                ok = ok and st.min <= q["sym"] <= st.max
            elif name == "ts" and q["t0"] is not None:
                ok = ok and not (st.max < q["t0"] or st.min >= q["t1"])
        if ok:
            hit_rg += 1
            hit_bytes += chunk
            hit_rows += rg.num_rows
    return dict(
        row_groups=md.num_row_groups, hit_row_groups=hit_rg,
        data_bytes=data_bytes, hit_bytes=hit_bytes, hit_rows=hit_rows,
        footer_bytes=md.serialized_size, file_bytes=path.stat().st_size,
    )


def where_for(q: dict) -> str:
    parts = []
    if q["sym"] is not None:
        parts.append(f"symbol = '{q['sym']}'")
    if q["t0"] is not None:
        parts.append(f"ts >= {q['t0']} AND ts < {q['t1']}")
    return " WHERE " + " AND ".join(parts) if parts else ""


def sql_for(path: pathlib.Path, q: dict) -> str:
    return f"SELECT count(*), sum(vol) FROM read_parquet('{path.as_posix()}'){where_for(q)}"


def selectivity(con, glob: str, q: dict) -> float:
    """谓词真实选择率：剪枝的理论下限，用来算读放大。"""
    n = con.execute(f"SELECT count(*) FROM read_parquet('{glob}')").fetchone()[0]
    m = con.execute(f"SELECT count(*) FROM read_parquet('{glob}'){where_for(q)}").fetchone()[0]
    return m / n


def median_ms(con, sql: str, repeat: int = 7) -> float:
    con.execute(sql).fetchall()
    xs = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        con.execute(sql).fetchall()
        xs.append((time.perf_counter() - t0) * 1000)
    return statistics.median(xs)


# ---------------------------------------------------------------- 报告

def table(rows: list[dict], cols: list[str], headers: list[str]) -> str:
    def cell(v):
        if isinstance(v, float):
            return f"{v:,.1f}" if abs(v) >= 100 else f"{v:.2f}"
        if isinstance(v, int):
            return f"{v:,}"
        return str(v)

    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(cell(r.get(c)) for c in cols) + " |" for r in rows]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="跳过压缩扫的重复计时")
    args = ap.parse_args()

    EXP.mkdir(parents=True, exist_ok=True)
    feed = load_feed()
    n = feed.num_rows
    qs = probe_queries(feed)
    print(f"数据集：{n:,} 行 × {feed.num_columns} 列；查询：{[q['tag'] for q in qs]}")
    con = duckdb.connect()
    glob = (SRC / "**" / "*.parquet").as_posix()
    for q in qs:
        q["sel"] = selectivity(con, glob, q)
        print(f"  {q['tag']:16} 选择率 {q['sel']*100:5.2f}%")
    out: dict[str, list[dict]] = {}

    # ---- E0 三语言默认值 ---------------------------------------------
    print("\n[E0] 默认参数（(symbol, ts) 排序固定）")
    sorted_tab = feed.sort_by([("symbol", "ascending"), ("ts", "ascending")])
    e0 = []
    for tag, kw in [
        ("pyarrow 默认（snappy + 全字典）", dict(compression="snappy")),
        ("C++/Rust 默认（none + 全字典）", dict(compression="none")),
        ("调优后（zstd-3 + DELTA + rg128k）",
         dict(compression="zstd", row_group_size=131_072, **ENCODINGS["delta"])),
    ]:
        p = EXP / f"e0-{len(e0)}.parquet"
        t0 = time.perf_counter()
        pq.write_table(sorted_tab, p, version="2.6", **kw)
        w = (time.perf_counter() - t0) * 1000
        kb = p.stat().st_size / 1024
        e0.append(dict(variant=tag, kb=kb, bytes_row=p.stat().st_size / n, write_ms=w))
        print(f"  {tag:38} {kb/1024:5.2f} MB  {p.stat().st_size/n:5.2f} B/行  写 {w:5.0f} ms")
    out["e0"] = e0

    # ---- E1a 排序键 × 查询形状 --------------------------------------
    print("\n[E1a] 排序键 × 查询形状（rg=32,768 + DELTA，18 个行组）")
    e1a = []
    for tag, keys in [("到达顺序（ts 交错）", None),
                      ("(symbol, ts) 聚簇", [("symbol", "ascending"), ("ts", "ascending")])]:
        t = feed.sort_by(keys) if keys else feed
        p = EXP / f"e1a-{'feed' if keys is None else 'sym'}.parquet"
        w = write_variant(t, p, rg=32_768, enc="delta")
        for q in qs:
            pr = prune_probe(p, q)
            amp = (pr["hit_bytes"] / pr["data_bytes"]) / q["sel"]
            r = dict(order=tag, query=q["tag"], write_ms=w, sel=q["sel"],
                     rg_hit=pr["hit_row_groups"], row_groups=pr["row_groups"],
                     hit_mb=pr["hit_bytes"] / 1e6, data_mb=pr["data_bytes"] / 1e6,
                     amplification=amp, ms=median_ms(con, sql_for(p, q)))
            print(f"  {tag:20} {q['tag']:16} 命中 {pr['hit_row_groups']:2d}/{pr['row_groups']}"
                  f"  {pr['hit_bytes']/1e6:5.2f}/{pr['data_bytes']/1e6:5.2f} MB"
                  f"  放大 {amp:5.0f}x  {r['ms']:5.1f} ms")
            e1a.append(r)
    out["e1a"] = e1a

    # ---- E1b 编码档（固定 (symbol, ts) 排序）------------------------
    print("\n[E1b] 编码档（排序固定 (symbol, ts)，rg=131,072）")
    q3 = qs[2]
    e1b = []
    for enc in ("dict", "delta", "bss", "plain"):
        p = EXP / f"e1b-{enc}.parquet"
        w = write_variant(sorted_tab, p, enc=enc)
        pr = prune_probe(p, q3)
        r = dict(enc=enc, write_ms=w, ms=median_ms(con, sql_for(p, q3)),
                 kb=pr["file_bytes"] / 1024, hit_mb=pr["hit_bytes"] / 1e6,
                 row_groups=pr["row_groups"], footer_bytes=pr["footer_bytes"])
        print(f"  {enc:6} {r['kb']/1024:6.2f} MB  写 {w:5.0f} ms")
        e1b.append(r)
    out["e1b"] = e1b

    # ---- E2 行组大小 -------------------------------------------------
    print("\n[E2] 行组大小（(symbol, ts) + DELTA + zstd-3）")
    e2 = []
    for rg in (32_768, 131_072, 524_288, 2_097_152):
        p = EXP / f"e2-rg{rg}.parquet"
        w = write_variant(sorted_tab, p, rg=rg, enc="delta")
        pr = prune_probe(p, q3)
        r = dict(rg=rg, write_ms=w, ms=median_ms(con, sql_for(p, q3)),
                 kb=pr["file_bytes"] / 1024, row_groups=pr["row_groups"],
                 footer_bytes=pr["footer_bytes"], hit_mb=pr["hit_bytes"] / 1e6,
                 hit_rows=pr["hit_rows"], amplification=pr["hit_bytes"] / pr["data_bytes"] / q3["sel"])
        print(f"  rg={rg:>9,}  {r['kb']/1024:5.2f} MB  {pr['row_groups']:2d} 组"
              f"  footer {pr['footer_bytes']:>6,} B  点查读 {pr['hit_bytes']/1e6:5.2f} MB"
              f"  {r['ms']:5.1f} ms")
        e2.append(r)
    out["e2"] = e2

    # ---- E3 压缩算法 -------------------------------------------------
    print("\n[E3] 压缩算法（(symbol, ts) + DELTA）")
    e3 = []
    for codec, lv in [("none", None), ("snappy", None), ("lz4", None),
                      ("zstd", 1), ("zstd", 3), ("zstd", 9), ("gzip", 6)]:
        tag = codec if lv in (None, 3) else f"{codec}-{lv}"
        p = EXP / f"e3-{tag}.parquet"
        w = write_variant(sorted_tab, p, codec=codec, level=lv, enc="delta")
        full = f"SELECT count(*), sum(vol), avg(px) FROM read_parquet('{p.as_posix()}')"
        r = dict(codec=tag, write_ms=w, scan_ms=median_ms(con, full, 9 if not args.quick else 3),
                 kb=p.stat().st_size / 1024)
        print(f"  {tag:9} {r['kb']/1024:5.2f} MB  写 {w:6.0f} ms  全扫 {r['scan_ms']:5.1f} ms")
        e3.append(r)
    out["e3"] = e3

    # ---- E3b 编码 × 压缩 交互 ----------------------------------------
    print("\n[E3b] 编码 × 压缩（同一份 (symbol, ts) 排序数据）")
    e3b = []
    for enc in ("dict", "plain"):
        for codec in ("none", "zstd"):
            p = EXP / f"e3b-{enc}-{codec}.parquet"
            w = write_variant(sorted_tab, p, codec=codec, enc=enc)
            kb = p.stat().st_size / 1024
            e3b.append(dict(enc=enc, codec=codec, kb=kb, write_ms=w))
            print(f"  {enc:6} + {codec:6} {kb/1024:5.2f} MB  写 {w:5.0f} ms")
    out["e3b"] = e3b

    # ---- E4 页大小 × page index --------------------------------------
    print("\n[E4] 页大小 × page index（单行组 2,097,152 行，Q3 窗口 1min）")
    e4 = []
    for page in (1 << 20, 1 << 18, 1 << 16):
        for pi in (False, True):
            p = EXP / f"e4-{page}-{int(pi)}.parquet"
            w = write_variant(sorted_tab, p, rg=2_097_152, page=page, page_index=pi, enc="delta")
            r = dict(page_kb=page // 1024, index="开" if pi else "关", write_ms=w,
                     kb=p.stat().st_size / 1024, bytes=p.stat().st_size,
                     ms=median_ms(con, sql_for(p, q3)))
            print(f"  page={page//1024:>4} KB  index={int(pi)}  {r['kb']/1024:5.2f} MB"
                  f"  写 {w:5.0f} ms  点查 {r['ms']:5.1f} ms")
            e4.append(r)
    out["e4"] = e4

    # ---- 报告 -------------------------------------------------------
    nl = "\n\n"
    parts = [
        "# 第 3 章实验：写入器参数矩阵",
        f"数据集：`lake/data/raw/trades` 全部合约按 `ts` 拼成一张全市场表，"
        f"**{n:,} 行 × {feed.num_columns} 列**（8 个合约 × 2 天）。"
        "耗时取 7 次中位数（全扫 9 次），已预热。文件写在 `lake/data/exp03/`。\n\n"
        "需读字节按**列块压缩后大小**累加（`total_compressed_size`），"
        "不是 `total_byte_size`（那是未压缩大小，容易把结论夸大 2–3 倍）。\n\n"
        "读放大 = （命中行组字节 / 数据字节）÷ 谓词选择率。"
        "=1 表示剪枝正好压到选择率这个理论下限；>1 表示白读了不该读的字节。",
        "## E0 默认参数\n\n" + table(
            e0, ["variant", "kb", "bytes_row", "write_ms"],
            ["写法", "体积 KB", "字节/行", "写 ms"],
        ),
        "## E1a 排序键 × 查询形状\n\n" + table(
            e1a, ["order", "query", "rg_hit", "row_groups", "hit_mb",
                  "data_mb", "amplification", "ms"],
            ["行序", "查询", "命中行组", "总行组", "需读 MB", "数据 MB", "读放大", "耗时 ms"],
        ),
        "## E1b 编码档（(symbol, ts) 排序，rg=131,072）\n\n" + table(
            e1b, ["enc", "kb", "write_ms", "hit_mb", "footer_bytes", "ms"],
            ["编码", "体积 KB", "写 ms", "点查需读 MB", "footer B", "点查 ms"],
        ),
        "## E2 行组大小\n\n" + table(
            e2, ["rg", "row_groups", "kb", "footer_bytes", "hit_mb", "hit_rows",
                 "amplification", "write_ms", "ms"],
            ["行组行数", "行组数", "体积 KB", "footer B", "点查需读 MB", "点查命中行",
             "读放大", "写 ms", "点查 ms"],
        ),
        "## E3 压缩算法\n\n" + table(
            e3, ["codec", "kb", "write_ms", "scan_ms"],
            ["压缩", "体积 KB", "写 ms", "全扫 ms"],
        ),
        "## E3b 编码 × 压缩\n\n" + table(
            e3b, ["enc", "codec", "kb", "write_ms"],
            ["编码档", "压缩", "体积 KB", "写 ms"],
        ),
        "## E4 页大小 × page index（单行组）\n\n" + table(
            e4, ["page_kb", "index", "bytes", "write_ms", "ms"],
            ["页 KB", "page index", "体积 B", "写 ms", "点查 ms"],
        ),
    ]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(nl.join(parts) + "\n", encoding="utf-8")
    (ROOT / "reports" / "w03-writer.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n写出 {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
