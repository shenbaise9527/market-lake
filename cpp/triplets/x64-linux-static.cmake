# 本项目的 Linux 目标 triplet：x64、静态链接、本机原生编译。
#
# 为什么不直接用官方 triplets/x64-linux.cmake：
#   vcpkg 2026-04 起官方 x64-linux 是「静态 + 声明 VCPKG_CMAKE_SYSTEM_NAME Linux」。
#   声明了系统名，vcpkg 就会给每个端口传 -DCMAKE_SYSTEM_PROCESSOR=x64；
#   而 scripts/toolchains/linux.cmake 里是
#       if(VCPKG_TARGET_ARCHITECTURE STREQUAL "x64")
#         set(CMAKE_SYSTEM_PROCESSOR x86_64 CACHE STRING "")
#   命令行 -D 已经占了 cache，这个 set(... CACHE ...) 覆盖不掉，
#   于是 CMAKE_SYSTEM_PROCESSOR 停在 "x64"，与 host 的 "x86_64" 不等 → 被判定成交叉编译，
#   编译器被设成 x64-linux-gnu-g++（机器上没有），Arrow 配置阶段直接失败。
#
# 两个约束互相咬死：
#   · 不写 VCPKG_CMAKE_SYSTEM_NAME → vcpkg 认为这是 Windows 风格 triplet，报
#     "Use of Visual Studio's Developer Prompt is unsupported on non-Windows host"
#   · 写了 VCPKG_CMAKE_SYSTEM_NAME → 掉进上面的交叉编译误判
# 解法：保留系统名，再用 chainload toolchain 把处理器与编译器钉回本机真实值
# （chainload 在 vcpkg 默认 toolchain 之后 include，能覆盖掉错误推导）。
set(VCPKG_TARGET_ARCHITECTURE x64)
set(VCPKG_CRT_LINKAGE dynamic)
set(VCPKG_LIBRARY_LINKAGE static)

set(VCPKG_CMAKE_SYSTEM_NAME Linux)
set(VCPKG_CHAINLOAD_TOOLCHAIN_FILE "${CMAKE_CURRENT_LIST_DIR}/../cmake/linux-native-toolchain.cmake")
