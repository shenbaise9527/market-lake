# 术语表

按"读到就查"的方式写。每条只给**能让你继续往下读**的那点信息，不给完整定义——完整定义在规范里。

## 存储分层

**行存 / 列存**
行存把一行的字段放在一起（CSV、MySQL），列存把一列的所有值放在一起（Parquet、Arrow）。
判据不是"哪个先进"，而是访问模式：读整行、按主键点查 → 行存；扫几列做聚合 → 列存。

**row group（行组）**
Parquet 文件里若干行的集合，是**统计信息和剪枝的最小单位**。一个行组要么整块被读，要么整块被跳过。

**column chunk（列块）**
某个行组里**某一列**的全部数据，在文件中连续存放。**列裁剪的最小单位**：读 `px` 就只碰 `px` 的 chunk。

**page（页）**
column chunk 内部再切的小块（默认约 1 MB 量级），是**解码和内存占用的单位**。一次只解一页，不必整列进内存。

**footer（文件尾元数据）**
写在数据之后的"目录"：每列 min/max、null_count、编码列表、chunk 字节偏移、schema。
先读它，再决定读什么——这是"少读"的起点。写在尾部是为了支持单遍写入。

**PAR1**
文件头尾各 4 字节的 magic number，标识这是一个 Parquet 文件。

## 编码与压缩

**dictionary encoding（字典编码）**
把重复出现的值放进字典，数据里只存索引。低基数列（`symbol`、`channel`、`side`）效果极好，
**高基数列（时间戳、序号）会让字典本身成为负担**——这是第 1 章的坑 1。

**RLE（run-length encoding，游程编码）**
把连续重复的值压成 `(值, 次数)`。排序后的低基数列、`side` 这类列收益大。

**DELTA_BINARY_PACKED**
存相邻整数的差值再位打包。**单调递增的时间戳、序号**的标准解法。

**BYTE_STREAM_SPLIT（BSS）**
把浮点数的字节按位拆成多路（所有符号位一路、指数一路、尾数一路），让相似的高位字节聚在一起。
适用于高精度、近乎连续的浮点列；**在两位小数的价格列上反而变大**，见速查表。

**字典回退（dictionary fallback）**
字典页超过 `dictionary_pagesize_limit` 后，编码器放弃字典改用 `PLAIN`。
表现为文件突然变大、编码列表里多出 `PLAIN`——排查体积异常时先看这里。

## 剪枝

**列裁剪（column pruning / projection pushdown）**
只读查询用到的列。省的是"列的字节"。

**元数据剪枝（metadata pruning）**
用 footer 里的 min/max 判断某个行组不可能命中，整块跳过。省的是"行组的字节"。

**zone map**
每个数据块记录 min/max 的索引结构，DuckDB 内部对列段也用这个叫法。Parquet 里对应 column chunk statistics。

**bloom filter**
判断"某个值一定不在这个块里"，用于等值查询（`symbol = 'X'`）。
对范围查询没用——范围查询靠 min/max。

**page index（column index / offset index）**
比行组更细的统计：每个页的 min/max 与字节偏移。让剪枝从"行组级"细到"页级"。

## Arrow

**validity bitmap**
每行 1 bit 标记是否为空。比"每行带一个 null 标志"省 8 倍空间，且能整段快速判断"没有 null"。

**offsets buffer**
变长类型（字符串、list）用偏移数组表示每行的起止位置。int32 offsets 意味着单列上限 2 GB。

**RecordBatch**
一批行 + schema，是 Arrow 的计算与传输单位。一个 `Table` 由多个 batch 组成（`ChunkedArray`）。

**slice 零拷贝**
`array.slice()` 不复制数据，只调整 offset/length。好处是免费，坏处是**长期持有会让 offset 累积**，
底层 buffer 一直无法释放——长期缓存的数据要定期 compaction。

**C Data Interface**
跨语言传 Arrow 数据的 ABI：`ArrowArray` + `ArrowSchema` 两个 struct 加一个 `release` 回调。
零拷贝的关键，也是所有权最容易出错的地方（谁释放、何时释放）。

**IPC / Flight**
IPC 是 Arrow 的序列化格式（Stream/File 两种容器）；Flight 是建立在 gRPC 上的 RPC 协议，
用 IPC 格式传数据，比 HTTP+JSON 少两次编解码。

## 引擎与工程

**谓词下推（predicate pushdown）**
把 `WHERE` 条件下推到扫描层，让存储层先过滤，减少向上传递的数据量。
验证方法只有一个：看 `EXPLAIN ANALYZE` 里 `Rows Scanned` 有没有变小。

**ASOF JOIN**
"按最近的时间点对齐"的连接：每笔成交对齐到它之前最近的盘口快照。行情数据最核心的算子。

**morsel-driven parallelism**
把数据切成小块（morsel）丢进任务队列，线程空闲就抢一块。天然负载均衡，避免长尾分片。

**hive partitioning**
用目录名编码分区值（`dt=2026-11-05/exchange=SH/`），查询时目录即列。零成本获得分区裁剪。

**compaction（合并）**
把大量小文件合并成少数大文件，顺便重新排序。解决"小文件问题"的标准手段。

**小文件问题**
文件数过多导致元数据开销、随机 IO、并行调度成本超过真实数据读取成本。
经验目标：单文件 256 MB–1 GB。

**写入幂等**
同样的输入重跑一次，结果不变。做法：写临时文件 + 原子 rename，失败不留半成品。
