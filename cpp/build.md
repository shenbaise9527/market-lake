# 构建 mdlake_cpp（vcpkg 清单模式）

依赖全部交给 vcpkg，Windows / Linux 一套 `cpp/CMakePresets.json` 走通。
**不再**手工拼 MSVC 环境、也**不再**从 `pyarrow.libs/` 里捞 DLL —— 那些是上一版的临时做法，已废弃。

三个文件分工：

| 文件 | 作用 |
|---|---|
| `cpp/vcpkg.json` | 声明依赖：`arrow[compute,dataset]` + `parquet`，并用 `builtin-baseline` 锁版本 |
| `cpp/CMakePresets.json` | Windows / Linux 两套 triplet（静态 / 动态），配置与编译各一条命令 |
| `cpp/CMakeLists.txt` | 找 toolchain、选 triplet、接住 `*_static` 与 `*_shared` 两种 target 命名 |

首次 configure 时 vcpkg 自动把依赖装到 `cpp/vcpkg_installed/`（本机 3.6 GB，不入库）。
不需要先跑 `vcpkg install`。

## 0. 前置：一个 vcpkg

```bash
git clone https://github.com/microsoft/vcpkg
cd vcpkg && ./bootstrap-vcpkg.sh        # Windows: bootstrap-vcpkg.bat
export VCPKG_ROOT=/path/to/vcpkg        # Windows: setx VCPKG_ROOT D:\vcpkg
```

**机器上已经有 vcpkg 就直接用，别再 clone 一份**：vcpkg 只是个 checkout，`VCPKG_ROOT`
指到任意一份带 `vcpkg` 可执行文件 + `ports/` + `scripts/` 的目录即可（本文 Linux 一节实测
用的就是机器上原有的 `~/code/github/vcpkg`）。清单模式只在缺少对应版本时才去装包，
不会动 vcpkg 本身。

`CMakeLists.txt` 在没传 `CMAKE_TOOLCHAIN_FILE` 时会读 `VCPKG_ROOT`；预设则自己带 triplet，
两边都不需要手写 `-DCMAKE_TOOLCHAIN_FILE=...`。缺 `VCPKG_ROOT` 且不用预设时，配置阶段直接报错并给出提示，不会退化成"找不到 Arrow"的谜语。

## 1. Windows（MSVC 2022，静态 CRT）

```powershell
cd cpp
cmake --preset windows
cmake --build --preset windows-release
.\build\bin\Release\mdlake_cpp.exe ..\lake\data\lake\trades\dt=2026-11-04\symbol=002322.SZ\part-0.parquet 20 60
```

预设要点：

- 生成器 `Visual Studio 17 2022`，架构 x64（不用先开 "x64 Native Tools" 命令行）
- triplet `x64-windows-static`：Arrow / Parquet 全静态，运行期零第三方 DLL
- `MSVC_RUNTIME_LIBRARY=MultiThreaded` 由 `CMakeLists.txt` 设成 `/MT`，与静态 triplet 一致

## 2. Linux（gcc，静态链接）—— 已在真机跑通

实测机器：Ubuntu 24.04 / 8 核 Xeon E5-2630 v4 @ 2.20 GHz / 15 GB / `172.16.30.240`。

### 2.1 前置：系统工具

```bash
sudo apt install -y build-essential curl zip unzip tar pkg-config \
                    autoconf automake libtool flex bison m4 gperf
```

后面那串（`flex bison m4 gperf`）不是可选项：Arrow 的 Parquet 依赖 **thrift**，
thrift 的编译器要用 flex/bison 生成词法与语法分析器。缺了会在 `Building thrift` 那一步
明确报 `Could not find flex. Please install it via your package manager`。

**没有 root 也能补**（这台机器的 `sudo` 要密码，实测用的就是这条路）：

