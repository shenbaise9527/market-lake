# 第 4 章 · Arrow 内存模型：三件套与零拷贝的边界

!!! abstract "本章结论（读完你应该能复述这三句）"
    1. Arrow 数组只有三样东西：**长度、null 数、一组 buffer**。`int64` 是 2 个 buffer（validity + values），`string` 是 3 个（validity + offsets + data），字典数组是"索引 buffer + 一张值表"。简单到可以用几个指针跨语言描述——这就是第 5 章 C Data Interface 的全部前提。
    2. "零拷贝"省掉的**不是解析，是转置**。同样 587,000 行，行式 40 B 记录必须按 stride 把字段挑出来（11.2 ms、19.03 MB 分配），列式布局直接 `from_buffers` 套类型（**1.4 ms、0 分配**）。二进制不一定快，**列式**才快。
    3. `slice` 是 O(1)（100 万行 7.3 µs，地址不变），代价是**拖住整块 buffer**：2,000 个 1,000 行切片账面只有 15.3 MB，实际驻留 76.3 MB。

对应任务卡：仓库内 `docs/week04.md` ｜ 本章实验脚本：`py/w04_arrow_memory.py` ｜ 原始结果：`reports/w04-arrow.md`

---

## 1. 先看一张"数组长什么样"的图

Parquet 解决的是"怎么放在磁盘上"，Arrow 解决的是"怎么放在内存里"。两者都列式，但目标不同：Parquet 要**压得小**，Arrow 要**随机访问得快、且能被多个进程/语言共享**。

所以 Arrow 的设计约束是"结构必须简单到可以用指针描述"——不能有指针链、不能有虚函数、不能依赖某种语言的对象头。最后剩下的就只有 buffer：

<figure class="diagram">
<svg width="100%" viewBox="0 0 680 236" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
<title>三种 Arrow 数组的 buffer 布局</title>
<desc>Int64Array 由 validity 位图与 values 组成；StringArray 多一个 offsets 数组；DictionaryArray 用索引 buffer 指向一张值表。</desc>
<text x="0" y="14" font-size="13.2" font-weight="500" fill="currentColor">同样 4 行，三种类型的 buffer 数量不同</text>
<g font-size="10.5" fill="currentColor">
<text x="0" y="46" font-weight="500">Int64Array</text>
<text x="0" y="60" fill-opacity="0.6">2 个 buffer</text>
<text x="0" y="112" font-weight="500">StringArray</text>
<text x="0" y="126" fill-opacity="0.6">3 个 buffer</text>
<text x="0" y="186" font-weight="500">DictionaryArray</text>
<text x="0" y="200" fill-opacity="0.6">索引 + 值表</text>
</g>
<g stroke="currentColor" stroke-opacity="0.4" fill="none">
<rect x="128" y="34" width="30" height="24"/>
<rect x="166" y="34" width="30" height="24"/><rect x="196" y="34" width="30" height="24"/>
<rect x="226" y="34" width="30" height="24"/><rect x="256" y="34" width="30" height="24"/>
<rect x="128" y="100" width="30" height="24"/>
<rect x="166" y="100" width="26" height="24"/><rect x="192" y="100" width="26" height="24"/>
<rect x="218" y="100" width="26" height="24"/><rect x="244" y="100" width="26" height="24"/>
<rect x="270" y="100" width="26" height="24"/>
<rect x="304" y="100" width="96" height="24"/>
<rect x="128" y="174" width="30" height="24"/>
<rect x="166" y="174" width="26" height="24"/><rect x="192" y="174" width="26" height="24"/>
<rect x="218" y="174" width="26" height="24"/><rect x="244" y="174" width="26" height="24"/>
<rect x="330" y="174" width="70" height="24"/><rect x="400" y="174" width="70" height="24"/>
</g>
<g font-size="9.5" text-anchor="middle" fill="currentColor" fill-opacity="0.85">
<text x="143" y="50">1 B</text>
<text x="181" y="50">10</text><text x="211" y="50">20</text><text x="241" y="50">—</text><text x="271" y="50">40</text>
<text x="143" y="116">1 B</text>
<text x="179" y="116">0</text><text x="205" y="116">9</text><text x="231" y="116">18</text><text x="257" y="116">18</text><text x="283" y="116">27</text>
<text x="143" y="190">1 B</text>
<text x="179" y="190">0</text><text x="205" y="190">1</text><text x="231" y="190">—</text><text x="257" y="190">0</text>
<text x="365" y="190">000001.SZ</text><text x="435" y="190">600000.SH</text>
</g>
<g font-size="9.5" fill="currentColor" fill-opacity="0.65">
<text x="128" y="30">validity</text><text x="166" y="30">values：8 B/行</text>
<text x="128" y="96">validity</text><text x="166" y="96">offsets：4 B/行</text><text x="306" y="96">data：27 B</text>
<text x="128" y="170">validity</text><text x="166" y="170">indices：1 B/行</text><text x="330" y="170">dictionary 值表</text>
</g>
<text x="470" y="60" font-size="10.5" fill="currentColor" fill-opacity="0.75">values 是裸的内存块，</text>
<text x="470" y="74" font-size="10.5" fill="currentColor" fill-opacity="0.75">没有指针、没有对象头。</text>
<text x="470" y="126" font-size="10.5" fill="currentColor" fill-opacity="0.75">字符串不是 N 个对象，</text>
<text x="470" y="140" font-size="10.5" fill="currentColor" fill-opacity="0.75">是"一段字节 + 切分点"。</text>
<text x="470" y="196" font-size="10.5" fill="currentColor" fill-opacity="0.75">字典是内存表示，</text>
<text x="470" y="210" font-size="10.5" fill="currentColor" fill-opacity="0.75">不是写盘编码。</text>
<line x1="0" y1="218" x2="680" y2="218" stroke="currentColor" stroke-opacity="0.15"/>
<text x="0" y="233" font-size="11" fill="currentColor" fill-opacity="0.8">灰色格子是字节，不是行。注意 StringArray 的 offsets 有 5 个值——n 行需要 n+1 个切分点。</text>
</svg>
</figure>

