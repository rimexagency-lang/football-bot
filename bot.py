import os
import sys
import json
import time
import re
import signal
import requests
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

# ========== НАЛАШТУВАННЯ ==========
load_dotenv()

SPORTMONKS_TOKEN = os.getenv("SPORTMONKS_TOKEN")
DEEPL_TOKEN      = os.getenv("DEEPL_TOKEN")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL")

KYIV_TZ = ZoneInfo("Europe/Kyiv")

LEAGUE_IDS = [
    2, 5,
    8, 9, 24, 27,
    82,
    301,
    384, 387, 390,
    564, 567, 570,
    72, 462, 208, 453, 501, 600, 609,
    181, 244, 271, 444, 486, 573, 591
]

PRIORITY_LEAGUES = [2, 5, 8, 82, 564]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLISHED_FILE = os.path.join(BASE_DIR, "published_ids.json")

FALLBACK_IMAGES = [
    "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1d/Football_Pallo_valmiina-cropped.jpg/320px-Football_Pallo_valmiina-cropped.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/5/5c/Football_iu_002.jpg/320px-Football_iu_002.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e3/FCB-DFB-Pokal-Finale2014.jpg/320px-FCB-DFB-Pokal-Finale2014.jpg",
]
_fallback_index = 0
published_ids = {}


# ========== PUBLISHED IDS ==========

def _load_from_gist():
    gist_id = os.getenv("GIST_ID")
    github_token = os.getenv("GITHUB_TOKEN")
    if not gist_id:
        return {}
    try:
        headers = {"Accept": "application/vnd.github+json"}
        if github_token:
            headers["Authorization"] = f"Bearer {github_token}"
        r = requests.get(
            f"https://api.github.com/gists/{gist_id}",
            headers=headers, timeout=10
        )
        if r.status_code == 200:
            files = r.json().get("files", {})
            content_str = files.get("published_ids.json", {}).get("content", "")
            if content_str:
                data = json.loads(content_str)
                print(f"📦 Завантажено {len(data)} ID з GitHub Gist", flush=True)
                return data
    except Exception as e:
        print(f"⚠️ Gist помилка: {e}", flush=True)
    return {}


