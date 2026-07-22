#include "app_mai2led.h"

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "app_buttons.h"
#include "app_config.h"
#include "app_hid_keyboard.h"
#include "tim.h"
#include "tusb.h"
#include "ws28xx.h"

#define MAI2LED_CDC_ITF                   0u
#define LOGIC_LED_COUNT                   8u
#define IDLE_TASK_INTERVAL_MS             2u
#define RAINBOW_HUE_STEP                  1u
#define RAINBOW_UPDATE_DIVIDER           50u
#define SPECIAL_SEQ_LEN                   8u
#define SPECIAL_MAGIC_CMD              0xB7u
#define KEY_CONFIG_PACKET_LEN             4u
#define KEY_CONFIG_TIMEOUT_MS           100u

enum
{
  Sync = 0xE0u,
  Marker = 0xD0u,

  SetLedGs8Bit = 0x31u,
  SetLedGs8BitMulti = 0x32u,
  SetLedGs8BitMultiFade = 0x33u,
  SetLedFet = 0x39u,
  SetDcUpdate = 0x3Bu,
  SetLedGsUpdate = 0x3Cu,
  SetDc = 0x3Fu,
  SetEEPRom = 0x7Bu,
  GetEEPRom = 0x7Cu,
  SetEnableResponse = 0x7Du,
  SetDisableResponse = 0x7Eu,
  GetBoardInfo = 0xF0u,
  GetBoardStatus = 0xF1u,
  GetFirmSum = 0xF2u,
  GetProtocolVersion = 0xF3u,

  AckStatus_Ok = 0x01u,
  AckStatus_SumError = 0x02u,
  AckStatus_RecvBfOverFlow = 0x06u,

  AckReport_Ok = 0x01u,
  AckReport_ParamError = 0x04u
};

enum
{
  KEYCFG_SET_KEY = 0xA1u,
  KEYCFG_SAVE_FLASH = 0xA2u,
  KEYCFG_LOAD_DEFAULT = 0xA3u,
  KEYCFG_GET_KEY = 0xA4u,
  KEYCFG_SET_LEDS_PER_LOGIC = 0xA5u,
  KEYCFG_GET_LEDS_PER_LOGIC = 0xA6u,
  KEYCFG_SET_RAINBOW_ENABLED = 0xA7u,
  KEYCFG_GET_RAINBOW_ENABLED = 0xA8u,
  KEYCFG_SET_KEY_MODE = 0xA9u,
  KEYCFG_GET_KEY_MODE = 0xAAu,
  KEYCFG_SET_IO4_MODE = 0xABu,
  KEYCFG_GET_IO4_MODE = 0xACu,

  KEYCFG_OK = 0x00u,
  KEYCFG_SUM_ERROR = 0x01u,
  KEYCFG_INDEX_ERROR = 0x02u,
  KEYCFG_CMD_ERROR = 0x03u
};

typedef union
{
  uint8_t buffer[39];
  struct
  {
    uint8_t dstNodeID;
    uint8_t srcNodeID;
    uint8_t length;
    uint8_t command;
    union
    {
      struct
      {
        uint8_t index;
        uint8_t color[3];
      };
      struct
      {
        uint8_t start;
        uint8_t end;
        uint8_t skip;
        uint8_t Multi_color[3];
        uint8_t speed;
      };
      struct
      {
        uint8_t BodyLed;
        uint8_t ExtLed;
        uint8_t SideLed;
      };
      struct
      {
        uint8_t Set_adress;
        uint8_t writeData;
      };
      uint8_t Get_adress;
    };
  };
} PacketReq;

typedef union
{
  uint8_t buffer[16];
  struct
  {
    uint8_t dstNodeID;
    uint8_t srcNodeID;
    uint8_t length;
    uint8_t status;
    uint8_t command;
    uint8_t report;
    union
    {
      uint8_t eepData;
      struct
      {
        uint8_t boardNo[9];
        uint8_t firmRevision;
      };
      struct
      {
        uint8_t timeoutStat;
        uint8_t timeoutSec;
        uint8_t pwmIo;
        uint8_t fetTimeout;
      };
      struct
      {
        uint8_t sum_upper;
        uint8_t sum_lower;
      };
      struct
      {
        uint8_t appliMode;
        uint8_t major;
        uint8_t minor;
      };
    };
  };
} PacketAck;

typedef struct
{
  uint8_t r;
  uint8_t g;
  uint8_t b;
} RGB_t;

