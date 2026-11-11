# 第 10 章实测：端到端整合

pyarrow 26.0.1 / duckdb 2.0.0 / polars 2.1.0 / parquet-rs 60.1.0 / Arrow C++ 26.0.1
2026-11-11；587,000 行 × 7 列 + 2 分区列

## C1 视图层

- 587,000 行 / 4 合约 / 2 天，首次扫描 15 ms

## C2 一致性

- lake vs raw 指纹一致 = True（587,000 行）

## C3 质量报告

- 586,992 行；跳变 113；非正价格 0；重复 738

## C4 交叉验证（C++ vs Python 双均线回放）

- bar 239 / 开仓 1 / 点数收益 -0.98，一致 = True

## C++ 回放（mdlake_cpp，单遍顺序）

- 73,375 tick → 239 分钟 bar，20/60 双均线，开仓 1 次，点数收益 -0.98
- 回放 5.17 / 5.80 / 5.95 ms 三次（Release，vcpkg x64-windows-static 静态链接）；内存恒定（两个滑窗 + 当前 bar）
- Debug 版同结果、19.6 ms —— 换构建方式不改数值，只改部署形态（17 个 DLL → 1 个 25 MB exe）
- **Linux 复验**（Ubuntu 24.04 / gcc / Xeon E5-2630 v4 @2.2 GHz）：同一份 parquet → 239 bar / 1 开仓 / -0.98，与 Windows 及 Polars 逐位相同；回放 10.29–11.24 ms 五次，40.6 MB 单文件 ELF，`ldd` 只剩 libstdc++/libm/libgcc_s/libc
- Linux 侧首次构建 **35 分钟**（154 个包全源码，arrow 的 dbg+rel 占大头），之后增量编译 10 s

## Rust ingest（mdlake ingest）

- raw 8 文件 587,000 行 → 排序 → 8 个 (dt, symbol) 分区，zstd，rg=32,768
- 总耗时约 0.5 s；幂等（重跑两次仍 8 个文件，行数不变）
