// SPDX-License-Identifier: Apache-2.0
// The model HAL under other names, behind the fake /dev/g2d of the Linux HAL host test (v2.0).
#define g2d_hal_open g2d_model_hal_open
#define g2d_hal_close g2d_model_hal_close
#include "../../hal/model/g2d_hal_model.cpp"
