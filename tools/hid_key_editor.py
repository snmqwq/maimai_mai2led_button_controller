#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""独立的 HID 按键修改器。"""

from __future__ import annotations

import queue
import re
import sys
import threading
import time
import tkinter as tk
import traceback
from dataclasses import dataclass
from tkinter import messagebox, ttk

import serial
from serial.tools import list_ports


APP_TITLE = "HID 按键修改器"
BUTTON_COUNT = 13
BUTTON_NAMES = (
    "BTN1",
    "BTN2",
    "BTN3",
    "BTN4",
    "BTN5",
    "BTN6",
    "BTN7",
    "BTN8",
    "1P SELECT",
    "2P SELECT",
    "TEST",
    "SERVICE",
    "BTN",
)

MAGIC = bytes((0x91, 0x3E, 0xED, 0x20, 0x7C, 0x99, 0x58, 0xAC))
ACK_HEAD = 0xAC

CMD_SET_KEY = 0xA1
CMD_SAVE_FLASH = 0xA2
CMD_GET_KEY = 0xA4
CMD_SET_KEY_MODE = 0xA9
CMD_GET_KEY_MODE = 0xAA

KEY_MODE_1P = 0
KEY_MODE_2P = 1
KEY_MODE_CUSTOM = 2
KEY_MODE_OFF = 3
KEY_MODE_NAMES = {
    KEY_MODE_1P: "1P",
    KEY_MODE_2P: "2P",
    KEY_MODE_CUSTOM: "自定义",
    KEY_MODE_OFF: "关闭",
}
SELECTABLE_KEY_MODES = (KEY_MODE_1P, KEY_MODE_2P, KEY_MODE_CUSTOM)

STATUS_TEXT = {
    0x00: "成功",
    0x01: "校验和错误",
    0x02: "索引或参数错误",
    0x03: "命令错误",
}

SAVE_COOLDOWN_SECONDS = 5.0
ACK_TIMEOUT_SECONDS = 0.5
SAVE_ACK_TIMEOUT_SECONDS = 2.0


# These are single-USB-Keyboard-Usage mappings for AquaMai's KeyCodeID.
# Source: AquaMai.Config/Types/KeyCodeID.cs (MuNET-OSS/AquaMai).
HID_KEY_NAMES = {
    0x00: "None",
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
    # Windows maps Keyboard Application (0x65) to AquaMai/Unity KeyCode.Menu.
    0x65: "Menu",
    0x67: "KeypadEquals",
    0x75: "Help",
    0xE0: "LeftControl",
    0xE1: "LeftShift",
    0xE2: "LeftAlt",
    0xE3: "LeftWindows",
    0xE4: "RightControl",
    0xE5: "RightShift",
    0xE6: "RightAlt",
    0xE7: "RightWindows",
}
HID_KEY_NAMES.update(
    {0x04 + index: chr(ord("A") + index) for index in range(26)}
)
HID_KEY_NAMES.update(
    {0x1E + index: f"Alpha{index + 1}" for index in range(9)}
)
HID_KEY_NAMES[0x27] = "Alpha0"
HID_KEY_NAMES.update(
    {0x3A + index: f"F{index + 1}" for index in range(12)}
)
HID_KEY_NAMES.update(
    {0x68 + index: f"F{index + 13}" for index in range(3)}
)
HID_KEY_NAMES.update(
    {0x59 + index: f"Keypad{index + 1}" for index in range(9)}
)
HID_KEY_NAMES[0x62] = "Keypad0"

HID_NAME_TO_KEY = {name.upper(): code for code, name in HID_KEY_NAMES.items()}

DISPLAY_KEY_RE = re.compile(r"^(.*?)\s+\(0x([0-9A-Fa-f]{2})\)\s*$")


def key_name(key: int) -> str:
    return HID_KEY_NAMES.get(key, "不受支持")


def format_key(key: int) -> str:
    return f"{key_name(key)} (0x{key:02X})"


def parse_key(text: str) -> int:
    value = text.strip()
    if not value:
        raise ValueError("键值不能为空")

    display_match = DISPLAY_KEY_RE.fullmatch(value)
    if display_match:
        display_name = display_match.group(1).strip()
        key = int(display_match.group(2), 16)
        if display_name.upper() != key_name(key).upper():
            raise ValueError(
                f"HID 键名与数值不一致：{display_name} / 0x{key:02X}"
            )
    else:
        upper_value = value.upper()
        if upper_value in HID_NAME_TO_KEY:
            key = HID_NAME_TO_KEY[upper_value]
        else:
            try:
                if upper_value.startswith("0X"):
                    key = int(upper_value, 16)
                else:
                    raise ValueError
            except ValueError as error:
                raise ValueError(
                    f"未知 AquaMai KeyCodeID 名称或 HID 键值：{value}"
                ) from error

    if not 0 <= key <= 0xFF:
        raise ValueError("HID 键值必须在 0x00～0xFF 范围内")
    if key not in HID_KEY_NAMES:
        raise ValueError(
            f"0x{key:02X} 不在 AquaMai KeyCodeID 单键白名单中"
        )
    return key


def parse_staged_key(text: str, current_key: int) -> int:
    """Parse an edit while allowing an unsupported device value to stay unchanged."""
    value = text.strip()
    if current_key not in HID_KEY_NAMES and value == format_key(current_key):
        return current_key
    return parse_key(value)


KEY_CHOICES = tuple(format_key(code) for code in sorted(HID_KEY_NAMES))


class CommunicationError(RuntimeError):
    """串口通信失败。"""

    def __init__(self, message, fatal=False):
        super().__init__(message)
        self.fatal = fatal


class DeviceStatusError(RuntimeError):
    """设备返回了失败状态。"""

    def __init__(self, status):
        self.status = status
        message = STATUS_TEXT.get(status, f"未知状态 0x{status:02X}")
        super().__init__(message)


class ApplyError(RuntimeError):
    """批量写入失败，可能附带回滚后的设备快照。"""

    def __init__(
        self,
        message,
        observed_keys=None,
        observed_mode=None,
        rollback_complete=False,
        connection_lost=False,
    ):
        super().__init__(message)
        self.observed_keys = observed_keys
        self.observed_mode = observed_mode
        self.rollback_complete = rollback_complete
        self.connection_lost = connection_lost


@dataclass(frozen=True)
class Ack:
    status: int
    index: int
    key: int


@dataclass(frozen=True)
class ApplyResult:
    keys: tuple[int, ...]
    key_mode: int | None
    changed_indices: tuple[int, ...]


class DeviceClient:
    def __init__(self, port: str):
        try:
            self.serial = serial.Serial(
                port=port,
                baudrate=115200,
                timeout=0.05,
                write_timeout=0.5,
            )
        except (serial.SerialException, OSError) as error:
            raise CommunicationError(
                f"打开串口 {port} 失败：{error}", fatal=True
            ) from error
        self.port = port
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        return bool(self.serial and self.serial.is_open)

    def close(self) -> None:
        if self.serial is None:
            return
        try:
            self.serial.close()
        except (serial.SerialException, OSError):
            pass

    @staticmethod
    def _build_packet(command: int, index: int, key: int) -> bytes:
        if not 0 <= index <= 0xFF:
            raise ValueError("协议索引必须在 0～255 范围内")
        if not 0 <= key <= 0xFF:
            raise ValueError("协议键值必须在 0x00～0xFF 范围内")
        checksum = (command + index + key) & 0xFF
        return MAGIC + bytes((command, index, key, checksum))

    def _read_ack(self, timeout: float) -> Ack | None:
        deadline = time.monotonic() + timeout
        buffer = bytearray()
        while time.monotonic() < deadline:
            try:
                waiting = self.serial.in_waiting
                if waiting:
                    buffer.extend(self.serial.read(waiting))
            except (serial.SerialException, OSError) as error:
                raise CommunicationError(
                    f"读取串口失败：{error}", fatal=True
                ) from error

            while len(buffer) >= 4:
                if buffer[0] == ACK_HEAD:
                    return Ack(buffer[1], buffer[2], buffer[3])
                del buffer[0]
            time.sleep(0.005)
        return None

    def command(
        self,
        command: int,
        index: int = 0,
        key: int = 0,
        retries: int = 0,
        ack_timeout: float = ACK_TIMEOUT_SECONDS,
    ) -> Ack:
        last_error = None
        with self._lock:
            for attempt in range(retries + 1):
                try:
                    self.serial.reset_input_buffer()
                    packet = self._build_packet(command, index, key)
                    written = self.serial.write(packet)
                    if written != len(packet):
                        raise CommunicationError(
                            f"串口短写：应发送 {len(packet)} 字节，实际 {written}",
                            fatal=True,
                        )
                    self.serial.flush()
                except CommunicationError:
                    raise
                except (serial.SerialException, OSError) as error:
                    raise CommunicationError(
                        f"写入串口失败：{error}", fatal=True
                    ) from error

                ack = self._read_ack(ack_timeout)
                if ack is None:
                    last_error = CommunicationError(
                        f"设备未在 {ack_timeout:g} 秒内返回 ACK"
                    )
                    if attempt < retries:
                        continue
                    raise last_error

                if ack.status != 0:
                    raise DeviceStatusError(ack.status)

                if ack.index != index:
                    last_error = CommunicationError(
                        f"ACK 索引不匹配：请求 {index}，返回 {ack.index}"
                    )
                    if attempt < retries:
                        continue
                    raise last_error

                return ack

        raise last_error or CommunicationError("未知串口通信错误")

    def read_key(self, index: int) -> int:
        if not 0 <= index < BUTTON_COUNT:
            raise ValueError(f"按键索引必须在 0～{BUTTON_COUNT - 1} 范围内")
        return self.command(CMD_GET_KEY, index=index, retries=1).key

    def read_all_keys(self, progress=None) -> tuple[int, ...]:
        values = []
        for index in range(BUTTON_COUNT):
            if progress:
                progress(f"正在读取按键 {index + 1}/{BUTTON_COUNT}…")
            values.append(self.read_key(index))
        return tuple(values)

    def set_key(self, index: int, key: int) -> None:
        if not 0 <= index < BUTTON_COUNT:
            raise ValueError(f"按键索引必须在 0～{BUTTON_COUNT - 1} 范围内")
        if not 0 <= key <= 0xFF:
            raise ValueError("HID 键值必须在 0x00～0xFF 范围内")
        ack = self.command(CMD_SET_KEY, index=index, key=key, retries=1)
        if ack.key != key:
            raise CommunicationError(
                f"按键 {index} 返回值不匹配：写入 0x{key:02X}，返回 0x{ack.key:02X}"
            )

    def save_flash(self) -> None:
        ack = self.command(CMD_SAVE_FLASH, ack_timeout=SAVE_ACK_TIMEOUT_SECONDS)
        if ack.key != 0:
            raise CommunicationError(
                f"保存 ACK 返回值不匹配：期望 0，返回 0x{ack.key:02X}"
            )

    def get_key_mode(self) -> int | None:
        for attempt in range(2):
            try:
                mode = self.command(CMD_GET_KEY_MODE).key
                break
            except DeviceStatusError as error:
                if error.status != 0x03:
                    raise
                if attempt == 0:
                    time.sleep(0.02)
                    continue
                return None
        if mode not in KEY_MODE_NAMES:
            raise CommunicationError(f"设备返回未知键盘模式：{mode}")
        return mode

    def set_key_mode(self, mode: int) -> None:
        ack = self.command(CMD_SET_KEY_MODE, key=mode, retries=1)
        if ack.key != mode:
            raise CommunicationError(
                f"键盘模式返回值不匹配：写入 {mode}，返回 {ack.key}"
            )


