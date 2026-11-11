# C++ 侧说明

## 定位

C++ 分两个用途，别混：

1. **读**：嵌入已有系统（柜台、交易网关、老的行情中转）时，用 `arrow::dataset` 或
   `parquet::arrow::FileReader` 直接吃 Parquet，再交给已有的 C++ 计算代码。
2. **写**：极少数情况——你就在数据源那头（CTP/交易所 SDK 的 C++ 回调里），
   需要零拷贝地攒 `RecordBatch` 再落盘。这时用 `parquet::arrow::FileWriter`。

绝大多数人的生产链路里，C++ 只是"读写两端"，中间的计算交给 Python/Rust 更划算。

## 构建

依赖由 **vcpkg 清单模式**供给（`vcpkg.json` 声明 `arrow[compute,dataset]` + `parquet`，
`builtin-baseline` 锁版本），Windows / Linux 各一条预设命令：

```bash
cd cpp
cmake --preset windows && cmake --build --preset windows-release     # Windows
cmake --preset linux   && cmake --build --preset linux-release       # Linux（有 ninja）
cmake --preset linux-make && cmake --build --preset linux-make-release  # Linux（只有 make）
```

产物在 `build/bin/[Release/]mdlake_cpp.exe`（Linux：`build-make/bin/mdlake_cpp`），
两个平台都是**单文件、零第三方动态库**。首次 configure 由 vcpkg 自动装依赖
（Windows 3.6 GB / Linux 1.4 GB，均入 .gitignore），需要环境变量 `VCPKG_ROOT`
指向一份 vcpkg 克隆——**机器上已有就直接指过去，不用另装**。

Linux 侧要多装 `flex bison m4 gperf`（thrift 需要），并且要用项目自带的
`triplets/x64-linux-static.cmake`（官方 `x64-linux` 在本机会被误判成交叉编译，
预设里已经接好，不用手动指定）。

细节、两平台实测数字和三个坑（`/MT` 静态 CRT、Arrow 19 的 Snappy 查找 bug、
Linux triplet 误判）见 [`build.md`](build.md)。之前那套"直接借 `pyarrow` 的头文件与 DLL"的做法已删除。

## 与 Rust 版对照

| 概念 | C++ | Rust |
|---|---|---|
| 错误处理 | `Status` / `Result<T>` | `Result<T, ArrowError>` |
| 内存池 | `arrow::MemoryPool` | 无（所有权 + `Arc<Buffer>`） |
| 读取器 | `parquet::arrow::FileReader` | `ParquetRecordBatchReader` |
| 写入器 | `parquet::arrow::FileWriter` | `ArrowWriter` |
| 计算 | `arrow::compute::*` | `arrow::compute::kernels::*` |

## 注意

- `main.cpp` 已在 Windows（MSVC 2022 + `x64-windows-static`）与 Linux（gcc + `x64-linux-static`）
  两处编译并跑通，同一份 parquet 输出逐位一致（239 bar / 1 开仓 / -0.98）。
  换平台若报 target 名不对，`CMakeLists.txt` 已经同时接住 `Arrow::arrow_static` 与 `Arrow::arrow_shared`。
- 别用 `arrow::Table` 装 TB 级数据。`Table` 会把所有行组一次性读进内存，
  正确姿势是 `GetRecordBatchReader()` 逐批处理。
