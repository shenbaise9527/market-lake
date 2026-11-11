# 第 10 章 · 实战整合：一座能跑的 mini 行情数据湖

!!! abstract "本章结论（读完你应该能复述这三句）"
    1. **端到端只有 0.5 秒，但每一环都用了前九章的结论**。Rust `ingest` 把 raw 的 8 个文件读入（第 4 章 buffer 布局）、排序（第 3 章排序键）、按 `(dt, symbol)` 分区（第 8 章单分区 < 10 文件）写成 zstd Parquet（第 3 章编码档），幂等落盘（第 8 章原子替换）——587,000 行、8 分区、两次重跑行数不变。
    2. **跨语言必须对结果，不能对感觉**。同一份 002322.SZ 数据，C++ 回放（Arrow C++ 流式读取）与 Python（Polars 复算）各自独立实现 20/60 双均线：239 个分钟 bar、1 次开仓、点数收益 **-0.98**，逐位一致。两边都是独立实现——这不是"运行同一个库"，是两套代码对同一份数据给出同一份答案。
    3. **回放的形态由数据布局决定**。C++ 单遍回放只投影 `ts/px` 两列、逐行组流式读取，5.8 ms 扫完 73,375 tick，内存只有两个 SMA 滑窗——能这么写，是因为 ingest 已经按 `(symbol, ts)` 排好序、单合约单文件。**回测引擎的零随机 IO 不是 C++ 给的，是写入端给的。**

对应任务卡：仓库内 `docs/week10.md` ｜ 本章脚本：`py/capstone_query.py`、`rust/src/ingest.rs`、`cpp/main.cpp` ｜ 原始结果：`reports/w10-capstone.md`

---

## 1. 系统全景

<figure class="diagram">
<svg width="100%" viewBox="0 20 655 205" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
<title>mini 行情数据湖的端到端架构</title>
<desc>采集生成 raw hive 布局，Rust ingest 排序分区写出 lake，Python 挂视图做分析与质量闸门，C++ 做单遍回放，三端读同一份 Parquet。</desc>
<g stroke="currentColor" stroke-opacity="0.35" fill="none">
<rect x="0" y="24" width="196" height="76"/>
<rect x="226" y="24" width="196" height="76"/>
<rect x="452" y="24" width="196" height="76"/>
</g>
<g font-size="10" fill="currentColor">
<text x="10" y="44" font-weight="500">① 采集（gen_tick.py）</text>
<text x="10" y="60" fill-opacity="0.7">合成 587,000 tick / 8 文件</text>
<text x="10" y="76" fill-opacity="0.7">hive 布局 dt=*/exchange=*</text>
<text x="10" y="92" fill-opacity="0.55">乱序 · 重复 · 缺口 · 异常</text>
<text x="236" y="44" font-weight="500">② ingest（mdlake ingest）</text>
<text x="236" y="60" fill-opacity="0.7">读全量 → (dt, symbol, ts) 排序</text>
<text x="236" y="76" fill-opacity="0.7">分区 8 组 · zstd · rg=32,768</text>
<text x="236" y="92" fill-opacity="0.55">.tmp + 校验 + 原子 rename</text>
<text x="462" y="44" font-weight="500">③ 读端（两套独立实现）</text>
<text x="462" y="60" fill-opacity="0.7">Python：DuckDB 视图 + Polars</text>
<text x="462" y="76" fill-opacity="0.7">C++：Arrow C++ 流式回放</text>
<text x="462" y="92" fill-opacity="0.55">读同一份 Parquet，结果对齐</text>
</g>
<g stroke="currentColor" stroke-opacity="0.45" fill="none">
<path d="M198 62 h24"/><path d="M424 62 h24"/>
</g>
<g fill="currentColor" fill-opacity="0.5">
<path d="M222 62 l-6 -3 l0 6 z"/><path d="M448 62 l-6 -3 l0 6 z"/>
</g>
<g stroke="currentColor" stroke-opacity="0.35" fill="none">
<rect x="100" y="130" width="448" height="34"/>
</g>
<g font-size="10" fill="currentColor">
<text x="112" y="151" font-weight="500">lake/data/lake/trades/dt=2026-11-04/symbol=002322.SZ/part-0.parquet</text>
</g>
<g stroke="currentColor" stroke-opacity="0.3" fill="none">
<path d="M324 100 V130"/><path d="M550 100 V130"/><path d="M550 164 V186 H324 V164"/>
</g>
<g font-size="9.5" fill="currentColor" fill-opacity="0.7">
<text x="336" y="122">写入（一次性 / 重跑即替换）</text>
<text x="336" y="182">读取（反复 · 随机 · 流式）</text>
</g>
<text x="0" y="200" font-size="10" fill="currentColor" fill-opacity="0.75">关键点：写端只有一条路径，读端可以任意多。三语言各司其职，靠"文件格式 + 分区约定"解耦，不靠共享库。</text>
<text x="0" y="216" font-size="10" fill="currentColor" fill-opacity="0.55">任何一端换语言、换版本、换机器，只要 Parquet 语义不变，其他两端无感知——这就是把格式当接口。</text>
</svg>
</figure>

