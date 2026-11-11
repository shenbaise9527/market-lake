# 第 3 章 · 写入器：五个旋钮与一张矩阵

!!! abstract "本章结论（读完你应该能复述这三句）"
    1. 真正需要调的只有五个旋钮，其中 **排序键最贵也最有效**——但它没有"最优值"：同一份数据按 `ts` 到达顺序时，时间窗查询读放大 3.4×；按 `(symbol, ts)` 聚簇后反而 12.2×。它只对**对齐的那一类谓词**生效。
    2. `row_group_size` 是**点查粒度**旋钮，不是压缩率旋钮：32,768 → 2,097,152 行让点查需读字节从 0.41 MB 涨到 3.66 MB（8.9×）、footer 缩小 9.3×，而文件体积只差 **0.6%**。
    3. 编码和压缩**互相替代**：全 PLAIN 档靠 zstd 从 30.4 MB 压到 5.8 MB（5.2×），全字典档只从 11.4 MB 压到 7.8 MB（1.5×）——编码选对之后（3.5 MB），压缩能拿的只剩 11%。

对应任务卡：仓库内 `docs/week03.md` ｜ 本章实验脚本：`py/w03_writer.py` ｜ 原始结果：`reports/w03-writer.md` ｜ 页级探测：`rust/src/pages.rs`

---

## 1. 先看"用默认参数"能差多少

同一份数据（587,000 行 × 7 列，8 个合约 × 2 天，全市场按 `ts` 交错到达），三种写法：

```bash
uv run python py/w03_writer.py     # E0 组：只改写法，不改数据
```

| 写法 | 体积 | 字节/行 | 写耗时 |
|---|---|---|---|
| pyarrow 默认（`snappy` + 全列字典） | 7,427.1 KB | 12.96 | 86 ms |
| Arrow C++ / parquet-rs 默认（**不压缩** + 全列字典） | 8,417.8 KB | 14.68 | 77 ms |
| 调优后（`zstd-3` + 整数 DELTA + 行组 131,072） | **3,543.3 KB** | **6.18** | **67 ms** |

两件事值得先记住：

**① "默认"不是同一个东西。** pyarrow 把 `compression` 的默认值改成了 `snappy`，而它底下的 Arrow C++ 库、以及 Rust 的 parquet-rs，默认都是**不压缩**（`Compression::UNCOMPRESSED`）[^1]。同一句"按默认参数写"，换个语言体积差 13%（7,427 vs 8,418 KB）。跨语言比对文件大小时，先确认对方用的什么默认值。

**② 调优那份写得还更快**（67 ms vs 86 ms）。因为给 `ts` 关掉字典、换成 DELTA，省掉的是"为 587,000 个几乎唯一的时间戳建一张字典"的开销。**省空间和省 CPU 在这件事上不冲突**——只有当你把 `ts` 从字典换成 `PLAIN` 时才会冲突（见 §2 的 E1b）。

这五个旋钮各自管哪一层，一张图说完：

<figure class="diagram">
<svg width="100%" viewBox="0 0 680 172" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
<title>写入路径与五个旋钮的作用位置</title>
<desc>数据从内存表经过排序、行组切分、页切分与编码压缩，最后写成带 footer 的文件；五个旋钮分别作用在四个阶段上，代价各不相同。</desc>
<text x="0" y="14" font-size="13.2" font-weight="500" fill="currentColor">一次写入的四个阶段，五个旋钮</text>
<g stroke="currentColor" stroke-opacity="0.35" fill="none">
<rect x="0" y="28" width="148" height="52"/>
<rect x="172" y="28" width="148" height="52"/>
<rect x="344" y="28" width="148" height="52"/>
<rect x="516" y="28" width="148" height="52"/>
</g>
<g font-size="10.5" text-anchor="middle" fill="currentColor">
<text x="74" y="48" font-weight="500">Arrow Table</text>
<text x="74" y="66" fill-opacity="0.6">587,000 行 × 7 列</text>
<text x="246" y="48" font-weight="500">row group × 18</text>
<text x="246" y="66" fill-opacity="0.6">每 32,768 行</text>
<text x="418" y="48" font-weight="500">page × 30 / 列块</text>
<text x="418" y="66" fill-opacity="0.6">约 20,000 行一页</text>
<text x="590" y="48" font-weight="500">PAR1 … footer</text>
<text x="590" y="66" fill-opacity="0.6">1,522 B</text>
</g>
<g stroke="currentColor" stroke-opacity="0.45" fill="none">
<path d="M150 54 h16"/><path d="M322 54 h16"/><path d="M494 54 h16"/>
</g>
<g fill="currentColor" fill-opacity="0.55">
<path d="M166 54 l-7 -3.5 l0 7 z"/><path d="M338 54 l-7 -3.5 l0 7 z"/><path d="M510 54 l-7 -3.5 l0 7 z"/>
</g>
<g font-size="10" text-anchor="middle" fill="currentColor">
<text x="74" y="100" font-weight="500">① 排序键</text>
<text x="74" y="115" fill-opacity="0.65">改数据本身，不是参数</text>
<text x="246" y="100" font-weight="500">② row_group_size</text>
<text x="246" y="115" fill-opacity="0.65">一次最少读多少行</text>
<text x="418" y="100" font-weight="500">③ 编码　④ 压缩</text>
<text x="418" y="115" fill-opacity="0.65">每个值占几字节</text>
<text x="590" y="100" font-weight="500">⑤ page index</text>
<text x="590" y="115" fill-opacity="0.65">行组内能否再跳页</text>
</g>
<line x1="0" y1="132" x2="680" y2="132" stroke="currentColor" stroke-opacity="0.15"/>
<text x="0" y="150" font-size="11.5" fill="currentColor" fill-opacity="0.85">① 最贵：要重排全部数据 + 重写文件；② 只改切分；③④ 只花 CPU；⑤ 几乎免费（+0.5 KB）。</text>
<text x="0" y="168" font-size="11.5" fill="currentColor" fill-opacity="0.85">顺序建议：先定 ①（决定下游一切），再定 ②（决定点查粒度），最后调 ③④⑤。</text>
</svg>
</figure>