typedef enum
{
  LED_RECONFIG_IDLE = 0,
  LED_RECONFIG_CLEAR_PENDING,
  LED_RECONFIG_CLEAR_IN_FLIGHT,
  LED_RECONFIG_RELIGHT_PENDING
} LedReconfigureState;

_Static_assert(sizeof(PacketReq) == 39u,
               "Unexpected Mai2LED request layout");
_Static_assert(sizeof(PacketAck) == 16u,
               "Unexpected Mai2LED acknowledgement layout");

static WS28XX_HandleTypeDef hws;
static PacketReq req;
static PacketAck ack;
static uint8_t dummyEEPRom[8];

static uint8_t packet_rx_len;
static uint8_t packet_checksum;
static bool packet_escape;

static uint16_t led_total;
static bool app_initialized;
static bool idle_lights_enabled;
static bool idle_lights_refresh_requested;
static bool io_idle_clear_pending;
static uint8_t rainbow_hue;
static uint8_t rainbow_update_divider;
static uint32_t idle_task_last_tick;
static volatile LedReconfigureState led_reconfigure_state;
static uint16_t led_reconfigure_target_total;
static volatile bool led_dma_busy;
static volatile bool led_flush_pending;

static uint32_t StartFadeTime;
static uint32_t EndFadeTime;
static uint8_t StartFadeLed;
static uint8_t EndFadeLed;
static RGB_t StartFadeColor;
static RGB_t EndFadeColor;
static bool NeedFade;
static bool FadeStart;

static uint8_t special_detector[SPECIAL_SEQ_LEN];
static uint8_t const special_seq[SPECIAL_SEQ_LEN] =
{
  0x91u, 0x3Eu, 0xEDu, 0x20u, 0x7Cu, 0x99u, 0x58u, 0xACu
};

static bool led_try_update(void)
{
  if (led_dma_busy)
  {
    led_flush_pending = true;
    return false;
  }

  /*
   * WS28XX_Update() rewrites hws.Buffer before starting DMA and releases its
   * internal lock immediately. Keep an application-level ownership flag from
   * before that rewrite until the DMA completion/error callback stops DMA.
   */
  led_dma_busy = true;
  led_flush_pending = false;

  if (!WS28XX_Update(&hws))
  {
    led_dma_busy = false;
    led_flush_pending = true;
    return false;
  }

  return true;
}

static void led_flush_pending_task(void)
{
  if (led_flush_pending &&
      (!led_dma_busy) &&
      (led_reconfigure_state == LED_RECONFIG_IDLE))
  {
    (void)led_try_update();
  }
}

static void mai2led_cancel_fade(void)
{
  NeedFade = false;
  FadeStart = false;
}

static bool WS28XX_SetPixels_RGB(WS28XX_HandleTypeDef *hLed,
                                 uint16_t StartPixel,
                                 uint16_t EndPixel,
                                 uint8_t Red,
                                 uint8_t Green,
                                 uint8_t Blue)
{
  uint16_t pixel;

  if ((hLed == NULL) ||
      (StartPixel > EndPixel) ||
      (EndPixel >= hLed->MaxPixel))
  {
    return false;
  }

  for (pixel = StartPixel; pixel <= EndPixel; pixel++)
  {
    if (!WS28XX_SetPixel_RGB(hLed, pixel, Red, Green, Blue))
    {
      return false;
    }
  }

  return true;
}

static void led_config_apply(void)
{
  led_total =
    (uint16_t)LOGIC_LED_COUNT * AppConfig_Get()->leds_per_logic;
}

static void mai2led_Logic_To_Physical(uint8_t start_logic,
                                      uint8_t end_logic,
                                      uint8_t *start_phy,
                                      uint8_t *end_phy)
{
  uint8_t temp;
  uint8_t const leds_per_logic = AppConfig_Get()->leds_per_logic;

  if ((start_phy == NULL) || (end_phy == NULL))
  {
    return;
  }

  if (start_logic >= LOGIC_LED_COUNT)
  {
    start_logic = LOGIC_LED_COUNT - 1u;
  }
  if (end_logic >= LOGIC_LED_COUNT)
  {
    end_logic = LOGIC_LED_COUNT - 1u;
  }

  if (start_logic > end_logic)
  {
    temp = start_logic;
    start_logic = end_logic;
    end_logic = temp;
  }

  *start_phy = (uint8_t)(start_logic * leds_per_logic);
  *end_phy =
    (uint8_t)((((uint16_t)end_logic + 1u) * leds_per_logic) - 1u);
}

