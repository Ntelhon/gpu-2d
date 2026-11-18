// SPDX-License-Identifier: Apache-2.0
/* FreeRTOS configuration of the G2D FreeRTOS test on Cheshire (CVA6, RV64, v2.0; sw/apps/freertos/main_chs.c).
 * Timer: CLINT mtime / mtimecmp of hart 0 (Cheshire address map: CLINT at 0x0204_0000), mtime at the 1 MHz RTC
 * (Cheshire RtcFreq on the Genesys2 and in the SoC simulation); the RISC-V port takes configCPU_CLOCK_HZ as the
 * mtime rate. */
#ifndef FREERTOS_CONFIG_H
#define FREERTOS_CONFIG_H

#define configMTIME_BASE_ADDRESS (0x02040000UL + 0xBFF8UL)
#define configMTIMECMP_BASE_ADDRESS (0x02040000UL + 0x4000UL)
#define configCPU_CLOCK_HZ 1000000UL
#define configTICK_RATE_HZ 1000
#define configISR_STACK_SIZE_WORDS 2048
#define configENABLE_FPU 1          /* the driver and the geometry library use double: FP registers per task */
#define configUSE_PREEMPTION 1
#define configUSE_TIME_SLICING 1
#define configMAX_PRIORITIES 5
#define configMINIMAL_STACK_SIZE 1024
#define configTOTAL_HEAP_SIZE (200 * 1024)   /* .bss: crt0 zeroes it word by word (SoC simulation time) */
#define configMAX_TASK_NAME_LEN 16
#define configTICK_TYPE_WIDTH_IN_BITS TICK_TYPE_WIDTH_64_BITS
#define configUSE_MUTEXES 1
#define configUSE_RECURSIVE_MUTEXES 0
#define configUSE_COUNTING_SEMAPHORES 1
#define configSUPPORT_STATIC_ALLOCATION 1
#define configSUPPORT_DYNAMIC_ALLOCATION 1
#define configKERNEL_PROVIDED_STATIC_MEMORY 1
#define configUSE_IDLE_HOOK 0
#define configUSE_TICK_HOOK 0
#define configUSE_TIMERS 0
#define configCHECK_FOR_STACK_OVERFLOW 0
#define configUSE_MALLOC_FAILED_HOOK 0
#define INCLUDE_vTaskDelay 1
#define INCLUDE_vTaskDelete 1
#define INCLUDE_xTaskGetSchedulerState 1
#define configASSERT(x)                                                                                            \
    do {                                                                                                           \
        if (!(x)) {                                                                                                \
            extern void g2d_rtos_assert(const char *f, int l);                                                     \
            g2d_rtos_assert(__FILE__, __LINE__);                                                                   \
        }                                                                                                          \
    } while (0)

#endif