def apply_key_changes(
    client,
    current_keys,
    target_keys,
    original_mode=None,
    progress=None,
) -> ApplyResult:
    current = tuple(current_keys)
    target = tuple(target_keys)
    if len(current) != BUTTON_COUNT or len(target) != BUTTON_COUNT:
        raise ValueError(f"必须提供 {BUTTON_COUNT} 个按键值")
    for label, values in (("当前键值", current), ("目标键值", target)):
        invalid = [
            index
            for index, value in enumerate(values)
            if not isinstance(value, int) or not 0 <= value <= 0xFF
        ]
        if invalid:
            indexes = "、".join(str(index) for index in invalid)
            raise ValueError(f"{label}超出 0x00～0xFF：索引 {indexes}")
    changes = tuple(
        index for index, (old, new) in enumerate(zip(current, target))
        if old != new
    )
    if not changes:
        return ApplyResult(current, original_mode, ())
    unsupported = [index for index in changes if target[index] not in HID_KEY_NAMES]
    if unsupported:
        indexes = "、".join(str(index) for index in unsupported)
        raise ValueError(
            f"新键值不在 AquaMai KeyCodeID 单键白名单中：索引 {indexes}"
        )

    attempted = []
    confirmed = []
    touches_main_key = any(index < 8 for index in changes)
    try:
        for position, index in enumerate(changes, start=1):
            attempted.append(index)
            if progress:
                progress(
                    f"正在写入 {BUTTON_NAMES[index]}（{position}/{len(changes)}）…"
                )
            client.set_key(index, target[index])
            confirmed.append(index)

        if progress:
            progress("正在回读全部按键并校验…")
        observed = tuple(client.read_all_keys())
        mismatches = [
            index for index in range(BUTTON_COUNT)
            if observed[index] != target[index]
        ]
        if mismatches:
            labels = "、".join(BUTTON_NAMES[index] for index in mismatches)
            raise CommunicationError(f"以下按键回读不一致：{labels}")

        mode = client.get_key_mode()
        if original_mode is not None:
            expected_mode = (
                KEY_MODE_CUSTOM
                if original_mode in (KEY_MODE_1P, KEY_MODE_2P)
                and touches_main_key
                else original_mode
            )
            if mode != expected_mode:
                actual = "不支持/未知" if mode is None else KEY_MODE_NAMES[mode]
                raise CommunicationError(
                    f"键盘模式回读不一致：期望 {KEY_MODE_NAMES[expected_mode]}，"
                    f"实际 {actual}"
                )
        return ApplyResult(observed, mode, changes)
    except Exception as error:
        connection_lost = bool(
            isinstance(error, CommunicationError) and error.fatal
        )
        rollback_errors = []
        diagnostic_notes = []
        if progress:
            progress("写入失败，正在读取设备实际状态…")

        failure_keys = None
        failure_mode = None
        failure_mode_read_ok = False
        try:
            failure_keys = tuple(client.read_all_keys())
        except Exception as state_error:
            diagnostic_notes.append(f"失败后读取键值：{state_error}")
            connection_lost = connection_lost or bool(
                isinstance(state_error, CommunicationError) and state_error.fatal
            )
        if failure_keys is not None:
            try:
                failure_mode = client.get_key_mode()
                failure_mode_read_ok = True
            except Exception as mode_error:
                diagnostic_notes.append(f"失败后读取键盘模式：{mode_error}")
                connection_lost = connection_lost or bool(
                    isinstance(mode_error, CommunicationError) and mode_error.fatal
                )

        rollback_targets = ()
        can_compensate = failure_keys is not None and not connection_lost
        if (
            can_compensate
            and touches_main_key
            and original_mode is not None
            and not failure_mode_read_ok
        ):
            can_compensate = False
            diagnostic_notes.append("键盘模式未确认，未继续发送补偿写入")

        if can_compensate:
            rollback_targets = tuple(
                index
                for index in dict.fromkeys(attempted)
                if failure_keys[index] != current[index]
            )
            if rollback_targets and progress:
                progress("正在恢复确实发生变化的 RAM 键值…")
            for index in reversed(rollback_targets):
                try:
                    client.set_key(index, current[index])
                except Exception as rollback_error:
                    rollback_errors.append(
                        f"{BUTTON_NAMES[index]}：{rollback_error}"
                    )
                    connection_lost = connection_lost or bool(
                        isinstance(rollback_error, CommunicationError)
                        and rollback_error.fatal
                    )

        main_effect_seen = any(index < 8 for index in confirmed) or any(
            index < 8 for index in rollback_targets
        )
        hidden_custom_may_have_changed = (
            original_mode in (KEY_MODE_1P, KEY_MODE_2P)
            and main_effect_seen
        )
        unknown_mode_main_effect = (
            original_mode is None and main_effect_seen
        )
        if (
            can_compensate
            and original_mode in (KEY_MODE_1P, KEY_MODE_2P)
            and main_effect_seen
        ):
            try:
                client.set_key_mode(original_mode)
            except Exception as rollback_error:
                rollback_errors.append(f"键盘模式：{rollback_error}")
                connection_lost = connection_lost or bool(
                    isinstance(rollback_error, CommunicationError)
                    and rollback_error.fatal
                )

        observed = None
        observed_mode = None
        try:
            observed = tuple(client.read_all_keys())
        except Exception as readback_error:
            rollback_errors.append(f"回滚后读取：{readback_error}")
            connection_lost = connection_lost or bool(
                isinstance(readback_error, CommunicationError)
                and readback_error.fatal
            )
        try:
            observed_mode = client.get_key_mode()
        except Exception as mode_error:
            rollback_errors.append(f"回滚后读取键盘模式：{mode_error}")
            connection_lost = connection_lost or bool(
                isinstance(mode_error, CommunicationError) and mode_error.fatal
            )

        mode_restored = (
            original_mode is None or observed_mode == original_mode
        )

        rollback_complete = (
            observed == current
            and mode_restored
            and not rollback_errors
            and not hidden_custom_may_have_changed
            and not unknown_mode_main_effect
        )
        if rollback_complete:
            detail = "写入失败，已恢复修改前的 RAM 配置。"
        elif hidden_custom_may_have_changed and observed == current and mode_restored:
            detail = (
                "写入失败；可见键值和键盘模式已恢复，但固件可能已覆盖隐藏的"
                "自定义主键缓存。请勿保存 Flash，建议让设备重新上电后再连接。"
            )
        elif unknown_mode_main_effect and observed == current:
            detail = (
                "写入失败；可见键值已恢复，但设备不支持模式读取，无法排除"
                "主键写入带来的其他状态变化。请勿保存 Flash，建议让设备"
                "重新上电后再连接。"
            )
        elif rollback_errors:
            detail = "写入失败，且回滚不完整：" + "；".join(rollback_errors)
        else:
            detail = "写入失败，设备当前 RAM 状态无法确认。"
        if diagnostic_notes and not rollback_complete:
            detail += "\n诊断信息：" + "；".join(diagnostic_notes)
        raise ApplyError(
            f"{detail}\n\n原始错误：{error}",
            observed_keys=observed,
            observed_mode=observed_mode,
            rollback_complete=rollback_complete,
            connection_lost=connection_lost,
        ) from error


