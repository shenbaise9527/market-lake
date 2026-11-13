# 第 2 章 · Parquet 物理结构

!!! abstract "本章结论（读完你应该能复述这三句）"
    1. 一个 Parquet 文件 = **数据区 + 尾部 footer**。footer 写在最后不是设计缺陷，是为了支持"边收边写"的单遍写入：数据一路写下去，最后补一张目录。
    2. 可跳过的**最小单位是行组，不是行**。所以点查能省多少，取决于"排序键是否与查询条件对齐"——同一份数据，排序本身值 40% 的体积和两个数量级的扫描量。
    3. 编码要按列的分布选，不是越"高级"越好：本仓库实测 `ts/seq` 换 `DELTA_BINARY_PACKED` 让文件从 996.7 KB 降到 454.2 KB，但 `px` 换 `BYTE_STREAM_SPLIT` 反而涨到 3.4 倍。

对应任务卡：仓库内 `docs/week02.md` ｜ 本章实验脚本：`py/w02_encoding.py` ｜ 原始结果：`reports/w02-encoding.md`

---

## 1. 直觉：拿真实文件的字节偏移做一次解剖

不看文档，先看数字。下面这个文件是仓库里真实生成的单合约单日 tick 数据：

```text
文件: part-600267.SH.parquet
行数: 73,375    列数: 7    行组数: 1
文件大小: 1,046,325 B
写入器: parquet-cpp-arrow version 22.0.0
```

让 `mdlake inspect` 把每个列块的**字节位置**打出来：

```bash
cd rust && cargo run -q -- inspect "../lake/data/raw/trades/dt=2026-11-05/exchange=SH/part-600267.SH.parquet"
```

再用 pyarrow 读同一份元数据里的偏移字段（`file_offset` / `dictionary_page_offset` / `data_page_offset`），能拼出整份文件的字节地图：

<figure class="diagram">
<svg width="100%" viewBox="0 0 680 182" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
<title>一个真实 Parquet 文件的字节地图</title>
<desc>上半部分按真实字节比例画出七个列块与尾部 footer；下半部分用等宽块列出每个区域的准确字节数，其中 ts 列占 61%，footer 只有 1.5KB。</desc>
<text x="0" y="14" font-size="13.2" font-weight="500" fill="currentColor">字节地图（重写为 ts/seq 走 DELTA 的版本，465,129 B）</text>
<rect x="0" y="26" width="418" height="40" fill="#EF9F27" fill-opacity="0.55" stroke="currentColor" stroke-opacity="0.3"/>
<rect x="418" y="26" width="146" height="40" fill="#378ADD" fill-opacity="0.45" stroke="currentColor" stroke-opacity="0.3"/>
<rect x="564" y="26" width="63" height="40" fill="currentColor" fill-opacity="0.12" stroke="currentColor" stroke-opacity="0.3"/>
<rect x="627" y="26" width="27" height="40" fill="currentColor" fill-opacity="0.08" stroke="currentColor" stroke-opacity="0.3"/>
<rect x="654" y="26" width="23" height="40" fill="currentColor" fill-opacity="0.05" stroke="currentColor" stroke-opacity="0.3"/>
<rect x="677" y="26" width="3" height="40" fill="#1D9E75" fill-opacity="0.9"/>
<text x="209" y="51" font-size="12.1" text-anchor="middle" fill="currentColor">ts　285,776 B（61.4%）</text>
<text x="491" y="51" font-size="12.1" text-anchor="middle" fill="currentColor">px　99,726</text>
<text x="595" y="51" font-size="10" text-anchor="middle" fill="currentColor">vol 43,168</text>
<text x="0" y="82" font-size="11" fill="currentColor" fill-opacity="0.65">↑ 块宽 = 字节数比例。右侧 seq / symbol / footer 太窄，比例条里画不出来，看下面这行</text>
<g stroke="currentColor" stroke-opacity="0.3" stroke-width="1">
<rect x="7" y="96" width="66" height="42" fill="none"/>
<rect x="82" y="96" width="66" height="42" fill="#EF9F27" fill-opacity="0.3"/>
<rect x="157" y="96" width="66" height="42" fill="none"/>
<rect x="232" y="96" width="66" height="42" fill="#378ADD" fill-opacity="0.3"/>
<rect x="307" y="96" width="66" height="42" fill="none"/>
<rect x="382" y="96" width="66" height="42" fill="none"/>
<rect x="457" y="96" width="66" height="42" fill="none"/>
<rect x="532" y="96" width="66" height="42" fill="#1D9E75" fill-opacity="0.3"/>
<rect x="607" y="96" width="66" height="42" fill="none"/>
</g>
<g font-size="10" text-anchor="middle" fill="currentColor">
<text x="40" y="114">symbol</text><text x="40" y="129" fill-opacity="0.6">312 B</text>
<text x="115" y="114">ts</text><text x="115" y="129" fill-opacity="0.6">285,776 B</text>
<text x="190" y="114">seq</text><text x="190" y="129" fill-opacity="0.6">3,924 B</text>
<text x="265" y="114">px</text><text x="265" y="129" fill-opacity="0.6">99,726 B</text>
<text x="340" y="114">vol</text><text x="340" y="129" fill-opacity="0.6">43,168 B</text>
<text x="415" y="114">side</text><text x="415" y="129" fill-opacity="0.6">11,895 B</text>
<text x="490" y="114">channel</text><text x="490" y="129" fill-opacity="0.6">18,810 B</text>
<text x="565" y="114">footer</text><text x="565" y="129" fill-opacity="0.6">1,510 B</text>
<text x="640" y="114">合计</text><text x="640" y="129" fill-opacity="0.6">465,129 B</text>
</g>
<text x="0" y="158" font-size="12.1" fill="currentColor" fill-opacity="0.85">开头的 4 字节和结尾的 4 字节都是 magic "PAR1"；footer 紧挨在结尾 magic 之前。</text>
<text x="0" y="176" font-size="12.1" fill="currentColor" fill-opacity="0.85">footer 只有 1,510 B（0.3%），却决定了整份文件"该读哪一段"。</text>
</svg>
</figure>

