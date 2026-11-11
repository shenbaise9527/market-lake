# 第 9 章 · 性能调优：一套可复现的基准，和它拆穿的三个错觉

!!! abstract "本章结论（读完你应该能复述这三句）"
    1. **"哪个配置最快"在小数据上是噪声决定的**。36 组配置的矩阵，第一次跑三个目标选出同一个最优，第二次跑（同一份数据、同一台机器）变成三个目标各不相同——Q1 全程只差 2.1 ms，而一次 DuckDB 冷启动就是 20 ms。结论的置信度来自**指纹 + 重复 + 规模放大**，不来自表格本身。
    2. **规模才制造取舍**。24,000,000 行（870 MB 内存 / 1 GB 未压缩）上：zstd-9 写入比不压缩慢 **26 倍**，只换来体积 −65%；全扫却真实受益于压缩（84 → 61 ms）。8 MB 数据上看不见这些，因为所有配置都被固定开销淹没。
    3. **SLA 按 p99 定，不按中位数**。同一查询跑 100 次：热连接 p50 = 2.66 / p99 = 3.55 ms（×1.3）；新连接 p50 = 20.63 / p99 = 24.74 ms（×1.2，但绝对值是热连接的 7.8 倍）。拿 p50 做 SLA，等于把 1% 的请求判死刑。

对应任务卡：仓库内 `docs/week09.md` ｜ 本章实验脚本：`py/w09_bench.py` ｜ 原始结果：`reports/w09-bench.md`

---

## 1. 基准套件的四要素

第 3–8 章的每个数字都来自同一条流水线：

<figure class="diagram">
<svg width="100%" viewBox="0 20 652 188" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
<title>可复现基准的四要素流水线</title>
<desc>固定数据集加上固定查询集，先算数据指纹与查询指纹，然后跑参数矩阵，画出帕累托前沿，最后把结论固化为如果那么表。</desc>
<g stroke="currentColor" stroke-opacity="0.35" fill="none">
<rect x="0" y="24" width="146" height="52"/>
<rect x="166" y="24" width="146" height="52"/>
<rect x="332" y="24" width="146" height="52"/>
<rect x="498" y="24" width="146" height="52"/>
</g>
<g font-size="10" text-anchor="middle" fill="currentColor">
<text x="73" y="42" font-weight="500">固定数据集</text>
<text x="73" y="56" fill-opacity="0.6">行数 / 文件数 / sha256</text>
<text x="239" y="42" font-weight="500">固定查询集</text>
<text x="239" y="56" fill-opacity="0.6">点查 · 窄扫 · 全扫 · 聚合</text>
<text x="405" y="42" font-weight="500">指纹校验</text>
<text x="405" y="56" fill-opacity="0.6">每组配置结果必须一致</text>
<text x="571" y="42" font-weight="500">矩阵 + 重复</text>
<text x="571" y="56" fill-opacity="0.6">36 组 × 中位数(7)</text>
</g>
<g stroke="currentColor" stroke-opacity="0.45" fill="none">
<path d="M148 50 h14"/><path d="M314 50 h14"/><path d="M480 50 h14"/>
</g>
<g fill="currentColor" fill-opacity="0.5">
<path d="M162 50 l-6 -3 l0 6 z"/><path d="M328 50 l-6 -3 l0 6 z"/><path d="M494 50 l-6 -3 l0 6 z"/>
</g>
<text x="0" y="106" font-size="13.2" font-weight="500" fill="currentColor">产出</text>
<g stroke="currentColor" stroke-opacity="0.35" fill="none">
<rect x="0" y="116" width="200" height="40"/>
<rect x="220" y="116" width="200" height="40"/>
<rect x="440" y="116" width="204" height="40"/>
</g>
<g font-size="10" text-anchor="middle" fill="currentColor">
<text x="100" y="132">帕累托前沿图</text><text x="100" y="146" fill-opacity="0.6">体积 vs 耗时，只留不被支配的</text>
<text x="320" y="132">尾延迟分布</text><text x="320" y="146" fill-opacity="0.6">p50 / p95 / p99 × 100 次</text>
<text x="542" y="132">"如果…那么…"表</text><text x="542" y="146" fill-opacity="0.6">结论 + 一条复现命令</text>
</g>
<text x="0" y="186" font-size="10" fill="currentColor" fill-opacity="0.75">指纹是这套流程的锚：36 组配置的 Q3 结果必须逐位一致（count=587,000，sum_vol=266,165,100）。</text>
<text x="0" y="200" font-size="10" fill="currentColor" fill-opacity="0.55">行组大小不同会改变浮点求和顺序，所以指纹只用整数聚合与 min/max，不用 sum(px)。</text>
</svg>
</figure>

