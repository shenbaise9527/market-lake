# 第 5 章实测：IPC / C Data / Flight

587,000 行 × 7 列；pyarrow 26.0.1，2026-11-11

## E1 序列化矩阵

| 格式 | 体积 MB | 写 ms | 读 ms | 相对 IPC |
|---|---|---|---|---|
| CSV | 29.64 | 642.2 | 25.1 | 1.10× |
| JSON records | 66.03 | 505.9 | 542.0 | 2.46× |
| Parquet zstd-3 | 7.34 | 128.2 | 8.7 | 0.27× |
| IPC stream | 26.87 | 12.8 | 2.3 | 1.00× |
| IPC stream + zstd | 7.57 | 20.8 | 8.8 | 0.28× |
| IPC file | 26.87 | 12.1 | 6.9 | 1.00× |

## E2 结构

- stream 头 `ffffffffa8010000`；file 尾 `ARROW1`
- 字段名出现次数：stream 1，file 2（file 在 footer 里存了第二份 schema）
- 只读一列：file 0.9 ms / stream 1.2 ms

## E4 跨进程

- IPC 5.3 ms / 26.88 MB；JSON 563.9 ms / 66.03 MB

## E5 Flight

- Flight 中位 57.2 ms；IPC over HTTP 中位 46.9 ms；payload 26.87 MB
