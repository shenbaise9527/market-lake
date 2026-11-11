# 第 1 章 · 列存为什么快

!!! abstract "本章结论（读完你应该能复述这三句）"
    1. 列存快的主因是**读的字节少了**：一次查询只需要碰它真正用到的那几列，其余列的字节一个都不读。
    2. 压缩不是原因，是**副产品**：同一列类型相同、分布相近，编码和压缩才有效；压缩只是让"少读"这件事更省钱。
    3. Parquet 把"少读"落到了两个机制上：**列裁剪**（只读需要的 column chunk）和**元数据剪枝**（用行组统计信息跳过整块数据）。

对应任务卡：仓库内 `docs/week01.md` ｜ 本章实验脚本：`py/w01_compare.py`

---

## 1. 直觉：一次 `avg(px)` 到底碰到了哪些字节

先不谈编码、不谈格式，只问一个问题：执行 `SELECT avg(px) FROM trades`，磁盘上哪些字节被读进来了？

<figure class="diagram">
<svg width="100%" viewBox="0 0 680 238" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
<title>行式与列式布局下读取 px 列的字节范围</title>
<desc>行式存储中 px 分散在每一行里，读一列必须扫过全部行；列式存储中同一列连续存放，只需读一块连续区域。</desc>
<text x="0" y="16" font-size="14.3" font-weight="500" fill="currentColor">行式存储（CSV）</text>
<text x="360" y="16" font-size="14.3" font-weight="500" fill="currentColor">列式存储（Parquet）</text>
<g stroke="currentColor" stroke-opacity="0.35" stroke-width="1">
  <rect x="0" y="30" width="66" height="22" fill="none"/><rect x="66" y="30" width="66" height="22" fill="none"/><rect x="132" y="30" width="66" height="22" fill="#EF9F27" fill-opacity="0.6"/><rect x="198" y="30" width="66" height="22" fill="none"/>
  <rect x="0" y="52" width="66" height="22" fill="none"/><rect x="66" y="52" width="66" height="22" fill="none"/><rect x="132" y="52" width="66" height="22" fill="#EF9F27" fill-opacity="0.6"/><rect x="198" y="52" width="66" height="22" fill="none"/>
  <rect x="0" y="74" width="66" height="22" fill="none"/><rect x="66" y="74" width="66" height="22" fill="none"/><rect x="132" y="74" width="66" height="22" fill="#EF9F27" fill-opacity="0.6"/><rect x="198" y="74" width="66" height="22" fill="none"/>
  <rect x="0" y="96" width="66" height="22" fill="none"/><rect x="66" y="96" width="66" height="22" fill="none"/><rect x="132" y="96" width="66" height="22" fill="#EF9F27" fill-opacity="0.6"/><rect x="198" y="96" width="66" height="22" fill="none"/>
  <rect x="0" y="118" width="66" height="22" fill="none"/><rect x="66" y="118" width="66" height="22" fill="none"/><rect x="132" y="118" width="66" height="22" fill="#EF9F27" fill-opacity="0.6"/><rect x="198" y="118" width="66" height="22" fill="none"/>
</g>
<g font-size="11" fill="currentColor" fill-opacity="0.75">
  <text x="33" y="44" text-anchor="middle">symbol</text><text x="99" y="44" text-anchor="middle">ts</text><text x="165" y="44" text-anchor="middle">px</text><text x="231" y="44" text-anchor="middle">vol</text>
  <text x="33" y="66" text-anchor="middle">600267</text><text x="99" y="66" text-anchor="middle">1793…</text><text x="165" y="66" text-anchor="middle">32.41</text><text x="231" y="66" text-anchor="middle">300</text>
  <text x="165" y="88" text-anchor="middle">32.42</text><text x="165" y="110" text-anchor="middle">32.40</text><text x="165" y="132" text-anchor="middle">32.45</text>
</g>
<text x="0" y="158" font-size="12.1" fill="currentColor" fill-opacity="0.85">要读的 px 分散在每行中间，读下一个 px 都要跨过</text>
<text x="0" y="174" font-size="12.1" fill="currentColor" fill-opacity="0.85">symbol / ts / vol —— 实际读了 4 列，只用上 1 列</text>
<g stroke="currentColor" stroke-opacity="0.35" stroke-width="1">
  <rect x="360" y="30" width="66" height="110" fill="none"/>
  <rect x="434" y="30" width="66" height="110" fill="none"/>
  <rect x="508" y="30" width="66" height="110" fill="#EF9F27" fill-opacity="0.6"/>
  <rect x="582" y="30" width="66" height="110" fill="none"/>