**查询指纹**值得单独强调。改任何参数都可能改变结果——字典编码的 `any_value` 取值、浮点求和顺序、行组边界的聚合顺序。每组配置跑完先比对 `count + sum(vol) + min(px) + max(px)`，不一致直接终止。基准测出"快了 3 倍"而结果变了的次数，比想象中多。

## 2. 矩阵：36 组配置的全貌

轴：行序（到达 / 排序）× 编码（plain / dict）× 压缩（none / snappy / zstd-3）× 行组（8,192 / 32,768 / 131,072）。587,000 行单文件布局，完整数字在 `reports/w09-bench.md`，这里只看范围：

| 查询 | 最快 | 最慢 | 差距 |
|---|---|---|---|
| Q1 点查（1 合约 1 分钟） | 2.25 ms | 4.38 ms | 1.9× |
| Q3 全扫（全部 7 列） | 3.26 ms | 8.36 ms | 2.6× |
| Q4 聚合（分钟 bar） | 5.72 ms | 13.42 ms | 2.3× |

第一眼的结论很诱人："调参数能让全扫快 2.6 倍"。但先看帕累托前沿——它比"最快"诚实得多：

<figure class="diagram">
<svg width="100%" viewBox="0 20 640 218" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
<title>体积 vs 全扫耗时的帕累托前沿（Q3，36 组实测）</title>
<desc>横轴文件体积 5.4 到 11.6 MB，纵轴全扫耗时 3 到 8 毫秒。四个前沿点连成左下边界，其余 32 个配置被支配。</desc>
<g stroke="currentColor" stroke-opacity="0.25" fill="none">
<path d="M70 30 V180 H630"/>
</g>
<g font-size="9" fill="currentColor" fill-opacity="0.6">
<text x="120" y="194">6 MB</text><text x="300" y="194">8 MB</text><text x="482" y="194">10 MB</text>
<text x="44" y="184">3.0</text><text x="44" y="154">4.0</text><text x="44" y="94">6.0</text><text x="44" y="34">8.0</text>
</g>
<g stroke="currentColor" stroke-opacity="0.5" fill="none">
<path d="M85 141 L96 160 L126 168 L460 172" stroke-dasharray="4 3"/>
</g>
<g fill="currentColor" fill-opacity="0.35">
<circle cx="141" cy="65" r="3"/><circle cx="136" cy="94" r="3"/><circle cx="132" cy="63" r="3"/>
<circle cx="125" cy="43" r="3"/><circle cx="95" cy="54" r="3"/><circle cx="610" cy="80" r="3"/>
<circle cx="604" cy="131" r="3"/><circle cx="603" cy="95" r="3"/><circle cx="463" cy="56" r="3"/>
<circle cx="457" cy="117" r="3"/><circle cx="456" cy="78" r="3"/><circle cx="460" cy="91" r="3"/>
</g>
<g fill="currentColor">
<circle cx="85" cy="141" r="4.5"/><circle cx="96" cy="160" r="4.5"/>
<circle cx="126" cy="168" r="4.5"/><circle cx="460" cy="172" r="4.5"/>
</g>
<g font-size="9" fill="currentColor" fill-opacity="0.85">
<text x="95" y="136">dict-zstd rg131k</text>
<text x="106" y="163">dict-zstd rg32k</text>
<text x="136" y="181">plain-zstd rg32k</text>
<text x="428" y="162">dict-snappy rg32k</text>
</g>
<text x="0" y="216" font-size="10" fill="currentColor" fill-opacity="0.75">实心点 = 前沿（4 个），浅色点 = 被支配（32 个）。前沿的形状说明：从 9.7 MB 挪到 6 MB，</text>
<text x="0" y="230" font-size="10" fill="currentColor" fill-opacity="0.55">全扫只慢 0.14 ms——用 37% 的体积换 4% 的耗时，多数场景不该做这笔交换。</text>
</svg>
</figure>