写文件的顺序本身就是一条流水线，旋钮的位置决定了它能改什么、不能改什么。下面四节按"代价从高到低"排：排序（§2）→ 行组（§3）→ 压缩（§4）→ 页（§5）。

## 2. 排序键：只对齐的那一类谓词受益

"排序能提高压缩率"这句话对，但漏了一半：**它同时改变剪枝效果，而这两件事经常指向相反的排序键。**

先把"剪枝效果"量化。行组级剪枝只看 footer 里的 `min`/`max`：一个行组能不能跳过，取决于它的统计区间与谓词区间是否相交。所以定义一个不受缓存噪声影响的指标：

```text
读放大 = (命中行组字节 / 全部行组字节) ÷ 谓词选择率
```

`=1` 表示剪枝正好压到理论下限（选择率），`>1` 表示白读了不该读的字节。`选择率 = 匹配行数 / 总行数`，是任何剪枝都不可能突破的地板。

三个查询形状，选择率都控制在 5% 上下：

| 查询 | 条件 | 选择率 |
|---|---|---|
| Q1 | `ts` 落在 15 分钟窗口内（跨全部合约） | 3.20% |
| Q2 | `symbol = '002322.SZ'`（全时段） | 25.00% |
| Q3 | 单合约 + 1 分钟窗口 | 0.06% |

同一份数据写两遍（`rg=32,768`、整数 DELTA），只改行序：

<figure class="diagram">
<svg width="100%" viewBox="0 0 680 203" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
<title>排序键与查询形状的读放大矩阵</title>
<desc>按到达顺序存放时时间窗查询读放大 3.4 倍、单合约查询 4 倍；按合约聚簇后单合约查询降到 1.1 倍，但时间窗查询升到 12.2 倍。细粒度查询两种行序都差两个数量级。</desc>
<text x="0" y="14" font-size="13.2" font-weight="500" fill="currentColor">点查需读字节（上）与读放大（下），只改行序</text>
<g font-size="10.5" text-anchor="middle" fill="currentColor">
<text x="205" y="34" font-weight="500">Q1 时间窗 15min</text>
<text x="205" y="49" fill-opacity="0.6">选择率 3.20%</text>
<text x="389" y="34" font-weight="500">Q2 单合约全时段</text>
<text x="389" y="49" fill-opacity="0.6">选择率 25.0%</text>
<text x="573" y="34" font-weight="500">Q3 单合约 + 1min</text>
<text x="573" y="49" fill-opacity="0.6">选择率 0.06%</text>
</g>
<g>
<rect x="114" y="58" width="182" height="54" fill="#EF9F27" fill-opacity="0.16"/>
<rect x="298" y="58" width="182" height="54" fill="#EF9F27" fill-opacity="0.16"/>
<rect x="482" y="58" width="182" height="54" fill="#EF9F27" fill-opacity="0.5"/>
<rect x="114" y="118" width="182" height="54" fill="#EF9F27" fill-opacity="0.45"/>
<rect x="298" y="118" width="182" height="54" fill="#1D9E75" fill-opacity="0.3"/>
<rect x="482" y="118" width="182" height="54" fill="#EF9F27" fill-opacity="0.5"/>
</g>
<g font-size="15" text-anchor="middle" fill="currentColor">
<text x="205" y="82">0.49 MB</text><text x="389" y="82">4.43 MB</text><text x="573" y="82">0.24 MB</text>
<text x="205" y="142">1.42 MB</text><text x="389" y="142">1.00 MB</text><text x="573" y="142">0.41 MB</text>
</g>
<g font-size="10.5" text-anchor="middle" fill="currentColor" fill-opacity="0.8">
<text x="205" y="100">读放大 3.4×</text><text x="389" y="100">4.0×</text><text x="573" y="100">94.9×</text>
<text x="205" y="160">12.2×</text><text x="389" y="160">1.1×</text><text x="573" y="160">197.5×</text>
</g>
<g font-size="10.5" fill="currentColor" text-anchor="end">
<text x="104" y="80" font-weight="500">到达顺序</text><text x="104" y="95" fill-opacity="0.6">ts 交错</text>
<text x="104" y="140" font-weight="500">(symbol, ts)</text><text x="104" y="155" fill-opacity="0.6">按合约聚簇</text>
</g>
<line x1="0" y1="182" x2="680" y2="182" stroke="currentColor" stroke-opacity="0.15"/>
<text x="0" y="199" font-size="11" fill="currentColor" fill-opacity="0.8">绿色 = 已接近下限；橙越深 = 白读越多。Q3 两边都差两个数量级，因为行组粒度远大于匹配行数。</text>
</svg>
</figure>

