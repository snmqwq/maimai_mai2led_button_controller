#ifndef APP_MAI2LED_H
#define APP_MAI2LED_H

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

bool AppMai2Led_Init(void);
void AppMai2Led_Task(void);
void AppMai2Led_RestoreIdleLights(void);

#ifdef __cplusplus
}
#endif

#endif /* APP_MAI2LED_H */
