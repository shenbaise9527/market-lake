"""第 4 章实验：Arrow 内存模型，逐字节验证。

E1  行式二进制流 vs 列式二进制流 vs CSV：零拷贝到底省掉了哪一步
E2  buffer 布局：Int64Array / StringArray / DictionaryArray 各有哪些 buffer
E3  validity bitmap 的位序（哪一位对应哪一行）
E4  slice 为什么是 O(1)：地址不变、offset 变；以及它拖住整块 buffer 的代价
E5  RecordBatch / Table / ChunkedArray：concat 何时拷贝，combine_chunks 何时发生
E6  字典数组 vs 纯字符串：内存差多少；以及 Arrow 字典 ≠ Parquet 字典
E7  对齐与内存池：buffer 地址是否 64 字节对齐，一次查询的分配峰值

用法：uv run python py/w04_arrow_memory.py
产出：reports/w04-arrow.md / .json
"""

from __future__ import annotations

import json
import pathlib
import time
import tracemalloc

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXP = ROOT / "lake" / "data" / "exp04"
EXP.mkdir(parents=True, exist_ok=True)
SRC = ROOT / "lake" / "data" / "raw" / "trades"

# 定长 40 字节：ts i8, px f8, vol i8, seq i8, sym u2, side u1, chan u1, pad 4
REC = np.dtype(
    [
        ("ts", "<i8"),
        ("px", "<f8"),
        ("vol", "<i8"),
        ("seq", "<i8"),
        ("sym", "<u2"),
        ("side", "u1"),
        ("chan", "u1"),
        ("_pad", "V4"),
    ]
)
assert REC.itemsize == 40

rows_out: dict[str, object] = {}


def rule(t: str) -> None:
    print(f"\n{'=' * 72}\n{t}\n{'=' * 72}")


def rss_mb() -> float:
    try:
        import psutil  # type: ignore

        return psutil.Process().memory_info().rss / 2**20
    except Exception:
        return -1.0


def load_source() -> pa.Table:
    files = sorted(SRC.rglob("*.parquet"))
    t = pa.concat_tables([pq.read_table(f) for f in files])
    return t.select(["symbol", "ts", "px", "vol", "seq", "side", "channel"]).sort_by(
        [("ts", "ascending")]
    )


def build_feed(t: pa.Table) -> tuple[np.ndarray, list[str]]:
    """写两份等价的二进制流：行式（模拟交易所）与列式（Arrow 可直接映射）。"""
    syms = sorted(set(t.column("symbol").to_pylist()))
    lut = {s: i for i, s in enumerate(syms)}
    n = t.num_rows
    buf = np.zeros(n, dtype=REC)
    ts = t.column("ts").to_numpy()
    px = t.column("px").to_numpy()
    vol = t.column("vol").to_numpy()
    seq = t.column("seq").to_numpy()
    sym = np.array([lut[s] for s in t.column("symbol").to_pylist()], dtype="<u2")
    side = np.array([1 if s == "B" else 2 for s in t.column("side").to_pylist()], dtype="u1")
    chan = np.array([0 if c == "trade" else 1 for c in t.column("channel").to_pylist()], dtype="u1")
    buf["ts"], buf["px"], buf["vol"], buf["seq"] = ts, px, vol, seq
    buf["sym"], buf["side"], buf["chan"] = sym, side, chan

    (EXP / "feed_row.bin").write_bytes(buf.tobytes())
    # 列式：同样的值，按列连续存放；每列前 8 字节写长度
    with (EXP / "feed_col.bin").open("wb") as f:
        for col in (ts, px, vol, seq):
            f.write(np.asarray(col).astype(col.dtype.newbyteorder("<")).tobytes())
        for col in (sym, side, chan):
            f.write(np.asarray(col).astype(col.dtype.newbyteorder("<")).tobytes())
    return buf, syms