```bash
mkdir -p ~/tools/deb ~/tools/root && cd ~/tools/deb
apt-get download flex bison m4 gperf autoconf automake libtool   # 不需要 root
for f in ./*.deb; do dpkg -x "$f" ~/tools/root; done

export PATH=$HOME/tools/root/usr/bin:$PATH
export BISON_PKGDATADIR=$HOME/tools/root/usr/share/bison   # bison 把数据目录写死成 /usr/share/bison
export M4=$HOME/tools/root/usr/bin/m4                      # bison 起 m4 子进程按名字找，端口构建的 PATH 里没有它
```

这两个环境变量是踩出来的：只把 `bin` 加进 PATH，`bison` 会报
`/usr/share/bison/m4sugar/m4sugar.m4: cannot open`（数据目录没跟着走），
改对之后又报 `m4 subprocess failed: No such file or directory`（子进程找不到 m4）。
vcpkg 给端口构建的环境里 PATH 会被重整，靠 PATH 传递不可靠，直接点名最稳。

### 2.2 用机器上已有的 vcpkg，不要另装一份

```bash
export VCPKG_ROOT=$HOME/code/github/vcpkg        # 这台机器上本来就有的 checkout
cd cpp
cmake --preset linux-make                        # 没装 ninja 用 Makefiles 变体；装了就用 linux
cmake --build --preset linux-make-release
./build-make/bin/mdlake_cpp ../../lake/data/lake/trades/dt=2026-11-04/symbol=002322.SZ/part-0.parquet 20 60
```

`CMakeLists.txt` 在没显式传 toolchain 时读 `VCPKG_ROOT`，所以不需要 `-DCMAKE_TOOLCHAIN_FILE=...`。

> 换 `VCPKG_ROOT` 之后**必须删掉 `build*/CMakeCache.txt`**：toolchain 的**绝对路径**被缓存在里面，
> 旧目录一删，configure 直接报 `include could not find requested file: <旧路径>/scripts/buildsystems/vcpkg.cmake`。
> 这个报错看着像 vcpkg 坏了，其实只是缓存没清。

### 2.3 实测数字

| 项 | 数值 | 备注 |
|---|---|---|
| 首次 `cmake --preset linux-make`（含装依赖） | **35 分钟**（16:37 → 17:12） | 154 个包全源码编，arrow 的 debug+release 两套占大头 |
| 编译 `main.cpp` | 10 s | 单 TU + 静态链接 |
| `build-make/bin/mdlake_cpp` | 40,551,616 B（38.7 MiB） | 单文件，拷走就能跑 |
| 运行期依赖（`ldd`） | `libstdc++`、`libm`、`libgcc_s`、`libc` | **没有 `libarrow.so`**，所以不需要 `LD_LIBRARY_PATH` |
| 安装树 | 1.4 GB / 176 包 | 比 Windows 的 3.6 GB 小，因为只有一套配置 |

同一份 73,375 tick 的数据：

```text
239 bar / 开仓 1 次 / 点数收益 -0.98 / 回放 10.29 / 10.43 / 10.55 / 10.84 / 11.24 ms（跑五次）
```

**与 Windows、与第 10 章的 Polars 复算逐位一致**——换 OS、换编译器、换链接方式，数值一个没变。

⚠️ 别把 10.5 ms 和 Windows 的 5.8 ms 直接比：这台是 2016 年的 Xeon E5-2630 v4 @2.2 GHz，
Windows 那台是更新的桌面 CPU。跨机器比绝对耗时没有意义，能比的只有"结果是否一致"。

### 2.4 为什么是静态链接：`triplets/x64-linux-static.cmake`

原以为 Linux 走 `x64-linux`（动态），实测拿到的是一个 40 MB、`ldd` 里没有 arrow 的单文件。
两个原因叠在一起：

1. vcpkg 2026-04 起，官方 `triplets/x64-linux.cmake` 就是 `VCPKG_LIBRARY_LINKAGE static`；
2. 本项目 `CMakeLists.txt` 优先取 `Arrow::arrow_static`（Windows 静态 triplet 也要这个名字），
   两个平台于是都落到静态库上。

但官方那个 triplet 在本机直接编不过，见 §5 第 3 条；项目里因此放了自定义 triplet：

