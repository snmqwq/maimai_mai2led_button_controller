#include "app_config.h"

#include <stddef.h>
#include <string.h>

#include "class/hid/hid.h"
#include "main.h"

#define APP_CONFIG_MAGIC              0x4D324346u
#define APP_CONFIG_FORMAT_VERSION     1u
#define APP_CONFIG_COMMIT_MARKER      0xA55Au
#define APP_CONFIG_FLASH_PAGE_BYTES   FLASH_PAGE_SIZE

typedef struct
{
  uint32_t magic;
  uint16_t format_version;
  uint16_t data_size;
  uint32_t sequence;
  AppConfigData data;
  uint32_t crc32;
  uint16_t commit_marker;
  uint16_t reserved;
} AppConfigFlashRecord;

typedef struct
{
  bool has_valid_record;
  AppConfigFlashRecord latest_record;
  uint16_t first_free_slot;
} AppConfigScanResult;

extern uint8_t __app_config_flash_start__;
extern uint8_t __app_config_flash_end__;

#define APP_CONFIG_FLASH_ADDRESS \
  ((uint32_t)(uintptr_t)&__app_config_flash_start__)
#define APP_CONFIG_FLASH_END_ADDRESS \
  ((uint32_t)(uintptr_t)&__app_config_flash_end__)
#define APP_CONFIG_RECORD_COUNT \
  (APP_CONFIG_FLASH_PAGE_BYTES / sizeof(AppConfigFlashRecord))

_Static_assert(sizeof(AppConfigData) == 16u,
               "AppConfigData layout changed; update the Flash format version");
_Static_assert(sizeof(AppConfigFlashRecord) == 36u,
               "Unexpected AppConfigFlashRecord layout");
_Static_assert(offsetof(AppConfigFlashRecord, crc32) == 28u,
               "Unexpected CRC coverage");
_Static_assert(offsetof(AppConfigFlashRecord, commit_marker) == 32u,
               "Commit marker must be programmed last");

static uint8_t const app_config_keycodes_1p[APP_CONFIG_KEY_COUNT] =
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
  HID_KEY_8,
  HID_KEY_9,
  HID_KEY_ENTER
};

static uint8_t const app_config_keycodes_2p[APP_CONFIG_KEY_COUNT] =
{
  HID_KEY_8,
  HID_KEY_9,
  HID_KEY_6,
  HID_KEY_3,
  HID_KEY_2,
  HID_KEY_1,
  HID_KEY_4,
  HID_KEY_7,
  HID_KEY_3,
  HID_KEY_KEYPAD_MULTIPLY,
  HID_KEY_8,
  HID_KEY_9,
  HID_KEY_ENTER
};

static AppConfigData app_config_cache;
static AppConfigData app_config_saved;
static uint32_t app_config_next_sequence;
static uint16_t app_config_next_slot;
static bool app_config_has_saved;
static bool app_config_initialized;

static uint32_t AppConfig_CalculateCrc32(void const *data, size_t length)
{
  uint8_t const *bytes = (uint8_t const *)data;
  uint32_t crc = 0xFFFFFFFFu;
  size_t byte_index;

  for (byte_index = 0u; byte_index < length; byte_index++)
  {
    uint8_t bit_index;

    crc ^= bytes[byte_index];
    for (bit_index = 0u; bit_index < 8u; bit_index++)
    {
      uint32_t const polynomial_mask = 0u - (crc & 1u);
      crc = (crc >> 1u) ^ (0xEDB88320u & polynomial_mask);
    }
  }

  return ~crc;
}

bool AppConfig_Validate(AppConfigData const *config)
{
  if (config == NULL)
  {
    return false;
  }

  if ((config->leds_per_logic < 1u) || (config->leds_per_logic > 4u))
  {
    return false;
  }

  if (config->rainbow_enabled > 1u)
  {
    return false;
  }

  if (config->key_mode > (uint8_t)APP_CONFIG_KEY_MODE_CUSTOM)
  {
    return false;
  }

  return true;
}

