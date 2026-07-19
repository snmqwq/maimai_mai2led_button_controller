#ifndef APP_CONFIG_H
#define APP_CONFIG_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define APP_CONFIG_KEY_COUNT  13u

typedef enum
{
  APP_CONFIG_KEY_MODE_1P = 0u,
  APP_CONFIG_KEY_MODE_2P,
  APP_CONFIG_KEY_MODE_CUSTOM
} AppConfigKeyMode;

typedef enum
{
  APP_CONFIG_LOADED_DEFAULTS = 0u,
  APP_CONFIG_LOADED_FLASH
} AppConfigLoadResult;

typedef struct
{
  uint8_t leds_per_logic;
  uint8_t rainbow_enabled;
  uint8_t key_mode;
  uint8_t custom_keycodes[APP_CONFIG_KEY_COUNT];
} AppConfigData;

AppConfigLoadResult AppConfig_Init(void);
AppConfigLoadResult AppConfig_Reload(void);
AppConfigData const *AppConfig_Get(void);
uint8_t const *AppConfig_GetKeycodes(void);
bool AppConfig_Validate(AppConfigData const *config);
bool AppConfig_WriteCache(AppConfigData const *config);
void AppConfig_LoadDefaults(void);
bool AppConfig_SaveToFlash(void);
bool AppConfig_IsDirty(void);

#ifdef __cplusplus
}
#endif

#endif /* APP_CONFIG_H */