三层职责与所选语言：

| 层 | 语言 | 为什么是它 | 交付物 |
|---|---|---|---|
| 生产写入 | Rust | 单二进制部署、无 GC 抖动、parquet-rs 成熟 | `mdlake ingest`（0.5 s / 587k 行） |
| 分析与质量闸门 | Python | DuckDB/Polars 的交互性无可替代 | `py/capstone_query.py` |
| 低延迟回放 | C++ | 嵌入既有回测/实盘框架的通行证 | `mdlake_cpp`（5.8 ms / 单合约日） |

!!! note "三语言分工不是均衡分工"
    Python 承担了 80% 的探索与验证工作量；Rust 只在生产写入这一件事上，C++ 只在回放这一件事上。**先用 Python 把结论做实，再把少数关键环节换语言**——反过来（三语言并行开工）是学习期最大的浪费。

## 2. 写入端：`mdlake ingest`

一条命令完成"raw → lake"：

```text
$ cargo run --release -- ingest --in lake/data/raw/trades --out lake/data/lake/trades
合并 587000 行，排序完成，耗时 197.7ms
分区 8 组：2026-11-04/001317.SZ=73375行 ... 2026-11-05/601963.SH=73375行
写出 587000 行 → lake/data/lake/trades，分区 8 个，总耗时 464.0ms（幂等：重跑即整体替换）
```

它把前九章的结论各用了一遍：

| 结论 | 在 ingest 里的落点 |
|---|---|
| 排序键与谓词对齐（第 3 章） | `(dt, symbol, ts)` 全局排序后再切分区 |
| 单分区 < 10 文件（第 8 章） | 8 个 `(dt, symbol)` 分区，每分区 1 文件 |
| zstd-3 是甜点（第 9 章） | 默认 `--codec zstd`，价格列 PLAIN（BSS 负优化） |
| 幂等 = 新目录 + 原子替换（第 8 章） | `.tmp-*` → 行数校验 → rename；Windows 上不能覆盖式 rename，所以先删旧目录 |

!!! bug "本章踩的真 bug：zip 错位丢一半数据"
    `ingest` 第一版把"每个文件的 batch 列表"和"每个文件的分区值"直接 `zip`。每个文件解出 **2 个 batch**（65,536 行批大小 × 73,375 行），zip 把 16 个 batch 削成 8 个——**合并 293,500 行，凭空丢了 293,500 行**，而且不报任何错。

    被两道闸拦住：指纹对比（C2：`count + sum(vol)`）立刻对不上；行数校验（写出 587,000 ≠ 合并 293,500）直接 panic。**跨语言管线里，行数指纹不是可选项。**

## 3. 查询端：目录即表

```sql
CREATE VIEW trades AS
SELECT * FROM read_parquet('lake/data/lake/trades/**/*.parquet', hive_partitioning=true);
```

