# 第 8 章 · 数据湖工程：分区、幂等、去重与合并

!!! abstract "本章结论（读完你应该能复述这三句）"
    1. **分区粒度不是越细越好**。文件数 2 → 8 → 40，点查 3.56 → 19.99 ms。把开销拆开：`WHERE false`（一个字节都不读）也要 17.30 ms，即 **0.43–0.77 ms/文件**。慢的是"打开并读 footer"，不是剪枝失效。给 40 文件布局补上 `hour` 谓词后 18.26 ms——**几乎等于纯绑定开销**，说明扫描已经降到 0，剩下的全是打开文件的钱。
    2. **幂等只能靠"写新目录 + 原子替换"**。用 run 号文件名直接往同一目录追加两次：8 → 16 个文件、587,000 → 1,174,000 行，重复率 100%。而且 `write_dataset` 的默认行为会**先删分区内旧文件**，"追加两次"看起来居然幂等——这个默认值很容易把结论测反。
    3. **去重键必须是 `(symbol, ts, seq, channel)`**。只用 `(symbol, ts)` 合并掉 2,921 行，`sum(vol)` 少 0.50%——同一纳秒的多笔成交是真实成交，不是重复。只加 `seq` 完全没用（唯一行数一个没变），因为同一 `seq` 在不同 `channel` 上复用。

对应任务卡：仓库内 `docs/week08.md` ｜ 本章实验脚本：`py/w08_lake.py` ｜ 原始结果：`reports/w08-lake.md`

---

## 1. 目录即表：分区是什么

Hive 分区把列值编码进**路径**。查询时引擎先看路径，不匹配的目录连打开都不需要：

```text
lake/data/raw/trades/
├── dt=2026-11-04/
│   ├── symbol=001317.SZ/part-0.parquet
│   ├── symbol=002322.SZ/part-0.parquet
│   └── ...
└── dt=2026-11-05/
    └── ...
```

`WHERE dt = '2026-11-05'` 只走一个目录；`WHERE dt = '2026-11-05' AND symbol = '002322.SZ'` 只走一个文件。这就是"分区剪枝"，比行组剪枝更早、更便宜——**因为它不需要读任何文件的 footer**。

代价在另一头：路径不是免费的。每个分区值都要在文件系统里变成一个目录项，文件数随分区基数**乘性增长**。