static RGB_t rainbow_color(uint8_t pos)
{
  RGB_t color;

  pos = (uint8_t)(255u - pos);

  if (pos < 85u)
  {
    color.r = (uint8_t)(255u - (pos * 3u));
    color.g = 0u;
    color.b = (uint8_t)(pos * 3u);
  }
  else if (pos < 170u)
  {
    pos = (uint8_t)(pos - 85u);
    color.r = 0u;
    color.g = (uint8_t)(pos * 3u);
    color.b = (uint8_t)(255u - (pos * 3u));
  }
  else
  {
    pos = (uint8_t)(pos - 170u);
    color.r = (uint8_t)(pos * 3u);
    color.g = (uint8_t)(255u - (pos * 3u));
    color.b = 0u;
  }

  return color;
}

/*
 * Deliberate difference from main_old.c: distribute the color wheel over
 * physical pixels instead of assigning one color to each logical button.
 */
static void draw_button_rainbow(uint8_t hue)
{
  uint16_t pixel;

  for (pixel = 0u; pixel < led_total; pixel++)
  {
    uint8_t const hue_offset =
      (uint8_t)(((uint32_t)pixel * 256u) / led_total);
    RGB_t const color =
      rainbow_color((uint8_t)(hue + hue_offset));

    (void)WS28XX_SetPixel_RGB(&hws, pixel,
                               color.r, color.g, color.b);
  }
}

static bool flush_button_rainbow(void)
{
  draw_button_rainbow(rainbow_hue);

  if (led_try_update())
  {
    rainbow_hue += RAINBOW_HUE_STEP;
    return true;
  }

  return false;
}

static bool update_button_rainbow(void)
{
  if (rainbow_update_divider > 0u)
  {
    rainbow_update_divider--;
    return false;
  }

  if (flush_button_rainbow())
  {
    rainbow_update_divider = RAINBOW_UPDATE_DIVIDER - 1u;
    return true;
  }

  return false;
}

static void clear_button_lights(void)
{
  if (led_total > 0u)
  {
    (void)WS28XX_SetPixels_RGB(&hws, 0u, led_total - 1u,
                                0u, 0u, 0u);
  }
}

static void clear_all_button_lights(void)
{
  (void)WS28XX_SetPixels_RGB(&hws, 0u, WS28XX_PIXEL_MAX - 1u,
                              0u, 0u, 0u);
}

static void set_button_lights_white(void)
{
  if (led_total > 0u)
  {
    (void)WS28XX_SetPixels_RGB(&hws, 0u, led_total - 1u,
                                255u, 255u, 255u);
  }
}

static void led_idle_config_changed(void)
{
  if (idle_lights_enabled)
  {
    idle_lights_refresh_requested = true;
    rainbow_update_divider = 0u;
  }
}

static void io_mark_active(void)
{
  if (idle_lights_enabled)
  {
    clear_button_lights();
    idle_lights_enabled = false;
    idle_lights_refresh_requested = false;
    io_idle_clear_pending = true;
    rainbow_update_divider = 0u;
  }
}

void AppMai2Led_RestoreIdleLights(void)
{
  if (!app_initialized)
  {
    return;
  }

  idle_lights_enabled = true;
  idle_lights_refresh_requested = true;
  io_idle_clear_pending = false;
  rainbow_update_divider = 0u;
  mai2led_cancel_fade();
}

static void update_lights(void)
{
  if (!idle_lights_enabled)
  {
    return;
  }

  if (idle_lights_refresh_requested)
  {
    idle_lights_refresh_requested = false;

    if (AppConfig_Get()->rainbow_enabled != 0u)
    {
      if (flush_button_rainbow())
      {
        rainbow_update_divider = RAINBOW_UPDATE_DIVIDER - 1u;
      }
      else
      {
        idle_lights_refresh_requested = true;
      }
    }
    else
    {
      set_button_lights_white();
      if (!led_try_update())
      {
        idle_lights_refresh_requested = true;
      }
    }

    return;
  }

  if (AppConfig_Get()->rainbow_enabled != 0u)
  {
    (void)update_button_rainbow();
  }
}

static void led_reconfigure_begin(void)
{
  led_reconfigure_target_total =
    (uint16_t)LOGIC_LED_COUNT * AppConfig_Get()->leds_per_logic;

  /*
   * The WS28XX transfer length stays fixed at 32 pixels. Clear the whole
   * physical range first so every pixel outside the new logical range is off.
   */
  clear_all_button_lights();

  mai2led_cancel_fade();
  idle_lights_refresh_requested = false;
  led_reconfigure_state = LED_RECONFIG_CLEAR_PENDING;
}