</g>
<g stroke="currentColor" stroke-opacity="0.18" stroke-width="1">
  <line x1="360" y1="52" x2="426" y2="52"/><line x1="360" y1="74" x2="426" y2="74"/><line x1="360" y1="96" x2="426" y2="96"/><line x1="360" y1="118" x2="426" y2="118"/>
  <line x1="434" y1="52" x2="500" y2="52"/><line x1="434" y1="74" x2="500" y2="74"/><line x1="434" y1="96" x2="500" y2="96"/><line x1="434" y1="118" x2="500" y2="118"/>
  <line x1="508" y1="52" x2="574" y2="52"/><line x1="508" y1="74" x2="574" y2="74"/><line x1="508" y1="96" x2="574" y2="96"/><line x1="508" y1="118" x2="574" y2="118"/>
  <line x1="582" y1="52" x2="648" y2="52"/><line x1="582" y1="74" x2="648" y2="74"/><line x1="582" y1="96" x2="648" y2="96"/><line x1="582" y1="118" x2="648" y2="118"/>
</g>
<g font-size="11" fill="currentColor" fill-opacity="0.75">
  <text x="393" y="46" text-anchor="middle">symbol</text><text x="467" y="46" text-anchor="middle">ts</text><text x="541" y="46" text-anchor="middle">px</text><text x="615" y="46" text-anchor="middle">vol</text>
  <text x="541" y="68" text-anchor="middle">32.41</text><text x="541" y="90" text-anchor="middle">32.42</text><text x="541" y="112" text-anchor="middle">32.40</text><text x="541" y="134" text-anchor="middle">32.45</text>
</g>
<text x="360" y="158" font-size="12.1" fill="currentColor" fill-opacity="0.85">px 全部连续存放，读它就是读一段连续内存</text>
<text x="360" y="174" font-size="12.1" fill="currentColor" fill-opacity="0.85">→ 只读 1 列，且是一次顺序扫描</text>
<line x1="0" y1="192" x2="680" y2="192" stroke="currentColor" stroke-opacity="0.15"/>
<text x="0" y="212" font-size="12.1" fill="currentColor" fill-opacity="0.85">这条差别在 IO 上放大成 46 倍（见 §4 实测），在 CPU 上放大成 cache 命中率的差别。</text>
<text x="0" y="230" font-size="12.1" fill="currentColor" fill-opacity="0.85">注意：列存省的是"没用的列"，不是"没用的行"——行级过滤要靠 §2.4 的统计信息。</text>
</svg>
</figure>

这里有个容易被含糊过去的点：**列存省下的是"列的字节"，不是"行的字节"**。如果查询是 `WHERE symbol = 'X'`，列存照样要把 `symbol` 这一列整列读完（除非有统计信息能整块跳过）。列存和行存解决的是不同的问题，搞混这点，后面调优会一路错。

## 2. 机制拆解：Parquet 怎么把"少读"变成默认行为

### 2.1 文件是一层层套起来的

Parquet 文件内部是四层结构，每一层都为"跳过不必要的数据"服务：

