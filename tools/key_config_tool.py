#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import time
import configparser
import serial
from serial.tools import list_ports

BTN_NUM = 13

MAGIC = bytes([0x91, 0x3E, 0xED, 0x20, 0x7C, 0x99, 0x58, 0xAC])

KEYCFG_SET_KEY = 0xA1
KEYCFG_SAVE_FLASH = 0xA2
KEYCFG_LOAD_DEFAULT = 0xA3
KEYCFG_GET_KEY = 0xA4
KEYCFG_SET_LEDS_PER_LOGIC = 0xA5
KEYCFG_GET_LEDS_PER_LOGIC = 0xA6
KEYCFG_SET_RAINBOW_ENABLED = 0xA7
KEYCFG_GET_RAINBOW_ENABLED = 0xA8
KEYCFG_SET_KEY_MODE = 0xA9
KEYCFG_GET_KEY_MODE = 0xAA
KEYCFG_SET_IO4_MODE = 0xAB
KEYCFG_GET_IO4_MODE = 0xAC

ACK_HEAD = 0xAC

KEY_MODE_1P = 0
KEY_MODE_2P = 1
KEY_MODE_CUSTOM = 2
KEY_MODE_OFF = 3

KEY_MODE_NAME = {
    KEY_MODE_1P: "1p",
    KEY_MODE_2P: "2p",
    KEY_MODE_CUSTOM: "custom",
    KEY_MODE_OFF: "off",
}

KEY_MODE_DISPLAY = {
    KEY_MODE_1P: "1P",
    KEY_MODE_2P: "2P",
    KEY_MODE_CUSTOM: "自定义",
    KEY_MODE_OFF: "关闭",
}

KEY_MODE_VALUE = {
    "1p": KEY_MODE_1P,
    "2p": KEY_MODE_2P,
    "custom": KEY_MODE_CUSTOM,
    "off": KEY_MODE_OFF,
}

IO4_MODE_OFF = 0
IO4_MODE_1P = 1
IO4_MODE_2P = 2

IO4_MODE_NAME = {
    IO4_MODE_OFF: "off",
    IO4_MODE_1P: "1p",
    IO4_MODE_2P: "2p",
}

IO4_MODE_DISPLAY = {
    IO4_MODE_OFF: "关闭",
    IO4_MODE_1P: "1P",
    IO4_MODE_2P: "2P",
}

IO4_MODE_VALUE = {
    "off": IO4_MODE_OFF,
    "1p": IO4_MODE_1P,
    "2p": IO4_MODE_2P,
}

STATUS_TEXT = {
    0x00: "成功",
    0x01: "校验和错误",
    0x02: "索引或参数错误",
    0x03: "命令错误",
}

