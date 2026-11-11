# 第 7 章 · Polars：惰性求值、动态分组与流式写出的边界

!!! abstract "本章结论（读完你应该能复述这三句）"
    1. 同一个"单合约分钟 bar + VWAP"，**Polars 7.7 ms、DuckDB 27.1 ms、pandas 77.2 ms**。差距不在语言，在**是否把过滤条件下推到扫描**：Polars 的 `scan_parquet` 让 `dt`/`symbol` 变成扫描节点里的 `SELECTION`，pandas 只能全读再过滤（13.9 → 6.7 ms）。
    2. `group_by_dynamic` 要求输入按时间有序，否则**直接报错**（`ComputeError: input data is not sorted`）——这是好事。旧版本会静默产出重复桶，那种 bug 比报错难查十倍。
    3. `sink_parquet` 不是"免内存"：带全局 `sort` 时要 **+584 MB**，反而比 `collect` 后写（+259 MB）更多。真正省内存的是**去掉全局排序**（+110 MB）。流式的边界是"算子是否全局"，不是"函数名叫 sink"。

对应任务卡：仓库内 `docs/week07.md` ｜ 本章实验脚本：`py/w07_polars.py` ｜ 原始结果：`reports/w07-polars.md`
日常 API 怎么写：[附录 · Polars 常用方法速查](appendix/polars-cookbook.md)

---

## 1. 急切与惰性：两条不同的执行路径

同一份 Parquet，两种写法走的是完全不同的路：

<figure class="diagram">
<svg width="100%" viewBox="0 0 680 208" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
<title>急切读取与惰性下推的执行路径</title>
<desc>read_parquet 先把所有列全部读进内存再过滤；scan_parquet 把过滤与投影下推到扫描节点，只读命中的行组与列。</desc>
<text x="0" y="14" font-size="13.2" font-weight="500" fill="currentColor">pandas / read_parquet：先搬进来再筛</text>
<g stroke="currentColor" stroke-opacity="0.35" fill="none">
<rect x="0" y="26" width="132" height="40"/>
<rect x="152" y="26" width="132" height="40"/>
<rect x="304" y="26" width="132" height="40"/>
<rect x="456" y="26" width="132" height="40"/>
</g>
<g font-size="10" text-anchor="middle" fill="currentColor">
<text x="66" y="43">读全部 9 列</text><text x="66" y="58" fill-opacity="0.6">8 个文件</text>
<text x="218" y="43">过滤 dt/symbol</text><text x="218" y="58" fill-opacity="0.6">在内存里筛</text>
<text x="370" y="43">计算 m / 聚合</text><text x="370" y="58" fill-opacity="0.6">全量参与</text>
<text x="522" y="43">结果 239 行</text><text x="522" y="58" fill-opacity="0.6">13.9 ms</text>
</g>
<g stroke="currentColor" stroke-opacity="0.45" fill="none">
<path d="M134 46 h14"/><path d="M286 46 h14"/><path d="M438 46 h14"/>
</g>
<g fill="currentColor" fill-opacity="0.5">
<path d="M148 46 l-6 -3 l0 6 z"/><path d="M300 46 l-6 -3 l0 6 z"/><path d="M452 46 l-6 -3 l0 6 z"/>
</g>
<text x="0" y="90" font-size="13.2" font-weight="500" fill="currentColor">Polars scan_parquet：把条件塞进扫描节点</text>
<g stroke="currentColor" stroke-opacity="0.35" fill="none">
<rect x="0" y="102" width="196" height="56"/>
<rect x="216" y="102" width="132" height="56"/>
<rect x="368" y="102" width="132" height="56"/>
<rect x="520" y="102" width="132" height="56"/>
</g>
<g font-size="10" text-anchor="middle" fill="currentColor">
<text x="98" y="120" font-weight="500">Parquet SCAN</text>
<text x="98" y="136" fill-opacity="0.7">SELECTION 下推</text>
<text x="98" y="150" fill-opacity="0.7">PROJECT 5/9 列</text>
<text x="282" y="120">只读命中行组</text><text x="282" y="136" fill-opacity="0.6">146,750 行</text>
<text x="434" y="120">聚合</text><text x="434" y="136" fill-opacity="0.6">239 个桶</text>
<text x="586" y="120">结果</text><text x="586" y="136" fill-opacity="0.6">6.7 ms</text>
</g>
<g stroke="currentColor" stroke-opacity="0.45" fill="none">
<path d="M198 130 h14"/><path d="M350 130 h14"/><path d="M502 130 h14"/>
</g>
<g fill="currentColor" fill-opacity="0.5">
<path d="M212 130 l-6 -3 l0 6 z"/><path d="M364 130 l-6 -3 l0 6 z"/><path d="M516 130 l-6 -3 l0 6 z"/>
</g>
<line x1="0" y1="174" x2="680" y2="174" stroke="currentColor" stroke-opacity="0.15"/>
<text x="0" y="190" font-size="11" fill="currentColor" fill-opacity="0.8">区别只有一处：过滤发生在"读字节之前"还是"读进内存之后"。前者省 IO，后者只省 CPU。</text>
<text x="0" y="204" font-size="11" fill="currentColor" fill-opacity="0.8">实测 13.9 ms → 6.7 ms（2.1×），且内存峰值差了整整一份全表。</text>
</svg>
</figure>