<figure class="diagram">
<svg width="100%" viewBox="0 0 552 228" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
<title>Parquet 文件四层结构与 footer 剪枝</title>
<desc>文件由行组构成，行组内按列分成 column chunk，每个 chunk 由 page 组成；文件尾部的 footer 保存每列统计信息，用于跳过不匹配的行组。</desc>
<text x="0" y="14" font-size="13.2" font-weight="500" fill="currentColor">一个 Parquet 文件（本例：1 个行组 · 73,375 行 · 1.05 MB）</text>
<rect x="0" y="26" width="400" height="62" rx="4" fill="none" stroke="currentColor" stroke-opacity="0.4"/>
<text x="10" y="42" font-size="12.1" fill="currentColor" fill-opacity="0.9">Row Group 0（73,375 行）</text>
<rect x="10" y="50" width="70" height="30" rx="3" fill="none" stroke="currentColor" stroke-opacity="0.3"/>
<text x="45" y="63" font-size="9.9" text-anchor="middle" fill="currentColor" fill-opacity="0.8">symbol</text>
<text x="45" y="75" font-size="9.9" text-anchor="middle" fill="currentColor" fill-opacity="0.6">105 B</text>
<rect x="88" y="50" width="130" height="30" rx="3" fill="#EF9F27" fill-opacity="0.3" stroke="#EF9F27" stroke-opacity="0.8"/>
<text x="153" y="63" font-size="9.9" text-anchor="middle" fill="currentColor" fill-opacity="0.9">ts</text>
<text x="153" y="75" font-size="9.9" text-anchor="middle" fill="currentColor" fill-opacity="0.7">640 KB（61%）</text>
<rect x="226" y="50" width="86" height="30" rx="3" fill="#378ADD" fill-opacity="0.3" stroke="#378ADD" stroke-opacity="0.8"/>
<text x="269" y="63" font-size="9.9" text-anchor="middle" fill="currentColor" fill-opacity="0.9">px</text>
<text x="269" y="75" font-size="9.9" text-anchor="middle" fill="currentColor" fill-opacity="0.7">104 KB</text>
<rect x="320" y="50" width="70" height="30" rx="3" fill="none" stroke="currentColor" stroke-opacity="0.3"/>
<text x="355" y="63" font-size="9.9" text-anchor="middle" fill="currentColor" fill-opacity="0.8">vol…</text>
<text x="355" y="75" font-size="9.9" text-anchor="middle" fill="currentColor" fill-opacity="0.6">43 KB</text>
<rect x="0" y="98" width="400" height="44" rx="4" fill="none" stroke="currentColor" stroke-opacity="0.25" stroke-dasharray="4 3"/>
<text x="10" y="118" font-size="12.1" fill="currentColor" fill-opacity="0.7">Row Group 1（真实文件里按 max_row_group_size 切分，本例只有 1 组）</text>
<text x="10" y="133" font-size="11" fill="currentColor" fill-opacity="0.5">行组是"能否跳过"的最小单位：一个行组要么整块读，要么整块不读</text>
<rect x="0" y="152" width="400" height="48" rx="4" fill="#1D9E75" fill-opacity="0.16" stroke="#1D9E75" stroke-opacity="0.7"/>
<text x="10" y="170" font-size="12.1" font-weight="500" fill="currentColor">Footer（文件末尾）</text>
<text x="10" y="186" font-size="11" fill="currentColor" fill-opacity="0.8">每列 min/max、null_count、编码列表、chunk 字节偏移、schema</text>
<text x="0" y="218" font-size="11" fill="currentColor" fill-opacity="0.55">PAR1（4 字节 magic，文件头尾各一个）</text>
<text x="424" y="42" font-size="12.1" font-weight="500" fill="currentColor">读路径</text>
<text x="424" y="62" font-size="11" fill="currentColor" fill-opacity="0.85">① 读 footer（几 KB）</text>
<text x="424" y="78" font-size="11" fill="currentColor" fill-opacity="0.85">② 用它算：哪个行组可能</text>
<text x="434" y="92" font-size="11" fill="currentColor" fill-opacity="0.85">命中、只需哪几列</text>
<text x="424" y="112" font-size="11" fill="currentColor" fill-opacity="0.85">③ 跳到对应 chunk 读</text>
<text x="424" y="134" font-size="11" fill="currentColor" fill-opacity="0.6">→ 这就是"列裁剪"</text>
<text x="424" y="150" font-size="11" fill="currentColor" fill-opacity="0.6">   和"元数据剪枝"</text>
<text x="424" y="172" font-size="11" fill="currentColor" fill-opacity="0.85">数据写在 footer 之前，</text>
<text x="424" y="186" font-size="11" fill="currentColor" fill-opacity="0.85">是为了支持单遍写入：</text>
<text x="424" y="200" font-size="11" fill="currentColor" fill-opacity="0.85">边收边写，最后补一张</text>
<text x="424" y="214" font-size="11" fill="currentColor" fill-opacity="0.85">"目录"在文件尾部。</text>
</svg>
</figure>

四层的职责可以这样记：

| 层 | 是什么 | 为谁服务 |
|---|---|---|
| 文件 | 一个自包含单元，头尾 `PAR1` 之间是数据 + 末尾 footer | 分发与存储（对象存储上一个对象） |
| 行组 row group | 若干行的集合（本例 73,375 行） | **能否跳过的最小粒度**：统计信息只到这一级 |
| 列块 column chunk | 某个行组里某一列的全部数据，连续存放 | **列裁剪的最小粒度**：读 px 就只碰 px 的 chunk |
| 页 page | chunk 内部再切的小块（默认约 1 MB 量级） | 解码与内存上限：一次只解一页，不必整列进内存 |

上面这张表的结构定义来自 Parquet 官方文档的 *File Format* 一节[^1]——注意其中的关键设计：
元数据写在数据之后，读取方先读 footer 定位列块，再顺序读取被选中的 chunk。

关于"为什么要向量化"，一句话概括：逐行执行时每个元组都要过一次解释开销，且数据在 cache 里来回失效；
列式 + 批量处理把这两件事都改善了[^2]。这也是第 4 章 Arrow 内存布局要解决的问题。