def apply_key_mode_change(
    client,
    current_keys,
    current_mode,
    target_mode,
    progress=None,
) -> ApplyResult:
    """Switch keyboard mode in RAM, verify it, and restore the old mode on failure."""
    original_keys = tuple(current_keys)
    if len(original_keys) != BUTTON_COUNT:
        raise ValueError(f"必须提供 {BUTTON_COUNT} 个当前按键值")
    if current_mode not in KEY_MODE_NAMES:
        raise ValueError("当前键盘模式未知，不能安全切换")
    if target_mode not in SELECTABLE_KEY_MODES:
        raise ValueError("目标键盘模式必须是 1P、2P 或自定义")
    if current_mode == target_mode:
        return ApplyResult(original_keys, current_mode, ())

    try:
        if progress:
            progress(f"正在切换到 {KEY_MODE_NAMES[target_mode]} 模式…")
        client.set_key_mode(target_mode)

        if progress:
            progress("正在回读键盘模式…")
        observed_mode = client.get_key_mode()
        if observed_mode != target_mode:
            actual = (
                "不支持/未知"
                if observed_mode is None
                else KEY_MODE_NAMES.get(observed_mode, str(observed_mode))
            )
            raise CommunicationError(
                f"键盘模式回读不一致：期望 {KEY_MODE_NAMES[target_mode]}，"
                f"实际 {actual}"
            )

        if progress:
            progress("正在读取切换后的 13 个按键…")
        observed_keys = tuple(client.read_all_keys(progress))
        if len(observed_keys) != BUTTON_COUNT:
            raise CommunicationError(
                f"设备仅返回 {len(observed_keys)} 个按键，期望 {BUTTON_COUNT} 个"
            )
        return ApplyResult(observed_keys, observed_mode, ())
    except Exception as error:
        connection_lost = bool(
            isinstance(error, CommunicationError) and error.fatal
        )
        rollback_errors = []
        observed_keys = None
        observed_mode = None
        rollback_complete = False

        if not connection_lost:
            if progress:
                progress("模式切换未确认，正在恢复原模式…")
            try:
                client.set_key_mode(current_mode)
                observed_mode = client.get_key_mode()
                if observed_mode != current_mode:
                    actual = (
                        "不支持/未知"
                        if observed_mode is None
                        else KEY_MODE_NAMES.get(observed_mode, str(observed_mode))
                    )
                    raise CommunicationError(
                        f"恢复模式回读不一致：期望 {KEY_MODE_NAMES[current_mode]}，"
                        f"实际 {actual}"
                    )
                observed_keys = tuple(client.read_all_keys())
                if observed_keys != original_keys:
                    raise CommunicationError("恢复原模式后，按键回读与切换前不一致")
                rollback_complete = True
            except Exception as rollback_error:
                rollback_errors.append(str(rollback_error))
                connection_lost = connection_lost or bool(
                    isinstance(rollback_error, CommunicationError)
                    and rollback_error.fatal
                )

        if not rollback_complete and not connection_lost:
            observed_mode = None
            observed_keys = None
            try:
                observed_mode = client.get_key_mode()
            except Exception as state_error:
                rollback_errors.append(f"读取最终模式：{state_error}")
                connection_lost = connection_lost or bool(
                    isinstance(state_error, CommunicationError)
                    and state_error.fatal
                )
            if not connection_lost:
                try:
                    observed_keys = tuple(client.read_all_keys())
                except Exception as state_error:
                    rollback_errors.append(f"读取最终键值：{state_error}")
                    connection_lost = connection_lost or bool(
                        isinstance(state_error, CommunicationError)
                        and state_error.fatal
                    )
            rollback_complete = (
                observed_mode == current_mode
                and observed_keys == original_keys
            )

        if rollback_complete:
            detail = "模式切换失败，已恢复切换前的 RAM 状态。"
        elif connection_lost:
            detail = "模式切换失败且连接已丢失，设备 RAM 状态无法确认。"
        else:
            detail = "模式切换失败，未能确认恢复；禁止保存到 Flash。"
        if rollback_errors:
            detail += "\n诊断信息：" + "；".join(rollback_errors)
        raise ApplyError(
            f"{detail}\n\n原始错误：{error}",
            observed_keys=observed_keys,
            observed_mode=observed_mode,
            rollback_complete=rollback_complete,
            connection_lost=connection_lost,
        ) from error