`lf.explain()` 能直接看到下推结果（本机 polars 2.1.0）：

```text
simple π 3/3 ["ts", "px", "vol"]
  Parquet SCAN [dt=2026-11-05/exchange=SH/part-600267.SH.parquet, ... 3 other sources]
  PROJECT 5/9 COLUMNS
  SELECTION: (col("dt") == 2026-11-05) & (col("symbol") == "002322.SZ")
  ESTIMATED ROWS: 293500
```

四行信息量很大：

- `PROJECT 5/9 COLUMNS`：9 列里只读 5 列（3 个 select 列 + 2 个过滤列，其中 `dt`/`symbol` 来自分区键）。
- `SELECTION`：两个过滤条件都进了扫描节点，不是读完再筛。
- `4 other sources`：文件级剪枝已经生效——`dt=2026-11-05` 直接排除了另外 4 个文件。
- `ESTIMATED ROWS: 293500`：这是**分区级**估计（4 个文件 × 73,375），不是过滤后的行数。别拿它当结果行数。

## 2. 同一个查询，三种写法

查询：单合约、单日、按分钟聚合，输出 `bars / volume / vwap`。

```python
# Polars：声明式，过滤与投影自动下推
(pl.scan_parquet(TRADES, hive_partitioning=True)
   .filter((pl.col("dt") == pl.date(2026, 11, 5)) & (pl.col("symbol") == "002322.SZ"))
   .with_columns((pl.col("ts") // 60_000_000_000 * 60_000_000_000).alias("m"))
   .group_by("m")
   .agg(pl.len().alias("trades"), pl.col("vol").sum().alias("volume"),
        (pl.col("px") * pl.col("vol")).sum().alias("notional"))
   .select(pl.len().alias("bars"), pl.col("volume").sum(),
           (pl.col("notional").sum() / pl.col("volume").sum()).alias("vwap"))
   .collect())
```

| 实现 | 中位耗时 | 相对 Polars | 结果 |
|---|---|---|---|
| pandas（`read_parquet` + `groupby.agg`） | 77.2 ms | 10.0× | 239 bars / 33,429,400 / 4.4989 |
| DuckDB（SQL） | 27.1 ms | 3.50× | 同上 |
| **Polars（lazy）** | **7.7 ms** | 1.00× | 同上 |

三个数字要这么读：

**① pandas 慢在"全读 + 逐组 Python 调用"。** 77 ms 里，读 Parquet 约 12 ms，剩下的是一次全表拷贝 + 239 次分组函数调用。pandas 3.x 的 `groupby.agg` 已经向量化了不少，但它仍然是"先物化整表"的模型。

