#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import codecs
import configparser
import os
import re
import shutil
import sys
import tempfile
import time
import tkinter as tk
import tomllib
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import serial
from serial.tools import list_ports

import key_config_tool as protocol


SAVE_COOLDOWN_SECONDS = 5.0
DEVICE_POLL_INTERVAL_MS = 1000
READBACK_ATTEMPTS = 3
READBACK_RETRY_DELAY_SECONDS = 0.05

DEFAULT_DEVICE_VALUES = {
    "keyboard": protocol.KEY_MODE_1P,
    "io4": protocol.IO4_MODE_OFF,
    "light": 1,
    "led_count": 2,
}
DEFAULT_DEVICE_KEYCODES = (
    0x1A, 0x08, 0x07, 0x06, 0x1B, 0x1D, 0x04, 0x14,
    0x20, 0x55, 0x25, 0x26, 0x28,
)


class AckTimeoutError(RuntimeError):
    pass


class DeviceCommandError(RuntimeError):
    pass


def device_config_is_default(values, keycodes):
    return (
        all(values.get(name) == value
            for name, value in DEFAULT_DEVICE_VALUES.items())
        and tuple(keycodes) == DEFAULT_DEVICE_KEYCODES
    )


def write_once_and_read_back(
    send_command,
    set_command,
    get_command,
    new_value,
    valid_values,
    attempts=READBACK_ATTEMPTS,
    retry_delay=READBACK_RETRY_DELAY_SECONDS,
):
    """Send SET once, then let GET decide the device's actual state."""
    if attempts < 1:
        raise ValueError("回读次数必须至少为 1")

    set_error = None
    try:
        set_ack = send_command(set_command, new_value)
        if set_ack["key"] != new_value:
            set_error = RuntimeError(
                f"SET ACK 返回值不一致：期望 {new_value}，返回 {set_ack['key']}"
            )
    except RuntimeError as error:
        set_error = error

    last_read_error = None
    for attempt in range(attempts):
        if attempt and retry_delay > 0:
            time.sleep(retry_delay * attempt)
        try:
            read_ack = send_command(get_command)
            observed = read_ack["key"]
            if observed not in valid_values:
                raise RuntimeError(f"GET 返回无效值：{observed}")
            return observed, set_error
        except RuntimeError as error:
            last_read_error = error

    detail = f"连续 {attempts} 次回读设备状态失败：{last_read_error}"
    if set_error is not None:
        detail = f"SET 未确认（{set_error}）；{detail}"
    raise RuntimeError(detail) from last_read_error

BUTTON_DISPLAY_NAMES = (
    "BTN1", "BTN2", "BTN3", "BTN4", "BTN5", "BTN6", "BTN7", "BTN8",
    "1P SELECT", "2P SELECT", "TEST", "SERVICE", "BTN",
)

AQUAMAI_DEFAULTS = {
    "Button1_1P": "W",
    "Button2_1P": "E",
    "Button3_1P": "D",
    "Button4_1P": "C",
    "Button5_1P": "X",
    "Button6_1P": "Z",
    "Button7_1P": "A",
    "Button8_1P": "Q",
    "Select_1P": "Alpha3",
    "Button1_2P": "Keypad8",
    "Button2_2P": "Keypad9",
    "Button3_2P": "Keypad6",
    "Button4_2P": "Keypad3",
    "Button5_2P": "Keypad2",
    "Button6_2P": "Keypad1",
    "Button7_2P": "Keypad4",
    "Button8_2P": "Keypad7",
    "Select_2P": "KeypadMultiply",
    "Test": "ScrollLock",
    "Service": "Pause",
}

AQUAMAI_HID_NAMES = {
    0x28: "Return",
    0x29: "Escape",
    0x2A: "Backspace",
    0x2B: "Tab",
    0x2C: "Space",
    0x2D: "Minus",
    0x2E: "Equals",
    0x2F: "LeftBracket",
    0x30: "RightBracket",
    0x31: "Backslash",
    0x33: "Semicolon",
    0x34: "Quote",
    0x35: "BackQuote",
    0x36: "Comma",
    0x37: "Period",
    0x38: "Slash",
    0x39: "CapsLock",
    0x46: "Print",
    0x47: "ScrollLock",
    0x48: "Pause",
    0x49: "Insert",
    0x4A: "Home",
    0x4B: "PageUp",
    0x4C: "Delete",
    0x4D: "End",
    0x4E: "PageDown",
    0x4F: "RightArrow",
    0x50: "LeftArrow",
    0x51: "DownArrow",
    0x52: "UpArrow",
    0x53: "Numlock",
    0x54: "KeypadDivide",
    0x55: "KeypadMultiply",
    0x56: "KeypadMinus",
    0x57: "KeypadPlus",
    0x58: "KeypadEnter",
    0x63: "KeypadPeriod",
    0xE0: "LeftControl",
    0xE1: "LeftShift",
    0xE2: "LeftAlt",
    0xE3: "LeftWindows",
    0xE4: "RightControl",
    0xE5: "RightShift",
    0xE6: "RightAlt",
    0xE7: "RightWindows",
}
AQUAMAI_HID_NAMES.update(
    {0x04 + index: chr(ord("A") + index) for index in range(26)}
)
AQUAMAI_HID_NAMES.update(
    {0x1E + index: f"Alpha{index + 1}" for index in range(9)}
)
AQUAMAI_HID_NAMES[0x27] = "Alpha0"
AQUAMAI_HID_NAMES.update(
    {0x3A + index: f"F{index + 1}" for index in range(12)}
)
AQUAMAI_HID_NAMES.update(
    {0x59 + index: f"Keypad{index + 1}" for index in range(9)}
)
AQUAMAI_HID_NAMES[0x62] = "Keypad0"


def aquamai_name(hid_key):
    try:
        return AQUAMAI_HID_NAMES[hid_key]
    except KeyError as error:
        raise ValueError(
            f"HID 键值 0x{hid_key:02X} 没有对应的 AquaMai 按键名称"
        ) from error


def build_aquamai_mappings(keycodes, target_player):
    if len(keycodes) != protocol.BTN_NUM:
        raise ValueError(f"需要 {protocol.BTN_NUM} 个按键值")
    if target_player not in ("1P", "2P"):
        raise ValueError("Custom 写入目标必须是 1P 或 2P")

    mappings = {
        f"Button{index + 1}_{target_player}": aquamai_name(keycodes[index])
        for index in range(8)
    }
    mappings.update({
        "Select_1P": aquamai_name(keycodes[8]),
        "Select_2P": aquamai_name(keycodes[9]),
        "Test": aquamai_name(keycodes[10]),
        "Service": aquamai_name(keycodes[11]),
    })
    return mappings


_AQUAMAI_SECTION_RE = re.compile(
    r"^[ \t]*\[[ \t]*GameSystem[ \t]*\.[ \t]*KeyMap[ \t]*\]"
    r"[ \t]*(?:#.*)?(?:\r?\n)?$"
)
_TOML_TABLE_RE = re.compile(
    r"^[ \t]*\[\[?.*?\]\]?[ \t]*(?:#.*)?(?:\r?\n)?$"
)


def _line_ending(line):
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    return ""


