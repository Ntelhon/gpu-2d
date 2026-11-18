// SPDX-License-Identifier: Apache-2.0
/* Reference for test_linux_hal: the shared drawing sequence (sw/tests/freertos/g2d_seq.c) on the model HAL directly;
 * prints one CRC per sequence id: seq_ref <ids> <rounds>. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "g2d_hal.h"
#include "g2d_seq.h"
#include "vg_lite.h"

int main(int argc, char **argv)
{
    int n = argc > 1 ? atoi(argv[1]) : 3, rounds = argc > 2 ? atoi(argv[2]) : 3;
    const g2d_hal_t *hal = g2d_hal_open();
    if (!hal || vg_lite_init(0, 0) != VG_LITE_SUCCESS)
        return 1;
    for (int i = 0; i < n; i++) {
        g2d_seq_job_t j;
        memset(&j, 0, sizeof j);
        j.id = i;
        j.rounds = rounds;
        g2d_seq_run(&j);
        printf("%08x\n", (unsigned)j.crc);
        if (j.errors)
            return 1;
    }
    vg_lite_close();
    g2d_hal_close(hal);
    return 0;
}
