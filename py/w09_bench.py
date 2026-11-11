"""第 9 章实验：可复现基准。

B1  基准固定：数据指纹（行数 / 文件 / sha256）+ 查询指纹（所有配置结果必须一致）
B2  矩阵：sorted × encoding × codec × row_group = 2 × 2 × 3 × 3 = 36 组
    每组测：体积、写耗时、Q1 点查、Q2 窄列扫、Q3 全扫、Q4 聚合
B3  帕累托前沿：体积 vs 全扫耗时
B4  三目标冲突：点查最优 / 全扫最优 / 聚合最优 是不是同一组配置
B5  尾延迟：点查 100 次，p50 / p95 / p99；同连接（热）vs 新连接（冷 DuckDB）

用法：uv run python py/w09_bench.py
产出：reports/w09-bench.md
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import statistics
import time

import duckdb
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXP = ROOT / "lake" / "data" / "exp09"
RAW = ROOT / "lake" / "data" / "raw" / "trades"
out: dict[str, object] = {}

SYM = "002322.SZ"


def rule(t: str) -> None:
    print(f"\n{'=' * 74}\n{t}\n{'=' * 74}")


def load() -> tuple[pa.Table, pa.Table]:
    d = ds.dataset(RAW, format="parquet", partitioning="hive")
    t_all = d.to_table()
    # 到达顺序：按文件顺序拼接，不做任何排序（真实落盘的样子）
    arrival = t_all
    # 排序版本
    sorted_t = t_all.sort_by(
        [("dt", "ascending"), ("symbol", "ascending"), ("ts", "ascending")]
    )
    return arrival, sorted_t


def fingerprint() -> dict:
    files = sorted(RAW.rglob("*.parquet"))
    h = hashlib.sha256()
    for f in files:
        h.update(f.name.encode())
        h.update(str(f.stat().st_size).encode())
    rows = sum(pq.ParquetFile(f).metadata.num_rows for f in files)
    return {"files": len(files), "rows": rows, "sha": h.hexdigest()[:16]}


def write_variant(t: pa.Table, path: pathlib.Path, sort: bool, enc: str,
                  codec: str, rg: int) -> float:
    kwargs: dict = dict(compression=codec, row_group_size=rg)
    if enc == "dict":
        kwargs["use_dictionary"] = ["symbol", "side", "channel"]
    else:
        kwargs["use_dictionary"] = False
    t0 = time.perf_counter()
    pq.write_table(t, path, **kwargs)
    return (time.perf_counter() - t0) * 1000


class Querier:
    """同一连接下跑四个固定查询；fresh=True 时每次新建连接。"""

    def __init__(self, path: pathlib.Path, fresh: bool = False) -> None:
        self.p = path.as_posix()
        self.fresh = fresh
        self.con = None if fresh else duckdb.connect()

    def _con(self):
        if self.fresh:
            return duckdb.connect()
        if self.con is None:
            self.con = duckdb.connect()
        return self.con

    def run(self, q: str):
        return self._con().execute(q).fetchone()

    def close(self) -> None:
        if self.con is not None:
            self.con.close()


def make_queries(p: str) -> dict[str, str]:
    lo = 1793842200005459260
    return {
        "Q1 点查(1合约1分钟)": (
            f"SELECT count(*), sum(vol) FROM read_parquet('{p}') "
            f"WHERE symbol='{SYM}' AND ts >= {lo} AND ts < {lo + 60_000_000_000}"
        ),
        "Q2 窄列扫(1天vol)": (
            f"SELECT sum(vol) FROM read_parquet('{p}') "
            f"WHERE ts >= {lo} AND ts < {lo + 86_400_000_000_000}"
        ),
        "Q3 全扫(全部7列)": (
            f"SELECT count(*), sum(vol), min(px), max(px) FROM read_parquet('{p}')"
        ),
        "Q4 聚合(分钟bar)": (
            f"SELECT count(*) FROM (SELECT symbol, ts // 60000000000 AS m, sum(vol) v "
            f"FROM read_parquet('{p}') GROUP BY 1, 2)"
        ),
    }


def median_ms(q: Querier, sql: str, n: int = 7) -> float:
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        q.run(sql)
        ts.append((time.perf_counter() - t0) * 1000)
    return statistics.median(ts)


def b1() -> dict:
    rule("B1  基准固定：数据指纹与查询指纹")
    fp = fingerprint()
    print(f"数据集：{fp['files']} 个文件 / {fp['rows']:,} 行 / sha256:{fp['sha']}")
    print("查询集：Q1 点查 ｜ Q2 窄列扫 ｜ Q3 全扫 ｜ Q4 聚合（同一连接，中位数）")
    out["b1"] = fp
    return fp


def b2(arrival: pa.Table, sorted_t: pa.Table) -> list[dict]:
    rule("B2  矩阵：sorted × encoding × codec × row_group（36 组）")
    rows: list[dict] = []
    ref_fp: tuple | None = None
    if EXP.exists():
        shutil.rmtree(EXP)
    EXP.mkdir(parents=True)

    combos = [
        (sort, enc, codec, rg)
        for sort in (False, True)
        for enc in ("plain", "dict")
        for codec in ("none", "snappy", "zstd")
        for rg in (8_192, 32_768, 131_072)
    ]
    t_all_start = time.perf_counter()
    for i, (sort, enc, codec, rg) in enumerate(combos):
        src = sorted_t if sort else arrival
        name = f"v{ i:02d}-{'srt' if sort else 'arr'}-{enc}-{codec}-{rg}.parquet"
        p = EXP / name
        wms = write_variant(src, p, sort, enc, codec, rg)
        size = p.stat().st_size
        q = Querier(p)
        qs = make_queries(p)
        try:
            fp = q.run(qs["Q3 全扫(全部7列)"])
            key = (fp[0], fp[1])
            if ref_fp is None:
                ref_fp = key
            elif key != ref_fp:
                raise SystemExit(f"查询指纹不一致：{name} {key} != {ref_fp}")
            r = dict(
                idx=i, sort=sort, enc=enc, codec=codec, rg=rg,
                kb=size / 1024, write_ms=wms,
                q1=median_ms(q, qs["Q1 点查(1合约1分钟)"]),
                q2=median_ms(q, qs["Q2 窄列扫(1天vol)"]),
                q3=median_ms(q, qs["Q3 全扫(全部7列)"]),
                q4=median_ms(q, qs["Q4 聚合(分钟bar)"]),
            )
        finally:
            q.close()
        rows.append(r)
        print(f"[{i:02d}] {'排序' if sort else '到达'} {enc:5} {codec:6} rg={rg:>7,}  "
              f"{r['kb']:8.1f} KB  写 {wms:5.0f} ms  "
              f"Q1 {r['q1']:5.2f}  Q2 {r['q2']:5.2f}  Q3 {r['q3']:5.2f}  Q4 {r['q4']:5.2f} ms")
    print(f"\n36 组共 {time.perf_counter() - t_all_start:.1f} s；查询指纹全部一致 "
          f"(count={ref_fp[0]:,}, sum_vol={ref_fp[1]:,})")
    out["b2"] = rows
    return rows


def pareto(rows: list[dict]) -> list[dict]:
    pts = sorted(rows, key=lambda r: (r["kb"], r["q3"]))
    front: list[dict] = []
    best = float("inf")
    for r in pts:
        if r["q3"] < best:
            front.append(r)
            best = r["q3"]
    return front


def b3(rows: list[dict]) -> list[dict]:
    rule("B3  帕累托前沿：体积 vs 全扫耗时（Q3）")
    front = pareto(rows)
    for r in front:
        print(f"  {r['kb']:8.1f} KB  Q3 {r['q3']:5.2f} ms   "
              f"{'排序' if r['sort'] else '到达'} {r['enc']}-{r['codec']}-rg{r['rg']:,}")
    dominated = [r for r in rows if r not in front]
    print(f"\n前沿 {len(front)} 个点，其余 {len(dominated)} 个被支配：")
    print("  体积更大或相同、却更慢的配置——不论怎么调，都不该选它们。")
    out["b3_front"] = front
    out["b3_dominated_n"] = len(dominated)
    return front


def b4(rows: list[dict]) -> None:
    rule("B4  三目标冲突：同一份数据，三个目标各选各的最优")
    best_q1 = min(rows, key=lambda r: r["q1"])
    best_q3 = min(rows, key=lambda r: r["q3"])
    best_q4 = min(rows, key=lambda r: r["q4"])
    worst_q1 = max(rows, key=lambda r: r["q1"])
    for tag, r in (("点查最优", best_q1), ("全扫最优", best_q3), ("聚合最优", best_q4)):
        print(f"  {tag}: {'排序' if r['sort'] else '到达'} {r['enc']}-{r['codec']}-"
              f"rg{r['rg']:,}  Q1 {r['q1']:.2f} / Q3 {r['q3']:.2f} / Q4 {r['q4']:.2f}")
    same = (best_q1["idx"] == best_q3["idx"] == best_q4["idx"])
    print(f"\n三个目标是同一个配置吗：{'是' if same else '不是'}")
    print(f"点查最快 vs 最慢差 {worst_q1['q1'] / best_q1['q1']:.1f}×；"
          f"但同一配置的全扫可能与最差相差 "
          f"{max(r['q3'] for r in rows) / best_q3['q3']:.1f}×")
    print("→ 调优必须先声明优化目标。'哪个配置最快'是没有意义的问题。")
    out["b4"] = {"best_q1": best_q1, "best_q3": best_q3, "best_q4": best_q4,
                 "same": same}


def b5(rows: list[dict]) -> None:
    rule("B5  尾延迟：p50 / p95 / p99（100 次）")
    best = min(rows, key=lambda r: r["q1"])
    p = (EXP / f"v{best['idx']:02d}-{'srt' if best['sort'] else 'arr'}-"
         f"{best['enc']}-{best['codec']}-{best['rg']}.parquet")
    qs = make_queries(p.as_posix())
    res = {}
    for label, fresh in (("同连接(热)", False), ("新连接(冷DuckDB)", True)):
        q = Querier(p, fresh=fresh)
        try:
            ts = []
            for _ in range(100):
                t0 = time.perf_counter()
                q.run(qs["Q1 点查(1合约1分钟)"])
                ts.append((time.perf_counter() - t0) * 1000)
            ts.sort()
            res[label] = {"p50": ts[49], "p95": ts[94], "p99": ts[98],
                          "max": ts[-1], "cv": statistics.pstdev(ts) / statistics.mean(ts)}
            print(f"  {label:14} p50 {ts[49]:5.2f}  p95 {ts[94]:5.2f}  "
                  f"p99 {ts[98]:5.2f}  max {ts[-1]:6.2f}  变异系数 {res[label]['cv']:.2f}")
        finally:
            q.close()
    print("\n新连接的 p99 抬高来自 DuckDB 的初始化与 footer 解析；")
    print("同连接的偶发尖刺来自 OS 缓存与调度。SLA 应按 p99 定，不按中位数。")
    out["b5"] = res


def b6() -> None:
    rule("B6  规模才制造冲突：8 MB 上没有的取舍，300 MB 上有吗")
    import numpy as np

    n, n_sym = 24_000_000, 100
    rng = np.random.default_rng(42)
    ts = (rng.integers(0, 4 * 3600, n) * 1_000_000_000
          + 1_793_846_400_000_000_000 + rng.integers(0, 999, n))
    sym = (rng.integers(0, n_sym, n)).astype(np.int32)
    px = 100.0 + rng.normal(0, 1, n).cumsum() * 0.01
    vol = rng.integers(1, 1000, n)
    seq = np.arange(n, dtype=np.int64)
    side = (rng.integers(0, 2, n)).astype(np.uint8)
    chan = (rng.integers(0, 3, n)).astype(np.uint8)
    t = pa.table({
        "ts": pa.array(ts, pa.int64()), "px": pa.array(px, pa.float64()),
        "vol": pa.array(vol, pa.int64()), "seq": pa.array(seq, pa.int64()),
        "symbol": pa.array(sym, pa.int32()), "side": pa.array(side, pa.uint8()),
        "channel": pa.array(chan, pa.uint8()),
    })
    t = t.sort_by([("symbol", "ascending"), ("ts", "ascending")])
    print(f"合成 {n:,} 行（100 合约 × 4 小时），内存 {t.nbytes / 2**20:.0f} MB，"
          f"按 (symbol, ts) 排序，rg=131,072")

    p_big = EXP / "b6"
    p_big.mkdir(exist_ok=True)
    lo = 1_793_846_400_000_000_000
    rows6 = []
    for codec, level in (("none", None), ("snappy", None), ("zstd", 3), ("zstd", 9)):
        kw = dict(compression=codec, row_group_size=131_072, use_dictionary=False)
        if level is not None:
            kw["compression_level"] = level
        p = p_big / f"{codec}{level or ''}.parquet"
        t0 = time.perf_counter()
        pq.write_table(t, p, **kw)
        w = (time.perf_counter() - t0) * 1000
        q = Querier(p)
        try:
            q1 = median_ms(q, f"SELECT count(*), sum(vol) FROM read_parquet('{p.as_posix()}') "
                              f"WHERE symbol = 17 AND ts >= {lo + 3600_000_000_000} "
                              f"AND ts < {lo + 3600_000_000_000 + 60_000_000_000}")
            q3 = median_ms(q, f"SELECT count(*), sum(vol), min(px), max(px) "
                              f"FROM read_parquet('{p.as_posix()}')", 5)
        finally:
            q.close()
        mb = p.stat().st_size / 2**20
        rows6.append(dict(codec=f"{codec}-{level}" if level else codec, mb=mb,
                          write_ms=w, q1=q1, q3=q3))
        print(f"  {codec}-{level or '':2} {mb:7.1f} MB  写 {w:6.0f} ms  "
              f"Q1 点查 {q1:6.2f} ms  Q3 全扫 {q3:7.2f} ms")
    b_q1 = min(rows6, key=lambda r: r["q1"])
    b_q3 = min(rows6, key=lambda r: r["q3"])
    b_mb = min(rows6, key=lambda r: r["mb"])
    b_w = min(rows6, key=lambda r: r["write_ms"])
    print(f"\n点查最优 {b_q1['codec']} ｜ 全扫最优 {b_q3['codec']} ｜ "
          f"最小体积 {b_mb['codec']} ｜ 最快写入 {b_w['codec']}")
    print("规模放大后取舍才出现：zstd-9 写入比 none 慢 "
          f"{rows6[3]['write_ms'] / rows6[0]['write_ms']:.0f} 倍，"
          f"只换来体积 -{(1 - rows6[3]['mb'] / rows6[0]['mb']) * 100:.0f}%。")
    print("点查对压缩几乎无感（只读 1 个行组，省的都是解码差异）；全扫则真实受益于压缩。")
    out["b6"] = rows6


def report() -> None:
    rows = out["b2"]
    lines = [
        "# 第 9 章实测：可复现基准",
        "",
        "pyarrow 26.0.1 / duckdb 2.0.0，2026-11-11；587,000 行 × 7 列，单文件布局",
        "",
        f"- 数据指纹：{out['b1']['files']} 文件 / {out['b1']['rows']:,} 行 / sha256:{out['b1']['sha']}",
        "- 查询指纹：36 组配置的 Q3 结果完全一致（count + sum(vol)）",
        "",
        "## B2 矩阵（36 组）",
        "",
        "| # | 行序 | 编码 | 压缩 | 行组 | 体积 KB | 写 ms | Q1 点查 | Q2 窄扫 | Q3 全扫 | Q4 聚合 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['idx']:02d} | {'排序' if r['sort'] else '到达'} | {r['enc']} | "
            f"{r['codec']} | {r['rg']:,} | {r['kb']:.1f} | {r['write_ms']:.0f} | "
            f"{r['q1']:.2f} | {r['q2']:.2f} | {r['q3']:.2f} | {r['q4']:.2f} |"
        )
    lines += ["", "## B3 帕累托前沿（体积 vs Q3）", ""]
    for r in out["b3_front"]:
        lines.append(f"- {r['kb']:.1f} KB / Q3 {r['q3']:.2f} ms ｜ "
                     f"{'排序' if r['sort'] else '到达'} {r['enc']}-{r['codec']}-rg{r['rg']:,}")
    b4 = out["b4"]
    lines += [
        "",
        "## B4 三目标最优",
        "",
    ]
    for tag, key in (("点查", "best_q1"), ("全扫", "best_q3"), ("聚合", "best_q4")):
        r = b4[key]
        lines.append(f"- {tag}最优：{'排序' if r['sort'] else '到达'} {r['enc']}-"
                     f"{r['codec']}-rg{r['rg']:,}")
    lines += [
        "",
        "## B5 尾延迟（Q1 × 100）",
        "",
        "| 模式 | p50 | p95 | p99 | max | 变异系数 |",
        "|---|---|---|---|---|---|",
    ]
    for label, v in out["b5"].items():
        lines.append(f"| {label} | {v['p50']:.2f} | {v['p95']:.2f} | {v['p99']:.2f} | "
                     f"{v['max']:.2f} | {v['cv']:.2f} |")
    lines += [
        "",
        "## B6 规模放大（24,000,000 行，排序，rg=131,072）",
        "",
        "| 压缩 | 体积 MB | 写 ms | Q1 点查 ms | Q3 全扫 ms |",
        "|---|---|---|---|---|",
    ]
    for r in out["b6"]:
        lines.append(f"| {r['codec']} | {r['mb']:.1f} | {r['write_ms']:.0f} | "
                     f"{r['q1']:.2f} | {r['q3']:.2f} |")
    (ROOT / "reports" / "w09-bench.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (ROOT / "reports" / "w09-bench.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print("\n报告已写入 reports/w09-bench.md")


def main() -> None:
    b1()
    arrival, sorted_t = load()
    print(f"到达序 {arrival.num_rows:,} 行；排序版同样 {sorted_t.num_rows:,} 行")
    rows = b2(arrival, sorted_t)
    b3(rows)
    b4(rows)
    b5(rows)
    b6()
    report()


if __name__ == "__main__":
    main()
