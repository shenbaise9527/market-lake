# 第 5 周 · IPC、Flight 与跨语言零拷贝

**阶段** S2 ｜ **时长** 9h ｜ **前置** W4

## 本周目标

1. 用 C Data Interface 在三语言间传数据，且**证明**没有复制。
2. 会写/读 Arrow IPC stream。
3. 起一个最小 Flight 服务，理解它替代了什么。

## 阅读（约 2h）

- Arrow IPC：stream 与 file 的区别、RecordBatch 的消息封装
- Arrow Flight：`DoGet` / `DoPut` / `Ticket` 的概念（不必读协议细节）
- C Data Interface：`ArrowArray` / `ArrowSchema` 结构体与 `release` 回调的生命周期语义

## 动手（约 6h）

1. 跑 `py/cdata_demo.py`，解释「地址相同」证明了什么，以及如果不相同意味着什么。
   然后自己加一项检查：把 polars 的行过滤一下，再看地址是否还相同（预期变或不变？先猜再做）。
2. **IPC**：pyarrow 写一个 `RecordBatchStreamWriter` 到文件；
   Rust 用 `arrow-ipc` 读回来，打印 schema 与行数。要求两端 schema 完全一致。
3. **Flight**：用 `pyarrow.flight` 起一个本地服务，提供 `DoGet(ticket=symbol+日期范围)`；
   客户端拉 30 次，记录 p50 / p99 延迟。
4. **传输对比**：同一份 100MB 数据，分别用 Flight / JSON / CSV 传一次，
   记录体积、序列化耗时、反序列化耗时。
5. **时区陷阱**：构造一列「不带 tz 的 timestamp」，跨语言读一遍，
   观察是否偏移 8 小时，以及怎么让两端一致（结论写进 cheatsheet）。

## 验收

- `cdata_demo.py` 的地址对比通过，且能解释生命周期风险。
- IPC 往返后行数、列数、类型完全一致。
- Flight 端到端 p50 ≤ 5ms（本地回环，单合约一天数据）。
- 能回答：什么时候该用 Flight，什么时候直接读 Parquet 就够了？

## 常见坑

- C Data Interface 里 `release` 回调谁调用、调用几次，搞错就是 use-after-free 或内存泄漏。
- 跨语言 schema 不一致最常见的三处：timestamp 单位（ns/us）、timezone 字符串、dictionary 的 value 类型。
- Flight 不是"更快的 HTTP"，它只是把序列化成本降到接近零；网络 RTT 仍然是主项。
- IPC file 格式带 footer 可以随机访问，stream 格式只能顺序读——按用途选。

## 交付物

- `rust/src/read_ipc.rs`
- `py/flight_server.py` + `py/flight_client.py`
- `notes/week05.md`：传输对比表 + 时区结论