<figure class="diagram">
<svg width="100%" viewBox="0 0 592 232" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
<title>三种分区布局的文件数、行组数与点查延迟</title>
<desc>A 按天分区只有 2 个文件，每个 8 个行组；B 按天加合约 8 个文件；C 再切到小时变成 40 个文件，每个文件只剩 1 个行组。点查延迟随文件数上升。</desc>
<text x="0" y="14" font-size="13.2" font-weight="500" fill="currentColor">A 只按天：2 个文件，文件内 8 个行组</text>
<g stroke="currentColor" stroke-opacity="0.35" fill="none">
<rect x="0" y="24" width="150" height="34"/>
<rect x="158" y="24" width="150" height="34"/>
</g>
<g font-size="10" text-anchor="middle" fill="currentColor">
<text x="75" y="38">dt=2026-11-04</text><text x="75" y="52" fill-opacity="0.6">293,500 行 · 8 行组</text>
<text x="233" y="38">dt=2026-11-05</text><text x="233" y="52" fill-opacity="0.6">293,500 行 · 8 行组</text>
</g>
<text x="330" y="38" font-size="10" fill="currentColor" fill-opacity="0.75">点查 3.56 ms ｜ 绑定 1.53 ms</text>
<text x="330" y="52" font-size="10" fill="currentColor" fill-opacity="0.55">剪到 1 文件，行组再剪到 1 个</text>
<text x="0" y="86" font-size="13.2" font-weight="500" fill="currentColor">B 天 × 合约：8 个文件，每文件 2–4 个行组</text>
<g stroke="currentColor" stroke-opacity="0.35" fill="none">
<rect x="0" y="96" width="66" height="34"/><rect x="74" y="96" width="66" height="34"/>
<rect x="148" y="96" width="66" height="34"/><rect x="222" y="96" width="66" height="34"/>
<rect x="296" y="96" width="66" height="34"/><rect x="370" y="96" width="66" height="34"/>
<rect x="444" y="96" width="66" height="34"/><rect x="518" y="96" width="66" height="34"/>
</g>
<g font-size="8.6" text-anchor="middle" fill="currentColor" fill-opacity="0.75">
<text x="33" y="116">sym 1</text><text x="107" y="116">sym 2</text>
<text x="181" y="116">sym 3</text><text x="255" y="116">sym 4</text>
<text x="329" y="116">sym 1</text><text x="403" y="116">sym 2</text>
<text x="477" y="116">sym 3</text><text x="551" y="116">sym 4</text>
</g>
<text x="0" y="150" font-size="10" fill="currentColor" fill-opacity="0.75">点查 5.51 ms ｜ 绑定 4.18 ms</text>
<text x="0" y="166" font-size="13.2" font-weight="500" fill="currentColor">C 天 × 合约 × 小时：40 个文件，每文件只剩 1 个行组</text>
<g stroke="currentColor" stroke-opacity="0.45" fill="none">
<rect x="0" y="176" width="9" height="26"/><rect x="12" y="176" width="9" height="26"/>
<rect x="24" y="176" width="9" height="26"/><rect x="36" y="176" width="9" height="26"/>
<rect x="48" y="176" width="9" height="26"/><rect x="60" y="176" width="9" height="26"/>
<rect x="72" y="176" width="9" height="26"/><rect x="84" y="176" width="9" height="26"/>
<rect x="96" y="176" width="9" height="26"/><rect x="108" y="176" width="9" height="26"/>
<rect x="120" y="176" width="9" height="26"/><rect x="132" y="176" width="9" height="26"/>
<rect x="144" y="176" width="9" height="26"/><rect x="156" y="176" width="9" height="26"/>
<rect x="168" y="176" width="9" height="26"/><rect x="180" y="176" width="9" height="26"/>
<rect x="192" y="176" width="9" height="26"/><rect x="204" y="176" width="9" height="26"/>
<rect x="216" y="176" width="9" height="26"/><rect x="228" y="176" width="9" height="26"/>
</g>
<text x="244" y="184" font-size="8.6" fill="currentColor" fill-opacity="0.5">… 共 40 个</text>
<text x="244" y="198" font-size="10" fill="currentColor" fill-opacity="0.75">点查 19.99 ms ｜ 绑定 17.30 ms</text>
<text x="0" y="222" font-size="10" fill="currentColor" fill-opacity="0.55">下面 20 个小方块代表 40 个文件里的前 20 个：每个文件只有 1 个行组，文件内已无剪枝余地。</text>
</svg>
</figure>

!!! warning "行组和文件不是一回事"
    行组是**文件内**的剪枝单位，文件是**目录级**的剪枝单位。切到小时之后每个文件只剩一个行组，等于放弃了行组级剪枝——省下的字节抵不过多打开 38 个文件的开销。

## 2. 打开一个文件要多少钱

这是本章最有用的一个数字，也最容易测错。做法是把查询条件写成 `WHERE false`：SQL 引擎仍然必须**枚举所有文件、逐个打开、读 footer、合并 schema**，但不读任何一个数据页。

| 布局 | 文件数 | footer | 绑定 `WHERE false` | 每文件 | 点查 |
|---|---|---|---|---|---|
| A 只按天 | 2 | 17.6 KB | 1.53 ms | 0.77 ms | 3.56 ms |
| B 天 × 合约 | 8 | 24.2 KB | 4.18 ms | 0.52 ms | 5.51 ms |
| C 天 × 合约 × 小时 | 40 | 71.3 KB | 17.30 ms | 0.43 ms | 19.99 ms |

三点值得记住：