def load_published_ids():
    gist_data = _load_from_gist()
    if gist_data:
        try:
            with open(PUBLISHED_FILE, "w", encoding="utf-8") as f:
                json.dump(gist_data, f)
        except Exception:
            pass
        return gist_data

    if os.path.exists(PUBLISHED_FILE):
        try:
            with open(PUBLISHED_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                today = datetime.now().strftime("%Y-%m-%d")
                return {str(i): today for i in data}
            print(f"📦 Завантажено {len(data)} ID з файлу", flush=True)
            return data
        except Exception:
            pass
    return {}


def save_published_ids(published_dict):
    try:
        with open(PUBLISHED_FILE, "w", encoding="utf-8") as f:
            json.dump(published_dict, f)
    except Exception as e:
        print(f"⚠️ Файл: {e}", flush=True)

    gist_id = os.getenv("GIST_ID")
    github_token = os.getenv("GITHUB_TOKEN")
    if gist_id and github_token:
        try:
            r = requests.patch(
                f"https://api.github.com/gists/{gist_id}",
                headers={
                    "Authorization": f"Bearer {github_token}",
                    "Accept": "application/vnd.github+json"
                },
                json={"files": {"published_ids.json": {"content": json.dumps(published_dict)}}},
                timeout=10
            )
            if r.status_code == 200:
                print(f"☁️ Gist збережено ({len(published_dict)} ID)", flush=True)
        except Exception as e:
            print(f"⚠️ Gist помилка: {e}", flush=True)


def cleanup_old_ids(published_dict, days=7):
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    cleaned = {k: v for k, v in published_dict.items() if v >= cutoff}
    removed = len(published_dict) - len(cleaned)
    if removed:
        print(f"🧹 Видалено старих ID: {removed}", flush=True)
    return cleaned


# ========== ДОПОМІЖНІ ФУНКЦІЇ ==========

def format_form(text):
    def replace_form(match):
        form = match.group(1)
        emoji = ""
        for char in form:
            if char == "W": emoji += "🟢"
            elif char == "L": emoji += "🔴"
            elif char == "D": emoji += "🟡"
        return f"({emoji})"
    return re.sub(r'\[([WDLU]+)\]', replace_form, text or "")


def to_kyiv_str(starting_at):
    if not starting_at:
        return ""
    try:
        utc_dt = datetime.strptime(starting_at[:16], "%Y-%m-%d %H:%M")
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
        kyiv_dt = utc_dt.astimezone(KYIV_TZ)
        return kyiv_dt.strftime("%d.%m %H:%M")
    except Exception:
        return starting_at[:16]


# ========== ФОТО ==========

def get_image(fixture):
    global _fallback_index

    participants = fixture.get("participants", [])
    sorted_p = sorted(
        participants,
        key=lambda p: 0 if str((p.get("meta") or {}).get("location", "")).lower() == "home" else 1
    )

    for p in sorted_p:
        img = p.get("image_path") or p.get("logo_path")
        if img and img.startswith("http"):
            print(f"✅ Логотип команди: {img}", flush=True)
            return img

    league = fixture.get("league") or {}
    img = league.get("image_path") or league.get("logo_path")
    if img and img.startswith("http"):
        print(f"✅ Логотип ліги: {img}", flush=True)
        return img

    img = FALLBACK_IMAGES[_fallback_index % len(FALLBACK_IMAGES)]
    _fallback_index += 1
    print(f"⚠️ Fallback: {img}", flush=True)
    return img


# ========== SPORTMONKS NEWS API ==========

def get_all_news():
    """Отримує новини напряму через news endpoints."""
    all_news = []

    for endpoint in ["prematch/upcoming", "prematch", "postmatch"]:
        url = f"https://api.sportmonks.com/v3/football/news/{endpoint}"
        params = {"api_token": SPORTMONKS_TOKEN, "per_page": 50}
        try:
            page = 1
            while True:
                params["page"] = page
                r = requests.get(url, params=params, timeout=20)
                if r.status_code != 200:
                    print(f"❌ {endpoint}: {r.status_code} {r.text[:100]}", flush=True)
                    break
                resp = r.json()
                data = resp.get("data", [])
                all_news.extend(data)
                print(f"  📡 {endpoint} стор.{page}: {len(data)} новин", flush=True)
                if not resp.get("pagination", {}).get("has_more", False):
                    break
                page += 1
                if page > 10:
                    break
        except Exception as e:
            print(f"❌ {endpoint}: {e}", flush=True)

    print(f"📰 Всього новин з API: {len(all_news)}", flush=True)
    return all_news


# ========== ПЕРЕКЛАД ==========

def translate(text):
    if not text or not DEEPL_TOKEN:
        return text or ""
    try:
        r = requests.post(
            "https://api-free.deepl.com/v2/translate",
            headers={"Authorization": f"DeepL-Auth-Key {DEEPL_TOKEN}"},
            json={"text": [text], "target_lang": "UK"},
            timeout=15
        )
        return r.json()["translations"][0]["text"]
    except requests.exceptions.Timeout:
        print("⏱ DeepL timeout", flush=True)
        return text
    except Exception as e:
        print(f"⚠️ Переклад: {e}", flush=True)
        return text


# ========== TELEGRAPH ==========

def get_telegraph_token():
    token_file = os.path.join(BASE_DIR, "telegraph_token.txt")
    if os.path.exists(token_file):
        with open(token_file, "r") as f:
            token = f.read().strip()
        if token:
            return token
    try:
        r = requests.post(
            "https://api.telegra.ph/createAccount",
            json={"short_name": "FootballBot", "author_name": "Football News"},
            timeout=10
        )
        data = r.json()
        if data.get("ok"):
            token = data["result"]["access_token"]
            with open(token_file, "w") as f:
                f.write(token)
            print("✅ Telegraph акаунт створено", flush=True)
            return token
    except Exception as e:
        print(f"⚠️ Telegraph: {e}", flush=True)
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
            clean = re.sub(r'<[^>]+>', '', para.strip())
            if clean:
                nodes.append({"tag": "p", "children": [clean]})
        if not nodes:
            return None
        r = requests.post(
            "https://api.telegra.ph/createPage",
            json={
                "access_token": token,
                "title": (title or "Футбольні новини")[:256],
                "content": nodes,
                "return_content": False
            },
            timeout=10
        )
        data = r.json()
        if data.get("ok"):
            url = data["result"]["url"]
            print(f"✅ Telegraph: {url}", flush=True)
            return url
    except Exception as e:
        print(f"⚠️ Telegraph публікація: {e}", flush=True)
    return None


# ========== TELEGRAM ==========

def send_telegram(text, image_url=None, telegraph_url=None):
    reply_markup = None
    if telegraph_url:
        reply_markup = {"inline_keyboard": [[{"text": "📖 Читати повністю", "url": telegraph_url}]]}

    if image_url and len(text) <= 1024:
        payload = {
            "chat_id": TELEGRAM_CHANNEL,
            "photo": image_url,
            "caption": text,
            "parse_mode": "HTML"
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
            json=payload, timeout=20
        )
        if r.status_code == 200:
            print("📤 Відправлено фото+підпис", flush=True)
            return True

        try:
            img_data = requests.get(image_url, timeout=10).content
            files = {"photo": ("photo.jpg", img_data, "image/jpeg")}
            data = {"chat_id": TELEGRAM_CHANNEL, "caption": text, "parse_mode": "HTML"}
            if reply_markup:
                data["reply_markup"] = json.dumps(reply_markup)
            r2 = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                data=data, files=files, timeout=30
            )
            if r2.status_code == 200:
                print("📤 Відправлено файлом", flush=True)
                return True
        except Exception as e:
            print(f"⚠️ Скачування фото: {e}", flush=True)

    if image_url:
        text = f'<a href="{image_url}">&#8205;</a>' + text
    payload = {
        "chat_id": TELEGRAM_CHANNEL,
        "text": text,
        "parse_mode": "HTML"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json=payload, timeout=15
    )
    if r.status_code == 200:
        print("📤 Відправлено текст", flush=True)
        return True
    print(f"❌ Telegram помилка: {r.text[:200]}", flush=True)
    return False


