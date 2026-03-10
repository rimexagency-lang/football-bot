import os
import json
import time
import re
import html
import mimetypes
from urllib.parse import urlparse
from datetime import datetime, timedelta, timezone

import requests
import schedule
from dotenv import load_dotenv


# ========== НАЛАШТУВАННЯ ==========
load_dotenv()

SPORTMONKS_TOKEN = os.getenv("SPORTMONKS_TOKEN")
DEEPL_TOKEN = os.getenv("DEEPL_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL")

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
MAX_POSTS_PER_RUN = 3
TELEGRAM_PAUSE_SECONDS = 5
PUBLISHED_IDS_KEEP_DAYS = 7
RUN_HOURS_UTC = set(range(6, 22))  # 06:00-21:59 UTC

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLISHED_FILE = os.path.join(BASE_DIR, "published_ids.json")
TELEGRAPH_TOKEN_FILE = os.path.join(BASE_DIR, "telegraph_token.txt")

# Google/Wikimedia пошук вимкнено навмисно:
# він часто дає старі або нерелевантні фото.
# Тепер бот бере лише:
# 1) логотипи команд із SportMonks
# 2) логотип ліги з SportMonks
# 3) нейтральний fallback
FALLBACK_IMAGES = [
    "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1d/Football_Pallo_valmiina-cropped.jpg/640px-Football_Pallo_valmiina-cropped.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/5/5c/Football_iu_002.jpg/640px-Football_iu_002.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/2/21/Soccer_ball.svg/512px-Soccer_ball.svg.png",
]

_fallback_index = 0
_used_images = set()


# ========== БАЗОВІ ДОПОМІЖНІ ФУНКЦІЇ ==========

def now_utc():
    return datetime.now(timezone.utc)


def parse_sportmonks_dt(value):
    """
    Повертає timezone-aware datetime у UTC.
    Підтримує:
    - 2026-03-10 20:00:00
    - 2026-03-10 20:00
    - 2026-03-10T20:00:00Z
    - 2026-03-10T20:00:00+00:00
    """
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
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(raw[:19], fmt).replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass

    return None


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


def _load_from_gist():
    gist_id = os.getenv("GIST_ID")
    github_token = os.getenv("GITHUB_TOKEN")

    if not gist_id:
        return {}

    try:
        headers = {"Accept": "application/vnd.github+json"}
        if github_token:
            headers["Authorization"] = f"Bearer {github_token}"

        response = requests.get(
            f"https://api.github.com/gists/{gist_id}",
            headers=headers,
            timeout=10
        )
        response.raise_for_status()

        files = response.json().get("files", {})
        content_str = files.get("published_ids.json", {}).get("content", "")
        if content_str:
            data = json.loads(content_str)
            normalized = normalize_published_ids(data)
            print(f"📦 Завантажено {len(normalized)} ID з GitHub Gist")
            return normalized
    except Exception as e:
        print(f"⚠️ Gist помилка завантаження: {e}")

    return {}


