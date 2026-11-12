"""W1 对照实验参照实现 —— 建议先自己写一遍，卡住了再看这里。

测三个查询目标（它们的最优配置是相反的，W9 会反复回到这张表）：
  a) 全扫聚合 : sum(vol), avg(px)                 -> 比 IO 吞吐
  b) 点查     : WHERE symbol=? AND ts BETWEEN ?   -> 比元数据剪枝
  c) 窄列扫描 : avg(px)                           -> 比列裁剪

用法: uv run python py/w01_compare.py
"""

from __future__ import annotations

import pathlib
import time

import duckdb

ROOT = pathlib.Path(__file__).resolve().parent.parent
DAY = "2026-11-05"
TRADES_DIR = ROOT / "lake" / "data" / "raw" / "trades"
# PARQUET_SRC 是一段可复用的内联子查询：先把 trades 目录下所有 parquet 当成一张大表，
# 再筛出单日数据，后面拼接进 COPY / sum / avg 等查询里复用。
#
# 1) read_parquet 通配加载（统一加载目录下所有 parquet）：
#    dir/*.parquet      该目录一层
#    dir/**/*.parquet   该目录所有层级（递归），此处即 2 天 x 2 交易所共 8 个文件
#    dir/               目录整体
#
# 2) hive_partitioning = true：解析 Hive 风格路径中的 key=value 段，生成"虚拟分区列"。
#    目录布局: trades/dt=2026-11-05/exchange=SH/part-600267.SH.parquet
#    数据文件本身不存 dt / exchange 两列（避免同一值重复存几十万次），
#    开关对比（DESCRIBE 实测）:
#      不开: [symbol, ts, seq, px, vol, side, channel]
#      开:   [... 同上 ..., dt(DATE), exchange(VARCHAR)]   <- 从路径反推
#    （新版 DuckDB 默认自动探测 hive 布局，显式写 true 是明确声明、不依赖版本行为。）
#
# 3) 目录名解析为列后，WHERE dt = '...' 可做文件级剪枝：
#    光看路径就能排除 dt=2026-11-04 下的整批文件，不用打开文件（EXPLAIN 实测
#    "Scanning Files: 4/8"）。它与 Parquet 行组统计剪枝是两个独立维度：
#      - hive 分区剪枝: 按路径剪"文件"，粒度是整个文件，零 IO 成本
#      - 行组剪枝:      打开文件后按 min/max 跳过 row group，粒度是文件内数据块
#    按天/按交易所分区，本质是给引擎留一条"不用打开文件就能排除整批数据"的捷径。
PARQUET_SRC = (
    f"(SELECT * FROM read_parquet('{TRADES_DIR / '**' / '*.parquet'}', hive_partitioning = true) "
    f"WHERE dt = '{DAY}')"
)
CSV_DIR = ROOT / "lake" / "data" / "exp" / "csv"
CSV_PATH = CSV_DIR / f"trades-{DAY}.csv"
CSV_SRC = f"read_csv_auto('{CSV_PATH}')"


def export_csv(con: duckdb.DuckDBPyConnection) -> None:
    """把单日 Parquet 落成 CSV，供后面的"同等查询换文件格式"对照使用。

    核心就一行 COPY ... TO，语法拆解:
      COPY (子查询) TO '路径'   把查询结果流式写出，不落中间表、不整块进内存
      TO '....csv'             文件后缀决定格式（.csv -> CSV，.parquet -> Parquet；
                              也可用 FORMAT csv 显式指定）
      (HEADER, DELIMITER ',')  CSV 专有选项: 写表头行、字段分隔符用逗号

    三点注意:
      1) hive 分区列被物化成实体列。dt / exchange 在 Parquet 里只是路径信息，
         写进 CSV 就变成普通文本列，每行重复 '2026-11-05,SH'。代价是 CSV 侧
         彻底失去分区剪枝能力，只能老实读完整行（此处 WHERE 已在 PARQUET_SRC
         里应用过，导出的本来就是单日数据）。
      2) 体积膨胀。实测单日 19.3 MB / 293,501 行（含表头）vs Parquet 4.11 MB，
         约 4.7x: CSV 纯文本、无压缩、无列式编码，ts 这种 19 位整数和重复的
         'SH' 全按字符存。这是 W1 想看到的第一层差距。
      3) 流式写出，内存友好，但重复跑会白花时间覆盖同一文件 —— 所以下面有
         "已存在就跳过" 的判断（想重测先删掉产物）。
    """
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    if CSV_PATH.exists():
        print(f"CSV 已存在，跳过导出（想重测就删掉它）: {CSV_PATH}")
        return
    t0 = time.perf_counter()
    con.execute(f"COPY {PARQUET_SRC} TO '{CSV_PATH}' (HEADER, DELIMITER ',')")
    print(f"导出 CSV 用时 {time.perf_counter() - t0:.2f}s")