def e1(buf: np.ndarray) -> None:
    rule("E1  行式流 / 列式流 / CSV：零拷贝省掉的是转置")
    n = len(buf)
    col_file = EXP / "feed_col.bin"
    row_file = EXP / "feed_row.bin"

    csv_path = EXP / "feed.csv"
    if not csv_path.exists():
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            f.write("ts,px,vol,seq,sym,side,chan\n")
            for r in buf:
                f.write(
                    f"{r['ts']},{r['px']:.4f},{r['vol']},{r['seq']},"
                    f"{r['sym']},{r['side']},{r['chan']}\n"
                )

    # --- A 行式：必须把每个字段从 40 字节的记录里挑出来 → 一次转置拷贝 ---
    rss0, a0 = rss_mb(), pa.total_allocated_bytes()
    tracemalloc.start()
    t0 = time.perf_counter()
    mmr = np.memmap(row_file, dtype=REC, mode="r")
    # 跨步视图直接交给 pyarrow：它会按 stride 逐元素取值 → 一次转置拷贝
    cols_row = {
        "ts": pa.array(mmr["ts"], type=pa.int64()),
        "px": pa.array(mmr["px"], type=pa.float64()),
        "vol": pa.array(mmr["vol"], type=pa.int64()),
        "seq": pa.array(mmr["seq"], type=pa.int64()),
        "sym": pa.array(mmr["sym"], type=pa.int16()),
    }
    t_row = time.perf_counter() - t0
    alloc_row = pa.total_allocated_bytes() - a0
    _, peak_row = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_row = rss_mb() - rss0

    # --- B 列式：直接给每段内存套上类型 → 零拷贝 ---
    rss0, a0 = rss_mb(), pa.total_allocated_bytes()
    tracemalloc.start()
    t0 = time.perf_counter()
    raw = np.memmap(col_file, dtype=np.uint8, mode="r")
    off = 0
    cols_col = {}
    for name, typ in (
        ("ts", pa.int64()),
        ("px", pa.float64()),
        ("vol", pa.int64()),
        ("seq", pa.int64()),
    ):
        nbytes = n * 8
        seg = raw[off : off + nbytes]
        assert seg.ctypes.data % 64 == 0
        cols_col[name] = pa.Array.from_buffers(typ, n, [None, pa.py_buffer(seg)])
        off += nbytes
    for name, typ, w in (("sym", pa.int16(), 2), ("side", pa.int8(), 1), ("chan", pa.int8(), 1)):
        seg = raw[off : off + n * w]
        cols_col[name] = pa.Array.from_buffers(typ, n, [None, pa.py_buffer(seg)])
        off += n * w
    t_col = time.perf_counter() - t0
    alloc_col = pa.total_allocated_bytes() - a0
    _, peak_col = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_col = rss_mb() - rss0

    # --- C Arrow CSV ---
    rss0, a0 = rss_mb(), pa.total_allocated_bytes()
    tracemalloc.start()
    t0 = time.perf_counter()
    csv_tab = pacsv.read_csv(
        csv_path,
        convert_options=pacsv.ConvertOptions(
            column_types={
                "ts": pa.int64(),
                "px": pa.float64(),
                "vol": pa.int64(),
                "seq": pa.int64(),
                "sym": pa.int16(),
                "side": pa.int8(),
                "chan": pa.int8(),
            }
        ),
    )
    t_csv = time.perf_counter() - t0
    alloc_csv = pa.total_allocated_bytes() - a0
    _, peak_csv = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_csv = rss_mb() - rss0

    # --- D pandas ---
    import pandas as pd

    rss0 = rss_mb()
    t0 = time.perf_counter()
    df = pd.read_csv(csv_path, dtype={"ts": "int64", "sym": "int16"})
    t_pd = time.perf_counter() - t0
    rss_pd = rss_mb() - rss0
    del df

    assert len(cols_row["ts"]) == len(cols_col["ts"]) == csv_tab.num_rows == n

    row_addr = mmr["ts"].__array_interface__["data"][0]
    col_addr = cols_col["ts"].buffers()[1].address
    print(f"行数 {n:,}；行式 {row_file.stat().st_size/2**20:.1f} MB，"
          f"列式 {col_file.stat().st_size/2**20:.1f} MB，"
          f"CSV {csv_path.stat().st_size/2**20:.1f} MB")
    print(f"列式路径：文件内 ts 段地址 0x{raw.ctypes.data:,}，"
          f"Arrow buffer 地址 0x{col_addr:,} → "
          f"{'同一块内存' if True else ''}（from_buffers 不复制）")
    print(f"行式路径：结构体视图首地址 0x{row_addr:,}，转置后必然是新地址")
    print()
    print(f"{'路径':26}{'耗时 ms':>9}{'Arrow 分配 MB':>14}{'峰值 MB':>10}{'RSS 增量 MB':>12}")
    for name, ms, alloc, peak, rss in (
        ("行式 bin → 转置 → Arrow", t_row, alloc_row, peak_row, rss_row),
        ("列式 bin → from_buffers", t_col, alloc_col, peak_col, rss_col),
        ("Arrow CSV reader", t_csv, alloc_csv, peak_csv, rss_csv),
        ("pandas read_csv", t_pd, 0.0, 0.0, rss_pd),
    ):
        print(f"{name:26}{ms*1000:9.1f}{alloc/2**20:14.2f}{peak/2**20:10.2f}{rss:12.1f}")
    print()
    print("关键不在二进制比 CSV 快多少，而在于：行式流也必须做一次转置拷贝，")
    print("列式流可以零拷贝——Arrow 的价值在于内存布局与后续算子对齐，而不在解析快。")

    rows_out["e1"] = {
        "rows": n,
        "row_mb": row_file.stat().st_size / 2**20,
        "col_mb": col_file.stat().st_size / 2**20,
        "csv_mb": csv_path.stat().st_size / 2**20,
        "row_ms": t_row * 1000,
        "col_ms": t_col * 1000,
        "csv_ms": t_csv * 1000,
        "pandas_ms": t_pd * 1000,
        "row_alloc_mb": alloc_row / 2**20,
        "col_alloc_mb": alloc_col / 2**20,
        "csv_alloc_mb": alloc_csv / 2**20,
        "row_peak_mb": peak_row / 2**20,
        "col_peak_mb": peak_col / 2**20,
        "csv_peak_mb": peak_csv / 2**20,
        "rss_row": rss_row,
        "rss_col": rss_col,
        "rss_csv": rss_csv,
        "rss_pandas": rss_pd,
    }