static bool AppConfig_RecordIsValid(AppConfigFlashRecord const *record)
{
  if ((record->commit_marker != APP_CONFIG_COMMIT_MARKER) ||
      (record->magic != APP_CONFIG_MAGIC) ||
      (record->format_version != APP_CONFIG_FORMAT_VERSION) ||
      (record->data_size != sizeof(AppConfigData)) ||
      (record->sequence == 0u))
  {
    return false;
  }

  if (record->crc32 !=
      AppConfig_CalculateCrc32(record, offsetof(AppConfigFlashRecord, crc32)))
  {
    return false;
  }

  return AppConfig_Validate(&record->data);
}

static bool AppConfig_SequenceIsNewer(uint32_t candidate, uint32_t current)
{
  return ((int32_t)(candidate - current) > 0);
}

static AppConfigFlashRecord const *AppConfig_GetFlashRecord(uint16_t slot)
{
  uintptr_t const address = (uintptr_t)APP_CONFIG_FLASH_ADDRESS +
                            ((uintptr_t)slot * sizeof(AppConfigFlashRecord));
  return (AppConfigFlashRecord const *)address;
}

static AppConfigScanResult AppConfig_ScanFlash(void)
{
  AppConfigScanResult result;
  uint16_t slot;

  memset(&result, 0, sizeof(result));
  result.first_free_slot = (uint16_t)APP_CONFIG_RECORD_COUNT;

  for (slot = 0u; slot < APP_CONFIG_RECORD_COUNT; slot++)
  {
    AppConfigFlashRecord const *record = AppConfig_GetFlashRecord(slot);

    if ((record->magic == UINT32_MAX) &&
        (result.first_free_slot == APP_CONFIG_RECORD_COUNT))
    {
      result.first_free_slot = slot;
    }

    if (AppConfig_RecordIsValid(record) &&
        ((!result.has_valid_record) ||
         AppConfig_SequenceIsNewer(record->sequence,
                                   result.latest_record.sequence)))
    {
      memcpy(&result.latest_record, record, sizeof(result.latest_record));
      result.has_valid_record = true;
    }
  }

  return result;
}

static void AppConfig_SetDefaults(AppConfigData *config)
{
  config->leds_per_logic = 2u;
  config->rainbow_enabled = 0u;
  config->key_mode = (uint8_t)APP_CONFIG_KEY_MODE_1P;
  memcpy(config->custom_keycodes, app_config_keycodes_1p,
         sizeof(config->custom_keycodes));
}

AppConfigLoadResult AppConfig_Reload(void)
{
  AppConfigScanResult const scan = AppConfig_ScanFlash();

  app_config_next_slot = scan.first_free_slot;
  app_config_initialized = true;

  if (scan.has_valid_record)
  {
    app_config_cache = scan.latest_record.data;
    app_config_saved = scan.latest_record.data;
    app_config_has_saved = true;
    app_config_next_sequence =
      (scan.latest_record.sequence == UINT32_MAX) ?
      1u : (scan.latest_record.sequence + 1u);
    return APP_CONFIG_LOADED_FLASH;
  }

  AppConfig_SetDefaults(&app_config_cache);
  memset(&app_config_saved, 0, sizeof(app_config_saved));
  app_config_has_saved = false;
  app_config_next_sequence = 1u;
  return APP_CONFIG_LOADED_DEFAULTS;
}

AppConfigLoadResult AppConfig_Init(void)
{
  return AppConfig_Reload();
}

AppConfigData const *AppConfig_Get(void)
{
  return &app_config_cache;
}

uint8_t const *AppConfig_GetKeycodes(void)
{
  switch ((AppConfigKeyMode)app_config_cache.key_mode)
  {
    case APP_CONFIG_KEY_MODE_2P:
      return app_config_keycodes_2p;

    case APP_CONFIG_KEY_MODE_CUSTOM:
      return app_config_cache.custom_keycodes;

    case APP_CONFIG_KEY_MODE_1P:
    default:
      return app_config_keycodes_1p;
  }
}

bool AppConfig_WriteCache(AppConfigData const *config)
{
  if (!AppConfig_Validate(config))
  {
    return false;
  }

  app_config_cache = *config;
  app_config_initialized = true;
  return true;
}

void AppConfig_LoadDefaults(void)
{
  AppConfig_SetDefaults(&app_config_cache);
  app_config_initialized = true;
}