```text
cpp/triplets/x64-linux-static.cmake        # 目标 triplet：x64 + 静态 + Linux
cpp/cmake/linux-native-toolchain.cmake     # chainload：把处理器/编译器钉回本机真实值
```

预设里已经接好（`VCPKG_TARGET_TRIPLET=x64-linux-static`、`VCPKG_OVERLAY_TRIPLETS=cpp/triplets`），
`cmake --preset linux-make` 会自己找到它们。

## 3. Windows 实测（11 + MSVC 14.36 + vcpkg 2026-04-08）

| 项 | 数值 | 备注 |
|---|---|---|
| 首次 `cmake --preset windows` | **218 s** | 命中本机二进制缓存（`%LOCALAPPDATA%\vcpkg\archives`），装 89 个包 / 3.6 GB |
| 再次 configure | 20 s | 安装树已就绪，只做 CMake 探测 |
| 增量编译（改 `main.cpp`，单 TU + 静态链接） | 5.5 s | 静态链接不影响日常迭代速度 |
| `build/bin/Release/mdlake_cpp.exe` | 25,068,544 B（23.9 MiB） | 一个文件，拷走就能跑 |
| `build/bin/Debug/mdlake_cpp.exe` | 92.5 MB | 带 PDB，仅用于调试 |
| 运行期依赖（`dumpbin /dependents`） | `KERNEL32`、`USER32`、`WS2_32`、`CRYPT32`、`SHELL32`、`ole32`、`ADVAPI32`、`dbghelp` | **全是系统 DLL**，无 `msvcp140*.dll`、无 `arrow.dll` |

同一份数据（002322.SZ，2026-11-04，73,375 tick）：

```text
Release：239 bar / 开仓 1 次 / 点数收益 -0.98 / 回放 5.17 / 5.80 / 5.95 ms（跑三次）
Debug  ：239 bar / 开仓 1 次 / 点数收益 -0.98 / 回放 19.62 ms
```

回放耗时含进程启动与 footer 解析，同一份二进制来回跳 0.8 ms —— 报单次数字没有意义，报区间。

与第 10 章的 Python（Polars）复算结果逐位一致 —— 换依赖管理方式没有改变任何数值，
这正是要用 `C4` 那种指纹来兜底的原因。

## 4. 版本锁定：`builtin-baseline`

```json
"builtin-baseline": "d5cb5b9392b89fe9ad25786022415999202277d6"
```

这一行把整套 ports 树钉在 2025-03-11 的提交上 → 解出来的就是 **Arrow / Parquet 19.0.1**。
两个理由：

1. **可复现**：别人 clone 下来，装的和你一模一样的版本；不写 baseline 就跟着 vcpkg 最新 port 走，哪天 Arrow 换了 API 你的代码就编不过。
2. **能命中二进制缓存**：同 baseline + 同 triplet + 同 feature 集合 = 同一个 ABI 哈希，`archives/` 里的包直接解压，218 s 装完 89 个包。换 baseline 意味着 Arrow 从源码编一遍（本机量级：**30–60 分钟**，还要拉 openssl/thrift/boost 一堆源码）。

想升到当前 registry 的 Arrow（本机 vcpkg 是 24.0.0）：删掉 `builtin-baseline` 那行，
`rm -rf build vcpkg_installed`，重新 configure。第一次会从源码编，之后又有缓存了。
Arrow 24 的头文件用了 `std::span`，所以 `CMakeLists.txt` 里统一设 `C++20`（Arrow 19 用 17 也能编，但没必要为省一个标准版本留个坑）。

## 5. 三个必须写下来的坑

**（1）`LNK2038: MTd_StaticDebug vs MT_StaticRelease`**

vcpkg 静态 triplet 的库是 `/MT` 编的，可执行文件也得是 `/MT`，否则链接期直接失败。
`CMAKE_MSVC_RUNTIME_LIBRARY` 必须在 **`project()` 之后**设 —— 它在 `project()` 之前
`MSVC` 这个变量还没定义，`if(MSVC)` 整段被跳过，最后仍按默认 `/MD` 编译。