static void led_reconfigure_task(void)
{
  switch (led_reconfigure_state)
  {
    case LED_RECONFIG_CLEAR_PENDING:
      clear_all_button_lights();

      if (led_try_update())
      {
        led_reconfigure_state = LED_RECONFIG_CLEAR_IN_FLIGHT;
      }
      break;

    case LED_RECONFIG_CLEAR_IN_FLIGHT:
      break;

    case LED_RECONFIG_RELIGHT_PENDING:
      led_total = led_reconfigure_target_total;

      if (AppConfig_Get()->rainbow_enabled == 0u)
      {
        set_button_lights_white();
        if (!led_try_update())
        {
          break;
        }
      }

      idle_lights_refresh_requested = false;
      led_reconfigure_state = LED_RECONFIG_IDLE;
      break;

    case LED_RECONFIG_IDLE:
    default:
      break;
  }
}

static bool mai2led_command_is_io_activity(uint8_t command)
{
  switch (command)
  {
    case 0u:
    case SPECIAL_MAGIC_CMD:
    case AckStatus_SumError:
    case AckStatus_RecvBfOverFlow:
      return false;

    default:
      return true;
  }
}

static bool mai2led_command_writes_button_lights(uint8_t command)
{
  switch (command)
  {
    case SetLedGs8Bit:
    case SetLedGs8BitMulti:
    case SetLedGs8BitMultiFade:
    case SetLedGsUpdate:
      return true;

    default:
      return false;
  }
}

static void update_io_idle_clear(uint8_t command)
{
  if (!io_idle_clear_pending)
  {
    return;
  }

  if (mai2led_command_writes_button_lights(command))
  {
    io_idle_clear_pending = false;
    return;
  }

  if (led_try_update())
  {
    io_idle_clear_pending = false;
  }
}

static bool special_sequence_detect(uint8_t value)
{
  memmove(&special_detector[0], &special_detector[1],
          SPECIAL_SEQ_LEN - 1u);
  special_detector[SPECIAL_SEQ_LEN - 1u] = value;

  return memcmp(special_detector, special_seq,
                SPECIAL_SEQ_LEN) == 0;
}

static uint8_t key_config_read_packet(uint8_t *cmd,
                                      uint8_t *idx,
                                      uint8_t *key)
{
  uint8_t buf[KEY_CONFIG_PACKET_LEN];
  uint8_t count = 0u;
  uint32_t const start = HAL_GetTick();

  while (count < KEY_CONFIG_PACKET_LEN)
  {
    tud_task();

    if (tud_cdc_n_available(MAI2LED_CDC_ITF) != 0u)
    {
      if (tud_cdc_n_read(MAI2LED_CDC_ITF,
                         &buf[count], 1u) == 1u)
      {
        count++;
      }
    }

    if ((uint32_t)(HAL_GetTick() - start) >
        KEY_CONFIG_TIMEOUT_MS)
    {
      return KEYCFG_CMD_ERROR;
    }
  }

  if ((uint8_t)(buf[0] + buf[1] + buf[2]) != buf[3])
  {
    return KEYCFG_SUM_ERROR;
  }

  *cmd = buf[0];
  *idx = buf[1];
  *key = buf[2];
  return KEYCFG_OK;
}