这张图是本章最该记住的一张，因为它推翻了一个流行结论：

**排序键没有"最优值"，只有"对齐的谓词"。** 按 `(symbol, ts)` 聚簇让 Q2 从 4.0× 降到 1.1×（几乎完美），代价是 Q1 从 3.4× 升到 12.2×——因为每个合约的时间区间互相不重叠，一个合约要跨 5 个行组，时间窗查询就得同时读 7 个行组。反过来，按 `ts` 交错存放时，`symbol` 列的 `min/max` 覆盖全部 8 个合约，**剪枝完全失效**（18/18 行组全读）。

Q3 那两格（94.9× / 197.5×）说明第三件事：**行组粒度是硬地板。** 一分钟单合约只有 306 行匹配，而一个行组 32,768 行——即使剪枝完美，读放大也在 100× 量级。这不是排序能解决的问题，只能靠更小的行组、分区（第 8 章）或页级剪枝（§5）。

那么排序换来的压缩收益呢？固定 `(symbol, ts)`、`rg=131,072`，只换编码档：

| 编码档 | 体积 | 写耗时 | footer | 点查需读 |
|---|---|---|---|---|
| 全列字典（pyarrow 默认思路） | 7,792.6 KB | 115 ms | 4,666 B | 1.80 MB |
| **整数 DELTA + 低基数列留字典** | **3,543.3 KB** | **67 ms** | 4,501 B | 0.82 MB |
| DELTA + `px` 走 BYTE_STREAM_SPLIT | 5,205.7 KB | 61 ms | 4,459 B | 1.24 MB |
| 全关字典（全 PLAIN + DELTA） | 5,902.8 KB | 64 ms | 4,230 B | 1.30 MB |

分列看这两份文件（`rg=32,768`，同为 DELTA 档，只差行序）：

| 列 | 到达顺序 | `(symbol, ts)` 聚簇 | 变化 |
|---|---|---|---|
| `ts` | 2,041.2 KB | 2,187.5 KB | **+7%（变差）** |
| `seq` | 641.0 KB | 14.2 KB | −98% |
| `px` | 899.8 KB | 745.8 KB | −17% |
| `symbol` | 148.0 KB | 3.1 KB | −98% |
| 合计 | 4,560.7 KB | 3,781.3 KB | **−17%** |

两个反直觉的点：

**① `ts` 反而变大了 7%。** 到达顺序下 `ts` 全局单调，DELTA 拿到的是一路平滑的差值；按合约聚簇后，每换一个合约 `ts` 就要"重启"一次，行组内出现 8 段不连续。**排序键改善的是"它自己那列"，可能顺手弄坏别的列。**

**② 字典编码对行序不敏感。** 全字典档 7,792.6 KB 里，`ts` 占 4,939.1 KB、`seq` 占 1,510.0 KB——字典编码的是"值集合 + 索引"，与相邻性无关，所以排序在它身上一分钱也挣不到。这就是为什么"先换编码、再谈排序"经常没效果：**你调的旋钮对这类数据根本不敏感**。

一句话总结这一节：**排序是"选择哪一维做主键"的决定，不是"优化"按钮。** 时间序列的主谓词通常是时间，所以"文件内按 `ts` 排序 + 按 `symbol` 分区"几乎总是对的——那需要把两个维度拆到两个层级，第 8 章讲。

## 3. 行组大小：拿 metadata 换粒度

