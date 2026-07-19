#include "app_buttons.h"

#include "main.h"
#include "multi_button.h"

#define APP_BUTTON_TASK_INTERVAL_MS  (1000u / 500u)

typedef struct
{
  GPIO_TypeDef *port;
  uint16_t pin;
} AppButtonGpio;

static AppButtonGpio const app_button_gpio[APP_BUTTON_COUNT] =
{
  {BTN0_GPIO_Port, BTN0_Pin},
  {BTN1_GPIO_Port, BTN1_Pin},
  {BTN2_GPIO_Port, BTN2_Pin},
  {BTN3_GPIO_Port, BTN3_Pin},
  {BTN4_GPIO_Port, BTN4_Pin},
  {BTN5_GPIO_Port, BTN5_Pin},
  {BTN6_GPIO_Port, BTN6_Pin},
  {BTN7_GPIO_Port, BTN7_Pin},
  {BTN8_GPIO_Port, BTN8_Pin},
  {BTN9_GPIO_Port, BTN9_Pin},
  {BTN10_GPIO_Port, BTN10_Pin},
  {BTN11_GPIO_Port, BTN11_Pin},
  {BTN12_GPIO_Port, BTN12_Pin}
};

static Button app_buttons[APP_BUTTON_COUNT];
static uint16_t app_buttons_started_mask;
static uint32_t app_buttons_last_tick;

static uint8_t AppButtons_ReadLevel(uint8_t button_id)
{
  if (button_id >= APP_BUTTON_COUNT)
  {
    return 0u;
  }

  return (HAL_GPIO_ReadPin(app_button_gpio[button_id].port,
                           app_button_gpio[button_id].pin) == GPIO_PIN_SET) ? 1u : 0u;
}

static void AppButtons_Button08LongPressCallback(Button *handle, void *user_data)
{
  (void)handle;
  (void)user_data;
}

void AppButtons_Init(void)
{
  uint8_t button_id;

  app_buttons_started_mask = 0u;
  app_buttons_last_tick = HAL_GetTick();

  for (button_id = 0u; button_id < APP_BUTTON_COUNT; button_id++)
  {
    uint8_t const active_level = (button_id < APP_BUTTON_MAIN_COUNT) ? 1u : 0u;

    button_init(&app_buttons[button_id], AppButtons_ReadLevel, active_level, button_id);

    if (button_id == 8u)
    {
      button_attach(&app_buttons[button_id], BTN_LONG_PRESS_START,
                    AppButtons_Button08LongPressCallback, NULL);
    }

    /* A main button that is high during initialization is treated as absent. */
    if ((button_id < APP_BUTTON_MAIN_COUNT) &&
        (AppButtons_ReadLevel(button_id) != 0u))
    {
      continue;
    }

    if (button_start(&app_buttons[button_id]) == 0)
    {
      app_buttons_started_mask |= (uint16_t)(1u << button_id);
    }
  }
}

void AppButtons_Task(void)
{
  uint32_t const now = HAL_GetTick();

  while ((uint32_t)(now - app_buttons_last_tick) >= APP_BUTTON_TASK_INTERVAL_MS)
  {
    app_buttons_last_tick += APP_BUTTON_TASK_INTERVAL_MS;
    button_ticks();
  }
}

uint8_t AppButtons_IsStarted(uint8_t button_id)
{
  if (button_id >= APP_BUTTON_COUNT)
  {
    return 0u;
  }

  return ((app_buttons_started_mask & (uint16_t)(1u << button_id)) != 0u) ? 1u : 0u;
}

uint8_t AppButtons_IsPressed(uint8_t button_id)
{
  if (AppButtons_IsStarted(button_id) == 0u)
  {
    return 0u;
  }

  return (button_is_pressed(&app_buttons[button_id]) > 0) ? 1u : 0u;
}

uint16_t AppButtons_GetPressedMask(void)
{
  uint16_t pressed_mask = 0u;
  uint8_t button_id;

  for (button_id = 0u; button_id < APP_BUTTON_COUNT; button_id++)
  {
    if (AppButtons_IsPressed(button_id) != 0u)
    {
      pressed_mask |= (uint16_t)(1u << button_id);
    }
  }

  return pressed_mask;
}
