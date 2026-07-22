#include "tusb.h"

#include "app_hid_io4.h"

uint16_t tud_hid_get_report_cb(uint8_t instance, uint8_t report_id,
                               hid_report_type_t report_type,
                               uint8_t *buffer, uint16_t reqlen)
{
  (void)instance;
  (void)report_id;
  (void)report_type;
  (void)buffer;
  (void)reqlen;

  return 0;
}

void tud_hid_set_report_cb(uint8_t instance, uint8_t report_id,
                           hid_report_type_t report_type,
                           uint8_t const *buffer, uint16_t bufsize)
{
  if ((instance == APP_HID_IO4_INSTANCE) &&
      (report_type == HID_REPORT_TYPE_OUTPUT))
  {
    AppHidIo4_SetReport(report_id, buffer, bufsize);
  }
}
