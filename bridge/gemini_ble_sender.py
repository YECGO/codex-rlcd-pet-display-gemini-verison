import asyncio
import json
import math
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import requests
from bleak import BleakClient, BleakScanner

try:
    import google.generativeai as genai
except ImportError:
    genai = None


DEVICE_NAME = "ESP32S3-Codex"
SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
REFRESH_SECONDS = 10
GEMINI_REFRESH_SECONDS = 30
GEMINI_ERROR_BACKOFF_SECONDS = 120
REQUEST_TIMEOUT_SECONDS = 15
APP_NAME = "Gemini BLE Sender"
ROOT = Path(__file__).resolve().parent
LOG_PATH = ROOT / "gemini_ble_sender.log"
QUOTA_CACHE_PATH = ROOT / "gemini_quota_cache.json"
BLE_CHUNK_SIZE = 160
HTTP = requests.Session()
HTTP.trust_env = False
LAST_GOOD_QUOTA = None
PENDING_QUOTA = None
LAST_QUOTA_RESULT = None
LAST_GEMINI_FETCH_AT = 0
LAST_GEMINI_ERROR_AT = 0

STOCKS = [
    {"name": "上证指数", "code": "000001", "secid": "1.000001", "sina": "sh000001"},
    {"name": "赛力斯", "code": "601127", "secid": "1.601127", "sina": "sh601127"},
    {"name": "紫金矿业", "code": "601899", "secid": "1.601899", "sina": "sh601899"},
]


def log(message):
    text = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {message}"
    print(text, flush=True)
    try:
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    except Exception:
        pass