前沿只有 4 个点，其余 32 个配置**在任何权重下都不该选**。但注意前沿的形状：`dict-snappy-rg32k`（9.7 MB / 3.26 ms）和 `plain-zstd-rg32k`（6.0 MB / 3.40 ms）之间，**37% 的体积只换 4% 的耗时**。这不是"压缩更好"或"不压缩更好"，而是你的磁盘和内存预算替你选。

!!! tip "帕累托前沿的用法"
    不要问"哪个配置最快"，问"我的体积预算内哪个最快"。前沿图把这个问题变成读图：沿 x 轴找到预算位置，取该点左侧最靠近前沿的配置。`reports/w09-bench.json` 里有全部 36 个点，`py/w09_bench.py` 的 `pareto()` 可以重算。

## 3. 噪声拆穿的第一个错觉："三目标最优配置不同"

计划这章时，我的假设是：点查、全扫、聚合的最优配置互不相同，因为它们优化的机制不同（剪枝粒度 / 解码速度 / 分组局部性）。实测教育了我。

同一天、同一台机器、同一份数据，B4 跑了两遍：

| 运行 | 点查最优 | 全扫最优 | 聚合最优 |
|---|---|---|---|
| 第一次 | 到达 plain-snappy-rg32k | 到达 plain-snappy-rg32k | 到达 plain-snappy-rg32k |
| 第二次 | 排序 dict-none-rg32k | 到达 dict-snappy-rg32k | 排序 dict-none-rg131k |

两轮互相矛盾。原因不在代码，在量级：**Q1 的全部差异区间只有 2.1 ms**（2.25–4.38），而一次 DuckDB 冷启动 20.6 ms、一次页缓存未命中就能抖动 1 ms。8 MB 数据集上，所有配置都活在固定开销的噪声地板之下，"谁最快"由调度器心情决定。

这不是失败，是测量结果本身：

!!! warning "结论的置信度是有量级的"
    - 差异 < 固定开销（本机约 2–3 ms / 查询）：**不可信**，两次运行就会翻面；
    - 差异 ≈ 数倍于固定开销：可信，但只在这个数据规模上可信；
    - 差异来自结构性原因（文件数、数据量、编码方式）：可迁移。

    第 3 章"排序改善编码 54%"属于第三类——因为字典页的有无是结构性的。本章矩阵里 rg=8,192 比 rg=32,768 的 Q1 快 0.3 ms 属于第一类——它是噪声。

**正确的做法不是否认小数据上的测量，而是升级实验规模**，让效应大于噪声。

## 4. 规模放大：24,000,000 行上才出现的取舍

同样的四个压缩档，数据放大 41 倍（870 MB 内存 / 1,008 MB 未压缩，按 `(symbol, ts)` 排序，rg=131,072）：

| 压缩 | 体积 | 写耗时 | Q1 点查 | Q3 全扫 |
|---|---|---|---|---|
| none | 1,007.8 MB | 774 ms | 6.41 ms | 84.37 ms |
| snappy | 539.3 MB | 1,559 ms | 5.84 ms | 66.66 ms |
| zstd-3 | 359.4 MB | 2,601 ms | 5.75 ms | 60.90 ms |
| zstd-9 | 352.3 MB | 20,021 ms | 5.92 ms | 60.74 ms |

