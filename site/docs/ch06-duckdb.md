# 第 6 章 · DuckDB：下推到了哪一步，以及一个真实的计划陷阱

!!! abstract "本章结论（读完你应该能复述这三句）"
    1. 一次查询的字节数由三层剪枝决定，**顺序是"文件 → 行组 → 列"**：全表 `count(*)` 读 131.0 kB（只有 footer），限定一天降到 81.9 kB，再 `sum(vol)` 才 258.7 kB，读全 7 列 4,198.4 kB。**"扫了多少行"是误导性指标，`Data Read` 才是真的。**
    2. `ASOF JOIN` 的输入**必须是带统计信息的表**。同样一条 SQL，CTE 版走 `Nested Loop Join`、中间结果 875 万行、262.8 ms；物化成 `TEMP TABLE` 后走 `Asof Join`、13.0 ms——**20 倍差距，只因为优化器估不出基数**。
    3. 按时间补空之前先看交易日历：09:30–14:59 墙钟 330 个分钟桶，实际有成交 239 个，缺 91 个——其中 90 个是午休、1 个（13:05）才是真的没成交。**把休市当数据缺口，会做出假的告警。**

对应任务卡：仓库内 `docs/week06.md` ｜ 本章实验脚本：`py/w06_duckdb.py` ｜ 原始结果：`reports/w06-duckdb.md`

---

## 1. 三层剪枝：文件、行组、列

DuckDB 读 Parquet 目录时，能在三个层级上"不看"：

| 层级 | 依据 | 省掉的是 |
|---|---|---|
| 文件 / 分区 | Hive 路径、文件级 `min/max` | 打开文件的系统调用 + footer |
| 行组 | 每个行组每列的 `min/max` | 行组对应的字节 |
| 列 | SQL 里引用的列 | 未引用列的全部字节 |

实测（8 个文件、每文件 1 个行组、每天 4 个合约）：

| 查询 | Data Read | 扫过的行组 | 中位耗时 |
|---|---|---|---|
| 全表 `count(*)` | 131.0 kB | 8 | 5.03 ms |
| `WHERE dt='2026-11-05'` | 81.9 kB | 4 | **3.29 ms** |
| 同一天 + `sum(vol)` | 258.7 kB | 4 | 4.16 ms |
| `symbol='002322.SZ'`（不限 dt） | 217.8 kB | **2** | 5.90 ms |
| 单合约 + 单日 | **127.6 kB** | **1** | 4.73 ms |

<figure class="diagram">
<svg width="100%" viewBox="0 0 680 206" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
<title>三层剪枝各自省掉多少字节</title>
<desc>全表计数只读八个文件的 footer，共一百三十一千字节；限定一天减半；读取一列增加到二百五十八千字节；读全部七列达到四千一百九十八千字节。</desc>
<text x="0" y="14" font-size="13.2" font-weight="500" fill="currentColor">同一条 SQL 改一个条件，读的字节差 32 倍</text>
<g font-size="10.5" fill="currentColor">
<text x="0" y="44" font-weight="500">全表 count(*)</text><text x="0" y="58" fill-opacity="0.6">8 个文件</text>
<text x="0" y="84" font-weight="500">+ dt 一天</text><text x="0" y="98" fill-opacity="0.6">4 个文件</text>
<text x="0" y="124" font-weight="500">+ sum(vol)</text><text x="0" y="138" fill-opacity="0.6">读 1 列</text>
<text x="0" y="164" font-weight="500">+ 全部 7 列</text><text x="0" y="178" fill-opacity="0.6">读 7 列</text>
</g>
<g fill="currentColor" fill-opacity="0.18">
<rect x="122" y="32" width="13" height="26"/>
<rect x="122" y="72" width="8" height="26"/>
</g>
<g fill="#EF9F27" fill-opacity="0.45">
<rect x="122" y="112" width="26" height="26"/>
</g>
<g fill="#EF9F27" fill-opacity="0.7">
<rect x="122" y="152" width="420" height="26"/>
</g>
<g font-size="10" fill="currentColor" fill-opacity="0.85">
<text x="143" y="50">131.0 kB</text><text x="138" y="90">81.9 kB</text>
<text x="156" y="130">258.7 kB</text><text x="550" y="170">4,198.4 kB</text>
</g>
<g font-size="9.5" fill="currentColor" fill-opacity="0.6">
<text x="143" y="64">只有 footer</text><text x="138" y="104">文件级剪枝</text>
<text x="156" y="144">列裁剪</text><text x="550" y="184">全列</text>
</g>
<line x1="0" y1="190" x2="680" y2="190" stroke="currentColor" stroke-opacity="0.15"/>
<text x="0" y="204" font-size="11" fill="currentColor" fill-opacity="0.8">条形按 4,198 kB 等比缩放；前三行被压得几乎看不见——这就是"列裁剪"的威力。</text>
</svg>
</figure>