这张图里最值得盯的不是 ts 占 61%，而是**footer 的位置和大小**：它在文件末尾，只有 1.5 KB，却记录了每一列的 min/max、编码、字节偏移。所有剪枝决策都从这里开始。

由此可以立刻回答任务卡里那道题：

> **一个 1 GB 文件、行组 128 MB，点查命中一个行组时，最少要读多少字节？**

答案是 **footer（几 KB 到几百 KB，取决于列数与行组数）+ 该行组里被选中列的所有 page**。行组内无法按行跳过——命中一个行组就得把它的相关列整块解出来。这就是"行组大小"必须认真选的物理原因，第三、八章会反复用到。

## 2. 四层结构：每一层都在为"跳过"服务

Parquet 的层级是 `file → row group → column chunk → page`。名字很像，职责完全不同：

<figure class="diagram">
<svg width="100%" viewBox="0 0 680 236" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
<title>Parquet 四层结构与各层职责</title>
<desc>文件由多个行组组成，行组内每一列是一个列块，列块内又切成多个页；剪枝按行组和页两级进行，列的读取以列块为单位。</desc>
<text x="0" y="14" font-size="13.2" font-weight="500" fill="currentColor">file</text>
<text x="212" y="14" font-size="13.2" font-weight="500" fill="currentColor">row group 0</text>
<text x="470" y="14" font-size="13.2" font-weight="500" fill="currentColor">ts 的 column chunk</text>
<rect x="0" y="24" width="150" height="150" fill="none" stroke="currentColor" stroke-opacity="0.4"/>
<rect x="8" y="32" width="134" height="38" fill="#EF9F27" fill-opacity="0.35" stroke="currentColor" stroke-opacity="0.25"/>
<rect x="8" y="78" width="134" height="38" fill="currentColor" fill-opacity="0.06" stroke="currentColor" stroke-opacity="0.25"/>
<rect x="8" y="124" width="134" height="38" fill="currentColor" fill-opacity="0.06" stroke="currentColor" stroke-opacity="0.25"/>
<text x="75" y="56" font-size="10.5" text-anchor="middle" fill="currentColor">row group 0</text>
<text x="75" y="102" font-size="10.5" text-anchor="middle" fill="currentColor">row group 1</text>
<text x="75" y="148" font-size="10.5" text-anchor="middle" fill="currentColor">row group 2</text>
<text x="75" y="186" font-size="10" text-anchor="middle" fill="currentColor" fill-opacity="0.6">footer 记录每个行组的列统计</text>
<line x1="155" y1="43" x2="205" y2="43" stroke="currentColor" stroke-opacity="0.4" stroke-dasharray="3 2"/>
<path d="M205 43 l-7 -3.5 l0 7 z" fill="currentColor" fill-opacity="0.5"/>
<g stroke="currentColor" stroke-opacity="0.3" stroke-width="1">
<rect x="212" y="24" width="218" height="24" fill="none"/>
<rect x="212" y="52" width="218" height="24" fill="#EF9F27" fill-opacity="0.3"/>
<rect x="212" y="80" width="218" height="24" fill="none"/>
<rect x="212" y="108" width="218" height="24" fill="none"/>
<rect x="212" y="136" width="218" height="24" fill="none"/>
</g>
<g font-size="10" fill="currentColor">
<text x="222" y="40">symbol　一个列块 = 这一列在行组内的全部数据</text>
<text x="222" y="68" font-weight="500">ts</text>
<text x="222" y="96">seq</text>
<text x="222" y="124">px</text>
<text x="222" y="152">vol / side / channel …</text>
</g>
<line x1="434" y1="64" x2="462" y2="64" stroke="currentColor" stroke-opacity="0.4" stroke-dasharray="3 2"/>
<path d="M462 64 l-7 -3.5 l0 7 z" fill="currentColor" fill-opacity="0.5"/>
<g stroke="currentColor" stroke-opacity="0.3" stroke-width="1">
<rect x="470" y="24" width="210" height="28" fill="#1D9E75" fill-opacity="0.18"/>
<rect x="470" y="58" width="210" height="26" fill="none"/>
<rect x="470" y="90" width="210" height="26" fill="none"/>
<rect x="470" y="122" width="210" height="26" fill="none"/>
<rect x="470" y="154" width="210" height="26" fill="none"/>
</g>
<g font-size="10" fill="currentColor">
<text x="480" y="42">字典页（可选，只有字典编码才有）</text>
<text x="480" y="75">data page 0　≈ 1 MB</text>
<text x="480" y="107">data page 1</text>
<text x="480" y="139">data page 2</text>
<text x="480" y="171">…　最后一页可能不满</text>
</g>
<g font-size="10.5" fill="currentColor" fill-opacity="0.75">
<text x="212" y="196">列块：读 = 这列整块读；跳过 = 整块不读</text>
<text x="470" y="196">页：解压与剪枝的最小单位</text>
</g>
<line x1="0" y1="208" x2="680" y2="208" stroke="currentColor" stroke-opacity="0.15"/>
<text x="0" y="226" font-size="11.5" fill="currentColor" fill-opacity="0.8">两级剪枝：行组靠 footer 里的 min/max 跳；页靠 page index 跳。列裁剪是另一回事——它决定"读哪几个 chunk"。</text>
</svg>
</figure>

