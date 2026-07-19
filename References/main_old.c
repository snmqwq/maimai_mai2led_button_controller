/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"
#include "dma.h"
#include "tim.h"
#include "usb.h"
#include "gpio.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include "tusb.h"
#include "usb_descriptors.h"
#include "ws28xx.h"
#include "mai2led.h"
#include "multi_button.h"
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */
#define mai2led_cdc_itf 0
#define BTN_NUM  13
/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/

/* USER CODE BEGIN PV */

/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
/* USER CODE BEGIN PFP */

/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */
WS28XX_HandleTypeDef hws;

//--------------------------------------------------------------------+
// Global Config / Forward Declarations
//--------------------------------------------------------------------+

extern uint8_t hid_key_map[BTN_NUM];

static uint8_t key_config_read_packet(uint8_t *cmd, uint8_t *idx, uint8_t *key);
static uint8_t key_config_process(uint8_t *out_idx, uint8_t *out_key);
static void key_config_send_ack(uint8_t status, uint8_t idx, uint8_t key);
static void led_idle_config_changed(void);
static void idle_lights_restore(void);
static void btn8_long_press_cb(Button* handle, void* user_data);
static void mai2led_cancel_fade(void);

void keymap_load_default(void);
bool keymap_load_from_flash(void);
bool keymap_save_to_flash(void);
void led_config_load_default(void);


#define LOGIC_LED_COUNT             8
#define IDLE_RESTORE_BUTTON_ID      8
#define LEDS_PER_LOGIC_DEFAULT      2
#define LEDS_PER_LOGIC_MIN          1
#define LEDS_PER_LOGIC_MAX          4
#define LED_TOTAL_MAX               (LOGIC_LED_COUNT * LEDS_PER_LOGIC_MAX)
#define RAINBOW_ENABLED_DEFAULT     true
#define RAINBOW_HUE_STEP            1
#define RAINBOW_UPDATE_DIVIDER      50

static uint8_t leds_per_logic = LEDS_PER_LOGIC_DEFAULT;
static uint16_t led_total = LOGIC_LED_COUNT * LEDS_PER_LOGIC_DEFAULT;
static bool rainbow_enabled = RAINBOW_ENABLED_DEFAULT;
static bool idle_lights_enabled = true;
static bool idle_lights_refresh_requested = true;
static bool io_idle_clear_pending = false;

static bool led_config_is_valid(uint8_t value)
{
	return (value >= LEDS_PER_LOGIC_MIN) && (value <= LEDS_PER_LOGIC_MAX);
}

void led_config_load_default(void)
{
	leds_per_logic = LEDS_PER_LOGIC_DEFAULT;
	rainbow_enabled = RAINBOW_ENABLED_DEFAULT;
	led_total = LOGIC_LED_COUNT * leds_per_logic;
}

static void led_config_apply(void)
{
	led_total = LOGIC_LED_COUNT * leds_per_logic;

	if(hws.hTim != NULL)
	{
		hws.MaxPixel = led_total;
	}
}

bool WS28XX_SetPixels_RGB(WS28XX_HandleTypeDef *hLed,
        uint16_t StartPixel,
        uint16_t EndPixel,
        uint8_t Red,
        uint8_t Green,
        uint8_t Blue)
{
	bool answer = true;

	do
	{
		if (hLed == NULL)
		{
			answer = false;
			break;
		}

		if (StartPixel > EndPixel)
		{
			answer = false;
			break;
		}

		if (EndPixel >= hLed->MaxPixel)
		{
			answer = false;
			break;
		}

		for (uint16_t pixel = StartPixel; pixel <= EndPixel; pixel++)
		{
			if (WS28XX_SetPixel_RGB(hLed, pixel, Red, Green, Blue) == false)
			{
				answer = false;
				break;
			}
		}
	}
	while (0);

	return answer;
}

#define SPECIAL_SEQ_LEN  8

#define SPECIAL_MAGIC_CMD  0xB7

#define KEYCFG_SET_KEY       0xA1
#define KEYCFG_SAVE_FLASH    0xA2
#define KEYCFG_LOAD_DEFAULT  0xA3
#define KEYCFG_GET_KEY       0xA4
#define KEYCFG_SET_LEDS_PER_LOGIC  0xA5
#define KEYCFG_GET_LEDS_PER_LOGIC  0xA6
#define KEYCFG_SET_RAINBOW_ENABLED 0xA7
#define KEYCFG_GET_RAINBOW_ENABLED 0xA8