static uint8_t key_config_process(uint8_t *out_idx,
                                  uint8_t *out_key)
{
  uint8_t cmd;
  uint8_t idx;
  uint8_t key;
  uint8_t const ret =
    key_config_read_packet(&cmd, &idx, &key);
  AppConfigData config;

  if (ret != KEYCFG_OK)
  {
    return ret;
  }

  *out_idx = idx;
  *out_key = key;

  switch (cmd)
  {
    case KEYCFG_SET_KEY:
      if (idx >= APP_CONFIG_KEY_COUNT)
      {
        return KEYCFG_INDEX_ERROR;
      }

      config = *AppConfig_Get();
      if ((idx < APP_BUTTON_MAIN_COUNT) &&
          ((AppConfig_GetKeyboardMode() == APP_CONFIG_KEY_MODE_1P) ||
           (AppConfig_GetKeyboardMode() == APP_CONFIG_KEY_MODE_2P)))
      {
        memcpy(config.custom_keycodes, AppConfig_GetKeycodes(),
               sizeof(config.custom_keycodes));
        AppConfig_SetKeyboardMode(&config,
                                  APP_CONFIG_KEY_MODE_CUSTOM);
      }
      config.custom_keycodes[idx] = key;
      if (!AppConfig_WriteCache(&config))
      {
        return KEYCFG_CMD_ERROR;
      }
      AppHidKeyboard_SetKeycodes(AppConfig_GetKeycodes());
      return KEYCFG_OK;

    case KEYCFG_SAVE_FLASH:
      return AppConfig_SaveToFlash() ?
        KEYCFG_OK : KEYCFG_CMD_ERROR;

    case KEYCFG_LOAD_DEFAULT:
      AppConfig_LoadDefaults();
      AppHidKeyboard_SetKeycodes(AppConfig_GetKeycodes());
      led_reconfigure_begin();
      return AppConfig_SaveToFlash() ?
        KEYCFG_OK : KEYCFG_CMD_ERROR;

    case KEYCFG_GET_KEY:
      if (idx >= APP_CONFIG_KEY_COUNT)
      {
        return KEYCFG_INDEX_ERROR;
      }
      *out_key = AppConfig_GetKeycodes()[idx];
      return KEYCFG_OK;

    case KEYCFG_SET_LEDS_PER_LOGIC:
      if ((idx != 0u) || (key < 1u) || (key > 4u))
      {
        return KEYCFG_INDEX_ERROR;
      }

      config = *AppConfig_Get();
      config.leds_per_logic = key;
      if (!AppConfig_WriteCache(&config))
      {
        return KEYCFG_CMD_ERROR;
      }
      led_reconfigure_begin();
      *out_key = AppConfig_Get()->leds_per_logic;
      return KEYCFG_OK;

    case KEYCFG_GET_LEDS_PER_LOGIC:
      if (idx != 0u)
      {
        return KEYCFG_INDEX_ERROR;
      }
      *out_key = AppConfig_Get()->leds_per_logic;
      return KEYCFG_OK;

    case KEYCFG_SET_RAINBOW_ENABLED:
      if ((idx != 0u) || (key > 1u))
      {
        return KEYCFG_INDEX_ERROR;
      }

      config = *AppConfig_Get();
      config.rainbow_enabled = key;
      if (!AppConfig_WriteCache(&config))
      {
        return KEYCFG_CMD_ERROR;
      }
      led_idle_config_changed();
      *out_key = AppConfig_Get()->rainbow_enabled;
      return KEYCFG_OK;

    case KEYCFG_GET_RAINBOW_ENABLED:
      if (idx != 0u)
      {
        return KEYCFG_INDEX_ERROR;
      }
      *out_key = AppConfig_Get()->rainbow_enabled;
      return KEYCFG_OK;

    case KEYCFG_SET_KEY_MODE:
      if ((idx != 0u) ||
          (key > (uint8_t)APP_CONFIG_KEY_MODE_DISABLED))
      {
        return KEYCFG_INDEX_ERROR;
      }

      config = *AppConfig_Get();
      AppConfig_SetKeyboardMode(&config, (AppConfigKeyMode)key);
      if (!AppConfig_WriteCache(&config))
      {
        return KEYCFG_CMD_ERROR;
      }
      AppHidKeyboard_SetKeycodes(AppConfig_GetKeycodes());
      *out_key = (uint8_t)AppConfig_GetKeyboardMode();
      return KEYCFG_OK;

    case KEYCFG_GET_KEY_MODE:
      if (idx != 0u)
      {
        return KEYCFG_INDEX_ERROR;
      }
      *out_key = (uint8_t)AppConfig_GetKeyboardMode();
      return KEYCFG_OK;

    case KEYCFG_SET_IO4_MODE:
      if ((idx != 0u) ||
          (key > (uint8_t)APP_CONFIG_IO4_MODE_2P))
      {
        return KEYCFG_INDEX_ERROR;
      }

      config = *AppConfig_Get();
      AppConfig_SetIo4Mode(&config, (AppConfigIo4Mode)key);
      if (!AppConfig_WriteCache(&config))
      {
        return KEYCFG_CMD_ERROR;
      }
      *out_key = (uint8_t)AppConfig_GetIo4Mode();
      return KEYCFG_OK;

    case KEYCFG_GET_IO4_MODE:
      if (idx != 0u)
      {
        return KEYCFG_INDEX_ERROR;
      }
      *out_key = (uint8_t)AppConfig_GetIo4Mode();
      return KEYCFG_OK;

    default:
      return KEYCFG_CMD_ERROR;
  }
}

