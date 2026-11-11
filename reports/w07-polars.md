# 第 7 章实测：Polars / 三引擎

polars 2.1.0 / duckdb 2.0.0 / pandas 3.1.1，2026-11-11

## E1 同一查询三实现

| 实现 | 中位 ms | 相对 Polars |
|---|---|---|
| pandas（全读 + groupby.apply） | 77.2 | 10.0× |
| DuckDB | 27.1 | 3.50× |
| Polars（lazy） | 7.7 | 1.00× |

结果：239 个 bar，sum(vol)=33,429,400，vwap=4.4989

## E2 惰性

- 全读再过滤 13.9 ms vs 惰性下推 6.7 ms（2.1×）

## E4 join_asof

- Polars 1.3 ms / 957 行（LEFT）
- DuckDB 14.4 ms / 238 行（INNER）

## E5 sink_parquet

- 5,000,000 行，三种写法：scan→filter→sink 20 ms / +110 MB；scan→sort→sink 102 ms / +584 MB；collect→write 99 ms / +259 MB
- 读回行数一致=True
