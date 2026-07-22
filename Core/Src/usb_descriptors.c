#include "tusb.h"
#include "stm32f0xx.h"

#include <stddef.h>
#include <string.h>

enum
{
  ITF_NUM_HID_IO4 = 0,
  ITF_NUM_CDC,
  ITF_NUM_CDC_DATA,
  ITF_NUM_HID_KEYBOARD,
  ITF_NUM_TOTAL
};

enum
{
  STRID_LANGID = 0,
  STRID_MANUFACTURER,
  STRID_PRODUCT,
  STRID_SERIAL,
  STRID_HID_IO4,
  STRID_CDC,
  STRID_HID_KEYBOARD
};

#define USB_VID                  0x0CA3
#define USB_PID                  0x0021
#define USB_BCD                  0x0100

#define EPNUM_HID_IO4_IN         0x81
#define EPNUM_HID_IO4_OUT        0x01
#define EPNUM_CDC_NOTIF          0x82
#define EPNUM_CDC_OUT            0x02
#define EPNUM_CDC_IN             0x83
#define EPNUM_HID_KEYBOARD_IN    0x84
#define HID_IO4_EP_SIZE          64
#define HID_KEYBOARD_EP_SIZE     16

#define CONFIG_TOTAL_LEN         (TUD_CONFIG_DESC_LEN +            \
                                  TUD_HID_INOUT_DESC_LEN +         \
                                  TUD_CDC_DESC_LEN +               \
                                  TUD_HID_DESC_LEN)

static tusb_desc_device_t const device_descriptor =
{
  .bLength            = sizeof(tusb_desc_device_t),
  .bDescriptorType    = TUSB_DESC_DEVICE,
  .bcdUSB             = 0x0200,
  .bDeviceClass       = 0x00,
  .bDeviceSubClass    = 0x00,
  .bDeviceProtocol    = 0x00,
  .bMaxPacketSize0    = CFG_TUD_ENDPOINT0_SIZE,
  .idVendor           = USB_VID,
  .idProduct          = USB_PID,
  .bcdDevice          = USB_BCD,
  .iManufacturer      = STRID_MANUFACTURER,
  .iProduct           = STRID_PRODUCT,
  .iSerialNumber      = STRID_SERIAL,
  .bNumConfigurations = 1
};

uint8_t const *tud_descriptor_device_cb(void)
{
  return (uint8_t const *)&device_descriptor;
}

enum
{
  REPORT_ID_IO4_INPUT = 1,
  REPORT_ID_IO4_OUTPUT = 16
};