四层各自的参数与默认值（pyarrow / parquet-rs 基本一致）：

| 层 | 决定它的参数 | 默认值 | 作用 |
|---|---|---|---|
| file | — | — | 容器，尾部 footer |
| row group | `row_group_size` | 1,048,576 行（并非按字节） | 剪枝粒度；越大压缩率越好、点查读得越多 |
| column chunk | 列的物理存储 | 每列每行组一个 | 列裁剪的粒度 |
| page | `data_page_size` | 1 MB | 解压与编解码的粒度；越小 page index 越细、开销越大 |

这里有个**极易搞错**的点：`row_group_size` 是按**行数**算的，不是字节。同样的 1,048,576 行，7 列的 tick 数据只有几十 MB，而 200 列宽表可能几个 GB。想让行组落在 128 MB 附近，得按"行数 × 每行字节"反推，或者写完后读元数据校正（第八章的 compaction 会用到）。

## 3. 一个 page 里到底有什么

page 是"解压"和"剪枝"的最小单位。它的内部布局比大多数人以为的复杂：

<figure class="diagram">
<svg width="100%" viewBox="0 0 680 184" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
<title>data page v1 的内部布局</title>
<desc>一个数据页由 thrift 编码的页头、重复级别、定义级别和值主体组成；重复与定义级别用 RLE 编码，值主体用该列选择的编码。</desc>
<text x="0" y="14" font-size="13.2" font-weight="500" fill="currentColor">data page v1</text>
<g stroke="currentColor" stroke-opacity="0.35" stroke-width="1">
<rect x="0" y="26" width="150" height="46" fill="#378ADD" fill-opacity="0.25"/>
<rect x="150" y="26" width="96" height="46" fill="none"/>
<rect x="246" y="26" width="96" height="46" fill="none"/>
<rect x="342" y="26" width="338" height="46" fill="#EF9F27" fill-opacity="0.25"/>
</g>
<g font-size="10.5" text-anchor="middle" fill="currentColor">
<text x="75" y="46">page header</text><text x="75" y="62" fill-opacity="0.65">thrift，未压缩</text>
<text x="198" y="46">repetition levels</text><text x="198" y="62" fill-opacity="0.65">RLE / bit-packed</text>
<text x="294" y="46">definition levels</text><text x="294" y="62" fill-opacity="0.65">RLE / bit-packed</text>
<text x="511" y="46">values</text><text x="511" y="62" fill-opacity="0.65">PLAIN / DICT / DELTA / BSS</text>
</g>
<text x="0" y="92" font-size="11.5" fill="currentColor" fill-opacity="0.8">header 里有：页类型、未压缩/压缩字节数、值个数、levels 的编码方式（RLE），以及页级统计（可选，pyarrow 会写）。</text>
<text x="0" y="110" font-size="11.5" fill="currentColor" fill-opacity="0.8">注意 v1 页头不含 levels 的字节长度——要解出 levels 才知道 values 从哪开始（v2 补上了，见下图）。</text>
<text x="0" y="128" font-size="11.5" fill="currentColor" fill-opacity="0.8">repetition levels 只在嵌套类型（LIST / STRUCT）里出现；扁平表里这一段的长度为 0。</text>
<text x="0" y="146" font-size="11.5" fill="currentColor" fill-opacity="0.8">definition levels 记录"这一行是不是 null"，非空列全 0 长度，所以扁平非空表几乎不占空间。</text>
<line x1="0" y1="158" x2="680" y2="158" stroke="currentColor" stroke-opacity="0.15"/>
<text x="0" y="178" font-size="11.5" fill="currentColor" fill-opacity="0.85" font-weight="500">v2 把 levels 移出压缩体，单独一段：不解压 values 也能先按 levels 判断有没有数据可跳。</text>
</svg>
</figure>

三个细节值得记住，它们是后面排错的关键：

1. **页头不压缩**。所以"读一页"永远是"读页头 + 解压正文"，页头本身有几十到几百字节的开销。页切得太碎，页头开销会反噬压缩收益。
2. **字典页必须排在该列块的所有数据页之前**。这就是元数据里 `dictionary_page_offset` 总是小于 `data_page_offset` 的原因；两者都存在时，`file_offset` 通常等于字典页的偏移。
3. **`encodings` 列表是"这个列块用过的编码集合"，不是"整列都用它"**。看到 `PLAIN + RLE + RLE_DICTIONARY` 三个并存，意味着：字典页用了 PLAIN 存字典值、数据页用了 `RLE_DICTIONARY` 存索引、levels 用了 RLE。这也是任务卡里那条坑的由来。

