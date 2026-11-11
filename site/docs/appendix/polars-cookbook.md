# 附录 · Polars 常用方法速查

这份是**"想做什么 → 怎么写"**的对照手册，不是原理讲解。机制与边界（惰性引擎怎么下推、流式写到哪一步会退化）在第 7 章；这里只回答日常问题。

- 版本：**polars 2.1.0**（`uv sync` 装出来的就是它）
- 每个代码块都在 `py/polars_cookbook.py` 里，输出是**本机实跑**的结果，不是"预期输出"
- 数据用的是仓库自己的 `lake/data/lake/trades`（587,000 行、9 列），不是编造的 3 行小表

```bash
uv run python py/polars_cookbook.py            # 全部跑一遍
uv run python py/polars_cookbook.py --only F   # 只跑第 F 节（窗口函数）
```

---

## 0. 先改掉这六个 pandas 习惯

不熟练的根源通常不是记不住 API，而是**脑子里的模型还是 pandas 的**。下面六条一句话版：

| 你可能习惯这么写 | Polars 里应该 | 差别不是语法，是模型 |
|---|---|---|
| `df["px"]` 拿到 Series，然后就地算 | `pl.col("px")` —— 这是**表达式**，不是数据 | 表达式可以下推、可以复用、可以进 `over`；Series 不行 |
| `df.loc[...]` / `df.iloc[...]` | `filter()` / `slice()` / `gather()` | **Polars 没有索引**。行就是行，序号只在需要时用 `with_row_index` 现造 |
| `df.drop(..., inplace=True)` | 没有 `inplace`，每个方法**返回新对象** | 不可变才敢重排计划、并行执行、共享缓存 |
| `groupby().transform()` | `expr.over(...)` | 窗口是表达式的一部分，不是"另一次聚合再对齐回去" |
| `apply(lambda row: ...)` | 表达式 + `str` / `dt` 命名空间 | 逐行调 Python 是性能杀手，也是 Polars 唯一真正慢的写法 |
| `df.append()` / `pd.concat` | `pl.concat([...], how="vertical_relaxed")` | 类型严格：`Int64` 与 `Float64` 拼接要显式放宽 |

<figure class="diagram">
<svg width="100%" viewBox="0 0 680 302" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
<title>pandas 与 Polars 的对象模型对照</title>
<desc>pandas 的 DataFrame 是索引加列块，每次操作物化一份新数据；Polars 的 LazyFrame 是一张算子计划图，表达式描述列变换，collect 时才分配内存并执行。</desc>
<text x="0" y="14" font-size="13.2" font-weight="500" fill="currentColor">pandas：数据 + 索引，每步都物化</text>
<g stroke="currentColor" stroke-opacity="0.35" fill="none">
<rect x="0" y="26" width="146" height="34"/>
<rect x="0" y="66" width="146" height="46"/>
<rect x="0" y="118" width="146" height="34"/>
</g>
<g font-size="10" fill="currentColor">
<text x="10" y="47">行索引 Index</text>
<text x="10" y="86">列块（每列一块内存）</text>
<text x="10" y="108" fill-opacity="0.65">int64 / float64 / object…</text>
<text x="10" y="139">行标签、对齐规则</text>
</g>
<text x="0" y="172" font-size="10.5" fill="currentColor" fill-opacity="0.8">df[df.px &gt; 71] → 立刻复制一份新表；索引随之重排</text>

<line x1="0" y1="188" x2="680" y2="188" stroke="currentColor" stroke-opacity="0.15"/>

<text x="0" y="208" font-size="13.2" font-weight="500" fill="currentColor">Polars：一张计划图，表达式是积木</text>
<g stroke="currentColor" stroke-opacity="0.35" fill="none">
<rect x="0" y="220" width="118" height="40"/>
<rect x="140" y="220" width="118" height="40"/>
<rect x="280" y="220" width="118" height="40"/>
<rect x="420" y="220" width="118" height="40"/>
</g>
<g font-size="10" text-anchor="middle" fill="currentColor">
<text x="59" y="238">SCAN</text><text x="59" y="252" fill-opacity="0.65">只登记文件</text>
<text x="199" y="238">FILTER</text><text x="199" y="252" fill-opacity="0.65">下推到扫描</text>
<text x="339" y="238">WITH_COLUMNS</text><text x="339" y="252" fill-opacity="0.65">表达式</text>
<text x="479" y="238">COLLECT</text><text x="479" y="252" fill-opacity="0.65">才分配内存</text>
</g>
<g stroke="currentColor" stroke-opacity="0.45" fill="none">
<path d="M120 240 h14"/><path d="M260 240 h14"/><path d="M400 240 h14"/>
</g>
<g fill="currentColor" fill-opacity="0.5">
<path d="M134 240 l-6 -3 l0 6 z"/><path d="M274 240 l-6 -3 l0 6 z"/><path d="M414 240 l-6 -3 l0 6 z"/>
</g>
<text x="0" y="284" font-size="11" fill="currentColor" fill-opacity="0.8">前三个节点只是往图上加节点，不碰数据；优化器重排完，collect 才真正执行。</text>
<text x="0" y="298" font-size="11" fill="currentColor" fill-opacity="0.8">所以"先 filter 还是先 select"不用你操心，但"用 pandas 的写法"会让下推失效。</text>
</svg>
</figure>