# ========== ОБРОБКА НОВИНИ ==========

def process_news(news_item):
    """Обробляє одну новину напряму."""
    news_id = news_item.get("id")
    if not news_id or str(news_id) in published_ids:
        return 0

    league_id = news_item.get("league_id")
    if league_id not in LEAGUE_IDS:
        return 0

    fixture_id = news_item.get("fixture_id")
    title = (news_item.get("title") or "").strip()
    news_type_raw = news_item.get("type", "prematch")
    news_type = "post" if "post" in str(news_type_raw).lower() else "pre"

    # Отримуємо деталі fixture
    fixture_name = f"Матч ID:{fixture_id}"
    league_name = ""
    starting_at = ""
    image_url = None

    if fixture_id:
        try:
            r = requests.get(
                f"https://api.sportmonks.com/v3/football/fixtures/{fixture_id}",
                params={"api_token": SPORTMONKS_TOKEN, "include": "participants;league"},
                timeout=10
            )
            if r.status_code == 200:
                f = r.json().get("data", {})
                fixture_name = f.get("name", fixture_name)
                league_name = (f.get("league") or {}).get("name", "")
                starting_at = f.get("starting_at", "")
                image_url = get_image(f)
        except Exception as e:
            print(f"⚠️ Fixture {fixture_id}: {e}", flush=True)

    # Отримуємо текст новини
    lines = news_item.get("lines", [])
    if not lines and fixture_id:
        try:
            r = requests.get(
                f"https://api.sportmonks.com/v3/football/news/{news_id}",
                params={"api_token": SPORTMONKS_TOKEN, "include": "lines"},
                timeout=10
            )
            if r.status_code == 200:
                lines = r.json().get("data", {}).get("lines", [])
        except Exception:
            pass

    if not title and not lines:
        return 0

    title_ua = format_form(translate(title)) if title else ""
    lines_ua = [
        format_form(translate(line.get("text", "")))
        for line in lines if line.get("text", "").strip()
    ]

    full_text = "\n\n".join(lines_ua)
    preview = ""
    if full_text:
        clean = re.sub(r'<[^>]+>', '', full_text)
        preview = (clean[:300].rsplit(" ", 1)[0] + "…") if len(clean) > 300 else clean

    type_label = "📊 Підсумок матчу" if news_type == "post" else "🔮 Прев'ю матчу"
    date_str = to_kyiv_str(starting_at)

    post = f"📌 <b>{fixture_name}</b>\n"
    if league_name:
        post += f"⚽ {league_name} • 🗓 {date_str} за Києвом\n"
    post += f"{type_label}\n"
    if title_ua:
        post += f"\n<b>{title_ua}</b>\n"
    if preview:
        post += f"\n{preview}"

    telegraph_url = None
    if full_text:
        telegraph_url = publish_to_telegraph(
            title=title_ua or fixture_name,
            text=full_text,
            image_url=image_url
        )

    published_ids[str(news_id)] = datetime.now().strftime("%Y-%m-%d")
    save_published_ids(published_ids)

    send_telegram(post, image_url=image_url, telegraph_url=telegraph_url)
    print(f"✅ [{news_type}] {title_ua or title or fixture_name}", flush=True)

    time.sleep(3)
    return 1


