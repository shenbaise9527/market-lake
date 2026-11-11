# 第 3 周 · 写入决策：排序、分区、压缩、文件尺寸

**阶段** S1 ｜ **时长** 10h ｜ **前置** W2

## 本周目标

1. 三种语言各写一遍 tick 写入器，参数可控。
2. 给出**你自己数据上**的推荐配置（不是抄来的最佳实践）。
3. 理解小文件问题为什么是数据湖的头号杀手。

## 阅读（约 2h）

- Hive 分区约定（`key=value` 目录）
- `WriterProperties` 全参数（Rust 与 C++ 语义一致）
- 本仓库 `docs/architecture.md` 第 2、3 节

## 动手（约 6h）

1. **Rust 写入器**：`mdlake rewrite` 已可用，先读懂 `rust/src/writer.rs` 的排序与属性构造，
   然后加两个参数：`--compression-level`、`--bucket N`（按 `hash(symbol)%N` 分桶写多个文件）。
2. **Python 写入器**：用 pyarrow 实现同功能（排序 + 分区 + 分桶 + zstd），
   输出到 `lake/data/curated/`，并打印每个文件的行数与体积。
3. **矩阵实验**：第 3 章已用 `py/w03_writer.py` 跑完 E0–E4（体积 / 读放大 / 耗时，
   见 `reports/w03-writer.md`）。你的任务：把脚本里的 `SRC` 改成**你自己的数据**重跑一遍，
   与第 3 章数字逐项对比，差异超过 20% 的项写清原因，把推荐配置填进 cheatsheet。
4. **小文件量化**：把一个 300MB 文件拆成 300 个 1MB 文件，跑同一个点查，比较延迟与
   `EXPLAIN ANALYZE` 里的文件打开数量。
5. **十档盘口两版**：`book` 表扁平 40 列 vs `LIST<STRUCT<px,vol>>`（5 元素），
   比较体积、`avg(bid_px1)` 的耗时、代码复杂度。

## 验收

- `uv run python py/verify.py --week 3` 全绿（Rust 工具项）。
- 产出一张**推荐配置表**：日增行数 → 文件数 / 单文件大小 / row_group / 压缩 / 排序键。
- 每天每交易所文件数 ≤ 32（用 `find ... | wc -l` 数）。
- 能解释：为什么「按 symbol 分目录」在 A 股场景是灾难。

## 常见坑

- **排序要在写之前做完**：Parquet 不可变，写完再排序等于重写。
- 分区键不要再存成一列（重复占空间，且和 Hive 分区冲突）。
- zstd 级别 19 在 tick 数据上收益很小、写耗时翻倍，别默认开。
- 分桶数不要太多：16 个桶 × 3000 合约 × 每天 = 每天 16 个文件，刚好。

## 交付物

- `py/write_curated.py`（Python 写入器）
- Rust 写入器新增参数
- `notes/week03.md`：推荐配置表 + 小文件实验数据 + 十档两版对比