三条容易读错的细节：

**① `count(*)` 真的不读数据。** 131.0 kB ≈ 8 个 footer（每个约 16 kB）。限定一天后 81.9 kB ≈ 4 个 footer——文件级剪枝发生在**打开文件之前**，连 footer 都不读。

**② 单合约不限 dt 时，只扫了 2 个行组。** 文件级没法剪（`symbol` 不是分区键，8 个文件都要打开读 footer），但每个文件的 `symbol` 列 `min=max`，所以行组级剪枝把 8 个降到 2 个。**分区键不是唯一手段，列统计也能救一半。**

**③ `Data Read` 与 `Rows Scanned` 不是一回事。** 同样的聚合，`Rows Scanned` 给的是"7 列 × 行组数"这种按列累加的数字，拿它当行数会差 7 倍。判断"读了多少"只看 `Data Read`。

## 2. 列裁剪：不读的列一个字节都不花

固定 `dt='2026-11-05'`（4 个文件、4 个行组），只改 SELECT 里引用哪些列：

| 引用列 | Data Read | 中位耗时 |
|---|---|---|
| `count(*)` | 81.9 kB | 5.29 ms |
| `sum(vol)` | 258.7 kB | 4.50 ms |
| `sum(px)` | 423.3 kB | 5.46 ms |
| `sum(vol), sum(px)` | 600.1 kB | 5.67 ms |
| 全 7 列聚合 | **4,198.4 kB** | **13.64 ms** |

`vol` 只占 177 kB（整数、DELTA 压得好），`px` 占 341 kB（浮点），两列加起来 600 kB——**正好是各自之和，没有额外开销**。而读全 7 列是 4,198 kB，是 `sum(vol)` 的 16 倍。

这就是"列存"三个字的具体含义：不是"读得快"，是"**可以不读**"。所以第 3 章那个结论在这里落地了：`ts` 列占了文件的 60%，但只要你不用它，它一个字节都不花；反过来，`SELECT *` 是唯一能让列存退化成行存的写法。

## 3. ASOF JOIN：一个仍然活着的计划陷阱

`ASOF JOIN` 是行情数据最重要的算子：把成交对齐到"之前最近的一条盘口"。语义是 `b.ts >= q.ts` 且取 `q.ts` 最大的一条，每个 `b` 行只匹配一行。

同一件事，五种写法，本机实测（`bars` 957 行、`q` 73,375 行）：

| 写法 | 中位耗时 | 连接算子 |
|---|---|---|
| A 物化表 ASOF | **13.0 ms** | `Asof Join` |
| B CTE 直接 ASOF | **262.8 ms** | `Nested Loop Join` |
| C 子查询 + 过滤 | 13.1 ms | `Asof Join` |
| D `LEFT JOIN ... ON qq.ts <= b.ts` | 55.2 ms | `Hash Join` |
| E CTE + `AS MATERIALIZED` | 221.3 ms | `Nested Loop Join` |

<figure class="diagram">
<svg width="100%" viewBox="0 0 680 214" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
<title>同一个 ASOF 查询的两种计划</title>
<desc>物化表走 Asof Join，估计九百五十七行、耗时十三毫秒；CTE 版退化成嵌套循环，中间结果八百七十五万行、耗时二百六十二毫秒。</desc>
<text x="0" y="14" font-size="13.2" font-weight="500" fill="currentColor">同一条 SQL，两种连接算法</text>
<g stroke="currentColor" stroke-opacity="0.35" fill="none">
<rect x="0" y="28" width="326" height="76"/>
<rect x="354" y="28" width="326" height="76"/>
</g>
<g font-size="11" fill="currentColor">
<text x="14" y="48" font-weight="500">A · 物化成 TEMP TABLE</text>
<text x="368" y="48" font-weight="500">B · WITH 子句直接 ASOF</text>
<text x="14" y="70" font-size="10.5" fill-opacity="0.75">估计 957 行 → Asof Join</text>
<text x="368" y="70" font-size="10.5" fill-opacity="0.75">估不出基数 → Nested Loop Join</text>
<text x="14" y="88" font-size="10.5" fill-opacity="0.75">中间结果 957 行</text>
<text x="368" y="88" font-size="10.5" fill-opacity="0.75">中间结果 8,759,024 行</text>
</g>
<g font-size="10.5" fill="currentColor" fill-opacity="0.7">
<text x="0" y="128">耗时</text>
</g>
<rect x="48" y="116" width="34" height="18" fill="#1D9E75" fill-opacity="0.55"/>
<text x="90" y="130" font-size="10.5" fill="currentColor">13.0 ms</text>
<rect x="48" y="142" width="640" height="18" fill="#EF9F27" fill-opacity="0.5"/>
<text x="48" y="176" font-size="10.5" fill="currentColor">262.8 ms（条形已截断，实际是 20 倍）</text>
<line x1="0" y1="188" x2="680" y2="188" stroke="currentColor" stroke-opacity="0.15"/>
<text x="0" y="203" font-size="11" fill="currentColor" fill-opacity="0.8">AS MATERIALIZED 也没救（221.3 ms）——问题不是有没有物化，是有没有列统计。</text>
<text x="0" y="216" font-size="11" fill="currentColor" fill-opacity="0.8">CTE 是匿名的，优化器拿不到基数的直方图，只能猜 1 行，于是选嵌套循环。</text>
</svg>
</figure>