一个具体的例子：这段代码在 pandas 里是三次全表复制，在 Polars 里**一个字节都没读**——`lf` 只是三行计划：

```python
lf = (pl.scan_parquet(TRADES)          # 不加节点
        .filter(pl.col("px") > 71)      # 加一个 FILTER 节点
        .select("ts", "px"))            # 加一个 PROJECT 节点
# 到这里什么也没算。真要看数据，才 collect：
lf.collect()
```

---

## 1. 读和写

### 1.1 一律从 `scan_parquet` 开始

```python
import polars as pl

TRADES = "lake/data/lake/trades/**/*.parquet"

lf = pl.scan_parquet(TRADES, hive_partitioning=True)   # 路径里的 dt= / symbol= 变成列
q = (lf.filter((pl.col("dt") == "2026-11-04") & (pl.col("symbol") == "002322.SZ"))
       .select("ts", "px", "vol"))
```

`q.explain()` 能看到优化器干了什么（不执行）：

```text
simple π 3/3 ["ts", "px", "vol"]
  Parquet SCAN [lake/data/lake/trades/dt=2026-11-04/symbol=002322.SZ/part-0.parquet]
  PROJECT 5/9 COLUMNS
  SELECTION: (col("dt") == "2026-11-04") & (col("symbol") == "002322.SZ")
  ESTIMATED ROWS: 73375
```

同一个查询，`read_parquet` 与 `scan_parquet` 的实测（同一进程、同一份数据）：

| 写法 | 行数 | 耗时 |
|---|---|---|
| `read_parquet(...).filter(...)` | 73,375 | **17.0 ms** |
| `scan_parquet(...).filter(...).collect()` | 73,375 | **1.3 ms** |

差别不在"快 13 倍"这个数字（含首次读盘，重复跑会变），而在**读了多少**：`read_parquet` 把 9 列全搬进内存再筛，`scan_parquet` 只读命中的行组与 5 列。

!!! warning "`ESTIMATED ROWS` 不是结果行数"
    73375 是**分区级估计**（这个文件有多少行），不是过滤后的行数。要看真实行数用 `df.height`。

### 1.2 写出

```python
# 过滤 + 投影后直接流式写，不要先 collect
(pl.scan_parquet(TRADES, hive_partitioning=True)
   .filter(pl.col("symbol") == "002322.SZ")
   .select("ts", "px", "vol")
   .sink_parquet("lake/data/exp/cookbook/px_only.parquet"))
```

实测写出 850 KB。**但 `sink` 不等于省内存**：只要计划里有全局 `sort`，它就得把整份数据攒进内存，那时反而不如 `collect()` 再写——第 7 章 §5 有 +110 / +584 / +259 MB 的实测对照。

### 1.3 其它格式

```python
df.write_csv("x.csv")            # 分区间隔、表头都默认
df.write_ndjson("x.ndjson")      # 每行一个 JSON
df.write_ipc("x.arrow")          # Arrow IPC，跨进程交换用这个
pl.scan_csv("x.csv")             # 同样有惰性版
pl.scan_ndjson("x.ndjson")
```

CSV 读回来类型是推断的（`[String, Int64, Float64, Int64]`），长整型时间戳不会自己变成 `Datetime`——**永远显式 `cast`**。

---

## 2. 选列与过滤

```python
df.select("symbol", "px", "vol")                    # 按名
df.select(pl.col("px") * pl.col("vol"))             # 按表达式（结果列名就是表达式）
df.select(pl.col("^s.*$"))                          # 正则批量：命中 symbol / seq / side
df.drop("channel", "side")                          # 丢列
```

六个过滤写法，覆盖日常九成：

```python
df.filter(pl.col("px") > 71)                                     # 单条件 → 30 行
df.filter((pl.col("px") > 71) & (pl.col("side") == 1))           # 多条件 → 13 行
df.filter(pl.col("channel").is_in([0, 2]))                       # 集合 → 96 行
df.filter(pl.col("vol").is_between(100, 200))                    # 闭区间
df.filter(~pl.col("channel").is_in([0]))                         # 取反用 ~
df.filter(pl.col("px").is_not_null() & (pl.col("px") > 0))       # null 要显式处理
```

!!! warning "多条件必须加括号"
    `pl.col("a") > 1 & pl.col("b") < 2` 会按位运算优先级解析成 `a > (1 & b) < 2`。每个比较都要单独括号：`(pl.col("a") > 1) & (pl.col("b") < 2)`。

