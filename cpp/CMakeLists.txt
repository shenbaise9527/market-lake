cmake_minimum_required(VERSION 3.21)

# =============================================================================
# 依赖一律由 vcpkg 清单模式（manifest）供给：
#   cpp/vcpkg.json        -> 依赖清单：arrow[compute,dataset] + parquet，builtin-baseline 锁版本
#   cpp/CMakePresets.json -> Windows / Linux 两套 triplet，并把依赖树指向 cpp/vcpkg_installed
#   cpp/triplets/         -> Linux 自定义 triplet（官方 x64-linux 会被误判成交叉编译，见 build.md §5）
# 首次 configure 时 vcpkg 自动安装，不需要先手工 vcpkg install。
# （不用预设时安装树默认落在 <build>/vcpkg_installed，预设则统一到源码目录，便于复用）
# =============================================================================

# --- 工具链：没用 --preset 时退回读环境变量 VCPKG_ROOT -------------------------
if(NOT DEFINED CMAKE_TOOLCHAIN_FILE AND DEFINED ENV{VCPKG_ROOT})
  set(CMAKE_TOOLCHAIN_FILE "$ENV{VCPKG_ROOT}/scripts/buildsystems/vcpkg.cmake"
      CACHE STRING "vcpkg toolchain file")
endif()
if(NOT DEFINED CMAKE_TOOLCHAIN_FILE)
  message(FATAL_ERROR
    "找不到 vcpkg 工具链。二选一：\n"
    "  1) 用预设：cmake --preset windows  /  cmake --preset linux\n"
    "  2) 设置环境变量 VCPKG_ROOT 指向 vcpkg 目录，或显式传 "
    "-DCMAKE_TOOLCHAIN_FILE=<vcpkg>/scripts/buildsystems/vcpkg.cmake")
endif()

# --- triplet：Windows 静态（单 exe 免 DLL），Linux 默认动态 --------------------
# 必须在 project() 之前设定：toolchain 文件在 project() 期间执行。
# 注意此时 project() 还没跑，WIN32/MSVC 都未定义，只能用 CMAKE_HOST_WIN32。
if(CMAKE_HOST_WIN32)
  set(_mdlake_triplet x64-windows-static)
else()
  set(_mdlake_triplet x64-linux)
endif()
if(NOT DEFINED VCPKG_TARGET_TRIPLET)
  set(VCPKG_TARGET_TRIPLET "${_mdlake_triplet}" CACHE STRING "vcpkg target triplet")
endif()
if(NOT DEFINED VCPKG_HOST_TRIPLET)
  set(VCPKG_HOST_TRIPLET "${_mdlake_triplet}" CACHE STRING "vcpkg host triplet")
endif()

project(mdlake_cpp LANGUAGES CXX)

# x64-windows-static 里的库是 /MT 编的，可执行文件必须是 /MT，否则链接期
# 报 LNK2038 "RuntimeLibrary 不匹配 MT_StaticRelease vs MD_DynamicRelease"。
# 必须放在 project() 之后：MSVC 这个变量要等语言启用后才有定义。
# CMP0091 NEW（CMake>=3.15）下该变量才会真正落到 MSVC_RUNTIME_LIBRARY。
if(MSVC)
  set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
endif()

set(CMAKE_CXX_STANDARD 20)          # Arrow 16+ 头文件用到 std::span，17 编不过
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

# ---- Snappy 兼容垫片（Arrow 19 的已知坑，vcpkg 静态 triplet 上才会犯） --------
# Arrow 自带的 FindSnappyAlt.cmake 只认 Snappy::snappy-static 这个名字，而 vcpkg
# 导出的是 Snappy::snappy；认不出来就退回 find_library()，在 x64-windows-static
# 上会先捞到 debug/lib/snappy.lib（/MTd），Release 链接立刻报
#   LNK2038: RuntimeLibrary 不匹配 MTd_StaticDebug vs MT_StaticRelease
# 给它补一个别名，Arrow 就会走 vcpkg 的 imported target，Debug/Release 各自正确。
find_package(Snappy CONFIG QUIET)
if(TARGET Snappy::snappy AND NOT TARGET Snappy::snappy-static)
  add_library(Snappy::snappy-static ALIAS Snappy::snappy)
endif()

find_package(Arrow CONFIG REQUIRED)
find_package(Parquet CONFIG REQUIRED)

# vcpkg 静态 triplet 导出 Arrow::arrow_static / Parquet::parquet_static；
# 动态 triplet 与 conda-forge 导出 *_shared。两种命名都接住，换 triplet 不用改脚本。
set(_mdlake_arrow "")
foreach(_cand Arrow::arrow_static Arrow::arrow_shared arrow)
  if(TARGET ${_cand})
    set(_mdlake_arrow ${_cand})
    break()
  endif()
endforeach()

set(_mdlake_parquet "")
foreach(_cand Parquet::parquet_static Parquet::parquet_shared parquet)
  if(TARGET ${_cand})
    set(_mdlake_parquet ${_cand})
    break()
  endif()
endforeach()

if(NOT _mdlake_arrow OR NOT _mdlake_parquet)
  message(FATAL_ERROR "找到了 Arrow/Parquet 的 CMake 配置，但没有可链接的 target")
endif()
message(STATUS "vcpkg triplet : ${VCPKG_TARGET_TRIPLET}")
message(STATUS "link          : ${_mdlake_parquet} ${_mdlake_arrow}")

add_executable(mdlake_cpp main.cpp)
target_link_libraries(mdlake_cpp PRIVATE ${_mdlake_parquet} ${_mdlake_arrow})

if(MSVC)
  target_compile_options(mdlake_cpp PRIVATE /utf-8)   # 源码含中文，缺它会乱码
  target_compile_definitions(mdlake_cpp PRIVATE _CRT_SECURE_NO_WARNINGS NOMINMAX)
else()
  target_compile_options(mdlake_cpp PRIVATE -O2 -Wall -Wextra -Wno-unused-parameter)
endif()

# 产物落在 build/bin 下。多配置生成器（VS）会再套一层配置名：
#   build/bin/Release/mdlake_cpp.exe、build/bin/Debug/mdlake_cpp.exe
# 不要抹平这一层——否则 Debug 会静默覆盖 Release 的产物。
set_target_properties(mdlake_cpp PROPERTIES
  RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")
