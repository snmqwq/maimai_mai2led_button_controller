#ifndef APP_BUTTONS_H
#define APP_BUTTONS_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define APP_BUTTON_COUNT       13u
#define APP_BUTTON_MAIN_COUNT   8u
#define APP_BUTTON_AUX_COUNT    5u

void AppButtons_Init(void);
void AppButtons_Task(void);
uint8_t AppButtons_IsStarted(uint8_t button_id);
uint8_t AppButtons_IsPressed(uint8_t button_id);
uint16_t AppButtons_GetPressedMask(void);

#ifdef __cplusplus
}
#endif

#endif /* APP_BUTTONS_H */