按位置取行（**没有 loc/iloc**）：

```python
df.head(2)          # 前 2 行
df.tail(2)          # 后 2 行
df.slice(10, 3)     # 从第 10 行起 3 行
df.gather([0, 5, 9])  # 按行号列表取
df.sample(n=100, seed=42)          # 固定 seed 才能复现
df.gather_every(20)                # 每 20 行取一行
```

---

## 3. 造列与改列

### 3.1 `with_columns`：唯一该记的动词

```python
df.with_columns(
    (pl.col("px") * pl.col("vol")).alias("notional"),
    (pl.col("px") - pl.col("px").shift(1)).alias("dpx"),
    pl.col("ts").cast(pl.Datetime("ns")).alias("t"),
)
```

三件事一次说清：

- **覆盖已有列不用 `.alias()`**：`with_columns(pl.col("px") * 2)` 会原地覆盖 `px`（返回新表，原表不动）
- **`with_columns` 内的表达式互相看不到**：同一批里的 `notional` 不能被另一个表达式引用。要分两步写，或者重算
- **`shift` 默认跨整个表**，多合约数据要 `shift(1).over("symbol")`，否则会把上一支股票的收盘价串进来

原对象永远不变：跑完上面那句，`"notional" in df.columns` 仍然是 `False`。

### 3.2 `when / then / otherwise`：向量化 if-else

```python
df.with_columns(
    pl.when(pl.col("side") == 1).then(pl.lit("买"))
      .when(pl.col("side") == -1).then(pl.lit("卖"))
      .otherwise(pl.lit("未知")).alias("side_cn")
)
```

```text
┌──────┬─────────┐
│ side ┆ side_cn │
│ i8   ┆ str     │
╞══════╪═════════╡
│ 1    ┆ 买      │
│ -1   ┆ 卖      │
│ 1    ┆ 买      │
│ 0    ┆ 未知    │
└──────┴─────────┘
```

`then` 里必须放表达式：常量要 `pl.lit(...)`，否则会被当成列名。

### 3.3 字符串：`str` 命名空间

```python
pl.DataFrame({"code": ["2322", "600267", "1", "SZ000001"]}).with_columns(
    pl.col("code").str.zfill(6).alias("补齐6位"),
    pl.col("code").str.len_chars().alias("长度"),
    pl.col("code").str.contains("SZ").alias("含SZ"),
    pl.col("code").str.slice(0, 2).alias("前两位"),
    pl.col("code").str.to_uppercase().alias("大写"),
)
```

```text
┌──────────┬──────────┬──────┬───────┬────────┬──────────┐
│ code     ┆ 补齐6位  ┆ 长度 ┆ 含SZ  ┆ 前两位 ┆ 大写     │
│ str      ┆ str      ┆ u32  ┆ bool  ┆ str    ┆ str      │
╞══════════╪══════════╪══════╪═══════╪════════╪══════════╡
│ 2322     ┆ 002322   ┆ 4    ┆ false ┆ 23     ┆ 2322     │
│ 600267   ┆ 600267   ┆ 6    ┆ false ┆ 60     ┆ 600267   │
│ 1        ┆ 000001   ┆ 1    ┆ false ┆ 1      ┆ 1        │
│ SZ000001 ┆ SZ000001 ┆ 8    ┆ true  ┆ SZ     ┆ SZ000001 │
└──────────┴──────────┴──────┴───────┴────────┴──────────┘
```

正则提取用 `str.extract`（第二个参数是捕获组号）：

```python
pl.DataFrame({"s": ["600000.SH", "000001.SZ", "IF2412.CFE"]}).with_columns(
    pl.col("s").str.extract(r"^(\d+)\.", 1).alias("code"),
    pl.col("s").str.extract(r"\.(\w+)$", 1).alias("exch"),
)
```

```text
┌────────────┬────────┬──────┐
│ s          ┆ code   ┆ exch │
│ str        ┆ str    ┆ str  │
╞════════════╪════════╪══════╡
│ 600000.SH  ┆ 600000 ┆ SH   │
│ 000001.SZ  ┆ 000001 ┆ SZ   │
│ IF2412.CFE ┆ null   ┆ CFE  │
└────────────┴────────┴──────┘
```

注意 `IF2412.CFE` 的 `code` 是 `null` 而不是 `"IF2412"`——**正则按 `^(\d+)` 匹配不到就返回 null，不会退回部分匹配**。这是好事，比 pandas 的 `str.extract` 静默给 NaN 更容易发现。

常备清单：`str.len_chars` / `str.slice` / `str.to_uppercase` / `str.contains` / `str.starts_with` / `str.replace` / `str.replace_all` / `str.split` / `str.join` / `str.strip_chars` / `str.zfill` / `str.strptime`（字符串转时间）。两列拼接直接用 `+`：`pl.col("a") + pl.col("b")`。