class HidKeyEditor:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.minsize(760, 700)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.client: DeviceClient | None = None
        self.current_port: str | None = None
        self.port_states = {}
        self.port_lookup = {}
        self.device_keys: tuple[int, ...] = ()
        self.connect_snapshot: tuple[int, ...] = ()
        self.key_mode: int | None = None
        self.ram_dirty = False
        self.save_blocked = False
        self.loading = False
        self.busy = False
        self.closing = False
        self.cooldown_until = 0.0
        self.cooldown_after_id = None
        self.worker_events = queue.Queue()

        self.port_var = tk.StringVar()
        self.mode_var = tk.StringVar(value="当前模式：--")
        self.mode_choice_var = tk.IntVar(value=-1)
        self.status_var = tk.StringVar(value="请选择串口并连接设备。")
        self.current_vars = [tk.StringVar(value="--") for _ in range(BUTTON_COUNT)]
        self.edit_vars = [tk.StringVar() for _ in range(BUTTON_COUNT)]
        self.row_status_vars = [tk.StringVar(value="未连接") for _ in range(BUTTON_COUNT)]
        self.edit_boxes = []
        self.mode_buttons = []

        self._build_ui()
        self.refresh_ports()
        self._update_controls()
        self.root.after(50, self._poll_worker_events)

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=12)
        main.grid(row=0, column=0, sticky="nsew")
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)

        connection = ttk.LabelFrame(main, text="设备连接", padding=8)
        connection.grid(row=0, column=0, sticky="ew")
        connection.columnconfigure(0, weight=1)

        self.port_box = ttk.Combobox(
            connection,
            textvariable=self.port_var,
            state="normal",
            width=42,
        )
        self.port_box.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.refresh_button = ttk.Button(
            connection, text="刷新", command=self.refresh_ports, width=8
        )
        self.refresh_button.grid(row=0, column=1, padx=(0, 6))

        self.connect_button = ttk.Button(
            connection, text="连接", command=self.toggle_connection, width=8
        )
        self.connect_button.grid(row=0, column=2, padx=(0, 6))

        self.read_button = ttk.Button(
            connection, text="重新读取", command=self.read_device, width=10
        )
        self.read_button.grid(row=0, column=3)

        mode_frame = ttk.Frame(connection)
        mode_frame.grid(
            row=1, column=0, columnspan=4, sticky="ew", pady=(8, 0)
        )
        mode_frame.columnconfigure(5, weight=1)

        ttk.Label(mode_frame, textvariable=self.mode_var, width=18).grid(
            row=0, column=0, sticky="w", padx=(0, 12)
        )
        ttk.Label(mode_frame, text="切换到：").grid(
            row=0, column=1, sticky="w"
        )
        for column, mode in enumerate(SELECTABLE_KEY_MODES, start=2):
            button = ttk.Radiobutton(
                mode_frame,
                text=KEY_MODE_NAMES[mode],
                value=mode,
                variable=self.mode_choice_var,
                command=self._mode_choice_changed,
            )
            button.grid(row=0, column=column, sticky="w", padx=(0, 10))
            self.mode_buttons.append(button)

        self.apply_mode_button = ttk.Button(
            mode_frame,
            text="应用模式到 RAM",
            command=self.apply_mode_change,
            width=16,
        )
        self.apply_mode_button.grid(row=0, column=6, sticky="e")

        ttk.Label(
            mode_frame,
            text=(
                "1P/2P 使用固件固定主键预设；自定义模式使用可编辑键表。"
                "模式切换只写入 RAM。"
            ),
        ).grid(row=1, column=0, columnspan=7, sticky="w", pady=(7, 0))

        editor = ttk.LabelFrame(main, text="HID 键位", padding=8)
        editor.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        editor.columnconfigure(3, weight=1)

        headers = ("编号", "逻辑按键", "设备当前值", "新的 HID 键值", "状态")
        for column, text in enumerate(headers):
            ttk.Label(editor, text=text).grid(
                row=0, column=column, sticky="w", padx=(0, 10), pady=(0, 5)
            )

        for index, name in enumerate(BUTTON_NAMES):
            row = index + 1
            ttk.Label(editor, text=str(index), width=5).grid(
                row=row, column=0, sticky="w", padx=(0, 10), pady=2
            )
            ttk.Label(editor, text=name, width=14).grid(
                row=row, column=1, sticky="w", padx=(0, 10), pady=2
            )
            ttk.Label(
                editor,
                textvariable=self.current_vars[index],
                width=22,
            ).grid(row=row, column=2, sticky="w", padx=(0, 10), pady=2)

            box = ttk.Combobox(
                editor,
                textvariable=self.edit_vars[index],
                values=KEY_CHOICES,
                state="disabled",
                width=28,
            )
            box.grid(row=row, column=3, sticky="ew", padx=(0, 10), pady=2)
            box.bind(
                "<<ComboboxSelected>>",
                lambda _event, item=index: self._editor_changed(item),
            )
            box.bind(
                "<KeyRelease>",
                lambda _event, item=index: self._editor_changed(item),
            )
            box.bind(
                "<FocusOut>",
                lambda _event, item=index: self._editor_changed(item),
            )
            self.edit_boxes.append(box)

            ttk.Label(
                editor,
                textvariable=self.row_status_vars[index],
                width=10,
            ).grid(row=row, column=4, sticky="w", pady=2)

        ttk.Separator(editor, orient="horizontal").grid(
            row=BUTTON_COUNT + 1,
            column=0,
            columnspan=5,
            sticky="ew",
            pady=(8, 6),
        )
        ttk.Label(
            editor,
            text=(
                "仅提供 AquaMai KeyCodeID 中可由单个 USB HID 键表达的项目；"
                "可选择名称或输入对应的白名单 0xNN。"
                "如需修改原有自定义主键，请先切换到自定义模式。"
            ),
            wraplength=710,
        ).grid(
            row=BUTTON_COUNT + 2,
            column=0,
            columnspan=5,
            sticky="w",
        )

        actions = ttk.Frame(main)
        actions.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        actions.columnconfigure(1, weight=1)

        self.discard_button = ttk.Button(
            actions, text="撤销未应用修改", command=self.discard_edits
        )
        self.discard_button.grid(row=0, column=0, padx=(0, 8))

        self.apply_button = ttk.Button(
            actions, text="应用按键到 RAM", command=self.apply_changes
        )
        self.apply_button.grid(row=0, column=2, padx=(0, 8))

        self.save_button = ttk.Button(
            actions, text="保存全部配置到 Flash", command=self.save_to_flash
        )
        self.save_button.grid(row=0, column=3)

        ttk.Label(
            main,
            textvariable=self.status_var,
            anchor="w",
            wraplength=730,
        ).grid(row=3, column=0, sticky="ew", pady=(10, 0))

    def refresh_ports(self) -> None:
        if self.client is not None or self.busy:
            return
        previous_port = self.port_lookup.get(self.port_var.get())
        ports = list(list_ports.comports())
        self.port_lookup = {
            f"{port.device}  {port.description}": port.device for port in ports
        }
        choices = list(self.port_lookup)
        self.port_box["values"] = choices
        selected = next(
            (
                label
                for label, device in self.port_lookup.items()
                if device == previous_port
            ),
            choices[0] if choices else "",
        )
        self.port_var.set(selected)
        if choices:
            self.status_var.set(f"发现 {len(choices)} 个串口，请连接设备。")
        else:
            self.status_var.set("未发现串口。")

    def toggle_connection(self) -> None:
        if self.client is None:
            self.connect()
        else:
            self.disconnect()

    def connect(self) -> None:
        selected = self.port_var.get().strip()
        port = self.port_lookup.get(selected, selected)
        if not port:
            messagebox.showwarning("未选择串口", "请先选择一个串口。")
            return

        def work(progress):
            client = DeviceClient(port)
            try:
                keys = client.read_all_keys(progress)
                progress("正在读取键盘模式…")
                mode = client.get_key_mode()
                return client, keys, mode
            except Exception:
                client.close()
                raise

        def success(result):
            client, keys, mode = result
            self.client = client
            self.current_port = port
            self.device_keys = tuple(keys)
            self.connect_snapshot = tuple(keys)
            self.key_mode = mode
            self.ram_dirty, self.save_blocked = self.port_states.get(
                port, (False, False)
            )
            self._load_keys(keys)
            self._update_mode_text()
            if self.save_blocked:
                self.status_var.set(
                    f"已连接 {port}；程序仍记录该端口的 RAM 状态不确定，"
                    "禁止保存。请让设备重新上电并重启本程序。"
                )
            elif self.ram_dirty:
                self.status_var.set(
                    f"已连接 {port}；程序仍记录该端口有尚未保存的 RAM 修改。"
                )
            else:
                self.status_var.set(f"已连接 {port}，13 个按键已读取。")

        self._start_task(work, success, self._connection_error)

    def disconnect(self, ask=True) -> None:
        if self.busy:
            return
        if ask and not self._confirm_leave("断开设备"):
            return

        if self.client is not None:
            self.client.close()
        if self.current_port is not None:
            self.port_states[self.current_port] = (
                self.ram_dirty,
                self.save_blocked,
            )
        self.client = None
        self.current_port = None
        self.device_keys = ()
        self.connect_snapshot = ()
        self.key_mode = None
        self.ram_dirty = False
        self.save_blocked = False
        self._clear_keys()
        self._update_mode_text()
        self.status_var.set("设备已断开。")
        self._update_controls()

    def read_device(self) -> None:
        if self.client is None:
            return
        if self._has_staged_changes() and not messagebox.askyesno(
            "放弃未应用修改",
            "重新读取会放弃界面中尚未应用的修改，是否继续？",
        ):
            return

        client = self.client

        def work(progress):
            keys = client.read_all_keys(progress)
            mode = client.get_key_mode()
            return keys, mode

        def success(result):
            keys, mode = result
            self.device_keys = tuple(keys)
            self.key_mode = mode
            self._load_keys(keys)
            self._update_mode_text()
            self.status_var.set("设备按键配置已重新读取。")

        self._start_task(work, success, self._task_error)

    def discard_edits(self) -> None:
        if self.device_keys:
            self._load_keys(self.device_keys)
            self._sync_mode_choice()
            self._update_controls()
            self.status_var.set("尚未应用的界面修改已撤销。")

    def _mode_choice_changed(self) -> None:
        if not self.loading:
            if self._has_staged_mode_change():
                self.status_var.set(
                    "已选择目标模式；请先应用或撤销模式切换，再修改按键。"
                )
            self._update_controls()

    def apply_mode_change(self) -> None:
        if self.client is None or self.busy:
            return
        target_mode = self.mode_choice_var.get()
        if self.key_mode is None:
            self._sync_mode_choice()
            messagebox.showwarning(
                "不支持模式切换",
                "当前固件无法读取键盘模式，因此不能安全切换。",
            )
            return
        if target_mode not in SELECTABLE_KEY_MODES:
            messagebox.showwarning("未选择模式", "请选择 1P、2P 或自定义模式。")
            return
        if target_mode == self.key_mode:
            self.status_var.set("设备已经处于所选键盘模式。")
            self._update_controls()
            return

        key_changed, _valid = self._inspect_edits()
        had_key_edits = key_changed
        if key_changed:
            if not messagebox.askyesno(
                "放弃未应用按键修改",
                "切换键盘模式后会重新读取 13 个键值，并放弃界面中尚未应用的"
                "按键修改。是否继续？",
            ):
                return

        client = self.client
        before_keys = tuple(self.device_keys)
        original_mode = self.key_mode
        was_dirty = self.ram_dirty
        was_blocked = self.save_blocked

        def work(progress):
            return apply_key_mode_change(
                client,
                before_keys,
                original_mode,
                target_mode,
                progress=progress,
            )

        def success(result: ApplyResult):
            self.device_keys = result.keys
            self.key_mode = result.key_mode
            self.ram_dirty = True
            self.save_blocked = was_blocked
            self._load_keys(result.keys)
            self._update_mode_text()
            if self.save_blocked:
                self.status_var.set(
                    f"键盘模式已切换为 {KEY_MODE_NAMES[result.key_mode]} 并完成回读；"
                    "但此前 RAM 状态不确定，本次会话仍禁止保存。"
                )
            else:
                self.status_var.set(
                    f"键盘模式已切换为 {KEY_MODE_NAMES[result.key_mode]}，"
                    "并重新读取 13 个按键。若需断电保留，请保存全部配置到 Flash。"
                )

        def failure(error):
            if isinstance(error, ApplyError):
                if error.observed_keys is not None:
                    self.device_keys = tuple(error.observed_keys)
                    if error.rollback_complete and had_key_edits:
                        self._refresh_current_values()
                    else:
                        self._load_keys(self.device_keys)
                if error.observed_mode is not None:
                    self.key_mode = error.observed_mode
                elif not error.rollback_complete:
                    self.key_mode = None
                self.ram_dirty = was_dirty or not error.rollback_complete
                self.save_blocked = was_blocked or not error.rollback_complete
            self._update_mode_text()
            self._task_error(error, title="模式切换失败")

        self._start_task(work, success, failure)

    def _collect_target_keys(self) -> tuple[int, ...]:
        if not self.device_keys:
            raise ValueError("尚未读取设备按键")
        values = []
        errors = []
        for index, variable in enumerate(self.edit_vars):
            try:
                values.append(
                    parse_staged_key(variable.get(), self.device_keys[index])
                )
            except ValueError as error:
                errors.append(f"{BUTTON_NAMES[index]}：{error}")
        if errors:
            raise ValueError("\n".join(errors))
        return tuple(values)

    def apply_changes(self) -> None:
        if self.client is None:
            return
        try:
            target = self._collect_target_keys()
        except ValueError as error:
            messagebox.showerror("键值无效", str(error))
            return

        changes = tuple(
            index
            for index, (old, new) in enumerate(zip(self.device_keys, target))
            if old != new
        )
        if not changes:
            self.status_var.set("没有需要应用的按键修改。")
            return

        if self.key_mode in (KEY_MODE_1P, KEY_MODE_2P) and any(
            index < 8 for index in changes
        ):
            if not messagebox.askyesno(
                "将切换为自定义模式",
                "修改 BTN1～BTN8 会使固件从 1P/2P 切换到自定义键盘模式，"
                "固件会先把当前预设复制为新的自定义键表，再修改所选按键；"
                "原有自定义 BTN1～BTN8 将被替换。\n\n"
                "如需保留原有自定义键表，请取消并先切换到自定义模式。\n\n"
                "是否继续？",
            ):
                return

        client = self.client
        before = tuple(self.device_keys)
        original_mode = self.key_mode
        was_dirty = self.ram_dirty

        def work(progress):
            return apply_key_changes(
                client,
                before,
                target,
                original_mode=original_mode,
                progress=progress,
            )

        def success(result: ApplyResult):
            self.device_keys = result.keys
            self.key_mode = result.key_mode
            self.ram_dirty = True
            self._load_keys(result.keys)
            self._update_mode_text()
            names = "、".join(BUTTON_NAMES[index] for index in result.changed_indices)
            if self.save_blocked:
                self.status_var.set(
                    f"已写入 RAM 并回读验证：{names}；但此前设备状态不确定，"
                    "本次会话禁止保存。请让设备重新上电后再连接。"
                )
            else:
                self.status_var.set(
                    f"已写入 RAM 并回读验证：{names}。"
                    "如需断电保存，请保存全部配置到 Flash。"
                )

        def failure(error):
            if isinstance(error, ApplyError):
                if error.observed_keys is not None:
                    self.device_keys = tuple(error.observed_keys)
                    self._refresh_current_values()
                self.key_mode = error.observed_mode
                self._update_mode_text()
                self.ram_dirty = was_dirty or not error.rollback_complete
                self.save_blocked = self.save_blocked or not error.rollback_complete
            self._task_error(error, title="应用失败")

        self._start_task(work, success, failure)

    def save_to_flash(self) -> None:
        if self.client is None or not self.ram_dirty or self.busy:
            return
        key_changed, valid = self._inspect_edits()
        if self.save_blocked:
            self.status_var.set(
                "设备 RAM 状态不确定，禁止保存；请让设备重新上电后再连接。"
            )
            return
        if key_changed or self._has_staged_mode_change() or not valid:
            self.status_var.set("请先应用或撤销界面中的修改，再保存配置。")
            return
        if time.monotonic() < self.cooldown_until:
            return
        if not messagebox.askyesno(
            "保存全部配置到 Flash",
            "固件会把设备当前 RAM 中的全部控制器配置保存到 Flash，"
            "不仅包括按键。\n\n确认继续？",
        ):
            return

        client = self.client

        def work(progress):
            progress("正在保存全部配置到 Flash…")
            client.save_flash()

        def success(_result):
            self.ram_dirty = False
            self.save_blocked = False
            if self.current_port is not None:
                self.port_states[self.current_port] = (False, False)
            self.cooldown_until = time.monotonic() + SAVE_COOLDOWN_SECONDS
            self.status_var.set("全部配置已保存到 Flash，5 秒内禁止重复保存。")
            self._cooldown_tick()

        def failure(error):
            self.cooldown_until = time.monotonic() + SAVE_COOLDOWN_SECONDS
            self._cooldown_tick()
            self._task_error(error, title="保存结果未确认")

        self._start_task(work, success, failure)

    def _load_keys(self, keys) -> None:
        self.loading = True
        try:
            for index, key in enumerate(keys):
                formatted = format_key(key)
                self.current_vars[index].set(formatted)
                self.edit_vars[index].set(formatted)
                state = "未修改" if key in HID_KEY_NAMES else "不支持（保留）"
                self.row_status_vars[index].set(state)
        finally:
            self.loading = False
        self._update_controls()

    def _refresh_current_values(self) -> None:
        for index, key in enumerate(self.device_keys):
            self.current_vars[index].set(format_key(key))
            self._editor_changed(index)

    def _clear_keys(self) -> None:
        self.loading = True
        try:
            for index in range(BUTTON_COUNT):
                self.current_vars[index].set("--")
                self.edit_vars[index].set("")
                self.row_status_vars[index].set("未连接")
        finally:
            self.loading = False

    def _editor_changed(self, index: int) -> None:
        if self.loading or not self.device_keys:
            return
        try:
            key = parse_staged_key(
                self.edit_vars[index].get(), self.device_keys[index]
            )
        except ValueError:
            self.row_status_vars[index].set("格式错误")
        else:
            if key != self.device_keys[index]:
                state = "待写入"
            elif key in HID_KEY_NAMES:
                state = "未修改"
            else:
                state = "不支持（保留）"
            self.row_status_vars[index].set(state)
        self._update_controls()

    def _inspect_edits(self):
        if not self.device_keys:
            return False, False
        changed = False
        valid = True
        for index, variable in enumerate(self.edit_vars):
            try:
                key = parse_staged_key(variable.get(), self.device_keys[index])
            except ValueError:
                valid = False
                changed = changed or (
                    variable.get().strip() != format_key(self.device_keys[index])
                )
                continue
            changed = changed or key != self.device_keys[index]
        return changed, valid

    def _has_staged_changes(self) -> bool:
        changed, _valid = self._inspect_edits()
        return changed or self._has_staged_mode_change()

    def _has_staged_mode_change(self) -> bool:
        target_mode = self.mode_choice_var.get()
        return (
            self.key_mode in KEY_MODE_NAMES
            and target_mode in SELECTABLE_KEY_MODES
            and target_mode != self.key_mode
        )

    def _confirm_leave(self, action: str) -> bool:
        warnings = []
        key_changed, _valid = self._inspect_edits()
        if key_changed:
            warnings.append("界面中有尚未应用到设备的按键修改")
        if self._has_staged_mode_change():
            warnings.append("界面中有尚未应用到设备的键盘模式修改")
        if self.ram_dirty:
            warnings.append("设备 RAM 中有尚未保存到 Flash 的修改")
        if self.save_blocked:
            warnings.append("程序认为设备 RAM 状态不确定")
        if not warnings:
            return True
        details = "；\n".join(warnings)
        return messagebox.askyesno(
            "存在未完成的修改",
            f"{details}。\n\n仍要{action}吗？",
        )

    def _update_mode_text(self) -> None:
        if self.key_mode is None:
            self.mode_var.set("当前模式：旧固件或无法读取")
        else:
            self.mode_var.set(f"当前模式：{KEY_MODE_NAMES[self.key_mode]}")
        self._sync_mode_choice()

    def _sync_mode_choice(self) -> None:
        target = self.key_mode if self.key_mode in SELECTABLE_KEY_MODES else -1
        self.mode_choice_var.set(target)

    def _update_controls(self) -> None:
        connected = self.client is not None
        editable = connected and not self.busy
        key_changed, valid = self._inspect_edits()
        mode_changed = self._has_staged_mode_change()

        self.port_box.configure(
            state="disabled" if connected or self.busy else "normal"
        )
        self.refresh_button.configure(
            state="disabled" if connected or self.busy else "normal"
        )
        self.connect_button.configure(
            text="断开" if connected else "连接",
            state="disabled" if self.busy else "normal",
        )
        self.read_button.configure(state="normal" if editable else "disabled")
        mode_editable = editable and self.key_mode is not None and not key_changed
        for button in self.mode_buttons:
            button.configure(state="normal" if mode_editable else "disabled")
        self.apply_mode_button.configure(
            state="normal" if mode_editable and mode_changed else "disabled"
        )
        for box in self.edit_boxes:
            box.configure(
                state="normal" if editable and not mode_changed else "disabled"
            )
        self.discard_button.configure(
            state=(
                "normal"
                if editable and (key_changed or mode_changed)
                else "disabled"
            )
        )
        self.apply_button.configure(
            state=(
                "normal"
                if editable and key_changed and valid and not mode_changed
                else "disabled"
            )
        )

        cooldown_active = time.monotonic() < self.cooldown_until
        save_enabled = (
            editable
            and self.ram_dirty
            and not self.save_blocked
            and not key_changed
            and not mode_changed
            and valid
            and not cooldown_active
        )
        self.save_button.configure(
            state="normal" if save_enabled else "disabled"
        )

    def _start_task(self, work, success, failure) -> None:
        if self.busy:
            return
        self.busy = True
        self._update_controls()

        def progress(message):
            self.worker_events.put(("progress", message))

        def runner():
            try:
                result = work(progress)
            except Exception as error:
                self.worker_events.put(("failure", failure, error))
            else:
                self.worker_events.put(("success", success, result))

        threading.Thread(target=runner, daemon=True).start()

    def _poll_worker_events(self) -> None:
        try:
            while True:
                event = self.worker_events.get_nowait()
                kind = event[0]
                if kind == "progress":
                    self.status_var.set(event[1])
                    continue

                self.busy = False
                callback, payload = event[1], event[2]
                callback(payload)
                self._update_controls()
        except queue.Empty:
            pass

        if not self.closing:
            self.root.after(50, self._poll_worker_events)

    def _connection_error(self, error) -> None:
        self.status_var.set(f"连接失败：{error}")
        messagebox.showerror("连接失败", str(error))

    def _task_error(self, error, title="操作失败") -> None:
        messagebox.showerror(title, str(error))
        connection_lost = (
            isinstance(error, CommunicationError) and error.fatal
        ) or (
            isinstance(error, ApplyError) and error.connection_lost
        )
        if self.client is not None and (
            connection_lost or not self.client.is_open
        ):
            self.disconnect(ask=False)
            self.status_var.set(f"{title}：{error}；设备连接已关闭。")
        else:
            self.status_var.set(f"{title}：{error}")

    def _cooldown_tick(self) -> None:
        remaining = self.cooldown_until - time.monotonic()
        if remaining <= 0:
            self.cooldown_until = 0.0
            self.cooldown_after_id = None
            self.save_button.configure(text="保存全部配置到 Flash")
            self._update_controls()
            return
        seconds = max(1, int(remaining + 0.999))
        self.save_button.configure(
            text=f"保存全部配置到 Flash（{seconds}s）",
            state="disabled",
        )
        self.cooldown_after_id = self.root.after(100, self._cooldown_tick)

    def close(self) -> None:
        if self.busy:
            messagebox.showinfo("操作进行中", "请等待当前串口操作完成后再关闭程序。")
            return
        if not self._confirm_leave("关闭程序"):
            return

        self.closing = True
        if self.cooldown_after_id is not None:
            self.root.after_cancel(self.cooldown_after_id)
            self.cooldown_after_id = None
        if self.client is not None:
            self.client.close()
        self.root.destroy()