def _toml_keymap(text, description):
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"{description}不是合法 TOML：{error}") from error

    game_system = document.get("GameSystem")
    keymap = game_system.get("KeyMap") if isinstance(game_system, dict) else None
    if not isinstance(keymap, dict):
        raise ValueError("所选文件中缺少 [GameSystem.KeyMap] 配置段")
    return keymap


def _toml_scan_line(line, quote_state, container_depth):
    """Track multiline strings and array/inline-table continuations."""
    index = 0
    length = len(line)
    while index < length:
        if quote_state is not None:
            closing = line.find(quote_state, index)
            if closing < 0:
                return quote_state, container_depth
            if quote_state == '"""':
                backslashes = 0
                cursor = closing - 1
                while cursor >= 0 and line[cursor] == "\\":
                    backslashes += 1
                    cursor -= 1
                if backslashes % 2:
                    index = closing + 3
                    continue
            quote_state = None
            index = closing + 3
            continue

        char = line[index]
        if char == "#":
            break
        if line.startswith('"""', index):
            quote_state = '"""'
            index += 3
            continue
        if line.startswith("'''", index):
            quote_state = "'''"
            index += 3
            continue
        if char in ('"', "'"):
            delimiter = char
            index += 1
            while index < length:
                if delimiter == '"' and line[index] == "\\":
                    index += 2
                    continue
                if line[index] == delimiter:
                    index += 1
                    break
                index += 1
            continue
        if char in "[{":
            container_depth += 1
        elif char in "]}" and container_depth:
            container_depth -= 1
        index += 1
    return quote_state, container_depth


def _toml_comment_suffix(line):
    """Return a single-line TOML assignment's whitespace/comment suffix."""
    quote = None
    index = 0
    content_end = len(line) - len(_line_ending(line))
    while index < content_end:
        char = line[index]
        if quote is not None:
            if quote == '"' and char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char in ('"', "'"):
            quote = char
        elif char == "#":
            start = index
            while start > 0 and line[start - 1] in " \t":
                start -= 1
            return line[start:content_end]
        index += 1
    return ""


def rewrite_aquamai_keymap(text, mappings):
    keymap_before = _toml_keymap(text, "原 AquaMai 配置文件")
    for field in mappings:
        if field in keymap_before and not isinstance(keymap_before[field], str):
            raise ValueError(f"[GameSystem.KeyMap] 的 {field} 不是字符串，拒绝覆盖")
    if ("DisableDebugInput" in keymap_before
            and type(keymap_before["DisableDebugInput"]) is not bool):
        raise ValueError(
            "[GameSystem.KeyMap] 的 DisableDebugInput 不是布尔值，拒绝覆盖"
        )

    lines = text.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in text else "\n"
    target_fields = set(mappings) | {"DisableDebugInput"}
    field_pattern = "|".join(
        sorted((re.escape(field) for field in target_fields), key=len, reverse=True)
    )
    assignment_re = re.compile(
        rf"^(?P<indent>[ \t]*)(?P<field>{field_pattern})[ \t]*="
    )
    commented_re = re.compile(
        rf"^(?P<indent>[ \t]*)#[ \t]*(?P<field>{field_pattern})[ \t]*="
    )

    section_start = None
    section_end = len(lines)
    section_count = 0
    in_target = False
    quote_state = None
    container_depth = 0
    active = {}
    commented = {}
    pending = None

    for index, line in enumerate(lines):
        at_statement_start = quote_state is None and container_depth == 0
        if pending is not None and at_statement_start:
            field, start, indent = pending
            active[field] = (start, index - 1, indent)
            pending = None

        is_table = at_statement_start and _TOML_TABLE_RE.match(line) is not None
        if is_table:
            if in_target and section_end == len(lines):
                section_end = index
                in_target = False
            if _AQUAMAI_SECTION_RE.match(line):
                section_count += 1
                if section_start is None:
                    section_start = index
                    in_target = True
            quote_state = None
            container_depth = 0
            continue

        active_match = assignment_re.match(line) if in_target and at_statement_start else None
        comment_match = commented_re.match(line) if in_target and at_statement_start else None
        quote_state, container_depth = _toml_scan_line(
            line, quote_state, container_depth
        )

        if active_match:
            field = active_match.group("field")
            if field in active or pending is not None:
                raise ValueError(f"[GameSystem.KeyMap] 中存在重复配置：{field}")
            if quote_state is None and container_depth == 0:
                active[field] = (index, index, active_match.group("indent"))
            else:
                pending = (field, index, active_match.group("indent"))
        elif comment_match:
            field = comment_match.group("field")
            commented.setdefault(
                field, (index, index, comment_match.group("indent"))
            )

    if pending is not None:
        field, start, indent = pending
        active[field] = (start, len(lines) - 1, indent)

    if section_count != 1 or section_start is None:
        if section_count > 1:
            raise ValueError("存在多个 [GameSystem.KeyMap] 配置段，拒绝修改")
        raise ValueError(
            "找到了 KeyMap 数据，但无法安全定位简单的 [GameSystem.KeyMap] 配置段"
        )

    for field in target_fields:
        if field in keymap_before and field not in active:
            raise ValueError(f"无法安全定位 {field} 的赋值，原文件未修改")

    entries = [
        (field, f'"{value}"', value == AQUAMAI_DEFAULTS[field])
        for field, value in mappings.items()
    ]
    entries.append(("DisableDebugInput", "false", False))

    edits = {}
    missing_lines = []
    changed_fields = []
    for field, serialized_value, use_comment in entries:
        active_location = active.get(field)
        location = active_location or commented.get(field)
        comment = "#" if use_comment else ""
        if location is None:
            missing_lines.append(f"{comment}{field} = {serialized_value}{newline}")
            changed_fields.append(field)
            continue

        start, end, indent = location
        suffix = (
            _toml_comment_suffix(lines[start])
            if active_location is not None and start == end
            else ""
        )
        eol = _line_ending(lines[end])
        replacement = (
            f"{indent}{comment}{field} = {serialized_value}{suffix}{eol}"
        )
        original = "".join(lines[start:end + 1])
        if replacement != original:
            edits[start] = (end, replacement)
            changed_fields.append(field)

    output = []
    index = 0
    inserted = False
    while index < len(lines):
        if index == section_end and missing_lines:
            if output and not output[-1].endswith(("\r", "\n")):
                output[-1] += newline
            output.extend(missing_lines)
            inserted = True
        edit = edits.get(index)
        if edit is None:
            output.append(lines[index])
            index += 1
        else:
            end, replacement = edit
            output.append(replacement)
            index = end + 1
    if missing_lines and not inserted:
        if output and not output[-1].endswith(("\r", "\n")):
            output[-1] += newline
        output.extend(missing_lines)

    rewritten = "".join(output)
    keymap_after = _toml_keymap(rewritten, "修改后的 AquaMai 配置文件")
    for field, value in mappings.items():
        if value == AQUAMAI_DEFAULTS[field]:
            if field in keymap_after:
                raise ValueError(f"修改后默认项 {field} 未正确停用，原文件未修改")
        elif keymap_after.get(field) != value:
            raise ValueError(f"修改后 {field} 未通过校验，原文件未修改")
    if keymap_after.get("DisableDebugInput") is not False:
        raise ValueError("修改后 DisableDebugInput 未通过校验，原文件未修改")

    return rewritten, changed_fields