### 3.4 时间：`dt` 命名空间

本项目落地的时间是 **int64 纳秒**（见 `docs/schema.md`），所以第一件事永远是转换：

```python
df.with_columns(pl.col("ts").cast(pl.Datetime("ns")).alias("t")).select(
    "t",
    pl.col("t").dt.truncate("1m").alias("分钟桶"),
    pl.col("t").dt.hour().alias("时"),
    pl.col("t").dt.minute().alias("分"),
    pl.col("t").dt.strftime("%H:%M:%S%.3f").alias("格式化"),
)
```

```text
┌───────────────────────────┬─────────────────────┬─────┬─────┬───────────────┐
│ t                         ┆ 分钟桶              ┆ 时  ┆ 分  ┆ 格式化        │
│ datetime[ns]              ┆ datetime[ns]        ┆ i8  ┆ i8  ┆ str           │
╞═══════════════════════════╪═════════════════════╪═════╪═════╪═══════════════╡
│ 2026-11-04 01:30:00.0100… ┆ 2026-11-04 01:30:00 ┆ 1   ┆ 30  ┆ 01:30:00.010  │
│ 2026-11-04 01:30:00.2334… ┆ 2026-11-04 01:30:00 ┆ 1   ┆ 30  ┆ 01:30:00.233  │
└───────────────────────────┴─────────────────────┴─────┴─────┴───────────────┘
```

`pl.from_epoch("ts", time_unit="ns")` 与 `cast(pl.Datetime("ns"))` 等价，前者更明确。

**注意时区**：数据是 UTC 纳秒，`cast` 出来是 **UTC 的 naive datetime**（这里显示 01:30 就是 UTC 01:30 = 北京 09:30）。要么 `.dt.replace_time_zone("UTC").dt.convert_time_zone("Asia/Shanghai")` 显式转换，要么整条链路都用 UTC，别混。

常备清单：`dt.year/month/day/hour/minute/second/millisecond`、`dt.weekday`（1=周一）、`dt.ordinal_day`、`dt.truncate("5m")`、`dt.offset_by("1d")`、`dt.strftime`、`dt.total_seconds`、`dt.date()`。

---

## 4. 聚合

一次算全部指标，**不要**分组算好几遍再 join 回去：

```python
bars = (
    df.with_columns(pl.col("ts").cast(pl.Datetime("ns")).alias("t"))
      .with_columns(pl.col("t").dt.truncate("1m"))
      .group_by("t")
      .agg(
          pl.len().alias("trades"),
          pl.col("vol").sum().alias("volume"),
          pl.col("px").mean().alias("avg_px"),
          pl.col("px").min().alias("low"),
          pl.col("px").max().alias("high"),
          pl.col("px").first().alias("open"),
          pl.col("px").last().alias("close"),
          (pl.col("px") * pl.col("vol")).sum().alias("notional"),
      )
      .with_columns((pl.col("notional") / pl.col("volume")).alias("vwap"))
      .sort("t")
)
```

```text
┌─────────────────────┬────────┬────────┬───────────┬───────┬───────┬───────┬───────────┐
│ t                   ┆ trades ┆ volume ┆ avg_px    ┆ low   ┆ high  ┆ close ┆ vwap      │
│ datetime[ns]        ┆ u32    ┆ i64    ┆ f64       ┆ f64   ┆ f64   ┆ f64   ┆ f64       │
╞═════════════════════╪════════╪════════╪═══════════╪═══════╪═══════╪═══════╪═══════════╡
│ 2026-11-04 01:30:00 ┆ 291    ┆ 132900 ┆ 70.965395 ┆ 70.23 ┆ 71.72 ┆ 71.23 ┆ 70.944771 │
│ 2026-11-04 01:31:00 ┆ 354    ┆ 152300 ┆ 70.683418 ┆ 70.02 ┆ 71.29 ┆ 71.25 ┆ 70.659875 │
└─────────────────────┴────────┴────────┴───────────┴───────┴───────┴───────┴───────────┘
共 239 个分钟桶
```

239 个桶与第 7 章、C++ 回放、DuckDB 的结果完全一致——**这是这个仓库所有数字的验收线**。

常用聚合函数：

| 想要 | 写法 |
|---|---|
| 行数 | `pl.len()`（不要用 `pl.count()`，2.x 里它是 deprecated） |
| 去重计数 | `pl.col("symbol").n_unique()` |
| 分位数 | `pl.col("vol").quantile(0.95)` |
| 中位数 / 标准差 | `.median()` / `.std()` |
| 首尾 | `.first()` / `.last()`（**依赖当前顺序**，先 sort） |
| 取最大/最小的 k 个 | `.top_k(3)` / `.bottom_k(3)` |
| 组内前 3 行 | `.head(3)`（返回 list 列） |