行组是剪枝的最小单位，所以它直接决定了"一次最少读多少行"。固定其余参数，只扫 `row_group_size`：

| 行组行数 | 行组数 | 体积 | footer | 点查需读 | 点查耗时 |
|---|---|---|---|---|---|
| 32,768 | 18 | 3,553.5 KB | 14,145 B | 0.41 MB | 2.54 ms |
| 131,072 | 5 | **3,543.3 KB** | 4,501 B | 0.82 MB | 3.37 ms |
| 524,288 | 2 | 3,562.5 KB | 2,270 B | 3.26 MB | 4.98 ms |
| 2,097,152 | 1 | 3,573.2 KB | **1,522 B** | **3.66 MB** | 5.82 ms |

<figure class="diagram">
<svg width="100%" viewBox="0 0 680 212" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
<title>行组大小对三个指标的不同影响</title>
<desc>行组从三万二千行增到二百零九万行，点查需读字节涨八点九倍、footer 缩小九点三倍，但文件体积几乎不变（零点六个百分点）。</desc>
<text x="0" y="14" font-size="13.2" font-weight="500" fill="currentColor">同一份数据，只改 row_group_size</text>
<g font-size="11" text-anchor="middle" fill="currentColor" fill-opacity="0.85">
<text x="100" y="34" font-weight="500">点查需读（MB）</text>
<text x="340" y="34" font-weight="500">footer（KB）</text>
<text x="580" y="34" font-weight="500">文件体积（MB）</text>
</g>
<g stroke="currentColor" stroke-opacity="0.2">
<line x1="0" y1="150" x2="200" y2="150"/><line x1="240" y1="150" x2="440" y2="150"/><line x1="480" y1="150" x2="680" y2="150"/>
</g>
<g fill="#EF9F27" fill-opacity="0.55">
<rect x="8" y="139" width="34" height="11"/>
<rect x="56" y="127" width="34" height="23"/>
<rect x="104" y="60" width="34" height="90"/>
<rect x="152" y="49" width="34" height="101"/>
</g>
<g fill="#378ADD" fill-opacity="0.5">
<rect x="248" y="47" width="34" height="103"/>
<rect x="296" y="117" width="34" height="33"/>
<rect x="344" y="133" width="34" height="17"/>
<rect x="392" y="139" width="34" height="11"/>
</g>
<g fill="currentColor" fill-opacity="0.28">
<rect x="488" y="52" width="34" height="98"/>
<rect x="536" y="53" width="34" height="97"/>
<rect x="584" y="52" width="34" height="98"/>
<rect x="632" y="52" width="34" height="98"/>
</g>
<g font-size="9.5" text-anchor="middle" fill="currentColor">
<text x="25" y="134">0.41</text><text x="73" y="122">0.82</text><text x="121" y="55">3.26</text><text x="169" y="44">3.66</text>
<text x="265" y="42">14.1</text><text x="313" y="112">4.5</text><text x="361" y="128">2.3</text><text x="409" y="134">1.5</text>
<text x="505" y="47">3.55</text><text x="553" y="48">3.54</text><text x="601" y="47">3.56</text><text x="649" y="47">3.57</text>
</g>
<g font-size="9.5" text-anchor="middle" fill="currentColor" fill-opacity="0.65">
<text x="25" y="166">32k</text><text x="73" y="166">128k</text><text x="121" y="166">512k</text><text x="169" y="166">2M</text>
<text x="265" y="166">32k</text><text x="313" y="166">128k</text><text x="361" y="166">512k</text><text x="409" y="166">2M</text>
<text x="505" y="166">32k</text><text x="553" y="166">128k</text><text x="601" y="166">512k</text><text x="649" y="166">2M</text>
</g>
<g font-size="10" text-anchor="middle" fill="currentColor" fill-opacity="0.75">
<text x="100" y="182">×8.9</text><text x="340" y="182">÷9.3</text><text x="580" y="182">+0.6%</text>
</g>
<line x1="0" y1="192" x2="680" y2="192" stroke="currentColor" stroke-opacity="0.15"/>
<text x="0" y="208" font-size="11" fill="currentColor" fill-opacity="0.8">三块面板同为 0 起点：体积那一栏几乎是一条水平线——行组大小不是压缩率旋钮。</text>
</svg>
</figure>

只有中间那块（footer）和左边那块（需读字节）在动，右边那块几乎不动。原因在 §4 会讲透：**压缩的单位是页，不是行组。** 你把两个 page 拼进同一个行组，不会让它们压得更小，只会让 footer 少记一条、让剪枝粒度变粗。

工程上的经验式：