### data page v2：同一份数据的另一种切法

data page 有 v1、v2 两个版本（`PageHeader.type` 分别是 `DATA_PAGE=0` 和 `DATA_PAGE_V2=3`）。v1 是几乎所有写入器的默认；v2 是规范后来补的页格式，写侧要显式开启。与上图 v1 逐段对齐看：

<figure class="diagram">
<svg width="100%" viewBox="0 0 680 176" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
<title>data page v2 的内部布局（与 v1 逐段对齐）</title>
<desc>v2 数据页由页头、独立且不压缩的重复级别段、独立且不压缩的定义级别段、单独压缩的值主体组成；levels 的字节长度直接写在页头里。</desc>
<text x="0" y="14" font-size="13.2" font-weight="500" fill="currentColor">data page v2（同一份数据、同一 zstd，与上图 v1 对齐）</text>
<g stroke="currentColor" stroke-opacity="0.35" stroke-width="1">
<rect x="0" y="26" width="150" height="46" fill="#378ADD" fill-opacity="0.25"/>
<rect x="150" y="26" width="96" height="46" fill="#1D9E75" fill-opacity="0.3"/>
<rect x="246" y="26" width="96" height="46" fill="#1D9E75" fill-opacity="0.3"/>
<rect x="342" y="26" width="338" height="46" fill="#EF9F27" fill-opacity="0.25"/>
</g>
<g font-size="10.5" text-anchor="middle" fill="currentColor">
<text x="75" y="46">page header</text><text x="75" y="62" fill-opacity="0.65">thrift，未压缩，76 B</text>
<text x="198" y="46">repetition levels</text><text x="198" y="62" fill-opacity="0.65">RLE，不压缩（0 B）</text>
<text x="294" y="46">definition levels</text><text x="294" y="62" fill-opacity="0.65">RLE，不压缩（4 B）</text>
<text x="511" y="46">values</text><text x="511" y="62" fill-opacity="0.65">同 v1 编码，单独压缩</text>
</g>
<text x="0" y="92" font-size="11.5" fill="currentColor" fill-opacity="0.8">levels 搬出压缩体、永不压缩，字节长度直接写在页头（本例 def=4 B / rep=0 B）——不解压 values 就能扫 levels。</text>
<text x="0" y="110" font-size="11.5" fill="currentColor" fill-opacity="0.8">页头自描述更强：num_nulls、num_rows 直接给出（v1 只有 num_values）；是否压缩由 is_compressed 显式声明。</text>
<text x="0" y="128" font-size="11.5" fill="currentColor" fill-opacity="0.8">代价是页头更大（76 B vs 69 B）；且 v2 要求页从行边界开始，一行不允许跨页。</text>
<line x1="0" y1="140" x2="680" y2="140" stroke="currentColor" stroke-opacity="0.15"/>
<text x="0" y="160" font-size="11.5" fill="currentColor" fill-opacity="0.85" font-weight="500">读侧三种实现（Arrow / parquet-rs / DuckDB）都透明支持；收益主要在嵌套稀疏数据与页级剪枝，扁平全非空表上差距不大。</text>
</svg>
</figure>

同一份数据、pyarrow 写两遍（只切 `data_page_version`，其余 zstd / 单行组不变）的实测对照：

| 对比项 | v1（实测） | v2（实测） |
|---|---|---|
| 页头大小 | 69 B | 76 B（多出的字节换来显式长度与统计） |
| levels | 与 values 捆在一起进压缩体 | 独立段、不压缩，长度写在页头（def=4 B / rep=0 B） |
| 页头里的页信息 | num_values + levels 的编码方式 | 另有 num_nulls=0、num_rows、is_compressed=true、可选页级统计 |
| 压缩粒度 | levels 与 values 整段一起压 | 只压 values，levels 永不压 |
| 页边界 | 无行对齐要求 | 必须从行边界开始（行不跨页） |
| 文件总体积 | 1,020,636 B | 1,020,577 B（扁平全非空表上差距可忽略） |

### 怎么知道手里的文件是 v1 还是 v2

判定只需读 2 个字节：页类型是页头的第 1 个 thrift compact 字段，`zigzag varint` 解码后 `0=DATA_PAGE(v1)`、`3=DATA_PAGE_V2`，页头起点就是元数据里的 `data_page_offset`：

```python
def data_page_version(path: str) -> str:
    """读 data_page_offset 起的第一个页头 type 字段。"""
    import pyarrow.parquet as pq

    md = pq.ParquetFile(path).metadata
    cc = md.row_group(0).column(0)
    with open(path, "rb") as f:
        f.seek(cc.data_page_offset)
        raw = f.read(2)
    # 第 1 字节是 thrift 字段头(0x15)，第 2 字节是 zigzag varint：
    # 0x00 -> type=0 (v1)；0x06 -> type=3 (v2)
    ptype = (raw[1] >> 1) ^ -(raw[1] & 1)
    return "v2" if ptype == 3 else "v1"
```

