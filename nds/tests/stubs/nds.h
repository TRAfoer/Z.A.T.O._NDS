#ifndef TEST_NDS_H
#define TEST_NDS_H
#include <stdbool.h>
#include <stdint.h>
#ifndef TIMER_DATA
static inline volatile uint16_t *test_timer_data(void) { static volatile uint16_t data[4]; return data; }
static inline volatile uint16_t *test_timer_control(void) { static volatile uint16_t control[4]; return control; }
#define TIMER_DATA(n) (test_timer_data()[n])
#define TIMER_CR(n) (test_timer_control()[n])
#define TIMER_ENABLE 128u
#define TIMER_DIV_1024 3u
#define BUS_CLOCK 33513982u
#endif
static inline void soundEnable(void) {}
#endif
