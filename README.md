# market-lake — 行情数据湖学习工作台

围绕 **Parquet / Arrow / DuckDB / Polars** 四件套，用真实的 tick 级行情数据，
把「海量行情数据怎么存、怎么算、怎么快」这条链路在 **Python / Rust / C++** 三种语言里各走一遍。

不是教程合集，是一个**可以边学边演进的仓库**：每周的任务卡让你改这个仓库，
十周之后它会变成一条能跑的行情数据管线。

---

## 先建立正确的分层认知

这四个东西不是并列的四个技术，而是一条流水线上的三层：

```
      ┌──────────────────────────────────────────────┐
      │ 计算层  DuckDB  ·  Polars  ·  DataFusion      │  ← 写 SQL / 表达式
      ├──────────────────────────────────────────────┤
      │ 内存层  Apache Arrow（RecordBatch / 零拷贝）   │  ← 数据在内存里的样子
      ├──────────────────────────────────────────────┤
      │ 存储层  Parquet（行组 / 编码 / 统计信息）      │  ← 数据在磁盘上的样子
      └──────────────────────────────────────────────┘
              行情数据的特殊性决定了每一层的取舍
```

行情数据的四个事实，贯穿全部十周：

| 事实 | 直接后果 |
|---|---|
| append-only、读多写极少 | 可以「写时排序 + 大文件」，不必考虑原地更新 |
| 查询几乎都带 `(symbol, ts)` | 排序键、分区键、min/max 统计决定性能上限 |
| 单日是「天 × 合约 × 十万到百万行」 | 按天分区天然合理，但要防小文件 |
| 回测是单遍顺序扫描 | 顺序读吞吐 ≫ 随机点查，两者优化方向相反 |

---

## 快速开始

```bash
# 0) 取代码并装 Python 依赖（数据、依赖树、构建产物都不在仓库里，克隆只有 ~1.4 MB）
git clone https://github.com/shenbaise9527/market-lake.git
cd market-lake
uv sync

# 1) 生成合成行情（含乱序/重复/缺口/异常值，故意的）
uv run python py/gen_tick.py --days 2 --rows 300000 --symbols 4 --with-book

# 2) 看 Parquet 的物理结构（本仓库最有用的工具）
cd rust && cargo run -q -- inspect "../lake/data/raw/trades/dt=2026-11-05/exchange=SH/part-600267.SH.parquet" --head 1

# 3) 直接对着目录跑 SQL，不建表
cd .. && uv run python py/q_duckdb.py

# 4) 同一组计算用 Polars 写一遍，对比计划与耗时
uv run python py/q_polars.py

# 5) 证明"零拷贝"不是营销词（比内存地址）
uv run python py/cdata_demo.py

# 6) 基准矩阵：编码 × 压缩 × 行组 × 排序
uv run python py/bench.py --codes none,snappy,zstd --encodings plain,bss --row-groups 65536,1048576

# 7) 验收自检（每周任务卡做完跑一次）
uv run python py/verify.py
```

本机已验证：`8/8 通过`（587,000 行合成数据，8 个数据文件）。

---

## 目录

```
market-lake/
├── PLAN.md               十周总表（先看这个）
├── docs/
│   ├── week01..10.md     每周任务卡：目标 / 阅读 / 动手 / 验收 / 坑
│   ├── architecture.md   TB 级数据湖的架构决策（分区分桶、增量、compaction）
│   ├── schema.md         行情表结构与精度约定
│   ├── reading.md        按周排的资料清单
│   └── cheatsheet.md     调参结论速查表（你自己填，这是最值钱的产出）
├── py/
│   ├── gen_tick.py       合成 tick 生成器（注入四类脏数据）
│   ├── q_duckdb.py       五个必练 SQL（含 ASOF JOIN）
│   ├── q_polars.py       惰性求值 / group_by_dynamic / join_asof / sink
│   ├── cdata_demo.py     Arrow 零拷贝验证 + 内存地址对比
│   ├── bench.py          基准矩阵，输出 markdown 表
│   └── verify.py         验收自检
├── rust/                 mdkake 命令行：inspect / rewrite / scan
├── cpp/                  C++ 回放器（vcpkg 清单装 Arrow/Parquet，静态单 exe）
└── lake/                 数据目录（不入库）
    └── data/{raw,derived,exp}/{trades,book}/dt=.../exchange=.../
```