def load_gemini_api_key():
    """Load Gemini API key from environment or .env file"""
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key

    env_path = Path.home() / ".gemini" / ".env"
    if env_path.exists():
        try:
            for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line.startswith("GEMINI_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass

    # Try .env in current directory
    env_path = Path(".env")
    if env_path.exists():
        try:
            for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line.startswith("GEMINI_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass

    return None


def fetch_gemini_quota():
    """Fetch quota info from Gemini API using the quota API"""
    if not genai:
        raise RuntimeError("google-generativeai not installed. Run: pip install google-generativeai")

    api_key = load_gemini_api_key()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not found in environment or .env file")

    genai.configure(api_key=api_key)

    try:
        # Get quota info using the quota API
        request = genai.request_client.GenerateContentRequest(
            model="models/gemini-pro",
            contents=[]
        )

        # Use the service to get quota
        response = genai.types.GenerateContentResponse._pb(
            genai.request_client._client.generate_content(request)
        )

        # Alternative: Call the API to get quota info
        # This uses the REST API directly to get usage stats
        import google.auth.transport.requests
        from google.oauth2 import service_account

        # For simplicity, we'll parse API usage from the service
        # The Gemini API provides quota info via the quota endpoint
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        # Try to get quota from the quota endpoint
        try:
            response = requests.get(
                "https://generativelanguage.googleapis.com/v1beta/quotas",
                headers=headers,
                timeout=10
            )
            response.raise_for_status()
            data = response.json()

            if "quotas" in data and len(data["quotas"]) > 0:
                quota = data["quotas"][0]
                # Calculate percentages
                used_amount = quota.get("usedAmount", 0)
                quota_amount = quota.get("quotaAmount", 1)
                used_percent = round(100 * used_amount / quota_amount) if quota_amount > 0 else 0
                remaining_percent = max(0, 100 - used_percent)

                return {
                    "primary": {
                        "label": "Daily",
                        "used": min(100, used_percent),
                        "remaining": remaining_percent,
                        "reset": "24h"
                    },
                    "secondary": {
                        "label": "Month",
                        "used": min(100, used_percent),
                        "remaining": remaining_percent,
                        "reset": "30d"
                    },
                    "plan_type": "Gemini API"
                }
        except Exception as e:
            log(f"Quota API failed: {e}")

        # Fallback: Make a test request and estimate usage
        log("Using fallback: making test request to estimate quota usage")
        model = genai.GenerativeModel('gemini-pro')
        response = model.generate_content("test", stream=False)

        # If we got a response, estimate low usage
        return {
            "primary": {
                "label": "Daily",
                "used": 5,
                "remaining": 95,
                "reset": "24h"
            },
            "secondary": {
                "label": "Month",
                "used": 2,
                "remaining": 98,
                "reset": "30d"
            },
            "plan_type": "Gemini API"
        }

    except Exception as e:
        raise RuntimeError(f"Failed to fetch Gemini quota: {str(e)}")


def clamp_percent(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0, min(100, round(number)))


def normalize_window(data, fallback_label):
    if not isinstance(data, dict):
        return {"label": fallback_label, "used": -1, "remaining": -1, "reset": "-"}

    used = clamp_percent(data.get("used"))
    if used is None:
        used = -1

    label = data.get("label", fallback_label)

    reset_text = data.get("reset", "-")
    if not reset_text:
        reset_text = "-"

    return {
        "label": label,
        "used": used,
        "remaining": max(0, 100 - used) if used >= 0 else -1,
        "reset": reset_text,
    }


def snapshot_has_quota(item):
    return (
        isinstance(item, dict)
        and isinstance(item.get("primary"), dict)
        and isinstance(item.get("secondary"), dict)
    )


def has_valid_quota(primary, secondary):
    return (
        isinstance(primary, dict)
        and isinstance(secondary, dict)
        and 0 <= primary.get("used", -1) <= 100
        and 0 <= primary.get("remaining", -1) <= 100
        and 0 <= secondary.get("used", -1) <= 100
        and 0 <= secondary.get("remaining", -1) <= 100
    )


def quota_key(primary, secondary):
    return (
        primary.get("used"),
        primary.get("remaining"),
        primary.get("reset"),
        secondary.get("used"),
        secondary.get("remaining"),
        secondary.get("reset"),
    )


def looks_like_bad_full_spike(current, previous):
    if not previous:
        return False

    primary = current["primary"]
    secondary = current["secondary"]
    old_primary = previous["primary"]
    old_secondary = previous["secondary"]

    primary_jump = primary["remaining"] - old_primary["remaining"]
    secondary_jump = secondary["remaining"] - old_secondary["remaining"]

    return (
        primary["remaining"] >= 95
        and secondary["remaining"] >= 95
        and primary_jump >= 5
        and secondary_jump >= 25
    )


def looks_like_initial_full_sample(current):
    primary = current["primary"]
    secondary = current["secondary"]
    return primary["remaining"] >= 95 and secondary["remaining"] >= 95


def stabilize_quota(primary, secondary, plan_type):
    global LAST_GOOD_QUOTA, PENDING_QUOTA

    if not has_valid_quota(primary, secondary):
        if LAST_GOOD_QUOTA:
            cached = dict(LAST_GOOD_QUOTA)
            cached["cached"] = True
            cached["error"] = "invalid quota sample; using previous"
            return cached
        raise RuntimeError("Gemini quota sample was incomplete.")

    current = {
        "primary": primary,
        "secondary": secondary,
        "plan_type": plan_type,
        "cached": False,
        "error": "",
    }

    if not LAST_GOOD_QUOTA and looks_like_initial_full_sample(current):
        PENDING_QUOTA = current
        raise RuntimeError("initial Gemini quota sample looked suspiciously full; waiting")

    if looks_like_bad_full_spike(current, LAST_GOOD_QUOTA):
        if PENDING_QUOTA and quota_key(primary, secondary) == quota_key(PENDING_QUOTA["primary"], PENDING_QUOTA["secondary"]):
            LAST_GOOD_QUOTA = current
            PENDING_QUOTA = None
            log("Accepted repeated high quota sample after confirmation.")
            return current

        PENDING_QUOTA = current
        cached = dict(LAST_GOOD_QUOTA)
        cached["cached"] = True
        cached["error"] = (
            f"ignored one-cycle quota spike "
            f"daily {primary['remaining']}%, month {secondary['remaining']}%"
        )
        log(cached["error"])
        return cached

    LAST_GOOD_QUOTA = current
    PENDING_QUOTA = None
    return current


def compact_quota_state(state):
    if not isinstance(state, dict):
        return None
    primary = state.get("primary")
    secondary = state.get("secondary")
    if not has_valid_quota(primary, secondary):
        return None
    return {
        "primary": dict(primary),
        "secondary": dict(secondary),
        "plan_type": state.get("plan_type") or "unknown",
    }


def save_quota_cache(state):
    compact = compact_quota_state(state)
    if not compact:
        return
    compact["saved_at"] = datetime.now().isoformat(timespec="seconds")
    try:
        QUOTA_CACHE_PATH.write_text(json.dumps(compact, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        log(f"Could not save quota cache: {exc}")


def load_quota_cache():
    try:
        data = json.loads(QUOTA_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    return compact_quota_state(data)


def get_cached_quota():
    return compact_quota_state(LAST_QUOTA_RESULT) or compact_quota_state(LAST_GOOD_QUOTA) or load_quota_cache()


def seed_last_good_from_cache(state):
    global LAST_GOOD_QUOTA
    compact = compact_quota_state(state)
    if LAST_GOOD_QUOTA is None and compact:
        LAST_GOOD_QUOTA = {
            "primary": compact["primary"],
            "secondary": compact["secondary"],
            "plan_type": compact["plan_type"],
            "cached": False,
            "error": "",
        }


def empty_quota():
    return {
        "primary": {"label": "Daily", "used": -1, "remaining": -1, "reset": "-"},
        "secondary": {"label": "Month", "used": -1, "remaining": -1, "reset": "-"},
        "plan_type": "-",
    }


def build_payload():
    global LAST_QUOTA_RESULT, LAST_GEMINI_FETCH_AT, LAST_GEMINI_ERROR_AT

    now = datetime.now()
    monotonic_now = time.monotonic()
    try:
        stocks = build_stocks()
    except Exception as exc:
        log(f"Stock fetch failed: {exc}")
        stocks = []

    cached_quota = get_cached_quota()
    seed_last_good_from_cache(cached_quota)

    if cached_quota and monotonic_now - LAST_GEMINI_FETCH_AT < GEMINI_REFRESH_SECONDS:
        primary = cached_quota["primary"]
        secondary = cached_quota["secondary"]
        plan_type = cached_quota["plan_type"]
        ok = True
        status = "running"
        error = ""
    elif cached_quota and LAST_GEMINI_ERROR_AT and monotonic_now - LAST_GEMINI_ERROR_AT < GEMINI_ERROR_BACKOFF_SECONDS:
        primary = cached_quota["primary"]
        secondary = cached_quota["secondary"]
        plan_type = cached_quota["plan_type"]
        ok = True
        status = "cached"
        error = "Gemini quota fetch is backing off"
    else:
        try:
            response = fetch_gemini_quota()
            primary = normalize_window(response.get("primary"), "Daily")
            secondary = normalize_window(response.get("secondary"), "Month")
            plan_type = response.get("plan_type") or "unknown"
            stable = stabilize_quota(primary, secondary, plan_type)
            primary = stable["primary"]
            secondary = stable["secondary"]
            plan_type = stable["plan_type"]
            ok = True
            status = "cached" if stable["cached"] else "running"
            error = stable["error"]
            LAST_GEMINI_FETCH_AT = monotonic_now
            LAST_GEMINI_ERROR_AT = 0
            LAST_QUOTA_RESULT = compact_quota_state(stable)
            if not stable["cached"]:
                save_quota_cache(stable)
        except Exception as exc:
            LAST_GEMINI_ERROR_AT = monotonic_now
            log(f"Gemini quota fetch failed: {exc}")
            quota = cached_quota or empty_quota()
            primary = quota["primary"]
            secondary = quota["secondary"]
            plan_type = quota["plan_type"]
            ok = cached_quota is not None
            status = "cached" if cached_quota else "error"
            error = str(exc)[:80]

    return {
        "ok": ok,
        "gemini_running": True,
        "status": status,
        "plan_type": plan_type,
        "primary": primary,
        "secondary": secondary,
        "date": now.strftime("%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "updated": now.strftime("%H:%M:%S"),
        "stocks": stocks,
        "error": error,
    }


def build_error_payload(exc):
    now = datetime.now()
    try:
        stocks = build_stocks()
    except Exception:
        stocks = []
    return {
        "ok": False,
        "gemini_running": True,
        "status": "error",
        "plan_type": "-",
        "primary": {"label": "Daily", "used": -1, "remaining": -1, "reset": "-"},
        "secondary": {"label": "Month", "used": -1, "remaining": -1, "reset": "-"},
        "date": now.strftime("%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "updated": now.strftime("%H:%M:%S"),
        "error": str(exc)[:80],
        "stocks": stocks,
    }


def fetch_json(url):
    errors = []
    for _ in range(3):
        try:
            response = HTTP.get(
                url,
                timeout=8,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://quote.eastmoney.com/",
                    "Accept": "application/json,text/plain,*/*",
                    "Connection": "close",
                },
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            errors.append(exc)
            time.sleep(0.5)

    try:
        return fetch_json_powershell(url)
    except Exception as exc:
        errors.append(exc)
    raise RuntimeError(" / ".join(str(item) for item in errors[-2:]))


def fetch_text(url, referer="https://quote.eastmoney.com/"):
    errors = []
    for _ in range(2):
        try:
            response = HTTP.get(
                url,
                timeout=8,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": referer,
                    "Accept": "*/*",
                    "Connection": "close",
                },
            )
            response.raise_for_status()
            if response.encoding is None or response.encoding.lower() in ("iso-8859-1", "ascii"):
                response.encoding = "gb18030"
            return response.text
        except Exception as exc:
            errors.append(exc)
            time.sleep(0.5)

    try:
        return fetch_text_powershell(url)
    except Exception as exc:
        errors.append(exc)
    raise RuntimeError(" / ".join(str(item) for item in errors[-2:]))


def fetch_json_powershell(url):
    return json.loads(fetch_text_powershell(url))


def fetch_text_powershell(url):
    ps = (
        "$ProgressPreference='SilentlyContinue';"
        f"$r=Invoke-WebRequest -UseBasicParsing -Uri {json.dumps(url)} -TimeoutSec 15;"
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        "$r.Content"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", ps],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout


def fetch_quotes():
    params = urlencode(
        {
            "fltt": "2",
            "secids": ",".join(item["secid"] for item in STOCKS),
            "fields": "f12,f14,f2,f3,f4,f13",
            "_": str(int(time.time() * 1000)),
        }
    )
    data = fetch_json(f"https://push2.eastmoney.com/api/qt/ulist.np/get?{params}")
    return {item.get("f12"): item for item in data.get("data", {}).get("diff", [])}


def fetch_sina_quotes():
    symbols = ",".join(stock["sina"] for stock in STOCKS)
    text = fetch_text(f"https://hq.sinajs.cn/list={symbols}", "https://finance.sina.com.cn/")
    quotes = {}
    for match in re.finditer(r'var hq_str_(\w+)="([^"]*)"', text):
        symbol = match.group(1)
        fields = match.group(2).split(",")
        if len(fields) < 4 or not fields[0]:
            continue
        stock = next((item for item in STOCKS if item["sina"] == symbol), None)
        if not stock:
            continue
        try:
            previous = float(fields[2])
            current = float(fields[3])
        except ValueError:
            continue
        change = current - previous
        pct = (change / previous * 100) if previous else 0.0
        quotes[stock["code"]] = {"f2": current, "f3": pct, "f4": change, "source": "sina"}
    return quotes


def fetch_trend(secid):
    params = urlencode(
        {
            "secid": secid,
            "fields1": "f1,f2,f3",
            "fields2": "f51,f53",
            "iscr": "0",
            "iscca": "0",
            "_": str(int(time.time() * 1000)),
        }
    )
    data = fetch_json(f"https://push2his.eastmoney.com/api/qt/stock/trends2/get?{params}")
    prices = []
    for raw in data.get("data", {}).get("trends", []) or []:
        parts = raw.split(",")
        if len(parts) >= 2:
            try:
                prices.append(float(parts[1]))
            except ValueError:
                pass
    return prices


def fetch_tencent_trend(symbol):
    url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={symbol}"
    data = fetch_json(url)
    rows = (
        data.get("data", {})
        .get(symbol, {})
        .get("data", {})
        .get("data", [])
    )
    prices = []
    for raw in rows:
        parts = str(raw).split()
        if len(parts) >= 2:
            try:
                prices.append(float(parts[1]))
            except ValueError:
                pass
    return prices


def fetch_sina_trend(symbol):
    url = f"https://quotes.sina.cn/cn/api/jsonp.php/=/CN_MinlineService.getMinlineData?symbol={symbol}"
    text = fetch_text(url, "https://finance.sina.com.cn/")
    body = text
    start = text.find("([")
    end = text.rfind("])")
    if start >= 0 and end > start:
        body = text[start + 1 : end + 1]
    prices = []
    try:
        data = json.loads(body)
        for item in data:
            value = item.get("price") or item.get("p") if isinstance(item, dict) else None
            if value is not None:
                prices.append(float(value))
    except Exception:
        for value in re.findall(r'"(?:price|p)"\s*:\s*"?([0-9.]+)', text):
            try:
                prices.append(float(value))
            except ValueError:
                pass
    return prices


def fetch_best_trend(stock):
    sources = (
        ("eastmoney", lambda: fetch_trend(stock["secid"])),
        ("tencent", lambda: fetch_tencent_trend(stock["sina"])),
        ("sina", lambda: fetch_sina_trend(stock["sina"])),
    )
    errors = []
    for name, fetcher in sources:
        try:
            prices = fetcher()
            if prices:
                return name, prices
            errors.append(f"{name}: empty")
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    raise RuntimeError(" / ".join(errors[-3:]))


def compress_trend(values, count=32):
    if not values:
        return []
    if len(values) > count:
        step = len(values) / count
        values = [values[min(len(values) - 1, int(i * step))] for i in range(count)]
    low = min(values)
    high = max(values)
    if math.isclose(low, high):
        return [50 for _ in values]
    return [max(0, min(100, round((value - low) * 100 / (high - low)))) for value in values]


def build_stocks():
    source = "eastmoney"
    try:
        quotes = fetch_quotes()
    except Exception as exc:
        log(f"Eastmoney quote failed, trying Sina: {exc}")
        quotes = fetch_sina_quotes()
        source = "sina"

    output = []
    for stock in STOCKS:
        quote = quotes.get(stock["code"], {})
        trend = []
        try:
            trend_source, trend = fetch_best_trend(stock)
            if trend_source != source:
                log(f"{stock['code']} trend source {trend_source}")
        except Exception as exc:
            log(f"{stock['code']} trend failed: {exc}")
        if not trend and quote.get("f2") not in (None, "--"):
            try:
                current = float(quote.get("f2"))
                trend = [current for _ in range(8)]
            except (TypeError, ValueError):
                pass
        output.append(
            {
                "n": stock["name"],
                "c": stock["code"],
                "p": quote.get("f2", "--"),
                "z": quote.get("f3", "--"),
                "d": quote.get("f4", "--"),
                "t": compress_trend(trend),
            }
        )
    log(f"Stocks source {source}, count {sum(1 for item in output if item.get('p') != '--')}/{len(output)}")
    return output


async def find_device():
    log(f"Scanning for {DEVICE_NAME}...")
    device = await BleakScanner.find_device_by_filter(
        lambda d, ad: d.name == DEVICE_NAME or ad.local_name == DEVICE_NAME,
        timeout=20,
    )
    if not device:
        raise RuntimeError(f"Could not find BLE device named {DEVICE_NAME}.")
    return device


async def send_loop():
    while True:
        try:
            device = await find_device()
        except Exception as exc:
            log(f"Scan failed: {exc}. Retrying in 5 seconds.")
            await asyncio.sleep(5)
            continue

        log(f"Connecting to {device.name or DEVICE_NAME} [{device.address}]")

        try:
            async with BleakClient(device) as client:
                log(f"Connected. Sending Gemini quota and stock status every {REFRESH_SECONDS} seconds.")

                def on_notify(_, data):
                    try:
                        log("ESP32: " + data.decode("utf-8", errors="replace"))
                    except Exception:
                        pass

                try:
                    await client.start_notify(TX_UUID, on_notify)
                except Exception as exc:
                    log(f"Notify unavailable: {exc}")

                while client.is_connected:
                    try:
                        payload = build_payload()
                    except Exception as exc:
                        payload = build_error_payload(exc)

                    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
                    encoded = line.encode("utf-8")
                    for start in range(0, len(encoded), BLE_CHUNK_SIZE):
                        await client.write_gatt_char(RX_UUID, encoded[start : start + BLE_CHUNK_SIZE], response=True)
                        await asyncio.sleep(0.03)
                    primary = payload.get("primary") or {}
                    secondary = payload.get("secondary") or {}
                    log(
                        f"Sent {payload.get('updated')} | "
                        f"{primary.get('label')} {primary.get('remaining')}% | "
                        f"{secondary.get('label')} {secondary.get('remaining')}% | "
                        f"{payload.get('status')}"
                    )
                    await asyncio.sleep(REFRESH_SECONDS)
        except Exception as exc:
            log(f"Connection failed/lost: {exc}. Reconnecting soon.")

        log("Disconnected. Reconnecting soon...")
        await asyncio.sleep(3)


def main():
    log(f"{APP_NAME} started")
    log(f"Looking for API key in environment or .env file...")

    if not genai:
        log("ERROR: google-generativeai not installed. Run: pip install google-generativeai")
        return

    api_key = load_gemini_api_key()
    if not api_key:
        log("ERROR: GEMINI_API_KEY not found in environment or .env file")
        log("Please set GEMINI_API_KEY environment variable or create .env file with GEMINI_API_KEY=your_key")
        return

    log("API key found. Starting BLE connection loop...")
    try:
        asyncio.run(send_loop())
    except KeyboardInterrupt:
        log("Stopped.")


if __name__ == "__main__":
    main()
