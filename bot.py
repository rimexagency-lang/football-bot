import os
import json
import time
import re
import html
import sys
import signal
import atexit
import mimetypes
from urllib.parse import urlparse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv


# ========== ВЕРСИЯ ==========
VERSION = "bot-hourly-v5"


# ========== НАСТРОЙКИ ==========
load_dotenv()

SPORTMONKS_TOKEN = os.getenv("SPORTMONKS_TOKEN")
DEEPL_TOKEN = os.getenv("DEEPL_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL")

GIST_ID = os.getenv("GIST_ID")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

UTC = timezone.utc
KYIV_TZ = ZoneInfo("Europe/Kyiv")

LEAGUE_IDS = [
    2, 5,
    8, 9, 24, 27,
    82,
    301,
    384, 387, 390,
    564, 567, 570,
    72,
    462,
    208,
    453,
    501,
    600,
    609,
    181, 244, 271,
    444, 486, 573, 591
]

PRIORITY_LEAGUES = [2, 5, 8, 82, 564]

# 0 = публиковать все новые новости за проход
MAX_POSTS_PER_RUN = 0
TELEGRAM_PAUSE_SECONDS = 5
PUBLISHED_IDS_KEEP_DAYS = 7
SPORTMONKS_TIMEOUT = 20
DEFAULT_TIMEOUT = 15

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLISHED_FILE = os.path.join(BASE_DIR, "published_ids.json")
TELEGRAPH_TOKEN_FILE = os.path.join(BASE_DIR, "telegraph_token.txt")

FALLBACK_IMAGES = [
    "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1d/Football_Pallo_valmiina-cropped.jpg/640px-Football_Pallo_valmiina-cropped.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/5/5c/Football_iu_002.jpg/640px-Football_iu_002.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/2/21/Soccer_ball.svg/512px-Soccer_ball.svg.png",
]

_fallback_index = 0
_used_images = set()
published_ids = {}


# ========== ЛОГИ ==========
def now_utc():
    return datetime.now(UTC)


def log(message: str) -> None:
    ts_utc = now_utc().strftime("%Y-%m-%d %H:%M:%S")
    ts_kyiv = now_utc().astimezone(KYIV_TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts_utc} UTC | {ts_kyiv} Kyiv] {message}", flush=True)


def on_exit():
    log("🛑 Процес завершується (atexit)")


def handle_signal(signum, frame):
    log(f"🛑 Отримано сигнал завершення: {signum}")
    sys.exit(0)


atexit.register(on_exit)
signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)


# ========== ХЕЛПЕРЫ ==========
def html_escape(text):
    return html.escape(text or "", quote=False)


def html_escape_attr(text):
    return html.escape(text or "", quote=True)


def strip_html(text):
    return re.sub(r"<[^>]+>", "", text or "").strip()


def safe_truncate(text, max_len=300):
    clean = strip_html(text)
    if len(clean) <= max_len:
        return clean

    short = clean[:max_len].rstrip()
    if " " in short:
        short = short.rsplit(" ", 1)[0]
    return short.rstrip(" .,;:-") + "…"


def parse_sportmonks_dt(value):
    if not value:
        return None

    raw = str(value).strip()
    if not raw:
        return None

    candidates = [raw]

    if raw.endswith("Z"):
        candidates.append(raw[:-1] + "+00:00")

    if " " in raw and "T" not in raw:
        candidates.append(raw.replace(" ", "T"))

    if raw.endswith("Z") and " " in raw:
        candidates.append(raw.replace(" ", "T")[:-1] + "+00:00")

    for candidate in candidates:
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        except ValueError:
            pass

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw[:19], fmt).replace(tzinfo=UTC)
        except ValueError:
            pass

    return None


def to_kyiv_str(dt):
    if not dt:
        return ""
    return dt.astimezone(KYIV_TZ).strftime("%d.%m %H:%M")


def get_list_relation(obj, *keys):
    for key in keys:
        value = obj.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict) and isinstance(value.get("data"), list):
            return value["data"]
    return []