class _FakeClient:
    def __init__(
        self,
        keys,
        mode=KEY_MODE_CUSTOM,
        fail_index=None,
        corrupt_read_index=None,
        mode_read_value="actual",
        fail_save=False,
    ):
        self.keys = list(keys)
        self.mode = mode
        self.fail_index = fail_index
        self.corrupt_read_index = corrupt_read_index
        self.mode_read_value = mode_read_value
        self.fail_save = fail_save
        self.failed = False
        self.set_calls = []
        self.set_mode_calls = []
        self.save_calls = 0
        self.is_open = True

    def set_key(self, index, key):
        self.set_calls.append((index, key))
        if index == self.fail_index and not self.failed:
            self.failed = True
            raise CommunicationError("模拟写入失败")
        self.keys[index] = key
        if index < 8 and self.mode in (KEY_MODE_1P, KEY_MODE_2P):
            self.mode = KEY_MODE_CUSTOM

    def read_all_keys(self, progress=None):
        values = list(self.keys)
        if self.corrupt_read_index is not None and self.set_calls:
            values[self.corrupt_read_index] = 0xEE
        return tuple(values)

    def get_key_mode(self):
        if self.mode_read_value == "actual":
            return self.mode
        return self.mode_read_value

    def set_key_mode(self, mode):
        self.set_mode_calls.append(mode)
        self.mode = mode

    def save_flash(self):
        self.save_calls += 1
        if self.fail_save:
            raise CommunicationError("模拟保存失败")

    def close(self):
        self.is_open = False


