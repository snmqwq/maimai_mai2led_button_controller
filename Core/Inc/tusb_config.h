#ifndef TUSB_CONFIG_H_
#define TUSB_CONFIG_H_

#ifdef __cplusplus
extern "C" {
#endif

/* STM32F072 USB FS device, bare-metal mode. */
#define CFG_TUSB_MCU               OPT_MCU_STM32F0
#define CFG_TUSB_OS                OPT_OS_NONE
#define CFG_TUSB_RHPORT0_MODE      (OPT_MODE_DEVICE | OPT_MODE_FULL_SPEED)

#define CFG_TUD_ENABLED            1
#define CFG_TUD_ENDPOINT0_SIZE     64

/* One CDC ACM interface and one HID interface. */
#define CFG_TUD_CDC                1
#define CFG_TUD_HID                1

/* Keep the remaining USB device classes disabled. */
#define CFG_TUD_MSC                0
#define CFG_TUD_MIDI               0
#define CFG_TUD_VENDOR             0

/* Full-speed endpoint and FIFO sizes. */
#define CFG_TUD_CDC_RX_BUFSIZE     64
#define CFG_TUD_CDC_TX_BUFSIZE     64
#define CFG_TUD_CDC_EP_BUFSIZE     64
#define CFG_TUD_HID_EP_BUFSIZE     16

#ifdef __cplusplus
}
#endif

#endif /* TUSB_CONFIG_H_ */