def get_first_value(obj, *keys):
    for key in keys:
        value = obj.get(key)
        if value:
            return value
    return None


def is_supported_image_url(url):
    if not url or not isinstance(url, str):
        return False
    if not url.startswith(("http://", "https://")):
        return False

    path = urlparse(url).path.lower()
    return path.endswith((".jpg", ".jpeg", ".png", ".webp"))


def format_form(text):
    def replace_form(match):
        form = match.group(1)
        emoji = ""
        for char in form:
            if char == "W":
                emoji += "🟢"
            elif char == "L":
                emoji += "🔴"
            elif char == "D":
                emoji += "🟡"
            else:
                emoji += char
        return f"({emoji})"

    return re.sub(r"\[([WDLU]+)\]", replace_form, text or "")


# ========== PUBLISHED IDS ==========
def normalize_published_ids(data):
    today = now_utc().strftime("%Y-%m-%d")

    if isinstance(data, list):
        return {str(i): today for i in data}

    if isinstance(data, dict):
        normalized = {}
        for key, value in data.items():
            if key is None:
                continue
            if isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", value):
                normalized[str(key)] = value
            else:
                normalized[str(key)] = today
        return normalized

    return {}


def load_published_ids_from_gist():
    if not GIST_ID:
        return {}

    try:
        headers = {"Accept": "application/vnd.github+json"}
        if GITHUB_TOKEN:
            headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

        response = requests.get(
            f"https://api.github.com/gists/{GIST_ID}",
            headers=headers,
            timeout=10
        )
        response.raise_for_status()

        files = response.json().get("files", {})
        content_str = files.get("published_ids.json", {}).get("content", "")
        if not content_str:
            return {}

        data = json.loads(content_str)
        result = normalize_published_ids(data)
        log(f"📦 Завантажено {len(result)} ID з GitHub Gist")
        return result
    except Exception as e:
        log(f"⚠️ Gist помилка завантаження: {e}")
        return {}


