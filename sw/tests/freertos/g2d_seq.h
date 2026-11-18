// SPDX-License-Identifier: Apache-2.0
/* Drawing sequence shared by the FreeRTOS tests (host: test_freertos.c; Cheshire: sw/apps/freertos): clears and a
 * filled triangle per round on a W x H target, vg_lite_finish, CRC-32 of the target after every round. */
#ifndef G2D_SEQ_H
#define G2D_SEQ_H
#include <stdint.h>

typedef struct {
    int id;
    int rounds;
    uint32_t crc;
    int errors;
    volatile int done;
} g2d_seq_job_t;

void g2d_seq_run(g2d_seq_job_t *j);   /* calls g2d_seq_yield() between rounds if the program defines it */
void g2d_seq_yield(void) __attribute__((weak));   /* FreeRTOS programs: taskYIELD() */

#endif
