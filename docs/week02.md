# 第 2 周 · Parquet 物理结构

**阶段** S1 ｜ **时长** 9h ｜ **前置** W1

## 本周目标

1. 给定一个查询，能**预测**它会读文件的哪几个行组、哪几个列块。
2. 把四种编码各自适合什么数据讲清楚（PLAIN / RLE_DICTIONARY / DELTA_BINARY_PACKED / BYTE_STREAM_SPLIT）。
3. 会用 column statistics 与 page index 做剪枝。

## 阅读（约 3h）

- Parquet 规范：*File Layout*、*Row Group*、*Column Chunk*、*Page*、*Encodings*
- `pyarrow.parquet.ParquetWriter` / `write_table` 的全部参数（签名就是知识点）
- 本仓库 `rust/src/inspect.rs`（60 行，读完能自己写一遍）

## 动手（约 5h）

1. **重写 inspect**：用 pyarrow 实现和 `mdlake inspect` 等价的输出
   （`pq.ParquetFile(path).metadata`、`row_group(i).column(j)`、`statistics`）。
   要求打印：行组行数、每列编码、压缩算法、压缩后大小、min/max。
2. **编码实验**（核心任务）：同一份数据写 6 个版本，填体积表

   | 变量 | 取值 |
   |---|---|
   | `ts` | PLAIN / DELTA_BINARY_PACKED |
   | `px` | PLAIN / BYTE_STREAM_SPLIT |
   | `symbol` | dictionary on / off |

   提示：pyarrow 的 `column_encoding={'px': 'BYTE_STREAM_SPLIT'}` 要求该列 `use_dictionary=False`。
3. **剪枝实验**：准备「已按 (symbol, ts) 排序」和「乱序」两个文件，
   跑同一个点查，读 `EXPLAIN ANALYZE` 里的 `Rows Scanned` 与 `File Filters`。
   目标：排序后的扫描行数下降 10 倍以上。
4. **行组大小实验**：`row_group_size` 取 16384 / 65536 / 262144 / 1048576，
   测点查与全扫耗时，画一张表。
5. **开 page index**：`write_page_index=True`，比较文件体积与点查耗时（page 级剪枝更细）。

## 验收

- `uv run python py/verify.py --week 2` 全绿（乱序检测项）。
- 填完 `docs/cheatsheet.md` 的「Parquet 编码」一节，至少 6 行。
- 能回答：为什么 `BYTE_STREAM_SPLIT` 对「两位小数的价格」有效，但对「随机浮点」几乎无效？
- 能回答：一个 1GB 文件、行组 128MB，点查命中一个行组时，最少要读多少字节？

## 常见坑

- **默认值陷阱**：pyarrow 默认对几乎所有列开字典编码，对时间戳/价格是负优化。
- 字典页（dictionary page）和数据页是分开的，`encodings` 里出现 `PLAIN_DICTIONARY` 不代表整列都用字典。
- `min/max` 统计对 float 是**近似**语义（有 `min_value_exact` 标记），别拿它做精确断言。
- 行组的 min/max 是按列算的，多列查询要同时命中所有列才剪得掉。

## 交付物

- `py/inspect_pyarrow.py`（自己写的元数据解析器）
- `notes/week02.md`：编码对比表 + 剪枝实验记录 + 两个问题的答案
