# SPDX-License-Identifier: Apache-2.0
# G2D software stack (Phase 6): vg_lite driver, HAL backends, driver tests. Built from model/CMakeLists.txt
# (`make model`), which provides g2d_geom, g2d_model and G2D_ROOT.

# vg_lite driver (C99, deterministic floating point as sw/lib/geom, rendering.md §9.1)
set(VGL_SRC ${G2D_ROOT}/sw/driver/vg_lite)
file(GLOB VGL_DRV_C ${VGL_SRC}/drv_*.c)
set(VGL_STUBS ${CMAKE_CURRENT_BINARY_DIR}/generated/vg_lite_stubs.c)
add_custom_command(
  OUTPUT ${VGL_STUBS}
  COMMAND ${Python3_EXECUTABLE} ${G2D_ROOT}/scripts/gen_vglite_stubs.py ${G2D_ROOT}/sw/include/vg_lite.h
          ${VGL_STUBS} ${VGL_DRV_C}
  DEPENDS ${G2D_ROOT}/scripts/gen_vglite_stubs.py ${G2D_ROOT}/sw/include/vg_lite.h ${VGL_DRV_C}
  COMMENT "vg_lite stubs for functions the driver does not implement")
add_library(vg_lite STATIC ${VGL_DRV_C} ${VGL_STUBS})
target_include_directories(vg_lite PUBLIC ${G2D_ROOT}/sw/include ${G2D_ROOT}/sw/hal PRIVATE ${VGL_SRC})
target_link_libraries(vg_lite PUBLIC g2d_geom)
target_compile_options(vg_lite PRIVATE -Wall -Wextra -Werror -pedantic -ffp-contract=off)
set_source_files_properties(${VGL_STUBS} PROPERTIES COMPILE_OPTIONS "-Wno-unused-parameter")

# HAL: software backend over the golden model
add_library(g2d_hal_model STATIC ${G2D_ROOT}/sw/hal/model/g2d_hal_model.cpp)
target_include_directories(g2d_hal_model PUBLIC ${G2D_ROOT}/sw/hal ${G2D_ROOT}/sw/hal/model ${G2D_ROOT}/sw/include)
target_link_libraries(g2d_hal_model PUBLIC g2d_model)
target_compile_options(g2d_hal_model PRIVATE -Wall -Wextra -Werror -ffp-contract=off)

# Driver unit tests on the software backend (ctest)
add_executable(test_driver ${G2D_ROOT}/sw/tests/test_driver.c)
target_link_libraries(test_driver vg_lite g2d_hal_model m)
target_compile_options(test_driver PRIVATE -Wall -Wextra -Werror -ffp-contract=off)
set_target_properties(test_driver PROPERTIES LINKER_LANGUAGE CXX)
add_test(NAME test_driver COMMAND test_driver)

# The driver with its non-default build options (v1.6b, rendering.md §9.10): strict Gaussian weights, OPENVG_* formats
# compiled out; checked by test_driver_opts
add_library(vg_lite_opts STATIC ${VGL_DRV_C} ${VGL_STUBS})
target_include_directories(vg_lite_opts PUBLIC ${G2D_ROOT}/sw/include ${G2D_ROOT}/sw/hal PRIVATE ${VGL_SRC})
target_link_libraries(vg_lite_opts PUBLIC g2d_geom)
target_compile_options(vg_lite_opts PRIVATE -Wall -Wextra -Werror -pedantic -ffp-contract=off)
target_compile_definitions(vg_lite_opts PRIVATE G2D_DRV_STRICT_GAUSSIAN=1 G2D_DRV_OPENVG_FORMATS=0)
add_executable(test_driver_opts ${G2D_ROOT}/sw/tests/test_driver_opts.c)
target_link_libraries(test_driver_opts vg_lite_opts g2d_hal_model m)
target_compile_options(test_driver_opts PRIVATE -Wall -Wextra -Werror -ffp-contract=off)
set_target_properties(test_driver_opts PROPERTIES LINKER_LANGUAGE CXX)
add_test(NAME test_driver_opts COMMAND test_driver_opts)

