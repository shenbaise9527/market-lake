# 由 triplets/x64-linux-static.cmake 通过 VCPKG_CHAINLOAD_TOOLCHAIN_FILE 加载。
#
# 唯一职责：把「本机原生编译」的事实钉死，压掉 vcpkg 默认 toolchain 从
# -DCMAKE_SYSTEM_PROCESSOR=x64 与 host 的 x86_64 之间推出的「交叉编译」结论。
# 不写具体路径，跟着 PATH 里的 gcc/g++ 走。
find_program(_MDLAKE_CC NAMES gcc REQUIRED)
find_program(_MDLAKE_CXX NAMES g++ REQUIRED)

set(CMAKE_SYSTEM_PROCESSOR x86_64)
set(CMAKE_C_COMPILER "${_MDLAKE_CC}")
set(CMAKE_CXX_COMPILER "${_MDLAKE_CXX}")

unset(_MDLAKE_CC)
unset(_MDLAKE_CXX)
