# 第 5 章 · IPC · Flight · 零拷贝的边界

!!! abstract "本章结论（读完你应该能复述这三句）"
    1. **IPC 不是"另一种 Parquet"**：它不压缩、不做编码，就是"把 Arrow 的 buffer 原样搬走 + 一份 schema"。同一份 587,000 行数据，IPC 26.87 MB、JSON 66.03 MB，而读回耗时 **2.3 ms vs 542.5 ms**——差 236 倍的不是带宽，是"解析"。
    2. 跨进程**仍然要拷贝**（走内核管道），省掉的是"把字节重新解释成结构"。实测父进程挂类型 4.4 ms / JSON 解析 547.6 ms（124×）——**零拷贝的边界是进程，不是机器**。
    3. Flight 在单次、本机、27 MB 的传输上**没有速度优势**（最小 31.0 ms vs IPC over HTTP 27.0 ms）。它的价值在重复调用、并行 endpoint、认证与背压，不在"更快"。

对应任务卡：仓库内 `docs/week05.md` ｜ 本章实验脚本：`py/w05_ipc.py` ｜ 原始结果：`reports/w05-ipc.md`

---

## 1. 一张图看清六种格式的取舍

同一张表（587,000 行 × 7 列，`ts` 排序），六种写法的体积与读回耗时：

<figure class="diagram">
<svg width="100%" viewBox="0 0 680 214" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
<title>六种序列化的体积与读回耗时</title>
<desc>JSON 六十六兆、CSV 三十兆、IPC 二十七兆、Parquet 与压缩 IPC 约七兆；读回耗时 JSON 五百多毫秒，IPC 仅二点三毫秒。</desc>
<text x="0" y="14" font-size="13.2" font-weight="500" fill="currentColor">同一份数据：体积（左）与读回耗时（右）</text>
<g font-size="10" fill="currentColor">
<text x="0" y="42" font-weight="500">JSON records</text><text x="0" y="68" font-weight="500">CSV</text>
<text x="0" y="94" font-weight="500">IPC stream</text><text x="0" y="120" font-weight="500">IPC file</text>
<text x="0" y="146" font-weight="500">IPC + zstd</text><text x="0" y="172" font-weight="500">Parquet zstd</text>
</g>
<g fill="#EF9F27" fill-opacity="0.55">
<rect x="108" y="32" width="420" height="13"/>
</g>
<g fill="currentColor" fill-opacity="0.22">
<rect x="108" y="58" width="188" height="13"/>
<rect x="108" y="84" width="171" height="13"/>
<rect x="108" y="110" width="171" height="13"/>
</g>
<g fill="#1D9E75" fill-opacity="0.45">
<rect x="108" y="136" width="48" height="13"/>
<rect x="108" y="162" width="47" height="13"/>
</g>
<g font-size="9.5" fill="currentColor" fill-opacity="0.85">
<text x="534" y="43">66.03 MB</text><text x="302" y="69">29.64 MB</text>
<text x="285" y="95">26.87 MB</text><text x="285" y="121">26.87 MB</text>
<text x="162" y="147">7.57 MB</text><text x="161" y="173">7.34 MB</text>
</g>
<g font-size="9.5" text-anchor="end" fill="currentColor" fill-opacity="0.7">
<text x="678" y="43">542.5 ms</text><text x="678" y="69">29.9 ms</text>
<text x="678" y="95">2.3 ms</text><text x="678" y="121">5.3 ms</text>
<text x="678" y="147">8.3 ms</text><text x="678" y="173">6.7 ms</text>
</g>
<line x1="0" y1="184" x2="680" y2="184" stroke="currentColor" stroke-opacity="0.15"/>
<text x="0" y="199" font-size="11" fill="currentColor" fill-opacity="0.8">JSON 大是因为键名重复（symbol 写了 587,000 次）；IPC 只比裸数据多 schema 与对齐填充；</text>
<text x="0" y="212" font-size="11" fill="currentColor" fill-opacity="0.8">Parquet 更小是因为它真的在压缩与编码——代价是读的时候要解码（6.7 ms vs 2.3 ms）。</text>
</svg>
</figure>

