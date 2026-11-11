# 十周学习计划

> 目标不是"学会四个库"，而是**能在 TB 级行情数据上做出正确的存储与查询决策，并说清为什么**。
> 每周 8–10 小时，共约 90 小时。

## 总览

| 周 | 阶段 | 主题 | 关键问题 | 小时 |
|---|---|---|---|---|
| 1 | S0 基础 | 列式存储、压缩、向量化；环境与合成数据 | 为什么列存比行存快？快在哪一步？ | 8 |
| 2 | S1 Parquet | 物理结构：行组、列块、页、编码、统计信息 | 一次查询到底读了文件的哪几 KB？ | 9 |
| 3 | S1 Parquet | 写入决策：排序键、分区、压缩、文件尺寸 | 同样数据，为什么体积差 5 倍、速度快 10 倍？ | 10 |
| 4 | S2 Arrow | 内存布局：buffer、validity、offsets、dictionary | 零拷贝到底省掉了哪一步？ | 9 |
| 5 | S2 Arrow | IPC / Flight / C Data Interface | 跨语言、跨进程传数据，怎么不序列化？ | 9 |
| 6 | S3 引擎 | DuckDB：下推、ASOF、窗口、内存与并发边界 | SQL 引擎什么时候帮不了你？ | 9 |
| 7 | S3 引擎 | Polars 惰性求值 + DataFusion | 同一件事三种写法，差在哪？ | 9 |
| 8 | S4 工程 | 增量写入、去重、分层 rollup、compaction | 每天新增 50GB，怎么保证不烂？ | 10 |
| 9 | S5 调优 | 基准方法论、实验矩阵、内存与并行 | 你的调参结论经得起复现吗？ | 9 |
| 10 | S6 实战 | 回放引擎 + 架构文档 + 复盘 | 能把它讲给别人听吗？ | 10 |

## 每周节奏（8–10 小时怎么切）

| 时段 | 内容 | 产出 |
|---|---|---|
| 第 1 段（3h） | 读任务卡指定的章节/规范 + 跑通示例 | 疑问清单 |
| 第 2 段（4h） | 做任务卡的动手项，改仓库代码 | 可运行的代码 + 数字 |
| 第 3 段（2h） | 填 `docs/cheatsheet.md`，跑 `verify.py` | 结论 + 全绿 |

**门禁**：`uv run python py/verify.py --week N` 全绿，且 cheatsheet 新增至少 2 条带数字的结论，
才允许进入第 N+1 周。宁可慢一周，不要带着模糊往下走。

---

## 分周详解

### 第 1 周 · 基础认知（S0）

- **读**：Parquet 官方 `File Format` 文档的 "Concepts" 一节；任意一篇讲列存 IO 摊还的文章。
- **做**：
  1. 跑通 README 里 7 条命令；
  2. 把同一份 tick 存成 CSV 和 Parquet，用 `duckdb` 各跑一次 `sum(vol)`，记录耗时与 IO；
  3. 用 `mdlake inspect` 看一个文件，把每个数字的含义写进笔记（行组数、编码、压缩比）。
- **验收**：能准确回答"为什么读取 3 列时 Parquet 只读了 1/9 的字节"。
- **坑**：别一上来就装一堆东西。本周只需要 pyarrow + duckdb + cargo。

### 第 2 周 · Parquet 物理结构（S1）

- **读**：Parquet 规范的 *File Layout*、*Encodings*、*Column Chunk / Page* 三节；
  `pyarrow.parquet.ParquetWriter` 的每个参数都要能说出它对文件的影响。
- **做**：
  1. 写一个脚本站析出一个文件的 **每个列块用了什么编码**（`mdlake inspect` 已给，自己用 pyarrow 再实现一遍）；
  2. 造 5 组数据：`ts` 用 `PLAIN` / `DELTA_BINARY_PACKED`，`px` 用 `PLAIN` / `BYTE_STREAM_SPLIT`，看体积；
  3. 验证统计信息剪枝：读一个**已排序**文件和一个**乱序**文件，同样点查，比较 `EXPLAIN ANALYZE` 的 `Rows Scanned`。