HID_KEY_NAME = {
    0x00: "NONE",
    0x01: "ERROR_ROLLOVER",
    0x02: "POST_FAIL",
    0x03: "ERROR_UNDEFINED",

    0x04: "A", 0x05: "B", 0x06: "C", 0x07: "D",
    0x08: "E", 0x09: "F", 0x0A: "G", 0x0B: "H",
    0x0C: "I", 0x0D: "J", 0x0E: "K", 0x0F: "L",
    0x10: "M", 0x11: "N", 0x12: "O", 0x13: "P",
    0x14: "Q", 0x15: "R", 0x16: "S", 0x17: "T",
    0x18: "U", 0x19: "V", 0x1A: "W", 0x1B: "X",
    0x1C: "Y", 0x1D: "Z",

    0x1E: "1", 0x1F: "2", 0x20: "3", 0x21: "4",
    0x22: "5", 0x23: "6", 0x24: "7", 0x25: "8",
    0x26: "9", 0x27: "0",

    0x28: "ENTER",
    0x29: "ESC",
    0x2A: "BACKSPACE",
    0x2B: "TAB",
    0x2C: "SPACE",

    0x2D: "-", 0x2E: "=",
    0x2F: "[", 0x30: "]",
    0x31: "\\",
    0x32: "NON_US_#",
    0x33: ";", 0x34: "'",
    0x35: "`",
    0x36: ",", 0x37: ".",
    0x38: "/",

    0x39: "CAPSLOCK",

    0x3A: "F1", 0x3B: "F2", 0x3C: "F3", 0x3D: "F4",
    0x3E: "F5", 0x3F: "F6", 0x40: "F7", 0x41: "F8",
    0x42: "F9", 0x43: "F10", 0x44: "F11", 0x45: "F12",

    0x46: "PRINTSCREEN",
    0x47: "SCROLLLOCK",
    0x48: "PAUSE",

    0x49: "INSERT",
    0x4A: "HOME",
    0x4B: "PAGEUP",
    0x4C: "DELETE",
    0x4D: "END",
    0x4E: "PAGEDOWN",

    0x4F: "RIGHT",
    0x50: "LEFT",
    0x51: "DOWN",
    0x52: "UP",

    0x53: "NUMLOCK",
    0x54: "KP_/",
    0x55: "KP_*",
    0x56: "KP_-",
    0x57: "KP_+",
    0x58: "KP_ENTER",
    0x59: "KP_1",
    0x5A: "KP_2",
    0x5B: "KP_3",
    0x5C: "KP_4",
    0x5D: "KP_5",
    0x5E: "KP_6",
    0x5F: "KP_7",
    0x60: "KP_8",
    0x61: "KP_9",
    0x62: "KP_0",
    0x63: "KP_.",

    0x64: "NON_US_BACKSLASH",
    0x65: "APPLICATION",
    0x66: "POWER",
    0x67: "KP_EQUALS",

    0x68: "F13", 0x69: "F14", 0x6A: "F15",
    0x6B: "F16", 0x6C: "F17", 0x6D: "F18",
    0x6E: "F19", 0x6F: "F20", 0x70: "F21",
    0x71: "F22", 0x72: "F23", 0x73: "F24",

    0x74: "EXECUTE",
    0x75: "HELP",
    0x76: "MENU",
    0x77: "SELECT",
    0x78: "STOP",
    0x79: "AGAIN",
    0x7A: "UNDO",
    0x7B: "CUT",
    0x7C: "COPY",
    0x7D: "PASTE",
    0x7E: "FIND",
    0x7F: "MUTE",
    0x80: "VOLUME_UP",
    0x81: "VOLUME_DOWN",

    0x82: "LOCKING_CAPS",
    0x83: "LOCKING_NUM",
    0x84: "LOCKING_SCROLL",

    0x85: "KP_COMMA",
    0x86: "KP_EQUAL_SIGN",

    0x87: "INTERNATIONAL1",
    0x88: "INTERNATIONAL2",
    0x89: "INTERNATIONAL3",
    0x8A: "INTERNATIONAL4",
    0x8B: "INTERNATIONAL5",
    0x8C: "INTERNATIONAL6",
    0x8D: "INTERNATIONAL7",
    0x8E: "INTERNATIONAL8",
    0x8F: "INTERNATIONAL9",

    0x90: "LANG1",
    0x91: "LANG2",
    0x92: "LANG3",
    0x93: "LANG4",
    0x94: "LANG5",

    0xE0: "LCTRL",
    0xE1: "LSHIFT",
    0xE2: "LALT",
    0xE3: "LGUI",
    0xE4: "RCTRL",
    0xE5: "RSHIFT",
    0xE6: "RALT",
    0xE7: "RGUI",
}

KEY_NAME_TO_HID = {name: code for code, name in HID_KEY_NAME.items()}


def list_serial_ports():
    ports = list(list_ports.comports())
    if not ports:
        print("未发现串口。")
        return []
    print("可用串口:")
    for i, p in enumerate(ports):
        print(f"  [{i}] {p.device}  {p.description}")
    return ports


def choose_port():
    ports = list_serial_ports()
    if not ports:
        sys.exit(1)
    while True:
        s = input("请选择串口序号或直接输入 COM 口名: ").strip()
        if not s:
            continue
        if s.isdigit():
            idx = int(s)
            if 0 <= idx < len(ports):
                return ports[idx].device
            print("序号超出范围。")
        else:
            return s


