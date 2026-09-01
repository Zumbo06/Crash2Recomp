cmake_minimum_required(VERSION 3.20)
set(PSXRECOMP_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/psxrecomp")
if(NOT EXISTS "${PSXRECOMP_ROOT}/runtime/runtime.cmake")
  message(FATAL_ERROR
    "PSXRecomp runtime is missing: ${PSXRECOMP_ROOT}/runtime/runtime.cmake\n"
    "This generated project needs the psxrecomp framework tree at ${PSXRECOMP_ROOT}.\n"
    "If this project came from git, run: git submodule update --init --recursive\n"
    "If this project came from psxrecomp.exe, regenerate it from the full CLI zip and keep the generated psxrecomp/ folder.")
endif()
project(Crash_Bandicoot_2Recomp C CXX)
set(CMAKE_C_STANDARD 99)
set(CMAKE_CXX_STANDARD 17)
set(PSX_RECOMP_UI OFF CACHE BOOL "" FORCE)
set(PSXRECOMP_BIOS_STEMS "OpenBIOS" CACHE STRING "" FORCE)
include("${PSXRECOMP_ROOT}/runtime/runtime.cmake")
file(GLOB GAME_FULL CONFIGURE_DEPENDS
  "${CMAKE_CURRENT_SOURCE_DIR}/generated/*_full.c"
  "${CMAKE_CURRENT_SOURCE_DIR}/generated/*_full_*.c")
file(GLOB GAME_DISPATCH "${CMAKE_CURRENT_SOURCE_DIR}/generated/*_dispatch.c")
psxrecomp_add_runtime_target(psx-runtime
  GAME_GENERATED_FULL_C "${GAME_FULL}"
  GAME_GENERATED_DISPATCH_C "${GAME_DISPATCH}"
  WINDOW_TITLE "Crash Bandicoot 2 Recompiled"
  DEFAULT_GAME_CONFIG_PATH "game.toml"
)
