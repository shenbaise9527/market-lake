# 第 6 章实测：DuckDB 下推与 ASOF

duckdb 2.0.0，2026-11-11；数据 587,000 行 × 7 列，按 dt/exchange 两級分区、每合约一文件

## E1 分区裁剪

| 查询 | Data Read kB | 扫过的行组 | 中位 ms |
|---|---|---|---|
| 全表 count | 131.0 | 8 | 6.17 |
| dt 一天 | 81.9 | 4 | 5.50 |
| dt 一天 + 抄表 | 258.7 | 4 | 4.36 |
| 单合约（不按 dt） | 217.8 | 2 | 6.62 |
| 单合约 + dt | 127.6 | 1 | 5.45 |

## E3 ASOF 四种写法

| 写法 | 中位 ms | 连接算子 |
|---|---|---|
| A 物化表 ASOF | 13.0 | Asof Join |
| B CTE 直接 ASOF | 262.8 | Nested Loop Join |
| C 子查询 + 过滤 | 13.1 | Asof Join |
| D LEFT JOIN + 不等号 | 55.2 | Hash Join |
| E CTE + MATERIALIZED | 221.3 | Nested Loop Join |

## E5 线程

| threads | ms |
|---|---|
| 1 | 21.7 |
| 2 | 15.4 |
| 4 | 12.1 |
| 8 | 12.2 |

## E6 分区写出

- 4 个文件 / 27 ms / 1,913 个 bar / 结果一致=True