def calc_sum(cmd, idx, key):
    return (cmd + idx + key) & 0xFF


def build_packet(cmd, idx=0, key=0):
    return MAGIC + bytes([cmd, idx, key, calc_sum(cmd, idx, key)])


def read_ack(ser, timeout=0.5):
    deadline = time.monotonic() + timeout
    buf = bytearray()
    while time.monotonic() < deadline:
        n = ser.in_waiting
        if n:
            buf.extend(ser.read(n))
            while len(buf) >= 4:
                if buf[0] == ACK_HEAD:
                    return bytes(buf[:4])
                buf.pop(0)
        time.sleep(0.005)
    return None


def send_cmd(ser, cmd, idx=0, key=0):
    ser.reset_input_buffer()
    pkt = build_packet(cmd, idx, key)
    written = ser.write(pkt)
    if written != len(pkt):
        raise serial.SerialTimeoutException(
            f"串口短写：应发送 {len(pkt)} 字节，实际发送 {written} 字节"
        )
    ser.flush()
    ack = read_ack(ser)
    if ack is None:
        print("未收到 ACK")
        return None
    _, status, ack_idx, ack_key = ack
    return {
        "raw": ack,
        "status": status,
        "status_text": STATUS_TEXT.get(status, f"未知状态 0x{status:02X}"),
        "idx": ack_idx,
        "key": ack_key,
    }


def hid_name(key):
    return HID_KEY_NAME.get(key, f"0x{key:02X}")


def parse_key(s):
    s = s.strip().upper()
    if s.startswith("0X"):
        v = int(s, 16)
    elif s in KEY_NAME_TO_HID:
        v = KEY_NAME_TO_HID[s]
    elif s.isdigit():
        v = int(s, 10)
    else:
        raise ValueError(f"未知键名: {s}")
    if not (0 <= v <= 0xFF):
        raise ValueError("键值必须在 0x00~0xFF 范围内")
    return v


def get_key(ser, idx):
    ack = send_cmd(ser, KEYCFG_GET_KEY, idx, 0)
    if ack is None:
        return None
    if ack["status"] != 0:
        print(f"读取失败：编号={idx}，状态={ack['status_text']}")
        return None
    return ack["key"]


def probe_light_config(ser):
    ack = send_cmd(ser, KEYCFG_GET_LEDS_PER_LOGIC, 0, 0)
    return ack is not None and ack["status"] == 0


def probe_key_mode(ser):
    ack = send_cmd(ser, KEYCFG_GET_KEY_MODE, 0, 0)
    return ack is not None and ack["status"] == 0


def probe_io4_mode(ser):
    ack = send_cmd(ser, KEYCFG_GET_IO4_MODE, 0, 0)
    return ack is not None and ack["status"] == 0


def get_leds_per_logic(ser):
    ack = send_cmd(ser, KEYCFG_GET_LEDS_PER_LOGIC, 0, 0)
    if ack is None:
        return None
    if ack["status"] != 0:
        print(f"读取每逻辑灯珠数失败：{ack['status_text']}")
        return None
    return ack["key"]


def get_rainbow_enabled(ser):
    ack = send_cmd(ser, KEYCFG_GET_RAINBOW_ENABLED, 0, 0)
    if ack is None:
        return None
    if ack["status"] != 0:
        print(f"读取待机彩虹设置失败：{ack['status_text']}")
        return None
    return bool(ack["key"])


def read_light_config(ser):
    leds = get_leds_per_logic(ser)
    rainbow = get_rainbow_enabled(ser)

    if leds is None or rainbow is None:
        return

    print("\n当前灯光配置：")
    print(f"  每逻辑灯珠数 = {leds}")
    print(f"  待机彩虹 = {'开启' if rainbow else '关闭'}")
    print()