对仓库文件实测输出 `v1`。写入器默认行为（本仓库三件套实测）：

| 写入器 | 默认数据页 | 切到 v2 的方法 |
|---|---|---|
| pyarrow | v1 | 写参数 `data_page_version="2.0"`（写侧别名 `2.6` 不被接受，见下） |
| DuckDB | v1 | 写侧无 v2 页开关；`COPY ... (PARQUET_VERSION v2)` 实测写出的仍是 v1 页 |
| polars | v1 | 原生写侧无开关（只有 `data_page_size`）；`use_pyarrow=True` 转手 pyarrow 才能切 |

最容易踩的混淆在这里：**"格式版本"和"数据页版本"是两回事**。pyarrow 的 `version="2.6"` 与 DuckDB 的 `PARQUET_VERSION v2` 指的是 Parquet **格式版本**（管的是逻辑类型、nanos 时间戳这类 footer 能力），跟数据页 v1/v2 无关——实测 `PARQUET_VERSION v2` 写出的文件，页头 type 依然是 `DATA_PAGE(0)`。数据页要单独用 `data_page_version` 切，切完用上面的函数验证。

### levels：把嵌套结构压平的伴随流

页布局里那两段 `repetition levels / definition levels` 是干什么的最容易含糊。Parquet 把嵌套类型（LIST / STRUCT、可空字段）**压平**成一列值存储，值流本身不携带任何"归属"信息——谁来记录这串值原来是几行、是第几个元素、哪里是 null？就是这两条 RLE 伴随流，**每个值都带一对 (def, rep)**：

- **definition level**：这条路径上有几层 optional 字段**有值**。`def = max_def` 说明值是真的；小于它说明中间某层是 null。它同时能表达空列表这类"壳在、没值"的状态。
- **repetition level**：这个值和前一个值的关系。`rep = 0` 是新行的开始；`rep = n` 表示还在第 n 层重复字段的同一个容器里。它负责把压平的值流**重新拼回行**。

用一个列表列（`a: optional list of optional item`）看全貌，三行逻辑数据 `[1,2] / [] / null`：

<figure class="diagram">
<svg width="100%" viewBox="0 0 680 212" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
<title>definition level 与 repetition level 的含义</title>
<desc>逻辑上三行列表数据，压平后每个值伴随一对 def 与 rep 级别；def 记录路径上有几层 optional 有值，rep 记录与前一值是否同一行同一列表。</desc>
<text x="0" y="14" font-size="13.2" font-weight="500" fill="currentColor">逻辑数据（左）与压平后的存储流（右）</text>
<g stroke="currentColor" stroke-opacity="0.35" stroke-width="1">
<rect x="0" y="26" width="150" height="28" fill="#378ADD" fill-opacity="0.25"/>
<rect x="0" y="60" width="150" height="28" fill="#378ADD" fill-opacity="0.25"/>
<rect x="0" y="94" width="150" height="28" fill="currentColor" fill-opacity="0.06"/>
</g>
<g font-size="11.5" fill="currentColor">
<text x="10" y="45">行 1：a = [1, 2]</text>
<text x="10" y="79">行 2：a = [ ]（空列表）</text>
<text x="10" y="113">行 3：a = null</text>
</g>
<g stroke="currentColor" stroke-opacity="0.35" stroke-width="1">
<rect x="230" y="26" width="84" height="28" fill="#378ADD" fill-opacity="0.25"/>
<rect x="322" y="26" width="84" height="28" fill="#1D9E75" fill-opacity="0.3"/>
<rect x="414" y="26" width="84" height="28" fill="#EF9F27" fill-opacity="0.3"/>
<rect x="230" y="60" width="84" height="28" fill="#378ADD" fill-opacity="0.25"/>
<rect x="322" y="60" width="84" height="28" fill="#1D9E75" fill-opacity="0.3"/>
<rect x="414" y="60" width="84" height="28" fill="#EF9F27" fill-opacity="0.3"/>
<rect x="230" y="94" width="84" height="28" fill="currentColor" fill-opacity="0.06"/>
<rect x="322" y="94" width="84" height="28" fill="#1D9E75" fill-opacity="0.3"/>
<rect x="414" y="94" width="84" height="28" fill="#EF9F27" fill-opacity="0.3"/>
<rect x="230" y="128" width="84" height="28" fill="currentColor" fill-opacity="0.06"/>
<rect x="322" y="128" width="84" height="28" fill="#1D9E75" fill-opacity="0.3"/>
<rect x="414" y="128" width="84" height="28" fill="#EF9F27" fill-opacity="0.3"/>
</g>
<g font-size="12" text-anchor="middle" fill="currentColor">
<text x="272" y="45">值 1</text><text x="364" y="45">def=2</text><text x="456" y="45">rep=0</text>
<text x="272" y="79">值 2</text><text x="364" y="79">def=2</text><text x="456" y="79">rep=1</text>
<text x="272" y="113">（无值）</text><text x="364" y="113">def=1</text><text x="456" y="113">rep=0</text>
<text x="272" y="147">（无值）</text><text x="364" y="147">def=0</text><text x="456" y="147">rep=0</text>
</g>
<g font-size="11" fill="currentColor" fill-opacity="0.8">
<text x="510" y="45">两层 optional 都有值</text>
<text x="510" y="79">同行同列表的下一个元素</text>
<text x="510" y="113">a 有值，列表为空</text>
<text x="510" y="147">a 是 null，整条路径无值</text>
</g>
<text x="0" y="184" font-size="11.5" fill="currentColor" fill-opacity="0.8">读侧按 def 重建 null、按 rep 重建行边界——嵌套结构全靠这两条流还原。</text>
<text x="0" y="204" font-size="11.5" fill="currentColor" fill-opacity="0.85" font-weight="500">退化到扁平表：def 恒为 1 → RLE 压成 4 B；rep 恒为 0 → 0 B——正是上面 v2 页头的实测值。</text>
</svg>
</figure>