#define TUD_HID_REPORT_DESC_IO4(...)                                      \
  HID_USAGE_PAGE(HID_USAGE_PAGE_DESKTOP),                                \
  HID_USAGE(HID_USAGE_DESKTOP_JOYSTICK),                                 \
  HID_COLLECTION(HID_COLLECTION_APPLICATION),                            \
    HID_REPORT_ID(REPORT_ID_IO4_INPUT)                                    \
    HID_REPORT_COUNT(1), HID_REPORT_SIZE(16),                             \
    HID_USAGE_PAGE(HID_USAGE_PAGE_DESKTOP),                              \
    HID_USAGE(HID_USAGE_DESKTOP_X),                                      \
    HID_INPUT(HID_DATA | HID_VARIABLE | HID_ABSOLUTE),                   \
    HID_REPORT_COUNT(1), HID_REPORT_SIZE(16),                             \
    HID_USAGE_PAGE(HID_USAGE_PAGE_DESKTOP),                              \
    HID_USAGE(HID_USAGE_DESKTOP_Y),                                      \
    HID_INPUT(HID_DATA | HID_VARIABLE | HID_ABSOLUTE),                   \
    HID_REPORT_COUNT(1), HID_REPORT_SIZE(16),                             \
    HID_USAGE_PAGE(HID_USAGE_PAGE_DESKTOP),                              \
    HID_USAGE(HID_USAGE_DESKTOP_X),                                      \
    HID_INPUT(HID_DATA | HID_VARIABLE | HID_ABSOLUTE),                   \
    HID_REPORT_COUNT(1), HID_REPORT_SIZE(16),                             \
    HID_USAGE_PAGE(HID_USAGE_PAGE_DESKTOP),                              \
    HID_USAGE(HID_USAGE_DESKTOP_Y),                                      \
    HID_INPUT(HID_DATA | HID_VARIABLE | HID_ABSOLUTE),                   \
    HID_REPORT_COUNT(1), HID_REPORT_SIZE(16),                             \
    HID_USAGE_PAGE(HID_USAGE_PAGE_DESKTOP),                              \
    HID_USAGE(HID_USAGE_DESKTOP_X),                                      \
    HID_INPUT(HID_DATA | HID_VARIABLE | HID_ABSOLUTE),                   \
    HID_REPORT_COUNT(1), HID_REPORT_SIZE(16),                             \
    HID_USAGE_PAGE(HID_USAGE_PAGE_DESKTOP),                              \
    HID_USAGE(HID_USAGE_DESKTOP_Y),                                      \
    HID_INPUT(HID_DATA | HID_VARIABLE | HID_ABSOLUTE),                   \
    HID_REPORT_COUNT(1), HID_REPORT_SIZE(16),                             \
    HID_USAGE_PAGE(HID_USAGE_PAGE_DESKTOP),                              \
    HID_USAGE(HID_USAGE_DESKTOP_X),                                      \
    HID_INPUT(HID_DATA | HID_VARIABLE | HID_ABSOLUTE),                   \
    HID_REPORT_COUNT(1), HID_REPORT_SIZE(16),                             \
    HID_USAGE_PAGE(HID_USAGE_PAGE_DESKTOP),                              \
    HID_USAGE(HID_USAGE_DESKTOP_Y),                                      \
    HID_INPUT(HID_DATA | HID_VARIABLE | HID_ABSOLUTE),                   \
    HID_REPORT_COUNT(1), HID_REPORT_SIZE(16),                             \
    HID_USAGE_PAGE(HID_USAGE_PAGE_DESKTOP),                              \
    HID_USAGE(HID_USAGE_DESKTOP_RX),                                     \
    HID_INPUT(HID_DATA | HID_VARIABLE | HID_ABSOLUTE),                   \
    HID_REPORT_COUNT(1), HID_REPORT_SIZE(16),                             \
    HID_USAGE_PAGE(HID_USAGE_PAGE_DESKTOP),                              \
    HID_USAGE(HID_USAGE_DESKTOP_RY),                                     \
    HID_INPUT(HID_DATA | HID_VARIABLE | HID_ABSOLUTE),                   \
    HID_REPORT_COUNT(1), HID_REPORT_SIZE(16),                             \
    HID_USAGE_PAGE(HID_USAGE_PAGE_DESKTOP),                              \
    HID_USAGE(HID_USAGE_DESKTOP_RX),                                     \
    HID_INPUT(HID_DATA | HID_VARIABLE | HID_ABSOLUTE),                   \
    HID_REPORT_COUNT(1), HID_REPORT_SIZE(16),                             \
    HID_USAGE_PAGE(HID_USAGE_PAGE_DESKTOP),                              \
    HID_USAGE(HID_USAGE_DESKTOP_RY),                                     \
    HID_INPUT(HID_DATA | HID_VARIABLE | HID_ABSOLUTE),                   \
    HID_REPORT_COUNT(1), HID_REPORT_SIZE(16),                             \
    HID_USAGE_PAGE(HID_USAGE_PAGE_DESKTOP),                              \
    HID_USAGE(HID_USAGE_DESKTOP_SLIDER),                                 \
    HID_INPUT(HID_DATA | HID_VARIABLE | HID_ABSOLUTE),                   \
    HID_REPORT_COUNT(1), HID_REPORT_SIZE(16),                             \
    HID_USAGE_PAGE(HID_USAGE_PAGE_DESKTOP),                              \
    HID_USAGE(HID_USAGE_DESKTOP_SLIDER),                                 \
    HID_INPUT(HID_DATA | HID_VARIABLE | HID_ABSOLUTE),                   \
    HID_REPORT_COUNT(48), HID_REPORT_SIZE(1),                             \
    HID_USAGE_MIN_N(1, 2), HID_USAGE_MAX_N(48, 2),                       \
    HID_INPUT(HID_DATA | HID_VARIABLE | HID_ABSOLUTE),                   \
    HID_REPORT_COUNT(1), HID_REPORT_SIZE(232),                           \
    HID_INPUT(HID_CONSTANT | HID_ABSOLUTE),                              \
    HID_USAGE_PAGE_N(0xFFA0, 2),                                        \
    HID_USAGE(0x00),                                                     \
    HID_REPORT_ID(REPORT_ID_IO4_OUTPUT)                                  \
    HID_COLLECTION(HID_COLLECTION_APPLICATION),                         \
      HID_USAGE(0x00),                                                   \
      HID_LOGICAL_MIN(0), HID_LOGICAL_MAX(255),                          \
      HID_REPORT_COUNT(63), HID_REPORT_SIZE(8),                          \
      HID_OUTPUT(HID_DATA | HID_VARIABLE | HID_ABSOLUTE),                \
    HID_COLLECTION_END,                                                  \
  HID_COLLECTION_END