关键在于两张计划的**估计行数**：

```text
A（TEMP TABLE）:
  Asof Join
    ~957 rows          ← 来自表的列统计
    ~957 rows / ~73,375 rows

B（CTE）:
  Nested Loop Join     ← 没有估计值可打印
    8,759,024 rows     ← 238 × 73,375 / 2 量级的笛卡尔积
```

幂等的解释是：`bars` 和 `q` 都是"扫 Parquet + 聚合"的结果，CTE 没有列统计，优化器按默认选择率猜出很小的基数，于是认为"嵌套循环更便宜"（小表驱动大表），结果生成了 875 万行中间结果。**`AS MATERIALIZED` 只保证物化，不产生统计信息**，所以 E 依然是嵌套循环。

正确的写法只有两条：

```sql
-- ① 落成临时表（推荐）：让优化器有统计可用
CREATE OR REPLACE TEMP TABLE bars AS
SELECT symbol, ts // 60000000000 * 60000000000 AS ts, sum(vol) AS vol
FROM read_parquet('...') WHERE dt = '2026-11-05' GROUP BY 1, 2;

CREATE OR REPLACE TEMP TABLE q AS
SELECT symbol, ts, bid_px1, ask_px1 FROM read_parquet('...')
WHERE dt = '2026-11-05' AND symbol = '002322.SZ';

SELECT count(*) FROM bars ASOF JOIN q USING (symbol, ts);
```

```sql
-- ② 或者干脆手写"取最近一条"的语义（窗口函数），不依赖 ASOF
SELECT count(*) FROM (
  SELECT b.*, row_number() OVER (PARTITION BY b.symbol, b.ts ORDER BY q.ts DESC) AS rn
  FROM bars b LEFT JOIN q ON b.symbol = q.symbol AND q.ts <= b.ts
) WHERE rn = 1;
```

第 ② 种在 D 的形态上要 55.2 ms（`Hash Join` + 窗口），比物化表慢，但**不会因为统计信息变化而突然退化 20 倍**——这是"可预测性"换"绝对速度"的取舍。生产里我倾向物化表，因为它同时解决了"重复扫 Parquet"的问题：`bars` 只算一次，后面几十个查询复用。

## 4. 时间语义：`time_bucket` 与 `date_trunc` 不是一回事

`date_trunc('minute', t)` 只能按自然单位截断（秒/分/时/天）；`time_bucket(INTERVAL 90 SECOND, t)` 可以按任意间隔，并且**以 epoch 为原点对齐**。单合约一天的数据：

| 指标 | 值 |
|---|---|
| tick 行数 | 73,375 |
| 90 秒桶 | 160 个 |
| 分钟桶 | 239 个 |
| 时间跨度 | 09:30:00 → 14:59:59 |

90 秒桶只有 160 个，而 4 小时 = 14,400 秒 ÷ 90 ≈ 160——**桶的对齐原点是 epoch，不是 09:30**，所以第一个桶和最后一个桶都是不完整的。**做 VWAP 时这会带来偏差**：开盘第一个桶可能只含 30 秒的成交，却和完整桶一起参与比较。

分钟桶 239 个，而墙钟 09:30–14:59 是 330 分钟，缺 91 个：

```text
缺口 11:30 … 13:05，共 91 个
其中 90 个在 11:30–12:59（午休）
另外 1 个是真空档：13:05
```