`agg` 里返回 list 是常态——一个桶内的多笔成交就是 list，要展开用 `explode`：

```text
┌─────────────────────┬───────────────────────┬──────┐
│ t                   ┆ 前3笔                 ┆ 笔数 │
│ datetime[ns]        ┆ list[f64]             ┆ u32  │
╞═════════════════════╪═══════════════════════╪══════╡
│ 2026-11-04 01:30:00 ┆ [70.84, 70.74, 70.74] ┆ 291  │
└─────────────────────┴───────────────────────┴──────┘
```

---

## 5. 窗口：`over` 替代 `transform`

`over` 的心智模型：**先在每个组内算一个聚合值，再按原行广播回去**，行数不变。

```python
df.with_columns(pl.col("t").dt.truncate("1m").alias("m")).with_columns(
    pl.col("vol").sum().over("m").alias("m_volume"),
    (pl.col("vol") / pl.col("vol").sum().over("m")).round(4).alias("share"),
)
```

```text
┌───────────────────────────┬──────┬──────────┬────────┐
│ t                         ┆ vol  ┆ m_volume ┆ share  │
│ datetime[ns]              ┆ i64  ┆ i64      ┆ f64    │
╞═══════════════════════════╪══════╪══════════╪════════╡
│ 2026-11-04 01:30:00.0100… ┆ 200  ┆ 132900   ┆ 0.0015 │
│ 2026-11-04 01:30:00.2334… ┆ 300  ┆ 132900   ┆ 0.0023 │
│ 2026-11-04 01:30:00.4765… ┆ 400  ┆ 132900   ┆ 0.003  │
└───────────────────────────┴──────┴──────────┴────────┘
```

同一个 `over` 还能挂排名、累计、差分：

```python
df.with_columns(
    pl.col("vol").rank("ordinal", descending=True).over("side").alias("组内量排名"),
    pl.col("vol").cum_sum().over("side").alias("组内累计量"),
    (pl.col("px") - pl.col("px").shift(1).over("side")).alias("与上一笔价差"),
)
```

```text
┌──────┬───────┬──────┬────────────┬────────────┬──────────────┐
│ side ┆ px    ┆ vol  ┆ 组内量排名 ┆ 组内累计量 ┆ 与上一笔价差 │
│ i8   ┆ f64   ┆ i64  ┆ u32        ┆ i64        ┆ f64          │
╞══════╪═══════╪══════╪════════════╪════════════╪══════════════╡
│ 1    ┆ 70.84 ┆ 200  ┆ 1439       ┆ 200        ┆ null         │
│ -1   ┆ 70.74 ┆ 300  ┆ 1010       ┆ 300        ┆ null         │
│ 1    ┆ 70.74 ┆ 400  ┆ 780        ┆ 600        ┆ -0.1         │
└──────┴───────┴──────┴────────────┴────────────┴──────────────┘
```

注意 `shift` 的那一列：第一行天然是 `null`（没有"上一笔"），**不要 fill 成 0**，那会把第一笔的开盘跳空算进去。

滚动窗口是独立的：

```python
df.with_columns(
    pl.col("px").rolling_mean(window_size=100).alias("ma100"),
    pl.col("px").rolling_std(window_size=100).alias("sd100"),
)
```

```text
┌───────┬─────────┬──────────┐
│ px    ┆ ma100   ┆ sd100    │
│ f64   ┆ f64     ┆ f64      │
╞═══════╪═════════╪══════════╡
│ 72.18 ┆ 71.6388 ┆ 0.372736 │
│ 72.26 ┆ 71.6523 ┆ 0.370514 │
└───────┴─────────┴──────────┘
```

`window_size=100` 是**按行数**；按时间用 `rolling_mean_by("t", window_size="1m")`。两种都需要输入有序，否则算出来的是乱序窗口。

---

## 6. 排序、去重、取前 N

```python
df.sort(["side", "vol"], descending=[False, True])   # 升降序分开给
df.sort("t", nulls_last=True)                        # null 放最后
```

去重是这里最容易出错的地方。给定去重键 `(symbol, ts)` 与一个重复样例：

```python
dup = pl.DataFrame({
    "symbol": ["A", "A", "A", "B"],
    "ts":     [1, 1, 2, 3],
    "seq":    [0, 1, 0, 0],
    "px":     [10.0, 10.5, 11.0, 20.0],
})
dup.unique(subset=["symbol", "ts"], keep="last", maintain_order=True)
```

```text
keep="last" → 留下 seq=1 的那条（px=10.5）
┌────────┬─────┬─────┬──────┐
│ symbol ┆ ts  ┆ seq ┆ px   │
│ str    ┆ i64 ┆ i64 ┆ f64  │
╞════════╪═════╪═════╪══════╡
│ A      ┆ 1   ┆ 1   ┆ 10.5 │
│ A      ┆ 2   ┆ 0   ┆ 11.0 │
│ B      ┆ 3   ┆ 0   ┆ 20.0 │
└────────┴─────┴─────┴──────┘

keep="any"  → 留下 seq=0 的那条（px=10.0），不保证是"对"的那条
```

