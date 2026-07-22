#include "app_hid_io4.h"

#include <string.h>

#include "app_buttons.h"
#include "app_config.h"
#include "tusb.h"

#define APP_HID_IO4_COIN_BUTTON_ID  12u

typedef struct __attribute__((packed))
{
  uint16_t adcs[8];
  uint16_t spinners[4];
  uint16_t chutes[2];
  uint16_t buttons[2];
  uint8_t system_status;
  uint8_t usb_status;
  uint8_t padding[29];
} AppHidIo4InputReport;

_Static_assert(sizeof(AppHidIo4InputReport) == 63u,
               "IO4 input report must be 63 bytes");

static uint8_t const app_hid_io4_main_bits_1p[APP_BUTTON_MAIN_COUNT] =
{
  2u, 3u, 0u, 15u, 14u, 13u, 12u, 11u
};

static uint8_t const app_hid_io4_main_bits_2p[APP_BUTTON_MAIN_COUNT] =
{
  18u, 19u, 16u, 31u, 30u, 29u, 28u, 27u
};

static AppHidIo4InputReport app_hid_io4_report;
static volatile uint8_t app_hid_io4_report_requested;
static uint16_t app_hid_io4_last_pressed_mask;

static uint32_t AppHidIo4_NeutralMainButtons(void)
{
  uint32_t buttons = 0u;
  uint8_t button_id;

  for (button_id = 0u; button_id < APP_BUTTON_MAIN_COUNT; button_id++)
  {
    buttons |= (uint32_t)1u << app_hid_io4_main_bits_1p[button_id];
    buttons |= (uint32_t)1u << app_hid_io4_main_bits_2p[button_id];
  }

  return buttons;
}

static void AppHidIo4_UpdateInputReport(void)
{
  uint16_t const pressed_mask = AppButtons_GetPressedMask();
  AppConfigIo4Mode const mode = AppConfig_GetIo4Mode();
  uint8_t const *main_bits = NULL;
  uint32_t buttons = AppHidIo4_NeutralMainButtons();
  uint8_t button_id;

  if (mode == APP_CONFIG_IO4_MODE_1P)
  {
    main_bits = app_hid_io4_main_bits_1p;
  }
  else if (mode == APP_CONFIG_IO4_MODE_2P)
  {
    main_bits = app_hid_io4_main_bits_2p;
  }

  if (main_bits != NULL)
  {
    for (button_id = 0u; button_id < APP_BUTTON_MAIN_COUNT; button_id++)
    {
      if ((pressed_mask & (uint16_t)(1u << button_id)) != 0u)
      {
        buttons &= ~((uint32_t)1u << main_bits[button_id]);
      }
    }

    if ((pressed_mask & (uint16_t)(1u << 8u)) != 0u)
    {
      buttons |= (uint32_t)1u << 1u;
    }
    if ((pressed_mask & (uint16_t)(1u << 9u)) != 0u)
    {
      buttons |= (uint32_t)1u << 20u;
    }
    if ((pressed_mask & (uint16_t)(1u << 10u)) != 0u)
    {
      buttons |= (uint32_t)1u << 9u;
    }
    if ((pressed_mask & (uint16_t)(1u << 11u)) != 0u)
    {
      buttons |= (uint32_t)1u << 6u;
    }

    if (((pressed_mask &
          (uint16_t)(1u << APP_HID_IO4_COIN_BUTTON_ID)) != 0u) &&
        ((app_hid_io4_last_pressed_mask &
          (uint16_t)(1u << APP_HID_IO4_COIN_BUTTON_ID)) == 0u))
    {
      app_hid_io4_report.chutes[0] += 0x0100u;
    }
  }

  app_hid_io4_report.buttons[0] = (uint16_t)buttons;
  app_hid_io4_report.buttons[1] = (uint16_t)(buttons >> 16u);
  app_hid_io4_last_pressed_mask = pressed_mask;
}

void AppHidIo4_Init(void)
{
  memset(&app_hid_io4_report, 0, sizeof(app_hid_io4_report));
  app_hid_io4_report_requested = 0u;
  app_hid_io4_last_pressed_mask = AppButtons_GetPressedMask();
  AppHidIo4_UpdateInputReport();
}

void AppHidIo4_RequestReportFromISR(void)
{
  app_hid_io4_report_requested = 1u;
}

void AppHidIo4_Task(void)
{
  if ((app_hid_io4_report_requested == 0u) ||
      (!tud_hid_n_ready(APP_HID_IO4_INSTANCE)))
  {
    return;
  }

  app_hid_io4_report_requested = 0u;
  AppHidIo4_UpdateInputReport();

  if (!tud_hid_n_report(APP_HID_IO4_INSTANCE,
                        APP_HID_IO4_INPUT_REPORT_ID,
                        &app_hid_io4_report,
                        sizeof(app_hid_io4_report)))
  {
    app_hid_io4_report_requested = 1u;
  }
}

void AppHidIo4_SetReport(uint8_t report_id,
                         uint8_t const *buffer,
                         uint16_t bufsize)
{
  uint8_t command;

  if ((buffer == NULL) || (bufsize == 0u))
  {
    return;
  }

  if (report_id == APP_HID_IO4_OUTPUT_REPORT_ID)
  {
    command = buffer[0];
  }
  else if ((report_id == 0u) &&
           (bufsize >= 2u) &&
           (buffer[0] == APP_HID_IO4_OUTPUT_REPORT_ID))
  {
    command = buffer[1];
  }
  else
  {
    return;
  }

  switch (command)
  {
    case 0x01u:
    case 0x02u:
      app_hid_io4_report.system_status = 0x30u;
      break;

    case 0x03u:
      app_hid_io4_report.chutes[0] = 0u;
      app_hid_io4_report.chutes[1] = 0u;
      app_hid_io4_report.system_status = 0u;
      break;

    case 0x41u:
      /* Reserved for the IO4 RGB output mapping. */
      break;

    case 0x04u:
    default:
      break;
  }

  app_hid_io4_report_requested = 1u;
}