把六个数字排成表：

| 格式 | 体积 | 写 | 读回 | 相对 IPC | 性质 |
|---|---|---|---|---|---|
| JSON records | 66.03 MB | 463 ms | **542.5 ms** | 2.46× | 文本、键名重复、每个数字要 parse |
| CSV | 29.64 MB | 645 ms | 29.9 ms | 1.10× | 文本、无 schema、类型靠猜 |
| **IPC stream** | **26.87 MB** | **16 ms** | **2.3 ms** | 1.00× | 裸 buffer + schema，无编码 |
| IPC file | 26.87 MB | 10.5 ms | 5.3 ms | 1.00× | 同上 + footer（可随机取 batch） |
| IPC stream + zstd | 7.57 MB | 19 ms | 8.3 ms | 0.28× | 压一层，读时要解压 |
| Parquet zstd-3 | 7.34 MB | 127 ms | 6.7 ms | 0.27× | 编码 + 压缩，写最贵 |

三个反常识的点：

**① IPC 只比裸数据大一点点。** 7 列 × 587,000 行的裸字节是 20.2 MB（第 4 章实测），IPC 26.87 MB，多出来的 6.7 MB 是字符串列的 offsets + 对齐填充 + 每个 batch 的消息头。**IPC 的"开销"就是 Arrow 的内存布局本身**，没有额外编码层。

**② JSON 慢 236 倍不是带宽问题。** 66 MB 在两台机器之间传也就几十毫秒；慢的是 410 万个值逐个 `parse` 成数字。CSV 只慢 13 倍，因为 29.6 MB 且解析器是 C++ 写的。**格式的读取代价 ≈ 值个数 × 单值解析成本**。

**③ Parquet 比压缩 IPC 还小，但读更慢。** Parquet 要做字典解码、DELTA 解码、页解压；IPC+zstd 只需解压。**同一个"压缩比"，编码层的复杂度决定了读代价**。这也解释了为什么"热数据用 IPC、冷数据用 Parquet"是对的：前者换来 2.9 倍的读速度，代价是 3.7 倍体积。

## 2. IPC 的字节结构：stream 与 file 只差一个 footer

两种布局的差别比名字小得多，但决定了能不能随机访问：

<figure class="diagram">
<svg width="100%" viewBox="0 0 680 200" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
<title>IPC stream 与 file 的字节布局对比</title>
<desc>stream 以 continuation 标记开头，依次是 schema 消息与五个 batch，以 EOS 结束；file 以 ARROW1 magic 开头，末尾多一个 footer 与 footer 长度。</desc>
<text x="0" y="14" font-size="13.2" font-weight="500" fill="currentColor">stream：只能顺着读</text>
<g stroke="currentColor" stroke-opacity="0.4" fill="none">
<rect x="0" y="26" width="116" height="26"/>
<rect x="120" y="26" width="86" height="26"/><rect x="210" y="26" width="86" height="26"/>
<rect x="300" y="26" width="86" height="26"/><rect x="390" y="26" width="86" height="26"/>
<rect x="480" y="26" width="86" height="26"/><rect x="570" y="26" width="56" height="26"/>
</g>
<g font-size="9.5" text-anchor="middle" fill="currentColor" fill-opacity="0.85">
<text x="58" y="43">schema</text><text x="163" y="43">batch 0</text><text x="253" y="43">batch 1</text>
<text x="343" y="43">batch 2</text><text x="433" y="43">batch 3</text><text x="523" y="43">batch 4</text>
<text x="598" y="43">EOS</text>
</g>
<g font-size="9.5" fill="currentColor" fill-opacity="0.6">
<text x="0" y="66">FFFFFFFF + 长度</text><text x="570" y="66">FFFFFFFF 0</text>
</g>
<text x="0" y="98" font-size="13.2" font-weight="500" fill="currentColor">file：magic + 同样的消息序列 + footer</text>
<g stroke="currentColor" stroke-opacity="0.4" fill="none">
<rect x="0" y="110" width="62" height="26"/>
<rect x="66" y="110" width="96" height="26"/>
<rect x="166" y="110" width="78" height="26"/><rect x="248" y="110" width="78" height="26"/>
<rect x="330" y="110" width="78" height="26"/><rect x="412" y="110" width="78" height="26"/>
<rect x="494" y="110" width="78" height="26"/>
<rect x="576" y="110" width="60" height="26" fill="#1D9E75" fill-opacity="0.18"/>
</g>
<g font-size="9.5" text-anchor="middle" fill="currentColor" fill-opacity="0.85">
<text x="31" y="127">ARROW1</text><text x="114" y="127">schema</text>
<text x="205" y="127">batch 0</text><text x="287" y="127">batch 1</text><text x="369" y="127">batch 2</text>
<text x="451" y="127">batch 3</text><text x="533" y="127">batch 4</text><text x="606" y="127">footer</text>
</g>
<text x="0" y="150" font-size="9.5" fill="currentColor" fill-opacity="0.6">magic 8 B</text>
<text x="576" y="150" font-size="9.5" fill="currentColor" fill-opacity="0.6">560 B + 长度 + magic</text>
<line x1="0" y1="164" x2="680" y2="164" stroke="currentColor" stroke-opacity="0.15"/>
<text x="0" y="180" font-size="11" fill="currentColor" fill-opacity="0.8">取第 3 个 batch：file 0.87 ms（按序号定位），stream 1.32 ms（要先解前两个）。</text>
<text x="0" y="196" font-size="11" fill="currentColor" fill-opacity="0.8">file 的 footer 里还存了第二份 schema——字段名在 file 里出现 2 次，在 stream 里只有 1 次。</text>
</svg>
</figure>