这也是第 4 节编码表里 `RLE` 的主战场之一：levels 几乎全是小整数、大量重复，RLE/bit-packed 混合编码是它们唯一指定的编码。而 v2 把 levels 独立成不压缩的段（本节开头两张图），让读侧**不解压 values 也能先扫 levels**——全 null 页可以整页跳过解压，这是 v2 在剪枝上最大的收益。

## 4. 六种编码，各自为哪种分布而生

| 编码 | 编码后的样子 | 适合 | 不适合 | 本仓库实测（73,375 行） |
|---|---|---|---|---|
| `PLAIN` | 原样定长拼接 | 兜底、无更好选择时 | 几乎所有有规律的数据 | `vol` 关字典后 42.2 → 73.6 KB |
| `RLE` | 值 + 重复次数 | 常量列、levels | 高基数列 | `symbol` 整列 312 B（1 个值） |
| `RLE_DICTIONARY` | 字典 + 索引（RLE 压缩） | **低基数**（合约、交易所、通道） | 近乎唯一的列 | `ts` 默认 614.5 KB ← **反面教材** |
| `DELTA_BINARY_PACKED` | 首值 + 差值（变长位宽） | **单调递增整数**（时间戳、序号） | 乱序或抖动大的整数 | `ts` 614.5 → 279.1 KB；`seq` 210.8 → **3.8 KB** |
| `DELTA_BYTE_ARRAY` | 前缀长度 + 后缀 | 有公共前缀的字符串 | 无规律字符串 | 本例 `symbol` 全同值，用不上 |
| `BYTE_STREAM_SPLIT` | 按字节位拆成 8 个流 | 高基数、相邻相近的浮点 | 低基数、高重复的浮点 | `px` 97.4 → **326.5 KB（负优化）** |

`RLE_DICTIONARY` 对 `ts` 为什么是灾难：73,375 个时间戳几乎全不相同，字典要为每个值存一份 int64（8 字节）+ 索引，相当于"原数据 + 一份索引"。而 `DELTA_BINARY_PACKED` 只需要存"首值 + 相邻差值"，tick 数据相邻时间差通常几十毫秒（几千万纳秒），位宽远小于 64 位。

`BYTE_STREAM_SPLIT` 为什么在价格列上是负优化，在别处却是神器，下一节用实测数字讲。

## 5. 实测：同一份数据，七种组合

`py/w02_encoding.py` 把同一张表写七遍，只改编码参数（压缩统一 zstd-3，行组不变）：

```bash
uv run python py/w02_encoding.py     # 产出 reports/w02-encoding.md
```

<figure class="diagram">
<svg width="100%" viewBox="0 0 680 226" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
<title>七种编码组合的文件体积对比</title>
<desc>默认全字典方案 996.7 KB；仅把 ts 与 seq 换成 DELTA_BINARY_PACKED 就降到 454.2 KB；在此基础上把 px 换成 BYTE_STREAM_SPLIT 反而升到 683.3 KB；行序打乱后所有方案都变差。</desc>
<text x="0" y="14" font-size="13.2" font-weight="500" fill="currentColor">同一份 73,375 行数据 · zstd-3 · 1 个行组</text>
<line x1="150" y1="24" x2="150" y2="196" stroke="currentColor" stroke-opacity="0.2"/>
<g font-size="10.5" fill="currentColor">
<text x="0" y="44">a 全默认字典</text>
<text x="0" y="74">b ts/seq 走 DELTA</text>
<text x="0" y="104">c 再用 px 走 BSS</text>
<text x="0" y="134">d 全部关字典</text>
<text x="0" y="164">f 乱序 + b 的编码</text>
<text x="0" y="194">g 乱序 + 默认字典</text>
</g>
<rect x="150" y="32" width="392" height="16" fill="currentColor" fill-opacity="0.35"/>
<rect x="150" y="62" width="179" height="16" fill="#1D9E75" fill-opacity="0.65"/>
<rect x="150" y="92" width="269" height="16" fill="#EF9F27" fill-opacity="0.5"/>
<rect x="150" y="122" width="297" height="16" fill="currentColor" fill-opacity="0.25"/>
<rect x="150" y="152" width="296" height="16" fill="currentColor" fill-opacity="0.25"/>
<rect x="150" y="182" width="451" height="16" fill="currentColor" fill-opacity="0.35"/>
<g font-size="10.5" fill="currentColor">
<text x="550" y="44">996.7 KB</text>
<text x="337" y="74" font-weight="500">454.2 KB（−54%）</text>
<text x="427" y="104">683.3 KB</text>
<text x="455" y="134">756.3 KB</text>
<text x="454" y="164">753.5 KB</text>
<text x="609" y="194">1,147.2 KB</text>
</g>
<line x1="0" y1="206" x2="680" y2="206" stroke="currentColor" stroke-opacity="0.15"/>
<text x="0" y="222" font-size="11" fill="currentColor" fill-opacity="0.8">柱长 = 文件体积。e（+page index）与 c 几乎同高，未单列，只多 0.1 KB。</text>
</svg>
</figure>