---

## 三种语言怎么分工

生产环境里三者的定位不同，学习时不要平均用力：

| | 写代码的速度 | 运行速度 | 本仓库里的角色 |
|---|---|---|---|
| **Python** | 最快 | 慢（但 Arrow/Polars 已把热点下沉到 C++/Rust） | 探索、验证、查询层、基准脚本 |
| **Rust** | 中 | 最快且可控 | 写入器、服务端、DataFusion 自定义算子 |
| **C++** | 最慢 | 最快 | 只在嵌入既有系统（柜台、网关）时用 |

**关键练习**：同一个 schema，在三种语言里各写一次 parquet 写入器。
写三遍之后你对「行组 / 编码 / 排序键 / 统计信息」的理解会从"知道"变成"有手感"。

---

## 十周路线

| 周 | 阶段 | 主题 | 交付物 |
|---|---|---|---|
| 1 | S0 | 列式存储为什么快 + 环境 + 合成数据 | 能跑通全部 7 条命令 |
| 2 | S1 | Parquet 物理结构：行组、页、编码、统计 | `inspect` 输出解读笔记 |
| 3 | S1 | 写入器：排序键、分区、压缩、文件尺寸 | 三语言写入器 v0 + 体积对比表 |
| 4 | S2 | Arrow 内存模型：buffer / validity / offsets | 手写 tick → RecordBatch 解析器 |
| 5 | S2 | IPC、Flight、C Data Interface 零拷贝 | 跨语言零拷贝 demo + 地址验证 |
| 6 | S3 | DuckDB：下推、ASOF、窗口、内存管理 | 5 个核心查询 + 执行计划分析 |
| 7 | S3 | Polars 惰性求值 + DataFusion（Rust） | 同查询三实现对比表 |
| 8 | S4 | 数据湖工程：增量、去重、分层、compaction | raw→1s→1m 三层 + 质量报告 |
| 9 | S5 | 性能调优与基准体系 | 调参速查表（填满 cheatsheet.md） |
| 10 | S6 | 实战收口：回放引擎 + 架构文档 | 单遍顺序回放的读取器 + 复盘 |

每周 8–10 小时：约 3 小时读、5 小时写、1 小时记录结论。

---

## 环境说明（本机实测）

| 组件 | 版本 | 备注 |
|---|---|---|
| Python | **用 uv 管理**（uv 0.9.5，Python ≥3.12） | `uv sync` 一键建 `.venv` 并锁版本，见下方 |
| Rust | cargo 1.99，arrow + parquet 60.1.0 | `CARGO_TARGET_DIR=D:\rust\cargo\bin`，所以二进制不在 `rust/target/` 下，用 `cargo run --` |
| C++ | cmake 3.30 + vcpkg | 依赖由 `cpp/vcpkg.json` 声明，首次 configure 自动装（Windows 3.6 GB / Linux 1.4 GB，不入库）；两个平台的构建与实测见 `cpp/build.md` |

**Python 一律通过 uv 运行**（依赖声明在 `pyproject.toml`，锁定在 `uv.lock`）：

```bash
cd E:\work\learning\market-lake
uv sync                          # 首次：创建 .venv 并安装全部依赖
uv run uv run python py/verify.py       # 之后所有脚本都用 uv run
```

不要再用系统 `python` 或 `C:\Python312\python.exe`——那个环境是临时装的，uv 的 `.venv` 才是本仓库的唯一事实来源。换机器时只需 `uv sync` 一条命令恢复全部依赖。

---

## 学习法：每周一个闭环

```
读 30%  →  写 50%  →  测 20%
   ↑                        │
   └──── 结论写进 cheatsheet ┘
```

三条铁律：

1. **不写不读第二遍**。任何概念，当天必须落成一段能跑的代码，否则等于没学。
2. **每个结论都要有数字**。不要写"zstd 压缩比更高"，要写"这份 tick 数据上 zstd-3 比 snappy 小 42%，全扫慢 3ms"。
3. **先手写，再用库**。先自己写一遍逐字节解析，再用 `pyarrow.parquet.read_metadata()`，理解深度差一个量级。

每周结束时把 `py/verify.py` 跑一遍，全 PASS 才进下一周。
