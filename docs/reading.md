# 资料清单

只列"值得读完整篇"的，按周排。读的时候带着任务卡里的问题去读，不要通读。
（链接均在 2026-11 核实可访问；DuckDB 博客偶尔改版换 URL，404 了就站内搜标题。）

## W1（S0 基础认知）

| 资料 | 读哪几节 | 目的 |
|---|---|---|
| [Parquet File Format](https://parquet.apache.org/docs/file-format/) | *Concepts* 一节 | 四层结构 + 单遍写/先读 footer 两个设计决策 |
| [CSV Files: Dethroning Parquet — or Not?](https://duckdb.org/2024/12/05/csv-files-dethroning-parquet-or-not.html) | 全文 | 官方版 CSV vs Parquet，配合 `w01_compare.py` |
| [MonetDB/X100 (CIDR 2005)](https://www.cidrdb.org/cidr2005/papers/P19.pdf) | 只读 §1–§3 | 列式 + 向量化执行出处：tuple-at-a-time 慢在哪、cache line |
| [Arrow and the "10 Things I Hate About pandas"](https://wesmckinney.com/blog/apache-arrow-pandas-internals/) | 内存布局对比部分 | Arrow 存在的理由，W4 的铺垫 |

### W1 中文替代（2026-11 核实）

| 中文资料 | 替代哪项 | 备注 |
|---|---|---|
| [DuckDB：列式存储与 Parquet 解析](http://mp.weixin.qq.com/s?__biz=MzUwOTU4OTU2NQ==&mid=2247483675&idx=1&sn=ae0be589fe1303d940eb92197bc28b66)（公众号《DuckDB:从上手到内核》#5） | Parquet Concepts 的先导 | 逐层 ASCII 图解 + Zone Map/谓词下推实测；公众号链接可能失效，失效就搜标题 |
| [从行到向量 —— 数据库执行引擎的进化之路](http://mp.weixin.qq.com/s?__biz=Mzk3NTE2NzU5NQ==&mid=2247488037&idx=1&sn=0d688a439e70db8dd5b16c92b9f3ec32)（公众号"老纪的技术唠嗑局"） | X100 论文 §1–§3 | 火山模型 vs 向量化、cache、分支预测、SIMD，带 TPC-H 数字；读它可免读论文 |

## 规范（这几份必须读原文，二手解读都有偏差）

| 资料 | 读哪几节 | 周 |
|---|---|---|
| [Parquet File Format](https://parquet.apache.org/docs/file-format/) | File Layout / Row Group / Column Chunk / Page；Encodings 全部 | W2 |
| [Parquet Encodings 详解](https://github.com/apache/parquet-format/blob/master/Encodings.md) | PLAIN、RLE/Bit-Packing、DELTA_BINARY_PACKED、BYTE_STREAM_SPLIT | W2 |
| [Arrow Columnar Format](https://arrow.apache.org/docs/format/Columnar.html) | Physical Memory Layout、Validity Bitmaps、Variable-size Binary、Dictionary-encoded | W4 |
| [Arrow IPC Format](https://arrow.apache.org/docs/format/Columnar.html#serialization-and-interprocess-communication-ipc) | Stream vs File、RecordBatch 封装、schema 平铺 | W5 |
| [Arrow Flight](https://arrow.apache.org/docs/format/Flight.html) | 概念即可（DoGet/DoPut、Ticket） | W5 |
| [Arrow C Data Interface](https://arrow.apache.org/docs/format/CDataInterface.html) | struct 定义、release 回调语义 | W5 |

读规范的方法：先读概念图，再看字段定义，最后回到本仓库 `mdlake inspect` 的输出对照。
看不懂的字段先跳过，等第 2 周实验做完再回来看，会突然全通。

## 官方文档（按需查，不要通读）

- **pyarrow**：`pyarrow.parquet.ParquetWriter` 参数表、`pyarrow.compute` 的 kernel 列表、
  `pyarrow.dataset` 的 scanner 与下推。重点看 "Buffered IO" 与 "Memory pools" 两节。
- **polars**：`LazyFrame` 的 optimization 章节、`group_by_dynamic`、`join_asof`、`sink_parquet`。
  版本更新快，优先看 docstring 而不是博客。
- **duckdb**：Parquet 扩展（`read_parquet` 参数全集）、`EXPLAIN ANALYZE` 输出结构、
  ASOF JOIN、`SET` 参数（threads / memory_limit / preserve_insertion_order）。
- **arrow-rs**：`arrow::compute::kernels` 索引、`parquet::arrow::ArrowWriter` 与 `WriterProperties`、
  `ParquetRecordBatchReaderBuilder`。docs.rs 上直接搜方法名最快。
- **arrow C++**：`parquet::arrow::FileReader` / `FileWriter`、`arrow::compute` 的 `CallFunction` 风格。

## 文章与博客（建立判断力用）

- DuckDB 官方博客的 Parquet 系列（尤其讲压缩与下推的几篇）
- "Apache Arrow and the 10 Things I Hate About pandas"（Wes McKinney）—— 理解 Arrow 的设计动机
- Parquet 的 `BYTE_STREAM_SPLIT` 提案讨论（理解浮点列为什么难压）
- 任何一篇讲 "small files problem" 的工程文（W8 会用到）

## 论文（选读，W9 想深入再看）

- **Dremel**（Parquet 的列式嵌套编码来源）：只读第 3、4 节，理解 repetition/definition levels
- **Monarch / Velox**：向量化执行引擎的调度模型，理解"为什么列存必须配向量化"

## 反面教材（一定要看）

- 用 pandas 处理 10GB tick 的各类"优化技巧"——大部分是在教你把内存榨干的错误方向
- 把 Parquet 当数据库用的教程（`WHERE` 不带分区键，每次全扫）——学会识别这类反模式

## 查询与验证

- 遇到行为不确定时，先写 5 行代码验证，再查文档。这个习惯比任何资料都值钱。
- 每个"听说"的结论，都要在 `docs/cheatsheet.md` 里留一条带数字的记录，否则删掉。