三行对应三种最常用的类型。把它们写成表：

| 类型 | buffer 0 | buffer 1 | buffer 2 | 每行成本 |
|---|---|---|---|---|
| `int64` / `float64` | validity（1 bit） | values（8 B） | — | 8 B + 0.125 B |
| `string` | validity（1 bit） | offsets（4 B，n+1 个） | data（变长） | 4 B + 平均字节数 |
| `dictionary<string, int8>` | validity（1 bit） | indices（1 B） | 值表（唯一值 × 长度） | 1 B + 可忽略 |

`null` 不占 values 的空间——`values` 长度永远等于行数，null 位置填什么值都行（通常是 0 或上一个值的副本），由 validity 决定它是否有效。**所以"含 null 的 int64 数组"和"不含 null 的"占一样多的 values 内存**，差别只有那张 1 bit/行 的位图。

## 2. validity bitmap：最低位是第 0 行

这是全章唯一需要背下来的位序约定。造 8 个值、第 0 行和第 7 行为 null：

```text
bitmap 十六进制 7e  二进制 0111_1110
行 0: bit=0 → null      行 4: bit=1 → valid
行 1: bit=1 → valid     行 5: bit=1 → valid
行 2: bit=1 → valid     行 6: bit=1 → valid
行 3: bit=1 → valid     行 7: bit=0 → null
```

`0x7E` 的低位是第 0 行、高位是第 7 行[^1]。这意味着两件事：

**① 位图天生适合 SIMD。** 判断"这 64 行里有没有 null"就是读一个 `uint64` 看是否全 1，一条指令；而"哪些行是 null"可以用 `pext`/`tzcnt` 直接产出索引。这解释了为什么 Arrow 的 filter 比"逐行 if"快一个量级。

**② 没有 null 时 buffer 是 `None`，一个字节都不占。** 实测 100 万行无 null 的 `int64` 数组，`buffers()` 返回 `[None, 8,000,000]`；全 null 的返回 `[125,000, 8,000,000]`——位图 125 KB 照占，values 也照占 8 MB（因为长度固定）。