- **行组行数 ≈ 你能接受的"最小读取量"**。tick 数据 32,768 行 ≈ 0.2 MB，点查 2.5 ms；2,097,152 行 ≈ 3.5 MB，点查 5.8 ms。差 3 ms 听上去不多，但那是**每次查询都要付**的固定成本。
- **footer 是 O(行组数 × 列数)**。18 个行组只有 14 KB，200 列 × 2000 个行组就到了几十 MB 量级——这时读 footer 本身就变成一次 IO。
- 默认的 1,048,576 行适合"整表扫描为主"的分析型负载；**行情这种点查 + 时间窗混合负载，常见甜点是 64k–256k 行**（本例 128k 行组已接近最小体积）。

## 4. 压缩：先看编码留下了多少冗余

`zstd-3` 固定，只换压缩算法（同为 DELTA 编码、128k 行组）：

| 压缩 | 体积 | 字节/行 | 写耗时 | 全扫 |
|---|---|---|---|---|
| `none` | 3,964.0 KB | 6.91 | 102 ms | 2.95 ms |
| `snappy` | 3,830.0 KB | 6.68 | 103 ms | 3.16 ms |
| `lz4` | 3,811.2 KB | 6.65 | 104 ms | 3.08 ms |
| `zstd-1` | 3,543.3 KB | 6.18 | 108 ms | 3.08 ms |
| `zstd-3` | **3,536.9 KB** | 6.17 | 100 ms | 3.19 ms |
| `zstd-9` | 3,501.1 KB | 6.11 | 156 ms | 3.36 ms |
| `gzip-6` | 3,476.4 KB | 6.06 | 242 ms | 4.22 ms |

注意第一行：**在这个编码档下，不压缩也只比 zstd-3 大 11%。** 这和"压缩能省 5 倍"的直觉冲突，因为冗余已经被 DELTA 和字典吃掉了。换编码档再看一次：

<figure class="diagram">
<svg width="100%" viewBox="0 0 680 203" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
<title>编码与压缩的替代关系</title>
<desc>全 PLAIN 档靠 zstd 从三十点四兆压到五点八兆，收益五点二倍；全字典档只从十一点四兆压到七点八兆，收益一点五倍；DELTA 档几乎没有压缩空间，只有一点一倍。</desc>
<text x="0" y="14" font-size="13.2" font-weight="500" fill="currentColor">同一份数据：编码决定"还剩多少可压"</text>
<g font-size="10.5" text-anchor="middle" fill="currentColor" fill-opacity="0.75">
<text x="205" y="34">不压缩</text><text x="415" y="34">zstd-3</text><text x="565" y="34">压缩买到</text>
</g>
<g font-size="10.5" fill="currentColor" text-anchor="end">
<text x="98" y="52" font-weight="500">全字典</text><text x="98" y="66" fill-opacity="0.6">pyarrow 默认</text>
<text x="98" y="104" font-weight="500">DELTA + 字典</text><text x="98" y="118" fill-opacity="0.6">本章调优档</text>
<text x="98" y="156" font-weight="500">全 PLAIN</text><text x="98" y="170" fill-opacity="0.6">最差写法</text>
</g>
<g>
<rect x="110" y="36" width="190" height="40" fill="#EF9F27" fill-opacity="0.1"/>
<rect x="320" y="36" width="190" height="40" fill="#EF9F27" fill-opacity="0.1"/>
<rect x="110" y="88" width="190" height="40" fill="#1D9E75" fill-opacity="0.2"/>
<rect x="320" y="88" width="190" height="40" fill="#1D9E75" fill-opacity="0.2"/>
<rect x="110" y="140" width="190" height="40" fill="#EF9F27" fill-opacity="0.55"/>
<rect x="320" y="140" width="190" height="40" fill="#1D9E75" fill-opacity="0.28"/>
</g>
<g font-size="13" text-anchor="middle" fill="currentColor">
<text x="205" y="61">11,386 KB</text><text x="415" y="61">7,793 KB</text>
<text x="205" y="113">3,964 KB</text><text x="415" y="113">3,537 KB</text>
<text x="205" y="165">30,401 KB</text><text x="415" y="165">5,903 KB</text>
</g>
<g font-size="11.5" text-anchor="middle" fill="currentColor" fill-opacity="0.85">
<text x="565" y="61">1.46×</text><text x="565" y="113">1.12×</text><text x="565" y="165" font-weight="500">5.15×</text>
</g>
<line x1="0" y1="184" x2="680" y2="184" stroke="currentColor" stroke-opacity="0.15"/>
<text x="0" y="199" font-size="11" fill="currentColor" fill-opacity="0.8">编码越好，压缩能挣的越少——两者不是叠加，是替代。先选编码，再决定值不值得为压缩付 CPU。</text>
</svg>
</figure>