class _ProtocolFakeSerial:
    def __init__(self, responder):
        self.responder = responder
        self.received = []
        self.buffer = bytearray()
        self.is_open = True

    @property
    def in_waiting(self):
        return len(self.buffer)

    def reset_input_buffer(self):
        self.buffer.clear()

    def write(self, packet):
        packet = bytes(packet)
        self.received.append(packet)
        self.buffer.extend(self.responder(packet))
        return len(packet)

    def flush(self):
        return None

    def read(self, count):
        size = min(count, 2)
        result = bytes(self.buffer[:size])
        del self.buffer[:size]
        return result

    def close(self):
        self.is_open = False


class _ProtocolDeviceModel:
    PRESET_1P = (0x1A, 0x08, 0x07, 0x06, 0x1B, 0x1D, 0x04, 0x14)
    PRESET_2P = (0x60, 0x61, 0x5E, 0x5B, 0x5A, 0x59, 0x5C, 0x5F)

    def __init__(self, keys, mode=KEY_MODE_CUSTOM):
        self.custom_keys = list(keys)
        self.mode = mode
        self.flash_saves = 0

    def effective_keys(self):
        values = list(self.custom_keys)
        if self.mode == KEY_MODE_1P:
            values[:8] = self.PRESET_1P
        elif self.mode == KEY_MODE_2P:
            values[:8] = self.PRESET_2P
        return values

    def __call__(self, packet):
        if len(packet) != 12 or packet[:8] != MAGIC:
            return bytes((ACK_HEAD, 0x03, 0, 0))
        command, index, key, checksum = packet[8:]
        if checksum != (command + index + key) & 0xFF:
            return bytes((ACK_HEAD, 0x01, 0, 0))

        if command == CMD_SET_KEY:
            if index >= BUTTON_COUNT:
                return bytes((ACK_HEAD, 0x02, index, key))
            if index < 8 and self.mode in (KEY_MODE_1P, KEY_MODE_2P):
                self.custom_keys = self.effective_keys()
                self.mode = KEY_MODE_CUSTOM
            self.custom_keys[index] = key
            return bytes((ACK_HEAD, 0, index, key))
        if command == CMD_GET_KEY:
            if index >= BUTTON_COUNT:
                return bytes((ACK_HEAD, 0x02, index, key))
            return bytes((ACK_HEAD, 0, index, self.effective_keys()[index]))
        if command == CMD_GET_KEY_MODE:
            if index != 0:
                return bytes((ACK_HEAD, 0x02, index, key))
            return bytes((ACK_HEAD, 0, 0, self.mode))
        if command == CMD_SET_KEY_MODE:
            if index != 0 or key not in KEY_MODE_NAMES:
                return bytes((ACK_HEAD, 0x02, index, key))
            self.mode = key
            return bytes((ACK_HEAD, 0, 0, self.mode))
        if command == CMD_SAVE_FLASH:
            self.flash_saves += 1
            return bytes((ACK_HEAD, 0, 0, 0))
        return bytes((ACK_HEAD, 0x03, 0, 0))


def _make_protocol_test_client(fake_serial):
    client = DeviceClient.__new__(DeviceClient)
    client.serial = fake_serial
    client.port = "FAKE"
    client._lock = threading.Lock()
    return client