本机实测的字节（`reports/w05-ipc.md`）：

```text
stream 前 8 字节 : ffffffff a8010000   ← continuation 标记 + 消息长度 0x1a8
stream 后 8 字节 : ffffffff 00000000   ← EOS：continuation + 长度 0
file   前 8 字节 : 4152524f 57310000   ← "ARROW1" + 2 字节填充
file   后 8 字节 : 0000 4152524f5731   ← footer 长度 + "ARROW1"
footer 长度字段   : 560 B
```

三件事值得记住：

**① 每条消息都以 `0xFFFFFFFF` 开头。** 这是 IPC "continuation marker"，用来对齐与向后兼容（老版本没有它，直接写 4 字节长度）。这也是为什么 IPC 文件可以被 `mmap` 后逐条消息扫描——不需要解析整个文件。

**② schema 只发一次。** 用字段名 `channel` 在整个文件里出现的次数做代理指标：stream 里 **1 次**，file 里 **2 次**（file 的 footer 又存了一份）。这意味着 5 个 batch 共享同一份 schema，**不是每个 batch 都带类型信息**——这是 IPC 比 JSON 省体积的一半原因，另一半是值本身是裸字节。

**③ file 的 footer 才是随机访问的关键。** 它记录每个 batch 的 offset 与长度，所以 `get_batch(2)` 可以直接 seek（0.87 ms）；stream 没有索引，只能从头解码（1.32 ms）。差 0.45 ms 看着小，但当 batch 数是 1,000 而不是 5 时，这个差距是线性的。

## 3. 零拷贝的边界：进程内为真，跨进程为假

"零拷贝"这个词被用得太宽。按边界分三层，只有第一层是真的：