def load_published_ids():
    if os.path.exists(PUBLISHED_FILE):
        try:
            with open(PUBLISHED_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            result = normalize_published_ids(data)
            log(f"📦 Завантажено {len(result)} ID з файлу")
            return result
        except Exception as e:
            log(f"⚠️ Помилка читання published_ids.json: {e}")

    return load_published_ids_from_gist()


def save_published_ids_to_gist(published_dict):
    if not GIST_ID or not GITHUB_TOKEN:
        return

    try:
        response = requests.patch(
            f"https://api.github.com/gists/{GIST_ID}",
            headers={
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                "Accept": "application/vnd.github+json"
            },
            json={
                "files": {
                    "published_ids.json": {
                        "content": json.dumps(published_dict, ensure_ascii=False, indent=2)
                    }
                }
            },
            timeout=10
        )
        response.raise_for_status()
        log(f"☁️ Backup у GitHub Gist ({len(published_dict)} ID)")
    except Exception as e:
        log(f"⚠️ Gist помилка збереження: {e}")


def save_published_ids(published_dict):
    try:
        with open(PUBLISHED_FILE, "w", encoding="utf-8") as f:
            json.dump(published_dict, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"⚠️ Помилка запису файлу: {e}")

    save_published_ids_to_gist(published_dict)


def cleanup_old_ids(published_dict, days=PUBLISHED_IDS_KEEP_DAYS):
    cutoff_date = (now_utc() - timedelta(days=days)).date()
    cleaned = {}

    for key, value in published_dict.items():
        try:
            item_date = datetime.strptime(value, "%Y-%m-%d").date()
            if item_date >= cutoff_date:
                cleaned[key] = value
        except Exception:
            continue

    removed = len(published_dict) - len(cleaned)
    if removed:
        log(f"🧹 Видалено старих ID: {removed}")

    return cleaned


# ========== ФОТО ==========
def _candidate_image_from_participant(participant):
    return get_first_value(
        participant,
        "image_path", "logo_path", "imagePath", "logoPath"
    )


def _candidate_image_from_league(league):
    return get_first_value(
        league,
        "image_path", "logo_path", "imagePath", "logoPath"
    )


def get_image_from_fixture(fixture):
    participants = get_list_relation(fixture, "participants")
    sorted_participants = sorted(
        participants,
        key=lambda p: 0 if str(get_first_value((p.get("meta") or {}), "location") or "").lower() == "home" else 1
    )

    candidates = []

    for participant in sorted_participants:
        img = _candidate_image_from_participant(participant)
        if img and is_supported_image_url(img):
            candidates.append(img)

    league = fixture.get("league", {}) or {}
    league_img = _candidate_image_from_league(league)
    if league_img and is_supported_image_url(league_img):
        candidates.append(league_img)

    for img in candidates:
        if img not in _used_images:
            _used_images.add(img)
            log(f"✅ SportMonks image: {img}")
            return img

    for img in candidates:
        log(f"✅ SportMonks image (повтор): {img}")
        return img

    return None


def get_fallback_image():
    global _fallback_index
    img = FALLBACK_IMAGES[_fallback_index % len(FALLBACK_IMAGES)]
    _fallback_index += 1
    log(f"⚠️ Fallback фото: {img}")
    return img


def get_image(fixture):
    img = get_image_from_fixture(fixture)
    if img:
        return img
    return get_fallback_image()


# ========== SPORTMONKS ==========
def get_fixtures_for_news_scan():
    start_date = (now_utc() - timedelta(days=1)).strftime("%Y-%m-%d")
    end_date = (now_utc() + timedelta(days=3)).strftime("%Y-%m-%d")

    url = f"https://api.sportmonks.com/v3/football/fixtures/between/{start_date}/{end_date}"
    params = {
        "api_token": SPORTMONKS_TOKEN,
        "include": "prematchNews.lines;postmatchNews.lines;participants;league",
        "per_page": 100
    }

    try:
        all_fixtures = []
        page = 1

        while True:
            params["page"] = page
            response = requests.get(url, params=params, timeout=SPORTMONKS_TIMEOUT)

            if response.status_code != 200:
                log(f"❌ Помилка SportMonks API: {response.status_code} | {response.text[:300]}")
                break

            payload = response.json()
            batch = payload.get("data", [])
            all_fixtures.extend(batch)

            has_more = payload.get("pagination", {}).get("has_more", False)
            if not has_more:
                break

            page += 1
            if page > 20:
                log("⚠️ Досягнуто ліміт пагінації (20 сторінок)")
                break

        filtered = [f for f in all_fixtures if f.get("league_id") in LEAGUE_IDS]
        log(f"⚽ Матчів після фільтра ліг: {len(filtered)} / {len(all_fixtures)}")
        return filtered

    except Exception as e:
        log(f"❌ Помилка отримання матчів: {e}")
        return []


# ========== ПЕРЕВОД ==========
def translate(text):
    if not text:
        return ""

    if not DEEPL_TOKEN:
        return text

    try:
        response = requests.post(
            "https://api-free.deepl.com/v2/translate",
            headers={"Authorization": f"DeepL-Auth-Key {DEEPL_TOKEN}"},
            data={"text": text, "target_lang": "UK"},
            timeout=15
        )
        response.raise_for_status()
        data = response.json()
        return data["translations"][0]["text"]
    except requests.exceptions.Timeout:
        log("⏱ DeepL timeout")
        return text
    except Exception as e:
        log(f"⚠️ Помилка перекладу: {e}")
        return text


# ========== TELEGRAPH ==========
def get_telegraph_token():
    if os.path.exists(TELEGRAPH_TOKEN_FILE):
        try:
            with open(TELEGRAPH_TOKEN_FILE, "r", encoding="utf-8") as f:
                token = f.read().strip()
            if token:
                return token
        except Exception as e:
            log(f"⚠️ Помилка читання telegraph_token.txt: {e}")

    try:
        response = requests.post(
            "https://api.telegra.ph/createAccount",
            data={
                "short_name": "FootballBot",
                "author_name": "Football News"
            },
            timeout=10
        )
        response.raise_for_status()
        data = response.json()

        if data.get("ok"):
            token = data["result"]["access_token"]
            with open(TELEGRAPH_TOKEN_FILE, "w", encoding="utf-8") as f:
                f.write(token)
            log("✅ Telegraph акаунт створено")
            return token

        log(f"⚠️ Telegraph createAccount error: {data}")
        return None
    except Exception as e:
        log(f"⚠️ Telegraph помилка: {e}")
        return None


def publish_to_telegraph(title, text, image_url=None):
    try:
        token = get_telegraph_token()
        if not token:
            return None

        nodes = []

        if image_url:
            nodes.append({"tag": "img", "attrs": {"src": image_url}})

        for para in text.split("\n\n"):
            clean = strip_html(para)
            if clean:
                nodes.append({"tag": "p", "children": [clean]})

        if not nodes:
            return None

        response = requests.post(
            "https://api.telegra.ph/createPage",
            data={
                "access_token": token,
                "title": (title or "Футбольні новини")[:256],
                "content": json.dumps(nodes, ensure_ascii=False),
                "return_content": "false"
            },
            timeout=10
        )
        response.raise_for_status()
        data = response.json()

        if data.get("ok"):
            url = data["result"]["url"]
            log(f"✅ Telegraph: {url}")
            return url

        log(f"⚠️ Telegraph createPage error: {data}")
        return None
    except Exception as e:
        log(f"⚠️ Telegraph помилка публікації: {e}")
        return None


# ========== TELEGRAM ==========
def telegram_api(method, *, json_payload=None, data=None, files=None, timeout=DEFAULT_TIMEOUT):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    return requests.post(url, json=json_payload, data=data, files=files, timeout=timeout)


def build_reply_markup(telegraph_url):
    if not telegraph_url:
        return None
    return {
        "inline_keyboard": [[{
            "text": "📖 Читати повністю",
            "url": telegraph_url
        }]]
    }


def _send_as_text(text, image_url=None, telegraph_url=None):
    message_text = text
    if image_url:
        message_text = f'<a href="{html_escape_attr(image_url)}">&#8205;</a>' + message_text

    payload = {
        "chat_id": TELEGRAM_CHANNEL,
        "text": message_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }

    reply_markup = build_reply_markup(telegraph_url)
    if reply_markup:
        payload["reply_markup"] = reply_markup

    response = telegram_api("sendMessage", json_payload=payload)

    if response.status_code != 200:
        log(f"❌ sendMessage помилка: {response.text[:500]}")
        return False

    log("📤 Відправлено як текст")
    return True


def _download_image_for_upload(image_url):
    response = requests.get(image_url, timeout=15)
    response.raise_for_status()

    path = urlparse(image_url).path.lower()
    ext = os.path.splitext(path)[1]
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        ext = ".jpg"

    filename = f"photo{ext}"
    mime = mimetypes.guess_type(filename)[0] or "image/jpeg"
    return filename, mime, response.content


def post_to_telegram(text, image_url=None, telegraph_url=None):
    try:
        caption_limit = 1024
        reply_markup = build_reply_markup(telegraph_url)

        if image_url and len(text) <= caption_limit:
            payload = {
                "chat_id": TELEGRAM_CHANNEL,
                "photo": image_url,
                "caption": text,
                "parse_mode": "HTML"
            }
            if reply_markup:
                payload["reply_markup"] = reply_markup

            response = telegram_api("sendPhoto", json_payload=payload, timeout=20)
            if response.status_code == 200:
                log("📤 Відправлено як фото+підпис")
                return True

            log(f"⚠️ sendPhoto URL помилка: {response.text[:300]}")
            log("⚠️ Пробуємо скачати фото і відправити файлом...")

            try:
                filename, mime, content = _download_image_for_upload(image_url)
                files = {"photo": (filename, content, mime)}
                data = {
                    "chat_id": TELEGRAM_CHANNEL,
                    "caption": text,
                    "parse_mode": "HTML"
                }
                if reply_markup:
                    data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)

                response2 = telegram_api("sendPhoto", data=data, files=files, timeout=30)
                if response2.status_code == 200:
                    log("📤 Відправлено як завантажений файл")
                    return True

                log(f"❌ sendPhoto(file) помилка: {response2.text[:300]}")
            except Exception as e:
                log(f"⚠️ Помилка скачування/відправки фото: {e}")

            return _send_as_text(text, None, telegraph_url)

        if image_url and len(text) > caption_limit:
            payload = {"chat_id": TELEGRAM_CHANNEL, "photo": image_url}
            response = telegram_api("sendPhoto", json_payload=payload, timeout=20)

            if response.status_code != 200:
                log(f"⚠️ sendPhoto без підпису помилка: {response.text[:300]}")
            else:
                log("📤 Фото відправлено окремо")

            return _send_as_text(text, None, telegraph_url)

        return _send_as_text(text, image_url, telegraph_url)

    except Exception as e:
        log(f"❌ Помилка відправки в Telegram: {e}")
        return False


