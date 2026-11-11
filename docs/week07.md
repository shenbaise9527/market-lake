# 第 7 周 · Polars 与 DataFusion

**阶段** S3 ｜ **时长** 9h ｜ **前置** W6

## 本周目标

1. 用 Polars 惰性 API 重写 W6 的五个查询，结果必须完全一致。
2. 跑一次「数据大于内存」的流式聚合。
3. 在 Rust 里用 DataFusion 搭一个同样的查询层。

## 阅读（约 2h）

- Polars 的 lazy 优化阶段说明（predicate / projection pushdown、slice pushdown、common subplan elimination）
- `sink_parquet` 的流式语义与内存上限
- DataFusion 的 `SessionContext`、`TableProvider`、`ListingTable`

## 动手（约 6h）

1. 跑 `py/q_polars.py`，把 `explain()` 的输出和 DuckDB 的 `EXPLAIN` 对照，看两边下推的差异。
2. **重写五个查询**（bar 聚合 / 去重 / ASOF / 脏数据 / 点查），
   要求结果与 DuckDB 版逐行一致：比较 `count(*)` 和一个 `sum(px*vol)` 的 checksum。
3. **流式实验**：把 `--rows` 调到总量的 10 倍（或加 `POLARS_MAX_THREADS=2` 限制内存），
   用 `sink_parquet` 完成 bar 聚合，记录峰值内存。再试 `collect()` 版本，看它什么时候崩。
4. **DataFusion**：在 `rust/src/` 加一个子命令 `sql`，用 DataFusion 注册 Parquet 目录，
   执行同样的 bar 聚合，打印耗时。
5. **三引擎对比表**：四类查询 × 三个引擎（DuckDB / Polars / DataFusion），
   同一份数据、热缓存、3 次取最小值。填进 cheatsheet。

## 验收

- 三引擎在四类查询上的结果**完全一致**（行数 + 数值 checksum 相等）。
- 流式版本处理 10 倍数据不 OOM。
- 能解释 Polars 的 `group_by_dynamic` 为什么要求输入有序，以及不排序会报什么错。
- 能说出 DataFusion 相比 DuckDB 的优势（可嵌入、可扩展算子、Rust 生态）与代价（要自己写的东西多）。

## 常见坑

- `group_by_dynamic` 前必须 `.sort()`，乱序 tick 会直接报 `input data is not sorted`。
- hive 分区键会被识别成 `Date`，用字符串比较会报类型错，要用 `pl.date(...)`。
- `sink_parquet` 是流式的，但上游若有 `sort` 就会退化成全内存排序——想清楚再写。
- DataFusion 的默认并行度按 CPU 核数走，容器里要显式限制，否则会被 OOM kill。

## 交付物

- `rust/src/sql.rs`（DataFusion 查询子命令）
- `notes/week07.md`：三引擎对比表 + 流式内存记录 + 结果一致性校验方法