def _create_unique_backup(path, original_bytes):
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    base_candidates = [
        path.with_name(f"{path.name}.bak"),
        path.with_name(f"{path.name}.bak.{timestamp}"),
    ]
    binary_flag = getattr(os, "O_BINARY", 0)

    for sequence in range(10000):
        if sequence < len(base_candidates):
            candidate = base_candidates[sequence]
        else:
            candidate = path.with_name(
                f"{path.name}.bak.{timestamp}.{sequence - 1}"
            )
        try:
            descriptor = os.open(
                candidate,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | binary_flag,
                0o600,
            )
        except FileExistsError:
            continue

        try:
            with os.fdopen(descriptor, "wb") as backup_file:
                backup_file.write(original_bytes)
                backup_file.flush()
                os.fsync(backup_file.fileno())
            shutil.copystat(path, candidate)
            return candidate
        except Exception:
            candidate.unlink(missing_ok=True)
            raise
    raise OSError("无法为配置文件创建唯一备份名")


def _atomic_replace_with_backup(path, original_bytes, replacement_bytes):
    if replacement_bytes == original_bytes:
        return None
    if path.is_symlink():
        raise ValueError("为避免替换符号链接目标，请先选择实际配置文件")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    backup_path = None
    replaced = False
    try:
        with os.fdopen(descriptor, "wb") as temporary_file:
            temporary_file.write(replacement_bytes)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.chmod(temporary_path, path.stat().st_mode)

        if path.read_bytes() != original_bytes:
            raise RuntimeError("配置文件在保存期间被其他程序修改，已取消覆盖")

        backup_path = _create_unique_backup(path, original_bytes)
        if path.read_bytes() != original_bytes:
            raise RuntimeError("配置文件在备份期间被其他程序修改，已取消覆盖")
        os.replace(temporary_path, path)
        replaced = True
        return backup_path
    finally:
        temporary_path.unlink(missing_ok=True)
        if backup_path is not None and not replaced:
            backup_path.unlink(missing_ok=True)


def sync_aquamai_file(filename, keycodes, target_player):
    path = Path(filename)
    if path.suffix.lower() != ".toml":
        raise ValueError("请选择 .toml 配置文件")

    raw = path.read_bytes()
    has_bom = raw.startswith(codecs.BOM_UTF8)
    payload = raw[len(codecs.BOM_UTF8):] if has_bom else raw
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("AquaMai 配置文件必须使用 UTF-8 编码") from error

    mappings = build_aquamai_mappings(keycodes, target_player)
    rewritten, changed_fields = rewrite_aquamai_keymap(text, mappings)
    replacement = rewritten.encode("utf-8")
    if has_bom:
        replacement = codecs.BOM_UTF8 + replacement
    backup_path = _atomic_replace_with_backup(path, raw, replacement)

    return changed_fields, backup_path


_INI_SECTION_RE = re.compile(
    r"^[ \t]*\[(?P<name>[^\]\r\n]+)\][ \t]*(?:[;#].*)?(?:\r?\n)?$"
)


def _parse_segatools_ini(text, description):
    parser = configparser.RawConfigParser(
        strict=True,
        allow_no_value=True,
        inline_comment_prefixes=(";", "#"),
        empty_lines_in_values=False,
    )
    try:
        parser.read_string(text)
    except configparser.Error as error:
        raise ValueError(f"{description}不是合法 INI：{error}") from error

    io4_sections = [
        section for section in parser.sections()
        if section.strip().casefold() == "io4"
    ]
    if len(io4_sections) != 1:
        if io4_sections:
            raise ValueError("存在多个 [io4] 配置段，拒绝修改")
        raise ValueError("所选文件中缺少 [io4] 配置段")
    return parser, io4_sections[0]


def rewrite_segatools_io4(text):
    parser_before, section_before = _parse_segatools_ini(
        text, "原 segatools 配置文件"
    )
    enable_before = parser_before.get(
        section_before, "enable", raw=True, fallback=None
    )
    if parser_before.has_option(section_before, "enable") and enable_before is None:
        raise ValueError("[io4] Enable 没有值，拒绝自动覆盖")
    if isinstance(enable_before, str) and enable_before.strip() == "0":
        return text, False
    lines = text.splitlines(keepends=True)
    headers = []
    for index, line in enumerate(lines):
        match = _INI_SECTION_RE.match(line)
        if match:
            headers.append((index, match.group("name").strip()))

    io4_headers = [
        (index, name) for index, name in headers if name.casefold() == "io4"
    ]
    if len(io4_headers) != 1:
        if io4_headers:
            raise ValueError("存在多个 [io4] 配置段，拒绝修改")
        raise ValueError("无法安全定位 [io4] 配置段，原文件未修改")

    section_start = io4_headers[0][0]
    section_end = next(
        (index for index, _name in headers if index > section_start), len(lines)
    )
    active_pattern = re.compile(
        r"^(?P<indent>[ \t]*)(?P<name>enable)[ \t]*[:=][ \t]*"
        r"[^;#\r\n]*?(?P<suffix>[ \t]*(?:[;#].*)?)(?P<eol>\r?\n)?$",
        re.IGNORECASE,
    )
    commented_pattern = re.compile(
        r"^(?P<indent>[ \t]*)[;#][ \t]*(?P<name>enable)"
        r"[ \t]*[:=][^\r\n]*(?P<eol>\r?\n)?$",
        re.IGNORECASE,
    )
    active_matches = [
        (index, active_pattern.match(lines[index]))
        for index in range(section_start + 1, section_end)
        if active_pattern.match(lines[index])
    ]
    if len(active_matches) > 1:
        raise ValueError("[io4] 中存在重复的 Enable 配置")
    commented_matches = [
        (index, commented_pattern.match(lines[index]))
        for index in range(section_start + 1, section_end)
        if commented_pattern.match(lines[index])
    ]

    newline = "\r\n" if "\r\n" in text else "\n"
    if active_matches:
        index, match = active_matches[0]
        replacement = (
            f'{match.group("indent")}{match.group("name")}=0'
            f'{match.group("suffix")}{match.group("eol") or ""}'
        )
        changed = replacement != lines[index]
        lines[index] = replacement
    elif commented_matches:
        index, match = commented_matches[0]
        replacement = (
            f'{match.group("indent")}{match.group("name")}=0'
            f'{match.group("eol") or ""}'
        )
        changed = replacement != lines[index]
        lines[index] = replacement
    else:
        insertion_index = section_start + 1
        if not lines[section_start].endswith(("\r", "\n")):
            lines[section_start] += newline
        lines.insert(insertion_index, f"Enable=0{newline}")
        changed = True

    rewritten = "".join(lines)
    parser_after, section_after = _parse_segatools_ini(
        rewritten, "修改后的 segatools 配置文件"
    )
    if parser_after.get(section_after, "enable", raw=True, fallback="").strip() != "0":
        raise ValueError("修改后 [io4] Enable 未通过校验，原文件未修改")
    return rewritten, changed