bool AppConfig_IsDirty(void)
{
  if (!app_config_initialized)
  {
    return false;
  }

  return (!app_config_has_saved) ||
         (memcmp(&app_config_cache, &app_config_saved,
                 sizeof(app_config_cache)) != 0);
}

static bool AppConfig_FlashLayoutIsValid(void)
{
  return ((APP_CONFIG_FLASH_ADDRESS % FLASH_PAGE_SIZE) == 0u) &&
         ((APP_CONFIG_FLASH_END_ADDRESS - APP_CONFIG_FLASH_ADDRESS) ==
          APP_CONFIG_FLASH_PAGE_BYTES);
}

static bool AppConfig_EraseFlashPage(void)
{
  FLASH_EraseInitTypeDef erase_init;
  uint32_t page_error = UINT32_MAX;

  erase_init.TypeErase = FLASH_TYPEERASE_PAGES;
  erase_init.PageAddress = APP_CONFIG_FLASH_ADDRESS;
  erase_init.NbPages = 1u;

  return (HAL_FLASHEx_Erase(&erase_init, &page_error) == HAL_OK) &&
         (page_error == UINT32_MAX);
}

static bool AppConfig_ProgramHalfwords(uint32_t address,
                                      void const *data,
                                      size_t length)
{
  uint8_t const *bytes = (uint8_t const *)data;
  size_t offset;

  for (offset = 0u; offset < length; offset += 2u)
  {
    uint16_t const halfword = (uint16_t)bytes[offset] |
                              ((uint16_t)bytes[offset + 1u] << 8u);

    if ((halfword != UINT16_MAX) &&
        (HAL_FLASH_Program(FLASH_TYPEPROGRAM_HALFWORD,
                           address + (uint32_t)offset,
                           halfword) != HAL_OK))
    {
      return false;
    }
  }

  return memcmp((void const *)(uintptr_t)address, data, length) == 0;
}

bool AppConfig_SaveToFlash(void)
{
  AppConfigFlashRecord record;
  AppConfigFlashRecord const *written_record;
  uint32_t write_address;
  bool success;

  if ((!app_config_initialized) ||
      (!AppConfig_Validate(&app_config_cache)) ||
      (!AppConfig_FlashLayoutIsValid()))
  {
    return false;
  }

  if (!AppConfig_IsDirty())
  {
    return true;
  }

  memset(&record, 0xFF, sizeof(record));
  record.magic = APP_CONFIG_MAGIC;
  record.format_version = APP_CONFIG_FORMAT_VERSION;
  record.data_size = sizeof(AppConfigData);
  record.sequence = app_config_next_sequence;
  record.data = app_config_cache;
  record.crc32 =
    AppConfig_CalculateCrc32(&record, offsetof(AppConfigFlashRecord, crc32));
  record.commit_marker = APP_CONFIG_COMMIT_MARKER;

  if (HAL_FLASH_Unlock() != HAL_OK)
  {
    return false;
  }

  success = true;
  if (app_config_next_slot >= APP_CONFIG_RECORD_COUNT)
  {
    success = AppConfig_EraseFlashPage();
    if (success)
    {
      app_config_next_slot = 0u;
    }
  }

  write_address = APP_CONFIG_FLASH_ADDRESS +
                  ((uint32_t)app_config_next_slot *
                   sizeof(AppConfigFlashRecord));

  if (success)
  {
    success =
      AppConfig_ProgramHalfwords(write_address, &record,
                                offsetof(AppConfigFlashRecord,
                                         commit_marker));
  }

  if (success)
  {
    success =
      AppConfig_ProgramHalfwords(
        write_address + offsetof(AppConfigFlashRecord, commit_marker),
        &record.commit_marker, sizeof(record.commit_marker));
  }

  (void)HAL_FLASH_Lock();

  written_record =
    (AppConfigFlashRecord const *)(uintptr_t)write_address;
  if ((!success) || (!AppConfig_RecordIsValid(written_record)))
  {
    AppConfigScanResult const scan = AppConfig_ScanFlash();
    app_config_next_slot = scan.first_free_slot;
    return false;
  }

  app_config_saved = app_config_cache;
  app_config_has_saved = true;
  app_config_next_slot++;
  app_config_next_sequence =
    (app_config_next_sequence == UINT32_MAX) ?
    1u : (app_config_next_sequence + 1u);
  return true;
}