- **验收**：能解释 `BYTE_STREAM_SPLIT` 为什么对浮点价格有效，以及它对哪些数据无效。
- **坑**：`RLE_DICTIONARY` 对高基数列（时间戳、价格）是负优化，pyarrow 默认却会开——这是最常见的"默认值陷阱"。

### 第 3 周 · 写入决策（S1）

- **读**：`WriterProperties` 全参数；Hive 分区约定；小文件问题的经典讨论。
- **做**：
  1. 用 Rust `mdlake rewrite` 和 pyarrow 各写一遍「排序 + 分区 + zstd」，对比体积与写耗时；
  2. 实验：`row_group_size` 取 16k / 64k / 256k / 1M，画体积-扫描时间曲线（bench.py 已支持）；
  3. 实验：把 `ts` 从 int64 换成 `TIMESTAMP(ns)` 再写一次，比较体积与读取速度；
  4. 十档盘口两版：扁平 40 列 vs `LIST<STRUCT>`，量化差异。
- **验收**：给出你自己数据上的「row_group_size / 压缩 / 排序键」推荐值，并说明代价。
- **坑**：文件别小于 100MB，也别大于 2GB；按天分区 + 单合约单文件在 A 股会碎成几千个文件。

### 第 4 周 · Arrow 内存模型（S2）

- **读**：Arrow Columnar Format 规范（*Physical Memory Layout* 一节，重点是 validity bitmap 与 offsets）；
  arrow-rs 的 `RecordBatch` / `Array` 文档。
- **做**：
  1. Rust：手写一个定长 tick 二进制解析器 → 直接产出 `RecordBatch`（不经过 CSV/JSON）；
  2. Python：用 pyarrow 手工构造同样的 batch，逐一打印 buffer 布局；
  3. 造一个含 null 的数组，观察 validity bitmap 的字节；
  4. 把 `StringArray` 的 offsets 打印出来，理解为什么 2GB 是硬上限。
- **验收**：能画出 `Int64Array` 和 `StringArray` 的内存布局图，并解释 `slice` 为什么是 O(1)。
- **坑**：`slice` 零拷贝会让 offset 累积，长期持有小片段会拖住整块 buffer —— 要定期 `compact`。

### 第 5 周 · 跨进程与跨语言（S2）

- **读**：Arrow IPC 格式（stream vs file）、Flight / Flight SQL 的定位、C Data Interface。
- **做**：
  1. `py/cdata_demo.py` 跑通并解释地址相同意味着什么；
  2. 用 pyarrow 写一份 IPC stream，Rust 侧用 `arrow-ipc` 读进来，验证 schema 兼容；
  3. 起一个最小 Flight 服务，客户端按 `(symbol, ts range)` 拉数据，测端到端延迟；
  4. 对比同一次传输：Flight vs JSON vs CSV 的体积与耗时。
- **验收**：能说明「零拷贝的边界在哪里」——哪些操作一定逃不掉复制。
- **坑**：跨语言传 schema 时 utc 标记、`timezone` 字符串不一致会导致读出来时间偏移；务必统一用 `int64 ns` 落地，展示层再转。

### 第 6 周 · DuckDB（S3）

- **读**：DuckDB 的 Parquet 扫描与下推文档、ASOF JOIN 文档、`EXPLAIN ANALYZE` 输出解读。
- **做**：
  1. 五个查询各跑一遍（`q_duckdb.py`），每个都看 `EXPLAIN ANALYZE`；
  2. 复现本仓库发现的那个坑：对 `read_parquet + WHERE symbol=...` 直接 ASOF 会退化成嵌套循环，物化临时表后恢复；
  3. 调 `threads` 与 `memory_limit`，观察何时开始 spill；
  4. 同一个 bar 聚合，用 `COPY ... TO` 写成按 dt 分区的 Parquet。
- **验收**：能判断「什么时候 DuckDB 足够，什么时候必须换引擎」。
- **坑**：DuckDB 的并发模型是单写多读；把它当服务端给几十个用户查询会撞墙。

### 第 7 周 · Polars 与 DataFusion（S3）

