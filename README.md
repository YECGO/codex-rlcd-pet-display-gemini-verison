# Codex RLCD Pet Display - Gemini Version

修改成 **Google Gemini API** 账号限额显示。一个运行在 Waveshare/微雪 `ESP32-S3-RLCD-4.2` 黑白全反射屏上的 Gemini 额度桌面小屏。

它通过电脑端 Python 脚本调用 Gemini API 获取配额信息，再用 BLE 同步到 ESP32-S3。主屏显示 Gemini 配额、时间、房间温湿度、电池电量，并带一个会随 Gemini 日配额使用率自动变化的桌宠。

## 特性

- ESP32-S3 + 4.2 inch RLCD 横屏 UI，适配 `300x400` ST7305 黑白屏
- BLE 串口式同步，不依赖 USB 数据线传输
- Gemini 日/月配额使用率显示
- 桌宠 mood 跟随日配额 `used%` 自动变化
  - `<20% used`: energetic
  - `<45% used`: normal
  - `<70% used`: tired
  - `<90% used`: exhausted
  - `>=90% used`: dying
- SHTC3 室温/湿度显示
- 18650 电池电量显示，`BAT_ADC = GPIO4`
- 可选股票页，支持 A 股行情和分时线同步
- Windows 开机自启动桥接脚本

## 硬件

已测试：

- 开发板：`ESP32-S3-RLCD-4.2`
- MCU：`ESP32-S3-WROOM-1-N16R8`
- 屏幕：4.2 inch 反射式液晶屏，`300x400`，ST7305
- 传感器：SHTC3（I2C）
- 电池 ADC：`GPIO4`，分压比 `3.0`

固件使用的主要引脚：

```text
RLCD_DC     GPIO5
RLCD_TE     GPIO6
RLCD_SCK    GPIO11
RLCD_DIN    GPIO12
RLCD_CS     GPIO40
RLCD_RESET  GPIO41
I2C_SDA     GPIO13
I2C_SCL     GPIO14
BAT_ADC     GPIO4
```

## 项目布局

```text
firmware/
  ESP32S3_Codex_BLE_Monitor/
    ESP32S3_Codex_BLE_Monitor.ino

bridge/
  gemini_ble_sender.py
  gemini_ble_autostart_watch.py
  Start-Gemini-BLE-Sender-Visible.bat
  Start-Gemini-BLE-Autostart-Watch.bat
  GEMINI_SETUP.md

previews/
  preview_codex_fluffy_dog_v3.html
  preview_codex_mood_dog_v2.html
  preview_codex_line_dog.html
```

## 设置

### 1. Arduino

安装：

- Arduino IDE 2.x
- ESP32 core `3.3.x`
- `ArduinoJson`
- `ST7305_MonoTFT_Library`

推荐开发板配置：

```text
FQBN:
esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=cdc,FlashSize=16M,PSRAM=opi,PartitionScheme=app3M_fat9M_16MB
```

打开并上传：

```text
firmware/ESP32S3_Codex_BLE_Monitor/ESP32S3_Codex_BLE_Monitor.ino
```

刷写后，开发板以以下名称广播 BLE：

```text
ESP32S3-Codex
```

### 2. Python Bridge

安装 Python 依赖：

```powershell
pip install -r requirements.txt
```

获取 Gemini API Key：

1. 访问 [Google AI Studio](https://aistudio.google.com/app/apikeys)
2. 创建新 API Key
3. 复制 Key

设置 API Key（选择一种方法）：

**方法 A: 环境变量（推荐）**
```powershell
setx GEMINI_API_KEY "your_api_key_here"
```

**方法 B: .env 文件**
在 `bridge/` 目录创建 `.env` 文件：
```
GEMINI_API_KEY=your_api_key_here
```

运行调试模式：

```powershell
bridge\Start-Gemini-BLE-Sender-Visible.bat
```

运行后台监视器：

```powershell
bridge\Start-Gemini-BLE-Autostart-Watch.bat
```

监视器保持 BLE 发送器活跃。在 Windows 上，可以将 `.bat` 放在启动文件夹中，以在登录后开始同步。

## 工作原理

1. `gemini_ble_sender.py` 调用 Google Gemini API 获取配额信息
2. 构建包含配额、时间和可选股票数据的紧凑 JSON 有效负载
3. 通过 BLE 分块将 JSON 发送到 ESP32-S3
4. 固件重绘 RLCD 主页并更新桌宠心情

示例有效负载格式：

```json
{
  "ok": true,
  "status": "running",
  "date": "07-08",
  "time": "11:03:44",
  "primary": {"label": "Daily", "used": 43, "remaining": 57, "reset": "24h"},
  "secondary": {"label": "Month", "used": 32, "remaining": 68, "reset": "30d"}
}
```

## 注意

- 这是个人爱好项目，不是官方 Google 产品
- BLE 链接是本地的，在您的 PC 和 ESP32-S3 之间
- 桥接脚本从 Gemini API 读取配额，不会上传您的提示词
- 股票数据使用公开行情端点，尽力而为

## 许可证

MIT