行情数据里价格、成交量基本不会有 null，所以"给每个字段都留 valid 标记"的旧式设计（比如某些自研二进制协议里的 `valid` 布尔字段）纯属浪费：**1 字节/行 的标记 vs 0.125 字节/行 的位图，差 8 倍**。

## 3. offsets：字符串的两个隐藏成本

`StringArray` 的 `data` buffer 是**一整段连续的字节**，`offsets` 记录每行的起止位置：

```text
offsets: [0, 9, 18, 18, 27]
data   : b'000001.SZ600000.SH000001.SZ'
```

第 2 行（`600000.SH`）是 `data[9:18]`，第 3 行是 null → `data[18:18]`（长度为 0）。`n` 行需要 `n+1` 个 offset，最后一个 offset 等于 `data` 的总长度。

这个设计带来两个成本，都是真实项目里会咬人的：

**① 每行固定 4 字节的 offset。** 即使字符串只有 1 个字符，也要 4 B 的切分点；8 个字符的合约代码实际占 4 + 8 = 12 B。实测 587,000 行的 `symbol` 列：`13.1 B/行`（4 B offset + 9.0 B 平均长度 + 位图摊销）。

**② offset 是 `int32`，所以单个数组的 `data` 不能超过 2 GiB。** 这不是实现限制，是格式定义——`large_string` / `large_binary` 用 `int64` offset 就是为了突破它[^2]。行情场景里单列超过 2 GB 并不罕见：一天全市场 tick 的 `symbol` 列如果不去重，字符串总量很容易到几 GB。

所以**别把 symbol 存成字符串**。用字典数组，或者干脆在落地时映射成 `int16` 的合约 ID（第 8 章的分区设计会用到这一点）。

## 4. 字典：Arrow 的字典和 Parquet 的字典不是一回事

这是最容易混淆的一处。同一个词，两个层面：

| | Parquet 字典 | Arrow 字典 |
|---|---|---|
| 位置 | 磁盘上的**编码方式** | 内存里的**数组类型** |
| 结构 | 字典页 + RLE 索引 | indices buffer + 值表数组 |
| 何时用 | 写文件时决定（`use_dictionary`） | 读进来后决定（`dictionary_encode()`） |
| 能否混用 | 读出来后通常被解码成普通数组 | 可以再编码成 Parquet 字典，但两者独立 |

实测 587,000 行、4 个唯一合约：

| 表示 | 内存 | 每行 |
|---|---|---|
| 纯 `StringArray` | 7.35 MB | 13.1 B |
| `DictionaryArray` | **2.24 MB** | **4.00 B** |
| 比值 | **3.3×** | |

但字典不是免费的，反例就在同一张表里：

| 列 | 唯一值 / 行数 | 字典数组 | 原始 `int64` |
|---|---|---|---|
| `ts` | 584,079 / 587,000 | 6.7 MB | **4.5 MB** |

时间戳几乎没有重复值，字典等于"给每个值建一条索引记录"，反而比裸 `int64` 大 49%。这就是第 2、3 章那个坑在内存侧的同一个版本：**字典只对低基数列有收益**。判断标准是唯一次数：唯一值占比超过约 50%，就该放弃字典。

顺带一个实践细节：字典数组的取值要经过一次 indirection（`indices[i]` → `dictionary[j]`），单次访问多一次跳转；但 4 个唯一值的值表常驻 L1，**随机访问反而比 13 B/行 的字符串更快**。字典在这里同时赢了内存和速度——只是别拿它去编码 `ts`。

## 5. slice / batch / table：拷贝发生在哪一步

Arrow 里"表"有三层，名字很容易混：

| 概念 | 是什么 | 典型来源 |
|---|---|---|
| `Array` | 一段连续（或带 offset）的列数据 | `pa.array([...])` |
| `RecordBatch` | schema + 行数 + 一组等长 `Array` | 一次 `next()`、一个行组 |
| `Table` / `ChunkedArray` | **多个** `RecordBatch` / 多个 `Array` 的列表 | `read_table`、多次 `concat` |

