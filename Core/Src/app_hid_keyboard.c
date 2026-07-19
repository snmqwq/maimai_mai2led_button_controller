#include "app_hid_keyboard.h"

#include <string.h>

#include "app_buttons.h"
#include "tusb.h"

#define APP_HID_KEY_COUNT    13u
#define APP_HID_REPORT_SIZE  (2u + APP_HID_KEY_COUNT)

static uint8_t app_hid_keycodes[APP_HID_KEY_COUNT];
static volatile uint8_t app_hid_report_requested;

void AppHidKeyboard_Init(uint8_t const keycodes[APP_HID_KEY_COUNT])
{
  if (keycodes == NULL)
  {
    memset(app_hid_keycodes, 0, sizeof(app_hid_keycodes));
  }
  else
  {
    memcpy(app_hid_keycodes, keycodes, sizeof(app_hid_keycodes));
  }

  app_hid_report_requested = 0u;
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

  if ((app_hid_report_requested == 0u) || (!tud_hid_ready()))
  {
    return;
  }

  app_hid_report_requested = 0u;
  pressed_mask = AppButtons_GetPressedMask();

  for (button_id = 0u; button_id < APP_HID_KEY_COUNT; button_id++)
  {
    if ((pressed_mask & (uint16_t)(1u << button_id)) != 0u)
    {
      report[2u + button_id] = app_hid_keycodes[button_id];
    }
  }

  if (!tud_hid_report(0u, report, sizeof(report)))
  {
    app_hid_report_requested = 1u;
  }
}
