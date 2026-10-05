# Unified Armory runtime.
#
#   Windows (the mod):   cmake -B build -A x64 && cmake --build build --config Release
#                        -> build/Release/version.dll
#   Any OS (core tests): cmake -B build && cmake --build build && ctest --test-dir build
cmake_minimum_required(VERSION 3.20)
project(UnifiedArmoryRuntime CXX C)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
# Static C runtime everywhere (MSVC): version.dll must not depend on a VC++ redistributable.
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")

include(FetchContent)
FetchContent_Declare(json URL https://github.com/nlohmann/json/releases/download/v3.11.3/json.tar.xz
                     DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
FetchContent_MakeAvailable(json)

add_library(ua_core STATIC src/core/bridge.cpp src/core/redirect.cpp src/core/selection.cpp src/core/runtime.cpp)
target_include_directories(ua_core PUBLIC src/core)
target_link_libraries(ua_core PUBLIC nlohmann_json::nlohmann_json)
set_property(TARGET ua_core PROPERTY POSITION_INDEPENDENT_CODE ON)

enable_testing()
add_executable(core_tests tests/core_tests.cpp)
target_link_libraries(core_tests PRIVATE ua_core)
add_test(NAME core_tests COMMAND core_tests)

if(WIN32)
  FetchContent_Declare(imgui GIT_REPOSITORY https://github.com/ocornut/imgui.git GIT_TAG v1.91.5)
  FetchContent_Declare(minhook GIT_REPOSITORY https://github.com/TsudaKageyu/minhook.git
                       GIT_TAG 8af6b4acae5a9388fd742b56fa79ece89d96f823)
  FetchContent_MakeAvailable(imgui minhook)

  add_library(imgui_dx11 STATIC
    ${imgui_SOURCE_DIR}/imgui.cpp ${imgui_SOURCE_DIR}/imgui_draw.cpp ${imgui_SOURCE_DIR}/imgui_tables.cpp
    ${imgui_SOURCE_DIR}/imgui_widgets.cpp
    ${imgui_SOURCE_DIR}/backends/imgui_impl_win32.cpp ${imgui_SOURCE_DIR}/backends/imgui_impl_dx11.cpp)
  target_include_directories(imgui_dx11 PUBLIC ${imgui_SOURCE_DIR} ${imgui_SOURCE_DIR}/backends)

  # Named version.dll: MCC loads it from its own folder, and it forwards the real API to the system copy.
  add_library(version SHARED
    src/win/dllmain.cpp src/win/proxy_version.cpp src/win/memory_win.cpp src/win/file_hooks.cpp
    src/win/overlay.cpp src/win/log.cpp src/win/version.def)
  target_link_libraries(version PRIVATE ua_core imgui_dx11 minhook d3d11 dxgi d3dcompiler dwmapi)
  target_compile_definitions(version PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX UNICODE _UNICODE)
  set_target_properties(version PROPERTIES OUTPUT_NAME version PREFIX "")
  # Stand-in MCC executable for tests/run_win_harness.sh; imports version.dll so the local copy loads.
  add_executable(win_harness tests/win_harness.cpp)
  target_link_libraries(win_harness PRIVATE version_import)
  add_library(version_import INTERFACE)
  target_link_libraries(version_import INTERFACE version)

  if(MINGW)
    target_link_options(win_harness PRIVATE -static -static-libgcc -static-libstdc++)
    target_link_options(version PRIVATE -static -static-libgcc -static-libstdc++)
  endif()
  if(MSVC)
    target_compile_options(version PRIVATE /W3 /utf-8)
  endif()
endif()