- **读**：Polars 的 lazy / streaming 引擎说明、表达式 API、`sink_*` 系列；DataFusion 的 `TableProvider`。
- **做**：
  1. `q_polars.py` 跑通；把第 6 周的 5 个查询用 Polars 重写；
  2. 用 `sink_parquet` 处理一份**大于内存**的合成数据（造 20GB 或限制 `POLARS_MAX_THREADS`）；
  3. Rust：用 DataFusion 注册一个 Parquet 目录，跑同样的 bar 聚合；
  4. 三份实现的行数与结果必须完全一致 —— 不一致就是有 bug，不是"差不多"。
- **验收**：三语言/三引擎结果对齐 + 一张耗时对比表。
- **坑**：`group_by_dynamic` 要求按索引列有序，乱序 tick 必须先 sort —— 这正是真实数据的常态。

### 第 8 周 · 数据湖工程（S4）

- **读**：`docs/architecture.md`；Iceberg / Delta 的定位（知道什么时候需要、什么时候是过度设计）。
- **做**：
  1. 实现 raw → 1s → 1m 三层 rollup，重跑幂等（临时目录 + 原子 rename）；
  2. 写去重任务，键为 `(exchange, symbol, ts, seq, channel)`，输出重复率报告；
  3. 写 compaction：合并小于 50MB 的文件，按 symbol 聚簇，比较合并前后的点查延迟；
  4. 写质量报告：时间缺口、重复率、异常跳变、每日行数环比。
- **验收**：同一天数据跑两遍，目录内容完全一致（幂等）；compaction 后点查变快。
- **坑**：不要用「按 symbol 分目录」——A 股几千个合约会产生几千个目录和几万个小文件。按 `dt + exchange + hash bucket` 分。

### 第 9 周 · 性能调优（S5）

- **读**：无新资料，全靠实验设计。
- **做**：
  1. 固定数据集 + 固定查询集，建 benchmark harness（冷/热缓存分开测）；
  2. 跑满实验矩阵：编码 × 压缩 × 行组 × 排序，输出表；
  3. 针对三个目标分别调优：点查、全扫、聚合 —— 记录它们的冲突；
  4. 填满 `docs/cheatsheet.md`。
- **验收**：任意一条结论都能被 `py/bench.py` 复现；能说出"最优配置"为什么依赖查询模式。
- **坑**：不控缓存测出来的数字没有意义；单次测量没有意义；只测平均值没有意义（要看尾部）。

### 第 10 周 · 实战收口（S6）

- **做**：
  1. 写一个 C++ 或 Rust 的**单遍顺序回放读取器**：按 `(symbol, ts)` 顺序喂给策略回调，零拷贝、无全量加载；
  2. 跑一次端到端：生成 → 落盘 → 分层 → 查询 → 回放，记录每段耗时；
  3. 写 `docs/architecture.md` 的"我的决策"一节：为什么这样分区、为什么这个排序键、容量怎么估；
  4. 复盘：把十周的 cheatsheet 整理成一篇 2000 字的总结。
- **验收**：能向别人完整讲清这条链路，以及每个选择的代价。
- **坑**：回放不要 `collect()` 全量再遍历 —— 那就退化成了把数据湖当内存数据库用。

---

## 里程碑

| 节点 | 判定 | 意义 |
|---|---|---|
| M1（W3 末） | 能独立设计一个 tick 表的 schema 与分区方案 | 存储层过关 |
| M2（W5 末） | 能在三语言间零拷贝传数据 | 内存层过关 |
| M3（W7 末） | 三个引擎跑出完全一致的结果 | 计算层过关 |
| M4（W9 末） | 有可复现的调参结论 | 从"会用"到"会判断" |
| M5（W10 末） | 端到端管线 + 架构文档 | 可以上生产讨论 |

## 如果时间被压缩

按 5 周算（每周 15h）：W1+W2 合并、W4+W5 合并、W8 砍掉 Iceberg 部分、W9 只做点查与全扫两组实验。
**不要砍** W3（写入决策）和 W10（回放），这两周是唯一能把知识串起来的地方。