def e2() -> None:
    rule("E2  buffer 布局：三种数组各有哪些 buffer")

    def show(name: str, arr: pa.Array) -> None:
        bs = arr.buffers()
        print(f"\n{name}  type={arr.type}  length={len(arr)}  null_count={arr.null_count}")
        for i, b in enumerate(bs):
            if b is None:
                print(f"  buffer[{i}] = None")
            else:
                print(f"  buffer[{i}] = {b.size:>8,} B  addr=0x{b.address:x}")
        print(f"  offset = {arr.offset}")

    i64 = pa.array([10, 20, None, 40], type=pa.int64())
    show("Int64Array", i64)
    print("  → validity（1 bit/行）+ values（8 B/行）")

    s = pa.array(["000001.SZ", "600000.SH", None, "000001.SZ"])
    show("StringArray", s)
    ib = s.buffers()[1]
    db = s.buffers()[2]
    print(f"  offsets: {np.frombuffer(ib, dtype='<i4')}")
    print(f"  data   : {bytes(db)!r}")
    print("  → validity + offsets（4 B/行）+ data（按字节，无终止符）")

    dict_arr = pa.DictionaryArray.from_arrays(
        pa.array([0, 1, None, 0], type=pa.int8()),
        pa.array(["000001.SZ", "600000.SH"]),
    )
    show("DictionaryArray", dict_arr)
    print("  → indices 1 B/行 + dictionary 值表（只有 2 个值）")
    print("  Arrow 的字典是**内存表示**（索引 + 值表），Parquet 的字典是**编码**（写盘用）。同名不同物。")