# ========== ЛОГИКА НОВОСТЕЙ ==========
def classify_news_for_fixture(fixture):
    now = now_utc()
    match_dt = parse_sportmonks_dt(fixture.get("starting_at"))

    prematch_list = get_list_relation(fixture, "prematchnews", "prematchNews")
    postmatch_list = get_list_relation(fixture, "postmatchnews", "postmatchNews")

    news_items = []

    if prematch_list:
        if match_dt is None or match_dt > now + timedelta(hours=2):
            news_items.extend([("pre", item) for item in prematch_list])

    if postmatch_list and match_dt is not None:
        if now - timedelta(hours=24) < match_dt < now - timedelta(hours=2):
            news_items.extend([("post", item) for item in postmatch_list])

    return news_items


def build_post_text(fixture_name, league_name, match_dt, news_type, title_ua, preview):
    type_label = "📊 Підсумок матчу" if news_type == "post" else "🔮 Прев'ю матчу"
    kyiv_time_str = to_kyiv_str(match_dt)

    parts = [
        f"📌 <b>{html_escape(fixture_name)}</b>",
        f"⚽ {html_escape(league_name)} • 🗓 {html_escape(kyiv_time_str)} за Києвом",
        type_label
    ]

    if title_ua:
        parts.append(f"<b>{html_escape(title_ua)}</b>")

    if preview:
        parts.append(html_escape(preview))

    return "\n\n".join(part for part in parts if part)


