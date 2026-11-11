"""合成行情数据生成器。

刻意注入了真实 tick 数据里一定会遇到的四类脏数据：
  1. 乱序（相邻行交换）
  2. 重复（同 ts 同 seq 的重复回报）
  3. 缺口（某段时间整段丢失）
  4. 异常值（价格跳变 10 倍）

用法：
    python py/gen_tick.py --days 3 --rows 1000000 --symbols 8 --out lake/data/raw
"""

from __future__ import annotations

import argparse
import pathlib

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

TRADE_SCHEMA = pa.schema(
    [
        ("symbol", pa.string()),
        ("ts", pa.int64()),  # epoch nanoseconds, UTC
        ("seq", pa.int64()),
        ("px", pa.float64()),
        ("vol", pa.int64()),
        ("side", pa.int8()),  # 1 主动买 / -1 主动卖 / 0 未知
        ("channel", pa.int16()),
    ]
)

LEVELS = 5


def make_symbols(n: int, rng: np.random.Generator) -> list[tuple[str, str]]:
    out = []
    for i in range(n):
        ex = "SH" if i % 2 == 0 else "SZ"
        code = f"{rng.integers(600000, 603000) if ex == 'SH' else rng.integers(1, 3000):06d}"
        out.append((ex, f"{code}.{ex}"))
    return out


def session_ns(day: str) -> np.ndarray:
    """上午 09:30-11:30、下午 13:00-15:00 的秒级时间戳（epoch ns）。"""
    morning = pd.Timestamp(f"{day} 09:30:00", tz="Asia/Shanghai")
    afternoon = pd.Timestamp(f"{day} 13:00:00", tz="Asia/Shanghai")
    sec = 1_000_000_000
    m = np.arange(0, 7200, dtype=np.int64) * sec + morning.value
    a = np.arange(0, 7200, dtype=np.int64) * sec + afternoon.value
    return np.concatenate([m, a])