def self_test() -> None:
    def expect_error(error_type, action):
        try:
            action()
        except error_type as error:
            return error
        raise AssertionError(f"预期抛出 {error_type.__name__}")

    expected_keys = (
        {0x00}
        | set(range(0x04, 0x32))
        | set(range(0x33, 0x64))
        | {0x65}
        | set(range(0x67, 0x6B))
        | {0x75}
        | set(range(0xE0, 0xE8))
    )
    assert set(HID_KEY_NAMES) == expected_keys
    assert len(HID_KEY_NAMES) == 110
    assert set(HID_NAME_TO_KEY) == {
        name.upper() for name in HID_KEY_NAMES.values()
    }
    assert len(HID_NAME_TO_KEY) == 110
    assert len(KEY_CHOICES) == len(set(KEY_CHOICES)) == 110
    for code in sorted(HID_KEY_NAMES):
        assert parse_key(format_key(code)) == code
    assert parse_key("A") == 0x04
    assert parse_key("Alpha3") == 0x20
    assert parse_key("0x20") == 0x20
    assert parse_key("Keypad8") == 0x60
    assert HID_KEY_NAMES[0x46] == "Print"
    assert HID_KEY_NAMES[0x65] == "Menu"
    assert HID_KEY_NAMES[0x68] == "F13"
    assert HID_KEY_NAMES[0x6A] == "F15"
    for rejected in (
        "3",
        "0x03",
        "KP_8",
        "F16",
        "F24 (0x73)",
        "Mouse0",
        "0x32",
        "0x64",
        "0x66",
        "0x74",
        "0x76",
        "0x86",
        "0x9A",
        "0x9C",
    ):
        expect_error(ValueError, lambda value=rejected: parse_key(value))
    expect_error(ValueError, lambda: parse_key("A (0x05)"))
    expect_error(ValueError, lambda: parse_key("garbage (0x04)"))
    expect_error(ValueError, lambda: parse_key("0x100"))
    legacy_text = format_key(0x73)
    assert parse_staged_key(legacy_text, 0x73) == 0x73
    expect_error(ValueError, lambda: parse_key(legacy_text))
    expect_error(ValueError, lambda: parse_staged_key("0x73", 0x73))
    expect_error(ValueError, lambda: parse_staged_key(legacy_text, 0x72))

    def responder(packet):
        assert len(packet) == 12
        assert packet[:8] == MAGIC
        command, index, key, checksum = packet[8:]
        assert checksum == (command + index + key) & 0xFF
        return bytes((0x55, ACK_HEAD, 0, index, key))

    protocol_fake = _ProtocolFakeSerial(responder)
    protocol_client = _make_protocol_test_client(protocol_fake)
    protocol_client.set_key(5, 0x04)
    protocol_client.set_key(5, 0x73)
    assert len(protocol_fake.received) == 2
    assert protocol_fake.received[-1][10] == 0x73

    def wrong_mode_key_responder(packet):
        return bytes((ACK_HEAD, 0, packet[9], (packet[10] + 1) & 0xFF))

    wrong_mode_key_client = _make_protocol_test_client(
        _ProtocolFakeSerial(wrong_mode_key_responder)
    )
    expect_error(
        CommunicationError,
        lambda: wrong_mode_key_client.set_key_mode(KEY_MODE_2P),
    )

    def wrong_index_responder(packet):
        return bytes((ACK_HEAD, 0, (packet[9] + 1) & 0xFF, packet[10]))

    bad_client = _make_protocol_test_client(
        _ProtocolFakeSerial(wrong_index_responder)
    )
    try:
        bad_client.command(CMD_GET_KEY, index=1)
    except CommunicationError:
        pass
    else:
        raise AssertionError("错误 ACK 索引未被拒绝")

    def status_responder(_packet):
        return bytes((ACK_HEAD, 0x01, 0, 0))

    status_client = _make_protocol_test_client(
        _ProtocolFakeSerial(status_responder)
    )
    status_error = expect_error(
        DeviceStatusError,
        lambda: status_client.command(CMD_GET_KEY, index=5),
    )
    assert status_error.status == 0x01

    def unsupported_mode_responder(_packet):
        return bytes((ACK_HEAD, 0x03, 0, 0))

    unsupported_client = _make_protocol_test_client(
        _ProtocolFakeSerial(unsupported_mode_responder)
    )
    assert unsupported_client.get_key_mode() is None
    assert len(unsupported_client.serial.received) == 2

    transient_mode_calls = [0]

    def transient_mode_responder(_packet):
        transient_mode_calls[0] += 1
        if transient_mode_calls[0] == 1:
            return bytes((ACK_HEAD, 0x03, 0, 0))
        return bytes((ACK_HEAD, 0, 0, KEY_MODE_CUSTOM))

    transient_mode_client = _make_protocol_test_client(
        _ProtocolFakeSerial(transient_mode_responder)
    )
    assert transient_mode_client.get_key_mode() == KEY_MODE_CUSTOM
    assert transient_mode_calls[0] == 2

    class ShortWriteSerial(_ProtocolFakeSerial):
        def write(self, packet):
            super().write(packet)
            return len(packet) - 1

    short_write_client = _make_protocol_test_client(
        ShortWriteSerial(responder)
    )
    short_write_error = expect_error(
        CommunicationError,
        lambda: short_write_client.command(CMD_GET_KEY),
    )
    assert short_write_error.fatal

    def wrong_save_responder(_packet):
        return bytes((ACK_HEAD, 0, 0, 1))

    wrong_save_client = _make_protocol_test_client(
        _ProtocolFakeSerial(wrong_save_responder)
    )
    expect_error(CommunicationError, wrong_save_client.save_flash)

    original = tuple(0x04 + index for index in range(BUTTON_COUNT))

    mode_model = _ProtocolDeviceModel(original, mode=KEY_MODE_CUSTOM)
    mode_serial = _ProtocolFakeSerial(mode_model)
    mode_client = _make_protocol_test_client(mode_serial)
    mode_noop = apply_key_mode_change(
        mode_client,
        original,
        KEY_MODE_CUSTOM,
        KEY_MODE_CUSTOM,
    )
    assert mode_noop.keys == original
    assert mode_serial.received == []

    mode_1p = apply_key_mode_change(
        mode_client,
        original,
        KEY_MODE_CUSTOM,
        KEY_MODE_1P,
    )
    expected_1p = _ProtocolDeviceModel.PRESET_1P + original[8:]
    assert mode_1p.keys == expected_1p
    assert mode_1p.key_mode == KEY_MODE_1P
    assert mode_serial.received[0][8:] == bytes(
        (CMD_SET_KEY_MODE, 0, KEY_MODE_1P, CMD_SET_KEY_MODE & 0xFF)
    )

    mode_2p = apply_key_mode_change(
        mode_client,
        mode_1p.keys,
        KEY_MODE_1P,
        KEY_MODE_2P,
    )
    expected_2p = _ProtocolDeviceModel.PRESET_2P + original[8:]
    assert mode_2p.keys == expected_2p
    assert mode_2p.key_mode == KEY_MODE_2P

    mode_custom = apply_key_mode_change(
        mode_client,
        mode_2p.keys,
        KEY_MODE_2P,
        KEY_MODE_CUSTOM,
    )
    assert mode_custom.keys == original
    assert mode_custom.key_mode == KEY_MODE_CUSTOM
    assert tuple(mode_model.custom_keys) == original

    invalid_mode_client = _FakeClient(original)
    expect_error(
        ValueError,
        lambda: apply_key_mode_change(
            invalid_mode_client,
            original,
            KEY_MODE_CUSTOM,
            KEY_MODE_OFF,
        ),
    )
    assert invalid_mode_client.set_mode_calls == []

    unknown_mode_client = _FakeClient(original)
    expect_error(
        ValueError,
        lambda: apply_key_mode_change(
            unknown_mode_client,
            original,
            None,
            KEY_MODE_1P,
        ),
    )
    assert unknown_mode_client.set_mode_calls == []

    class TargetModeReadFailsOnce(_FakeClient):
        def __init__(self, keys):
            super().__init__(keys, mode=KEY_MODE_CUSTOM)
            self.target_read_failed = False

        def get_key_mode(self):
            if self.mode == KEY_MODE_1P and not self.target_read_failed:
                self.target_read_failed = True
                raise CommunicationError("模拟目标模式回读失败")
            return self.mode

    rollback_mode_client = TargetModeReadFailsOnce(original)
    rollback_mode_error = expect_error(
        ApplyError,
        lambda: apply_key_mode_change(
            rollback_mode_client,
            original,
            KEY_MODE_CUSTOM,
            KEY_MODE_1P,
        ),
    )
    assert rollback_mode_error.rollback_complete
    assert rollback_mode_error.observed_mode == KEY_MODE_CUSTOM
    assert rollback_mode_error.observed_keys == original
    assert rollback_mode_client.set_mode_calls == [
        KEY_MODE_1P,
        KEY_MODE_CUSTOM,
    ]

    uncertain_mode_client = _FakeClient(
        original,
        mode=KEY_MODE_CUSTOM,
        mode_read_value=None,
    )
    uncertain_mode_error = expect_error(
        ApplyError,
        lambda: apply_key_mode_change(
            uncertain_mode_client,
            original,
            KEY_MODE_CUSTOM,
            KEY_MODE_2P,
        ),
    )
    assert not uncertain_mode_error.rollback_complete
    assert uncertain_mode_error.observed_mode is None
    assert uncertain_mode_client.set_mode_calls == [
        KEY_MODE_2P,
        KEY_MODE_CUSTOM,
    ]

    class AckLostAfterModeChange(_FakeClient):
        def __init__(self, keys, fatal=False):
            super().__init__(keys, mode=KEY_MODE_CUSTOM)
            self.lose_ack_once = True
            self.fatal = fatal

        def set_key_mode(self, mode):
            self.set_mode_calls.append(mode)
            self.mode = mode
            if self.lose_ack_once:
                self.lose_ack_once = False
                raise CommunicationError("模拟模式 ACK 丢失", fatal=self.fatal)

    ack_lost_client = AckLostAfterModeChange(original)
    ack_lost_error = expect_error(
        ApplyError,
        lambda: apply_key_mode_change(
            ack_lost_client,
            original,
            KEY_MODE_CUSTOM,
            KEY_MODE_1P,
        ),
    )
    assert ack_lost_error.rollback_complete
    assert not ack_lost_error.connection_lost
    assert ack_lost_error.observed_mode == KEY_MODE_CUSTOM
    assert ack_lost_error.observed_keys == original
    assert ack_lost_client.set_mode_calls == [
        KEY_MODE_1P,
        KEY_MODE_CUSTOM,
    ]

    class RollbackAckLost(_FakeClient):
        def __init__(self, keys):
            super().__init__(keys, mode=KEY_MODE_CUSTOM)
            self.target_read_failed = False
            self.rollback_ack_lost = False

        def get_key_mode(self):
            if self.mode == KEY_MODE_1P and not self.target_read_failed:
                self.target_read_failed = True
                raise CommunicationError("模拟目标模式回读失败")
            return self.mode

        def set_key_mode(self, mode):
            self.set_mode_calls.append(mode)
            self.mode = mode
            if mode == KEY_MODE_CUSTOM and not self.rollback_ack_lost:
                self.rollback_ack_lost = True
                raise CommunicationError("模拟回滚 ACK 丢失")

    rollback_ack_lost_client = RollbackAckLost(original)
    rollback_ack_lost_error = expect_error(
        ApplyError,
        lambda: apply_key_mode_change(
            rollback_ack_lost_client,
            original,
            KEY_MODE_CUSTOM,
            KEY_MODE_1P,
        ),
    )
    assert rollback_ack_lost_error.rollback_complete
    assert rollback_ack_lost_error.observed_mode == KEY_MODE_CUSTOM
    assert rollback_ack_lost_error.observed_keys == original

    fatal_ack_lost_client = AckLostAfterModeChange(original, fatal=True)
    fatal_ack_lost_error = expect_error(
        ApplyError,
        lambda: apply_key_mode_change(
            fatal_ack_lost_client,
            original,
            KEY_MODE_CUSTOM,
            KEY_MODE_1P,
        ),
    )
    assert not fatal_ack_lost_error.rollback_complete
    assert fatal_ack_lost_error.connection_lost
    assert fatal_ack_lost_client.set_mode_calls == [KEY_MODE_1P]

    off_mode_client = _FakeClient(original, mode=KEY_MODE_OFF)
    off_mode_result = apply_key_mode_change(
        off_mode_client,
        original,
        KEY_MODE_OFF,
        KEY_MODE_1P,
    )
    assert off_mode_result.key_mode == KEY_MODE_1P
    assert off_mode_client.set_mode_calls == [KEY_MODE_1P]

    preset_main_model = _ProtocolDeviceModel(original, mode=KEY_MODE_1P)
    preset_main_client = _make_protocol_test_client(
        _ProtocolFakeSerial(preset_main_model)
    )
    preset_main_client.set_key(0, 0x15)
    assert preset_main_client.get_key_mode() == KEY_MODE_CUSTOM
    assert preset_main_client.read_all_keys() == (
        (0x15,) + _ProtocolDeviceModel.PRESET_1P[1:] + original[8:]
    )

    preset_aux_model = _ProtocolDeviceModel(original, mode=KEY_MODE_2P)
    preset_aux_client = _make_protocol_test_client(
        _ProtocolFakeSerial(preset_aux_model)
    )
    preset_aux_client.set_key(8, 0x20)
    assert preset_aux_client.get_key_mode() == KEY_MODE_2P
    assert preset_aux_client.read_all_keys() == (
        _ProtocolDeviceModel.PRESET_2P + (0x20,) + original[9:]
    )
    assert tuple(preset_aux_model.custom_keys[:8]) == original[:8]

    target = list(original)
    target[0] = 0x14
    target[12] = 0x28
    client = _FakeClient(original, mode=KEY_MODE_1P)
    result = apply_key_changes(client, original, target, KEY_MODE_1P)
    assert result.keys == tuple(target)
    assert result.changed_indices == (0, 12)
    assert result.key_mode == KEY_MODE_CUSTOM
    assert client.set_calls == [(0, 0x14), (12, 0x28)]

    protocol_model = _ProtocolDeviceModel(original, mode=KEY_MODE_CUSTOM)
    integrated_client = _make_protocol_test_client(
        _ProtocolFakeSerial(protocol_model)
    )
    integrated_target = list(original)
    integrated_target[5] = 0x2C
    integrated_result = apply_key_changes(
        integrated_client,
        integrated_client.read_all_keys(),
        integrated_target,
        KEY_MODE_CUSTOM,
    )
    assert integrated_result.keys == tuple(integrated_target)
    assert protocol_model.flash_saves == 0
    integrated_client.save_flash()
    assert protocol_model.flash_saves == 1

    invalid_target = list(original)
    invalid_target[4] = 999
    expect_error(
        ValueError,
        lambda: apply_key_changes(
            _FakeClient(original), original, invalid_target, KEY_MODE_CUSTOM
        ),
    )

    unsupported_target = list(original)
    unsupported_target[4] = 0x6B
    unsupported_client = _FakeClient(original)
    expect_error(
        ValueError,
        lambda: apply_key_changes(
            unsupported_client,
            original,
            unsupported_target,
            KEY_MODE_CUSTOM,
        ),
    )
    assert unsupported_client.set_calls == []

    legacy_original = list(original)
    legacy_original[4] = 0x73
    legacy_original = tuple(legacy_original)
    legacy_noop_client = _FakeClient(legacy_original)
    legacy_noop = apply_key_changes(
        legacy_noop_client,
        legacy_original,
        legacy_original,
        KEY_MODE_CUSTOM,
    )
    assert legacy_noop.keys == legacy_original
    assert legacy_noop.changed_indices == ()
    assert legacy_noop_client.set_calls == []

    legacy_other_target = list(legacy_original)
    legacy_other_target[8] = 0x16
    legacy_other_client = _FakeClient(legacy_original)
    legacy_other = apply_key_changes(
        legacy_other_client,
        legacy_original,
        legacy_other_target,
        KEY_MODE_CUSTOM,
    )
    assert legacy_other.keys == tuple(legacy_other_target)
    assert legacy_other_client.set_calls == [(8, 0x16)]

    legacy_replace_target = list(legacy_original)
    legacy_replace_target[4] = 0x14
    legacy_replace_client = _FakeClient(legacy_original)
    legacy_replace = apply_key_changes(
        legacy_replace_client,
        legacy_original,
        legacy_replace_target,
        KEY_MODE_CUSTOM,
    )
    assert legacy_replace.keys == tuple(legacy_replace_target)
    assert legacy_replace_client.set_calls == [(4, 0x14)]

    legacy_rollback_target = list(legacy_original)
    legacy_rollback_target[4] = 0x14
    legacy_rollback_target[6] = 0x15
    legacy_rollback_client = _FakeClient(legacy_original, fail_index=6)
    legacy_rollback_error = expect_error(
        ApplyError,
        lambda: apply_key_changes(
            legacy_rollback_client,
            legacy_original,
            legacy_rollback_target,
            KEY_MODE_CUSTOM,
        ),
    )
    assert legacy_rollback_error.rollback_complete
    assert legacy_rollback_error.observed_keys == legacy_original
    assert legacy_rollback_client.set_calls == [
        (4, 0x14),
        (6, 0x15),
        (4, 0x73),
    ]

    first_write_fails = _FakeClient(
        original, mode=KEY_MODE_1P, fail_index=0
    )
    first_target = list(original)
    first_target[0] = 0x14
    first_error = expect_error(
        ApplyError,
        lambda: apply_key_changes(
            first_write_fails,
            original,
            first_target,
            KEY_MODE_1P,
        ),
    )
    assert first_error.rollback_complete
    assert first_write_fails.mode == KEY_MODE_1P
    assert first_write_fails.set_calls == [(0, 0x14)]

    failing = _FakeClient(original, mode=KEY_MODE_1P, fail_index=2)
    broken_target = list(original)
    broken_target[0] = 0x14
    broken_target[2] = 0x15
    try:
        apply_key_changes(failing, original, broken_target, KEY_MODE_1P)
    except ApplyError as error:
        assert not error.rollback_complete
        assert error.observed_keys == original
        assert failing.mode == KEY_MODE_1P
    else:
        raise AssertionError("模拟失败未触发 ApplyError")

    unknown_mode = _FakeClient(original, mode=KEY_MODE_1P, fail_index=2)
    try:
        apply_key_changes(unknown_mode, original, broken_target, None)
    except ApplyError as error:
        assert not error.rollback_complete
        assert error.observed_keys == original
        assert unknown_mode.mode == KEY_MODE_CUSTOM
    else:
        raise AssertionError("未知模式下的模拟失败未触发 ApplyError")

    secondary_failure = _FakeClient(
        original, mode=KEY_MODE_CUSTOM, fail_index=12
    )
    secondary_target = list(original)
    secondary_target[8] = 0x16
    secondary_target[12] = 0x28
    secondary_error = expect_error(
        ApplyError,
        lambda: apply_key_changes(
            secondary_failure,
            original,
            secondary_target,
            KEY_MODE_CUSTOM,
        ),
    )
    assert secondary_error.rollback_complete
    assert secondary_error.observed_mode == KEY_MODE_CUSTOM
    assert secondary_error.observed_keys == original

    corrupt_read = _FakeClient(
        original, mode=KEY_MODE_CUSTOM, corrupt_read_index=6
    )
    corrupt_target = list(original)
    corrupt_target[0] = 0x14
    corrupt_error = expect_error(
        ApplyError,
        lambda: apply_key_changes(
            corrupt_read,
            original,
            corrupt_target,
            KEY_MODE_CUSTOM,
        ),
    )
    assert not corrupt_error.rollback_complete

    missing_mode = _FakeClient(
        original, mode=KEY_MODE_1P, mode_read_value=None
    )
    missing_mode_target = list(original)
    missing_mode_target[0] = 0x14
    missing_mode_error = expect_error(
        ApplyError,
        lambda: apply_key_changes(
            missing_mode,
            original,
            missing_mode_target,
            KEY_MODE_1P,
        ),
    )
    assert not missing_mode_error.rollback_complete

    if sys.stdout is not None:
        print("HID 按键修改器自检通过")