def process_fixture(fixture, remaining_slots=None):
    fixture_name = fixture.get("name", "Невідомий матч")
    league_name = (fixture.get("league", {}) or {}).get("name", "")
    match_dt = parse_sportmonks_dt(fixture.get("starting_at"))

    news_items = classify_news_for_fixture(fixture)

    if not news_items:
        log(f"Немає релевантних новин для: {fixture_name}")
        return 0

    sent_count = 0

    for news_type, news in news_items:
        if remaining_slots is not None and remaining_slots > 0 and sent_count >= remaining_slots:
            break

        news_id = news.get("id")
        if not news_id:
            continue

        news_id_str = str(news_id)
        if news_id_str in published_ids:
            log(f"⏭ Уже публікувалось: {fixture_name} | news_id={news_id_str}")
            continue

        title = (news.get("title") or "").strip()
        lines = get_list_relation(news, "lines")

        if not title and not lines:
            continue

        title_ua = format_form(translate(title)) if title else ""
        lines_ua = []

        for line in lines:
            line_text = (line.get("text") or "").strip()
            if not line_text:
                continue
            lines_ua.append(format_form(translate(line_text)))

        full_text = "\n\n".join(lines_ua).strip()
        preview = safe_truncate(full_text, max_len=300) if full_text else ""

        post_text = build_post_text(
            fixture_name=fixture_name,
            league_name=league_name,
            match_dt=match_dt,
            news_type=news_type,
            title_ua=title_ua,
            preview=preview
        )

        image_url = get_image(fixture)
        log(f"🖼 Фото: {image_url}")

        telegraph_url = None
        if full_text:
            telegraph_url = publish_to_telegraph(
                title=title_ua or fixture_name,
                text=full_text,
                image_url=image_url
            )

        sent_ok = post_to_telegram(
            text=post_text,
            image_url=image_url,
            telegraph_url=telegraph_url
        )
        if not sent_ok:
            continue

        published_ids[news_id_str] = now_utc().strftime("%Y-%m-%d")
        save_published_ids(published_ids)

        log(f"✅ Опубліковано [{news_type}]: {title_ua or fixture_name}")

        sent_count += 1
        time.sleep(TELEGRAM_PAUSE_SECONDS)

    return sent_count