#define KEYCFG_OK            0x00
#define KEYCFG_SUM_ERROR     0x01
#define KEYCFG_INDEX_ERROR   0x02
#define KEYCFG_CMD_ERROR     0x03

static const uint8_t special_seq[SPECIAL_SEQ_LEN] =
{
	0x91,
	0x3e,
	0xed,
	0x20,
	0x7c,
	0x99,
	0x58,
	0xac
};

static bool special_sequence_detect(uint8_t r)
{
    static uint8_t detector[8];

    memmove(&detector[0],
            &detector[1],
            7);

    detector[7] = r;

    if (memcmp(detector,
               special_seq,
               8) == 0)
    {
        return true;
    }

    return false;
}

static uint8_t key_config_read_packet(uint8_t *cmd,
                                      uint8_t *idx,
                                      uint8_t *key)
{
    uint8_t buf[4];
    uint8_t count = 0;

    uint32_t start = HAL_GetTick();

    while(count < 4)
    {
        tud_task();

        if(tud_cdc_n_available(mai2led_cdc_itf))
        {
            tud_cdc_n_read(mai2led_cdc_itf, &buf[count], 1);
            count++;
        }

        // 超时，防止卡死
        if(HAL_GetTick() - start > 100)
        {
            return KEYCFG_CMD_ERROR;
        }
    }

    uint8_t sum = buf[0] + buf[1] + buf[2];

    if(sum != buf[3])
    {
        return KEYCFG_SUM_ERROR;
    }

    *cmd = buf[0];
    *idx = buf[1];
    *key = buf[2];

    return KEYCFG_OK;
}