static void key_config_send_ack(uint8_t status,
                                uint8_t idx,
                                uint8_t key)
{
  uint8_t buf[4];

  buf[0] = 0xACu;
  buf[1] = status;
  buf[2] = idx;
  buf[3] = key;

  (void)tud_cdc_n_write(MAI2LED_CDC_ITF, buf, sizeof(buf));
  (void)tud_cdc_n_write_flush(MAI2LED_CDC_ITF);
}

static void packet_reset(bool clear_request)
{
  packet_rx_len = 0u;
  packet_checksum = 0u;
  packet_escape = false;

  if (clear_request)
  {
    memset(&req, 0, sizeof(req));
  }
}

static uint8_t packet_read(void)
{
  while (tud_cdc_n_available(MAI2LED_CDC_ITF) != 0u)
  {
    uint8_t value;

    if (tud_cdc_n_read(MAI2LED_CDC_ITF,
                       &value, 1u) != 1u)
    {
      break;
    }

    if (special_sequence_detect(value))
    {
      packet_reset(true);
      return SPECIAL_MAGIC_CMD;
    }

    if (value == Sync)
    {
      packet_reset(true);
      continue;
    }

    if (value == Marker)
    {
      packet_escape = true;
      continue;
    }

    if (packet_escape)
    {
      value++;
      packet_escape = false;
    }

    if (packet_rx_len == (uint8_t)(req.length + 3u))
    {
      uint8_t const ret =
        (packet_checksum == value) ?
          req.command : AckStatus_SumError;

      packet_reset(false);
      return ret;
    }

    if (packet_rx_len >= sizeof(req.buffer))
    {
      packet_reset(true);
      return AckStatus_RecvBfOverFlow;
    }

    req.buffer[packet_rx_len++] = value;
    packet_checksum += value;
  }

  return 0u;
}

static void ack_init(uint8_t payload_length,
                     uint8_t status,
                     uint8_t report)
{
  ack.dstNodeID = req.srcNodeID;
  ack.srcNodeID = req.dstNodeID;
  ack.length = (uint8_t)(3u + payload_length);
  ack.status = status;
  ack.command = req.command;
  ack.report = report;
}

static void ack_init_ok(uint8_t payload_length)
{
  ack_init(payload_length, AckStatus_Ok, AckReport_Ok);
}

static void packet_write(void)
{
  uint8_t checksum = 0u;
  uint8_t write_len = 0u;
  uint8_t const total_len = (uint8_t)(ack.length + 3u);
  uint8_t data;

  if (ack.command == 0u)
  {
    return;
  }

  if (total_len > sizeof(ack.buffer))
  {
    ack.command = 0u;
    return;
  }

  data = Sync;
  (void)tud_cdc_n_write(MAI2LED_CDC_ITF, &data, 1u);

  while (write_len < total_len)
  {
    uint8_t value = ack.buffer[write_len++];

    checksum += value;
    if ((value == Sync) || (value == Marker))
    {
      data = Marker;
      (void)tud_cdc_n_write(MAI2LED_CDC_ITF, &data, 1u);
      data = (uint8_t)(value - 1u);
      (void)tud_cdc_n_write(MAI2LED_CDC_ITF, &data, 1u);
    }
    else
    {
      (void)tud_cdc_n_write(MAI2LED_CDC_ITF, &value, 1u);
    }
  }

  (void)tud_cdc_n_write(MAI2LED_CDC_ITF, &checksum, 1u);
  (void)tud_cdc_n_write_flush(MAI2LED_CDC_ITF);
  ack.command = 0u;
}

static void mai2led_setLedGs8Bit(void)
{
  uint8_t start_phy;
  uint8_t end_phy;

  mai2led_cancel_fade();
  mai2led_Logic_To_Physical(req.index, req.index,
                            &start_phy, &end_phy);
  (void)WS28XX_SetPixels_RGB(&hws, start_phy, end_phy,
                              req.color[0],
                              req.color[1],
                              req.color[2]);
  (void)led_try_update();
  ack_init_ok(0u);
}