- **成本是线性的、与查询命中无关**。40 个文件 = 17.30 ms，其中没有一个字节是数据。
- **每文件成本随文件变小而下降**（0.77 → 0.43 ms），因为 footer 更小了；但文件数涨得更快，总账还是亏。
- **绑定 + 扫描 ≈ 点查**：A 是 1.53 + 2.03 = 3.56，C 是 17.30 + 2.69 = 19.99。真正吃掉时间的部分一直在绑定这一侧。

反证实验把这件事钉死了：

```text
C 40文件 无 hour 谓词 : 19.99 ms
C 40文件 + hour 谓词  : 18.26 ms   ← 剪枝生效了
C 的纯绑定开销        : 17.30 ms   ← 剩下的全是它
```

补上 `hour` 谓词后只省了 1.7 ms。**扫描已经接近 0，时间全花在打开 40 个文件上**。这也是为什么"多分区 + 谓词写全"救不了小文件问题——谓词剪枝作用于扫描阶段，而绑定发生在它之前。

!!! tip "经验值"
    在 Windows + 本地 SSD 上，一个 Parquet 文件的打开成本约 **0.4–0.8 ms**。这个数字可以当预算用：如果点查 SLA 是 20 ms，**最多容忍 20–30 个文件**。SLA 越紧，单文件目标体积越大。

**单文件多大合适？** 反推：想让固定开销占比低于 10%，20 ms 预算里最多花 2 ms → 不超过 4 个文件。行情单次点查通常要读几十 MB 到几百 MB，所以**单文件目标 256 MB–1 GB** 这个业界经验值，本质是"把文件数压到个位数"。

## 3. 幂等：写新目录，然后原子替换

批处理任务一定会重跑。重跑不能产生重复数据，这件事只有一个可靠做法：

<figure class="diagram">
<svg width="100%" viewBox="0 0 652 202" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
<title>幂等写入与直接追加的对比</title>
<desc>上面是先写临时目录再整体 rename 替换，重跑两次结果一致；下面是直接往目标目录追加，重跑两次行数翻倍。</desc>
<text x="0" y="14" font-size="13.2" font-weight="500" fill="currentColor">幂等写法：先写 .tmp，校验通过再换名</text>
<g stroke="currentColor" stroke-opacity="0.35" fill="none">
<rect x="0" y="24" width="146" height="44"/>
<rect x="166" y="24" width="146" height="44"/>
<rect x="332" y="24" width="146" height="44"/>
<rect x="498" y="24" width="146" height="44"/>
</g>
<g font-size="10" text-anchor="middle" fill="currentColor">
<text x="73" y="42">写 .tmp-1/</text><text x="73" y="58" fill-opacity="0.6">8 个文件</text>
<text x="239" y="42">校验行数 / 校验和</text><text x="239" y="58" fill-opacity="0.6">587,000 行 ✓</text>
<text x="405" y="42">rename 成 trades/</text><text x="405" y="58" fill-opacity="0.6">目录级原子操作</text>
<text x="571" y="42">重跑再来一次</text><text x="571" y="58" fill-opacity="0.6">8 文件 / 587,000 行</text>
</g>
<g stroke="currentColor" stroke-opacity="0.45" fill="none">
<path d="M148 46 h14"/><path d="M314 46 h14"/><path d="M480 46 h14"/>
</g>
<g fill="currentColor" fill-opacity="0.5">
<path d="M162 46 l-6 -3 l0 6 z"/><path d="M328 46 l-6 -3 l0 6 z"/><path d="M494 46 l-6 -3 l0 6 z"/>
</g>
<text x="0" y="96" font-size="13.2" font-weight="500" fill="currentColor">反例：run 号文件名直接追加</text>
<g stroke="currentColor" stroke-opacity="0.35" fill="none">
<rect x="0" y="106" width="146" height="44"/>
<rect x="166" y="106" width="146" height="44"/>
<rect x="332" y="106" width="146" height="44"/>
<rect x="498" y="106" width="146" height="44"/>
</g>
<g font-size="10" text-anchor="middle" fill="currentColor">
<text x="73" y="124">第 1 次写入</text><text x="73" y="140" fill-opacity="0.6">part-run1-*.parquet</text>
<text x="239" y="124">8 文件 / 587,000 行</text><text x="239" y="140" fill-opacity="0.6">看起来正常</text>
<text x="405" y="124">第 2 次写入</text><text x="405" y="140" fill-opacity="0.6">part-run2-*.parquet</text>
<text x="571" y="124" font-weight="500">16 文件 / 1,174,000 行</text><text x="571" y="140" fill-opacity="0.7">重复率 100%</text>
</g>
<g stroke="currentColor" stroke-opacity="0.45" fill="none">
<path d="M148 128 h14"/><path d="M314 128 h14"/><path d="M480 128 h14"/>
</g>
<g fill="currentColor" fill-opacity="0.5">
<path d="M162 128 l-6 -3 l0 6 z"/><path d="M328 128 l-6 -3 l0 6 z"/><path d="M494 128 l-6 -3 l0 6 z"/>
</g>
<text x="0" y="176" font-size="10" fill="currentColor" fill-opacity="0.75">两种写法都写成功了，只有一种能重跑。rename 是目录级操作，读端看到的永远是完整的一份。</text>
<text x="0" y="192" font-size="10" fill="currentColor" fill-opacity="0.55">Windows 上目录 rename 不能覆盖已存在目录，实际做法是 trades → trades.old → 删除，或写版本化路径。</text>
</svg>
</figure>

