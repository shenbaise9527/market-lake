# 第 8 周 · 数据湖工程：增量、去重、分层、compaction

**阶段** S4 ｜ **时长** 10h ｜ **前置** W3/W6/W7

## 本周目标

1. 一条**幂等**的写入链路：重跑同一天，结果字节级一致。
2. raw → 1s → 1m 三层派生，且能单独重算任一层。
3. 一个能跑的 compaction 任务和一份质量报告。

## 阅读（约 2h）

- `docs/architecture.md` 第 4–7 节（写入幂等、去重、compaction、要不要 Iceberg）
- 任意一篇讲 "small files problem" 的工程文，重点是元数据开销的量级

## 动手（约 6h）

1. **幂等写入**：`py/etl_raw.py`
   - 先写 `lake/_tmp/<job_id>/`，`fsync` 后原子 rename 到目标分区
   - 目标分区已存在则整体替换（不是追加）
   - 结束时写 `_manifest/dt=.../meta.json`：行数、ts 范围、源文件列表、参数、耗时
   - **验证**：连跑两次，`sha256sum` 全部文件（时间戳字段除外）应一致
2. **去重 + 质量报告**：`py/etl_quality.py`
   输出每个 (dt, symbol)：重复行数、乱序修正数、序号断档数、异常跳变数、行数环比。
   要求所有清洗动作**都计数**，不许静默丢数据。
3. **分层 rollup**：`py/etl_bars.py` 生成 1s 和 1m 两层。
   验证：1m 由 1s 聚合 与 1m 由 tick 直接聚合，结果必须一致（这是常见的逻辑 bug 来源）。
4. **compaction**：`py/etl_compact.py`
   触发条件：分区内文件数 > 32 或平均文件 < 50MB → 合并重排。
   记录合并前后的：文件数、总字节、点查延迟。
5. **容量估算**：按你的真实数据规模填 cheatsheet 的「容量与成本」表。

## 验收

- `uv run python py/verify.py --week 8` 全绿（分区裁剪项）。
- 同一天数据跑两遍，除 manifest 的时间戳外，文件内容哈希一致。
- 1m 层两种来源（tick 直算 / 1s 聚合）结果完全一致。
- compaction 后单分区文件数 ≤ 8，点查 P50 下降。
- 质量报告覆盖五个指标，且与生成器注入的脏数据量对得上（重复率 ≈ 0.5%，异常 ≈ 0.01%）。

## 常见坑

- 直接 `write` 到目标分区，中途失败 = 半个分区，后续查询结果不可信。
- 「按 symbol 分目录」在 A 股会产生几千个目录，`list` 一次几秒钟。
- 去重键漏了 `channel`，会把不同通道的同一时刻成交误删。
- 用 `dt` 做分区却在文件里也存了 `dt` 列，DuckDB/Polars 会冲突或产生重复列。
- compaction 没有幂等保护，并发跑两次会互相覆盖。

## 交付物

- `py/etl_raw.py`、`py/etl_bars.py`、`py/etl_quality.py`、`py/etl_compact.py`
- `reports/quality-YYYY-MM-DD.md`
- `notes/week08.md`：幂等验证记录 + 1m 一致性验证 + compaction 前后对比