static void mai2led_setLedGs8BitMulti(void)
{
  mai2led_cancel_fade();

  if (req.end == 0x20u)
  {
    req.end = LOGIC_LED_COUNT;
  }

  mai2led_Logic_To_Physical(req.start,
                            (uint8_t)(req.end - 1u),
                            &StartFadeLed, &EndFadeLed);

  StartFadeColor.r = req.Multi_color[0];
  StartFadeColor.g = req.Multi_color[1];
  StartFadeColor.b = req.Multi_color[2];

  (void)WS28XX_SetPixels_RGB(&hws,
                              StartFadeLed, EndFadeLed,
                              StartFadeColor.r,
                              StartFadeColor.g,
                              StartFadeColor.b);
  ack_init_ok(0u);
}

static void mai2led_setLedGs8BitMultiFade(void)
{
  EndFadeColor.r = req.Multi_color[0];
  EndFadeColor.g = req.Multi_color[1];
  EndFadeColor.b = req.Multi_color[2];
  StartFadeTime = HAL_GetTick();

  mai2led_Logic_To_Physical(req.start,
                            (uint8_t)(req.end - 1u),
                            &StartFadeLed, &EndFadeLed);

  if (req.speed == 0u)
  {
    (void)WS28XX_SetPixels_RGB(&hws,
                                StartFadeLed, EndFadeLed,
                                EndFadeColor.r,
                                EndFadeColor.g,
                                EndFadeColor.b);
    mai2led_cancel_fade();
    ack_init_ok(0u);
    return;
  }

  EndFadeTime =
    StartFadeTime + ((4095u / req.speed) * 8u);
  NeedFade = true;
  FadeStart = false;
  ack_init_ok(0u);
}

static void mai2led_SetLedGsUpdate(void)
{
  if (!NeedFade)
  {
    (void)led_try_update();
  }
  else
  {
    FadeStart = true;
  }

  ack_init_ok(0u);
}

static void mai2led_setLedFet(void)
{
  /* No separate physical Body/Ext/Side LEDs are configured. */
  ack_init_ok(0u);
}

static void mai2led_getBoardInfo(void)
{
  memcpy(ack.boardNo, "15070-04", 8u);
  ack.boardNo[8] = 0xFFu;
  ack.firmRevision = 0x90u;
  ack_init_ok(10u);
}

static void mai2led_getBoardStatus(void)
{
  ack.timeoutStat = 0u;
  ack.timeoutSec = 1u;
  ack.pwmIo = 0u;
  ack.fetTimeout = 0u;
  ack_init_ok(4u);
}

static void mai2led_getFirmSum(void)
{
  ack.sum_upper = 0u;
  ack.sum_lower = 0u;
  ack_init_ok(2u);
}

static void mai2led_getProtocolVersion(void)
{
  ack.appliMode = 1u;
  ack.major = 1u;
  ack.minor = 1u;
  ack_init_ok(3u);
}

static RGB_t RGB_Blend(RGB_t c1, RGB_t c2,
                       uint8_t amount)
{
  RGB_t out;

  out.r =
    (uint8_t)((((uint16_t)c1.r * (255u - amount)) +
               ((uint16_t)c2.r * amount)) / 255u);
  out.g =
    (uint8_t)((((uint16_t)c1.g * (255u - amount)) +
               ((uint16_t)c2.g * amount)) / 255u);
  out.b =
    (uint8_t)((((uint16_t)c1.b * (255u - amount)) +
               ((uint16_t)c2.b * amount)) / 255u);
  return out;
}

static void mai2led_update_fade(void)
{
  uint32_t now;
  uint8_t progress;
  RGB_t color;

  if ((!NeedFade) || (!FadeStart))
  {
    return;
  }

  now = HAL_GetTick();
  if (now >= EndFadeTime)
  {
    NeedFade = false;
    FadeStart = false;
    progress = 255u;
  }
  else
  {
    progress =
      (uint8_t)(((now - StartFadeTime) * 255u) /
                (EndFadeTime - StartFadeTime));
  }

  color = RGB_Blend(StartFadeColor, EndFadeColor,
                    progress);
  (void)WS28XX_SetPixels_RGB(&hws,
                              StartFadeLed, EndFadeLed,
                              color.r, color.g, color.b);
  (void)led_try_update();
}

bool AppMai2Led_Init(void)
{
  memset(&hws, 0, sizeof(hws));
  memset(&req, 0, sizeof(req));
  memset(&ack, 0, sizeof(ack));
  memset(dummyEEPRom, 0, sizeof(dummyEEPRom));
  memset(special_detector, 0, sizeof(special_detector));

  app_initialized = false;
  led_reconfigure_state = LED_RECONFIG_IDLE;
  led_reconfigure_target_total = 0u;
  led_dma_busy = true;
  led_flush_pending = false;
  packet_reset(true);
  led_config_apply();

  if (!WS28XX_Init(&hws, &htim17, 48u,
                   TIM_CHANNEL_1, WS28XX_PIXEL_MAX))
  {
    led_dma_busy = false;
    return false;
  }

  app_initialized = true;
  idle_task_last_tick = HAL_GetTick();
  AppMai2Led_RestoreIdleLights();
  return true;
}