实测（两次运行）：

```text
第一次：8 个文件 / 587,000 行
第二次：8 个文件 / 587,000 行 → 幂等

带 run 号文件名追加写两次：16 个文件 / 1,174,000 行（多出 587,000 行重复）
```

!!! bug "这个坑会骗人：`write_dataset` 默认会替你删旧文件"
    我第一次跑反例时，两次追加也只有 8 个文件、587,000 行——看起来"追加也是幂等的"。原因是 `pyarrow.dataset.write_dataset` 的 `existing_data_behavior` 默认是 `delete_matching`：**写某个分区前，先把该分区目录里同格式的文件删掉**。

    这个默认值意味着：

    - 你以为在测"追加"，实际在测"覆盖"；
    - 真到了生产上（换了工具、换了版本、或写到别的目录），重复数据会立刻出现。

    反例里必须显式写 `existing_data_behavior="overwrite_or_ignore"`，才能构造出真正的追加。测并发/幂等类问题时，**先确认默认行为**，否则结论是反的。

`_SUCCESS` 标记是另一条路：写完后放一个空文件，读端只读带标记的分区。它解决的是"读到写了一半的数据"，但**不解决重跑重复**——重跑还是会写第二份。两者要一起用：先写 `.tmp`、校验、rename、再放 `_SUCCESS`。

## 4. 去重键：`(symbol, ts)` 为什么不够

行情数据里"同一纳秒多笔成交"是常态，不是异常。看实测：

| 去重键 | 唯一行数 | 合并掉 |
|---|---|---|
| `(symbol, ts)` | 584,079 | 2,921 |
| `(symbol, ts, seq)` | 584,079 | 2,921 |
| `(symbol, ts, seq, channel)` | 586,262 | 738 |

两个反直觉的点：

**第一，加 `seq` 一点用都没有。** 唯一行数从 584,079 一个没变。原因是交易所的 `seq` 是**按 channel 独立编号**的：channel A 的 seq 1000 和 channel B 的 seq 1000 是两条不同的回报。必须 `(seq, channel)` 一起看才有唯一性。

**第二，剩下的 738 行才是真重复。** 加上 `channel` 后从 587,000 降到 586,262，差 738 行——这些是重传/补发的重复回报。**去重的目标是把 587,000 降到 586,262，而不是降到 584,079。**

丢数据的代价是可量化的：

```text
按 (symbol, ts) 去重后 sum(vol)：264,845,000 / 原始 266,165,100
                                  少了 1,320,100（0.50%）
```