**做对账时永远 `keep="last"` 并先按 `seq` 排序**。`keep="any"` 快，但它选哪条是实现细节，跨版本可能变。

取前 N：

```python
df.select(pl.col("vol").top_k(5))                        # [23200, 14100, 11900, 10100, 9600]
df.select(pl.col("vol").arg_sort(descending=True))       # 行号 [48429, 45422, 46675, …]
df.sort("vol", descending=True).head(5)                  # 要整行就用这个
```

`top_k` 在 `agg` 里就是"每组的最大 k 个"，比 `sort + group_by + head` 省一次全局排序。

---

## 7. 连接

```python
left  = pl.DataFrame({"symbol": ["A", "B", "C"], "v": [1, 2, 3]})
right = pl.DataFrame({"symbol": ["A", "B", "D"], "w": [10, 20, 40]})
```

| `how=` | 行数 | 语义 | 什么时候用 |
|---|---|---|---|
| `inner` | 2 | 只留两边都有的键 | 对齐后的分析 |
| `left` | 3 | 保留左表全部（默认） | **绝大多数业务场景** |
| `full` | 4 | 两边都留，缺的置 null | 对账、找差异 |
| `semi` | 2 | 只留左表命中行，不带右表列 | 过滤（相当于 `is_in` 的 join 版） |
| `anti` | 1 | 只留左表**没**命中的行 | 找孤儿记录 |
| `cross` | 9 | 笛卡尔积，**不能传 `on`** | 生成参数网格 |

`full` 的结果里右表的键会变成 `symbol_right`：

```text
┌────────┬──────┬──────────────┬──────┐
│ symbol ┆ v    ┆ symbol_right ┆ w    │
╞════════╪══════╪══════════════╪══════╡
│ A      ┆ 1    ┆ A            ┆ 10   │
│ B      ┆ 2    ┆ B            ┆ 20   │
│ null   ┆ null ┆ D            ┆ 40   │
│ C      ┆ 3    ┆ null         ┆ null │
└────────┴──────┴──────────────┴──────┘
```

**同名非键列会自动加后缀**，不用手写 `suffixes`：

```python
pl.DataFrame({"symbol": ["A"], "px": [1.0]}).join(
    pl.DataFrame({"symbol": ["A"], "px": [2.0]}), on="symbol", suffix="_book")
# 结果列：symbol, px, px_book
```

时间对齐用 `join_asof`（详解见第 7 章 §4，**默认 LEFT，和 DuckDB 的 INNER 不同**）：

```python
trades.join_asof(snaps, on="t", strategy="backward")   # 取"不大于当前时间"的最近一条
trades.join_asof(snaps, on="t", strategy="forward")    # 取"不小于当前时间"的最近一条
```

两个前置条件：两边都按 `on` 排好序、键的类型与单位一致（`Datetime("ns")` 对 `Datetime("us")` 会报错）。

---

## 8. 时间序列

### 8.1 `group_by_dynamic`：不规则时间 → 规整桶

```python
df.group_by_dynamic("t", every="5m", period="5m", closed="left").agg(
    pl.len().alias("trades"),
    pl.col("px").first().alias("open"),
    pl.col("px").last().alias("close"),
    pl.col("vol").sum().alias("volume"),
)
```

```text
┌─────────────────────┬────────┬───────┬───────┬────────┐
│ t                   ┆ trades ┆ open  ┆ close ┆ volume │
│ datetime[ns]        ┆ u32    ┆ f64   ┆ f64   ┆ i64    │
╞═════════════════════╪════════╪═══════╪═══════╪════════╡
│ 2026-11-04 01:30:00 ┆ 1658   ┆ 70.84 ┆ 70.14 ┆ 735900 │
│ 2026-11-04 01:35:00 ┆ 1568   ┆ 70.18 ┆ 70.22 ┆ 694700 │
└─────────────────────┴────────┴───────┴───────┴────────┘
5 分钟桶共 48 个
```

**必须先排序**，乱序输入直接报错（这是保护，不是限制）：

```text
InvalidOperationError: argument in operation 'group_by_dynamic' is not sorted,
please sort the 'expr/series/column' first
```

参数怎么选：

| 参数 | 含义 | 坑 |
|---|---|---|
| `every` | 桶宽 | 可以与 `period` 不同（滚动窗口） |
| `period` | 每个桶覆盖的时间跨度 | 默认等于 `every`；`period > every` 就是滑动窗口，会产生重叠 |
| `offset` | 整个网格平移 | `offset="-3m"` 在整 5 分钟前 3 分钟切：01:27 / 01:32 / 01:37… |
| `closed` | 区间开闭 | `"left"` 表示 `[start, end)`，做 bar 一般都用这个 |