def sync_segatools_file(filename):
    path = Path(filename)
    if path.suffix.lower() != ".ini":
        raise ValueError("请选择 .ini 配置文件")

    raw = path.read_bytes()
    if raw.startswith(codecs.BOM_UTF8):
        preamble = codecs.BOM_UTF8
        encoding = "utf-8"
    elif raw.startswith(codecs.BOM_UTF16_LE):
        preamble = codecs.BOM_UTF16_LE
        encoding = "utf-16-le"
    elif raw.startswith(codecs.BOM_UTF16_BE):
        preamble = codecs.BOM_UTF16_BE
        encoding = "utf-16-be"
    else:
        preamble = b""
        try:
            raw.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            encoding = "gb18030"

    try:
        text = raw[len(preamble):].decode(encoding)
    except UnicodeDecodeError as error:
        raise ValueError("无法识别 segatools 配置文件编码") from error

    rewritten, changed = rewrite_segatools_io4(text)
    replacement = preamble + rewritten.encode(encoding)
    backup_path = _atomic_replace_with_backup(path, raw, replacement)

    return changed, backup_path


class ConfigGui:
    def __init__(self, root):
        self.root = root
        self.root.title("控制器配置")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.ser = None
        self.port_lookup = {}
        self.current_values = {}
        self.current_keycodes = []
        self.loading = False
        self.cooldown_until = 0.0
        self.cooldown_after_id = None
        self.poll_after_id = None
        self.save_result_unknown = False

        self.port_var = tk.StringVar()
        self.io4_var = tk.IntVar(value=-1)
        self.keyboard_var = tk.IntVar(value=-1)
        self.light_var = tk.IntVar(value=-1)
        self.led_count_var = tk.IntVar(value=-1)
        self.custom_target_var = tk.StringVar(value="1P")
        self.status_var = tk.StringVar(value="请选择串口并连接设备。")
        self.key_name_vars = [
            tk.StringVar(value=f"{name}: --")
            for name in BUTTON_DISPLAY_NAMES
        ]

        self.option_widgets = []
        self.custom_target_widgets = []
        self._build_ui()
        self.refresh_ports()
        self._set_options_enabled(False)
        self._update_save_button()

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=12)
        main.grid(row=0, column=0, sticky="nsew")

        connection = ttk.LabelFrame(main, text="设备连接", padding=8)
        connection.grid(row=0, column=0, sticky="ew")

        self.port_box = ttk.Combobox(
            connection, textvariable=self.port_var, state="readonly", width=34
        )
        self.port_box.grid(row=0, column=0, padx=(0, 6))

        self.refresh_button = ttk.Button(
            connection, text="刷新", command=self.refresh_ports, width=7
        )
        self.refresh_button.grid(row=0, column=1, padx=(0, 6))

        self.connect_button = ttk.Button(
            connection, text="连接", command=self.toggle_connection, width=7
        )
        self.connect_button.grid(row=0, column=2)

        io4_frame = ttk.LabelFrame(main, text="IO4 模式", padding=8)
        io4_frame.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self._add_radio_group(
            io4_frame,
            self.io4_var,
            (("1P", protocol.IO4_MODE_1P),
             ("2P", protocol.IO4_MODE_2P),
             ("关闭", protocol.IO4_MODE_OFF)),
            lambda: self._write_option("io4"),
        )

        keyboard_frame = ttk.LabelFrame(main, text="键盘模式", padding=8)
        keyboard_frame.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self._add_radio_group(
            keyboard_frame,
            self.keyboard_var,
            (("1P", protocol.KEY_MODE_1P),
             ("2P", protocol.KEY_MODE_2P),
             ("自定义", protocol.KEY_MODE_CUSTOM),
             ("关闭", protocol.KEY_MODE_OFF)),
            lambda: self._write_option("keyboard"),
        )

        light_frame = ttk.LabelFrame(main, text="待机灯光", padding=8)
        light_frame.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self._add_radio_group(
            light_frame,
            self.light_var,
            (("白光", 0), ("彩虹", 1)),
            lambda: self._write_option("light"),
        )

        led_count_frame = ttk.LabelFrame(
            main, text="每逻辑灯珠数", padding=8
        )
        led_count_frame.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        self._add_radio_group(
            led_count_frame,
            self.led_count_var,
            (("1", 1), ("2", 2), ("3", 3), ("4", 4)),
            lambda: self._write_option("led_count"),
        )

        key_frame = ttk.LabelFrame(main, text="当前键位", padding=8)
        key_frame.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        for index, variable in enumerate(self.key_name_vars):
            label = ttk.Label(
                key_frame, textvariable=variable, anchor="w", width=18
            )
            label.grid(
                row=index // 4,
                column=index % 4,
                padx=(0, 4),
                pady=2,
                sticky="w",
            )

        aquamai_frame = ttk.LabelFrame(main, text="游戏配置同步", padding=8)
        aquamai_frame.grid(row=6, column=0, sticky="ew", pady=(10, 0))

        ttk.Label(aquamai_frame, text="Custom 写入目标：").grid(
            row=0, column=0, sticky="w"
        )
        for column, player in enumerate(("1P", "2P"), start=1):
            radio = ttk.Radiobutton(
                aquamai_frame,
                text=player,
                value=player,
                variable=self.custom_target_var,
            )
            radio.grid(row=0, column=column, padx=(0, 14), sticky="w")
            self.custom_target_widgets.append(radio)

        self.aquamai_button = ttk.Button(
            aquamai_frame,
            text="选择 AquaMai.toml 并立即同步",
            command=self.choose_and_sync_aquamai,
        )
        self.aquamai_button.grid(
            row=1, column=0, columnspan=3, sticky="ew", pady=(8, 0)
        )

        self.segatools_button = ttk.Button(
            aquamai_frame,
            text="选择 segatools.ini 并设置实体 IO4",
            command=self.choose_and_sync_segatools,
        )
        self.segatools_button.grid(
            row=2, column=0, columnspan=3, sticky="ew", pady=(8, 0)
        )

        flash_actions = ttk.Frame(main)
        flash_actions.grid(row=7, column=0, sticky="ew", pady=(12, 0))
        flash_actions.columnconfigure(0, weight=1)
        flash_actions.columnconfigure(1, weight=1)

        self.save_button = ttk.Button(
            flash_actions, text="保存到 Flash", command=self.save_to_flash
        )
        self.save_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        self.default_button = ttk.Button(
            flash_actions,
            text="一键恢复默认配置",
            command=self.restore_defaults,
        )
        self.default_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        status = ttk.Label(
            main, textvariable=self.status_var, anchor="w", wraplength=500
        )
        status.grid(row=8, column=0, sticky="ew", pady=(10, 0))

    def _add_radio_group(self, parent, variable, items, command):
        for column, (text, value) in enumerate(items):
            radio = ttk.Radiobutton(
                parent,
                text=text,
                value=value,
                variable=variable,
                command=command,
            )
            radio.grid(row=0, column=column, padx=(0, 14), sticky="w")
            self.option_widgets.append(radio)

    def refresh_ports(self):
        if self.ser is not None:
            return

        previous_device = self.port_lookup.get(self.port_var.get())
        ports = list(list_ports.comports())
        self.port_lookup = {
            f"{port.device}  {port.description}": port.device for port in ports
        }
        choices = list(self.port_lookup)
        self.port_box["values"] = choices

        selected = next(
            (name for name, device in self.port_lookup.items()
             if device == previous_device),
            choices[0] if choices else "",
        )
        self.port_var.set(selected)

        if choices:
            self.status_var.set(f"发现 {len(choices)} 个串口，请连接设备。")
        else:
            self.status_var.set("未发现串口。")

    def toggle_connection(self):
        if self.ser is None:
            self.connect()
        else:
            self.disconnect("设备已断开。")

    def connect(self):
        port = self.port_lookup.get(self.port_var.get())
        if not port:
            messagebox.showwarning("未选择串口", "请先选择一个串口。")
            return

        try:
            self.ser = serial.Serial(
                port=port,
                baudrate=115200,
                timeout=0.05,
                write_timeout=0.5,
            )
            self._read_device_config()
        except (serial.SerialException, OSError, RuntimeError) as error:
            self.disconnect(f"连接失败：{error}")
            messagebox.showerror("连接失败", str(error))
            return

        self.port_box.configure(state="disabled")
        self.refresh_button.configure(state="disabled")
        self.connect_button.configure(text="断开")
        self._set_options_enabled(True)
        self._update_save_button()
        self.status_var.set(f"已连接 {port}，当前配置已读取。")
        self._schedule_device_poll()

    def disconnect(self, status_text):
        self._cancel_device_poll()
        self._close_serial()
        self.current_values.clear()
        self.current_keycodes.clear()
        self.save_result_unknown = False
        self.loading = True
        try:
            self.keyboard_var.set(-1)
            self.io4_var.set(-1)
            self.light_var.set(-1)
            self.led_count_var.set(-1)
        finally:
            self.loading = False
        self._clear_key_names()
        self.port_box.configure(state="readonly")
        self.refresh_button.configure(state="normal")
        self.connect_button.configure(text="连接")
        self._set_options_enabled(False)
        self._update_save_button()
        self.status_var.set(status_text)

    def _close_serial(self):
        if self.ser is not None:
            try:
                self.ser.close()
            except (serial.SerialException, OSError):
                pass
            self.ser = None

    def _schedule_device_poll(self):
        self._cancel_device_poll()
        if self.ser is not None:
            self.poll_after_id = self.root.after(
                DEVICE_POLL_INTERVAL_MS, self._poll_device_config
            )

    def _cancel_device_poll(self):
        if self.poll_after_id is not None:
            self.root.after_cancel(self.poll_after_id)
            self.poll_after_id = None

    def _poll_device_config(self):
        self.poll_after_id = None
        if self.ser is None:
            return

        try:
            self._read_device_config()
        except RuntimeError as error:
            self.disconnect(f"持续读取主控配置失败，设备已断开：{error}")
            return

        self._set_options_enabled(True)
        self._schedule_device_poll()

    def _send_command(self, command, value=0, index=0):
        if self.ser is None:
            raise RuntimeError("设备未连接。")

        try:
            ack = protocol.send_cmd(self.ser, command, index, value)
        except (serial.SerialException, OSError) as error:
            raise RuntimeError(f"串口通信失败：{error}") from error

        if ack is None:
            raise AckTimeoutError("设备未返回 ACK。")
        if ack["status"] != 0:
            raise DeviceCommandError(ack["status_text"])
        if ack["idx"] != index:
            raise RuntimeError(
                f"ACK 索引不一致：期望 {index}，返回 {ack['idx']}"
            )
        return ack

    def _read_device_config(self):
        keyboard = self._send_command(protocol.KEYCFG_GET_KEY_MODE)["key"]
        io4 = self._send_command(protocol.KEYCFG_GET_IO4_MODE)["key"]
        light = self._send_command(protocol.KEYCFG_GET_RAINBOW_ENABLED)["key"]
        led_count = self._send_command(
            protocol.KEYCFG_GET_LEDS_PER_LOGIC
        )["key"]

        if keyboard not in protocol.KEY_MODE_NAME:
            raise RuntimeError(f"设备返回未知键盘模式：{keyboard}")
        if io4 not in protocol.IO4_MODE_NAME:
            raise RuntimeError(f"设备返回未知 IO4 模式：{io4}")
        if light not in (0, 1):
            raise RuntimeError(f"设备返回未知灯光模式：{light}")
        if led_count not in (1, 2, 3, 4):
            raise RuntimeError(f"设备返回无效灯珠数：{led_count}")

        self.loading = True
        try:
            self.keyboard_var.set(keyboard)
            self.io4_var.set(io4)
            self.light_var.set(light)
            self.led_count_var.set(led_count)
        finally:
            self.loading = False

        self.current_values = {
            "keyboard": keyboard,
            "io4": io4,
            "light": light,
            "led_count": led_count,
        }
        self._read_key_names()

    def _read_device_config_with_retries(self, attempts=READBACK_ATTEMPTS):
        if attempts < 1:
            raise ValueError("回读次数必须至少为 1")

        last_error = None
        for attempt in range(attempts):
            if attempt:
                time.sleep(READBACK_RETRY_DELAY_SECONDS * attempt)
            try:
                self._read_device_config()
                return
            except RuntimeError as error:
                last_error = error
        raise RuntimeError(
            f"连续 {attempts} 次读取完整设备配置失败：{last_error}"
        ) from last_error

    def _read_key_names(self):
        keycodes = []
        for index in range(protocol.BTN_NUM):
            ack = self._send_command(
                protocol.KEYCFG_GET_KEY, index=index
            )
            if ack["idx"] != index:
                raise RuntimeError(
                    f"按键编号返回不一致：请求 {index}，返回 {ack['idx']}"
                )
            keycodes.append(ack["key"])

        self.current_keycodes = keycodes
        for index, keycode in enumerate(keycodes):
            self.key_name_vars[index].set(
                f"{BUTTON_DISPLAY_NAMES[index]}: {protocol.hid_name(keycode)}"
            )

    def _clear_key_names(self):
        for name, variable in zip(BUTTON_DISPLAY_NAMES, self.key_name_vars):
            variable.set(f"{name}: --")

    def choose_and_sync_aquamai(self):
        if self.ser is None:
            return

        keyboard_mode = self.keyboard_var.get()
        if keyboard_mode == protocol.KEY_MODE_OFF:
            messagebox.showwarning(
                "键盘模式已关闭",
                "请先选择键盘 1P、2P 或自定义模式，再同步 AquaMai 配置。",
            )
            return

        filename = filedialog.askopenfilename(
            parent=self.root,
            title="选择实际使用的 AquaMai.toml",
            filetypes=(("TOML 配置文件", "*.toml"), ("所有文件", "*.*")),
            initialfile="AquaMai.toml",
        )
        if not filename:
            return

        if keyboard_mode == protocol.KEY_MODE_1P:
            target_player = "1P"
        elif keyboard_mode == protocol.KEY_MODE_2P:
            target_player = "2P"
        else:
            target_player = self.custom_target_var.get()

        self._set_options_enabled(False)
        try:
            self._read_key_names()
            changed_fields, backup_path = sync_aquamai_file(
                filename, self.current_keycodes, target_player
            )
        except (OSError, RuntimeError, ValueError) as error:
            self.status_var.set(f"AquaMai 配置同步失败：{error}")
            messagebox.showerror("同步失败", str(error))
        else:
            if changed_fields:
                detail = f"已更新 {len(changed_fields)} 项"
            else:
                detail = "配置已经一致，无需更新"
            backup_detail = (
                f"备份：{backup_path}"
                if backup_path is not None
                else "未创建备份（文件无变化）"
            )
            self.status_var.set(
                f"AquaMai 同步完成：{detail}；{backup_detail}"
            )
            messagebox.showinfo(
                "同步完成",
                f"{detail}。\n\n{backup_detail}",
            )
        finally:
            self._set_options_enabled(self.ser is not None)

    def choose_and_sync_segatools(self):
        if self.ser is None:
            return
        if self.io4_var.get() == protocol.IO4_MODE_OFF:
            messagebox.showwarning(
                "IO4 未开启",
                "主控 IO4 当前为关闭状态，segatools.ini 未作修改。",
            )
            return

        filename = filedialog.askopenfilename(
            parent=self.root,
            title="选择实际使用的 segatools.ini",
            filetypes=(("INI 配置文件", "*.ini"), ("所有文件", "*.*")),
            initialfile="segatools.ini",
        )
        if not filename:
            return

        self._set_options_enabled(False)
        try:
            changed, backup_path = sync_segatools_file(filename)
        except (OSError, RuntimeError, ValueError) as error:
            self.status_var.set(f"Segatools 配置同步失败：{error}")
            messagebox.showerror("同步失败", str(error))
        else:
            detail = "已设置 [io4] Enable=0" if changed else (
                "[io4] Enable 已经是 0，无需修改"
            )
            backup_detail = (
                f"备份：{backup_path}"
                if backup_path is not None
                else "未创建备份（文件无变化）"
            )
            self.status_var.set(
                f"Segatools 同步完成：{detail}；{backup_detail}"
            )
            messagebox.showinfo(
                "同步完成",
                f"{detail}。\n\n{backup_detail}",
            )
        finally:
            self._set_options_enabled(self.ser is not None)

    def _write_option(self, option):
        if self.loading or self.ser is None:
            return

        settings = {
            "keyboard": (
                self.keyboard_var,
                protocol.KEYCFG_SET_KEY_MODE,
                protocol.KEYCFG_GET_KEY_MODE,
                protocol.KEY_MODE_NAME,
                "键盘模式",
            ),
            "io4": (
                self.io4_var,
                protocol.KEYCFG_SET_IO4_MODE,
                protocol.KEYCFG_GET_IO4_MODE,
                protocol.IO4_MODE_NAME,
                "IO4 模式",
            ),
            "light": (
                self.light_var,
                protocol.KEYCFG_SET_RAINBOW_ENABLED,
                protocol.KEYCFG_GET_RAINBOW_ENABLED,
                (0, 1),
                "待机灯光",
            ),
            "led_count": (self.led_count_var,
                          protocol.KEYCFG_SET_LEDS_PER_LOGIC,
                          protocol.KEYCFG_GET_LEDS_PER_LOGIC,
                          (1, 2, 3, 4),
                          "每逻辑灯珠数"),
        }
        variable, set_command, get_command, valid_values, display_name = (
            settings[option]
        )
        new_value = variable.get()
        old_value = self.current_values.get(option)

        if new_value == old_value:
            return

        self._set_options_enabled(False)
        try:
            observed, set_error = write_once_and_read_back(
                self._send_command,
                set_command,
                get_command,
                new_value,
                valid_values,
            )
        except RuntimeError as error:
            detail = (
                f"{display_name}写入后无法确认设备实际状态，已断开连接并禁止保存。"
                f"\n\n{error}"
            )
            self.disconnect(f"{display_name}状态未知，已断开：{error}")
            messagebox.showerror("设备状态未知", detail)
            return
        else:
            self.loading = True
            try:
                variable.set(observed)
            finally:
                self.loading = False
            self.current_values[option] = observed

            if option == "keyboard":
                try:
                    self._read_key_names()
                except RuntimeError as error:
                    detail = (
                        f"键盘模式已回读为 {observed}，但按键状态读取失败，"
                        f"已断开连接并禁止保存。\n\n{error}"
                    )
                    self.disconnect(f"按键状态未知，已断开：{error}")
                    messagebox.showerror("设备状态未知", detail)
                    return

            if observed == new_value:
                if set_error is None:
                    self.status_var.set(
                        f"{display_name}已写入 RAM，并已回读确认；"
                        "如需断电保存，请点击保存到 Flash。"
                    )
                else:
                    self.status_var.set(
                        f"{display_name}的 SET ACK 未确认，但 GET 回读确认写入成功；"
                        "如需断电保存，请点击保存到 Flash。"
                    )
                    messagebox.showwarning(
                        "已由回读确认",
                        f"{display_name}的 SET ACK 未确认：{set_error}\n\n"
                        f"设备回读值为 {observed}，已按设备实际状态更新界面。",
                    )
            else:
                extra = f"\n\nSET 返回：{set_error}" if set_error else ""
                self.status_var.set(
                    f"{display_name}目标值 {new_value} 未生效；"
                    f"设备实际值为 {observed}，界面已同步。"
                )
                messagebox.showwarning(
                    "写入未生效",
                    f"{display_name}目标值 {new_value} 未生效。"
                    f"\n设备回读值：{observed}。"
                    f"\n界面已按设备实际状态更新。{extra}",
                )
        finally:
            self._set_options_enabled(self.ser is not None)
            self._update_save_button()

    def restore_defaults(self):
        if self.ser is None or time.monotonic() < self.cooldown_until:
            return

        confirmed = messagebox.askyesno(
            "确认恢复默认配置",
            "将恢复默认按键、键盘模式、IO4、灯光和灯珠数，"
            "并立即写入 Flash。\n\n该操作不能由界面撤销，是否继续？",
            icon="warning",
        )
        if not confirmed:
            return

        self._set_options_enabled(False)
        self.save_button.configure(state="disabled")
        command_error = None
        try:
            try:
                ack = self._send_command(protocol.KEYCFG_LOAD_DEFAULT)
                if ack["key"] != 0:
                    raise RuntimeError(
                        f"恢复默认 ACK 返回值不一致："
                        f"期望 0，返回 {ack['key']}"
                    )
            except RuntimeError as error:
                command_error = error

            try:
                self._read_device_config_with_retries()
            except RuntimeError as read_error:
                detail = (
                    "恢复默认命令已经发送，但无法读取设备最终状态。"
                    "为防止误保存，连接已断开。"
                    f"\n\n命令结果：{command_error or 'ACK 成功'}"
                    f"\n回读结果：{read_error}"
                )
                self.disconnect("恢复默认后的设备状态未知，已断开连接。")
                self.save_result_unknown = True
                self.cooldown_until = (
                    time.monotonic() + SAVE_COOLDOWN_SECONDS
                )
                self.status_var.set(
                    "恢复默认结果未知，设备已断开；请重新连接后检查配置。"
                )
                messagebox.showerror("设备状态未知", detail)
                self._cooldown_tick()
                return

            defaults_observed = device_config_is_default(
                self.current_values, self.current_keycodes
            )
            self.cooldown_until = time.monotonic() + SAVE_COOLDOWN_SECONDS

            if defaults_observed and command_error is None:
                self.save_result_unknown = False
                self.status_var.set(
                    "默认配置已恢复、回读确认并保存到 Flash。"
                )
                messagebox.showinfo(
                    "恢复完成",
                    "默认配置已恢复，设备回读一致，并已保存到 Flash。",
                )
            elif defaults_observed and isinstance(
                command_error, DeviceCommandError
            ):
                self.save_result_unknown = False
                self.status_var.set(
                    "RAM 已恢复默认配置，但设备报告 Flash 保存失败；"
                    "冷却结束后可再次保存。"
                )
                messagebox.showwarning(
                    "默认值已生效，保存失败",
                    "设备回读确认 RAM 已恢复默认配置，但 Flash 保存失败："
                    f"{command_error}\n\n冷却结束后可点击“保存到 Flash”重试。",
                )
            elif defaults_observed:
                self.save_result_unknown = True
                self.status_var.set(
                    "RAM 已回读确认为默认配置，但 Flash 保存结果未知。"
                )
                messagebox.showwarning(
                    "默认值已确认，保存结果未知",
                    "恢复命令的 ACK 未确认，但设备回读已是默认配置。"
                    f"\n\nFlash 保存结果未知：{command_error}",
                )
            else:
                self.save_result_unknown = not isinstance(
                    command_error, DeviceCommandError
                )
                self.status_var.set(
                    "恢复默认未生效；界面已按设备实际配置刷新。"
                )
                messagebox.showerror(
                    "恢复默认未生效",
                    "设备回读配置不是预期默认值，界面已按设备实际状态更新。"
                    f"\n\n命令结果：{command_error or 'ACK 成功'}",
                )

            self._cooldown_tick()
        finally:
            self._set_options_enabled(self.ser is not None)
            self._update_save_button()

    def save_to_flash(self):
        if self.ser is None or time.monotonic() < self.cooldown_until:
            return

        self.save_button.configure(state="disabled")
        self.default_button.configure(state="disabled")
        try:
            ack = self._send_command(protocol.KEYCFG_SAVE_FLASH)
            if ack["key"] != 0:
                raise RuntimeError(
                    f"保存 ACK 返回值不一致：期望 0，返回 {ack['key']}"
                )
        except DeviceCommandError as error:
            self.save_result_unknown = False
            self.status_var.set(f"保存到 Flash 失败：{error}")
            messagebox.showerror("保存失败", str(error))
            self._update_save_button()
            return
        except RuntimeError as error:
            self.save_result_unknown = True
            self.cooldown_until = time.monotonic() + SAVE_COOLDOWN_SECONDS
            self.status_var.set(
                "保存命令已发送，但结果未确认；可能已经写入 Flash，"
                "5 秒内禁止重复保存。"
            )
            messagebox.showwarning(
                "保存结果未确认",
                f"无法确认保存结果：{error}\n\n"
                "设备可能已经完成 Flash 写入，请勿立即重复保存。",
            )
            self._cooldown_tick()
            return

        self.save_result_unknown = False
        self.cooldown_until = time.monotonic() + SAVE_COOLDOWN_SECONDS
        self.status_var.set("配置已保存到 Flash，5 秒内禁止重复保存。")
        self._cooldown_tick()

    def _cooldown_tick(self):
        remaining = self.cooldown_until - time.monotonic()
        if remaining <= 0:
            self.cooldown_until = 0.0
            self.cooldown_after_id = None
            self._update_save_button()
            if self.ser is not None:
                if self.save_result_unknown:
                    self.status_var.set(
                        "上次保存结果仍未确认；如需确保持久化，可再次保存。"
                    )
                else:
                    self.status_var.set("可以再次保存到 Flash。")
            return

        seconds = max(1, int(remaining + 0.999))
        self.save_button.configure(
            text=f"保存到 Flash（{seconds}s）", state="disabled"
        )
        self.cooldown_after_id = self.root.after(100, self._cooldown_tick)

    def _set_options_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        for widget in self.option_widgets:
            widget.configure(state=state)
        self.aquamai_button.configure(state=state)
        self.segatools_button.configure(state=state)
        default_state = (
            "normal"
            if enabled and time.monotonic() >= self.cooldown_until
            else "disabled"
        )
        self.default_button.configure(state=default_state)
        custom_state = (
            "normal"
            if enabled and self.keyboard_var.get() == protocol.KEY_MODE_CUSTOM
            else "disabled"
        )
        for widget in self.custom_target_widgets:
            widget.configure(state=custom_state)

    def _update_save_button(self):
        remaining = self.cooldown_until - time.monotonic()
        if remaining > 0:
            seconds = max(1, int(remaining + 0.999))
            self.save_button.configure(
                text=f"保存到 Flash（{seconds}s）", state="disabled"
            )
            self.default_button.configure(state="disabled")
        else:
            state = "normal" if self.ser is not None else "disabled"
            self.save_button.configure(text="保存到 Flash", state=state)
            self.default_button.configure(state=state)

    def close(self):
        self._cancel_device_poll()
        if self.cooldown_after_id is not None:
            self.root.after_cancel(self.cooldown_after_id)
            self.cooldown_after_id = None
        self._close_serial()
        self.root.destroy()