def set_leds_per_logic(ser, value):
    if not (1 <= value <= 4):
        print("每逻辑灯珠数必须在 1～4 范围内。")
        return

    ack = send_cmd(ser, KEYCFG_SET_LEDS_PER_LOGIC, 0, value)
    if ack is None:
        return

    print(f"ACK: {ack['raw'].hex(' ').upper()}  {ack['status_text']}")
    if ack["status"] == 0:
        print(f"已更新 RAM：每逻辑灯珠数 = {ack['key']}。运行 save 后写入 Flash。")
    else:
        print("设置每逻辑灯珠数失败。")


def parse_bool_value(value):
    value = value.strip().lower()
    if value in ("1", "on", "true", "yes", "enable", "enabled"):
        return 1
    if value in ("0", "off", "false", "no", "disable", "disabled"):
        return 0
    raise ValueError("参数必须是 on、off、1 或 0")


def set_rainbow_enabled(ser, enabled):
    ack = send_cmd(ser, KEYCFG_SET_RAINBOW_ENABLED, 0, enabled)
    if ack is None:
        return

    print(f"ACK: {ack['raw'].hex(' ').upper()}  {ack['status_text']}")
    if ack["status"] == 0:
        state = "开启" if ack["key"] else "关闭"
        print(f"已更新 RAM：待机彩虹 = {state}。运行 save 后写入 Flash。")
    else:
        print("设置待机彩虹失败。")


def parse_key_mode(value):
    value = value.strip().lower()
    if value not in KEY_MODE_VALUE:
        raise ValueError("mode 必须是 1p、2p、custom 或 off")
    return KEY_MODE_VALUE[value]


def get_key_mode(ser):
    ack = send_cmd(ser, KEYCFG_GET_KEY_MODE, 0, 0)
    if ack is None:
        return None
    if ack["status"] != 0:
        print(f"读取键盘模式失败：{ack['status_text']}")
        return None
    if ack["key"] not in KEY_MODE_NAME:
        print(f"读取键盘模式失败：未知模式 {ack['key']}")
        return None
    return ack["key"]


def read_key_mode(ser):
    mode = get_key_mode(ser)
    if mode is not None:
        print(f"\n当前键盘模式：{KEY_MODE_DISPLAY[mode]}\n")


def set_key_mode(ser, mode):
    ack = send_cmd(ser, KEYCFG_SET_KEY_MODE, 0, mode)
    if ack is None:
        return False

    print(f"ACK: {ack['raw'].hex(' ').upper()}  {ack['status_text']}")
    if ack["status"] != 0:
        print("设置键盘模式失败。")
        return False

    mode_name = KEY_MODE_DISPLAY.get(ack["key"], f"未知({ack['key']})")
    print(f"已更新 RAM：键盘模式 = {mode_name}。运行 save 后写入 Flash。")
    return True


def parse_io4_mode(value):
    value = value.strip().lower()
    if value not in IO4_MODE_VALUE:
        raise ValueError("iomode 必须是 off、1p 或 2p")
    return IO4_MODE_VALUE[value]


def get_io4_mode(ser):
    ack = send_cmd(ser, KEYCFG_GET_IO4_MODE, 0, 0)
    if ack is None:
        return None
    if ack["status"] != 0:
        print(f"读取 IO4 模式失败：{ack['status_text']}")
        return None
    if ack["key"] not in IO4_MODE_NAME:
        print(f"读取 IO4 模式失败：未知模式 {ack['key']}")
        return None
    return ack["key"]


def read_io4_mode(ser):
    mode = get_io4_mode(ser)
    if mode is not None:
        print(f"\n当前 IO4 模式：{IO4_MODE_DISPLAY[mode]}\n")


def set_io4_mode(ser, mode):
    ack = send_cmd(ser, KEYCFG_SET_IO4_MODE, 0, mode)
    if ack is None:
        return False

    print(f"ACK: {ack['raw'].hex(' ').upper()}  {ack['status_text']}")
    if ack["status"] != 0:
        print("设置 IO4 模式失败。")
        return False

    mode_name = IO4_MODE_DISPLAY.get(ack["key"], f"未知({ack['key']})")
    print(f"已更新 RAM：IO4 模式 = {mode_name}。运行 save 后写入 Flash。")
    return True