0.5% 的成交量，在分钟 bar 的 VWAP 上会直接体现为偏差。而且**方向不确定**：`any_value()` 取哪一笔取决于扫描顺序，重跑一次结果可能就变了——这种 bug 极难查。

!!! danger "去重键要写进 schema 文档，不是写在脚本里"
    正确键：`(exchange, symbol, ts, seq, channel)`。少一个 `exchange` 就会在多交易所（沪深 + 期货 + 期权）合并时出事。

    自检问句：**"如果这条数据出现两次，它是不是同一条？"** 只有全部字段逻辑相等才算重复。`ts` 相同、`px` 不同，是两笔成交。

## 5. 分层 rollup：raw → 1s → 1m

原始 tick 不是给查询用的，是给"重算"用的。查询走聚合层：

<figure class="diagram">
<svg width="100%" viewBox="0 20 648 178" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
<title>raw → 1s → 1m 三层的数据量与压缩比</title>
<desc>原始 tick 587,000 行 7.9 MB；1 秒线 111,626 行 1.16 MB；1 分钟线 1,913 行 36 KB。每往上一层，行数按聚合窗口缩小，但压缩比不按比例提升。</desc>
<g stroke="currentColor" stroke-opacity="0.4" fill="none">
<rect x="0" y="24" width="640" height="30"/>
<rect x="90" y="66" width="550" height="30"/>
<rect x="180" y="108" width="460" height="30"/>
</g>
<g font-size="10" fill="currentColor">
<text x="10" y="43">raw  587,000 行 · 7,897 KB · 100%</text>
<text x="100" y="85">1s  111,626 行 · 1,164 KB · 6.8× 压缩</text>
<text x="190" y="127">1m  1,913 行 · 36 KB · 225× 压缩</text>
</g>
<text x="0" y="160" font-size="10" fill="currentColor" fill-opacity="0.75">行数按窗口缩小，体积缩得更快——聚合后基数骤降，字典与 RLE 才开始起作用。</text>
<text x="0" y="176" font-size="10" fill="currentColor" fill-opacity="0.55">但 1s 层只压到 6.8×：tick 里"每秒十几笔"的稀疏性还在，列式编码吃不到多少红利。</text>
<text x="0" y="192" font-size="10" fill="currentColor" fill-opacity="0.55">所以 1s 层的价值不是省空间，是省 CPU：省掉每次都从 raw 重算。</text>
</svg>
</figure>

| 层 | 行数 | 体积 | 相对 raw |
|---|---|---|---|
| raw（tick） | 587,000 | 7,897 KB | 1× |
| 1s | 111,626 | 1,164 KB | 6.8× |
| 1m | 1,913 | 36 KB | 225× |

谁负责生成哪一层，比"分几层"更重要：

| 层 | 生成方式 | 重算范围 | 保留期 |
|---|---|---|---|
| raw | 采集落盘，**只追加不修改** | 永不重算 | 全量（冷存储） |
| 1s | 从 raw 聚合 | 按天 / 按合约 | 90 天 |
| 1m | 从 **1s** 聚合（不是从 raw） | 按天 | 全量 |

!!! tip "1m 从 1s 聚合，不是从 raw"
    从 1s 聚合 1m 快一个量级，但如果 1s 层用的是 `avg(px)` 而不是 `sum(px*vol)/sum(vol)` 这类**可再加总**的量，就会算错。

    规矩：聚合层只存**可再加总的量**（count、sum、min、max）。VWAP 这种比值，要么存 `(sum_px_vol, sum_vol)` 两个分量，要么每层都从 raw 重算。存一个 VWAP 字段在中间层，是最常见的错误。

## 6. compaction：把 40 个文件合成 8 个

流式写入天然产生小文件（每小时、每批次一个）。合并是必须的运维动作：

```text
合并前：40 个文件 / 7.42 MB / 点查 25.18 ms
合并后： 8 个文件 / 7.65 MB / 点查  6.03 ms
→ 文件数 ÷5，点查 ×4.2，体积反而 +3%
```

体积变大是正常的：合并后行组变大（8,192 → 32,768 行），压缩率略降。**用 3% 的空间换 4 倍的延迟，这笔买卖在任何场景下都成立。**