# ========== ГОЛОВНА ФУНКЦІЯ ==========

def run_all():
    global published_ids
    print(f"\n{'='*40}", flush=True)
    print(f"Запуск: {datetime.now().strftime('%Y-%m-%d %H:%M')}", flush=True)

    if not published_ids:
        published_ids = cleanup_old_ids(load_published_ids())
        print(f"📦 Активних published_ids: {len(published_ids)}", flush=True)

    fresh = _load_from_gist()
    if fresh:
        published_ids.update(fresh)
        print(f"🔄 published_ids оновлено: {len(published_ids)}", flush=True)

    all_news = get_all_news()

    # Сортуємо — пріоритетні ліги першими
    all_news.sort(key=lambda n: (
        PRIORITY_LEAGUES.index(n.get("league_id")) if n.get("league_id") in PRIORITY_LEAGUES
        else len(PRIORITY_LEAGUES)
    ))

    total = sum(process_news(n) for n in all_news)
    print(f"Готово. Опубліковано: {total}.", flush=True)


# ========== ЗАПУСК ==========

def sleep_until_next_hour():
    now = datetime.now()
    next_run = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    seconds = (next_run - now).total_seconds()
    print(f"⏰ Наступна перевірка: {next_run.strftime('%Y-%m-%d %H:%M')} UTC", flush=True)
    while seconds > 0:
        time.sleep(min(30, seconds))
        seconds = (next_run - datetime.now()).total_seconds()


def handle_signal(signum, frame):
    print(f"\n🛑 Сигнал {signum}, зупинка.", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    print("Бот запущено! Перевірка щогодини 24/7", flush=True)

    while True:
        try:
            run_all()
        except SystemExit:
            break
        except Exception as e:
            print(f"⚠️ Помилка run_all: {e}", flush=True)

        try:
            sleep_until_next_hour()
        except SystemExit:
            break
        except Exception as e:
            print(f"⚠️ Помилка очікування: {e}", flush=True)
            time.sleep(60)