def gen_symbol_day(
    symbol: str, day: str, rows: int, rng: np.random.Generator
) -> dict[str, np.ndarray]:
    base = float(rng.uniform(5, 80))
    secs = session_ns(day)

    # 事件时刻：在交易秒上做泊松展开，得到严格递增的纳秒时间戳
    pick = np.sort(rng.integers(0, secs.size, size=rows))
    frac = rng.integers(0, 1_000_000_000, size=rows, dtype=np.int64)
    ts = secs[pick] + frac
    ts.sort()

    # 价格：带漂移的随机游走，落到最小变动价位
    sigma = 0.0008
    step = rng.normal(0, sigma, size=rows).cumsum()
    px = base * np.exp(step)
    tick = 0.01
    px = np.round(px / tick) * tick
    px = np.maximum(px, tick)

    vol = (rng.lognormal(mean=5.5, sigma=1.0, size=rows)).astype(np.int64) // 100 * 100 + 100
    side = rng.choice(np.array([1, -1, 0], dtype=np.int8), size=rows, p=[0.48, 0.48, 0.04])
    seq = np.arange(rows, dtype=np.int64)

    # 1) 乱序：1% 的相邻行交换
    swap = rng.choice(rows - 1, size=max(1, rows // 100), replace=False)
    for i in swap:
        ts[i], ts[i + 1] = ts[i + 1], ts[i]
        px[i], px[i + 1] = px[i + 1], px[i]
        vol[i], vol[i + 1] = vol[i + 1], vol[i]
        seq[i], seq[i + 1] = seq[i + 1], seq[i]

    # 2) 重复：0.5% 的回报重复一次
    dup = rng.choice(rows, size=max(1, rows // 200), replace=False)
    ts = np.concatenate([ts, ts[dup]])
    px = np.concatenate([px, px[dup]])
    vol = np.concatenate([vol, vol[dup]])
    side = np.concatenate([side, side[dup]])
    seq = np.concatenate([seq, seq[dup]])

    # 3) 缺口：中间整段丢掉 2000 行
    if ts.size > 5000:
        cut = ts.size // 2
        keep = np.r_[0:cut, cut + 2000 : ts.size]
        ts, px, vol, side, seq = ts[keep], px[keep], vol[keep], side[keep], seq[keep]

    # 4) 异常值：0.01% 的价格跳 10 倍
    bad = rng.choice(ts.size, size=max(1, ts.size // 10000), replace=False)
    px[bad] *= 10

    n = ts.size
    return {
        "symbol": np.full(n, symbol, dtype=object),
        "ts": ts,
        "seq": seq,
        "px": px,
        "vol": vol,
        "side": side,
        "channel": rng.integers(0, 4, size=n, dtype=np.int16),
    }


def gen_book(cols: dict[str, np.ndarray], rng: np.random.Generator) -> dict[str, np.ndarray]:
    """由成交价衍生五档盘口（扁平化列，便于和 LIST<STRUCT> 做对比实验）。"""
    px = cols["px"]
    n = px.size
    out: dict[str, np.ndarray] = {
        "symbol": cols["symbol"],
        "ts": cols["ts"],
    }
    for lv in range(1, LEVELS + 1):
        out[f"bid_px{lv}"] = np.round((px - 0.01 * lv) / 0.01) * 0.01
        out[f"ask_px{lv}"] = np.round((px + 0.01 * lv) / 0.01) * 0.01
        out[f"bid_vol{lv}"] = (rng.lognormal(6, 0.8, n)).astype(np.int64) // 100 * 100
        out[f"ask_vol{lv}"] = (rng.lognormal(6, 0.8, n)).astype(np.int64) // 100 * 100
    return out


def write_table(cols: dict[str, np.ndarray], schema: pa.Schema, path: pathlib.Path, compression: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table(cols, schema=schema)
    pq.write_table(table, path, compression=compression, row_group_size=131072)


BOOK_SCHEMA = pa.schema(
    [("symbol", pa.string()), ("ts", pa.int64())]
    + [
        (f"bid_px{lv}", pa.float64()) for lv in range(1, LEVELS + 1)
    ]
    + [(f"ask_px{lv}", pa.float64()) for lv in range(1, LEVELS + 1)]
    + [(f"bid_vol{lv}", pa.int64()) for lv in range(1, LEVELS + 1)]
    + [(f"ask_vol{lv}", pa.int64()) for lv in range(1, LEVELS + 1)]
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--rows", type=int, default=400_000, help="每天总行数（所有合约合计）")
    ap.add_argument("--symbols", type=int, default=8)
    ap.add_argument("--out", default="lake/data/raw")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--with-book", action="store_true")
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    root = pathlib.Path(args.out)
    symbols = make_symbols(args.symbols, rng)
    per = max(1000, args.rows // len(symbols))
    end = pd.Timestamp("2026-11-05")
    days = [(end - pd.Timedelta(days=i)).strftime("%Y-%m-%d") for i in range(args.days)][::-1]

    for day in days:
        for exch, sym in symbols:
            cols = gen_symbol_day(sym, day, per, rng)
            p = root / "trades" / f"dt={day}" / f"exchange={exch}" / f"part-{sym}.parquet"
            write_table(cols, TRADE_SCHEMA, p, "zstd")
            if args.with_book:
                bk = gen_book(cols, rng)
                pb = root / "book" / f"dt={day}" / f"exchange={exch}" / f"part-{sym}.parquet"
                write_table(bk, BOOK_SCHEMA, pb, "zstd")
        print(f"{day} 完成：{len(symbols)} 个合约 × {per} 行")

    n_files = len(list(root.rglob("*.parquet")))
    size = sum(p.stat().st_size for p in root.rglob("*.parquet")) / 1e6
    print(f"共 {n_files} 个文件，{size:.1f} MB → {root}")


if __name__ == "__main__":
    main()