<figure class="diagram">
<svg width="100%" viewBox="0 0 512 218" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
<title>四个压缩档在三个指标上的真实代价（24M 行）</title>
<desc>三组横向条形：体积以 none 为基准，写耗时以 none 为基准，全扫耗时以 none 为基准。zstd-9 写入是 none 的 26 倍，但全扫只快 28%。</desc>
<text x="0" y="14" font-size="12" font-weight="500" fill="currentColor">体积（none = 100%）</text>
<g font-size="9.5" fill="currentColor">
<rect x="70" y="22" width="380" height="12" stroke="currentColor" stroke-opacity="0.4" fill="none"/>
<rect x="70" y="38" width="203" height="12" stroke="currentColor" stroke-opacity="0.4" fill="none"/>
<rect x="70" y="54" width="136" height="12" stroke="currentColor" stroke-opacity="0.4" fill="none"/>
<rect x="70" y="70" width="133" height="12" stroke="currentColor" stroke-opacity="0.4" fill="none"/>
<text x="458" y="32" fill-opacity="0.8">1,008 MB</text><text x="281" y="48" fill-opacity="0.8">539 MB</text>
<text x="214" y="64" fill-opacity="0.8">359 MB</text><text x="211" y="80" fill-opacity="0.8">352 MB</text>
</g>
<text x="0" y="104" font-size="12" font-weight="500" fill="currentColor">写耗时（none = 1×）</text>
<g font-size="9.5" fill="currentColor">
<rect x="70" y="112" width="14" height="12" stroke="currentColor" stroke-opacity="0.4" fill="none"/>
<rect x="70" y="128" width="28" height="12" stroke="currentColor" stroke-opacity="0.4" fill="none"/>
<rect x="70" y="144" width="48" height="12" stroke="currentColor" stroke-opacity="0.4" fill="none"/>
<rect x="70" y="160" width="364" height="12" stroke="currentColor" stroke-opacity="0.4" fill="none"/>
<text x="92" y="122" fill-opacity="0.8">0.77 s</text><text x="106" y="138" fill-opacity="0.8">1.56 s</text>
<text x="126" y="154" fill-opacity="0.8">2.60 s</text><text x="442" y="170" fill-opacity="0.8">20.0 s（26×）</text>
</g>
<text x="0" y="194" font-size="10" fill="currentColor" fill-opacity="0.75">点查四个档全在 5.8–6.4 ms（只读 1 个行组，压缩只剩解码差异）；全扫 84 → 61 ms。</text>
<text x="0" y="210" font-size="10" fill="currentColor" fill-opacity="0.55">zstd-3 是甜点：体积 −64%、全扫 −28%、写只慢 3.4×。zstd-9 多花 17 s 只再省 7 MB。</text>
</svg>
</figure>

这一张图把三件事说清楚了：

1. **点查对压缩无感**（5.8–6.4 ms）：谓词剪枝后只读 1 个行组，字节太少，压缩比无关紧要。
2. **全扫真实受益于压缩**（84 → 61 ms）：即使在 NVMe 上，少读 2/3 的字节仍然赢——解码 359 MB 比读取 1,008 MB 便宜。
3. **写端与读端的代价差 26 倍**：zstd-9 多花 19 s 只再省 7 MB（−2%）。归档场景另说，任何要重算的层都别用 zstd-9。

!!! warning "本节结论的适用边界"
    本机 NVMe 顺序读约 3 GB/s。把介质换慢一个量级（机械盘 ~150 MB/s、对象存储 ~50 MB/s 后更受限于请求次数），“读字节”的权重会再放大，压缩的收益更大——结论方向不变、幅度更大。反方向（更快介质、更大内存）则一切都被缓存吞掉，回到噪声地板。