# Driver robustness (v1.6, D-1..D-4) with the model HAL's test controls
add_executable(test_robust ${G2D_ROOT}/sw/tests/test_robust.c)
target_link_libraries(test_robust vg_lite g2d_hal_model m)
target_compile_options(test_robust PRIVATE -Wall -Wextra -Werror -ffp-contract=off)
set_target_properties(test_robust PROPERTIES LINKER_LANGUAGE CXX)
add_test(NAME test_robust COMMAND test_robust)

# Thread safety of the driver API (v2.0, C-1): pthread lock hooks, concurrent drawing threads
find_package(Threads REQUIRED)
add_executable(test_threads ${G2D_ROOT}/sw/tests/test_threads.c)
target_link_libraries(test_threads vg_lite g2d_hal_model m Threads::Threads)
target_compile_options(test_threads PRIVATE -Wall -Wextra -Werror -ffp-contract=off)
set_target_properties(test_threads PROPERTIES LINKER_LANGUAGE CXX)
add_test(NAME test_threads COMMAND test_threads)

# Linux HAL on the host (v2.0): the vg_lite driver + sw/hal/linux over a fake /dev/g2d (the kernel driver's semantics
# over the golden model), compared with the same sequence on the model HAL (seq_ref)
add_executable(seq_ref ${G2D_ROOT}/sw/tests/linux/seq_ref.c ${G2D_ROOT}/sw/tests/freertos/g2d_seq.c)
target_include_directories(seq_ref PRIVATE ${G2D_ROOT}/sw/tests/freertos)
target_link_libraries(seq_ref vg_lite g2d_hal_model m)
set_target_properties(seq_ref PROPERTIES LINKER_LANGUAGE CXX)
add_executable(test_linux_hal ${G2D_ROOT}/sw/tests/linux/test_linux_hal.c ${G2D_ROOT}/sw/tests/linux/fake_g2d.c
  ${G2D_ROOT}/sw/tests/linux/model_hal_renamed.cpp ${G2D_ROOT}/sw/hal/linux/g2d_hal_linux.c
  ${G2D_ROOT}/sw/tests/freertos/g2d_seq.c)
target_compile_definitions(test_linux_hal PRIVATE G2D_LINUX_FAKE)
target_include_directories(test_linux_hal PRIVATE ${G2D_ROOT}/sw/linux ${G2D_ROOT}/sw/tests/linux
  ${G2D_ROOT}/sw/tests/freertos ${G2D_ROOT}/sw/hal/model ${G2D_ROOT}/sw/include)
target_link_libraries(test_linux_hal vg_lite g2d_model m Threads::Threads)
target_compile_options(test_linux_hal PRIVATE -Wall -Wextra)
add_test(NAME test_linux_hal COMMAND sh -c "$<TARGET_FILE:test_linux_hal> $($<TARGET_FILE:seq_ref> 3 3 | tr '\\n' ' ')")

# FreeRTOS layer on the host (v2.0): FreeRTOS POSIX port + model HAL; needs scripts/fetch_freertos.sh
if(NOT G2D_FREERTOS_DIR)
  set(G2D_FREERTOS_DIR $ENV{HOME}/tools/src/FreeRTOS-Kernel-V11.3.3)
endif()
if(EXISTS ${G2D_FREERTOS_DIR}/tasks.c)
  set(RTOS ${G2D_FREERTOS_DIR})
  add_executable(test_freertos ${G2D_ROOT}/sw/tests/freertos/test_freertos.c ${G2D_ROOT}/sw/tests/freertos/g2d_seq.c
    ${G2D_ROOT}/sw/hal/freertos/g2d_freertos.c
    ${RTOS}/tasks.c ${RTOS}/list.c ${RTOS}/queue.c ${RTOS}/portable/MemMang/heap_3.c
    ${RTOS}/portable/ThirdParty/GCC/Posix/port.c ${RTOS}/portable/ThirdParty/GCC/Posix/utils/wait_for_event.c)
  target_include_directories(test_freertos PRIVATE ${G2D_ROOT}/sw/tests/freertos ${G2D_ROOT}/sw/hal/freertos
    ${RTOS}/include ${RTOS}/portable/ThirdParty/GCC/Posix ${RTOS}/portable/ThirdParty/GCC/Posix/utils)
  target_link_libraries(test_freertos vg_lite g2d_hal_model m Threads::Threads)
  set_target_properties(test_freertos PROPERTIES LINKER_LANGUAGE CXX)
  add_test(NAME test_freertos COMMAND test_freertos)