static uint8_t key_config_process(uint8_t *out_idx, uint8_t *out_key)
{
    uint8_t cmd;
    uint8_t idx;
    uint8_t key;

    uint8_t ret = key_config_read_packet(&cmd, &idx, &key);

    if(ret != KEYCFG_OK)
    {
        return ret;
    }

    *out_idx = idx;
    *out_key = key;

    switch(cmd)
    {
        case KEYCFG_SET_KEY:
            if(idx >= BTN_NUM)
            {
                return KEYCFG_INDEX_ERROR;
            }

            hid_key_map[idx] = key;
            return KEYCFG_OK;

        case KEYCFG_SAVE_FLASH:
            return keymap_save_to_flash() ? KEYCFG_OK : KEYCFG_CMD_ERROR;

        case KEYCFG_LOAD_DEFAULT:
            keymap_load_default();
            led_config_load_default();
            led_config_apply();
            led_idle_config_changed();
            return keymap_save_to_flash() ? KEYCFG_OK : KEYCFG_CMD_ERROR;

        case KEYCFG_GET_KEY:
            if(idx >= BTN_NUM)
            {
                return KEYCFG_INDEX_ERROR;
            }

            *out_key = hid_key_map[idx];
            return KEYCFG_OK;

        case KEYCFG_SET_LEDS_PER_LOGIC:
            if(idx != 0 || !led_config_is_valid(key))
            {
                return KEYCFG_INDEX_ERROR;
            }

            leds_per_logic = key;
            led_config_apply();
            led_idle_config_changed();
            *out_key = leds_per_logic;
            return KEYCFG_OK;

        case KEYCFG_GET_LEDS_PER_LOGIC:
            if(idx != 0)
            {
                return KEYCFG_INDEX_ERROR;
            }

            *out_key = leds_per_logic;
            return KEYCFG_OK;

        case KEYCFG_SET_RAINBOW_ENABLED:
            if(idx != 0 || key > 1)
            {
                return KEYCFG_INDEX_ERROR;
            }

            rainbow_enabled = key ? true : false;
            led_idle_config_changed();
            *out_key = rainbow_enabled ? 1 : 0;
            return KEYCFG_OK;

        case KEYCFG_GET_RAINBOW_ENABLED:
            if(idx != 0)
            {
                return KEYCFG_INDEX_ERROR;
            }

            *out_key = rainbow_enabled ? 1 : 0;
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

    buf[0] = 0xAC;
    buf[1] = status;
    buf[2] = idx;
    buf[3] = key;

    tud_cdc_n_write(mai2led_cdc_itf, buf, sizeof(buf));
    tud_cdc_n_write_flush(mai2led_cdc_itf);
}

static PacketReq req;
static PacketAck ack;

static uint8_t packet_read(void)
{
    static uint8_t r_len = 0;
    static uint8_t r = 0;
    static uint8_t checksum = 0;
    static bool escape = false;

    while (tud_cdc_n_available(mai2led_cdc_itf))
    {
        tud_cdc_n_read(mai2led_cdc_itf, &r, 1);

        // 优先检测 8 字节 magic sequence
        if (special_sequence_detect(r))
        {
            r_len = 0;
            checksum = 0;
            escape = false;
            memset(&req, 0, sizeof(req));

            return SPECIAL_MAGIC_CMD;
        }

        // 收到同步头，重新开始一包
        if (r == Sync)
        {
            r_len = 0;
            checksum = 0;
            escape = false;
            memset(&req, 0, sizeof(req));

            continue;
        }

        // 转义标记
        if (r == Marker)
        {
            escape = true;
            continue;
        }

        // 处理转义后的数据
        if (escape)
        {
            r++;
            escape = false;
        }

        // 包体接收完成，当前 r 是 checksum
        if (r_len == req.length + 3)
        {
            uint8_t ret;

            if (checksum == r)
            {
                ret = req.command;
            }
            else
            {
                ret = AckStatus_SumError;
            }

            // 关键：无论成功还是失败，返回前都清状态
            r_len = 0;
            checksum = 0;
            escape = false;

            return ret;
        }

        // 防止 req.buffer 越界
        if (r_len >= sizeof(req.buffer))
        {
            r_len = 0;
            checksum = 0;
            escape = false;
            memset(&req, 0, sizeof(req));

            return AckStatus_RecvBfOverFlow;
        }

        req.buffer[r_len++] = r;
        checksum += r;
    }

    return 0;
}

/*
static uint8_t packet_read()
{
	static uint8_t r_len, r, checksum;
	static bool escape = false;
	while (tud_cdc_n_available(mai2led_cdc_itf))
	{

		tud_cdc_n_read(mai2led_cdc_itf, &r, 1);

		if (special_sequence_detect(r))
		{
			r_len = 0;
			checksum = 0;
			escape = false;

			return SPECIAL_MAGIC_CMD;
		}

		if (r == Sync) {
			r_len = 0;
			checksum = 0;
			continue;
		}
		if (r == Marker) {
			escape = true;
			continue;
		}
		if (escape) {
			r++;
			escape = false;
		}

		if (r_len == req.length + 3) {
			if (checksum == r) {
				return req.command;
			}
			return AckStatus_SumError;
		}
		req.buffer[r_len++] = r;
		checksum += r;
	}
	return 0;
}
*/
void packet_write()
{
	if (ack.command == 0)
	{
		return;
	}
	uint8_t checksum = 0, w_len = 0, data;

	data=Sync;
	tud_cdc_n_write(mai2led_cdc_itf, &data, 1);

	while (w_len < ack.length + 3)
	{
		uint8_t w;
		w = ack.buffer[w_len++];
		checksum += w;
		if (w == Sync || w == Marker)
		{
			data = Marker;
			tud_cdc_n_write(mai2led_cdc_itf, &data, 1);
			data = --w;
			tud_cdc_n_write(mai2led_cdc_itf, &data, 1);
		}
		else
		{
			data = w;
			tud_cdc_n_write(mai2led_cdc_itf, &data, 1);
		}
	}
	data = checksum;
	tud_cdc_n_write(mai2led_cdc_itf, &data, 1);
	tud_cdc_n_write_flush(mai2led_cdc_itf);
	ack.command = 0;
}

static uint8_t dummyEEPRom[8] = { 0, 0, 0, 0, 0, 0, 0, 0 };

void ack_init(uint8_t length, uint8_t status, uint8_t report)
{
	ack.dstNodeID = req.srcNodeID;
	ack.srcNodeID = req.dstNodeID;
	ack.length = 3 + length;
	ack.status = status;
	ack.command = req.command;
	ack.report = report;
}

void mai2led_Logic_To_Physical(uint8_t start_logic,
					   uint8_t end_logic,
					   uint8_t *start_phy,
					   uint8_t *end_phy)
{
	if(start_phy == NULL || end_phy == NULL)
	{
	    return;
	}

    // 闄愬埗杈撳叆鑼冨洿 0~7
    if(start_logic >= LOGIC_LED_COUNT) start_logic = LOGIC_LED_COUNT - 1;
    if(end_logic >= LOGIC_LED_COUNT)   end_logic = LOGIC_LED_COUNT - 1;

    // 鑻ヨ緭鍏ュ弽浜嗭紝鑷姩浜ゆ崲
    if(start_logic > end_logic)
    {
        uint8_t temp = start_logic;
        start_logic = end_logic;
        end_logic = temp;
    }

    *start_phy = start_logic * leds_per_logic;
    *end_phy   = ((end_logic + 1) * leds_per_logic) - 1;
}

void mai2led_setLedGs8Bit()
{
	mai2led_cancel_fade();

	uint8_t start_phy, end_phy;
    mai2led_Logic_To_Physical(req.index, req.index, &start_phy, &end_phy);

    WS28XX_SetPixels_RGB(&hws, start_phy,  end_phy, req.color[0], req.color[1], req.color[2]);
    WS28XX_Update(&hws);

    ack_init(0, AckStatus_Ok, AckReport_Ok);
}

typedef struct
{
    uint8_t r;
    uint8_t g;
    uint8_t b;
} RGB_t;

static uint8_t rainbow_hue = 0;
static uint8_t rainbow_update_divider = 0;

static bool mai2led_command_is_io_activity(uint8_t command)
{
	switch(command)
	{
		case 0:
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
	switch(command)
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

static RGB_t rainbow_color(uint8_t pos)
{
	RGB_t color;

	pos = 255 - pos;

	if(pos < 85)
	{
		color.r = 255 - pos * 3;
		color.g = 0;
		color.b = pos * 3;
	}
	else if(pos < 170)
	{
		pos -= 85;
		color.r = 0;
		color.g = pos * 3;
		color.b = 255 - pos * 3;
	}
	else
	{
		pos -= 170;
		color.r = pos * 3;
		color.g = 255 - pos * 3;
		color.b = 0;
	}

	return color;
}

static void clear_button_lights(void)
{
	WS28XX_SetPixels_RGB(&hws, 0, led_total - 1, 0, 0, 0);
}

static void set_button_lights_white(void)
{
	WS28XX_SetPixels_RGB(&hws, 0, led_total - 1, 255, 255, 255);
}

static void led_idle_config_changed(void)
{
	if(idle_lights_enabled)
	{
		idle_lights_refresh_requested = true;
		rainbow_update_divider = 0;
	}
}

static void io_mark_active(void)
{
	if(idle_lights_enabled)
	{
		clear_button_lights();
		idle_lights_enabled = false;
		idle_lights_refresh_requested = false;
		io_idle_clear_pending = true;
		rainbow_update_divider = 0;
	}
}

static void draw_button_rainbow(uint8_t hue)
{
	for(uint8_t i = 0; i < LOGIC_LED_COUNT; i++)
	{
		uint8_t start_phy;
		uint8_t end_phy;
		RGB_t color = rainbow_color(hue + (uint8_t)(i * (256 / LOGIC_LED_COUNT)));

		mai2led_Logic_To_Physical(i, i, &start_phy, &end_phy);
		WS28XX_SetPixels_RGB(&hws, start_phy, end_phy, color.r, color.g, color.b);
	}
}

static bool flush_button_rainbow(void)
{
	draw_button_rainbow(rainbow_hue);

	if(WS28XX_Update(&hws))
	{
		rainbow_hue += RAINBOW_HUE_STEP;
		return true;
	}

	return false;
}

static bool update_button_rainbow(void)
{
	if(rainbow_update_divider > 0)
	{
		rainbow_update_divider--;
		return false;
	}

	if(flush_button_rainbow())
	{
		rainbow_update_divider = RAINBOW_UPDATE_DIVIDER - 1;
		return true;
	}

	return false;
}

static void idle_lights_restore(void)
{
	if(hws.hTim == NULL)
	{
		return;
	}

	idle_lights_enabled = true;
	idle_lights_refresh_requested = true;
	io_idle_clear_pending = false;
	rainbow_update_divider = 0;
	mai2led_cancel_fade();
}

static void btn8_long_press_cb(Button* handle, void* user_data)
{
	(void)handle;
	(void)user_data;

	idle_lights_restore();
}

static void update_lights(void)
{
	if(!idle_lights_enabled)
	{
		return;
	}

	if(idle_lights_refresh_requested)
	{
		idle_lights_refresh_requested = false;

		if(rainbow_enabled)
		{
			if(flush_button_rainbow())
			{
				rainbow_update_divider = RAINBOW_UPDATE_DIVIDER - 1;
			}
			else
			{
				idle_lights_refresh_requested = true;
			}
		}
		else
		{
			set_button_lights_white();

			if(!WS28XX_Update(&hws))
			{
				idle_lights_refresh_requested = true;
			}
		}

		return;
	}

	if(rainbow_enabled)
	{
		update_button_rainbow();
	}
}

static void update_io_idle_clear(uint8_t command)
{
	if(!io_idle_clear_pending)
	{
		return;
	}

	if(mai2led_command_writes_button_lights(command))
	{
		io_idle_clear_pending = false;
		return;
	}

	if(WS28XX_Update(&hws))
	{
		io_idle_clear_pending = false;
	}
}

uint32_t StartFadeTime, EndFadeTime, NowFadeTime;
uint8_t StartFadeLed, EndFadeLed, progress;
RGB_t StartFadeColor, EndFadeColor, NowFadeColor;
bool NeedFade = false, FadeStart = false;

static void mai2led_cancel_fade(void)
{
	NeedFade = false;
	FadeStart = false;
}

void mai2led_setLedGs8BitMulti()
{
	mai2led_cancel_fade();

	if (req.end == 0x20)
	{
		req.end = LOGIC_LED_COUNT;
	}

	mai2led_Logic_To_Physical(req.start, req.end - 1, &StartFadeLed, &EndFadeLed);

	StartFadeColor.r = req.Multi_color[0];
	StartFadeColor.g = req.Multi_color[1];
	StartFadeColor.b = req.Multi_color[2];

	WS28XX_SetPixels_RGB(&hws, StartFadeLed, EndFadeLed, StartFadeColor.r, StartFadeColor.g, StartFadeColor.b);

	ack_init(0, AckStatus_Ok, AckReport_Ok);
}

void mai2led_setLedGs8BitMultiFade()
{
	EndFadeColor.r = req.Multi_color[0];
	EndFadeColor.g = req.Multi_color[1];
	EndFadeColor.b = req.Multi_color[2];
	StartFadeTime = HAL_GetTick();
	mai2led_Logic_To_Physical(req.start, req.end - 1, &StartFadeLed, &EndFadeLed);

	if(req.speed == 0)
	{
		WS28XX_SetPixels_RGB(&hws, StartFadeLed, EndFadeLed, EndFadeColor.r, EndFadeColor.g, EndFadeColor.b);
		mai2led_cancel_fade();
		ack_init(0, AckStatus_Ok, AckReport_Ok);
		return;
	}

	EndFadeTime = StartFadeTime + (4095 / req.speed * 8);
	NeedFade = true;
	FadeStart = false;
	ack_init(0, AckStatus_Ok, AckReport_Ok);
}

void mai2led_SetLedGsUpdate()
{
	if(!NeedFade)
	{
		WS28XX_Update(&hws);
	}
	else
	{
		FadeStart = true;
	}
	ack_init(0, AckStatus_Ok, AckReport_Ok);
}

void mai2led_setLedFet()
{
#if NUM_LEDS > 8
	leds[8] = blend(0x000000, 0xFFFFFF, req.BodyLed);
	leds[9] = blend(0x000000, 0xFFFFFF, req.ExtLed);    //same as BodyLed
	leds[10] = blend(0x000000, 0xFFFFFF, req.SideLed);  //00 or FF
	FastLED.show();
#endif
	ack_init(0, AckStatus_Ok, AckReport_Ok);
}

void mai2led_getBoardInfo()
{
	memcpy(ack.boardNo, "15070-04\xFF\x90\x00", 10);
	ack.firmRevision = 144;
	ack_init(10, AckStatus_Ok, AckReport_Ok);
}

void mai2led_getBoardStatus() {  // unknown
	ack.timeoutStat = 0;
	ack.timeoutSec = 1;
	ack.pwmIo = 0;
	ack.fetTimeout = 0;
	ack_init(4, AckStatus_Ok, AckReport_Ok);
}

void mai2led_getFirmSum() {  // unknown
	ack.sum_upper = 0;
	ack.sum_lower = 0;
	ack_init(2, AckStatus_Ok, AckReport_Ok);
}

void mai2led_getProtocolVersion() {  // unknown
	ack.appliMode = 1;         // IsNeedFirmUpdate = false
	ack.major = 1;
	ack.minor = 1;
	ack_init(3, AckStatus_Ok, AckReport_Ok);
}

int32_t map_int32(int32_t x,
                  int32_t in_min,
                  int32_t in_max,
                  int32_t out_min,
                  int32_t out_max)
{
    if(in_max == in_min)
    {
        return out_min;
    }

    return (x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min;
}

RGB_t RGB_Blend(RGB_t c1, RGB_t c2, uint8_t amount)
{
    RGB_t out;

    out.r = ((uint16_t)c1.r * (255 - amount) + (uint16_t)c2.r * amount) / 255;
    out.g = ((uint16_t)c1.g * (255 - amount) + (uint16_t)c2.g * amount) / 255;
    out.b = ((uint16_t)c1.b * (255 - amount) + (uint16_t)c2.b * amount) / 255;

    return out;
}

uint8_t key_raw_level[BTN_NUM];
uint8_t key_state[BTN_NUM];
Button btn[BTN_NUM];


static const uint8_t default_hid_key_map[BTN_NUM] =
{
    HID_KEY_W,
    HID_KEY_E,
    HID_KEY_D,
    HID_KEY_C,
    HID_KEY_X,
    HID_KEY_Z,
    HID_KEY_A,
    HID_KEY_Q,
	HID_KEY_3,
	HID_KEY_KEYPAD_MULTIPLY,
	HID_KEY_9,
	HID_KEY_0,
	HID_KEY_ENTER
};

uint8_t hid_key_map[BTN_NUM];

uint8_t mai2_read_button_gpio(uint8_t button_id)
{
    switch(button_id)
    {
        case 0:
            return HAL_GPIO_ReadPin(GPIOA, GPIO_PIN_0);

        case 1:
            return HAL_GPIO_ReadPin(GPIOA, GPIO_PIN_1);

        case 2:
            return HAL_GPIO_ReadPin(GPIOA, GPIO_PIN_2);

        case 3:
            return HAL_GPIO_ReadPin(GPIOA, GPIO_PIN_3);

        case 4:
        	return HAL_GPIO_ReadPin(GPIOA, GPIO_PIN_4);

        case 5:
        	return HAL_GPIO_ReadPin(GPIOA, GPIO_PIN_5);

        case 6:
        	return HAL_GPIO_ReadPin(GPIOA, GPIO_PIN_6);

        case 7:
        	return HAL_GPIO_ReadPin(GPIOA, GPIO_PIN_7);

        case 8:
        	return HAL_GPIO_ReadPin(GPIOB, GPIO_PIN_0);

        case 9:
        	return HAL_GPIO_ReadPin(GPIOB, GPIO_PIN_1);

        case 10:
        	return HAL_GPIO_ReadPin(GPIOB, GPIO_PIN_2);

        case 11:
        	return HAL_GPIO_ReadPin(GPIOB, GPIO_PIN_10);

        case 12:
        	return HAL_GPIO_ReadPin(GPIOB, GPIO_PIN_11);

        default:
            return 1;
    }
}

void mai2_scan_all_buttons(void)
{
	for (uint8_t i = 0; i < BTN_NUM; i++)
    {
        key_raw_level[i] = mai2_read_button_gpio(i);
    }
}

void hid_key_task(void)
{
    hid_keyboard_13kro_report_t report;
    memset(&report, 0, sizeof(report));

    uint8_t key_count = 0;

    for(uint8_t i = 0; i < BTN_NUM; i++)
    {
        if(button_is_pressed(&btn[i]))
        {
            key_state[i] = 1;

            if(key_count < 13)
            {
                report.keycode[key_count++] = hid_key_map[i];
            }
        }
        else
        {
            key_state[i] = 0;
        }
    }

    if(tud_hid_n_ready(0))
    {
        tud_hid_n_report(0, 0, &report, sizeof(report));
    }
}

#define KEYMAP_FLASH_MAGIC     0x4B4D4150UL  // 'KMAP'
#define KEYMAP_FLASH_VERSION   0x0002

#define FLASH_SIZE_BYTES       (128U * 1024U)
#define FLASH_BASE_ADDR        0x08000000UL
#define KEYMAP_FLASH_ADDR      (FLASH_BASE_ADDR + FLASH_SIZE_BYTES - FLASH_PAGE_SIZE)

typedef struct __attribute__((packed))
{
    uint32_t magic;
    uint16_t version;
    uint16_t length;
    uint8_t  keymap[BTN_NUM];
    uint8_t  leds_per_logic;
    uint8_t  rainbow_enabled;
    uint8_t  checksum;
    uint8_t  reserved[1];   // 补齐为偶数字节，方便 halfword 写入
} keymap_flash_t;

static uint8_t keymap_calc_checksum(uint8_t const *data, uint16_t len)
{
    uint8_t sum = 0;

    for(uint16_t i = 0; i < len; i++)
    {
        sum += data[i];
    }

    return sum;
}

static uint8_t config_calc_checksum(uint8_t const *keymap,
                                    uint16_t keymap_len,
                                    uint8_t leds_per_logic_value,
                                    uint8_t rainbow_enabled_value)
{
    return keymap_calc_checksum(keymap, keymap_len) +
           leds_per_logic_value +
           rainbow_enabled_value;
}

void keymap_load_default(void)
{
    memcpy(hid_key_map, default_hid_key_map, BTN_NUM);
}

static bool config_load_default_and_save(void)
{
    keymap_load_default();
    led_config_load_default();
    return keymap_save_to_flash();
}

bool keymap_load_from_flash(void)
{
    const keymap_flash_t *flash_data = (const keymap_flash_t *)KEYMAP_FLASH_ADDR;

    if(flash_data->magic != KEYMAP_FLASH_MAGIC)
    {
        config_load_default_and_save();
        return false;
    }

    if(flash_data->version != KEYMAP_FLASH_VERSION)
    {
        config_load_default_and_save();
        return false;
    }

    if(flash_data->length != BTN_NUM)
    {
        config_load_default_and_save();
        return false;
    }

    if(!led_config_is_valid(flash_data->leds_per_logic) ||
       flash_data->rainbow_enabled > 1)
    {
        config_load_default_and_save();
        return false;
    }

    if(config_calc_checksum(flash_data->keymap,
                            BTN_NUM,
                            flash_data->leds_per_logic,
                            flash_data->rainbow_enabled) != flash_data->checksum)
    {
        config_load_default_and_save();
        return false;
    }

    memcpy(hid_key_map, flash_data->keymap, BTN_NUM);
    leds_per_logic = flash_data->leds_per_logic;
    rainbow_enabled = flash_data->rainbow_enabled ? true : false;
    led_config_apply();
    return true;
}

bool keymap_save_to_flash(void)
{
    keymap_flash_t data;

    memset(&data, 0xFF, sizeof(data));

    data.magic = KEYMAP_FLASH_MAGIC;
    data.version = KEYMAP_FLASH_VERSION;
    data.length = BTN_NUM;
    memcpy(data.keymap, hid_key_map, BTN_NUM);
    data.leds_per_logic = leds_per_logic;
    data.rainbow_enabled = rainbow_enabled ? 1 : 0;
    data.checksum = config_calc_checksum(data.keymap,
                                         BTN_NUM,
                                         data.leds_per_logic,
                                         data.rainbow_enabled);

    HAL_FLASH_Unlock();

    FLASH_EraseInitTypeDef erase;
    uint32_t page_error = 0;

    erase.TypeErase = FLASH_TYPEERASE_PAGES;
    erase.PageAddress = KEYMAP_FLASH_ADDR;
    erase.NbPages = 1;

    if(HAL_FLASHEx_Erase(&erase, &page_error) != HAL_OK)
    {
        HAL_FLASH_Lock();
        return false;
    }

    uint8_t *p = (uint8_t *)&data;

    for(uint32_t i = 0; i < sizeof(data); i += 2)
    {
        uint16_t halfword = p[i];

        if((i + 1) < sizeof(data))
        {
            halfword |= ((uint16_t)p[i + 1] << 8);
        }
        else
        {
            halfword |= 0xFF00;
        }

        if(HAL_FLASH_Program(FLASH_TYPEPROGRAM_HALFWORD,
                             KEYMAP_FLASH_ADDR + i,
                             halfword) != HAL_OK)
        {
            HAL_FLASH_Lock();
            return false;
        }
    }

    HAL_FLASH_Lock();
    return true;
}

volatile bool hid_scan_flag = false;
volatile bool button_tick_flag = false;

void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *htim)
{
	switch((uint32_t)htim->Instance)
	{
	    case (uint32_t)TIM6:
	    	button_tick_flag = true;
	        break;

	    case (uint32_t)TIM7:
	    	hid_scan_flag = true;
	        break;

	    default:
	        break;
	}
}
/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{

  /* USER CODE BEGIN 1 */

  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_DMA_Init();
  MX_TIM6_Init();
  MX_TIM7_Init();
  MX_TIM17_Init();
  MX_USB_PCD_Init();
  /* USER CODE BEGIN 2 */

	keymap_load_from_flash();

  	for (uint8_t i = 0; i < BTN_NUM; i++)
	{
		uint8_t active;

		if (i <= 7)
			active = 1;
		else
			active = 0;

		button_init(&btn[i], mai2_read_button_gpio, active, i);
	}

	button_attach(&btn[IDLE_RESTORE_BUTTON_ID],
	              BTN_LONG_PRESS_START,
	              btn8_long_press_cb,
	              NULL);

    mai2_scan_all_buttons();

    for (uint8_t i = 0; i < BTN_NUM; i++)
    {
    	if (i <= 7)
    	{
    		if(!key_raw_level[i])
    		{
    			button_start(&btn[i]);
    		}
    	}
    	else
    	{
    		button_start(&btn[i]);
    	}
    }
    HAL_TIM_Base_Start_IT(&htim6);


    WS28XX_Init(&hws, &htim17, 48, TIM_CHANNEL_1, led_total);
    idle_lights_restore();

    tusb_init();

    HAL_Delay(100);
    HAL_TIM_Base_Start_IT(&htim7);

  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
	while (1)
	{
		bool idle_lights_tick = false;

		tud_task();

		if(button_tick_flag)
		{
			button_tick_flag = false;
			button_ticks();
		}

		if(hid_scan_flag)
		{
			hid_scan_flag = false;
			hid_key_task();
			idle_lights_tick = true;
		}

		uint8_t packet_cmd = packet_read();

		if(mai2led_command_is_io_activity(packet_cmd))
		{
			io_mark_active();
		}

		switch(packet_cmd)
		{
			case SPECIAL_MAGIC_CMD:
			{
				uint8_t idx = 0;
				uint8_t key = 0;

				uint8_t status = key_config_process(&idx, &key);
				key_config_send_ack(status, idx, key);

				ack.command = 0;   // 清掉 Mai2LED ACK
				continue;          // 跳过 packet_write()
			}

			case AckStatus_SumError:
				ack_init(0, AckStatus_SumError, 0);
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
			 	dummyEEPRom[req.Set_adress] = req.writeData;
			  	ack_init(0, AckStatus_Ok, AckReport_Ok);
			  	break;
			case GetEEPRom:
			  	ack.eepData = dummyEEPRom[req.Get_adress];
			 	ack_init(1, AckStatus_Ok, AckReport_Ok);
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
			case 0:
				break;
			default:
			  	ack_init(0, AckStatus_Ok, AckReport_Ok);
		}
		packet_write();
		update_io_idle_clear(packet_cmd);

		if(NeedFade == true && FadeStart == true)
		{
			NowFadeTime = HAL_GetTick();
			if(NowFadeTime >= EndFadeTime)
			{
				NeedFade = false;
				FadeStart = false;
				progress = 255;
			}
			else
			{
				progress = map_int32(NowFadeTime, StartFadeTime, EndFadeTime, 0, 255);
			}

			NowFadeColor = RGB_Blend(StartFadeColor, EndFadeColor, progress);

			WS28XX_SetPixels_RGB(&hws, StartFadeLed, EndFadeLed, NowFadeColor.r, NowFadeColor.g, NowFadeColor.b);
			WS28XX_Update(&hws);
		}

		if(idle_lights_tick)
		{
			update_lights();
		}
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
	}
  /* USER CODE END 3 */
}

/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};
  RCC_PeriphCLKInitTypeDef PeriphClkInit = {0};

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI48;
  RCC_OscInitStruct.HSI48State = RCC_HSI48_ON;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_NONE;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_HSI48;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV1;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_1) != HAL_OK)
  {
    Error_Handler();
  }
  PeriphClkInit.PeriphClockSelection = RCC_PERIPHCLK_USB;
  PeriphClkInit.UsbClockSelection = RCC_USBCLKSOURCE_HSI48;

  if (HAL_RCCEx_PeriphCLKConfig(&PeriphClkInit) != HAL_OK)
  {
    Error_Handler();
  }
}