### 2.2 为什么要切成行组，而不是一列一个大数组

这是新手最容易觉得"多此一举"的地方：既然列存好，为什么不把整列存成一个大数组？

三个原因，都是硬的：

- **随机访问**：一列 10 亿行的大数组，要查第 9 亿行的某个值，得先定位到 9 亿 × 8 字节的偏移。切成行组后，footer 直接给出每组的字节偏移，定位是 O(1)。
- **内存上限**：不解压整列就没法算，而整列可能几十 GB。行组让"一次处理一小块"成为可能，这也是流式扫描和并行扫描的基础（每个线程领一个行组）。
- **剪枝粒度**：行组是统计信息的作用单位。行组太大 → 每组都命中，剪枝失效；行组太小 → 元数据膨胀、随机 IO 变多，而行情数据是顺序扫描为主，小行组会让顺序读退化成大量小读。

代价是：**一个行组内的行是绑定的**。这就是第 3 章要做的实验——按 `(symbol, ts)` 排序后重切行组，让"同一合约的数据聚在少数几个组里"，点查才能只碰 1–2 个组。

### 2.3 压缩是副产品，不是原因

同一列的数据类型相同、语义相近（价格都在 30 附近、成交量都是 100 的整数倍、时间戳单调递增），所以：

- **字典编码**对低基数列极有效：`symbol` 整列 73,375 个值只占 **105 字节**（字典里一条 + 全部指向它的索引）
- **差分编码**对单调递增的时间戳、序号极有效
- **RLE** 对连续重复值极有效

然后才是通用压缩（zstd / snappy）在这堆已经很规整的字节上再压一层。所以顺序是：**列存 → 同质化 → 编码有效 → 压缩有效**。反过来不成立：你把 CSV 用 zstd 压，压缩率远不如 Parquet，因为行式数据里类型是混着的。

## 3. 动手：三组命令，看懂全部结论

```bash
# ① CSV vs Parquet 三种查询目标（全扫 / 点查 / 窄列）
uv run python py/w01_compare.py

# ② 解剖单个文件的物理结构：行组、列块、编码、列级压缩后大小
cd rust && cargo run -q -- inspect "../lake/data/raw/trades/dt=2026-11-05/exchange=SH/part-600267.SH.parquet"

# ③ 看引擎到底读了多少字节（注意 EXPLAIN ANALYZE 里的 Data Read）
uv run python -c "
import duckdb
con = duckdb.connect()
S = \"(SELECT * FROM read_parquet('lake/data/raw/trades/**/*.parquet', hive_partitioning=true) WHERE dt='2026-11-05')\"
for sql in ['SELECT sum(vol), avg(px) FROM ' + S, 'SELECT avg(px) FROM ' + S]:
    plan = con.execute('EXPLAIN ANALYZE ' + sql).fetchall()[0][1]
    print([l.strip() for l in plan.splitlines() if 'Data Read' in l])
"
```

看代码的时候注意 `w01_compare.py` 里的一件事：每个查询**跑 3 次取最小值**。这不是洁癖——第一次查询要读页、分配到内存池，冷热缓存差出好几倍，取最小值才是"这个查询要多久"的稳定估计。

## 4. 实测数字（本机，2026-11）

环境：pyarrow 26.0.1 / DuckDB 2.0.0 / 4 线程 / 单日 2026-11-05，293,500 行成交。
下表全部为本机实测，可用 §3 的命令原样复现[^4]。

| 查询目标 | Parquet | CSV | 倍数 | 说明 |
|---|---|---|---|---|
| 文件体积 | 4.11 MB | 19.33 MB | 4.70x | 压缩只是这部分收益 |
| a 全扫聚合 `sum(vol), avg(px)` | 5.9 ms | 54.4 ms | 9.2x | 只读 px + vol 两列 |
| b 点查 `symbol + 10 分钟窗口` | 6.0 ms | 56.5 ms | 9.5x | 命中 1–2 个行组 |
| c 窄列扫描 `avg(px)` | 4.4 ms | 52.6 ms | 11.9x | 只读 px 一列 |
| c 的 `Data Read` | **423 kB** | **19.3 MB** | **46x** | 这才是"快 11.9 倍"的原因 |

单文件列级构成（`part-600267.SH.parquet`，7 列，压缩后 1,044,781 字节，原始 1,694,638 字节，比值 1.62x）：