触发条件，按优先级：

1. **文件数超过阈值**：单分区 > 10 个文件就该合并（对应上面 0.4–0.8 ms/文件的预算）。
2. **平均文件小于 64 MB**：说明写入批次太碎。
3. **有删除 / 更新累积**：纯追加场景不需要，有 upsert 才需要。

合并时的排序键选择——直接决定合并值不值：

!!! warning "合并时不排序，等于只做了一半"
    合并 40 个文件只是减少了打开次数。如果合并后文件内仍按到达顺序（ts 交错），行组级剪枝照样无效。

    正确做法：**合并 = 重写 + 按 `(symbol, ts)` 排序 + 增大行组**。这一步的收益（第 3 章 E1a：单合约查询读放大 4.0× → 1.1×）通常比"减少文件数"更大。

合并期间的读写一致性：先写 `trades.new/`，完成后 rename 替换 `trades/`；读端要么读旧的、要么读新的，不会读到一半（第 3 节的同一套机制）。

## 7. 质量报告：先量化，再谈"数据脏"

每批数据入库前跑一次，结果落成一张表，比事后排查便宜一百倍：

```text
行数 586,996
同纳秒相邻 2,921 笔
|涨跌| > 5% 的相邻跳变 117 笔
最大单笔涨幅 901.9%
2026-11-04  行 293,500  合约 4  非正价格 0
2026-11-05  行 293,500  合约 4  非正价格 0
```

四项指标各自抓不同的病：

| 指标 | 抓什么 | 本数据的读数 |
|---|---|---|
| 同纳秒笔数 | 交易所撮合的并发成交（**正常**） | 2,921 笔 |
| 相邻跳变 > 5% | 缺 tick、复权、单位错、脏价 | 117 笔（0.02%） |
| 最大单笔涨幅 | 极端值，看是否需要涨跌停校验 | 901.9%（合成数据注入的异常） |
| 非正价格 / 空值 | 解析错误或字段错位 | 0 |

!!! warning "缺口不都是质量问题"
    第 6 章测过：单合约一天从首笔到末笔应含 241 分钟，实际只有 151 分钟有成交，缺 90 分钟——且 90 个全部落在 11:30–12:59，是**休市**。

    "按时间补空"必须先知道交易日历。把休市当缺失去补零，会造出 90 个假的零成交量 bar，然后所有均线指标全错。判断顺序应该是：**先查日历，再看缺口**。

## 8. 要不要 Iceberg / Delta

判断标准很实际，按顺序问：

1. **有多个写入方吗？** 只有一个批处理任务在写 → 不需要。多个服务并发写同一张表 → 需要（乐观并发 + 冲突检测）。
2. **需要 upsert / 删除吗？** 纯追加 → 不需要。要按主键改数 → 需要（merge-on-read 或 copy-on-write）。
3. **需要时间旅行 / 快照隔离吗？** 审计、回溯、A/B 重算 → 需要。
4. **需要跨引擎一致读吗？** Spark + Trino + DuckDB 同时读同一份 → 需要（统一元数据）。

四条都不满足，**纯目录 + Hive 分区就够**。它的运维成本接近于零，而 Iceberg 带来的是：元数据服务（catalog）、定期 `rewrite_data_files`、快照过期清理，以及一套新的调试技能。

!!! note "单机规模的一个现实"
    本章 587,000 行、8 MB 的数据集，用 Iceberg 的收益是负的：manifest 文件本身就比数据大。Iceberg 的门槛量级在 **TB 起、多写入方**。在到那个规模之前，"目录 + 分区 + 幂等 rename + 定期 compaction"这套原始方案没有任何问题——而且你能完全看懂它在干什么。

## 9. schema 演进的安全边界