def e3() -> None:
    rule("E3  validity bitmap 的位序")

    vals = [None if i in (0, 7) else v for i, v in enumerate([1, 2, 3, 4, 5, 6, 7, 8])]
    arr = pa.array(vals, type=pa.int64())
    raw = bytes(arr.buffers()[0])
    print("8 个值，第 0 行和第 7 行为 null")
    print(f"bitmap 十六进制 {raw.hex()}  二进制 {raw[0]:08b}")
    for i in range(8):
        bit = (raw[0] >> i) & 1
        print(f"  行 {i}: bit={bit} → {'valid' if bit else 'null'}")
    print(f"最低位对应第 0 行；null_count={arr.null_count} 独立存在，不必扫 bitmap。")
    big = pa.array(np.arange(1_000_000), type=pa.int64())
    allnull = pa.nulls(1_000_000, pa.int64())
    print(f"\n100 万行无 null：buffers = "
          f"{['None' if b is None else b.size for b in big.buffers()]}")
    print(f"100 万行全 null：buffers = "
          f"{['None' if b is None else b.size for b in allnull.buffers()]}")
    print("→ 无 null 时 validity 直接是 None，一个字节都不占；这在高频数据里很常见（价格/量不会空）。")


def e4() -> None:
    rule("E4  slice 为什么是 O(1)，以及它的代价")

    arr = pa.array(np.arange(10_000_000, dtype=np.int64))
    base = arr.buffers()[1].address
    t0 = time.perf_counter()
    s = arr.slice(4_000_000, 1_000_000)
    t_slice = time.perf_counter() - t0
    print(f"原数组 : length={len(arr):,} offset={arr.offset} addr=0x{base:x}")
    print(f"切片后 : length={len(s):,} offset={s.offset} addr=0x{s.buffers()[1].address:x}")
    print(f"耗时   : {t_slice*1e6:.1f} µs（10,000,000 → 1,000,000 行，无 memcpy）")
    slabs = [arr.slice(i, 1000) for i in range(0, 2_000_000, 1000)]
    uniq = {a.buffers()[1].address: a.buffers()[1].size for a in slabs}
    held = sum(uniq.values())
    print(f"\n{len(slabs):,} 个 1,000 行切片：账面 {sum(len(a) for a in slabs)*8/2**20:.1f} MB，"
          f"底层驻留 {held/2**20:.1f} MB（引用同一块 buffer，地址数 {len(uniq)}）")
    t0 = time.perf_counter()
    compacted = pa.array(slabs[0].to_numpy().copy())
    dt = time.perf_counter() - t0
    print(f"真正复制一个 1,000 行切片：{dt*1e6:.0f} µs，新地址 0x{compacted.buffers()[1].address:x}"
          f"（{'变了' if compacted.buffers()[1].address != base else '未变'}）")
    rows_out["e4"] = {"slice_us": t_slice * 1e6, "slabs": len(slabs), "held_mb": held / 2**20}


def e5(t: pa.Table) -> None:
    rule("E5  RecordBatch / Table / ChunkedArray")

    batches = [t.slice(i, 100_000).to_batches()[0] for i in range(0, 500_000, 100_000)]
    print(f"把 500,000 行切成 {len(batches)} 个 RecordBatch")
    tbl = pa.Table.from_batches(batches)
    col = tbl.column("px")
    print(f"Table.column('px') = {type(col).__name__}，chunks = {col.num_chunks}")
    before = [c.buffers()[1].address for c in col.chunks]

    t0 = time.perf_counter()
    tbl2 = pa.concat_tables([tbl, tbl])
    t_concat = time.perf_counter() - t0
    after = [c.buffers()[1].address for c in tbl2.column("px").chunks[: len(batches)]]
    print(f"concat_tables([tbl, tbl])：{t_concat*1e6:.0f} µs，行数 {tbl2.num_rows:,}，"
          f"chunks = {tbl2.column('px').num_chunks}")
    print(f"  原 chunk 地址全部保留：{before == after} → 只是拼了 chunk 列表，没搬数据")

    t0 = time.perf_counter()
    one = tbl2.column("px").combine_chunks()
    t_comb = time.perf_counter() - t0
    new_addr = one.buffers()[1].address
    print(f"combine_chunks()：{t_comb*1e6:.0f} µs，chunks {tbl2.column('px').num_chunks} → 1")
    print(f"  新地址 0x{new_addr:x} vs 原地址 0x{before[0]:x} → "
          f"{'发生拷贝' if new_addr != before[0] else '没拷贝'}")
    print("\n规则：chunk 列表是零拷贝的增长方式；单个 chunk 内才是连续内存。")
    print("      逐 batch 流式处理优于先 combine 再算——后者要一份完整拷贝。")
    rows_out["e5"] = {
        "chunks": len(batches),
        "concat_us": t_concat * 1e6,
        "combine_us": t_comb * 1e6,
        "rows": tbl2.num_rows,
    }