`offset` 的实测效果（同一份数据，只改 offset）：

```text
默认        → 01:30:00 / 01:35:00 / 01:40:00
offset="-3m" → 01:27:00 / 01:32:00 / 01:37:00   ← 首桶只有 645 笔
```

第一个桶 645 笔而不是 1500 笔，因为数据从 01:30 才开始、而桶从 01:27 起算。**做 bar 时第一个桶和最后一个桶通常是不完整的**，要么丢弃，要么在输出里标记，别让它们进指标计算。

### 8.2 `upsample`：补成规整网格

```python
sparse.upsample(time_column="t", every="30s").select("t", pl.col("px").forward_fill())
```

```text
┌─────────────────────┬──────┐
│ t                   ┆ px   │
│ datetime[μs]        ┆ f64  │
╞═════════════════════╪══════╡
│ 2026-11-04 09:30:00 ┆ 10.0 │
│ 2026-11-04 09:30:30 ┆ 10.0 │   ← 补出来的点
│ 2026-11-04 09:31:00 ┆ 10.2 │
│ 2026-11-04 09:31:30 ┆ 10.2 │   ← 补出来的点
└─────────────────────┴──────┘
5 个原始点 → 9 个点
```

补出来的点是 `null`，**要不要 `forward_fill` 取决于语义**：盘口快照可以前向填充，成交量不行（补出来必须是 0，填成前值会虚增总量）。

---

## 9. 重塑

```python
# 长 → 宽：side 的三种取值变成三列
wide = df.pivot(on="side", index="m", values="vol", aggregate_function="sum")
# 列名变成 ['m', '1', '-1', '0'] —— 是字符串，因为原值是整数

# 宽 → 长
wide.unpivot(on=["1", "-1", "0"], variable_name="side", value_name="vol")

# 列表列展开成行
pl.DataFrame({"symbol": ["A", "B"], "pxs": [[10.0, 10.1, 10.2], [20.0]]}).explode("pxs")
# A 变成 3 行、B 保持 1 行，其他列跟着重复

# 拼接
pl.concat([a, b], how="vertical_relaxed")   # Int64 + Float64 需要 relaxed，否则 SchemaError
pl.concat([a, b], how="horizontal")         # 必须等高
pl.concat([a, b], how="diagonal")           # 按列名对齐，缺的补 null
```

`pivot` 的 `aggregate_function` 不能省：同一个 `(index, on)` 组合有多行时必须告诉它怎么合并（`sum` / `mean` / `first` / `len`）。

---

## 10. 惰性与性能

```python
q.explain()                        # 看计划，不执行
q.collect()                        # 执行，返回 DataFrame
q.collect(engine="streaming")      # 流式引擎（数据大于内存时用）
df.lazy()                          # 已经物化的表也能回到计划
```

`explain()` 里会看到这种形状（`dt` 是分区键，被改写成了集合谓词）：

```text
UNION[maintain_order: false]
  PLAN 0:
    AGGREGATE[len().alias("n"), col("vol").sum().alias("v")] BY [col("dt")]
      simple π 2/2 ["dt", "vol"]
        Parquet SCAN [.../dt=2026-11-04/symbol=002322.SZ/part-0.parquet]
        PROJECT 3/9 COLUMNS
        SELECTION: (col("dt").is_in([...]) & (col("symbol") == "002322.SZ"))
        ESTIMATED ROWS: 73375
  PLAN 1:
    ...（dt=2026-11-05 那个文件，计划相同）
```

两件事值得注意：

- **`UNION` 是按文件拆的**：每个 Parquet 文件一个子计划，各自下推 `SELECTION`。所以"分区设计"直接决定这里有 2 个还是 2000 个子计划
- **`PROJECT 3/9`**：只读 3 列。这就是为什么"先 `select` 再算"不用刻意优化，优化器自己会做——但如果你先用 `collect()` 把整表拉进来，它就没机会了

性能上有三条最有效的动作，按收益排序：

1. **别 `collect` 得比需要更早**：每次 `collect` 都是一次物化，中间表攒多了内存就上去了
2. **过滤条件写成本地可下推的形式**：`pl.col("dt") == "2026-11-04"` 能变成分区裁剪；`pl.col("dt").str.starts_with("2026-11")` 不能
3. **避免逐行 Python**：一旦出现 `map_elements`，就退回 pandas 的速度了。能用 `str`/`dt`/`when` 表达的，一律用表达式

---

## 11. pandas → Polars 对照速查

