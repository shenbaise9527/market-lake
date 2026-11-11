# 第 6 周 · DuckDB：把 SQL 打到 Parquet 上

**阶段** S3 ｜ **时长** 9h ｜ **前置** W2/W3（写入参数已定）

## 本周目标

1. 五个行情核心查询能写、能优化、能读执行计划。
2. 知道 DuckDB 的能力边界在哪里，什么时候必须换引擎。
3. 掌握 ASOF JOIN —— 行情数据处理里最重要的算子，没有之一。

## 阅读（约 2h）

- DuckDB 的 `read_parquet` 参数全集（`hive_partitioning`、`union_by_name`、`filename`、`file_row_number`）
- `EXPLAIN ANALYZE` 输出结构：每个算子的 rows / time / 扫描量
- ASOF JOIN 语义（`>=` 是 backward，`<=` 是 forward）

## 动手（约 6h）

1. 跑 `py/q_duckdb.py`，逐个看 5 个查询的执行计划。
2. **复现优化器坑**：直接对 `read_parquet + WHERE symbol=...` 做 ASOF JOIN，
   计时（会很慢）；再物化成临时表做同样的事，对比耗时。把原因写清楚：
   基数估计失真 → 计划退化为嵌套循环。
3. **三目标调优**：对同一个目录
   - 点查：`symbol + ts 区间`
   - 全扫聚合：`sum/avg`
   - 分钟聚合：`group by` + 时间桶

   分别记录耗时随 `threads`（1/2/4/8）、`memory_limit`（2G/8G）的变化，找出 spill 拐点。
4. **写出**：用 `COPY (...) TO 'lake/data/derived/bars_1m/dt=.../part-0.parquet' (FORMAT parquet, COMPRESSION zstd)`，
   确认写出后能被同一个 glob 读到（分区自洽）。
5. **并发**：开两个进程，一个持续追加写新分区，一个持续查询。记录是否报错、读到什么。

## 验收

- 五个查询的结果与 Polars 版完全一致（下周做，先自己核对一遍 bar 行数）。
- 能说出：DuckDB 在什么情况下会成为瓶颈（提示：不是"数据量大"）。
- ASOF JOIN 在物化后 ≤ 50ms（单合约、单日）。
- 填 cheatsheet「引擎对比」的 DuckDB 列。

## 常见坑

- 分区键与文件内同名列冲突会直接报错（所以本仓库的 `exchange` 只放在路径里）。
- `hive_partitioning` 会把 `dt=2026-11-05` 推断成 DATE 类型，字符串比较会报类型错。
- DuckDB 是单写多读：不适合当"几十个研究员同时查"的服务端。
- `ORDER BY` 在大结果集上很贵，能靠 `QUALIFY` / 窗口解决就别排序。

## 交付物

- `notes/week06.md`：五个查询的优化前后耗时表 + 优化器坑的复现记录
- `py/q_duckdb.py` 里新增你写的两个查询（比如「每合约日内最大回撤」或「十档失衡度」）