#define TUD_HID_REPORT_DESC_KEYBOARD_13KRO(...)                         \
  HID_USAGE_PAGE ( HID_USAGE_PAGE_DESKTOP     )                       , \
  HID_USAGE      ( HID_USAGE_DESKTOP_KEYBOARD )                       , \
  HID_COLLECTION ( HID_COLLECTION_APPLICATION )                       , \
    __VA_ARGS__                                                          \
    HID_USAGE_PAGE ( HID_USAGE_PAGE_KEYBOARD )                         , \
      HID_USAGE_MIN    ( 224                                    )      , \
      HID_USAGE_MAX    ( 231                                    )      , \
      HID_LOGICAL_MIN  ( 0                                      )      , \
      HID_LOGICAL_MAX  ( 1                                      )      , \
      HID_REPORT_COUNT ( 8                                      )      , \
      HID_REPORT_SIZE  ( 1                                      )      , \
      HID_INPUT        ( HID_DATA | HID_VARIABLE | HID_ABSOLUTE )      , \
      HID_REPORT_COUNT ( 1                                      )      , \
      HID_REPORT_SIZE  ( 8                                      )      , \
      HID_INPUT        ( HID_CONSTANT                           )      , \
    HID_USAGE_PAGE  ( HID_USAGE_PAGE_LED                   )           , \
      HID_USAGE_MIN    ( 1                                       )     , \
      HID_USAGE_MAX    ( 5                                       )     , \
      HID_REPORT_COUNT ( 5                                       )     , \
      HID_REPORT_SIZE  ( 1                                       )     , \
      HID_OUTPUT       ( HID_DATA | HID_VARIABLE | HID_ABSOLUTE  )     , \
      HID_REPORT_COUNT ( 1                                       )     , \
      HID_REPORT_SIZE  ( 3                                       )     , \
      HID_OUTPUT       ( HID_CONSTANT                            )     , \
    HID_USAGE_PAGE ( HID_USAGE_PAGE_KEYBOARD )                         , \
      HID_USAGE_MIN    ( 0                                   )         , \
      HID_USAGE_MAX_N  ( 255, 2                              )         , \
      HID_LOGICAL_MIN  ( 0                                   )         , \
      HID_LOGICAL_MAX_N( 255, 2                              )         , \
      HID_REPORT_COUNT ( 13                                  )         , \
      HID_REPORT_SIZE  ( 8                                   )         , \
      HID_INPUT        ( HID_DATA | HID_ARRAY | HID_ABSOLUTE )         , \
  HID_COLLECTION_END

/*
 * Fifteen-byte keyboard input report:
 *   byte 0: modifier usages E0-E7
 *   byte 1: reserved
 *   bytes 2-14: up to 13 simultaneous keyboard usages
 */
static uint8_t const hid_report_descriptor_io4[] =
{
  TUD_HID_REPORT_DESC_IO4()
};