void AppMai2Led_Task(void)
{
  uint8_t packet_cmd;
  uint32_t const now = HAL_GetTick();
  bool idle_lights_tick = false;

  if (!app_initialized)
  {
    return;
  }

  if ((uint32_t)(now - idle_task_last_tick) >=
      IDLE_TASK_INTERVAL_MS)
  {
    idle_task_last_tick = now;
    idle_lights_tick = true;
  }

  packet_cmd = packet_read();

  if (mai2led_command_is_io_activity(packet_cmd))
  {
    io_mark_active();
  }

  switch (packet_cmd)
  {
    case SPECIAL_MAGIC_CMD:
    {
      uint8_t idx = 0u;
      uint8_t key = 0u;
      uint8_t const status =
        key_config_process(&idx, &key);

      key_config_send_ack(status, idx, key);
      ack.command = 0u;
      return;
    }

    case AckStatus_SumError:
      ack_init(0u, AckStatus_SumError, 0u);
      break;

    case AckStatus_RecvBfOverFlow:
      ack_init(0u, AckStatus_RecvBfOverFlow, 0u);
      break;

    case SetLedGs8Bit:
      mai2led_setLedGs8Bit();
      break;

    case SetLedGs8BitMulti:
      mai2led_setLedGs8BitMulti();
      break;

    case SetLedGs8BitMultiFade:
      mai2led_setLedGs8BitMultiFade();
      break;

    case SetLedFet:
      mai2led_setLedFet();
      break;

    case SetLedGsUpdate:
      mai2led_SetLedGsUpdate();
      break;

    case SetEEPRom:
      if (req.Set_adress < sizeof(dummyEEPRom))
      {
        dummyEEPRom[req.Set_adress] = req.writeData;
        ack_init_ok(0u);
      }
      else
      {
        ack_init(0u, AckStatus_Ok,
                 AckReport_ParamError);
      }
      break;

    case GetEEPRom:
      if (req.Get_adress < sizeof(dummyEEPRom))
      {
        ack.eepData = dummyEEPRom[req.Get_adress];
        ack_init_ok(1u);
      }
      else
      {
        ack_init(0u, AckStatus_Ok,
                 AckReport_ParamError);
      }
      break;

    case GetBoardInfo:
      mai2led_getBoardInfo();
      break;

    case GetBoardStatus:
      mai2led_getBoardStatus();
      break;

    case GetFirmSum:
      mai2led_getFirmSum();
      break;

    case GetProtocolVersion:
      mai2led_getProtocolVersion();
      break;

    case SetEnableResponse:
    case SetDisableResponse:
    case 0u:
      break;

    case SetDcUpdate:
    case SetDc:
    default:
      ack_init_ok(0u);
      break;
  }

  packet_write();
  update_io_idle_clear(packet_cmd);
  mai2led_update_fade();
  led_reconfigure_task();

  if (idle_lights_tick &&
      (led_reconfigure_state == LED_RECONFIG_IDLE))
  {
    update_lights();
  }

  led_flush_pending_task();
}

void HAL_TIM_PWM_PulseFinishedCallback(TIM_HandleTypeDef *htim)
{
  if (htim != hws.hTim)
  {
    return;
  }

  (void)HAL_TIM_PWM_Stop_DMA(htim, hws.Channel);
  hws.Lock = 0u;
  led_dma_busy = false;

  if (led_reconfigure_state == LED_RECONFIG_CLEAR_IN_FLIGHT)
  {
    led_reconfigure_state = LED_RECONFIG_RELIGHT_PENDING;
  }
}

void HAL_TIM_ErrorCallback(TIM_HandleTypeDef *htim)
{
  if (htim != hws.hTim)
  {
    return;
  }

  (void)HAL_TIM_PWM_Stop_DMA(htim, hws.Channel);
  hws.Lock = 0u;
  led_dma_busy = false;
  led_flush_pending = true;

  if (led_reconfigure_state == LED_RECONFIG_CLEAR_IN_FLIGHT)
  {
    led_reconfigure_state = LED_RECONFIG_CLEAR_PENDING;
  }
}