**（2）`snappy.lib` 被解析成 debug 版**

Arrow 19 自带的 `FindSnappyAlt.cmake` 只认 `Snappy::snappy-static`，而 vcpkg 导出的是
`Snappy::snappy`；认不出来就退回 `find_library()`，在 `x64-windows-static` 上会先捞到
`debug/lib/snappy.lib`（`/MTd`）。Release 链接于是报上面那条 `LNK2038`。

`CMakeLists.txt` 里补了 4 行垫片：先 `find_package(Snappy CONFIG)`，再给
`Snappy::snappy-static` 建一个 ALIAS。这样 Arrow 直接走 vcpkg 的 imported target，
Debug / Release 各自取对的库 —— 两套配置都实测通过。

**（3）Linux 上 Arrow 把 triplet 误判成交叉编译**（vcpkg 2026-04-08）

现象：`cmake --preset linux` 在 arrow 的配置阶段失败，报

```text
The CMAKE_CXX_COMPILER: x64-linux-gnu-g++  is not a full path and was not found in the PATH.
```

推理链是这样接上的：

```text
官方 triplets/x64-linux.cmake 里有 set(VCPKG_CMAKE_SYSTEM_NAME Linux)
        ↓  vcpkg 认为"指定了目标系统"，给每个端口传 -DCMAKE_SYSTEM_PROCESSOR=x64
        ↓  scripts/toolchains/linux.cmake 里 if(VCPKG_TARGET_ARCHITECTURE STREQUAL "x64")
           set(CMAKE_SYSTEM_PROCESSOR x86_64 CACHE STRING "") —— 命令行 -D 已占住 cache，覆盖不掉
        ↓  脚本比较 CMAKE_SYSTEM_PROCESSOR(x64) != CMAKE_HOST_SYSTEM_PROCESSOR(x86_64)
        ↓  判定为交叉编译，去 GNU_TRIPLET 里拼编译器：x64-linux-gnu-g++
```

而删掉那行 `VCPKG_CMAKE_SYSTEM_NAME` 也不行——vcpkg 会先报
`Use of Visual Studio's Developer Prompt is unsupported on non-Windows host`（它把没有系统名的 triplet 当 Windows 风格）。

两个约束互相咬死，解法是保留系统名、再用 **chainload toolchain** 覆盖掉错误推导：

```text
triplets/x64-linux-static.cmake     →  VCPKG_CMAKE_SYSTEM_NAME Linux + VCPKG_CHAINLOAD_TOOLCHAIN_FILE
cmake/linux-native-toolchain.cmake  →  find_program(gcc/g++) 后 set 回 CMAKE_CXX_COMPILER，顺便把
                                       CMAKE_SYSTEM_PROCESSOR 定成 x86_64
```

代价要提前知道：**triplet 名变了，ABI 哈希就变了**，`x64-linux` 下已装好的包全部作废，
必须全量重编（本机 35 分钟）。所以别在编完一半的时候改 triplet 名。

顺带一条更朴素的教训：vcpkg 的 ABI 里含 **vcpkg-tool 版本**。同一个端口树，换个
bootstrap 出来的 vcpkg 二进制，装过的包也会被判定为过期而重编。想复用安装树，就得连工具版本一起复用。

## 6. 清理与离线

```bash
rm -rf build vcpkg_installed          # 3.6 GB（Linux 1.4 GB）的依赖树和构建目录都可以随时删，重配即还原
du -sh vcpkg_installed
```

- 二进制缓存在 `%LOCALAPPDATA%\vcpkg\archives`（Linux：`~/.cache/vcpkg/archives`），删安装树不会丢缓存，重装仍是分钟级。
- 完全离线：把缓存目录整体拷到目标机器同样的位置，configure 就不会联网。

## 7. 用法（与之前一致）

```bash
mdlake_cpp <单合约单日的 parquet> [fast=20] [slow=60]
```

输出：tick 数、分钟 bar 数、开仓次数、点数收益、回放耗时。