<figure class="diagram">
<svg width="100%" viewBox="0 0 680 196" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
<title>零拷贝的三层边界</title>
<desc>同一进程内多个库共享同一块 buffer；跨进程要经过一次内核拷贝但仍无需解析；跨网络必须序列化，接收端仍需一次拷贝。</desc>
<text x="0" y="14" font-size="13.2" font-weight="500" fill="currentColor">从"地址相同"到"必须复制"，中间只有一次边界</text>
<g stroke="currentColor" stroke-opacity="0.4" fill="none">
<rect x="0" y="30" width="204" height="86"/>
<rect x="238" y="30" width="204" height="86"/>
<rect x="476" y="30" width="204" height="86"/>
</g>
<g font-size="11" text-anchor="middle" fill="currentColor">
<text x="102" y="50" font-weight="500">同一进程</text>
<text x="340" y="50" font-weight="500">跨进程</text>
<text x="578" y="50" font-weight="500">跨网络</text>
<text x="102" y="72" font-size="10.5" fill-opacity="0.75">numpy / polars / duckdb</text>
<text x="102" y="88" font-size="10.5" fill-opacity="0.75">C Data Interface</text>
<text x="102" y="106" font-size="10.5" fill-opacity="0.75">拷贝 0 次 · 解析 0 次</text>
<text x="340" y="72" font-size="10.5" fill-opacity="0.75">IPC / 共享内存 / 管道</text>
<text x="340" y="88" font-size="10.5" fill-opacity="0.75">schema 随首包到达</text>
<text x="340" y="106" font-size="10.5" fill-opacity="0.75">拷贝 1 次 · 解析 0 次</text>
<text x="578" y="72" font-size="10.5" fill-opacity="0.75">Flight / HTTP</text>
<text x="578" y="88" font-size="10.5" fill-opacity="0.75">压缩、加密、分包</text>
<text x="578" y="106" font-size="10.5" fill-opacity="0.75">拷贝 ≥1 次 · 解析 0 次</text>
</g>
<g stroke="currentColor" stroke-opacity="0.45" fill="none">
<path d="M206 73 h28"/><path d="M444 73 h28"/>
</g>
<g fill="currentColor" fill-opacity="0.5">
<path d="M234 73 l-7 -3.5 l0 7 z"/><path d="M472 73 l-7 -3.5 l0 7 z"/>
</g>
<g font-size="9.5" text-anchor="middle" fill="currentColor" fill-opacity="0.7">
<text x="220" y="68">fork/pipe</text><text x="458" y="68">socket</text>
</g>
<g font-size="10.5" fill="currentColor" fill-opacity="0.85">
<text x="0" y="136">实测：pyarrow→polars→pyarrow 地址相同（1.9 ms）；</text>
<text x="0" y="152">C Data 胶囊导出再导入，地址相同；父进程读 26.88 MB IPC 只要 4.4 ms，</text>
<text x="0" y="168">同一份数据的 JSON 要 547.6 ms（124×）——差的全是解析。</text>
</g>
<line x1="0" y1="178" x2="680" y2="178" stroke="currentColor" stroke-opacity="0.15"/>
<text x="0" y="192" font-size="11" fill="currentColor" fill-opacity="0.8">"零拷贝"省略的主语永远是"再一次"：跨进程省掉的是解析，不是 memcpy。</text>
</svg>
</figure>

三条实测（E3，全部比地址）：

| 路径 | 地址相同 | 耗时 |
|---|---|---|
| `numpy ndarray → pa.array` | **是** | — |
| `pyarrow → polars → pyarrow` | **是** | 1.9 ms |
| `pyarrow → duckdb`（`duckdb.arrow` 聚合） | **是**（不导入存储） | 60.3 ms |
| C Data 胶囊 导出 → 导入 | **是** | — |

`duckdb.arrow(tbl)` 的语义要说清：它把 Arrow 表注册成一个"外部表"，聚合直接在 Arrow buffer 上做，**不把数据搬进 DuckDB 的列存**。所以 60.3 ms 是 587,000 行 `sum(vol)` 的计算时间，不是导入时间。这也是为什么"用 DuckDB 查 Arrow"经常比"先 `CREATE TABLE AS` 再查"快一个量级——后者是一次完整复制 + 编码。

跨进程那一层（E4）：子进程解析 Parquet、写成 IPC，父进程读回。

```text
父子进程总耗时（含 import 与解析 parquet）：2,321 ms
父进程挂类型（IPC file）  :   4.4 ms，26.88 MB
父进程解析（JSON）        : 547.6 ms，66.03 MB
→ 接收端差 124× 时间、2.5× 字节
```

注意 2.3 秒里绝大部分是子进程 `import pyarrow` 和解析 Parquet，不是传输。**跨进程的真实成本结构是"启动 + 解析"，IPC 只优化了后半段**——这直接影响第 8 章的架构选择：与其起一个短命进程传数据，不如常驻一个服务。