关键区别就一句：**`RecordBatch` 内部连续，`Table` 是一串 batch。**

<figure class="diagram">
<svg width="100%" viewBox="0 0 680 226" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
<title>slice 与 chunk：两种零拷贝，一种拷贝</title>
<desc>slice 只改 offset，三个切片共用一块 buffer；chunked array 是多个独立 buffer 的列表，concat 只加指针，combine_chunks 才真正复制。</desc>
<text x="0" y="14" font-size="13.2" font-weight="500" fill="currentColor">slice：一块 buffer，三个视图</text>
<rect x="20" y="58" width="640" height="26" fill="currentColor" fill-opacity="0.08" stroke="currentColor" stroke-opacity="0.35"/>
<g font-size="9.5" text-anchor="middle" fill="currentColor" fill-opacity="0.7">
<text x="60" y="75">0</text><text x="300" y="75">2.5 M</text><text x="600" y="75">10 M</text>
</g>
<g stroke="currentColor" stroke-opacity="0.5" fill="none" stroke-dasharray="3 3">
<path d="M30 56 V40"/><path d="M280 56 V40"/><path d="M600 56 V40"/>
</g>
<g font-size="9.5" text-anchor="middle" fill="currentColor">
<text x="30" y="32">slice(0, 1000)</text>
<text x="280" y="32">slice(4 M, 1 M)</text>
<text x="600" y="32">slice(9 M, 1 M)</text>
</g>
<g font-size="9.5" text-anchor="middle" fill="currentColor" fill-opacity="0.65">
<text x="30" y="100">offset 0</text><text x="280" y="100">offset 4,000,000</text><text x="600" y="100">offset 9,000,000</text>
</g>
<text x="20" y="120" font-size="10.5" fill="currentColor" fill-opacity="0.8">三个切片的 buffer 地址完全相同（0x…e040），只有 offset 和 length 不同 → 6.5 µs，无 memcpy。</text>
<line x1="0" y1="132" x2="680" y2="132" stroke="currentColor" stroke-opacity="0.15"/>
<text x="0" y="150" font-size="13.2" font-weight="500" fill="currentColor">chunk：多个独立 buffer，concat 只加指针</text>
<g stroke="currentColor" stroke-opacity="0.4" fill="none">
<rect x="10" y="162" width="60" height="22"/><rect x="76" y="162" width="60" height="22"/>
<rect x="142" y="162" width="60" height="22"/><rect x="208" y="162" width="60" height="22"/>
<rect x="380" y="162" width="150" height="22"/>
</g>
<g font-size="9.5" text-anchor="middle" fill="currentColor" fill-opacity="0.8">
<text x="40" y="177">chunk 0</text><text x="106" y="177">chunk 1</text><text x="172" y="177">chunk 2</text><text x="238" y="177">chunk 3</text>
<text x="455" y="177">新区块（复制）</text>
</g>
<path d="M310 173 h56" stroke="currentColor" stroke-opacity="0.45"/>
<path d="M366 173 l-7 -3.5 l0 7 z" fill="currentColor" fill-opacity="0.5"/>
<text x="338" y="164" font-size="9.5" text-anchor="middle" fill="currentColor" fill-opacity="0.8">combine_chunks</text>
<text x="10" y="200" font-size="10.5" fill="currentColor" fill-opacity="0.75">concat：9 µs，4 个地址全保留；combine：558 µs / 100 万行，地址全变。</text>
<line x1="0" y1="210" x2="680" y2="210" stroke="currentColor" stroke-opacity="0.15"/>
<text x="0" y="224" font-size="11" fill="currentColor" fill-opacity="0.8">切片的代价在生命周期：2,000 个切片账面 15.3 MB，实际让 76.3 MB 无法释放。</text>
</svg>
</figure>

实测三个数字：