之后所有分析只写 `trades`，任何路径都不出现。挂视图 0.1 ms，首次全扫 587,000 行约 30 ms。前面九章的所有查询改个路径就能跑在这座湖上。

## 4. 一致性与质量闸门

入库不算完成，**对上指纹才算**：

```text
raw : rows=587,000  sum(vol)=266,165,100  px∈[3.36, 861.90]
lake: rows=587,000  sum(vol)=266,165,100  px∈[3.36, 861.90]
指纹一致 = True
```

质量报告（`PARTITION BY symbol, dt` 开窗）：

```text
行数 586,992；跳变 113 笔；非正价格 0；重复 738
```

!!! note "跳变从 117 变 113：4 个假跳变被日界修复了"
    第 8 章的质量报告用 `PARTITION BY symbol` 开窗，跨日相邻（昨天收盘 ↔ 今天开盘）被当成相邻 tick，贡献了 4 笔 >5% 的假跳变。这里加上 `dt` 分区后是 113——**开窗边界是数据质量工具自己的质量项**。重复 738 与第 8 章 E3 的结论吻合（重传回报）。

## 5. 回放端：单遍顺序，恒定内存

`mdlake_cpp` 把回测引擎最苛刻的访问模式写成一条直线（依赖交给 vcpkg 静态 triplet，构建细节见 `cpp/build.md`）：

<figure class="diagram">
<svg width="100%" viewBox="0 20 655 191" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
<title>单遍顺序回放的状态机与它的内存足迹</title>
<desc>逐行组流式读 ts/px 两列，tick 进分钟桶，收盘时更新两个 SMA 滑窗并检测交叉，全程只有滑窗加当前 bar 的状态。</desc>
<g stroke="currentColor" stroke-opacity="0.35" fill="none">
<rect x="0" y="24" width="140" height="46"/>
<rect x="164" y="24" width="140" height="46"/>
<rect x="328" y="24" width="140" height="46"/>
<rect x="492" y="24" width="140" height="46"/>
</g>
<g font-size="10" text-anchor="middle" fill="currentColor">
<text x="70" y="43" font-weight="500">Parquet 流</text>
<text x="70" y="58" fill-opacity="0.6">3 行组 · 只投影 ts/px</text>
<text x="234" y="43" font-weight="500">分钟桶</text>
<text x="234" y="58" fill-opacity="0.6">ts // 60e9 · 末笔为 close</text>
<text x="398" y="43" font-weight="500">SMA(20) / SMA(60)</text>
<text x="398" y="58" fill-opacity="0.6">两个滑窗 + 累加和</text>
<text x="562" y="43" font-weight="500">交叉检测</text>
<text x="562" y="58" fill-opacity="0.6">金叉开多 · 死叉平仓</text>
</g>
<g stroke="currentColor" stroke-opacity="0.45" fill="none">
<path d="M142 47 h18"/><path d="M306 47 h18"/><path d="M470 47 h18"/>
</g>
<g fill="currentColor" fill-opacity="0.5">
<path d="M160 47 l-6 -3 l0 6 z"/><path d="M324 47 l-6 -3 l0 6 z"/><path d="M488 47 l-6 -3 l0 6 z"/>
</g>
<g font-size="10" fill="currentColor">
<text x="0" y="96" font-weight="500">实测（002322.SZ，2026-11-04，73,375 tick）</text>
<text x="0" y="114" fill-opacity="0.8">239 个分钟 bar ｜ 开仓 1 次 ｜ 点数收益 -0.98 ｜ 回放 5.80 ms</text>
<text x="0" y="132" fill-opacity="0.8">内存：两个 deque（fast=20 / slow=60 个 double）+ 当前 bar —— 约 1 KB，与 tick 数无关</text>
</g>
<g stroke="currentColor" stroke-opacity="0.3" fill="none">
<line x1="0" y1="148" x2="648" y2="148"/>
</g>
<text x="0" y="168" font-size="10" fill="currentColor" fill-opacity="0.75">这份从容是写端给的：文件按 (symbol, ts) 排序、单合约单文件、px 列可以整块直读 ——</text>
<text x="0" y="184" font-size="10" fill="currentColor" fill-opacity="0.75">于是回放零随机 IO、逐行组顺序推进、连列裁剪都只需要两个下标。</text>
<text x="0" y="202" font-size="10" fill="currentColor" fill-opacity="0.55">换成交错序的全市场大文件，同样的代码要么全量载入（内存爆），要么乱序 bar（结果错）。</text>
</svg>
</figure>

