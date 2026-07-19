#ifndef APP_HID_KEYBOARD_H
#define APP_HID_KEYBOARD_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define APP_HID_KEY_COUNT  13u

void AppHidKeyboard_Init(uint8_t const keycodes[APP_HID_KEY_COUNT]);
void AppHidKeyboard_SetKeycodes(
  uint8_t const keycodes[APP_HID_KEY_COUNT]);
void AppHidKeyboard_RequestReportFromISR(void);
void AppHidKeyboard_Task(void);

#ifdef __cplusplus
}
#endif

#endif /* APP_HID_KEYBOARD_H */