| 操作 | 安全吗 | 说明 |
|---|---|---|
| 加列（末尾、可空） | ✅ 安全 | 老文件读出来是 null，无需重写 |
| 加列（带默认值） | ⚠️ 有条件 | 默认值只对新写入生效；老数据仍是 null，读端要兼容 |
| 改列类型（int32 → int64） | ⚠️ 需重写 | 部分引擎能隐式提升，跨语言不可靠 |
| 改列类型（int64 → int32） | ❌ 危险 | 溢出静默截断 |
| 删列 | ✅ 逻辑删除 | 不重写文件，读端不再投影这一列 |
| 改名 | ❌ 破坏 | Parquet 存的是列名，等于删 + 加，老数据变 null |
| 改列顺序 | ✅ 无影响 | Parquet 按名字匹配，不按位置（CSV 才按位置） |

!!! tip "改名的正确姿势"
    加新列 → 双写一段时间 → 读端切到新列 → 删旧列。不要原地改名。这条在任何列式格式上都成立，因为列名写在每个文件的 footer 里。

## 10. 踩坑

| 坑 | 表现 | 正确做法 |
|---|---|---|
| 分区切太细 | 40 文件点查 19.99 ms，其中 17.30 ms 是打开文件 | 单文件目标 256 MB–1 GB，单分区文件数 < 10 |
| 靠"谓词写全"救小文件 | 加 `hour` 谓词只省 1.7 ms | 谓词剪枝作用于扫描，绑定在它之前 |
| 以为 `write_dataset` 会追加 | 默认 `delete_matching` 先删旧文件，"追加"其实在覆盖 | 学幂等之前先搞清默认行为 |
| 重跑产生重复 | 16 文件 / 1,174,000 行 | 写 `.tmp` + 校验 + 原子 rename |
| 用 `(symbol, ts)` 去重 | 合并 2,921 行，`sum(vol)` 少 0.50% | `(exchange, symbol, ts, seq, channel)` |
| 以为加 `seq` 就唯一 | 唯一行数一个没变 | `seq` 按 channel 独立编号 |
| 中间层存 VWAP | 从 1s 聚合 1m 时算错 | 只存可再加总的量，或存分量 |
| 合并只减文件数不排序 | 点查变快但读放大不变 | 合并 = 重写 + 排序 + 增大行组 |
| 用 `lag()` 数缺口 | 把休市当丢数据，补出假 bar | 先查交易日历 |
| 小规模就上 Iceberg | 元数据比数据大 | 四条判断标准都不满足时，目录方案更好 |

## 11. 出处

- Apache Parquet 规范 — *File Format*（footer、row group、column chunk 的层级关系）
- Apache Arrow — `pyarrow.dataset.write_dataset` 的 `existing_data_behavior`、`partitioning_flavor="hive"`
- DuckDB 文档 — *Reading Parquet*（`hive_partitioning`、文件级剪枝）、`EXPLAIN ANALYZE` 输出中的 `Scanning Files` / `Total Files Read`
- Apache Iceberg 规范 — *Table Metadata*、*Manifest Lists*（仅读规范，未实测）
- 本章数据：`reports/w08-lake.md`（脚本 `py/w08_lake.py`，2026-11-11，pyarrow 26.0.1 / duckdb 2.0.0）

[^1]: 绑定开销的测法：`SELECT count(*) FROM read_parquet('...', hive_partitioning=true) WHERE false`。此时引擎仍需枚举全部文件、打开并读 footer、合并 schema，但不读数据页。
[^2]: `existing_data_behavior` 默认值为 `delete_matching`，见 pyarrow 26.0.1 `pyarrow/dataset` 中 `write_dataset` 的参数默认值；该行为会在写入前删除目标分区目录内同格式文件。⚠️ 不同版本的默认值可能变化，生产代码应显式指定。
[^3]: 0.43–0.77 ms/文件 是本机 Windows 11 + NTFS + 本地 SSD 的中位数（每档 15 次取中位）。对象存储（S3/OSS）上单文件开销高一个量级，届时"单文件 256 MB–1 GB"应上调。

---

**下一步**：第 9 章把这些旋钮拧成一套可复现的基准——同一份数据、同一组查询，量出"改一个参数到底值不值"。
