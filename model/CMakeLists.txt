# SPDX-License-Identifier: Apache-2.0
cmake_minimum_required(VERSION 3.20)
project(g2d_model LANGUAGES C CXX)

# Determinism: no FMA contraction, no fast-math (spec/rendering.md §9.1).
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()
set(G2D_WARN -Wall -Wextra -Werror -ffp-contract=off)

if(NOT DEFINED G2D_ROOT)  # repository root; overridable for out-of-tree copies (mutation tests)
  get_filename_component(G2D_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/.." ABSOLUTE)
endif()
set(G2D_GEN_INC "${G2D_ROOT}/sw/include/generated")
if(NOT EXISTS "${G2D_GEN_INC}/g2d_regs.h")
  message(FATAL_ERROR "g2d_regs.h missing: run `make regs` first")
endif()

# Shared C99 driver geometry (spec/rendering.md §9), used by the driver and the model harness.
add_library(g2d_geom STATIC ${G2D_ROOT}/sw/lib/geom/g2d_geom.c)
target_include_directories(g2d_geom PUBLIC ${G2D_ROOT}/sw/lib/geom)
target_compile_options(g2d_geom PRIVATE ${G2D_WARN} -pedantic)

# Golden model.
add_library(g2d_model STATIC src/ops.cpp src/raster.cpp src/gpu.cpp)
target_include_directories(g2d_model PUBLIC include ${G2D_GEN_INC})
target_compile_options(g2d_model PRIVATE ${G2D_WARN})

# C++ unit tests.
enable_testing()
add_executable(test_ops tests/test_ops.cpp)
target_link_libraries(test_ops g2d_model)
target_compile_options(test_ops PRIVATE ${G2D_WARN})
add_test(NAME test_ops COMMAND test_ops)

add_executable(test_geom tests/test_geom.c)
target_link_libraries(test_geom g2d_geom m)
target_compile_options(test_geom PRIVATE ${G2D_WARN})
add_test(NAME test_geom COMMAND test_geom)

# Python bindings (pybind11 from the active Python environment).
find_package(Python3 COMPONENTS Interpreter Development.Module REQUIRED)

# Software stack: vg_lite driver, HAL backends, driver tests (sw/CMakeLists.txt).
add_subdirectory(${G2D_ROOT}/sw ${CMAKE_BINARY_DIR}/sw)
execute_process(COMMAND ${Python3_EXECUTABLE} -m pybind11 --cmakedir
                OUTPUT_VARIABLE pybind11_DIR OUTPUT_STRIP_TRAILING_WHITESPACE)
find_package(pybind11 CONFIG REQUIRED)
pybind11_add_module(g2d_model_py bindings/g2d_model.cpp)
set_target_properties(g2d_model_py PROPERTIES OUTPUT_NAME g2d_model)
target_link_libraries(g2d_model_py PRIVATE g2d_model g2d_geom)
target_compile_options(g2d_model_py PRIVATE -Wall -Wextra -ffp-contract=off)