static uint8_t const hid_report_descriptor_keyboard[] =
{
  TUD_HID_REPORT_DESC_KEYBOARD_13KRO()
};

uint8_t const *tud_hid_descriptor_report_cb(uint8_t instance)
{
  if (instance == 0u)
  {
    return hid_report_descriptor_io4;
  }
  if (instance == 1u)
  {
    return hid_report_descriptor_keyboard;
  }

  return NULL;
}

static uint8_t const configuration_descriptor[] =
{
  TUD_CONFIG_DESCRIPTOR(1, ITF_NUM_TOTAL, 0, CONFIG_TOTAL_LEN, 0, 100),

  TUD_HID_INOUT_DESCRIPTOR(ITF_NUM_HID_IO4, STRID_HID_IO4,
                           HID_ITF_PROTOCOL_NONE,
                           sizeof(hid_report_descriptor_io4),
                           EPNUM_HID_IO4_OUT, EPNUM_HID_IO4_IN,
                           HID_IO4_EP_SIZE, 1),

  TUD_CDC_DESCRIPTOR(ITF_NUM_CDC, STRID_CDC, EPNUM_CDC_NOTIF, 8,
                     EPNUM_CDC_OUT, EPNUM_CDC_IN, 64),

  TUD_HID_DESCRIPTOR(ITF_NUM_HID_KEYBOARD, STRID_HID_KEYBOARD,
                     HID_ITF_PROTOCOL_NONE,
                     sizeof(hid_report_descriptor_keyboard),
                     EPNUM_HID_KEYBOARD_IN,
                     HID_KEYBOARD_EP_SIZE, 1)
};

uint8_t const *tud_descriptor_configuration_cb(uint8_t index)
{
  (void)index;
  return configuration_descriptor;
}

static char const *const string_descriptors[] =
{
  NULL,
  "SEGA",
  "Maimai Controller Device",
  NULL,
  "I/O CONTROL BD;15257;01;90;1831;6679A;00;GOUT=14_ADIN=8,E_ROTIN=4_COININ=2_SWIN=2,E_UQ1=41,6;",
  "Mai2LED CDC",
  "13KRO Keyboard"
};

static uint16_t string_descriptor_buffer[128];

static uint8_t uid_to_utf16(uint16_t *destination)
{
  static char const hex_digits[] = "0123456789ABCDEF";
  volatile uint32_t const *const uid = (volatile uint32_t const *)UID_BASE;
  uint32_t const serial_words[2] =
  {
    uid[0] ^ uid[2],
    uid[1] ^ ((uid[2] << 16) | (uid[2] >> 16))
  };
  uint8_t length = 0;

  for (uint8_t word = 0; word < 2; word++)
  {
    uint32_t const value = serial_words[word];
    for (int8_t shift = 28; shift >= 0; shift -= 4)
    {
      destination[length++] = (uint16_t)hex_digits[(value >> shift) & 0x0FU];
    }
  }

  return length;
}

uint16_t const *tud_descriptor_string_cb(uint8_t index, uint16_t langid)
{
  (void)langid;

  uint8_t char_count;
  if (index == STRID_LANGID)
  {
    string_descriptor_buffer[1] = 0x0409;
    char_count = 1;
  }
  else if (index == STRID_SERIAL)
  {
    char_count = uid_to_utf16(&string_descriptor_buffer[1]);
  }
  else
  {
    if ((index >= TU_ARRAY_SIZE(string_descriptors)) ||
        (string_descriptors[index] == NULL))
    {
      return NULL;
    }

    char_count = (uint8_t)strlen(string_descriptors[index]);
    if (char_count > 126u)
    {
      char_count = 126u;
    }

    for (uint8_t i = 0; i < char_count; i++)
    {
      string_descriptor_buffer[1 + i] =
        (uint8_t)string_descriptors[index][i];
    }
  }

  string_descriptor_buffer[0] =
    (uint16_t)((TUSB_DESC_STRING << 8) | (2U * char_count + 2U));
  return string_descriptor_buffer;
}
