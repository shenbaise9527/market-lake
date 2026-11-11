# 出处与核对状态

## 引用纪律

本站的每条结论按**可信度分层**使用，不同层级的用途完全不同：

| 层级 | 来源类型 | 用它做什么 | 不能做什么 |
|---|---|---|---|
| ① | 规范（thrift、格式文档）、论文 | 描述机制、字节布局、API 语义的**唯一依据** | — |
| ② | 本机实测（脚本 + 版本号） | 所有"快 N 倍 / 省 N%" | 不能外推到别的数据规模 |
| ③ | 官方文档、工程博客 | 建立直觉、发现线索、了解实现取舍 | 不复制正文；说法必须与 ① 或 ② 对得上才采用 |

**三条硬规则**：

1. 机制类陈述必须能在 ① 里找到对应章节，脚注写清「文档名 + 小节」或「论文 + 页码」
2. 数字必须能指向仓库里的脚本；查不到实测的地方**宁可不写数字**
3. 博客只挂链接不复制正文——既尊重版权，也避免把别人的错误抄进来

## 已核实资料

访问日期统一为 2026-11-11。核对状态：✅ 已核对原文 ／ ⬜ 待核对。

### 规范与论文（层级 ①）

| 资料 | 用于 | 状态 |
|---|---|---|
| [Parquet File Format](https://parquet.apache.org/docs/file-format/)（File Layout / Row Group / Column Chunk / Page / Encodings / Page Index） | 第 1–3 章 | ✅ 概念节已核对 |
| [`parquet.thrift`](https://github.com/apache/parquet-format/blob/master/src/main/thrift/parquet.thrift)（字段语义唯一权威） | 第 2 章 | ⬜ 第 2 章写作时逐字段核对 |
| [Parquet Encodings.md](https://github.com/apache/parquet-format/blob/master/Encodings.md) | 第 2–3 章 | ⬜ |
| [Arrow Columnar Format](https://arrow.apache.org/docs/format/Columnar.html) | 第 4 章 | ⬜ |
| [Arrow IPC / Flight / C Data Interface](https://arrow.apache.org/docs/format/CDataInterface.html) | 第 5 章 | ⬜ |
| Boncz, Zukowski, Nes, *MonetDB/X100: A Hyper-Pipelined Query Execution Engine*, CIDR 2005（[PDF](https://www.cidrdb.org/cidr2005/papers/P19.pdf)，§1–§3） | 第 1 章（向量化执行的性能来源） | ✅ 第 1 章引用限于 §2 的定性结论 |

### 实现源码核对（层级 ①，本机安装版本，2026-11-11）

| 源码 | 核对了什么 |
|---|---|
| parquet-rs 60.1.0 `src/file/properties.rs` | 全部 `DEFAULT_*` 常量与 `set_*` setter（默认不压缩、页 1 MiB、每页 20,000 行、统计级别 Page） |
| Arrow C++ `parquet/properties.h`（随 pyarrow 26.0.1 分发） | `kDefaultDataPageSize` / `kDefaultMaxRowsPerPage` / `DEFAULT_COMPRESSION_TYPE` / `DEFAULT_IS_PAGE_INDEX_ENABLED` |
| pyarrow 26.0.1 `pq.write_table` 函数签名 | `compression='snappy'`、`write_page_index=False` 等默认值 |

### 官方文档与工程博客（层级 ③，只作线索）

| 资料 | 用于 | 说明 |
|---|---|---|
| [DuckDB: CSV Files — Dethroning Parquet, or Not?](https://duckdb.org/2024/12/05/csv-files-dethroning-parquet-or-not.html)（2024-12-05） | 第 1 章（CSV vs Parquet 场景判断） | 结论与本站实测方向一致 |
| [DuckDB: Query Engines — Gatekeepers of the Parquet File Format](https://duckdb.org/2025/01/22/parquet-encodings.html)（2025-01-22） | 第 2–3 章 | 引擎如何对待不同编码 |
| [DuckDB: Parquet Bloom Filters](https://duckdb.org/2025/03/07/parquet-bloom-filters-in-duckdb.html)（2025-03-07） | 第 2 章 | bloom filter 的实际收益 |
| [DuckDB: Lightweight Compression](https://duckdb.org/2022/10/28/lightweight-compression.html)（2022-10-28） | 第 3 章 | 压缩与编码的取舍 |
| [DuckDB: Memory Management](https://duckdb.org/2024/07/09/memory-management.html)（2024-07-09） | 第 6 章 | `memory_limit` 的真实行为 |
| [Wes McKinney: Apache Arrow and the "10 Things I Hate About pandas"](https://wesmckinney.com/blog/apache-arrow-pandas-internals/)（2017-09-21） | 第 1、4 章 | Arrow 内存布局的设计动机 |

### 中文资料（层级 ③，同一手资料的导读，可替代阅读但不作引用依据）

| 资料 | 可替代 | 备注 |
|---|---|---|
| [DuckDB：列式存储与 Parquet 解析](http://mp.weixin.qq.com/s?__biz=MzUwOTU4OTU2NQ==&mid=2247483675&idx=1&sn=ae0be589fe1303d940eb92197bc28b66)（公众号《DuckDB:从上手到内核》#5） | Parquet Concepts 的先导 | 有逐层图解与谓词下推实测；公众号链接可能失效，失效搜标题 |
| [从行到向量 —— 数据库执行引擎的进化之路](http://mp.weixin.qq.com/s?__biz=Mzk3NTE2NzU5NQ==&mid=2247488037&idx=1&sn=0d688a439e70db8dd5b16c92b9f3ec32)（公众号"老纪的技术唠嗑局"，OceanBase 社区） | X100 论文 §1–§3 | 火山模型 vs 向量化、cache、分支预测、SIMD，带 TPC-H 数字 |

!!! note "为什么中文资料不作为引用依据"
    二手导读难免简化甚至出错。本站允许"先读中文建立直觉"，但第 ③ 层的说法要能在 ① 或 ② 找到支撑才写进正文。
    这不是对中文资料的偏见——英文博客同样受这条约束。

## 实验与复现

| 脚本 | 产出 | 对应章节 |
|---|---|---|
| `py/gen_tick.py` | 合成 tick（注入乱序 / 重复 / 缺口 / 异常跳变） | 全部 |
| `py/w01_compare.py` | CSV vs Parquet 三种查询目标对照 | 第 1 章 |
| `py/bench.py` | 编码 × 压缩 × 行组的实验矩阵 → `reports/bench.md` | 第 3、9 章 |
| `py/w03_writer.py` | 五旋钮矩阵 E0–E4 → `reports/w03-writer.md` | 第 3 章 |
| `rust/src/pages.rs` | 页级剪枝探测（读 column/offset index）→ `mdlake pages` | 第 3 章 |
| `tools/measure_svg.py` | 图解包围盒测量（收紧 viewBox 用） | 写作工具 |
| `py/q_duckdb.py` | DuckDB 五个核心查询（含 ASOF） | 第 6 章 |
| `py/q_polars.py` | Polars 惰性版同一组查询 | 第 7 章 |
| `py/cdata_demo.py` | C Data Interface 零拷贝地址验证 | 第 5 章 |
| `rust/src/inspect.rs` | Parquet 物理结构解剖（`mdlake inspect`） | 第 1–2 章 |
| `py/verify.py` | 各周门禁自检 | 全部 |
| `py/w04_arrow_memory.py` | buffer 布局 / 零拷贝边界 / 字典 / 对齐实测 | 第 4 章 |
| `py/w05_ipc.py` | 序列化矩阵 / file vs stream / C Data 胶囊 / Flight | 第 5 章 |
| `py/w06_duckdb.py` | EXPLAIN ANALYZE / 列裁剪 / ASOF 计划陷阱 | 第 6 章 |
| `py/w07_polars.py` | 三引擎对齐 / group_by_dynamic / sink 边界 | 第 7 章 |
| `py/w08_lake.py` | 分区 / 幂等 / 去重键 / rollup / compaction / 质量报告 | 第 8 章 |
| `py/w09_bench.py` | 基准四要素 / 36 组矩阵 / 帕累托 / 尾延迟 / 规模放大 | 第 9 章 |
| `py/capstone_query.py` | 查询层视图 / 指纹对比 / 质量闸门 / C4 交叉验证 | 第 10 章 |
| `rust/src/ingest.rs` | 生产写入路径（排序 → 分区 → 幂等落盘） | 第 10 章 |
| `cpp/main.cpp` | Arrow C++ 单遍顺序回放（双均线信号） | 第 10 章 |
| `py/polars_cookbook.py` | Polars 常用方法示例集（全部输出实跑） | 附录 · Polars 速查 |
| `cpp/main.cpp` | Arrow C++ 单遍顺序回放（双均线信号） | 第 10 章 |

环境：Python 3.13（uv 管理，`uv sync` 复现）／ pyarrow 26.0.1 ／ polars 2.1.0 ／ duckdb 2.0.0 ／
Rust arrow + parquet 60.1.0 ／ Arrow C++ 26.0.1（MSVC 14.36，C++20）。**结论与版本强相关，重跑前先看版本是否变了。**