def read_all_keys(ser):
    print("\n当前键位：")
    print("编号  分组  HID值  键名")
    print("------------------------")
    for idx in range(BTN_NUM):
        key = get_key(ser, idx)
        group = "主键" if idx < 8 else "副键"
        if key is None:
            print(f"{idx:02d}    {group}  --     读取失败")
        else:
            print(f"{idx:02d}    {group}  0x{key:02X}   {hid_name(key)}")
    print()


def set_key(ser, idx, key):
    ack = send_cmd(ser, KEYCFG_SET_KEY, idx, key)
    if ack is None:
        return
    print(f"ACK: {ack['raw'].hex(' ').upper()}  {ack['status_text']}")
    if ack["status"] == 0:
        print(f"已修改：按键[{idx}] = 0x{key:02X} ({hid_name(key)})")
    else:
        print("修改失败。")


def save_flash(ser):
    ack = send_cmd(ser, KEYCFG_SAVE_FLASH, 0, 0)
    if ack is None:
        return
    print(f"ACK: {ack['raw'].hex(' ').upper()}  {ack['status_text']}")
    if ack["status"] == 0:
        print("已保存到 Flash。")
    else:
        print("保存失败。")


def load_default(ser):
    ack = send_cmd(ser, KEYCFG_LOAD_DEFAULT, 0, 0)
    if ack is None:
        return
    print(f"ACK: {ack['raw'].hex(' ').upper()}  {ack['status_text']}")
    if ack["status"] == 0:
        print("已恢复默认配置并保存到 Flash。")
    else:
        print("恢复默认失败。")

def export_ini(ser, filename, supports_light_config=False,
               supports_key_mode=False, supports_io4_mode=False):
    cfg = configparser.ConfigParser()
    cfg["keymap"] = {}

    for idx in range(BTN_NUM):
        key = get_key(ser, idx)

        if key is None:
            print(f"读取按键[{idx}]失败，已取消导出。")
            return

        cfg["keymap"][str(idx)] = hid_name(key)

    if supports_light_config:
        leds = get_leds_per_logic(ser)
        rainbow = get_rainbow_enabled(ser)

        if leds is None or rainbow is None:
            print("读取灯光配置失败，已取消导出。")
            return

        cfg["led"] = {
            "leds_per_logic": str(leds),
            "rainbow_enabled": "1" if rainbow else "0",
        }

    if supports_key_mode:
        mode = get_key_mode(ser)
        if mode is None:
            print("读取键盘模式失败，已取消导出。")
            return

        cfg["keyboard"] = {
            "mode": KEY_MODE_NAME[mode],
        }

    if supports_io4_mode:
        mode = get_io4_mode(ser)
        if mode is None:
            print("读取 IO4 模式失败，已取消导出。")
            return

        cfg["io4"] = {
            "mode": IO4_MODE_NAME[mode],
        }

    with open(filename, "w", encoding="utf-8") as f:
        cfg.write(f)

    print(f"设备配置已导出到：{filename}")