## 4. C Data Interface：两个 struct，一个回调

进程内零拷贝之所以能跨语言，靠的不是"大家都实现了 Arrow"，而是一个 **C ABI 约定**：两个 struct。

```c
struct ArrowSchema {          // 类型描述
  const char* format;         //   "l" = int64, "vu" = utf8 ...
  const char* name;
  // ... metadata, flags, children, dictionary
  void (*release)(struct ArrowSchema*);
};

struct ArrowArray {           // 数据本体
  int64_t length;             //   行数
  int64_t null_count;
  int64_t offset;             //   slice 的偏移
  const void** buffers;       //   指向上面的 buffer 数组
  int64_t n_buffers;
  // ... children, dictionary
  void (*release)(struct ArrowArray*);
};
```

生产者填好这两个 struct 交给消费者，消费者用完**必须调用 `release`**——这是全部所有权语义。没有引用计数、没有 GC 交互、没有语言运行时依赖。pyarrow / polars / duckdb / arrow-rs 之间能互传，就是因为都实现了这套 ABI。

Python 侧可以不解 ctypes 就验证：

```python
caps = arr.__arrow_c_array__()          # 返回 (schema_capsule, array_capsule)
arr2 = pa.Array._import_from_c_capsule(*caps)
assert arr2.buffers()[1].address == arr.buffers()[1].address   # True
```

**最容易出的事故是生命周期**：`release` 只能调一次，且必须在消费者真正用完 buffer 之后。C++ 侧过早释放会让 Python 读到已归还的内存（use-after-free，表现为"数据随机变脏"而不是崩溃）；反过来不释放就是稳定泄漏。所以跨语言接口的标准做法是：**生产者在导出后不再持有，消费者在 `release` 前不许复制指针**。第 10 章的 C++ 回放层会用到这一点。

## 5. Flight：什么时候值得

Flight 是"把 IPC 放到 gRPC 上"，加了三样 IPC 没有的东西：**认证、并行 endpoint、请求级元数据**。Ticket 是一个不透明的字节串，客户端不知道里面是什么，服务端自己解释（可以是 SQL、可以是分区键）。

本机实测（27 MB、单机 loopback、10 次取中位/最小）：

| 方式 | 中位 | 最小 | 体积 |
|---|---|---|---|
| Flight `do_get` | 57.2 ms | 31.0 ms | 26.94 MB |
| IPC stream over HTTP | 46.9 ms | 27.0 ms | 26.87 MB |

**Flight 没有更快，反而略慢 15%。** 因为 gRPC 要为每个 batch 加帧头、做流控，而 HTTP 路径只是一次 `send` 一整块字节。同机复跑中位数在 57–90 ms 之间波动，所以只看最小值：31.0 vs 27.0 ms。

这不是说 Flight 没用，而是**别为了"零拷贝"给它编造收益**。它真正解决的是：

- **多次调用**：连接复用、HTTP/2 多路复用，第 100 次请求不用重新握手；
- **并行拉取**：`get_flight_info` 返回多个 endpoint，客户端可以同时从多台机器拉不同分区（第 8 章的分区表正好对应这个模型）；
- **认证与授权**：gRPC 拦截器统一做 token 校验，比在每个 HTTP handler 里写一遍可靠；
- **背压与取消**：客户端取消一次大查询能真的停掉服务端，而不是把结果算完再丢掉。

反过来说：**单机、一次性、几十 MB 以内的传输，直接读文件或走 IPC over HTTP 更快也更简单。** 我在生产里见过用 Flight 传 2 MB 配置表的，那是把复杂度当成了先进性。

## 6. 实测汇总

| 实验 | 关键数字 |
|---|---|
| E1 序列化 | JSON 66.03 MB / 542.5 ms；IPC 26.87 MB / 2.3 ms；Parquet 7.34 MB / 6.7 ms |
| E2 结构 | stream 头 `FFFFFFFF`，file 头 `ARROW1`；footer 560 B；schema 1 次 vs 2 次 |
| E2 随机取 | 第 3 个 batch：file 0.87 ms，stream 1.32 ms |
| E3 零拷贝 | 四条路径地址全部相同；polars 往返 1.9 ms |
| E4 跨进程 | 4.4 ms vs 547.6 ms（124×）；26.88 MB vs 66.03 MB |
| E5 Flight | 最小 31.0 ms vs HTTP 27.0 ms——**没有优势** |