同一文件、同一台机器，上一版（借 `pyarrow` 的头文件与 DLL 编）回放 5.36 ms，这一版静态链接 5.80 ms；同一份二进制多跑几次落在 5.2–5.9 ms 之间，差距全在噪声内。换依赖管理方式换掉的是部署形态（17 个 DLL → 1 个 exe），不是运行时性能；**想让回放变快，去改写入端的排序与行组，别在链接方式上找**。

同一份 parquet 在 Linux 上（Ubuntu 24.04 / gcc / Xeon E5-2630 v4）又跑了一遍：**239 bar、开仓 1 次、-0.98，与 Windows、与 Polars 三方逐位相同**。回放 10.29–11.24 ms，比 Windows 慢近一倍——那是 2016 年的服务器 CPU 与更新的桌面 CPU 之间的差距，不是代码差距。产物形态也一致：40.6 MB 单文件 ELF，`ldd` 只剩 `libstdc++ / libm / libgcc_s / libc`，同样没有 `libarrow.so` 要带。

> 两个平台都验证过，才敢说"一致"不是某台机器的巧合。代价是 Linux 侧第一次要从源码编 154 个包（35 分钟）——这笔账只在第一次付，之后走二进制缓存与增量编译（10 s）。

**跨语言一致性是本章的验收线**。Python 侧用 Polars 独立复算同一策略（不共享任何 C++ 代码）：

```text
Python: 分钟 bar 239；开仓 1 次；点数收益 -0.98
C++  : 分钟 bar 239；开仓 1 次；点数收益 -0.98
两套独立实现一致 = True
```

这比单元测试强：两套实现、两种语言、同一条数据，结果逐位相等。**浮点累加顺序**是最容易破坏这种一致性的东西——两个滑窗都是固定窗口大小的算术和，求和顺序由数据顺序决定，而数据顺序由排序键决定，所以一致。

!!! tip "把'对结果'写进管线，而不是写进愿望"
    C4 的验证代码只有 40 行，跑一次不到 1 s。它防住的事故——"重写了回放器，策略结果悄悄变了"——是量化系统里最贵的事故。第 9 章的查询指纹管"读没读对"，C4 的策略指纹管"算没算对"，两层都要有。

## 6. 决策记录（给未来的自己）

六个决定，三个真的重要，三个其实无所谓：

| 决定 | 影响 | 复盘 |
|---|---|---|
| 分区只到 `(dt, symbol)` | **大**——8 个文件，绑定开销 3 ms 内；切到小时是 26 ms | 正确（第 8 章） |
| 排序 `(dt, symbol, ts)` | **大**——点查/回放/编码三处同时受益 | 正确（第 3、9 章） |
| zstd-3 默认 | **大**——体积 −64%，全扫 −28%，写只慢 3.4× | 正确（第 9 章） |
| 幂等 rename 模式 | **大**——重跑安全，且被行数校验兜底 | 正确（第 8 章） |
| px 列 PLAIN 而非 BSS | 小——省了一次负优化，但差异在 100 KB 级 | 无所谓（第 3 章已证伪 BSS） |
| rg=32,768 vs 65,536 | 小——全部查询差异 < 0.5 ms（第 9 章噪声地板） | 无所谓 |

**"哪三个其实无所谓"比"哪三个重要"更值钱**——它们以后还会诱惑你花一个下午去调。

## 7. 踩坑

