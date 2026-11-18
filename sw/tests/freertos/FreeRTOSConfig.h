// SPDX-License-Identifier: Apache-2.0
/* FreeRTOS configuration of the host test sw/tests/freertos/test_freertos.c (POSIX port, v2.0). */
#ifndef FREERTOS_CONFIG_H
#define FREERTOS_CONFIG_H

#define configUSE_PREEMPTION 1
#define configUSE_TIME_SLICING 1
#define configTICK_RATE_HZ 1000
#define configMAX_PRIORITIES 5
#define configMINIMAL_STACK_SIZE 4096
#define configTOTAL_HEAP_SIZE (64 * 1024 * 1024)
#define configMAX_TASK_NAME_LEN 16
#define configTICK_TYPE_WIDTH_IN_BITS TICK_TYPE_WIDTH_64_BITS
#define configUSE_MUTEXES 1
#define configUSE_RECURSIVE_MUTEXES 0
#define configUSE_COUNTING_SEMAPHORES 1
#define configSUPPORT_STATIC_ALLOCATION 1
#define configSUPPORT_DYNAMIC_ALLOCATION 1
#define configUSE_IDLE_HOOK 0
#define configUSE_TICK_HOOK 0
#define configUSE_TIMERS 0
#define configCHECK_FOR_STACK_OVERFLOW 0
#define configUSE_MALLOC_FAILED_HOOK 0
#define configKERNEL_PROVIDED_STATIC_MEMORY 1
#define INCLUDE_vTaskDelay 1
#define INCLUDE_vTaskDelete 1
#define INCLUDE_xTaskGetSchedulerState 1
#define INCLUDE_vTaskSuspend 1
#define configASSERT(x)                                                                                            \
    do {                                                                                                           \
        if (!(x)) {                                                                                                \
            extern void g2d_test_assert(const char *f, int l);                                                     \
            g2d_test_assert(__FILE__, __LINE__);                                                                   \
        }                                                                                                          \
    } while (0)

#endif
