# HID 按键修改器

这是一个独立的 13 键 HID 键位编辑程序，不修改灯光、IO4 等其他配置项。

## 启动

直接运行：

```text
dist\hid_key_editor.exe
```

从源码运行：

```powershell
.\.venv\Scripts\python.exe .\hid_key_editor.py
```

## 使用流程

1. 选择控制器串口并点击“连接”。
2. 如需切换键盘模式，选择 `1P`、`2P` 或 `自定义`，然后点击“应用模式到 RAM”。程序会回读模式和全部 13 键。
3. 在“新的 HID 键值”列选择 AquaMai 键名，或输入白名单内的十六进制 HID 值。
4. 点击“应用按键到 RAM”。程序会仅写入发生变化的按键，并重新读取 13 键进行校验。
5. 确认结果后点击“保存全部配置到 Flash”，使配置在设备断电后继续保留。

可选项仅保留 [AquaMai `KeyCodeID.cs`](https://github.com/MuNET-OSS/AquaMai/blob/main/AquaMai.Config/Types/KeyCodeID.cs) 中能由单个 USB 键盘 HID usage 表达的 110 项。名称与 AquaMai 一致，例如主键区数字 3 使用 `Alpha3`，小键盘 8 使用 `Keypad8`，回车使用 `Return`；也可输入相应的白名单十六进制值，例如 `0x20`。裸数字、`0x03`、`F16`、`Mouse0` 等不在该白名单内的输入会被拒绝。

如果设备原本存有白名单外的旧键值，程序会标记为“不支持（保留）”：它可以原样保留，以便修改其他按键，但不能新建这种值。将该项改为白名单内的键后即可正常写入。

## 注意事项

- “应用模式到 RAM”和“应用按键到 RAM”都不会写 Flash；未保存时，设备重新上电会恢复 Flash 中的配置。
- 1P、2P 是固件内置的固定 BTN1～BTN8 预设，模式选项用于切换当前生效映射，不能改写这两套固件预设；自定义模式使用可编辑键表。
- 单纯在 1P、2P 和自定义模式之间切换不会覆盖隐藏的自定义键表。若在 1P/2P 模式下直接修改 BTN1～BTN8，固件才会先把当前预设复制为新的自定义键表、切换到自定义模式，再修改所选键；要保留原自定义主键，应先切换到自定义模式。
- “保存全部配置到 Flash”是固件提供的整套配置保存命令，会同时保存设备 RAM 中现有的键盘模式、自定义键位、IO4 和灯光配置；本程序本身不会修改 IO4 或灯光配置。
- 如果批量写入失败且程序提示设备状态不确定，请勿保存 Flash。让设备重新上电后再重新连接。
- 索引 12 在 IO4 模式下用作 COIN。
- `Exclaim`、`At` 等移位符号需要组合键，`Mouse0`～`Mouse6` 需要鼠标 HID 报告，因此不作为本程序的一字节键盘键值提供。

## 自检与打包

```powershell
.\.venv\Scripts\python.exe .\hid_key_editor.py --self-test
.\.venv\Scripts\python.exe .\hid_key_editor.py --ui-smoke-test
.\.venv\Scripts\pyinstaller.exe --noconfirm .\hid_key_editor.spec
```
