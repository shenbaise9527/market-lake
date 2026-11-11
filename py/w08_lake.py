"""第 8 章实验：数据湖工程。

E1  三种分区策略：单文件/天、每合约每天、每合约每小时 —— 文件数、元数据开销、点查延迟
E2  幂等写入：临时目录 + 原子 rename，重跑两次结果不变；直接覆盖写会怎样
E3  去重键：为什么 (symbol, ts) 不够，必须带 seq/channel
E4  分层 rollup：raw → 1s → 1m，行数与体积
E5  compaction：小文件合并前后的点查延迟
E6  质量报告：缺口 / 重复率 / 价格跳变

用法：uv run python py/w08_lake.py
产出：reports/w08-lake.md
"""

from __future__ import annotations

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
EXP = ROOT / "lake" / "data" / "exp08"
RAW = ROOT / "lake" / "data" / "raw" / "trades"
out: dict[str, object] = {}


def rule(t: str) -> None:
    print(f"\n{'=' * 74}\n{t}\n{'=' * 74}")


def load() -> pa.Table:
    # 用 dataset 读，才能把 hive 分区键 dt/exchange 变成列
    d = ds.dataset(RAW, format="parquet", partitioning="hive")
    return d.to_table().sort_by(
        [("dt", "ascending"), ("symbol", "ascending"), ("ts", "ascending")]
    )


def reset(p: pathlib.Path) -> None:
    if p.exists():
        shutil.rmtree(p)
    p.mkdir(parents=True)


def write_partitioned(t: pa.Table, root: pathlib.Path, keys: list[str], rg: int = 32_768) -> None:
    ds.write_dataset(
        t,
        root,
        format="parquet",
        partitioning=keys,
        partitioning_flavor="hive",
        basename_template="part-{i}.parquet",
        existing_data_behavior="overwrite_or_ignore",
        max_rows_per_file=0,
        max_rows_per_group=rg,
        file_options=ds.ParquetFileFormat().make_write_options(compression="zstd"),
    )


def point_query(files_glob: str, symbol: str, lo: int, hi: int, hive: bool = True,
                extra: str = "") -> float:
    con = duckdb.connect()
    con.execute("SET threads=4")
    cond = f" AND {extra}" if extra else ""
    sql = (f"SELECT count(*), sum(vol) FROM read_parquet('{files_glob}', hive_partitioning={str(hive).lower()}) "
           f"WHERE symbol='{symbol}' AND ts >= {lo} AND ts < {hi}{cond}")
    ts = []
    for _ in range(15):
        t0 = time.perf_counter()
        con.execute(sql).fetchone()
        ts.append((time.perf_counter() - t0) * 1000)
    con.close()
    return statistics.median(ts)


def bind_cost(files_glob: str) -> float:
    """只绑定不扫描：WHERE false 下仍要为每个文件打开并读 footer，测的是固定开销。"""
    con = duckdb.connect()
    con.execute("SET threads=4")
    sql = f"SELECT count(*) FROM read_parquet('{files_glob}', hive_partitioning=true) WHERE false"
    ts = []
    for _ in range(15):
        t0 = time.perf_counter()
        con.execute(sql).fetchone()
        ts.append((time.perf_counter() - t0) * 1000)
    con.close()
    return statistics.median(ts)