这张图是本章第二个反直觉结论：**"压缩比 5 倍"是编码偷懒的代价，不是压缩的功劳。** 全 PLAIN 档 30.4 MB，一个 zstd 就砍到 5.9 MB；但只要把 `ts`/`seq` 换成 DELTA、其余留住字典，不压缩也只有 3,964 KB——比"PLAIN + zstd"还小 33%。

顺带一个写侧数字：全 PLAIN 档写最快（34.5 ms），因为它什么都不算；全字典 + zstd 最慢（185 ms），因为要为 587,000 个时间戳建字典再压。**CPU 花在编码上还是压缩上，体积收益差不多，但编码通常更便宜**——这是"先编码后压缩"的第二个理由。

读侧呢？全扫耗时全在 2.95–4.22 ms 之间，看不出差别。因为 3.5 MB 的文件整份都在 OS 页缓存里，解压成本被 CPU 流水线吃掉了。**压缩省的是磁盘带宽和网络带宽，不是延迟**——只有在冷读、对象存储、跨机房拉数据时，这 11%（或 5.2×）才变成真实的时间。

选型规则（本机实测支撑，不是道听途说）：

| 场景 | 选 | 理由 |
|---|---|---|
| 热数据、反复扫 | `snappy` / `zstd-1` | 体积已接近最优，写读都便宜 |
| 冷数据、长期归档 | `zstd-9` | 比 `zstd-3` 小 1.0%，写慢 1.56×——只在写一次读多次时划算 |
| 交换/兼容优先 | `gzip` | 通用性最好，写慢 2.4×，体积只小 1.7% |
| 临时文件、中间结果 | `none` | 反正马上删；但别写进数据湖 |

## 5. 页与 page index：免费，但要看读端

`data_page_size` 在 Arrow C++ 里叫 `data_pagesize`，默认 1 MiB。但它**只是上限**：还有一个 `max_rows_per_page`（默认 20,000 行）[^2]。本例 587,000 行，无论你把页设成 1 MiB 还是 256 KiB，实际都是 **30 页**——因为每页先被 20,000 行截断（587,000 ÷ 20,000 ≈ 29.4）。

| 页大小 | page index | 文件体积 | 写耗时 | 点查耗时 |
|---|---|---|---|---|
| 1,024 KB | 关 | 3,659,008 B | 116 ms | 6.63 ms |
| 1,024 KB | 开 | 3,658,520 B | 106 ms | 6.03 ms |
| 256 KB | 关 | 3,659,008 B | 118 ms | 5.83 ms |
| 256 KB | 开 | 3,658,520 B | 105 ms | 6.23 ms |
| 64 KB | 关 | 3,655,472 B | 112 ms | 5.82 ms |
| 64 KB | 开 | 3,654,968 B | 98 ms | 6.02 ms |

体积差 0.5 KB（0.01%），耗时差全在噪声里（±0.5 ms，本机重复运行的波动就有这么大）。**所以"开不开 page index"在这里测不出收益**——但这不代表它没用，而是说明**读端没吃它**。

行组内的页级统计存在 column index 里，页的字节位置存在 offset index 里。用 Rust 的 parquet-rs 读出来，能算清"如果按页剪枝，要读多少"：

```bash
cd rust && cargo run -q --release -- pages \
  ../lake/data/exp03/e4-1048576-1.parquet --column ts \
  --lo 1793842200005459260 --hi 1793842260005459260
```

```text
── 行组 #0  行数=587000  页数=30
  页  行区间起       min                 max                 字节   命中
    0          0  1793755800277983700  1793759340284749717    70072  ×
    3      55296  1793772132712328621  1793842257698502613    70219  ✓
    4      73728  1793842257748588524  1793845804186800678    70084  ✓
  ...
  行组内命中 7/30 页

合计：命中 7/30 页，需读 0.54 MB / 列块 2.24 MB（23.9%）
```

同一份数据、同一个查询：行组级剪枝要读 **3.66 MB**（1 个行组全读），页级剪枝只需 **0.54 MB**，理论下限是 0.003 MB。**这就是"参数有没有用取决于读端"的具体形态**：DuckDB 2.0.0 在本机没表现出页级剪枝收益（6.6 ms vs 行组切分后的 2.5 ms，说明它读的还是整块），parquet-rs 能算出来，但要不要用是调用方的决定[^3]。

实践建议：

- **page index 几乎不要钱（+0.5 KB），默认打开**。Arrow C++ 的 `enable_write_page_index()` 默认就是开的，但 pyarrow 的 `write_page_index` 默认是 `False`——又一个"默认值不一样"（见 §6）。
- **别为了 page index 去调小 `data_page_size`**：页数受 `max_rows_per_page` 支配，改页大小往往只是改 page header 数量，`64 KB` 档反而比 `1 MiB` 档略大（3,655,472 vs 3,659,008 B 是唯一例外，噪声级）。
- **真要吃页级收益，得自己写读端**，或者用明确声明支持页级跳过的引擎——判断方法就是看它的扫描算子有没有读 column index。