**② DuckDB 慢在"启动 + 解析 SQL + 冷启动"。** 27 ms 里包含连接初始化与查询计划，数据只有 587,000 行时这部分占比很高。数据量上去之后 DuckDB 会反超（第 9 章的扫描测试会看到），**这是"固定开销 vs 边际成本"的交叉点问题，不是引擎优劣**。

**③ Polars 快在"少读 + 无 Python 逐行"。** 7.7 ms 里没有一次 Python 层的循环，整个计划编译成 Rust 执行。

但**不要因此得出"Polars 全面更快"**：这台机器上数据只有 24 MB，全部在页缓存里。换成 100 GB、多用户并发、需要 join 五张表时，DuckDB 的优化器（第 6 章那些剪枝与连接算法）会体现价值。选型看的是"查询形状 × 数据规模 × 并发"，不是单条基准。

## 3. `group_by_dynamic`：必须有序，而且是硬要求

```python
raw.group_by_dynamic("t", every="1m", group_by="symbol").agg(...)
# ComputeError: input data is not sorted
```

报错信息很直白。**这不是限制，而是保护**：动态分组是按相邻行切窗口的流式算子，它假设输入按时间单调。乱序输入下，同一个时间窗会被切成多个碎片，产出**重复的桶**——结果不报错、但数值全错。

正确写法是先排序（单日单合约，3.7 ms，239 个桶）：

```python
raw.sort(["symbol", "t"]).group_by_dynamic("t", every="1m", group_by="symbol")
```

一个容易踩的细节：`group_by_dynamic` 的 `every="1m"` 是**按时间戳对齐到整分钟**，和 DuckDB 的 `time_bucket` 一样以 epoch 为原点。开盘 09:30:00.074 的第一笔会落在 `09:30:00` 这个桶里，桶内只有 0.93 秒的数据——做 VWAP 时要意识到这一点（第 6 章 §4 讲过同一个坑）。

## 4. `join_asof` 与 DuckDB `ASOF JOIN`：语义默认值不同

```python
bars.join_asof(q, on="ts", by="symbol", strategy="backward")
```

| | Polars `join_asof` | DuckDB `ASOF JOIN` |
|---|---|---|
| 默认连接类型 | **LEFT**（无匹配保留，右表列置 null） | **INNER**（无匹配丢弃） |
| 实测行数 | 957 行（719 行 `bid_px1` 为 null） | 238 行 |
| 实测耗时 | 1.3 ms | 14.4 ms |
| 前置要求 | 两边都按 `on` 列排序（否则警告/结果不可靠） | 右边需有统计信息（第 6 章） |

**957 vs 238 不是 bug，是默认语义不同。** 8 个合约里只有 1 个有盘口数据，Polars 保留全部 957 个 bar（其余 719 个的盘口列为 null），DuckDB 直接丢掉 719 行。做"成交对齐盘口"时，你要的几乎总是 LEFT（保留所有成交，缺盘口的标记出来），所以 Polars 的默认值更贴合业务；DuckDB 需要显式 `LEFT ASOF JOIN`。

耗时差 11 倍也有解释：DuckDB 那次要把两张表从 Parquet 读出来（14.4 ms 含扫描），Polars 的 `bars`/`q` 是已经物化的 DataFrame。**别拿这两个数字当引擎对比**——它们测的不是同一件事。

## 5. `sink_parquet`：流式的边界是"算子是否全局"

5,000,000 行合成数据（内存中 134 MB），三种写出方式：

| 写法 | 耗时 | RSS 增量 | 输出 |
|---|---|---|---|
| `scan → filter → sink` | 20 ms | **+110 MB** | 6.9 MB |
| `scan → sort → sink` | 102 ms | **+584 MB** | 13.5 MB |
| `collect → write_parquet` | 99 ms | +259 MB | 13.6 MB |