## 5. 尾延迟：p50 骗人，p99 才是 SLA

最优配置上把 Q1 跑 100 次：

| 模式 | p50 | p95 | p99 | max | 变异系数 |
|---|---|---|---|---|---|
| 同连接（热） | 2.66 ms | 3.12 ms | 3.55 ms | 3.62 ms | 0.10 |
| 新连接（冷 DuckDB） | 20.63 ms | 23.77 ms | 24.74 ms | 24.90 ms | 0.07 |

<figure class="diagram">
<svg width="100%" viewBox="0 0 562 200" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
<title>同一点查的两种延迟分布（各 100 次）</title>
<desc>两组柱状：热连接 p50 2.66 p95 3.12 p99 3.55；新连接 p50 20.63 p95 23.77 p99 24.74。冷连接整体抬高约 8 倍，两组内部 p50 到 p99 都只涨三成。</desc>
<text x="0" y="14" font-size="12" font-weight="500" fill="currentColor">同连接（热）：p50 2.66 → p99 3.55 ms</text>
<g font-size="9.5" fill="currentColor">
<rect x="70" y="22" width="152" height="13" stroke="currentColor" stroke-opacity="0.4" fill="none"/>
<rect x="70" y="39" width="178" height="13" stroke="currentColor" stroke-opacity="0.4" fill="none"/>
<rect x="70" y="56" width="203" height="13" stroke="currentColor" stroke-opacity="0.4" fill="none"/>
<text x="228" y="32" fill-opacity="0.8">2.66</text><text x="254" y="49" fill-opacity="0.8">3.12</text><text x="279" y="66" fill-opacity="0.8">3.55</text>
<text x="10" y="32" fill-opacity="0.7">p50</text><text x="10" y="49" fill-opacity="0.7">p95</text><text x="10" y="66" fill-opacity="0.7">p99</text>
</g>
<text x="0" y="96" font-size="12" font-weight="500" fill="currentColor">新连接（冷 DuckDB）：p50 20.63 → p99 24.74 ms</text>
<g font-size="9.5" fill="currentColor">
<rect x="70" y="104" width="380" height="13" stroke="currentColor" stroke-opacity="0.4" fill="none"/>
<rect x="70" y="121" width="437" height="13" stroke="currentColor" stroke-opacity="0.4" fill="none"/>
<rect x="70" y="138" width="455" height="13" stroke="currentColor" stroke-opacity="0.4" fill="none"/>
<text x="456" y="114" fill-opacity="0.8">20.63</text><text x="513" y="131" fill-opacity="0.8">23.77</text><text x="531" y="148" fill-opacity="0.8">24.74</text>
<text x="10" y="114" fill-opacity="0.7">p50</text><text x="10" y="131" fill-opacity="0.7">p95</text><text x="10" y="148" fill-opacity="0.7">p99</text>
</g>
<text x="0" y="176" font-size="10" fill="currentColor" fill-opacity="0.75">两个分布的形状相似（p99/p50 ≈ 1.2–1.3），差的是整体位置——连接与 footer 解析的固定成本。</text>
<text x="0" y="192" font-size="10" fill="currentColor" fill-opacity="0.55">SLA 含义：连接池保活价值 18 ms；"2.66 ms" 的 p50 承诺，会让 1% 的请求（3.55 ms+）被判违约。</text>
</svg>
</figure>

两组分布形状相似（p99 ≈ 1.3 × p50），差的是**位置**：新连接整体 +18 ms。这直接给出两个工程决定：

- **连接池必须保活**。一次冷启动 = 7.8 倍延迟，这比任何 Parquet 调参都大。
- **变异系数 0.07–0.10** 说明系统本身稳定，尖刺不是病，是结构（初始化、页缓存）。真要压 p99，优化的对象是"消除冷路径"，不是"给热路径提速"。