/* USER CODE BEGIN 4 */

void HAL_TIM_PWM_PulseFinishedCallback(TIM_HandleTypeDef *htim)
{
    if(htim == hws.hTim)
    {
        (void)HAL_TIM_PWM_Stop_DMA(htim, hws.Channel);
        hws.Lock = 0;
    }
}

void HAL_TIM_ErrorCallback(TIM_HandleTypeDef *htim)
{
    if(htim == hws.hTim)
    {
        (void)HAL_TIM_PWM_Stop_DMA(htim, hws.Channel);
        hws.Lock = 0;
    }
}

void tud_hid_set_report_cb(uint8_t instance,
                           uint8_t report_id,
                           hid_report_type_t report_type,
                           uint8_t const* buffer,
                           uint16_t bufsize)
{
    (void) instance;
    (void) report_id;
    (void) report_type;
    (void) buffer;
    (void) bufsize;
}

uint16_t tud_hid_get_report_cb(uint8_t instance,
                               uint8_t report_id,
                               hid_report_type_t report_type,
                               uint8_t* buffer,
                               uint16_t reqlen)
{
    (void) instance;
    (void) report_id;
    (void) report_type;
    (void) buffer;
    (void) reqlen;

    return 0;
}

/* USER CODE END 4 */

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  /* User can add his own implementation to report the HAL error return state */
  __disable_irq();
  while (1)
  {
  }
  /* USER CODE END Error_Handler_Debug */
}
#ifdef USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