def import_ini(ser, filename, supports_light_config=False,
               supports_key_mode=False, supports_io4_mode=False):
    cfg = configparser.ConfigParser()
    cfg.read(filename, encoding="utf-8")

    if "keymap" not in cfg:
        print("INI 文件缺少 [keymap] 配置段。")
        return

    for idx in range(BTN_NUM):
        key_text = cfg["keymap"].get(str(idx))

        if key_text is None:
            print(f"INI 缺少 keymap.{idx}，已跳过。")
            continue

        try:
            key = parse_key(key_text)
        except ValueError as e:
            print(f"按键[{idx}]无效：{key_text}，{e}")
            continue

        ack = send_cmd(ser, KEYCFG_SET_KEY, idx, key)

        if ack is None or ack["status"] != 0:
            print(f"写入按键[{idx}]失败。")
            return

        print(f"按键[{idx}] = 0x{key:02X} ({hid_name(key)})")

    if "led" in cfg:
        if not supports_light_config:
            print("检测到旧固件，已跳过 [led] 配置。")
        else:
            led_section = cfg["led"]

            if "leds_per_logic" in led_section:
                set_leds_per_logic(ser, int(led_section["leds_per_logic"], 0))

            if "rainbow_enabled" in led_section:
                set_rainbow_enabled(ser, parse_bool_value(led_section["rainbow_enabled"]))

    if "keyboard" in cfg and "mode" in cfg["keyboard"]:
        if not supports_key_mode:
            print("检测到旧固件，已跳过 [keyboard] 模式。")
        else:
            mode = parse_key_mode(cfg["keyboard"]["mode"])
            if not set_key_mode(ser, mode):
                print("写入键盘模式失败。")
                return

    if "io4" in cfg and "mode" in cfg["io4"]:
        if not supports_io4_mode:
            print("检测到旧固件，已跳过 [io4] 模式。")
        else:
            mode = parse_io4_mode(cfg["io4"]["mode"])
            if not set_io4_mode(ser, mode):
                print("写入 IO4 模式失败。")
                return

    print("INI 配置已写入设备 RAM，尚未写入 Flash。")
    print("如需断电保存，请运行 save。")


def print_help(supports_light_config=False, supports_key_mode=False,
               supports_io4_mode=False):
    print("""
可用命令：
  list                    读取并显示全部按键映射
  get <编号>              读取一个按键，例如：get 0
  set <编号> <键值>       修改 RAM 中的按键，例如：set 0 B / set 0 0x05
  save                    将当前 RAM 配置保存到 Flash
  default                 恢复默认配置并保存到 Flash
  keys                    显示已知的 HID 键名
  export <文件.ini>       将设备配置导出到 INI 文件
  import <文件.ini>       将 INI 配置写入设备 RAM，不自动保存到 Flash
  help                    显示本帮助
  exit                    退出工具
""")

    if supports_light_config:
        print("""灯光配置：
  light                   读取当前灯光配置
  leds <1-4>              设置每个逻辑灯对应的物理灯珠数，仅写入 RAM
  rainbow <on|off>        开启或关闭待机彩虹，仅写入 RAM
""")

    if supports_key_mode:
        print("""键盘模式：
  mode                    读取当前键盘模式
  mode <1p|2p|custom|off> 设置键盘模式，仅写入 RAM

  1P/2P 模式固定主按键 0～7，副按键 8～12 可单独修改。
  在 1P/2P 模式修改主按键时，固件会自动切换到自定义模式。
""")

    if supports_io4_mode:
        print("""IO4 模式：
  iomode                  读取当前 IO4 模式
  iomode <off|1p|2p>      设置 IO4 模式，仅写入 RAM

  IO4 与键盘模式相互独立；IO4 开启时按键 12 作为投币（Coin）。
""")


def print_keys():
    for code, name in sorted(HID_KEY_NAME.items()):
        print(f"0x{code:02X}  {name}")