## 6. 三语言写法对照

参数语义一样，名字全不一样，默认值还有三处不一致。下表按本地源码逐条核对（不是凭记忆）：

| 语义 | pyarrow `pq.write_table` | Arrow C++ `WriterProperties::Builder` | parquet-rs `WriterProperties::builder()` |
|---|---|---|---|
| 行组行数 | `row_group_size`（默认 `None` → C++ 默认 1,048,576） | `max_row_group_length`（1,048,576） | `set_max_row_group_row_count(Some(n))`；另有 `set_max_row_group_bytes` |
| 页大小上限 | `data_page_size`（`None` → 1 MiB） | `data_pagesize`（1 MiB） | `set_data_page_size_limit`（1 MiB） |
| 每页行数上限 | 不暴露（跟随 C++ 的 20,000） | `max_rows_per_page`（20,000） | `set_data_page_row_count_limit`（20,000） |
| 写批次 | `write_batch_size`（`None` → 1024） | `write_batch_size`（1024） | `set_write_batch_size`（1024） |
| 字典开关 | `use_dictionary=True / ["px"]` | `enable_dictionary()` / `disable_dictionary()` | `set_dictionary_enabled` / `set_column_dictionary_enabled` |
| 字典页上限 | `dictionary_pagesize_limit`（`None` → 1 MiB） | `dictionary_pagesize_limit`（1 MiB） | `set_dictionary_page_size_limit`（1 MiB） |
| 列编码 | `column_encoding={"px": "BYTE_STREAM_SPLIT"}` | `encoding(path, Encoding::BYTE_STREAM_SPLIT)` | `set_column_encoding(ColumnPath::from("px"), Encoding::BYTE_STREAM_SPLIT)` |
| 压缩 | **`compression="snappy"`** | `compression(...)`，**默认 UNCOMPRESSED** | `set_compression(...)`，**默认 UNCOMPRESSED** |
| 统计级别 | `write_statistics=True`（列块级） | `enable_statistics()`（默认 true） | `set_statistics_enabled(EnabledStatistics::Page)`（**默认页级**） |
| page index | **`write_page_index=False`** | `enable_write_page_index()`（**默认 true**） | 默认写 offset index（`set_offset_index_disabled(false)`） |
| 格式版本 | `version="2.6"`, `data_page_version="1.0"` | `version(ParquetVersion::PARQUET_2_6)` | `set_writer_version(WriterVersion::PARQUET_1_0 / 2_0)`，**没有 2.6** |
| 声明排序键 | `sorting_columns` | `set_sorting_columns` | `set_sorting_columns` |
| `created_by` | 不暴露 | `created_by` | `set_created_by` |

三处默认值不一致，每一处都会咬人：

1. **压缩**：pyarrow 默认 `snappy`，C++/Rust 默认不压缩。跨语言比对体积前先对齐。
2. **page index**：C++ 默认写，pyarrow 传 `False`。用 pyarrow 写的文件默认没有页级统计。
3. **统计级别**：parquet-rs 默认写到页（`EnabledStatistics::Page`），C++/pyarrow 是列块级。想省 footer 体积时可以往下调。

还有一处不是"默认值"而是**能力差异**：parquet-rs 的 `WriterVersion` 只有 `PARQUET_1_0` 和 `PARQUET_2_0`，没有 `2.6`。`2.6` 是格式版本（逻辑类型、`nan_count` 等），由 parquet-format 定义，Rust 实现内部按需写最新结构，所以没有这个枚举——**别照抄 Python 的 `version="2.6"` 去找对应 API。**

## 7. 代码：把这套矩阵跑一遍

```bash
cd market-lake

# 全矩阵（E0/E1a/E1b/E2/E3/E3b/E4，约 1 分钟）
uv run python py/w03_writer.py

# 只看结果
cat reports/w03-writer.md

# 页级探测（需要先编译一次）
cd rust && cargo build --release
cargo run -q --release -- pages ../lake/data/exp03/e4-1048576-1.parquet \
  --column ts --lo 1793842200005459260 --hi 1793842260005459260
```

脚本里有两处刻意的设计，值得抄走：

**① 用 footer 统计算"需读字节"，而不是用时延。** 时延受页缓存、CPU 频率、后台进程影响，本机重复运行能差 50%；而 `min/max` 相交判断完全是确定性的——同一份文件跑一百次都是同一个数。**先让指标稳定，再谈结论。**

**② 每次都重写文件，不复用旧文件。** 编码档、行组、压缩三组实验互相独立，复用会串味；`lake/data/exp03/` 下的 24 个文件总共不到 200 MB，不值得为省磁盘引入 bug。