def best_of(con: duckdb.DuckDBPyConnection, sql: str, n: int = 3) -> tuple[float, int]:
    """跑 n 次取最小值（避开冷启动和调度抖动），返回 (毫秒, EXPLAIN 里的 Data Read 行)。"""
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        con.execute(sql).fetchall()
        times.append((time.perf_counter() - t0) * 1000)
    plan = con.execute("EXPLAIN ANALYZE " + sql).fetchall()[0][1]
    return min(times), plan


def data_read(plan: str) -> str:
    for line in plan.splitlines():
        if "Data Read" in line:
            return " ".join(line.split())
    return "—"


def main() -> None:
    con = duckdb.connect()
    # 两行 SET 都是为了"让对照实验公平可复现"，而不是性能调优。
    #
    # SET threads = 4
    #   DuckDB 默认吃满所有核（本机实测 os.cpu_count=24 -> threads=24）。不钉死线程数，
    #   测出来的毫秒数就绑在"这台机器现在有多少核、有多闲"上，跨机对比、重跑复核都没意义。
    #   钉成 4 还有一层巧合：单日数据正好 2 交易所 x 2 只标的 = 4 个 parquet 文件，
    #   每个文件一个线程刚好打满文件级并行度，再往上加线程也压不出更多并行。
    #   （要测"核多是不是更快"时，就把这行删掉或用 SET threads = <N> 手动扫一遍。）
    #
    # SET enable_progress_bar = false
    #   Python 客户端默认是 True（实测 current_setting 返回 True），查询跑起来会往
    #   终端/Jupyter 刷进度条。在本脚本里它有两个坏处：一是每行输出混进一堆刷新字符，
    #   二是 best_of() 里连续跑十几次查询，进度条渲染本身就是计时噪音。
    #   关掉只是让结果干净，对查询耗时没有实质影响。
    con.execute("SET threads = 4")
    con.execute("SET enable_progress_bar = false")

    export_csv(con)

    rows, lo, hi = con.execute(f"SELECT count(*), min(ts), max(ts) FROM {PARQUET_SRC}").fetchone()
    win_lo, win_hi = lo + (hi - lo) // 2, lo + (hi - lo) // 2 + 600 * 10**9  # 中间 10 分钟

    pq_size = sum(p.stat().st_size for p in (TRADES_DIR / f"dt={DAY}").rglob("*.parquet"))
    csv_size = CSV_PATH.stat().st_size

    print(
        f"\n{DAY} 单日成交 {rows:,} 行 | Parquet {pq_size / 1e6:.2f} MB vs "
        f"CSV {csv_size / 1e6:.2f} MB | 体积比 {csv_size / pq_size:.2f}x\n"
    )

    queries = {
        "a 全扫聚合": "SELECT sum(vol), avg(px) FROM {src}",
        "b 点查": f"SELECT count(*), sum(vol) FROM {{src}} WHERE symbol = '600267.SH' AND ts BETWEEN {win_lo} AND {win_hi}",
        "c 窄列扫描": "SELECT avg(px) FROM {src}",
    }

    print(f"{'查询目标':<12}{'Parquet':>11}{'CSV':>11}{'倍数':>8}   {'Data Read(CSV 侧)':<12}")
    print("-" * 76)
    for name, tpl in queries.items():
        tp, plan_p = best_of(con, tpl.format(src=PARQUET_SRC))
        tc, plan_c = best_of(con, tpl.format(src=CSV_SRC))
        print(f"{name:<12}{tp:>9.1f}ms{tc:>9.1f}ms{tc / tp:>7.1f}x   {data_read(plan_c)}")
        # 想看 Parquet 侧到底读了多少字节，把下面这行取消注释
        print("    Parquet:", data_read(plan_p))

    print("\n提示：c 查询最能说明问题——CSV 依然要读完整行，Parquet 只读 px 一列。")
    print("自己动手：把上面 print 注释那行打开，对比两边的 Data Read 数字。")


if __name__ == "__main__":
    main()