## 7. 代码

```bash
cd market-lake
uv run python py/w05_ipc.py     # 约 15 秒（含 JSON 序列化与 Flight 起停）
cat reports/w05-ipc.md
```

脚本里有两个值得抄走的设计：

**① 每个格式都只测"读回"，不测"边读边算"。** 因为本章要回答的是"传输格式的代价"，不是"查询引擎的性能"。把聚合混进来，就分不清 6.7 ms 里有多少是解压、多少是 `sum`。

**② Flight 服务用线程内 `shutdown()`，不依赖进程退出。** `FlightServerBase` 不关会挂住 Python 退出，这一点在 CI 里表现为"测试跑完了但不结束"。客户端也要显式 `close()`。

## 8. 踩坑

| 坑 | 表现 | 正确做法 |
|---|---|---|
| 以为 IPC 比 Parquet 小 | IPC 26.87 MB vs Parquet 7.34 MB | IPC 不压缩不编码，只是"内存的搬运" |
| 以为 file 和 stream 只差 footer | file **开头**是 `ARROW1`，stream 开头是 `FFFFFFFF` | 按 magic 判断，别按扩展名 |
| 用 JSON 传行情 | 66 MB、542 ms 解析 | IPC / Parquet；JSON 只用于调试 |
| 以为零拷贝跨进程也成立 | 跨进程必然有一次 copy | 省的是解析，不是 memcpy |
| C Data 侧忘记 `release` | 稳定内存泄漏，或 use-after-free 读到脏数据 | 导出方不再持有，消费方 release 一次 |
| Flight 当"更快的 HTTP" | 单次本机实测反而慢 15% | 用它换认证/并行/取消，不换速度 |
| `duckdb.arrow()` 当成导入 | 以为数据进了 DuckDB | 它是在 Arrow buffer 上直接算 |
| 忽略 file 的 footer 里也有一份 schema | 数 schema 次数得到 2，以为写错了 | file = 头部 schema + footer schema，正常 |

## 9. 出处

- Apache Arrow, *IPC Format* — 消息封装（continuation marker、metadata length）、stream 与 file 的差异、file footer 结构
- Apache Arrow, *Flight* / *Flight SQL* — `GetFlightInfo` / `DoGet` / `DoPut`、Ticket 的不透明语义、endpoint 列表
- Apache Arrow, *C Data Interface* — `ArrowSchema` / `ArrowArray` 字段定义与 `release` 回调的所有权约定
- `pyarrow.ipc` 的 `IpcWriteOptions`（`compression`）、`pyarrow.flight.FlightServerBase`
- 本章数据：`reports/w05-ipc.md`（脚本 `py/w05_ipc.py`，2026-11-11，pyarrow 26.0.1）

[^1]: 消息前缀的 `0xFFFFFFFF` 是 "continuation marker"，见 IPC 规范的 *Encapsulated Message Format*；EOS 是长度为 0 的消息。本机实测 stream 首尾各 8 字节与规范一致。
[^2]: file 布局为 `ARROW1\0\0` + schema + record batches + footer + int32 footer 长度 + `ARROW1`，见 *File Format*。实测 footer 560 B；字段名在 file 中出现 2 次（头部 schema 与 footer 各一份）。
[^3]: Flight 延迟是本机 loopback、10 次取中位/最小，同机复跑中位数在 57–90 ms 波动。⚠️ 这个数字不能外推到跨机房场景——那时 RPC 开销会被网络 RTT 淹没，Flight 的多路复用与并行 endpoint 才开始有意义。

---

**下一步**：第 6 章换到读侧——DuckDB 拿到这些文件之后，谓词下推到底下推了什么，以及那个真实的 ASOF JOIN 计划陷阱。