else()
  message(STATUS "FreeRTOS kernel not found (${G2D_FREERTOS_DIR}): test_freertos skipped (scripts/fetch_freertos.sh)")
endif()

# Scene runner for the API scene tests (sw/tests/test_scenes.py)
add_executable(vgl_scene ${G2D_ROOT}/sw/tests/vgl_scene.c)
target_link_libraries(vgl_scene vg_lite g2d_hal_model m)
target_compile_options(vgl_scene PRIVATE -Wall -Wextra -Werror -ffp-contract=off)
set_target_properties(vgl_scene PROPERTIES LINKER_LANGUAGE CXX)

# LVGL host demos (Phase 6; `make lvgl`): four variants, VG-Lite draw unit on this driver (vg) or LVGL's
# software renderer (sw), 32- or 16-bit colour. LVGL's bundled vendor VG-Lite driver directory
# (src/libs/vg_lite_driver) is never compiled (independent implementation, docs/PROJECT_RULES.md).
option(G2D_BUILD_LVGL "Build the LVGL host demos" OFF)
if(G2D_BUILD_LVGL)
  if(NOT DEFINED G2D_LVGL_DIR)
    set(G2D_LVGL_DIR $ENV{HOME}/tools/src/lvgl-9.6.0)
  endif()
  if(NOT EXISTS ${G2D_LVGL_DIR}/lvgl.h)
    message(FATAL_ERROR "LVGL not found at ${G2D_LVGL_DIR}: run scripts/fetch_lvgl.sh")
  endif()
  file(GLOB_RECURSE LV_SRC ${G2D_LVGL_DIR}/src/*.c ${G2D_LVGL_DIR}/src/libs/thorvg/*.cpp
       ${G2D_LVGL_DIR}/src/debugging/vg_lite_tvg/*.cpp)
  list(FILTER LV_SRC EXCLUDE REGEX "/src/libs/vg_lite_driver/")
  file(GLOB_RECURSE LV_DEMO ${G2D_LVGL_DIR}/demos/widgets/*.c ${G2D_LVGL_DIR}/demos/benchmark/*.c
       ${G2D_LVGL_DIR}/demos/vector_graphic/*.c)
  list(APPEND LV_DEMO ${G2D_LVGL_DIR}/demos/lv_demos.c)
  # vg: VG-Lite draw unit on our driver; sw: LVGL's software renderer; tv: VG-Lite draw unit on LVGL's own vg_lite
  # simulator over ThorVG (src/debugging/vg_lite_tvg, MIT; a third-party reading of the vg_lite API)
  foreach(v vg32 vg16 sw32 sw16 tv32)
    string(SUBSTRING ${v} 0 2 kind)
    string(SUBSTRING ${v} 2 2 depth)
    if(kind STREQUAL "sw")
      set(vgdef 0)
    else()
      set(vgdef 1)
    endif()
    add_library(lvgl_${v} STATIC ${LV_SRC} ${LV_DEMO})
    target_include_directories(lvgl_${v} PUBLIC ${G2D_LVGL_DIR} ${G2D_LVGL_DIR}/include ${G2D_ROOT}/sw/apps/lvgl
                               ${G2D_ROOT}/sw/include)
    target_compile_definitions(lvgl_${v} PUBLIC LV_CONF_INCLUDE_SIMPLE G2D_LV_VG=${vgdef} G2D_LV_DEPTH=${depth})
    if(kind STREQUAL "tv")    # LVGL's simulator header has the error code the compat header adds for ours
      target_compile_options(lvgl_${v} PRIVATE -O2 -w)
    else()
      target_compile_options(lvgl_${v} PRIVATE -O2 -w -include ${G2D_ROOT}/sw/apps/lvgl/g2d_lvgl_compat.h)
    endif()
    add_executable(lvgl_demo_${v} ${G2D_ROOT}/sw/apps/lvgl/main.c)
    if(kind STREQUAL "tv")
      target_compile_definitions(lvgl_${v} PUBLIC G2D_LV_TVG=1)
      target_link_libraries(lvgl_demo_${v} lvgl_${v} m pthread)
    else()
      target_link_libraries(lvgl_demo_${v} lvgl_${v} vg_lite g2d_hal_model m)
    endif()
    set_target_properties(lvgl_demo_${v} PROPERTIES LINKER_LANGUAGE CXX POSITION_INDEPENDENT_CODE OFF)
    target_link_options(lvgl_demo_${v} PRIVATE -no-pie)
    target_compile_options(lvgl_demo_${v} PRIVATE -Wall -Wextra -fno-pie)
  endforeach()
endif()

# DOOM host harness (side project DOOM, step D1; `make doom-host`): doomgeneric fetched by scripts/fetch_doom.sh into
# build/ext (GPL-2.0, not committed), our platform layer sw/apps/doom (GPL-2.0-or-later), golden-model HAL.
option(G2D_BUILD_DOOM "Build the DOOM host harness" OFF)
if(G2D_BUILD_DOOM)
  set(G2D_DOOM_DIR ${G2D_ROOT}/build/ext/doomgeneric/doomgeneric)
  if(NOT EXISTS ${G2D_DOOM_DIR}/doomgeneric.h)
    message(FATAL_ERROR "doomgeneric not found at ${G2D_DOOM_DIR}: run scripts/fetch_doom.sh")
  endif()
  # the engine files of doomgeneric's Makefile, without its platform layers (doomgeneric_*.c)
  set(DOOM_ENGINE dummy am_map doomdef doomstat dstrings d_event d_items d_iwad d_loop d_main d_mode d_net f_finale
      f_wipe g_game hu_lib hu_stuff info i_cdmus i_endoom i_joystick i_scale i_sound i_system i_timer memio m_argv
      m_bbox m_cheat m_config m_controls m_fixed m_menu m_misc m_random p_ceilng p_doors p_enemy p_floor p_inter
      p_lights p_map p_maputl p_mobj p_plats p_pspr p_saveg p_setup p_sight p_spec p_switch p_telept p_tick p_user
      r_bsp r_data r_draw r_main r_plane r_segs r_sky r_things sha1 sounds statdump st_lib st_stuff s_sound tables
      v_video wi_stuff w_checksum w_file w_main w_wad z_zone w_file_stdc i_input i_video doomgeneric)
  list(TRANSFORM DOOM_ENGINE PREPEND ${G2D_DOOM_DIR}/)
  list(TRANSFORM DOOM_ENGINE APPEND .c)
  add_library(doom_engine STATIC ${DOOM_ENGINE})
  target_include_directories(doom_engine PUBLIC ${G2D_DOOM_DIR})
  target_compile_definitions(doom_engine PUBLIC CMAP256 DOOMGENERIC_RESX=320 DOOMGENERIC_RESY=200 NORMALUNIX LINUX
                             _DEFAULT_SOURCE)
  target_compile_options(doom_engine PRIVATE -O2 -w)
  add_executable(test_doom_keys ${G2D_ROOT}/sw/tests/test_doom_keys.c ${G2D_ROOT}/sw/apps/doom/doom_keys.c)
  target_include_directories(test_doom_keys PRIVATE ${G2D_ROOT}/sw/apps/doom ${G2D_DOOM_DIR})
  target_compile_options(test_doom_keys PRIVATE -O2 -Wall -Wextra -Werror)
  add_executable(doom_host ${G2D_ROOT}/sw/apps/doom/doom_host.c ${G2D_ROOT}/sw/apps/doom/doom_g2d.c
                 ${G2D_ROOT}/sw/apps/doom/doom_keys.c)
  target_include_directories(doom_host PRIVATE ${G2D_ROOT}/sw/apps/doom ${G2D_ROOT}/sw/include)
  target_link_libraries(doom_host doom_engine vg_lite g2d_hal_model m)
  set_target_properties(doom_host PROPERTIES LINKER_LANGUAGE CXX)
  target_compile_options(doom_host PRIVATE -O2 -Wall -Wextra)
endif()

# Live RTL backend (`make live`): the driver programs on a Verilator model of g2d_top (dv/live)
option(G2D_BUILD_LIVE "Build the live RTL backend (Verilator)" OFF)
if(G2D_BUILD_LIVE)
  add_subdirectory(${G2D_ROOT}/dv/live ${CMAKE_BINARY_DIR}/live)
endif()