| 坑 | 表现 | 正确做法 |
|---|---|---|
| batch 与文件 zip 错位 | ingest 凭空丢 293,500 行且无报错 | 按文件分组收集 batch；行数指纹兜底 |
| `lag()` 忘了按日分区 | 跨日相邻产生 4 笔假跳变 | 质量查询的窗口边界也要 review |
| Arrow C++ 26 头文件 + C++17 | `std::span` 报错连环炸 | Arrow 16+ 需要 **C++20**（`/std:c++20`） |
| `CHECK_OK` 按值接 `Result<unique_ptr>` | 编译错误：unique_ptr 不可拷贝 | 宏里用 `auto&` 绑引用 |
| 借 `pyarrow` 的头文件与 DLL 编 C++ | 能跑，但要往 exe 旁边拷 17 个 DLL，CRT 还是哈希名 | 换 vcpkg `x64-windows-static`：25 MB 单 exe，`dumpbin /dependents` 只剩系统 DLL |
| 静态 triplet + MSVC 默认 `/MD` | `LNK2038: MT_StaticDebug vs MT_StaticRelease` | `CMAKE_MSVC_RUNTIME_LIBRARY` 要设在 **`project()` 之后**——之前 `MSVC` 还没定义 |
| Arrow 19 的 `FindSnappyAlt` 只认 `Snappy::snappy-static` | 静默退回 `find_library`，捞到 `debug/lib/snappy.lib`（`/MTd`），Release 链接失败 | 给 `Snappy::snappy` 建 ALIAS，让它走 vcpkg 的 imported target |
| Arrow API 三年一换 | `OpenFile` 签名、`GetSchema` 形态都变了 | 编译错误即文档：看头文件，别背 API |
| Linux 上 vcpkg 把 triplet 误判成交叉编译 | `x64-linux-gnu-g++ is not a full path`，Arrow 配置阶段失败 | 官方 `x64-linux` 声明了 `VCPKG_CMAKE_SYSTEM_NAME`，带出 `-DCMAKE_SYSTEM_PROCESSOR=x64` ≠ host 的 `x86_64`；项目内自定义 triplet + chainload toolchain 钉回本机编译器 |
| 结果"差不多就行" | 跨语言浮点差异掩盖逻辑错误 | 策略指纹逐位相等，写进 CI |

## 8. 出处

- Apache Arrow C++ — `parquet/arrow/reader.h`（`OpenFile`、`GetRecordBatchReader` 的列投影与行组裁剪）、`Result<T>` 语义
- parquet-rs 60.1.0 — `ArrowWriter`、`WriterProperties::builder`（本项目 `rust/src/writer.rs` 使用处有行号注释）
- DuckDB 文档 — *Views*、`read_parquet` 的 `hive_partitioning`
- 本章数据：`reports/w10-capstone.md`（脚本 `py/capstone_query.py`，2026-11-11）

[^1]: 幂等 rename 在 Windows 上的变体：目录 rename 不能覆盖已存在目录，所以 ingest 先 `remove_dir_all` 旧目录再 rename。这有几十毫秒的窗口读端会看到"目录不存在"；生产上应换成"版本化目录 + 指针文件"或 POSIX 语义的文件系统（含对象存储的原子 put）。
[^2]: 回放点数收益 -0.98 是合成数据上 20/60 双均线的一次运行结果，**不是策略评估**——单日、单合约、无手续费滑点。它出现在这里只作为跨语言一致性的锚点。
[^3]: C++ 侧内存"恒定"的准确说法：额外状态是 O(slow_n) 的 deque 与 O(1) 的累加器；Arrow 的 batch 缓冲由 reader 复用，峰值内存受 batch 大小控制（默认 65,536 行）而非文件大小。

---

**下一步**：十个章节全部完成。回到[首页](index.md)看进度，或翻[调参速查表](appendix/cheatsheet.md)把结论用到你自己的数据上——从第 3 周任务卡开始，把 `py/w03_writer.py` 跑在你自己的行情上，差异超过 20% 的项写清楚原因。