# ========== ОСНОВНОЙ ПРОХОД ==========
def run_all():
    global _used_images
    _used_images = set()

    if not SPORTMONKS_TOKEN or not TELEGRAM_TOKEN or not TELEGRAM_CHANNEL:
        log("❌ Не вистачає обов'язкових змінних: SPORTMONKS_TOKEN / TELEGRAM_TOKEN / TELEGRAM_CHANNEL")
        return

    log("=" * 50)
    log(f"Запуск перевірки новин | VERSION={VERSION}")

    fixtures = get_fixtures_for_news_scan()
    if not fixtures:
        log("Немає матчів для перевірки.")
        return

    def fixture_sort_key(fixture):
        league_id = fixture.get("league_id", 9999)
        match_dt = parse_sportmonks_dt(fixture.get("starting_at")) or now_utc()
        priority = PRIORITY_LEAGUES.index(league_id) if league_id in PRIORITY_LEAGUES else len(PRIORITY_LEAGUES)
        return (priority, match_dt, fixture.get("name", ""))

    fixtures.sort(key=fixture_sort_key)

    total_sent = 0
    remaining = None if MAX_POSTS_PER_RUN == 0 else MAX_POSTS_PER_RUN

    for fixture in fixtures:
        if remaining is not None and remaining <= 0:
            break

        sent_now = process_fixture(fixture, remaining_slots=remaining)
        total_sent += sent_now

        if remaining is not None:
            remaining -= sent_now

    log(f"Готово. Нових новин опубліковано: {total_sent}.")


# ========== ОЖИДАНИЕ ДО СЛЕДУЮЩЕГО ЧАСА ==========
def sleep_until_next_hour():
    now = now_utc()
    next_run = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

    log(
        "⏰ Наступна перевірка: "
        f"{next_run.strftime('%Y-%m-%d %H:%M:%S')} UTC | "
        f"{next_run.astimezone(KYIV_TZ).strftime('%Y-%m-%d %H:%M:%S')} за Києвом"
    )

    while True:
        remaining = (next_run - now_utc()).total_seconds()
        if remaining <= 0:
            log("⏳ Час наступної перевірки настав")
            break

        sleep_for = min(300, remaining)
        log(f"💤 Очікування {int(sleep_for)} сек. до наступної перевірки")
        time.sleep(sleep_for)


# ========== ИНИЦИАЛИЗАЦИЯ ==========
def initialize_state():
    global published_ids

    loaded = load_published_ids()
    cleaned = cleanup_old_ids(loaded)

    if len(cleaned) != len(loaded):
        save_published_ids(cleaned)

    published_ids = cleaned
    log(f"📦 Активних published_ids: {len(published_ids)}")


# ========== ЗАПУСК ==========
if __name__ == "__main__":
    initialize_state()
    log(f"Бот запущено! VERSION={VERSION}")

    while True:
        try:
            run_all()
        except KeyboardInterrupt:
            log("Зупинка бота.")
            break
        except Exception as e:
            log(f"⚠️ Помилка під час run_all(): {e}")

        try:
            sleep_until_next_hour()
        except KeyboardInterrupt:
            log("Зупинка бота.")
            break
        except Exception as e:
            log(f"⚠️ Помилка в циклі очікування: {e}")
            time.sleep(30)