三列关键数据放在一起看：

| 列 | a 默认字典 | b 换编码 | 变化 | c 再改 px | 乱序 + b |
|---|---|---|---|---|---|
| `ts` | 614.5 KB | 279.1 KB | **−55%** | 279.1 | 414.4 KB |
| `seq` | 210.8 KB | 3.8 KB | **−98%** | 3.8 | 160.5 KB |
| `px` | 97.4 KB | 97.4 KB | — | **326.5 KB（×3.4）** | 102.8 KB |

三个可以带走的结论：

**① `seq` 从 210.8 KB 掉到 3.8 KB，是整章最夸张的一个数。** 因为 `seq` 在单合约单日内严格 0,1,2,… 递增，DELTA 之后差值是恒定 1，RLE 再把它压成"1 重复 73,374 次"。这不是编码神奇，是**数据本身的规律被编码器认出来了**。

**② `px` 用 `BYTE_STREAM_SPLIT` 反而涨 3.4 倍。** BSS 的做法是把 8 字节 double 拆成 8 条独立字节流分别压缩，它对"高基数、相邻值相近"的浮点（传感器读数、物理测量值）很有效，因为尾数字节的变化有规律。但这里的 `px` 只有几百个不同取值、重复率极高——**字典编码把它压到了 97 KB，而 BSS 主动放弃了"重复消除"这个最强的工具**，只剩字节流的熵编码，自然更差。

**③ 排序值 40% 体积，而且比编码更基础。** 对比 b（有序）与 f（同一批行，只打乱顺序）：`ts` 279.1 → 414.4 KB，`seq` 3.8 → **160.5 KB（42 倍）**。编码没变，只是"相同前缀的整数不再相邻"，DELTA 就退化了。反过来说，**先排序再选编码**，才能拿到 996.7 → 454.2 这 54% 的收益；只换编码不排序，最多拿到 996.7 → 753.5（24%）。

## 6. 统计信息有三层，管的事完全不同

剪枝能跳过多大一块，取决于用哪一层统计：

| 层 | 存在哪 | 记录什么 | 能跳过 | 代价 |
|---|---|---|---|---|
| column chunk stats | footer | 每列每行组的 `min` / `max` / `null_count` / `distinct_count` | **整个行组** | 几乎为 0，默认开启 |
| page index | footer 附近的独立段 | 每页的 `min` / `max` + 页的字节偏移 | **一页**（约 1 MB） | 文件略增（本例 +0.1 KB），读取时多一次 IO |
| bloom filter | 列块尾部 | 值是否**可能存在**（等值判断） | 整个行组（等值查询） | 体积约 1–2%，只对等值有效 |

对行情数据来说，这三层的分工很具体：

- **按时间范围查** → column chunk stats 就够：`ts` 的 `min/max` 能整块跳。
- **按合约查** → 若文件内已按 `symbol` 排序（第八章的分区策略），`symbol` 的 `min/max` 直接跳；若混装多合约，`symbol` 的 min/max 覆盖全体，剪枝完全失效，这时才需要 bloom filter 或分区。
- **按 `seq` 找缺口** → 必须读整列，任何统计都帮不上（`min/max` 只能说"这个范围里有值"，不能证明"某个值不存在"）。

一个必须知道的语义坑：**浮点列的 `min`/`max` 可能是近似值**。规范里有 `min_value_exact` / `max_value_exact` 两个布尔标记，为 false 时表示该边界可能被截断过（历史实现遗留）。拿它做"一定不存在"的断言会出错。整数与字符串没有这个问题。

### 实测：页级剪枝到底省多少

页级剪枝的规则一句话：**查询区间 ∩ [页 min, 页 max] = 空 → 整页不读**。它和行组级剪枝是同一套逻辑套了两层粒度——footer 的行组统计先跳行组，page index 再在存活的行组里跳页。

它到底值多少钱，取决于数据的有序程度。用 10 万行递增 `ts` 写两份文件（唯一区别：一份保序、一份打乱行序；都开 page index，切成 7 页，zstd），查询窗口取中间约 0.1% 的时间范围，把 page index（`ColumnIndex` + `OffsetIndex`，二进制在 footer 后面的独立段）逐字节解析出来算相交页数：

| 数据 | 相交页数 | 必读字节 | 节省 |
|---|---|---|---|
| 有序 | **1 / 7 页** | 29,199 / 192,575 B | **85%** |
| 乱序 | **7 / 7 页** | 192,575 / 192,575 B | **0%** |