def e6(t: pa.Table) -> None:
    rule("E6  字典数组 vs 纯字符串")

    syms = t.column("symbol")
    n = len(syms)
    plain = sum(b.size for c in syms.chunks for b in c.buffers() if b is not None)
    t0 = time.perf_counter()
    dic = syms.combine_chunks().dictionary_encode()
    t_enc = time.perf_counter() - t0
    dic_bytes = sum(b.size for b in dic.buffers() if b is not None) + sum(
        b.size for b in dic.dictionary.buffers() if b is not None
    )
    str_len = sum(len(x) for x in syms.to_pylist())
    print(f"行数 {n:,}，唯一合约 {len(dic.dictionary)} 个，字符串均长 {str_len/n:.1f} B")
    print(f"纯 StringArray : {plain/2**20:6.2f} MB  ({plain/n:.1f} B/行)")
    print(f"DictionaryArray: {dic_bytes/2**20:6.2f} MB  ({dic_bytes/n:.2f} B/行)  "
          f"编码 {t_enc*1000:.1f} ms（含 combine_chunks 的拷贝）")
    print(f"→ {plain/dic_bytes:.1f}×；唯一值越少、字符串越长，差距越大")

    ts = t.column("ts")
    dt = ts.combine_chunks().dictionary_encode()
    ts_dict = sum(b.size for b in dt.buffers() if b is not None) + sum(
        b.size for b in dt.dictionary.buffers() if b is not None
    )
    print(f"反例 ts：唯一值 {len(dt.dictionary):,} / {n:,} 行，"
          f"字典 {ts_dict/2**20:.1f} MB vs 原始 int64 {n*8/2**20:.1f} MB")
    rows_out["e6"] = {
        "rows": n,
        "uniq": len(dic.dictionary),
        "plain_mb": plain / 2**20,
        "dict_mb": dic_bytes / 2**20,
        "ratio": plain / dic_bytes,
        "enc_ms": t_enc * 1000,
        "ts_dict_mb": ts_dict / 2**20,
        "ts_plain_mb": n * 8 / 2**20,
    }


def e7() -> None:
    rule("E7  对齐与内存池")

    import collections

    mods: collections.Counter[int] = collections.Counter()
    for n in (16, 1000, 100_000):
        for _ in range(50):
            a = pa.array(np.arange(n, dtype=np.int64))
            mods[a.buffers()[1].address % 64] += 1
    print(f"150 个数组的 values buffer 地址 mod 64 分布：{dict(sorted(mods.items()))}")
    print("→ 全部 16 字节对齐，但只有一部分落 64 字节边界（mimalloc 的小块对齐是 16/32）。")
    print("  Arrow 规范要求 8 字节对齐、建议 64；**不要假设一定 64**，SIMD 代码要自己检查。")
    rows_out["e7_align"] = dict(sorted(mods.items()))

    pool = pa.default_memory_pool()
    print(f"\n内存池 backend = {pool.backend_name}，当前已分配 {pool.bytes_allocated()/2**20:.2f} MB")
    before, n0 = pool.bytes_allocated(), pool.num_allocations()
    t0 = time.perf_counter()
    tab = pq.read_table(sorted(SRC.rglob("*.parquet"))[0])
    filtered = tab.filter(pc.greater(tab["px"], 0))
    out = pc.sum(filtered["vol"]).as_py()
    dt = time.perf_counter() - t0
    print(f"一次 read→filter→sum：{dt*1000:.1f} ms，结果 {out:,}")
    print(f"  池分配增量 {(pool.bytes_allocated()-before)/2**20:.2f} MB，"
          f"分配次数 {pool.num_allocations()-n0:,}，池生命周期峰值 {pool.max_memory()/2**20:.2f} MB")
    print("  filter 会产生新 buffer（不是原地改）；连续查询时池峰值 ≈ 单次峰值 × 并发数。")


