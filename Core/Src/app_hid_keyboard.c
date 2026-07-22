#include "app_hid_keyboard.h"

#include <string.h>

#include "app_buttons.h"
#include "app_config.h"
#include "tusb.h"

#define APP_HID_KEYBOARD_INSTANCE  1u
#define APP_HID_REPORT_SIZE  (2u + APP_HID_KEY_COUNT)

static uint8_t app_hid_keycodes[APP_HID_KEY_COUNT];
static volatile uint8_t app_hid_report_requested;

void AppHidKeyboard_Init(uint8_t const keycodes[APP_HID_KEY_COUNT])
{
  AppHidKeyboard_SetKeycodes(keycodes);
  app_hid_report_requested = 0u;
}

void AppHidKeyboard_SetKeycodes(
  uint8_t const keycodes[APP_HID_KEY_COUNT])
{
  if (keycodes == NULL)
  {
    memset(app_hid_keycodes, 0, sizeof(app_hid_keycodes));
  }
  else
  {
    memcpy(app_hid_keycodes, keycodes, sizeof(app_hid_keycodes));
  }

  app_hid_report_requested = 1u;
}

void AppHidKeyboard_RequestReportFromISR(void)
{
  app_hid_report_requested = 1u;
}

void AppHidKeyboard_Task(void)
{
  uint8_t report[APP_HID_REPORT_SIZE] = {0};
  uint16_t pressed_mask;
  uint8_t button_id;
  uint8_t report_key_index = 2u;

  if ((app_hid_report_requested == 0u) ||
      (!tud_hid_n_ready(APP_HID_KEYBOARD_INSTANCE)))
  {
    return;
  }

  app_hid_report_requested = 0u;
  if (AppConfig_GetKeyboardMode() != APP_CONFIG_KEY_MODE_DISABLED)
  {
    pressed_mask = AppButtons_GetPressedMask();

    for (button_id = 0u; button_id < APP_HID_KEY_COUNT; button_id++)
    {
      if ((pressed_mask & (uint16_t)(1u << button_id)) != 0u)
      {
        uint8_t const keycode = app_hid_keycodes[button_id];

        if ((keycode >= HID_KEY_CONTROL_LEFT) &&
            (keycode <= HID_KEY_GUI_RIGHT))
        {
          report[0] |= (uint8_t)(1u <<
            (keycode - HID_KEY_CONTROL_LEFT));
        }
        else if ((keycode != HID_KEY_NONE) &&
                 (report_key_index < APP_HID_REPORT_SIZE))
        {
          report[report_key_index] = keycode;
          report_key_index++;
        }
      }
    }
  }

  if (!tud_hid_n_report(APP_HID_KEYBOARD_INSTANCE, 0u,
                        report, sizeof(report)))
  {
    app_hid_report_requested = 1u;
  }
}