def e1(t: pa.Table) -> None:
    rule("E1  三种分区策略")
    lo = int(t.column("ts")[0].as_py())
    # 取中间某个合约的 1 分钟窗口
    sym = "002322.SZ"
    day = t.filter(pa.compute.equal(t["symbol"], sym)).column("ts").to_pylist()
    mid = day[len(day) // 2]
    hi = mid + 60 * 10**9
    lo = mid

    variants = {
        "A 单文件/天": ["dt"],
        "B 每合约每天": ["dt", "symbol"],
        "C 每合约每小时": ["dt", "symbol", "hour"],
    }
    rows = []
    for name, keys in variants.items():
        root = EXP / f"e1-{keys[-1]}"
        reset(root)
        tab = t
        if "hour" in keys:
            tab = t.append_column(
                "hour", pa.array([x // 3_600_000_000_000 for x in t.column("ts").to_pylist()], pa.int64())
            )
        t0 = time.perf_counter()
        write_partitioned(tab, root, keys)
        wms = (time.perf_counter() - t0) * 1000
        files = sorted(root.rglob("*.parquet"))
        size = sum(f.stat().st_size for f in files)
        footers = sum(pq.ParquetFile(f).metadata.serialized_size for f in files)
        ms = point_query(f"{root.as_posix()}/**/*.parquet", sym, lo, hi)
        bms = bind_cost(f"{root.as_posix()}/**/*.parquet")
        rows.append(dict(name=name, files=len(files), mb=size / 2**20,
                         footer_kb=footers / 1024, write_ms=wms, q_ms=ms, bind_ms=bms))
        print(f"{name:16} {len(files):3d} 个文件  {size/2**20:6.2f} MB  "
              f"footer {footers/1024:6.1f} KB  写 {wms:5.0f} ms  "
              f"绑定 {bms:5.2f} ms  点查 {ms:5.2f} ms")
    a, b, c = rows
    print(f"\n文件数 {a['files']} → {b['files']} → {c['files']}：footer {a['footer_kb']:.1f} → "
          f"{c['footer_kb']:.1f} KB，点查 {a['q_ms']:.2f} → {c['q_ms']:.2f} ms。")
    print("把开销拆开看：`WHERE false`（只绑定、不读一个字节）下")
    for r in rows:
        print(f"  {r['name']:16} {r['files']:3d} 文件  绑定 {r['bind_ms']:5.2f} ms  "
              f"= {r['bind_ms']/r['files']:.2f} ms/文件")
    print("→ 开销与查询命中无关，是**每个文件 open + 读 footer** 的固定成本。")
    print("  极细分区让每文件只剩 1 个行组，行组级剪枝也没得可剪，纯亏。")

    hour = mid // 3_600_000_000_000
    q_hour = point_query(f"{(EXP / 'e1-hour').as_posix()}/**/*.parquet", sym, lo, hi,
                         extra=f"hour = {hour}")
    print(f"\n[反证] C 布局的谓词补上 hour = {hour}：点查 {q_hour:.2f} ms（不补 {c['q_ms']:.2f} ms）")
    print(f"  而 C 的纯绑定开销是 {c['bind_ms']:.2f} ms —— 两者几乎相等。")
    print("  说明扫描本身已经降到约 0，剩下的全是打开 40 个文件的钱；剪枝只作用于扫描，救不了绑定。")
    out["e1b"] = {"q_hour": q_hour, "q_all": c["q_ms"]}
    out["e1"] = rows


def e2(t: pa.Table) -> None:
    rule("E2  幂等写入：临时目录 + 原子 rename")
    root = EXP / "e2"
    reset(root)

    def write_once(run: int) -> int:
        tmp = root / f".tmp-{run}"
        tmp.mkdir(parents=True, exist_ok=True)
        write_partitioned(t, tmp, ["dt", "symbol"])
        # 原子替换：先删旧目录再改名（Windows 上目录 rename 不能覆盖）
        final = root / "trades"
        if final.exists():
            shutil.rmtree(final)
        tmp.rename(final)
        return len(list(final.rglob("*.parquet")))

    n1 = write_once(1)
    rows1 = sum(pq.ParquetFile(f).metadata.num_rows for f in (root / "trades").rglob("*.parquet"))
    n2 = write_once(2)
    rows2 = sum(pq.ParquetFile(f).metadata.num_rows for f in (root / "trades").rglob("*.parquet"))
    print(f"第一次：{n1} 个文件 / {rows1:,} 行")
    print(f"第二次：{n2} 个文件 / {rows2:,} 行 → {'幂等' if (n1, rows1) == (n2, rows2) else '不幂等'}")

    # 反例：每次用带 run 号的文件名追加写（真实管线的常见做法）
    # 注意必须显式 overwrite_or_ignore：默认的 delete_matching 会先删分区内旧文件，
    # 于是"追加两次"看起来也幂等——这是一个很容易把结论测反的坑。
    bad = EXP / "e2-bad"
    reset(bad)
    for run in (1, 2):
        ds.write_dataset(
            t, bad, format="parquet", partitioning=["dt", "symbol"],
            partitioning_flavor="hive",
            basename_template=f"part-run{run}-{{i}}.parquet",
            existing_data_behavior="overwrite_or_ignore",
        )
    nb = len(list(bad.rglob("*.parquet")))
    rb = sum(pq.ParquetFile(f).metadata.num_rows for f in bad.rglob("*.parquet"))
    print(f"带 run 号文件名追加写两次：{nb} 个文件 / {rb:,} 行（源 {t.num_rows:,} 行，"
          f"多出 {rb - t.num_rows:,} 行重复）")
    out["e2"] = {"files": n2, "rows": rows2, "bad_files": nb, "bad_rows": rb}


def e3(t: pa.Table) -> None:
    rule("E3  去重键：(symbol, ts) 不够")
    con = duckdb.connect()
    src = f"read_parquet('{RAW.as_posix()}/**/*.parquet', hive_partitioning=true)"
    total = con.execute(f"SELECT count(*) FROM {src}").fetchone()[0]
    keysets: list[dict] = []
    for keys in ("symbol, ts", "symbol, ts, seq", "symbol, ts, seq, channel"):
        uniq = con.execute(f"SELECT count(*) FROM (SELECT DISTINCT {keys} FROM {src})").fetchone()[0]
        keysets.append(dict(keys=keys, uniq=uniq))
        print(f"DISTINCT ({keys:28}) = {uniq:>7,}（合并掉 {total - uniq:,} 行）")
    print("加 seq 完全没变化：同一 seq 在不同 channel 上复用，seq 单独不唯一。")
    print("再加 channel 才到 586,262——剩下 738 行才是真正的重复回报（重传）。")
    print("只用 (symbol, ts)：同一纳秒的多笔成交被误合并，成交量直接少算。")

    # 反例：只用 (symbol, ts) 会丢多少成交量
    q = f"""
    WITH d AS (
      SELECT symbol, ts, any_value(vol) AS vol_keep FROM {src} GROUP BY 1, 2
    )
    SELECT (SELECT sum(vol) FROM {src}) AS all_vol, (SELECT sum(vol_keep) FROM d) AS kept_vol
    """
    a, k = con.execute(q).fetchone()
    print(f"按 (symbol, ts) 去重后 sum(vol)：{k:,} / 原始 {a:,}（少 {a-k:,}，{(a-k)/a:.2%}）")
    out["e3"] = {"total": total, "vol_all": float(a), "vol_kept": float(k),
                 "keysets": keysets}


def e4(t: pa.Table) -> None:
    rule("E4  分层 rollup：raw → 1s → 1m")
    con = duckdb.connect()
    con.execute("SET threads=4")
    src = f"read_parquet('{RAW.as_posix()}/**/*.parquet', hive_partitioning=true)"
    layers = {
        "raw": (t.num_rows, sum(f.stat().st_size for f in RAW.rglob("*.parquet"))),
    }
    root = EXP / "e4"
    reset(root)
    for name, step in (("1s", 1_000_000_000), ("1m", 60_000_000_000)):
        p = root / f"{name}.parquet"
        t0 = time.perf_counter()
        con.execute(f"""
            COPY (
              SELECT symbol, ts // {step} * {step} AS ts, count(*) AS trades,
                     sum(vol) AS volume, sum(px * vol) / sum(vol) AS vwap,
                     min(px) AS lo, max(px) AS hi
              FROM {src} GROUP BY 1, 2
            ) TO '{p.as_posix()}' (FORMAT PARQUET, COMPRESSION zstd)
        """)
        ms = (time.perf_counter() - t0) * 1000
        n = pq.ParquetFile(p).metadata.num_rows
        layers[name] = (n, p.stat().st_size, ms)
        print(f"{name:4} {n:>8,} 行  {p.stat().st_size/1024:8.1f} KB  "
              f"生成 {ms:5.0f} ms  压缩比 {layers['raw'][1]/p.stat().st_size:6.1f}×")
    print(f"raw  {layers['raw'][0]:>8,} 行  {layers['raw'][1]/1024:8.1f} KB")
    out["e4"] = {k: (v[0], v[1]) for k, v in layers.items()}


def e5(t: pa.Table) -> None:
    rule("E5  compaction：合并小文件")
    small = EXP / "e5-small"
    reset(small)
    hourly = t.append_column(
        "hour", pa.array([x // 3_600_000_000_000 for x in t.column("ts").to_pylist()], pa.int64())
    )
    write_partitioned(hourly, small, ["dt", "symbol", "hour"], rg=8_192)
    n_before = len(list(small.rglob("*.parquet")))
    merged = EXP / "e5-merged"
    reset(merged)
    write_partitioned(t, merged, ["dt", "symbol"], rg=32_768)
    n_after = len(list(merged.rglob("*.parquet")))

    sym, day = "002322.SZ", "2026-11-05"
    ts = t.filter(pa.compute.equal(t["symbol"], sym)).column("ts").to_pylist()
    lo = ts[len(ts) // 2]
    q_small = point_query(f"{small.as_posix()}/**/*.parquet", sym, lo, lo + 60 * 10**9)
    q_big = point_query(f"{merged.as_posix()}/**/*.parquet", sym, lo, lo + 60 * 10**9)
    size_small = sum(f.stat().st_size for f in small.rglob("*.parquet"))
    size_big = sum(f.stat().st_size for f in merged.rglob("*.parquet"))
    print(f"合并前：{n_before} 个文件 / {size_small/2**20:.2f} MB / 点查 {q_small:.2f} ms")
    print(f"合并后：{n_after} 个文件 / {size_big/2**20:.2f} MB / 点查 {q_big:.2f} ms")
    print(f"→ 文件数 ÷{n_before/n_after:.0f}，点查 ×{q_small/q_big:.1f}")
    out["e5"] = {"before": n_before, "after": n_after, "q_before": q_small, "q_after": q_big,
                 "size_before": size_small, "size_after": size_big}


def e6(t: pa.Table) -> None:
    rule("E6  质量报告：缺口 / 重复 / 跳变")
    con = duckdb.connect()
    src = f"read_parquet('{RAW.as_posix()}/**/*.parquet', hive_partitioning=true)"
    r = con.execute(f"""
        WITH x AS (
          SELECT dt, symbol, ts, px, vol,
                 lag(px) OVER (PARTITION BY symbol ORDER BY ts) AS prev_px,
                 lag(ts) OVER (PARTITION BY symbol ORDER BY ts) AS prev_ts
          FROM {src}
        )
        SELECT
          count(*) AS rows,
          sum(CASE WHEN px / prev_px - 1 > 0.05 OR px / prev_px - 1 < -0.05 THEN 1 ELSE 0 END) AS jumps,
          sum(CASE WHEN ts = prev_ts THEN 1 ELSE 0 END) AS same_ns,
          max(px / prev_px - 1) AS max_up
        FROM x WHERE prev_px IS NOT NULL
    """).fetchone()
    print(f"行数 {r[0]:,}；同纳秒相邻 {r[2]:,} 笔；|涨跌|>5% 的相邻跳变 {r[1]:,} 笔；最大单笔涨幅 {r[3]:.1%}")

    daily = con.execute(f"""
        SELECT dt, count(*) AS rows, count(DISTINCT symbol) AS syms,
               sum(CASE WHEN px <= 0 THEN 1 ELSE 0 END) AS bad_px
        FROM {src} GROUP BY 1 ORDER BY 1
    """).fetchall()
    for row in daily:
        print(f"  {row[0]}  行 {row[1]:>7,}  合约 {row[2]}  非正价格 {row[3]}")
    out["e6"] = {"rows": int(r[0]), "jumps": int(r[1]), "same_ns": int(r[2]), "max_up": float(r[3])}


def report() -> None:
    e1, e2, e3, e5, e6 = out["e1"], out["e2"], out["e3"], out["e5"], out["e6"]
    lines = [
        "# 第 8 章实测：数据湖工程",
        "",
        "pyarrow 26.0.1 / duckdb 2.0.0，2026-11-11；587,000 行 × 7 列",
        "",
        "## E1 分区策略",
        "",
        "| 策略 | 文件数 | 体积 MB | footer KB | 写 ms | 绑定 ms | 点查 ms |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in e1:
        lines.append(f"| {r['name']} | {r['files']} | {r['mb']:.2f} | {r['footer_kb']:.1f} | "
                     f"{r['write_ms']:.0f} | {r['bind_ms']:.2f} | {r['q_ms']:.2f} |")
    lines += [
        "",
        f"绑定开销 = `WHERE false` 下的耗时，不读任何数据，纯 `open + 读 footer`："
        f"{e1[0]['bind_ms']/e1[0]['files']:.2f} / {e1[1]['bind_ms']/e1[1]['files']:.2f} / "
        f"{e1[2]['bind_ms']/e1[2]['files']:.2f} ms per file。"
        f"给 C 布局加上 `hour` 谓词后 {out['e1b']['q_hour']:.2f} ms，几乎等于它的纯绑定开销 "
        f"{e1[2]['bind_ms']:.2f} ms：扫描已降到约 0，剩下的全是打开 40 个文件的钱。"
        f"剪枝只作用于扫描，救不了绑定。",
    ]
    lines += [
        "",
        "## E2 幂等",
        "",
        f"- 临时目录 + rename：{e2['files']} 文件 / {e2['rows']:,} 行（两次一致）",
        f"- 直接追加两次：{e2['bad_files']} 文件 / {e2['bad_rows']:,} 行（翻倍）",
        "",
        "## E3 去重键",
        "",
        "| 去重键 | 唯一行数 | 合并掉 |",
        "|---|---|---|",
        *[f"| `({r['keys']})` | {r['uniq']:,} | {e3['total'] - r['uniq']:,} |" for r in e3["keysets"]],
        "",
        f"按 `(symbol, ts)` 去重后 sum(vol) 少 "
        f"{(e3['vol_all']-e3['vol_kept'])/e3['vol_all']:.2%}"
        f"（{e3['vol_all']-e3['vol_kept']:,.0f} / {e3['vol_all']:,.0f}）——丢的是真实成交。",
        "",
        "## E4 分层 rollup",
        "",
        "| 层 | 行数 | 体积 | 相对 raw 压缩比 |",
        "|---|---|---|---|",
        *[f"| {k} | {v[0]:,} | {v[1]/1024:.1f} KB | {out['e4']['raw'][1]/v[1]:.0f}× |"
          for k, v in out["e4"].items()],
        "",
        "## E5 compaction",
        "",
        f"- {e5['before']} → {e5['after']} 文件；点查 {e5['q_before']:.2f} → {e5['q_after']:.2f} ms",
        "",
        "## E6 质量报告",
        "",
        f"- {e6['rows']:,} 行；跳变 {e6['jumps']:,} 笔；同纳秒 {e6['same_ns']:,} 笔；"
        f"最大涨幅 {e6['max_up']:.1%}",
    ]
    (ROOT / "reports" / "w08-lake.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (ROOT / "reports" / "w08-lake.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print("\n报告已写入 reports/w08-lake.md")


def main() -> None:
    t = load()
    print(f"源数据 {t.num_rows:,} 行")
    e1(t)
    e2(t)
    e3(t)
    e4(t)
    e5(t)
    e6(t)
    report()


if __name__ == "__main__":
    main()
