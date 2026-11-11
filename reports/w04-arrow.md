# 第 4 章实测：Arrow 内存模型

数据源 `lake/data/raw/trades`，587,000 行 × 7 列；pyarrow 26.0.1 / numpy 2.5.4，2026-11-11

## E1 行式流 / 列式流 / CSV

| 路径 | 耗时 ms | Arrow 分配 MB | 峰值 MB | RSS 增量 MB |
|---|---|---|---|---|
| 行式 bin → 转置 → Arrow | 11.2 | 19.03 | 0.01 | 31.9 |
| 列式 bin → `from_buffers` | 1.4 | 0.00 | 0.01 | 0.0 |
| Arrow CSV reader | 10.8 | 20.16 | 0.00 | 50.6 |
| pandas `read_csv` | 28.6 | — | — | 30.9 |

磁盘：行式 22.4 MB / 列式 20.2 MB / CSV 24.5 MB

## E4 slice

- 10,000,000 → 1,000,000 行：7.3 µs，地址不变
- 2,000 个 1,000 行切片账面 15.3 MB，底层驻留 76.3 MB

## E5 batch / table

- 5 个 RecordBatch → Table：`concat` 9 µs（零拷贝）
- `combine_chunks` 558 µs（拷贝），1,000,000 行

## E6 字典数组

- 587,000 行 × 4 个唯一合约：7.35 MB → 2.24 MB（3.3×），编码 10.5 ms
- 反例 `ts`：字典 6.7 MB vs 原始 4.5 MB

## E7 对齐

- values buffer 地址 mod 64 分布：{0: 50, 16: 50, 32: 25, 48: 25}（150 个数组，均 16 字节对齐）