def main():
    if "--self-test" in sys.argv:
        sample = (
            "[GameSystem.KeyMap]\r\n"
            '#Button1_1P = "W"\r\n'
            '#Select_1P = "Alpha3"\r\n'
            '#Select_2P = "KeypadMultiply"\r\n'
            '#Test = "ScrollLock"\r\n'
            '#Service = "Pause"\r\n'
            "\r\n[Next.Section]\r\n"
        )
        keycodes = [
            0x05, 0x08, 0x07, 0x06, 0x1B, 0x1D, 0x04, 0x14,
            0x20, 0x55, 0x25, 0x26, 0x28,
        ]
        rewritten, changed = rewrite_aquamai_keymap(
            sample, build_aquamai_mappings(keycodes, "1P")
        )
        assert 'Button1_1P = "B"\r\n' in rewritten
        assert '#Select_1P = "Alpha3"\r\n' in rewritten
        assert 'Test = "Alpha8"\r\n' in rewritten
        assert 'Service = "Alpha9"\r\n' in rewritten
        assert 'DisableDebugInput = false\r\n' in rewritten
        assert "Button1_1P" in changed
        segatools, segatools_changed = rewrite_segatools_io4(
            "[io4]\r\nEnable=1\r\ntest=0x38\r\n\r\n[button]\r\nenable=1\r\n"
        )
        assert "[io4]\r\nEnable=0\r\ntest=0x38" in segatools
        assert "[button]\r\nenable=1" in segatools
        assert segatools_changed
        commented_ini, _ = rewrite_segatools_io4(
            "[io4]\nEnable = 1 # keep\n"
        )
        assert "Enable=0 # keep\n" in commented_ini

        multiline = (
            'description = """text\n[Fake.Section]\ntext"""\n'
            "[GameSystem.KeyMap] # inline comment\n"
            "Button1_1P = '''old\n[Not.A.Section]\nvalue'''\n"
        )
        multiline_rewritten, _ = rewrite_aquamai_keymap(
            multiline, build_aquamai_mappings(keycodes, "1P")
        )
        tomllib.loads(multiline_rewritten)
        assert "[Fake.Section]" in multiline_rewritten
        assert "[Not.A.Section]" not in multiline_rewritten

        try:
            rewrite_aquamai_keymap(
                "[GameSystem.KeyMap]\nButton1_1P = 123\n",
                build_aquamai_mappings(keycodes, "1P"),
            )
        except ValueError:
            pass
        else:
            raise AssertionError("错误类型的 AquaMai 键值未被拒绝")

        try:
            rewrite_segatools_io4("[io4]\nEnable\n")
        except ValueError:
            pass
        else:
            raise AssertionError("无值的 segatools Enable 未被拒绝")

        calls = []

        def ack_lost_then_new(command, value=0, index=0):
            calls.append((command, value, index))
            if command == protocol.KEYCFG_SET_RAINBOW_ENABLED:
                raise AckTimeoutError("模拟 SET ACK 丢失")
            return {"key": 1}

        observed, set_error = write_once_and_read_back(
            ack_lost_then_new,
            protocol.KEYCFG_SET_RAINBOW_ENABLED,
            protocol.KEYCFG_GET_RAINBOW_ENABLED,
            1,
            (0, 1),
            retry_delay=0,
        )
        assert observed == 1 and isinstance(set_error, AckTimeoutError)
        assert calls == [
            (protocol.KEYCFG_SET_RAINBOW_ENABLED, 1, 0),
            (protocol.KEYCFG_GET_RAINBOW_ENABLED, 0, 0),
        ]

        def ack_lost_then_old(command, value=0, index=0):
            if command == protocol.KEYCFG_SET_RAINBOW_ENABLED:
                raise AckTimeoutError("模拟 SET ACK 丢失")
            return {"key": 0}

        observed, set_error = write_once_and_read_back(
            ack_lost_then_old,
            protocol.KEYCFG_SET_RAINBOW_ENABLED,
            protocol.KEYCFG_GET_RAINBOW_ENABLED,
            1,
            (0, 1),
            retry_delay=0,
        )
        assert observed == 0 and isinstance(set_error, AckTimeoutError)

        failed_calls = []

        def all_ack_lost(command, value=0, index=0):
            failed_calls.append(command)
            raise AckTimeoutError("模拟所有 ACK 丢失")

        try:
            write_once_and_read_back(
                all_ack_lost,
                protocol.KEYCFG_SET_RAINBOW_ENABLED,
                protocol.KEYCFG_GET_RAINBOW_ENABLED,
                1,
                (0, 1),
                attempts=3,
                retry_delay=0,
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("连续回读失败未进入未知状态")
        assert failed_calls.count(protocol.KEYCFG_SET_RAINBOW_ENABLED) == 1
        assert failed_calls.count(protocol.KEYCFG_GET_RAINBOW_ENABLED) == 3

        assert device_config_is_default(
            DEFAULT_DEVICE_VALUES, DEFAULT_DEVICE_KEYCODES
        )
        assert not device_config_is_default(
            {**DEFAULT_DEVICE_VALUES, "light": 0},
            DEFAULT_DEVICE_KEYCODES,
        )

        class SelfTestValue:
            def __init__(self, value=""):
                self.value = value

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        class SelfTestButton:
            def configure(self, **_kwargs):
                pass

        def make_restore_test(command_error=None):
            gui = ConfigGui.__new__(ConfigGui)
            gui.ser = object()
            gui.cooldown_until = 0.0
            gui.save_result_unknown = False
            gui.status_var = SelfTestValue()
            gui.save_button = SelfTestButton()
            gui.current_values = {}
            gui.current_keycodes = []
            gui._set_options_enabled = lambda _enabled: None
            gui._update_save_button = lambda: None
            gui._cooldown_tick = lambda: None
            sent = []

            def send(command, value=0, index=0):
                sent.append((command, value, index))
                if command_error is not None:
                    raise command_error
                return {"key": 0}

            def read_defaults():
                gui.current_values = dict(DEFAULT_DEVICE_VALUES)
                gui.current_keycodes = list(DEFAULT_DEVICE_KEYCODES)

            gui._send_command = send
            gui._read_device_config_with_retries = read_defaults
            return gui, sent

        original_askyesno = messagebox.askyesno
        original_showinfo = messagebox.showinfo
        original_showwarning = messagebox.showwarning
        original_showerror = messagebox.showerror
        messagebox.askyesno = lambda *_args, **_kwargs: True
        messagebox.showinfo = lambda *_args, **_kwargs: None
        messagebox.showwarning = lambda *_args, **_kwargs: None
        messagebox.showerror = lambda *_args, **_kwargs: None
        try:
            restore_gui, restore_sent = make_restore_test()
            restore_gui.restore_defaults()
            assert restore_sent == [(protocol.KEYCFG_LOAD_DEFAULT, 0, 0)]
            assert device_config_is_default(
                restore_gui.current_values, restore_gui.current_keycodes
            )
            assert not restore_gui.save_result_unknown

            lost_gui, lost_sent = make_restore_test(
                AckTimeoutError("模拟恢复默认 ACK 丢失")
            )
            lost_gui.restore_defaults()
            assert lost_sent == [(protocol.KEYCFG_LOAD_DEFAULT, 0, 0)]
            assert device_config_is_default(
                lost_gui.current_values, lost_gui.current_keycodes
            )
            assert lost_gui.save_result_unknown
        finally:
            messagebox.askyesno = original_askyesno
            messagebox.showinfo = original_showinfo
            messagebox.showwarning = original_showwarning
            messagebox.showerror = original_showerror

        class ShortWriteSerial:
            def reset_input_buffer(self):
                pass

            def write(self, packet):
                return len(packet) - 1

        try:
            protocol.send_cmd(ShortWriteSerial(), protocol.KEYCFG_GET_KEY_MODE)
        except serial.SerialTimeoutException:
            pass
        else:
            raise AssertionError("串口短写未被拒绝")

        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            aqua_path = temporary_root / "AquaMai.toml"
            aqua_path.write_text(
                '[GameSystem.KeyMap]\nButton1_1P = "A"\n',
                encoding="utf-8",
            )
            user_tmp = Path(f"{aqua_path}.tmp")
            user_tmp.write_bytes(b"user temporary file")
            old_backup = Path(f"{aqua_path}.bak")
            old_backup.write_bytes(b"old backup")
            aqua_original = aqua_path.read_bytes()

            _changed, backup_path = sync_aquamai_file(
                aqua_path, keycodes, "1P"
            )
            assert backup_path is not None and backup_path != old_backup
            assert backup_path.read_bytes() == aqua_original
            assert old_backup.read_bytes() == b"old backup"
            assert user_tmp.read_bytes() == b"user temporary file"

            _changed, second_backup = sync_aquamai_file(
                aqua_path, keycodes, "1P"
            )
            assert not _changed and second_backup is None

            ini_path = temporary_root / "segatools.ini"
            ini_original = (
                codecs.BOM_UTF16_BE
                + "[io4]\r\nEnable=1\r\n".encode("utf-16-be")
            )
            ini_path.write_bytes(ini_original)
            ini_changed, ini_backup = sync_segatools_file(ini_path)
            assert ini_changed and ini_backup.read_bytes() == ini_original
            assert ini_path.read_bytes().startswith(codecs.BOM_UTF16_BE)
            ini_changed, ini_backup = sync_segatools_file(ini_path)
            assert not ini_changed and ini_backup is None

        print("自检通过")
        return

    root = tk.Tk()
    ConfigGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
