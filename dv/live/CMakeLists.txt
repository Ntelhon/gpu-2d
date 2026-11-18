# SPDX-License-Identifier: Apache-2.0
# Live RTL backend (Phase 6, `make live`): g2d_top verilated with the dv/sva checkers, wrapped by the HAL in
# g2d_live.cpp, linked into the unchanged driver programs. Two configurations as the L3 benches (test_top):
#   dw64   DATA_W 64 (defaults)
#   dw128  DATA_W 128, KICK_DEPTH 8, MAX_BURST_BYTES 128, RD_OUTSTANDING 4, WR_OUTSTANDING 2 (ADDR_W 32)
# and, for performance measurements only (`make live-perf`, scripts/perf_lvgl.py):
#   dw128o8  DATA_W 128 with the default bursts and outstanding limits (MAX_BURST_BYTES 256, 8 / 8)
find_package(verilator 5.036 REQUIRED HINTS $ENV{VERILATOR_ROOT} $ENV{HOME}/tools/verilator)

set(RTL ${G2D_ROOT}/rtl)
set(LIVE_RTL ${RTL}/pkg/generated/g2d_csr_pkg.sv ${RTL}/pkg/generated/g2d_state_pkg.sv ${RTL}/pkg/generated/g2d_gamma_pkg.sv ${RTL}/pkg/g2d_pkg.sv
             ${RTL}/csr/generated/g2d_csr_regs.sv)
foreach(d common csr axi cmdproc fill blend blit raster gradient pix cdc top)   # dv/common/runner.py RTL_DIRS
  file(GLOB s ${RTL}/${d}/*.sv)
  list(SORT s)
  list(APPEND LIVE_RTL ${s})
endforeach()
file(GLOB SVA ${G2D_ROOT}/dv/sva/g2d_*_sva.sv)
list(SORT SVA)
list(APPEND LIVE_RTL ${SVA} ${G2D_ROOT}/dv/sva/g2d_binds.sv)

set(CFG_dw64 -GDATA_W=64)
set(CFG_dw128 -GDATA_W=128 -GKICK_DEPTH=8 -GMAX_BURST_BYTES=128 -GRD_OUTSTANDING=4 -GWR_OUTSTANDING=2)
set(CFG_dw128o8 -GDATA_W=128)
set(DW_dw64 64)
set(DW_dw128 128)
set(DW_dw128o8 128)

foreach(cfg dw64 dw128 dw128o8)
  add_library(g2d_hal_live_${cfg} STATIC ${CMAKE_CURRENT_SOURCE_DIR}/g2d_live.cpp)
  target_include_directories(g2d_hal_live_${cfg} PRIVATE ${G2D_ROOT}/sw/hal ${G2D_ROOT}/sw/hal/model ${G2D_GEN_INC}/..)
  target_compile_definitions(g2d_hal_live_${cfg} PRIVATE G2D_LIVE_DATA_W=${DW_${cfg}})
  verilate(g2d_hal_live_${cfg} SOURCES ${LIVE_RTL} TOP_MODULE g2d_top PREFIX Vg2d_top
           DIRECTORY ${CMAKE_CURRENT_BINARY_DIR}/verilated_${cfg}
           VERILATOR_ARGS -Wall ${RTL}/lint.vlt ${G2D_ROOT}/dv/sva/sva.vlt -O3 --x-assign unique --x-initial unique
                          --assert --timescale 1ns/1ps ${CFG_${cfg}})

  add_executable(vgl_scene_live_${cfg} ${G2D_ROOT}/sw/tests/vgl_scene.c)
  target_link_libraries(vgl_scene_live_${cfg} vg_lite g2d_hal_live_${cfg} m)
  set_target_properties(vgl_scene_live_${cfg} PROPERTIES LINKER_LANGUAGE CXX)

  add_executable(test_driver_live_${cfg} ${G2D_ROOT}/sw/tests/test_driver.c)
  target_link_libraries(test_driver_live_${cfg} vg_lite g2d_hal_live_${cfg} m)
  set_target_properties(test_driver_live_${cfg} PROPERTIES LINKER_LANGUAGE CXX)

  if(G2D_BUILD_LVGL)
    add_executable(lvgl_demo_live32_${cfg} ${G2D_ROOT}/sw/apps/lvgl/main.c)
    target_link_libraries(lvgl_demo_live32_${cfg} lvgl_vg32 vg_lite g2d_hal_live_${cfg} m)
    set_target_properties(lvgl_demo_live32_${cfg} PROPERTIES LINKER_LANGUAGE CXX POSITION_INDEPENDENT_CODE OFF)
    target_link_options(lvgl_demo_live32_${cfg} PRIVATE -no-pie)
    target_compile_options(lvgl_demo_live32_${cfg} PRIVATE -Wall -Wextra -fno-pie)
  endif()
endforeach()