| 想做的事 | pandas | Polars |
|---|---|---|
| 读 Parquet | `pd.read_parquet(p)` | `pl.scan_parquet(p)`（惰性） |
| 取列 | `df["px"]` / `df.px` | `df["px"]`（**返回 Series**）/ `pl.col("px")`（表达式） |
| 选多列 | `df[["a","b"]]` | `df.select("a","b")` |
| 按条件筛 | `df[df.px > 71]` | `df.filter(pl.col("px") > 71)` |
| 多条件 | `df[(df.a>1) & (df.b<2)]` | `df.filter((pl.col("a")>1) & (pl.col("b")<2))` |
| 新增列 | `df["c"] = df.a + df.b` | `df.with_columns((pl.col("a")+pl.col("b")).alias("c"))` |
| 条件赋值 | `np.where(cond, x, y)` | `pl.when(cond).then(x).otherwise(y)` |
| 重命名 | `df.rename(columns={...})` | `df.rename({...})` |
| 删列 | `df.drop(columns=[...])` | `df.drop(...)` |
| 分组聚合 | `df.groupby("k").agg(...)` | `df.group_by("k").agg(...)` |
| 组内变换 | `df.groupby("k")["v"].transform("sum")` | `pl.col("v").sum().over("k")` |
| 排序 | `df.sort_values("v", ascending=False)` | `df.sort("v", descending=True)` |
| 去重 | `df.drop_duplicates(["k"], keep="last")` | `df.unique(subset=["k"], keep="last")` |
| 前 N 行 | `df.head(5)` | `df.head(5)` |
| 前 N 大 | `df.nlargest(5, "v")` | `df.top_k(5, by="v")`（`by` 是 keyword-only） |
| 重设索引 | `df.reset_index(drop=True)` | `df.with_row_index()` |
| 透视 | `df.pivot_table(index=, columns=, values=, aggfunc=)` | `df.pivot(on=, index=, values=, aggregate_function=)` |
| 逆透视 | `df.melt(...)` | `df.unpivot(on=, variable_name=, value_name=)` |
| 拼接 | `pd.concat([a, b])` | `pl.concat([a, b], how="vertical_relaxed")` |
| 连接 | `df.merge(r, on="k", how="left")` | `df.join(r, on="k", how="left")` |
| 时间对齐 | `pd.merge_asof(..., direction="backward")` | `df.join_asof(r, on="t", strategy="backward")` |
| 滚动均值 | `df.v.rolling(100).mean()` | `pl.col("v").rolling_mean(window_size=100)` |
| 时间分桶 | `df.resample("1min").agg(...)` | `df.group_by_dynamic("t", every="1m").agg(...)` |
| 应用函数 | `df.apply(f, axis=1)` | **别用**；拆成表达式，或 `map_batches` |
| 转回去 | — | `df.to_pandas()` |
| 从 pandas 来 | — | `pl.from_pandas(pdf)` |

---

## 12. 踩坑

| 坑 | 表现 | 正确做法 |
|---|---|---|
| 用 `read_parquet` 做分析 | 17.0 ms vs 1.3 ms | 一律 `scan_parquet` + `collect` |
| 多条件漏括号 | `ValueError` 或结果莫名其妙 | 每个比较单独括号 |
| `with_columns` 里引用同批新列 | `ColumnNotFoundError` | 拆成两次 `with_columns`，或重算 |
| 多合约用 `shift` / `rolling` 忘了 `over` | 上一支股票的收盘价串进来 | `shift(1).over("symbol")` |
| `group_by_dynamic` 没排序 | `InvalidOperationError ... is not sorted` | 先 `sort("t")`；别关掉这个检查 |
| 时间桶首尾不完整 | 第一个桶只有 645 笔 | 丢弃或标记；指标计算要剔除 |
| 把 `ESTIMATED ROWS` 当结果行数 | 73375 vs 实际过滤结果 | 看 `df.height` |
| `unique(keep="any")` 做对账 | 留哪条不确定 | `keep="last"` + 先按 `seq` 排序 |
| `Int64` 与 `Float64` 拼接 | `SchemaError` | `how="vertical_relaxed"` |
| `cross` join 传了 `on` | `ValueError: cross join should not pass join keys` | 不传 `on` |
| UTC 时间当本地时间用 | 小时数差 8，且没人发现 | 全链路统一 UTC，或显式 `convert_time_zone` |
| 用 `map_elements` 处理百万行 | 慢到以为程序卡了 | 表达式 + 命名空间；实在不行 `map_batches` |
| `int64` 纳秒直接当时间格式化 | 得到 1970 年 | 先 `cast(pl.Datetime("ns"))` |

---

## 出处

- 全部示例与输出：本仓库 `py/polars_cookbook.py`（polars 2.1.0，2026-11-11 实跑）
- 数据：`lake/data/lake/trades`（587,000 行 / 9 列 / 2 天 / 8 合约），由 `py/gen_tick.py` 生成
- 机制与边界（惰性下推、流式写入、`join_asof` 与 DuckDB 的语义差异）：第 7 章
- 官方 API 索引：<https://docs.pola.rs/api/python/stable/reference/>