def ui_smoke_test() -> None:
    root = tk.Tk()
    root.withdraw()
    editor = HidKeyEditor(root)

    def wait_for_task():
        deadline = time.monotonic() + 2.0
        while editor.busy and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        assert not editor.busy

    original_askyesno = messagebox.askyesno
    original_task_error = editor._task_error
    assert [str(button.cget("text")) for button in editor.mode_buttons] == [
        "1P",
        "2P",
        "自定义",
    ]
    assert all(button.instate(("disabled",)) for button in editor.mode_buttons)
    assert editor.apply_mode_button.instate(("disabled",))
    assert str(editor.save_button.cget("text")) == "保存全部配置到 Flash"
    assert editor.save_button.instate(("disabled",))

    ui_client = _FakeClient(
        tuple(0x04 + index for index in range(BUTTON_COUNT)),
        mode=KEY_MODE_1P,
    )
    editor.client = ui_client
    editor.device_keys = tuple(0x04 + index for index in range(BUTTON_COUNT))
    editor.key_mode = KEY_MODE_1P
    editor._load_keys(editor.device_keys)
    editor._update_mode_text()
    editor._update_controls()
    assert editor.mode_choice_var.get() == KEY_MODE_1P
    assert all(button.instate(("!disabled",)) for button in editor.mode_buttons)
    assert editor.apply_mode_button.instate(("disabled",))

    staged_text = format_key(0x15)
    editor.edit_vars[0].set(staged_text)
    editor._editor_changed(0)
    assert all(button.instate(("disabled",)) for button in editor.mode_buttons)
    editor.mode_choice_var.set(KEY_MODE_2P)
    messagebox.askyesno = lambda *_args, **_kwargs: False
    editor.apply_mode_change()
    assert ui_client.set_mode_calls == []
    assert editor.edit_vars[0].get() == staged_text
    editor.discard_edits()
    messagebox.askyesno = original_askyesno

    editor.mode_choice_var.set(KEY_MODE_2P)
    editor._mode_choice_changed()
    assert editor.apply_mode_button.instate(("!disabled",))
    assert editor.save_button.instate(("disabled",))
    editor.discard_edits()
    assert editor.mode_choice_var.get() == KEY_MODE_1P

    editor.mode_choice_var.set(KEY_MODE_2P)
    editor._mode_choice_changed()
    editor.apply_mode_change()
    wait_for_task()
    assert ui_client.set_mode_calls == [KEY_MODE_2P]
    assert editor.key_mode == KEY_MODE_2P
    assert editor.ram_dirty
    assert editor.save_button.instate(("!disabled",))

    editor.save_blocked = True
    editor.mode_choice_var.set(KEY_MODE_CUSTOM)
    editor._mode_choice_changed()
    editor.apply_mode_change()
    wait_for_task()
    assert editor.key_mode == KEY_MODE_CUSTOM
    assert editor.save_blocked
    editor._update_controls()
    assert editor.save_button.instate(("disabled",))

    save_calls = ui_client.save_calls
    editor.save_to_flash()
    assert ui_client.save_calls == save_calls
    assert editor.ram_dirty

    editor.save_blocked = False
    editor.current_port = "FAKE"
    messagebox.askyesno = lambda *_args, **_kwargs: False
    editor.save_to_flash()
    assert ui_client.save_calls == save_calls
    assert editor.ram_dirty

    messagebox.askyesno = lambda *_args, **_kwargs: True
    editor.save_to_flash()
    wait_for_task()
    assert ui_client.save_calls == save_calls + 1
    assert not editor.ram_dirty
    assert editor.port_states["FAKE"] == (False, False)
    assert editor.cooldown_until > time.monotonic()
    assert editor.save_button.instate(("disabled",))

    if editor.cooldown_after_id is not None:
        root.after_cancel(editor.cooldown_after_id)
        editor.cooldown_after_id = None
    editor.cooldown_until = 0.0
    editor.ram_dirty = True
    ui_client.fail_save = True
    task_errors = []
    editor._task_error = lambda error, title="操作失败": task_errors.append(
        (title, error)
    )
    editor.save_to_flash()
    wait_for_task()
    assert ui_client.save_calls == save_calls + 2
    assert editor.ram_dirty
    assert task_errors and task_errors[-1][0] == "保存结果未确认"

    if editor.cooldown_after_id is not None:
        root.after_cancel(editor.cooldown_after_id)
        editor.cooldown_after_id = None
    editor.cooldown_until = 0.0
    messagebox.askyesno = original_askyesno
    editor._task_error = original_task_error

    editor.key_mode = None
    editor._update_mode_text()
    editor._update_controls()
    assert all(button.instate(("disabled",)) for button in editor.mode_buttons)
    editor.client = None
    editor.current_port = None
    editor.ram_dirty = False
    editor.save_blocked = False
    root.after(75, root.quit)
    root.mainloop()
    editor.close()


def main() -> int:
    if "--self-test" in sys.argv:
        try:
            self_test()
        except Exception:
            if sys.stderr is not None:
                traceback.print_exc()
            return 1
        return 0
    if "--ui-smoke-test" in sys.argv:
        try:
            ui_smoke_test()
        except Exception:
            if sys.stderr is not None:
                traceback.print_exc()
            return 1
        return 0
    root = tk.Tk()
    HidKeyEditor(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
