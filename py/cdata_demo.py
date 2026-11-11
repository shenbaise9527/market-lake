"""Arrow C Data Interface：证明"零拷贝"不是营销词。

做法：造一个 2000 万行的 int64 数组，在 pyarrow / polars / duckdb 之间传递，
然后直接比较底层 buffer 的内存地址。地址相同 = 没有复制。

用法：python py/cdata_demo.py
"""

from __future__ import annotations

import time

import duckdb
import numpy as np
import polars as pl
import pyarrow as pa


def rule(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def buf_addr(arr: pa.Array) -> int:
    """Arrow 数组 values buffer 的内存地址（第 0 个是 validity bitmap）。"""
    buffers = arr.buffers()
    return buffers[1].address if len(buffers) > 1 and buffers[1] is not None else 0


def main() -> None:
    n = 20_000_000
    np_arr = np.arange(n, dtype=np.int64)
    t0 = time.perf_counter()
    pa_arr = pa.array(np_arr, type=pa.int64())
    print(f"numpy → arrow 构造耗时 {time.perf_counter() - t0:.4f}s（这一步是零拷贝的）")

    rule("1. pyarrow → polars")
    tb = pa.table({"v": pa_arr})
    origin = buf_addr(tb.column("v").chunk(0))
    df = pl.from_arrow(tb)
    back = df.to_arrow()
    after = buf_addr(back.column("v").chunk(0))
    print(f"pyarrow buffer 地址: {origin}")
    print(f"polars 归还后地址  : {after}")
    print("结论：", "同一块内存，零拷贝" if origin == after else "被复制了")

    rule("2. pyarrow → duckdb")
    t0 = time.perf_counter()
    rel = duckdb.arrow(tb)
    s = rel.aggregate("sum(v)").fetchall()[0][0]
    print(f"duckdb 聚合 sum={s}，耗时 {time.perf_counter() - t0:.4f}s")
    print("注意：这是直接在 Arrow buffer 上算，没有把数据搬进 DuckDB 的存储。")

    rule("3. 对比：真的复制一次要多久")
    t0 = time.perf_counter()
    pa_arr_copy = pa_arr.slice(0, n)  # slice 也是零拷贝
    print(f"slice 2000 万行: {time.perf_counter() - t0:.6f}s（offset 变化而已）")
    t0 = time.perf_counter()
    _ = pa_arr.to_numpy(zero_copy_only=False).copy()
    print(f"真正 memcpy 2000 万行: {time.perf_counter() - t0:.4f}s")

    rule("4. 教训")
    print("零拷贝的代价是生命周期耦合：谁持有 buffer，谁负责 alive。")
    print("跨语言传递时用 Arrow C Data Interface（pyarrow 侧 import/export），")
    print("进程间传递用 Arrow IPC / Flight，而不是 JSON 或 CSV。")


if __name__ == "__main__":
    main()