def repl(ser, supports_light_config, supports_key_mode, supports_io4_mode):
    read_all_keys(ser)

    if supports_light_config:
        read_light_config(ser)
    else:
        print("\n检测到旧固件；已隐藏/禁用灯光配置命令。\n")

    if supports_key_mode:
        read_key_mode(ser)
    else:
        print("检测到旧固件；已隐藏/禁用键盘模式命令。\n")

    if supports_io4_mode:
        read_io4_mode(ser)
    else:
        print("检测到旧固件；已隐藏/禁用 IO4 模式命令。\n")

    print_help(supports_light_config, supports_key_mode, supports_io4_mode)
    while True:
        try:
            line = input("keycfg> ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            break
        if not line:
            continue
        parts = line.split()
        cmd = parts[0].lower()
        try:
            if cmd in ("exit", "quit", "q"):
                break
            elif cmd in ("help", "?"):
                print_help(supports_light_config, supports_key_mode,
                           supports_io4_mode)
            elif cmd == "keys":
                print_keys()
            elif cmd == "list":
                read_all_keys(ser)
            elif cmd == "get":
                if len(parts) != 2:
                    print("用法：get <编号>")
                    continue
                idx = int(parts[1], 0)
                if not (0 <= idx < BTN_NUM):
                    print(f"按键编号必须在 0～{BTN_NUM - 1} 范围内。")
                    continue
                key = get_key(ser, idx)
                if key is not None:
                    print(f"按键[{idx}] = 0x{key:02X} ({hid_name(key)})")
            elif cmd == "set":
                if len(parts) != 3:
                    print("用法：set <编号> <键值>")
                    continue
                idx = int(parts[1], 0)
                if not (0 <= idx < BTN_NUM):
                    print(f"按键编号必须在 0～{BTN_NUM - 1} 范围内。")
                    continue
                key = parse_key(parts[2])
                set_key(ser, idx, key)
            elif cmd == "save":
                save_flash(ser)
            elif cmd == "default":
                load_default(ser)
            elif cmd == "export":
                if len(parts) != 2:
                    print("用法：export <文件.ini>")
                    continue
                export_ini(ser, parts[1], supports_light_config,
                           supports_key_mode, supports_io4_mode)
            elif cmd == "import":
                if len(parts) != 2:
                    print("用法：import <文件.ini>")
                    continue
                import_ini(ser, parts[1], supports_light_config,
                           supports_key_mode, supports_io4_mode)
            elif cmd == "light":
                if not supports_light_config:
                    print("检测到旧固件；不支持灯光配置。")
                    continue
                read_light_config(ser)
            elif cmd == "leds":
                if not supports_light_config:
                    print("检测到旧固件；不支持灯光配置。")
                    continue
                if len(parts) != 2:
                    print("用法：leds <1-4>")
                    continue
                set_leds_per_logic(ser, int(parts[1], 0))
            elif cmd == "rainbow":
                if not supports_light_config:
                    print("检测到旧固件；不支持灯光配置。")
                    continue
                if len(parts) != 2:
                    print("用法：rainbow <on|off|1|0>")
                    continue
                set_rainbow_enabled(ser, parse_bool_value(parts[1]))
            elif cmd == "mode":
                if not supports_key_mode:
                    print("检测到旧固件；不支持键盘模式配置。")
                    continue
                if len(parts) == 1:
                    read_key_mode(ser)
                    continue
                if len(parts) != 2:
                    print("用法：mode <1p|2p|custom|off>")
                    continue
                set_key_mode(ser, parse_key_mode(parts[1]))
            elif cmd == "iomode":
                if not supports_io4_mode:
                    print("检测到旧固件；不支持 IO4 模式配置。")
                    continue
                if len(parts) == 1:
                    read_io4_mode(ser)
                    continue
                if len(parts) != 2:
                    print("用法：iomode <off|1p|2p>")
                    continue
                set_io4_mode(ser, parse_io4_mode(parts[1]))
            else:
                print("未知命令，请输入 help 查看帮助。")
        except ValueError as e:
            print(f"输入错误：{e}")


def main():
    if len(sys.argv) > 1 and sys.argv[1].lower() in ("-h", "--help"):
        print_help(True, True, True)
        return

    port = choose_port()
    try:
        ser = serial.Serial(port=port, baudrate=115200, timeout=0.05, write_timeout=0.5)
    except serial.SerialException as e:
        print(f"打开串口失败：{e}")
        sys.exit(1)
    print(f"已打开 {port}")
    supports_light_config = probe_light_config(ser)
    supports_key_mode = probe_key_mode(ser)
    supports_io4_mode = probe_io4_mode(ser)
    try:
        repl(ser, supports_light_config, supports_key_mode,
             supports_io4_mode)
    finally:
        ser.close()
        print("串口已关闭。")


if __name__ == "__main__":
    main()
