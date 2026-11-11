# 第 1 周 · 基础认知与工作台

**阶段** S0 ｜ **时长** 8h ｜ **前置** 无

## 本周目标

1. 能用自己的话解释「列存为什么比行存快」，指出快在 IO 还是 CPU。
2. 跑通仓库里全部命令，环境不再是障碍。
3. 看懂 `mdlake inspect` 输出的每一个数字。

## 阅读（约 3h，顺序就是推荐阅读顺序）

> **中文优先路线**：下面每项都标了中文替代，二选一即可。规范类（#1）建议中英对照，
> 其余读中文版完全够用；英文原版作为"以后的默认阅读材料"留着，毕竟一手资料都是英文的。

1. **[Parquet File Format](https://parquet.apache.org/docs/file-format/) — 只读 *Concepts* 一节（约 30min）**
   编码细节一概不看（那是 W2 的事）。读完要能画出四层结构
   `file → row group → column chunk → page`，并留意两个设计决策：
   元数据写在文件末尾（支持单遍写入）、读数据前先读 footer（这是点查快的根源）。
   **中文先导**：[DuckDB：列式存储与 Parquet 解析](http://mp.weixin.qq.com/s?__biz=MzUwOTU4OTU2NQ==&mid=2247483675&idx=1&sn=ae0be589fe1303d940eb92197bc28b66)（《DuckDB:从上手到内核》系列）
   有逐层 ASCII 图解和带数字的实测，先读它建立直觉，再扫英文 Concepts 对术语。
2. **[CSV Files: Dethroning Parquet — or Not?](https://duckdb.org/2024/12/05/csv-files-dethroning-parquet-or-not.html)（约 30min）**
   DuckDB 官方实测 CSV vs Parquet，和本周 `w01_compare.py` 是同一件事。
   带两个问题读：作者实测差了几个数量级、差在哪个环节？什么场景下 CSV 反而是合理选择？
   （上面那篇公众号文章的"动手验证"部分就是它的中文同类实验，可互为替代。）
3. **向量化执行：[MonetDB/X100 论文 §1–§3](https://www.cidrdb.org/cidr2005/papers/P19.pdf)（约 60min，跳读）**
   只抓三件事：tuple-at-a-time 为什么慢（每个 tuple 一次虚函数调用 + interpreter 开销）、
   column-at-a-time 换来了什么（CPU cache line 被吃满）、压缩为什么是副产品。
   **中文替代（推荐先读）**：[从行到向量 —— 数据库执行引擎的进化之路](http://mp.weixin.qq.com/s?__biz=Mzk3NTE2NzU5NQ==&mid=2247488037&idx=1&sn=0d688a439e70db8dd5b16c92b9f3ec32)（OceanBase 社区）
   把 X100 的思想用中文讲透了：火山模型逐行调用的开销、cache 友好性、分支预测、SIMD，
   还带 TPC-H 实测数字（向量化后计算密集查询快 10 倍）。读它足够，X100 原文降为选读。
4. **[Apache Arrow and the "10 Things I Hate About pandas"](https://wesmckinney.com/blog/apache-arrow-pandas-internals/)（约 30min，选读）**
   只读内存布局对比的部分：pandas 字符串是 `PyObject` 指针数组 vs Arrow 连续 buffer。
   这篇是 W4 Arrow 内存模型的铺垫，现在读是为了理解"Arrow 为什么必须存在"。
5. **收尾（约 30min）**：回到 `README.md` 的分层图，不看文档说出
   Parquet / Arrow / DuckDB 各自负责什么、数据从一方流向另一方时以什么形态存在。

## 动手（约 4h）

1. 按 README「快速开始」跑完 7 条命令，全部跑通为准。
2. **对照实验**：把一天的 trades 导成 CSV，再用 DuckDB 分别查 CSV 和 Parquet：

   ```python
   # 三种查询目标，各测 3 次取最小值
   # a) 全扫聚合：SELECT sum(vol), avg(px) FROM ...
   # b) 点查：SELECT * FROM ... WHERE symbol='600267.SH' AND ts BETWEEN ... AND ...
   # c) 窄列扫描：SELECT avg(px) FROM ...
   ```

   记录：文件体积、三项耗时、`EXPLAIN ANALYZE` 里的 `Data Read`。
3. **只读 3 列为什么只花 1/9 IO**：读 `px`、`vol`、`ts` 三列的查询，看 `Data Read` 相对全表多少。
4. **改生成器参数**：`--rows 1000000 --symbols 1`，观察单文件大小、行组数、压缩比怎么变。
5. 用 `mdlake inspect` 看一个文件，逐行解释输出：行组是什么、为什么 symbol 列只有 105 字节、
   为什么 ts 列占了文件的一半。

## 验收

- `uv run python py/verify.py --week 1` 全绿。
- 在 `notes/week01.md` 里写下三个问题的答案，每个不超过 5 行：
  1. 列存快，主要快在哪一步？（提示：不是"压缩"，压缩只是副产品）
  2. 为什么 `ts` 列用 `RLE_DICTIONARY` 反而最占空间？
  3. 这个文件的 2.28x 压缩比，主要来自哪一列？
- 不看文档，能说出 Arrow / Parquet / DuckDB 三者各自的职责。

## 常见坑

- **不要用系统 `python`，一律 `uv run python`**。uv 环境的依赖在 `pyproject.toml` / `uv.lock`，系统解释器里什么都没有。
- Rust 的 `CARGO_TARGET_DIR` 被设到了别处，`rust/target/` 是空的，用 `cargo run --` 而不是找二进制。
- 别在这一周装 Spark / ClickHouse / Iceberg。工具不是瓶颈，理解才是。

## 交付物

- `notes/week01.md`（三个答案 + CSV/Parquet 对照表）
- 生成好的 `lake/data/raw/`（至少 2 天数据）
