# Gemini BLE Sender - Configuration Guide

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Get Gemini API Key

1. Go to [Google AI Studio](https://aistudio.google.com/app/apikeys)
2. Create a new API key
3. Copy the key

### 3. Configure API Key

Choose one of these methods:

**Option A: Environment Variable (Recommended)**
```bash
# Windows - set permanently
setx GEMINI_API_KEY "your_api_key_here"

# Or temporarily in current session
set GEMINI_API_KEY=your_api_key_here
```

**Option B: .env File in Current Directory**
Create a `.env` file in the `bridge/` directory:
```
GEMINI_API_KEY=your_api_key_here
```

**Option C: .gemini Directory**
Create `~/.gemini/.env`:
```
GEMINI_API_KEY=your_api_key_here
```

### 4. Run the Sender

**Debug Mode (see output):**
```bash
bridge\Start-Gemini-BLE-Sender-Visible.bat
```

**Background Mode (autostart on Windows):**
```bash
bridge\Start-Gemini-BLE-Autostart-Watch.bat
```

Or add the .bat to your Startup folder:
- Windows Key + R
- `shell:startup`
- Paste `Start-Gemini-BLE-Autostart-Watch.bat` shortcut

## What It Does

- Fetches Gemini API quota (daily and monthly limits)
- Connects to ESP32-S3 via BLE
- Sends quota data every 10 seconds
- Displays stock data if configured
- Logs all activity to `gemini_ble_sender.log`

## Quota Display

- **Daily**: Primary window shows daily request limit usage
- **Monthly**: Secondary window shows monthly request limit usage
- Pet mood changes based on daily usage percentage

## Troubleshooting

**"GEMINI_API_KEY not found"**
- Set the environment variable or create .env file
- Restart the application

**"Could not find BLE device"**
- Ensure ESP32-S3 is powered on and advertising
- Check device name is "ESP32S3-Codex"

**"Failed to fetch Gemini quota"**
- Check API key is valid
- Check internet connection
- Check rate limits haven't been exceeded

## Logs

Check these files for diagnostics:
- `bridge/gemini_ble_sender.log` - Main sender logs
- `bridge/gemini_ble_autostart_watch.log` - Autostart watcher logs