| 列 | 压缩后 | 占文件 | 备注 |
|---|---|---|---|
| ts | 640,302 B | 61% | 反面教材，见 §5 |
| seq | 226,893 B | 22% | 0–74,999 递增，适合差分编码 |
| px | 104,463 B | 10% | 两位小数浮点 |
| vol | 42,790 B | 4% | 100 的整数倍 |
| channel | 18,602 B | 2% | 4 个取值 |
| side | 11,626 B | 1% | 3 个取值 |
| symbol | 105 B | 0.01% | 字典一行 |

把 423 kB 和 104,463 × 4 个文件 ≈ 418 kB 对一下：**基本相等**。说明读 `avg(px)` 时引擎确实只碰了 4 个 px 列块，一页元数据之外什么都没读。这种"用两个独立来源的数字互相验证"的习惯，比记住任何结论都值钱。

## 5. 踩坑

!!! warning "坑 1：默认编码在高基数列上会反噬"
    pyarrow 给**所有**列默认开 `RLE_DICTIONARY`（本例 `enc=[PLAIN,RLE,RLE_DICT]`）。对 `symbol` 是完美选择（105 字节），
    对 `ts` 是灾难：73,375 个几乎不重复的 int64 全部进字典，字典本身 586 KB，比原始数据还大。
    改法在第 2 章：显式指定 `DELTA_BINARY_PACKED`，同一列通常能降一个数量级。
    **教训：默认值不是推荐值，是按"最不容易出错"选的。**

!!! warning "坑 2：`Data Read` 不能当磁盘 IO 读"
    DuckDB 报的 `Data Read` 是扫描算子实际读入的字节数（压缩态），不等于磁盘 IO——文件可能已在 OS page cache 里。
    所以它适合比较"两个方案读的字节量与比例"，不适合当"磁盘压力"指标。想测真实 IO，得先清缓存或换冷文件。

!!! warning "坑 3：CSV 不是永远输"
    一次性导出、给人看、进 Excel、字段数会变、需要人眼 diff——这些场景 CSV 更合适。Parquet 的优势只在
    "反复扫、只要几列、量大"时兑现。判断标准不是格式先进，而是**访问模式**。这个判断与 DuckDB 官方博客的对照实测结论一致[^3]。

??? note "一个容易误读的细节：为什么"全扫聚合"的 Data Read 只有 600 kB？"
    那天的 4 个文件合计 4.11 MB，而 `sum(vol), avg(px)` 只读 px（10%）+ vol（4%）≈ 14%，
    600 kB 正对应这个比例。**"全表扫描"在列存里指的是"扫全部行"，不是"读全部字节"**。
    这也是为什么列存下 `SELECT *` 会比想象中贵得多——它才是真的读全部字节。

## 6. 自检：三个问题

写到 `notes/week01.md`，每个不超过 5 行：

1. 列存快，主要快在哪一步？（提示：不是"压缩"）
2. 为什么 `ts` 列用 `RLE_DICTIONARY` 反而最占空间？
3. 这个文件 4.70x 的体积优势，主要来自哪几列？（用 §4 的列级数字回答）

答不上来的话，回到 §1 的图看一遍——**这三题全部只需要那一张图加一张表**。

---

## 出处

[^1]: Parquet 文件布局与 footer 设计 —— Apache Parquet 官方文档 *File Format* 的 Concepts 一节，<https://parquet.apache.org/docs/file-format/>（访问 2026-11-11）。原文明确：文件由 N 列 × M 行组构成，元数据写在数据之后以支持单遍写入，读取方应先读元数据定位列块。
[^2]: 列存与向量化执行的性能来源 —— Boncz, Zukowski, Nes, *MonetDB/X100: A Hyper-Pipelined Query Execution Engine*, CIDR 2005, §1–§3，<https://www.cidrdb.org/cidr2005/papers/P19.pdf>（访问 2026-11-11）。本文关于"逐行执行的开销来自每 tuple 的解释开销与 cache 未命中"的表述依据该文 §2。
[^3]: CSV 与 Parquet 的对照实验与场景判断 —— DuckDB 官方博客 *CSV Files: Dethroning Parquet as the Ultimate Storage File Format — or Not?*（2024-12-05），<https://duckdb.org/2024/12/05/csv-files-dethroning-parquet-or-not.html>（访问 2026-11-11）。
[^4]: 本章所有数字均为本机实测，脚本 `py/w01_compare.py`、`py/gen_tick.py`、`rust/src/inspect.rs`，环境 pyarrow 26.0.1 / polars 2.1.0 / duckdb 2.0.0 / 4 线程，数据为 2026-11-04、2026-11-05 两天合成 tick（含刻意注入的乱序与重复）。