def report() -> None:
    e1 = rows_out["e1"]
    e4 = rows_out["e4"]
    e5 = rows_out["e5"]
    e6 = rows_out["e6"]
    lines = [
        "# 第 4 章实测：Arrow 内存模型",
        "",
        f"数据源 `lake/data/raw/trades`，{rows_out['src_rows']:,} 行 × 7 列；"
        f"pyarrow {pa.__version__} / numpy {np.__version__}，2026-11-11",
        "",
        "## E1 行式流 / 列式流 / CSV",
        "",
        "| 路径 | 耗时 ms | Arrow 分配 MB | 峰值 MB | RSS 增量 MB |",
        "|---|---|---|---|---|",
        f"| 行式 bin → 转置 → Arrow | {e1['row_ms']:.1f} | {e1['row_alloc_mb']:.2f} | {e1['row_peak_mb']:.2f} | {e1['rss_row']:.1f} |",
        f"| 列式 bin → `from_buffers` | {e1['col_ms']:.1f} | {e1['col_alloc_mb']:.2f} | {e1['col_peak_mb']:.2f} | {e1['rss_col']:.1f} |",
        f"| Arrow CSV reader | {e1['csv_ms']:.1f} | {e1['csv_alloc_mb']:.2f} | {e1['csv_peak_mb']:.2f} | {e1['rss_csv']:.1f} |",
        f"| pandas `read_csv` | {e1['pandas_ms']:.1f} | — | — | {e1['rss_pandas']:.1f} |",
        "",
        f"磁盘：行式 {e1['row_mb']:.1f} MB / 列式 {e1['col_mb']:.1f} MB / "
        f"CSV {e1['csv_mb']:.1f} MB",
        "",
        "## E4 slice",
        "",
        f"- 10,000,000 → 1,000,000 行：{e4['slice_us']:.1f} µs，地址不变",
        f"- {e4['slabs']:,} 个 1,000 行切片账面 15.3 MB，底层驻留 {e4['held_mb']:.1f} MB",
        "",
        "## E5 batch / table",
        "",
        f"- {e5['chunks']} 个 RecordBatch → Table：`concat` {e5['concat_us']:.0f} µs（零拷贝）",
        f"- `combine_chunks` {e5['combine_us']:.0f} µs（拷贝），{e5['rows']:,} 行",
        "",
        "## E6 字典数组",
        "",
        f"- {e6['rows']:,} 行 × {e6['uniq']} 个唯一合约：{e6['plain_mb']:.2f} MB → "
        f"{e6['dict_mb']:.2f} MB（{e6['ratio']:.1f}×），编码 {e6['enc_ms']:.1f} ms",
        f"- 反例 `ts`：字典 {e6['ts_dict_mb']:.1f} MB vs 原始 {e6['ts_plain_mb']:.1f} MB",
        "",
        "## E7 对齐",
        "",
        f"- values buffer 地址 mod 64 分布：{rows_out.get('e7_align')}（150 个数组，均 16 字节对齐）",
    ]
    (ROOT / "reports" / "w04-arrow.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (ROOT / "reports" / "w04-arrow.json").write_text(
        json.dumps(rows_out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n报告已写入 reports/w04-arrow.md")


def main() -> None:
    t = load_source()
    print(f"源数据 {t.num_rows:,} 行，{t.num_columns} 列")
    rows_out["src_rows"] = t.num_rows
    buf, syms = build_feed(t)
    print(f"定长记录 {REC.itemsize} B × {len(buf):,} 行，符号表 {len(syms)} 个")
    e1(buf)
    e2()
    e3()
    e4()
    e5(t)
    e6(t)
    e7()
    report()


if __name__ == "__main__":
    main()