<figure class="diagram">
<svg width="100%" viewBox="0 0 680 176" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
<title>一个交易日的分钟桶：哪些缺口是休市，哪些是真空档</title>
<desc>从九点半到十五点共三百三十个分钟桶，上午一百二十个、下午一百二十个有成交，午休九十分钟没有成交，另有一个真空档。</desc>
<text x="0" y="14" font-size="13.2" font-weight="500" fill="currentColor">330 个墙钟分钟里，239 个有成交</text>
<rect x="0" y="30" width="240" height="30" fill="currentColor" fill-opacity="0.22"/>
<rect x="240" y="30" width="180" height="30" fill="currentColor" fill-opacity="0.06"/>
<rect x="420" y="30" width="240" height="30" fill="currentColor" fill-opacity="0.22"/>
<rect x="600" y="30" width="2" height="30" fill="#EF9F27" fill-opacity="0.9"/>
<g font-size="9.5" text-anchor="middle" fill="currentColor" fill-opacity="0.85">
<text x="120" y="49">09:30–11:29　120 个桶</text>
<text x="330" y="49">11:30–12:59　0 个桶</text>
<text x="540" y="49">13:00–14:59　120 个桶</text>
</g>
<g font-size="9" text-anchor="middle" fill="currentColor" fill-opacity="0.6">
<text x="0" y="76">09:30</text><text x="240" y="76">11:30</text><text x="420" y="76">13:00</text><text x="680" y="76">14:59</text>
</g>
<path d="M601 30 V18" stroke="#EF9F27" stroke-opacity="0.8"/>
<text x="601" y="12" font-size="9.5" text-anchor="middle" fill="#EF9F27">13:05 真空档</text>
<text x="0" y="102" font-size="10.5" fill="currentColor" fill-opacity="0.8">若把"缺失的分钟"一律当异常：91 个告警，其中 90 个是误报——</text>
<text x="0" y="118" font-size="10.5" fill="currentColor" fill-opacity="0.8">每个交易日 90 条噪声，一周就把告警渠道淹了。</text>
<line x1="0" y1="134" x2="680" y2="134" stroke="currentColor" stroke-opacity="0.15"/>
<text x="0" y="150" font-size="11" fill="currentColor" fill-opacity="0.8">正确做法：把交易日历（含午休、集合竞价、夜盘）作为一张维表 join 进来，</text>
<text x="0" y="166" font-size="11" fill="currentColor" fill-opacity="0.8">只在"日历说应该有、实际没有"时才告警——这才是第 8 章质量报告的基础。</text>
</svg>
</figure>

这一节的目的不是教你写 SQL，而是提醒：**时间序列的"缺失"有三种**——休市（正常）、真空档（可能正常，流动性差）、丢数据（异常）。只有第三种该报警，而区分它们需要外部知识（交易日历），不在 Parquet 里。

## 5. 并行与内存：两个被夸大的旋钮

`threads` 对"扫 Parquet + 分组聚合"的影响：

| threads | 1 | 2 | 4 | 8 |
|---|---|---|---|---|
| 中位耗时 | 22.2 ms | 15.9 ms | **12.4 ms** | 12.0 ms |

1 → 4 线程是 1.8×，4 → 8 只有 1.04×。因为文件只有 8 个、每个 1 个行组，**并行度的上限是"可并行的扫描单元数"**，不是线程数。想让 8 线程有用，得先有 8 个以上的文件/行组（第 8 章的分区设计就是为这个服务的）。

内存限制则是反面教材：

```text
memory_limit= 64MB：OOM — failed to pin block of size 256.0 KiB (60.6 MiB/64 MiB)
memory_limit= 32MB：OOM
memory_limit= 16MB：OOM
```

**没有出现"优雅 spill"，而是直接失败。** 这不是 DuckDB 的 bug，而是"spill 需要先有内存"：buffer manager 要 pin 住一个 block 才能把它写到临时文件。587,000 行、20 MB 的数据在 64 MB 限制下就撞墙，说明**几百 MB 的测试数据根本验证不了 spill 行为**——那些"我把 memory_limit 调小就观察到 spill"的博客，用的数据量至少是 GB 级。

实践含义：调 `memory_limit` 时留 4–8 倍余量，别贴着数据量设；真要验证 spill，得造一份大于内存的数据（第 7 章的 `sink_parquet` 会做这件事）。

## 6. `COPY ... TO`：分区写出