乱序为什么一页都剪不掉：打乱行序后每页里都有接近全范围的值，每页的 `[min, max]` 都横跨整个时间轴，区间彼此完全覆盖。剪枝是"数据规律的放大器"，不是"引擎的魔法"——**没有排序，就没有剪枝**。这是第 5 节"排序比编码更基础"的第二个证据（第一个是 DELTA 编码退化）。

三个使用前提值得记：

1. **页级剪枝不是免费的默认项**。page index 要写侧显式开启（pyarrow `write_page_index=True`），不开的话行组内只能整块读；页越细索引越准，但页头开销也越大。
2. **剪枝回答的是"可能有"，不是"一定没有"**。`min/max` 只能排除区间不相交的部分；按 `seq` 找缺口这种"证明不存在"的问题，任何统计都帮不上。
3. **浮点列的 min/max 有近似坑**（上面的 `min_value_exact`），等值剪枝别信它。

## 7. 代码：用 20 行复刻 inspect

`mdlake inspect` 干的事，用 pyarrow 复刻只要二十来行——这本身就是理解格式的最好检验：

```python
import pyarrow.parquet as pq

md = pq.ParquetFile(path).metadata
print(f"行数={md.num_rows} 行组={md.num_row_groups} 列={md.num_columns}")

for rg in range(md.num_row_groups):
    g = md.row_group(rg)
    print(f"\n── 行组 #{rg} 行数={g.num_rows} 压缩后={g.total_byte_size} B")
    for c in range(g.num_columns):
        cc = g.column(c)
        st = cc.statistics
        print(
            f"  {cc.path_in_schema:10} {cc.compression:5} "
            f"{'+'.join(cc.encodings):30} {cc.total_compressed_size:>9} B "
            f"min={st.min if st else None} max={st.max if st else None}"
        )
```

要验证自己写对了，拿它和 Rust 版的输出逐行对比——字段名不同（`total_byte_size` vs `compressed_size`），数值必须一致。**这种"两种语言同一份结论"的交叉验证，比读十遍文档有用。**

## 8. 踩坑

| 坑 | 表现 | 正确做法 |
|---|---|---|
| 默认值陷阱 | pyarrow 几乎给所有列开字典，时间戳/价格列体积被撑大 2–3 倍 | 显式写 `column_encoding`；`ts`/`seq` 用 `DELTA_BINARY_PACKED` |
| 混淆 `encodings` 与"实际编码" | 以为整列都是字典，结果一半是 PLAIN | 看 `dictionary_page_offset` 是否存在，以及字典页字节占比 |
| 把格式版本当成数据页版本 | 以为 `version="2.6"` / `PARQUET_VERSION v2` 就写了 v2 数据页，实测仍是 v1 | 格式版本管逻辑类型；数据页用 `data_page_version`，再用上面的判定函数验证 |
| 把 `row_group_size` 当字节 | 以为设 134217728 就是 128 MB | 它是行数。按 `行数 × 每行字节` 反推 |
| 用 `min/max` 做精确判断 | 浮点列上误判"不存在" | 检查 `min_value_exact`；精确判断要靠实际扫描 |
| BSS 一律开 | 低基数浮点列体积暴涨 | 只有高基数、相邻相近的浮点才用；先测再开 |
| 页切太碎 | 压缩比下降、页头开销上升 | `data_page_size` 保持在 1 MB 量级，除非确实需要细粒度剪枝 |
| 以为页级剪枝天然生效 | 没开 page index，行组内只能整块读，怎么调页大小都不剪 | 写侧显式 `write_page_index=True`；再用 `has_column_index` / `has_offset_index` 确认 |

## 9. 出处

- Apache Parquet, *File Format* — 四层结构、页面类型、`file_offset` / `dictionary_page_offset` 语义
- Apache Parquet, *Encodings* — 各编码的字节格式与适用条件，`min_value_exact` 的定义
- `parquet-format` 仓库 `src/main/thrift/parquet.thrift` — `ColumnMetaData` / `PageHeader` / `DataPageHeader` / `DataPageHeaderV2` 字段的唯一权威定义
- 数据页 v1/v2 对比数据：同源文件 pyarrow 写两遍实测（2026-11-12，pyarrow 26.0.1 / duckdb 2.0.0 / polars），判定函数对仓库 `part-600267.SH.parquet` 输出 `v1`
- 页级剪枝实测：10 万行 `ts` 有序/乱序各写一份（`data_page_size=32KB`、`write_page_index=True`、zstd），按 thrift 解析 `ColumnIndex` / `OffsetIndex` 计算相交页与必读字节（2026-11-12，pyarrow 26.0.1）
- levels 语义：Parquet *File Format* 的 Nesting / Levels 小节；扁平表 def/rep 的字节数取自本页 v2 页头实测（4 B / 0 B）
- parquet-rs `src/file/metadata/mod.rs`、`src/encodings/` — 本项目 `mdlake inspect` 的实现依据（版本 60.1.0）
- 本章数据：`reports/w02-encoding.md`（脚本 `py/w02_encoding.py`，2026-11-11，pyarrow 26.0.1）

---

**下一步**：第 3 章把"编码"和"写入器参数"合到一起，做完整的调参矩阵（`encoding × compression × row_group × sort order`），并给出三语言写入器的参数对照。