- `slice(4,000,000, 1,000,000)`：**7.3 µs**，地址 `0x…e040` 不变，`offset` 从 0 变 4,000,000。
- `concat_tables([tbl, tbl])`：**9 µs**，行数翻倍、chunk 数从 5 变 10，**原有 chunk 地址一个都没变**——它只是把两个 chunk 列表拼起来。
- `combine_chunks()`：**558 µs**（100 万行），chunk 数 10 → 1，新地址与旧地址不同——**这一步才是真正的 memcpy**。

于是有了两条工程规则：

**① 流式处理优于"先拼再算"。** 读 100 个行组后 `combine_chunks()` 再聚合，等于把整列复制一遍；按 batch 逐个 `pc.sum` 累加，峰值内存只有 1 个 batch。第 5 章的 IPC stream、第 7 章的 `sink_parquet` 都是这个思路的延伸。

**② 切片要小心"拖住大 buffer"。** 做 2,000 个 1,000 行切片，账面 15.3 MB，实际一块 80 MB 的 buffer 一个字节都释放不掉（实测驻留 76.3 MB）。长时间持有小片段（比如按合约缓存最近 1 分钟数据）时，要么定期 `pa.array(slice)` 复制一份，要么从一开始就按需读取而不是切大数组。

## 6. 对齐与内存池：两个"看起来无关"的细节

**对齐。** Arrow 规范要求 buffer **8 字节对齐**、建议 64 字节填充[^3]。但"建议"不等于"保证"。实测 150 个数组的 values buffer 地址对 64 取模：

| `addr % 64` | 0 | 16 | 32 | 48 |
|---|---|---|---|---|
| 个数 | 50 | 50 | 25 | 25 |

全部 16 字节对齐，只有三分之一落在 64 边界上——因为默认内存池是 mimalloc，小块分配按 16/32 对齐。**所以写 SIMD 代码时不能假设 `data_ptr` 是 64 对齐**，要按运行时分派对齐/非对齐两个版本，或者用 `pc` 里已经做过分派的 kernel。这个坑在 C++ 侧尤其常见：同一份代码在 Arrow 官方分配器下跑得飞快，换成 jemalloc 或自定义池就慢 30%。

**内存池。** pyarrow 默认池 `mimalloc`，所有 Arrow buffer 都从它分配，可以查 `bytes_allocated()` / `max_memory()` / `num_allocations()`：

```text
内存池 backend = mimalloc，当前已分配 26.94 MB
一次 read→filter→sum：5.5 ms，结果 33,380,100
  池分配增量 6.76 MB，分配次数 168，池生命周期峰值 95.97 MB
```

一次 587,000 行的"读 → 过滤 → 求和"就要 168 次分配、6.76 MB 增量。注意 `max_memory()` 是**池生命周期峰值**，不会随释放回落——想测"单次查询峰值"得新建一个池（`pa.set_memory_pool(pa.jemalloc_memory_pool())` 或 `pa.proxy_memory_pool()`），或者用 `pa.total_allocated_bytes()` 做前后差。

更重要的是：`filter` **不修改原数组**，它分配一个新的 values buffer 并把命中的值拷过去。所以"过滤后只留 3% 的行"并不省内存——除非你把它写出去然后释放原表。连续查询时池峰值 ≈ 单次峰值 × 并发数，这就是为什么 Arrow 服务端要把 `memory_pool` 做成请求级的父子池，而不是全局一个。

## 7. 代码：把布局打印出来

```bash
cd E:\work\learning\market-lake
uv run python py/w04_arrow_memory.py      # 全流程约 3 秒
cat reports/w04-arrow.md
```

脚本做七组实验，最关键的是 E1——**同一个 587,000 行数据集，三种读法**：

| 路径 | 耗时 | Arrow 分配 |
|---|---|---|
| 行式 40 B 记录 → 按 stride 转置 | 11.2 ms | 19.03 MB |
| 列式布局 → `from_buffers` | **1.4 ms** | **0.00 MB** |
| Arrow CSV reader | 10.8 ms | 20.16 MB |
| pandas `read_csv` | 28.6 ms | — |