```sql
COPY (
  SELECT symbol, ts // 60000000000 * 60000000000 AS ts, sum(vol) AS vol, avg(px) AS px
  FROM read_parquet('lake/data/raw/trades/**/*.parquet', hive_partitioning=true)
  GROUP BY 1, 2
) TO 'lake/data/exp06/bars' (FORMAT PARQUET, PARTITION_BY (symbol), OVERWRITE_OR_IGNORE);
```

结果：4 个文件（每个合约一个目录）、29 ms、1,913 个分钟 bar、合计 37 KB。

```text
symbol=001317.SZ/data_0.parquet   9.3 KB
symbol=002322.SZ/data_0.parquet   9.3 KB
symbol=600267.SH/data_0.parquet   9.3 KB
symbol=601963.SH/data_0.parquet   9.4 KB
```

587,000 条 tick 聚成 1,913 个 bar，体积从 3.5 MB 降到 37 KB（**95 倍**）——这就是"分层 rollup"的收益来源，第 8 章会把它做成 raw → 1s → 1m 三层。

两个细节：`PARTITION_BY (symbol)` 生成的目录名是 `symbol=002322.SZ`，读回时必须带 `hive_partitioning=true` 才能把 `symbol` 变成列；`OVERWRITE_OR_IGNORE` 让重跑幂等（不然第二次会报目录已存在）。**幂等不是附加功能，是分区写出的一等需求**——第 8 章会展开。

## 7. 踩坑

| 坑 | 表现 | 正确做法 |
|---|---|---|
| 用 `Rows Scanned` 当行数 | 7 列 × 行组数，虚高 7 倍 | 看 `Data Read`（字节）与 `EXPLAIN` 的估计行数 |
| CTE 里做聚合再 ASOF | 262.8 ms vs 13.0 ms，`Nested Loop Join` | 先 `CREATE TEMP TABLE`，让优化器有统计 |
| 以为 `AS MATERIALIZED` 能修计划 | 221.3 ms，仍是嵌套循环 | 物化 ≠ 有统计；要真表 |
| 用 `LEFT JOIN + 不等号` 代替 ASOF | 55.2 ms，且结果行数会膨胀 | 要么 ASOF，要么窗口函数 + `rn=1` |
| 把休市当数据缺口 | 每天 90 条假告警 | 交易日历 join，区分休市/真空档/丢数据 |
| 以为 `time_bucket` 从开盘对齐 | 90 秒桶首尾都不完整 | 桶原点在 epoch；会话内聚合要先减开盘偏移 |
| 把 `memory_limit` 贴着数据量设 | 直接 OOM，不是 spill | 留 4–8 倍余量；验证 spill 要 GB 级数据 |
| `threads` 开到 32 期待线性加速 | 8 文件 1 行组，4 线程就饱和 | 并行度上限 = 扫描单元数 |

## 8. 出处

- DuckDB 文档 — *Parquet 扩展*：`hive_partitioning`、`filename`、`union_by_name`；`EXPLAIN ANALYZE` 输出字段（`Total Time` / `Data Read`）
- DuckDB 文档 — *ASOF JOIN*：`USING` 的等值键 + 不等值键语义、每个左行最多匹配一个右行
- DuckDB 文档 — *Time Functions*：`time_bucket`（任意间隔、epoch 对齐）与 `date_trunc`（自然单位）的差异
- DuckDB 文档 — *Limits / Memory Management*：`memory_limit`、`threads`、`preserve_insertion_order`
- 本章数据：`reports/w06-duckdb.md`（脚本 `py/w06_duckdb.py`，2026-11-11，duckdb 2.0.0）

[^1]: `Data Read` 是本机 `EXPLAIN ANALYZE` 的 Summary 字段，单位 kB/MB；`Row Groups Scanned: a / b` 出现在 Table Scan 算子里。两者都不等于行数——这点在官方文档里没有强调，是本机对照实验发现的。
[^2]: B/E 的退化在本机 duckdb 2.0.0 稳定复现（三次 258.8 / 262.8 / 260.8 ms，算子均为 `Nested Loop Join`，中间结果 8,759,024 行）。⚠️ 这是特定版本的优化器行为，升级后可能改变；判断方法始终是看 `EXPLAIN` 里的连接算子，而不是记版本号。
[^3]: 午休缺口 90 个 + 真空档 1 个（13:05）= 91。交易日历只覆盖 09:30–11:30 / 13:00–15:00，本数据没有集合竞价与夜盘，所以"应有多少桶"要按日历算，不能按墙钟算。

---

**下一步**：第 7 章把同样的五个查询用 Polars 重写——惰性计划、`group_by_dynamic`、`sink_parquet`，以及"什么时候该换 DataFusion"的判断标准。