<figure class="diagram">
<svg width="100%" viewBox="0 0 680 190" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
<title>三种写出方式的内存增量</title>
<desc>过滤后直接写出只用一百一十兆；带全局排序的 sink 要五百八十四兆，反而高于先 collect 再写的二百五十九兆。</desc>
<text x="0" y="14" font-size="13.2" font-weight="500" fill="currentColor">sink 不等于省内存：带全局排序时它更贵</text>
<g font-size="10.5" fill="currentColor">
<text x="0" y="48" font-weight="500">filter → sink</text><text x="0" y="62" fill-opacity="0.6">无全局算子</text>
<text x="0" y="96" font-weight="500">sort → sink</text><text x="0" y="110" fill-opacity="0.6">全局排序</text>
<text x="0" y="144" font-weight="500">collect → write</text><text x="0" y="158" fill-opacity="0.6">先落内存</text>
</g>
<g>
<rect x="128" y="36" width="110" height="22" fill="#1D9E75" fill-opacity="0.45"/>
<rect x="128" y="84" width="584" height="22" fill="#EF9F27" fill-opacity="0.5"/>
<rect x="128" y="132" width="259" height="22" fill="currentColor" fill-opacity="0.22"/>
</g>
<g font-size="10.5" fill="currentColor">
<text x="246" y="52">+110 MB　20 ms</text>
<text x="246" y="100">+584 MB　102 ms</text>
<text x="395" y="148">+259 MB　99 ms</text>
</g>
<line x1="0" y1="170" x2="680" y2="170" stroke="currentColor" stroke-opacity="0.15"/>
<text x="0" y="185" font-size="11" fill="currentColor" fill-opacity="0.8">条形按 584 MB 等比。三个数字都含 Python 侧 134 MB 的源 DataFrame 之外的部分。</text>
</svg>
</figure>

结论要说得准确一点：

- **省内存的是"没有全局算子"**，不是 `sink_parquet` 这个函数。有 `sort` 时它必须把整份数据攒进内存做归并，`sink` 只是把"写"这一步改成流式。
- **`sort → sink` 比 `collect → write` 更贵**（584 vs 259 MB），因为 sink 的排序算子有自己的缓冲区，且不会像 `collect` 那样在写完后立刻释放。这个结果和"Polars 流式引擎万能"的流行说法相反——**在 5M 行这个规模，流式没有优势**。
- 真要验证 `sink` 的价值，数据必须**大于内存**（几十 GB），否则观察到的只是缓冲区开销。这也是本章唯一没做到的实验：本机没有这么大的数据，我不给外推结论。

实践建议：需要"过滤 + 投影 + 写出"时用 `sink_parquet`（+110 MB 确实省）；需要"全局排序 + 写出"时，先问自己**能不能不排序**——能的话把 `sort` 挪到下游按需处理，比换引擎有效得多。

## 6. DataFusion：什么时候值得上

Rust 的 DataFusion 和 Polars 定位不同，不是"更快的 Polars"：

| 维度 | Polars | DataFusion |
|---|---|---|
| 定位 | 单机 DataFrame 库 | 可嵌入的查询引擎（库/服务） |
| 接口 | 表达式 API | SQL + `SessionContext` + `TableProvider` |
| 扩展点 | 自定义表达式（有限） | 自定义 `TableProvider` / UDF / 优化规则 |
| 多用户 | 进程内 | 可包成服务，连接级会话 |
| 生态 | Python 一等公民 | Rust 一等公民，Python 绑定较弱 |

判断标准很实际，按顺序问：

1. **要不要暴露 SQL 给外部用户？** 要 → DataFusion（或 DuckDB，看并发模型）。
2. **要不要接自定义数据源（对象存储、自研格式、HTTP 分片）？** 要 → DataFusion 的 `TableProvider` 是标准扩展点，Polars 只能先落成文件。
3. **要不要在多用户服务里做查询隔离与资源控制？** 要 → DataFusion 的 `SessionContext` 天然按请求隔离；DuckDB 是单写多读，Polars 是进程内。
4. **只是单机脚本/分析？** → Polars，代码量最少。