磁盘体积：行式 22.4 MB、列式 20.2 MB、CSV 24.5 MB。**行式和列式只差 2 MB，耗时差 8 倍、分配差 19 MB**——差的是"要不要转置"，不是"要不要解析"。

自己验证零拷贝，只需两行：

```python
seg = np.memmap("lake/data/exp04/feed_col.bin", dtype=np.uint8, mode="r")[0:8*n]
arr = pa.Array.from_buffers(pa.int64(), n, [None, pa.py_buffer(seg)])
print(seg.ctypes.data == arr.buffers()[1].address)   # True
```

`True` 的含义是：文件映射、numpy 视图、Arrow 数组三者是**同一段虚拟内存**。没有任何一方拥有它的独占副本——包括文件本身。

## 8. 踩坑

| 坑 | 表现 | 正确做法 |
|---|---|---|
| 以为"二进制就快" | 行式 bin 11.2 ms，比 CSV 还慢一点 | 快的是**列式 + 零拷贝**；行式流一样要转置 |
| 以为 `slice` 不占内存 | 2,000 个小切片账面 15 MB，实际拖住 76 MB | 长期持有小片段时复制一份，或按需重读 |
| 拿字典编码 `ts` | 6.7 MB vs 4.5 MB，反而更大 | 唯一值占比 >50% 的列不要用字典 |
| 以为 `concat` 会复制 | 其实 9 µs、地址全保留 | 但 chunk 数会涨；后续算子要按 chunk 处理 |
| 以为 validity 一定存在 | 无 null 时是 `None` | 判断 `arr.null_count == 0` 走快路径 |
| 以为 buffer 一定 64 字节对齐 | 实测 mod 64 只有 1/3 为 0 | SIMD 代码按运行时对齐分派 |
| 字符串列存 symbol | 13.1 B/行 + 2 GB 上限 | 字典数组或整数 ID |
| 把跨步 numpy 数组当零拷贝 | 跨步数组会逐元素拷贝 | 只有连续（C-contiguous）内存才能 `from_buffers` |

## 9. 出处

- Apache Arrow, *Columnar Format Specification* — **Physical Memory Layout**（数组 = length + null count + buffers）、**Validity Bitmaps**（LSB 优先）、**Variable-size Binary Layout**（offsets 为 `int32`，`n+1` 个）、**Dictionary-encoded Layout**
- Apache Arrow, 同上 — **Buffer Alignment and Padding**：要求 8 字节对齐、建议 64 字节[^3]
- Arrow C++ `arrow/array.h` / `arrow/buffer.h`（随 pyarrow 26.0.1 分发）— `ArrayData` 的 `buffers` / `offset` / `null_count` 字段
- 本章数据：`reports/w04-arrow.md`（脚本 `py/w04_arrow_memory.py`，2026-11-11，pyarrow 26.0.1 / numpy 2.5.4）

[^1]: Arrow 规范 *Validity Bitmaps*：bitmap 的第 i 位对应数组第 i 个元素，最低位在前。本节用 `0x7E` 实测复核：第 0 位与第 7 位为 0。
[^2]: 规范 *Variable-size Binary Layout*：`offsets` 为 32 位有符号整数，故单数组 data 上限 2 GiB；`LargeBinary` / `LargeString` 改用 64 位 offsets。注意：2 GiB 是**单个数组**上限，不是单个文件上限。
[^3]: 规范原文的措辞是"recommend 64-byte alignment and padding"，硬性要求是 8 字节。本机 pyarrow 26.0.1 + mimalloc 实测 150 个数组，`addr % 64` 分布为 `{0: 50, 16: 50, 32: 25, 48: 25}`——16 字节对齐有保证，64 没有。⚠️ 不同分配器（jemalloc / 系统 malloc）结果会不同，别把本机分布当规范。

---

**下一步**：第 5 章把这块内存"寄"到另一个进程——IPC 为什么比 JSON 省一个量级、Flight 的 Ticket 传了什么、C Data Interface 的 `release` 回调由谁负责。
