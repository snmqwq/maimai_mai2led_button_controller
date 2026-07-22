#ifndef APP_HID_IO4_H
#define APP_HID_IO4_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define APP_HID_IO4_INSTANCE          0u
#define APP_HID_IO4_INPUT_REPORT_ID   1u
#define APP_HID_IO4_OUTPUT_REPORT_ID  16u

void AppHidIo4_Init(void);
void AppHidIo4_RequestReportFromISR(void);
void AppHidIo4_Task(void);
void AppHidIo4_SetReport(uint8_t report_id,
                         uint8_t const *buffer,
                         uint16_t bufsize);

#ifdef __cplusplus
}
#endif

#endif /* APP_HID_IO4_H */