!!! warning "本节未做实测"
    本机没有安装 DataFusion（cargo 依赖未缓存，编译需拉取大量 crate），所以**本章不给 DataFusion 的任何性能数字**。上面的判断标准来自 API 形态与工程约束，不是基准测试。想验证，按第 10 章的 Rust 工程加 `datafusion` 依赖即可，代码结构与本仓库的 `mdlake` 一致。

## 7. 三引擎结果对齐

同一件事（全市场单日分钟 bar）在三处算，必须完全一致：

```text
DuckDB: bars=957  vol=132,732,600
Polars: bars=957  vol=132,732,600
一致：True
```

**"差不多"是不可接受的**：浮点求和顺序不同会带来末位差异，但行数与总量必须严格相等。这条校验应该写进 CI——第 9 章的基准套件里，每个查询都带一个"结果指纹"（行数 + 几个聚合值），跑基准前先比指纹，不一致就拒绝出数。

## 8. 踩坑

| 坑 | 表现 | 正确做法 |
|---|---|---|
| 用 `read_parquet` 做分析 | 13.9 ms vs 6.7 ms，内存多一份全表 | 一律 `scan_parquet` + `.collect()` |
| 忘记排序就 `group_by_dynamic` | `ComputeError: input data is not sorted` | 先 `sort(["symbol","t"])`；别关掉这个检查 |
| 以为 Polars 的 ASOF 和 DuckDB 一样 | 957 行 vs 238 行 | Polars 默认 LEFT，DuckDB 默认 INNER |
| 以为 `sink_parquet` 一定省内存 | 带 sort 时 +584 MB，比 collect 更贵 | 省内存靠去掉全局算子，不靠换函数 |
| 拿 `explain()` 的 `ESTIMATED ROWS` 当结果行数 | 293,500 是分区级估计 | 看实际 `height` |
| 单条基准决定引擎选型 | 24 MB 数据上 Polars 快 3.5× | 用目标规模 + 目标并发复测；固定开销会被摊薄 |
| 三引擎结果"差不多就行" | 末位差异掩盖逻辑错误 | 行数与聚合值严格相等，写进 CI |

## 9. 出处

- Polars 用户指南 — *Lazy API*（`scan_parquet`、谓词/投影下推、`explain`）、*Dynamic Group By*（要求有序）、*join_asof*（`strategy`、默认 LEFT）、*Streaming / sink_parquet*
- DuckDB 文档 — *ASOF JOIN*（默认 INNER）、*Time Functions*
- DataFusion 文档 — `SessionContext`、`TableProvider`、`ExecutionPlan`（仅作 API 对照，未实测）
- 本章数据：`reports/w07-polars.md`（脚本 `py/w07_polars.py`，2026-11-11，polars 2.1.0 / duckdb 2.0.0 / pandas 3.1.1）

[^1]: `explain()` 输出里的 `PROJECT 5/9 COLUMNS` 与 `SELECTION` 是本机 polars 2.1.0 的原文。`ESTIMATED ROWS` 是扫描节点的分区级估计，不是过滤后行数——用它判断"下推是否发生"可以，判断结果规模不行。
[^2]: `group_by_dynamic` 在乱序输入上直接抛 `ComputeError`。旧版本（0.x）在某些情况下会静默产出重复桶，我在 2024 年的项目里踩过；现在这个硬检查是改进，不要试图绕过。
[^3]: 5M 行、134 MB 的合成数据上，`sort → sink` 的 RSS 增量 584 MB 高于 `collect → write` 的 259 MB，三次复跑同向。⚠️ 这个结论只在"数据能装进内存"时成立；大于内存时的行为未验证。

---

**下一步**：第 8 章从"单文件怎么写"上升到"整座湖怎么长"——分区、幂等、去重、rollup、compaction，以及什么时候才真的需要 Iceberg。