!!! note "本章的"冷"只是新连接"
    Windows 上没有 `drop_caches`，OS 页缓存无法清除。真正的冷 IO（重启后首次读）会更慢、方差更大。对象存储上还要加每次请求的 RTT（几十 ms），那时"打开 40 个文件"（第 8 章）的结论会被再放大一个量级。

## 6. 结论固化：速查表只收"如果…那么…"

基准的终点不是表格，是决策规则。[调参速查表](appendix/cheatsheet.md)新增的 W9 节每条都带复现命令。核心五条：

| 如果你的场景是… | 那么… | 依据 |
|---|---|---|
| 数据 < 100 MB，本机 NVMe | 别调参，先升级数据规模再测 | §3：差异在噪声地板之下 |
| 批处理落盘，读多写少 | zstd-3，别用 zstd-9 | §4：26× 写耗时换 2% 体积 |
| 交互式点查服务 | 连接池保活 + 单分区 < 10 文件 | §5：冷启动 7.8× ＋ 第 8 章绑定开销 |
| 磁盘预算紧、扫描为主 | plain + zstd-3（前沿点 6.0 MB / 3.40 ms） | §2 帕累托前沿 |
| 结论要写进文档 | 附复现命令 + 数据指纹 | §1：无指纹的数字不可信 |

## 7. 踩坑

| 坑 | 表现 | 正确做法 |
|---|---|---|
| 单次运行下结论 | B4 两轮翻面：三目标最优从"同一个"变"三个不同" | 中位数(7)起步，关键结论隔天复跑 |
| 拿 sum(px) 当指纹 | 行组大小改变浮点求和顺序，指纹永远对不上 | 指纹用整数聚合 + min/max |
| 小数据上调参 | Q1 差异 2.1 ms < 冷启动 20.6 ms | 先把数据放大到差异 > 5× 固定开销 |
| 用 zstd-9 当默认 | 写入 26× 慢，体积只再省 2% | 在线层 zstd-3，zstd-9 只进归档 |
| 用 p50 定 SLA | 1% 请求超时被当成"偶发" | 按 p99 定，先消除冷路径 |
| 热缓存比较压缩算法 | 页缓存全热，IO 差异被抹掉 | 声明缓存状态；关键数字冷热各测一轮 |
| 迷信"最快配置" | 前沿上多个点各有取舍 | 按体积/写耗时预算在前沿上选点 |

## 8. 出处

- DuckDB 文档 — *Performance*（`SET threads`、`memory_limit` 与查询间缓存行为）
- pyarrow 文档 — `pq.write_table`（`compression_level`、`row_group_size`）与 `ParquetWriter` 语义
- 本章方法论参考 — Brendon Gregg, *Systems Performance*（延迟分位数与变异系数的使用）；无直接规范条目，属工程实践
- 本章数据：`reports/w09-bench.md`（脚本 `py/w09_bench.py`，2026-11-11，pyarrow 26.0.1 / duckdb 2.0.0；数据指纹 `b1` 节）

[^1]: 两次 B4 运行均为本机 2026-11-11 实测，间隔约 30 分钟，中间无系统配置变化。差异来源未定位到具体进程，但量级（±1 ms）与 Q1 分布的变异系数一致。
[^2]: 24M 行合成数据（种子 42）：100 合约 × 4 小时，px 为随机游走。写侧为单线程 pyarrow；DuckDB 读侧 4 线程。zstd-9 的 20 s 写耗时应理解为"单线程、默认窗口"下的结果，多线程写入器可摊薄但不改变与 zstd-3 的相对差距。
[^3]: 变异系数 = 标准差 / 均值，100 样本。0.07–0.10 属于稳定范围；> 0.3 时应先查环境（后台任务、杀毒扫描、Windows Defender 实时扫描是常见元凶）再谈优化。

---

**下一步**：第 10 章把前九章拧成一条端到端管线——Rust 写入、Python 查询、C++ 回放，交付一个能跑的 mini 行情数据湖。