def load_published_ids():
    if os.path.exists(PUBLISHED_FILE):
        try:
            with open(PUBLISHED_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            normalized = normalize_published_ids(data)
            print(f"📦 Завантажено {len(normalized)} ID з файлу")
            return normalized
        except Exception as e:
            print(f"⚠️ Помилка читання published_ids.json: {e}")

    return _load_from_gist()


def save_published_ids(published_dict):
    try:
        with open(PUBLISHED_FILE, "w", encoding="utf-8") as f:
            json.dump(published_dict, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Помилка запису файлу: {e}")

    gist_id = os.getenv("GIST_ID")
    github_token = os.getenv("GITHUB_TOKEN")

    if gist_id and github_token:
        try:
            response = requests.patch(
                f"https://api.github.com/gists/{gist_id}",
                headers={
                    "Authorization": f"Bearer {github_token}",
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
            print(f"☁️ Backup у GitHub Gist ({len(published_dict)} ID)")
        except Exception as e:
            print(f"⚠️ Gist помилка збереження: {e}")


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
        print(f"🧹 Видалено старих ID: {removed}")

    return cleaned


_loaded_published_ids = load_published_ids()
published_ids = cleanup_old_ids(_loaded_published_ids)
if len(published_ids) != len(_loaded_published_ids):
    save_published_ids(published_ids)

print(f"📦 Активних published_ids: {len(published_ids)}")


# ========== ФОТО ==========

def _candidate_image_from_participant(participant):
    return get_first_value(participant, "image_path", "logo_path", "imagePath", "logoPath")


def _candidate_image_from_league(league):
    return get_first_value(league, "image_path", "logo_path", "imagePath", "logoPath")


def get_image_from_fixture(fixture):
    """
    Лише актуальні та безпечні джерела:
    - логотип home/away команди з SportMonks
    - логотип ліги
    """
    participants = get_list_relation(fixture, "participants")
    sorted_participants = sorted(
        participants,
        key=lambda p: 0 if str(get_first_value(p.get("meta", {}), "location") or "").lower() == "home" else 1
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
            print(f"✅ SportMonks image: {img}")
            return img

    for img in candidates:
        print(f"✅ SportMonks image (повтор): {img}")
        return img

    return None


def get_fallback_image():
    global _fallback_index
    img = FALLBACK_IMAGES[_fallback_index % len(FALLBACK_IMAGES)]
    _fallback_index += 1
    print(f"⚠️ Fallback фото: {img}")
    return img


def get_image(fixture):
    img = get_image_from_fixture(fixture)
    if img:
        return img
    return get_fallback_image()


# ========== SPORTMONKS ==========

def get_todays_fixtures():
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
            response = requests.get(url, params=params, timeout=20)
            if response.status_code != 200:
                print(f"❌ Помилка SportMonks API: {response.status_code} | {response.text[:300]}")
                break

            payload = response.json()
            batch = payload.get("data", [])
            all_fixtures.extend(batch)

            if not payload.get("pagination", {}).get("has_more", False):
                break

            page += 1
            if page > 20:
                print("⚠️ Досягнуто ліміт пагінації (20 сторінок)")
                break

        filtered = [f for f in all_fixtures if f.get("league_id") in LEAGUE_IDS]
        print(f"[{now_utc().strftime('%H:%M')}] Матчів після фільтра ліг: {len(filtered)} / {len(all_fixtures)}")
        return filtered

    except Exception as e:
        print(f"❌ Помилка отримання матчів: {e}")
        return []


# ========== ПЕРЕКЛАД ==========

def translate(text):
    if not text:
        return ""

    if not DEEPL_TOKEN:
        return text

    try:
        response = requests.post(
            "https://api-free.deepl.com/v2/translate",
            headers={"Authorization": f"DeepL-Auth-Key {DEEPL_TOKEN}"},
            data={
                "text": text,
                "target_lang": "UK"
            },
            timeout=15
        )
        response.raise_for_status()
        data = response.json()
        return data["translations"][0]["text"]
    except requests.exceptions.Timeout:
        print("⏱ DeepL timeout")
        return text
    except Exception as e:
        print(f"⚠️ Помилка перекладу: {e}")
        return text


# ========== TELEGRAPH ==========

def _get_telegraph_token():
    if os.path.exists(TELEGRAPH_TOKEN_FILE):
        try:
            with open(TELEGRAPH_TOKEN_FILE, "r", encoding="utf-8") as f:
                token = f.read().strip()
            if token:
                return token
        except Exception as e:
            print(f"⚠️ Помилка читання telegraph_token.txt: {e}")

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
            print("✅ Telegraph акаунт створено")
            return token

        print(f"⚠️ Telegraph createAccount error: {data}")
    except Exception as e:
        print(f"⚠️ Telegraph помилка: {e}")

    return None


def publish_to_telegraph(title, text, image_url=None):
    try:
        token = _get_telegraph_token()
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
            print(f"✅ Telegraph: {url}")
            return url

        print(f"⚠️ Telegraph createPage error: {data}")
    except Exception as e:
        print(f"⚠️ Telegraph помилка публікації: {e}")

    return None


# ========== TELEGRAM ==========

def telegram_api(method, *, json_payload=None, data=None, files=None, timeout=15):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    response = requests.post(
        url,
        json=json_payload,
        data=data,
        files=files,
        timeout=timeout
    )
    return response


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

    response = telegram_api("sendMessage", json_payload=payload, timeout=15)

    if response.status_code != 200:
        print(f"❌ sendMessage помилка: {response.text[:500]}")
        return False

    print("📤 Відправлено як текст")
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
                print("📤 Відправлено як фото+підпис")
                return True

            print(f"⚠️ sendPhoto URL помилка: {response.text[:300]}")
            print("⚠️ Пробуємо скачати фото і відправити файлом...")

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

                response2 = telegram_api(
                    "sendPhoto",
                    data=data,
                    files=files,
                    timeout=30
                )
                if response2.status_code == 200:
                    print("📤 Відправлено як завантажений файл")
                    return True

                print(f"❌ sendPhoto(file) помилка: {response2.text[:300]}")
            except Exception as e:
                print(f"⚠️ Помилка скачування/відправки фото: {e}")

            return _send_as_text(text, None, telegraph_url)

        if image_url and len(text) > caption_limit:
            payload = {
                "chat_id": TELEGRAM_CHANNEL,
                "photo": image_url
            }
            response = telegram_api("sendPhoto", json_payload=payload, timeout=20)

            if response.status_code != 200:
                print(f"⚠️ sendPhoto без підпису помилка: {response.text[:300]}")
            else:
                print("📤 Фото відправлено окремо")

            return _send_as_text(text, None, telegraph_url)

        return _send_as_text(text, image_url, telegraph_url)

    except Exception as e:
        print(f"❌ Помилка відправки в Telegram: {e}")
        return False


# ========== ОБРОБКА МАТЧІВ ==========

def process_fixture(fixture, remaining_slots):
    fixture_name = fixture.get("name", "Невідомий матч")
    league_name = (fixture.get("league", {}) or {}).get("name", "")
    starting_at = fixture.get("starting_at", "")

    now_check = now_utc()
    match_dt = parse_sportmonks_dt(starting_at)

    today_check = now_check.date()
    match_date_check = match_dt.date() if match_dt else today_check
    pre_threshold = timedelta(hours=2) if match_date_check == today_check else timedelta(hours=4)

    prematch_list = get_list_relation(fixture, "prematchnews", "prematchNews")
    postmatch_list = get_list_relation(fixture, "postmatchnews", "postmatchNews")

    prematch = []
    if match_dt is None or match_dt > now_check + pre_threshold:
        prematch = [("pre", n) for n in prematch_list]

    postmatch = []
    if match_dt is not None and match_dt < now_check - timedelta(hours=2):
        postmatch = [("post", n) for n in postmatch_list]

    news_items = prematch + postmatch

    if not news_items:
        print(f"Немає новин для: {fixture_name}")
        return 0

    sent_count = 0

    for news_type, news in news_items:
        if sent_count >= remaining_slots:
            break

        news_id = news.get("id")
        if not news_id:
            continue

        news_id_str = str(news_id)
        if news_id_str in published_ids:
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
            translated = format_form(translate(line_text))
            lines_ua.append(translated)

        full_text = "\n\n".join(lines_ua).strip()
        preview = safe_truncate(full_text, max_len=300) if full_text else ""

        type_label = "📊 Підсумок матчу" if news_type == "post" else "🔮 Прев'ю матчу"
        date_str = match_dt.strftime("%d.%m %H:%M") if match_dt else ""

        post_parts = [
            f"📌 <b>{html_escape(fixture_name)}</b>",
            f"⚽ {html_escape(league_name)} • 🗓 {html_escape(date_str)} UTC",
            type_label
        ]

        if title_ua:
            post_parts.append(f"<b>{html_escape(title_ua)}</b>")

        if preview:
            post_parts.append(html_escape(preview))

        post_text = "\n\n".join(part for part in post_parts if part)

        image_url = get_image(fixture)
        print(f"🖼 Фото: {image_url}")

        telegraph_url = None
        if full_text:
            telegraph_url = publish_to_telegraph(
                title=title_ua or fixture_name,
                text=full_text,
                image_url=image_url
            )

        sent_ok = post_to_telegram(post_text, image_url=image_url, telegraph_url=telegraph_url)
        if not sent_ok:
            continue

        published_ids[news_id_str] = now_utc().strftime("%Y-%m-%d")
        save_published_ids(published_ids)

        print(f"✅ Опубліковано [{news_type}]: {title_ua or fixture_name}")

        sent_count += 1
        if sent_count < remaining_slots:
            time.sleep(TELEGRAM_PAUSE_SECONDS)

    return sent_count


# ========== ГОЛОВНА ФУНКЦІЯ ==========

def run_all():
    global _used_images
    _used_images = set()

    if not SPORTMONKS_TOKEN or not TELEGRAM_TOKEN or not TELEGRAM_CHANNEL:
        print("❌ Не вистачає обов'язкових змінних: SPORTMONKS_TOKEN / TELEGRAM_TOKEN / TELEGRAM_CHANNEL")
        return

    now = now_utc()

    if now.hour not in RUN_HOURS_UTC:
        print(f"⏭ Пропуск запуску: зараз {now.strftime('%H:%M')} UTC, вікно публікації 06:00-21:59 UTC")
        return

    print("\n" + "=" * 50)
    print(f"Запуск: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC")

    fixtures = get_todays_fixtures()
    if not fixtures:
        print("Немає матчів для обробки.")
        return

    today = now.date()
    yesterday = today - timedelta(days=1)

    selected = []
    for fixture in fixtures:
        match_dt = parse_sportmonks_dt(fixture.get("starting_at"))
        if not match_dt:
            continue

        has_postmatch = bool(get_list_relation(fixture, "postmatchnews", "postmatchNews"))

        if match_dt.date() == today:
            selected.append(fixture)
        elif match_dt.date() == yesterday and has_postmatch:
            selected.append(fixture)

    def league_priority(fixture):
        league_id = fixture.get("league_id", 9999)
        if league_id in PRIORITY_LEAGUES:
            return PRIORITY_LEAGUES.index(league_id)
        return len(PRIORITY_LEAGUES)

    selected.sort(
        key=lambda f: (
            league_priority(f),
            parse_sportmonks_dt(f.get("starting_at")) or now_utc(),
            f.get("name", "")
        )
    )

    filtered = []
    for fixture in selected:
        match_time = parse_sportmonks_dt(fixture.get("starting_at"))
        if not match_time:
            continue

        has_prematch = bool(get_list_relation(fixture, "prematchnews", "prematchNews"))
        has_postmatch = bool(get_list_relation(fixture, "postmatchnews", "postmatchNews"))

        prematch_threshold = timedelta(hours=2) if match_time.date() == today else timedelta(hours=4)

        should_take = False

        if has_prematch and match_time > now + prematch_threshold:
            should_take = True

        if has_postmatch and now - timedelta(hours=24) < match_time < now - timedelta(hours=2):
            should_take = True

        if should_take:
            filtered.append(fixture)

    print(f"Матчів з релевантними новинами: {len(filtered)}")

    published_count = 0

    for fixture in filtered:
        if published_count >= MAX_POSTS_PER_RUN:
            break

        remaining = MAX_POSTS_PER_RUN - published_count
        sent_now = process_fixture(fixture, remaining)
        published_count += sent_now

    print(f"Готово. Опубліковано за запуск: {published_count}.")


# ========== ЗАПУСК ==========

if __name__ == "__main__":
    print("Бот запущено!")
    run_all()

    # Раз на годину. UTC-фільтр перевіряється всередині run_all()
    schedule.every().hour.at(":00").do(run_all)
    print("📅 Розклад: перевірка щогодини, публікація лише з 06:00 до 21:59 UTC")

    while True:
        try:
            schedule.run_pending()
            time.sleep(30)
        except KeyboardInterrupt:
            print("\nЗупинка бота.")
            break
        except Exception as e:
            print(f"⚠️ Помилка в головному циклі: {e}")
            time.sleep(30)