想验证自己读懂了，改一个地方再跑：把 `ENCODINGS["delta"]` 里的 `"vol"` 删掉，看体积从 3,543 KB 涨到多少——这是上一个坑的复现。

## 8. 踩坑

| 坑 | 表现 | 正确做法 |
|---|---|---|
| 以为"默认"是同一个 | pyarrow 默认 snappy、C++/Rust 默认不压缩，同句"默认"差 13% | 跨语言比对前先打印 `created_by` 与压缩算法 |
| 只给整数列上 DELTA | `use_dictionary=["symbol","px"]` 顺手把 `vol` 的字典关了，347 → 590 KB（+70%） | 显式列出**所有**要留字典的列；改编码后复查每列体积 |
| 把 `total_byte_size` 当"需读字节" | 它是**未压缩**大小[^4]，全 PLAIN 档上会夸大 5 倍 | 用 `sum(column.total_compressed_size)` |
| 以为行组越大压得越好 | 32k → 2M 行，体积只差 0.6% | 压缩单位是页；行组只管粒度与 footer |
| 以为 `data_page_size` 决定页数 | 设 1 MiB 和 256 KiB，都是 30 页 | 页数由 `max_rows_per_page`（默认 20,000）支配 |
| 开 page index 就期待变快 | DuckDB 2.0 上测不出差异 | 参数有没有用取决于读端；先确认读端实现了页级剪枝 |
| 只换编码不排序 | ch2 的乱序实验：`seq` 3.8 → 160.5 KB（42 倍退化） | 顺序敏感的编码，必须先修顺序 |
| 排序键选"看起来最合理"的那个 | 按 `(symbol, ts)` 聚簇让时间窗查询从 3.4× 恶化到 12.2× | 按主谓词选排序键；两个维度都重要就分区 |

## 9. 出处

- Apache Parquet, *File Format* — 行组 / 列块 / 页的层级，以及"每个页独立压缩"的语义
- Apache Parquet, *Page Index*（`PageIndex.md`）— column index 与 offset index 的布局，页级 `min`/`max`
- `parquet-format` 的 `parquet.thrift` — `RowGroup.total_byte_size` 定义为**未压缩**字节数
- parquet-rs 60.1.0 `src/file/properties.rs`（`DEFAULT_*` 常量、全部 `set_*`）、`src/file/metadata/reader.rs:573`（`read_page_indexes`）、`src/file/metadata/page_index.rs`（`column_index` / `page_locations`）
- Arrow C++ `parquet/properties.h`（`kDefaultDataPageSize`、`kDefaultMaxRowsPerPage`、`DEFAULT_COMPRESSION_TYPE`、`DEFAULT_IS_PAGE_INDEX_ENABLED`）
- 本章数据：`reports/w03-writer.md`（脚本 `py/w03_writer.py`，2026-11-11，pyarrow 26.0.1 / duckdb 2.0.0）

[^1]: 默认值以本地安装的源码为准：pyarrow `pq.write_table` 签名里 `compression='snappy'`；Arrow C++ 头文件 `parquet/properties.h` 中 `DEFAULT_COMPRESSION_TYPE = Compression::UNCOMPRESSED`；parquet-rs `src/file/properties.rs` 中 `DEFAULT_COMPRESSION = Compression::UNCOMPRESSED`（版本 60.1.0）。三者均于 2026-11-11 在本机核对。
[^2]: Arrow C++ `parquet/properties.h`：`kDefaultDataPageSize = 1024 * 1024`、`kDefaultMaxRowsPerPage = 20'000`；parquet-rs `properties.rs`：`DEFAULT_PAGE_SIZE = 1024*1024`、`DEFAULT_DATA_PAGE_ROW_COUNT_LIMIT = 20_000`。页数实测 587,000 ÷ 20,000 ≈ 29.4 → 30 页，与默认值一致。
[^3]: "DuckDB 2.0.0 未利用页级剪枝"是**本机观察结论**，不是官方文档陈述：单行组文件开启 page index 后点查仍为 6.0–6.2 ms，与关闭时无差；而把同一份数据切成 18 个行组后点查降到 2.5 ms。若要作为结论引用，请在自己环境复测。⚠️ 未核对 DuckDB 源码。
[^4]: parquet-rs `src/file/metadata/mod.rs:656` 的注释："Total byte size of all uncompressed column data in this row group."；对应 thrift 字段 `RowGroup.total_byte_size`。需读字节应改用各列块 `total_compressed_size` 之和。

---

**下一步**：第 4 章进入内存侧——Arrow 的 `RecordBatch` / `ChunkedArray` / validity bitmap 怎么组织，以及为什么"把 tick 数据直接建成 Arrow 结构"比"先 pandas 再转换"少两次拷贝。
