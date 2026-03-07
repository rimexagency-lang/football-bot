import os
import json
import time
import re
import requests
import schedule
from datetime import datetime, timedelta
from dotenv import load_dotenv

# ========== НАЛАШТУВАННЯ ==========
load_dotenv()

SPORTMONKS_TOKEN = os.getenv("SPORTMONKS_TOKEN")
DEEPL_TOKEN      = os.getenv("DEEPL_TOKEN")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
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

# Файл зберігається поряд з bot.py
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLISHED_FILE = os.path.join(BASE_DIR, "published_ids.json")

# Надійні football фото — прямі посилання, перевірені вручну
FALLBACK_IMAGES = [
    "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1d/Football_Pallo_valmiina-cropped.jpg/320px-Football_Pallo_valmiina-cropped.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/5/5c/Football_iu_002.jpg/320px-Football_iu_002.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e3/FCB-DFB-Pokal-Finale2014.jpg/320px-FCB-DFB-Pokal-Finale2014.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/9/9e/FIFA_World_Cup_2010_final.jpg/320px-FIFA_World_Cup_2010_final.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/2/2e/UEFA_Euro_2016_final.jpg/320px-UEFA_Euro_2016_final.jpg",
]
_fallback_index = 0


# ID повідомлення в Telegram де зберігається backup
BACKUP_MSG_ID_FILE = os.path.join(BASE_DIR, "backup_msg_id.txt")


def load_published_ids():
    """Завантажує ID з файлу або з GitHub Gist."""
    # 1. Локальний файл
    if os.path.exists(PUBLISHED_FILE):
        try:
            with open(PUBLISHED_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                today = datetime.now().strftime("%Y-%m-%d")
                return {str(i): today for i in data}
            if data:
                print(f"📦 Завантажено {len(data)} ID з файлу")
                return data
        except (json.JSONDecodeError, Exception):
            pass

    # 2. GitHub Gist backup
    return _load_from_gist()


def _load_from_gist():
    """Завантажує published_ids з GitHub Gist."""
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
            headers=headers,
            timeout=10
        )
        if r.status_code == 200:
            files = r.json().get("files", {})
            content_str = files.get("published_ids.json", {}).get("content", "")
            if content_str:
                data = json.loads(content_str)
                print(f"📦 Завантажено {len(data)} ID з GitHub Gist")
                return data
        else:
            print(f"⚠️ Gist читання помилка: {r.status_code}")
    except Exception as e:
        print(f"⚠️ Gist помилка: {e}")
    return {}


def save_published_ids(published_dict):
    """Зберігає ID у файл і надсилає backup в Telegram як приховане повідомлення."""
    # 1. Локальний файл
    try:
        with open(PUBLISHED_FILE, "w", encoding="utf-8") as f:
            json.dump(published_dict, f)
    except Exception as e:
        print(f"⚠️ Файл: {e}")

    # 2. Backup у GitHub Gist
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
                print(f"☁️ Backup у GitHub Gist ({len(published_dict)} ID)")
            else:
                print(f"⚠️ Gist збереження помилка: {r.status_code} {r.text[:100]}")
        except Exception as e:
            print(f"⚠️ Gist помилка: {e}")


def cleanup_old_ids(published_dict, days=7):
    """Видаляє ID старші ніж days днів щоб файл не розростався."""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    cleaned = {k: v for k, v in published_dict.items() if v >= cutoff}
    removed = len(published_dict) - len(cleaned)
    if removed:
        print(f"🧹 Видалено старих ID: {removed}")
    return cleaned


published_ids = cleanup_old_ids(load_published_ids())
print(f"📦 Завантажено published_ids: {len(published_ids)} записів")


# ========== ФУНКЦІЇ ==========

def format_form(text):
    def replace_form(match):
        form = match.group(1)
        emoji = ""
        for char in form:
            if char == "W": emoji += "🟢"
            elif char == "L": emoji += "🔴"
            elif char == "D": emoji += "🟡"
        return f"({emoji})"
    return re.sub(r'\[([WDLU]+)\]', replace_form, text)


def get_image_wikimedia(query):
    """Шукає фото через Wikimedia Commons API — тільки спортивні фото."""
    try:
        search_url = "https://commons.wikimedia.org/w/api.php"
        # Додаємо "FC" або "stadium" щоб уникнути газет і старих фото
        search_query = f"{query} football"
        params = {
            "action": "query",
            "generator": "search",
            "gsrnamespace": "6",
            "gsrsearch": search_query,
            "gsrlimit": "10",  # Беремо більше щоб відфільтрувати погані
            "prop": "imageinfo",
            "iiprop": "url|size|mime",
            "iiurlwidth": "960",
            "format": "json"
        }
        headers = {"User-Agent": "FootballNewsBot/1.0 (contact: admin@example.com)"}
        r = requests.get(search_url, params=params, headers=headers, timeout=8)
        r.raise_for_status()

        if not r.text.strip():
            return None

        data = r.json()
        pages = data.get("query", {}).get("pages", {})

        # Збираємо кандидатів і фільтруємо
        BAD_KEYWORDS = [
            ".pdf", ".ogv", ".svg", ".tif", ".gif",
            "logo", "Logo", "badge", "Badge", "emblem", "Emblem",
            "crest", "Crest", "shield", "Shield", "coat_of",
            "flag", "Flag", "map", "Map", "portrait", "Portrait",
            "newspaper", "Newspaper", "kit", "Kit", "jersey", "Jersey",
            "_fc_", "_sc_", "_ac_", "fc_logo", "club_logo",
            "wappen", "Wappen",  # герб по-німецьки
        ]

        candidates = []
        for page in pages.values():
            info = page.get("imageinfo", [])
            if not info:
                continue
            url = info[0].get("thumburl") or info[0].get("url", "")
            url_lower = url.lower()

            # Фільтр поганих форматів і типів
            if not url_lower.endswith((".jpg", ".jpeg", ".png", ".webp")):
                continue
            if any(bad in url_lower for bad in BAD_KEYWORDS):
                continue

            # Перевіряємо розмір — мінімум 400px ширина
            width = info[0].get("thumbwidth", 0) or 0
            if width and width < 400:
                continue

            candidates.append(url)

        if candidates:
            print(f"✅ Wikimedia: {candidates[0]}")
            return candidates[0]

        print("  [Wikimedia] підходящих фото не знайдено")
    except requests.exceptions.Timeout:
        print("  [Wikimedia] timeout")
    except Exception as e:
        print(f"  [Wikimedia] помилка: {e}")
    return None


def get_image_openverse(query):
    """
    Openverse (WordPress/CC) — безкоштовно, без ключів, прямі посилання на фото.
    """
    try:
        # Спрощений пошук — тільки назви команд без "vs"
        clean_query = query.replace(" vs ", " ").replace(" vs. ", " ")
        r = requests.get(
            "https://api.openverse.org/v1/images/",
            params={
                "q": f"{clean_query} football",
                "page_size": 5,
                "license_type": "commercial",
            },
            headers={"User-Agent": "FootballNewsBot/1.0"},
            timeout=10
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        print(f"  [Openverse] знайдено: {len(results)}")
        for item in results:
            url = item.get("url", "")
            if url and url.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                print(f"✅ Openverse: {url}")
                return url
    except requests.exceptions.Timeout:
        print("  [Openverse] timeout")
    except Exception as e:
        print(f"  [Openverse] помилка: {e}")
    return None


def get_image_pixabay(query):
    """
    Pixabay — безкоштовний API (потрібна реєстрація на pixabay.com).
    Додайте PIXABAY_TOKEN у .env файл щоб увімкнути.
    """
    PIXABAY_TOKEN = os.getenv("PIXABAY_TOKEN")
    if not PIXABAY_TOKEN:
        return None
    try:
        r = requests.get(
            "https://pixabay.com/api/",
            params={
                "key": PIXABAY_TOKEN,
                "q": f"{query} football",
                "image_type": "photo",
                "per_page": 3,
                "safesearch": "true"
            },
            timeout=8
        )
        hits = r.json().get("hits", [])
        if hits:
            url = hits[0].get("webformatURL")
            if url:
                print(f"✅ Pixabay: {url}")
                return url
    except Exception as e:
        print(f"Pixabay помилка: {e}")
    return None


def get_fallback_image():
    """Повертає наступне фото зі статичного списку по черзі."""
    global _fallback_index
    img = FALLBACK_IMAGES[_fallback_index % len(FALLBACK_IMAGES)]
    _fallback_index += 1
    print(f"⚠️ Fallback фото #{_fallback_index}: {img}")
    return img


def get_image_pexels(query):
    """Шукає фото через Pexels API — якісні спортивні фото."""
    token = os.getenv("PEXELS_TOKEN")
    if not token:
        return None
    try:
        r = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": token},
            params={"query": query, "per_page": 5, "orientation": "landscape"},
            timeout=10
        )
        if r.status_code != 200:
            return None
        photos = r.json().get("photos", [])
        for photo in photos:
            url = photo.get("src", {}).get("large") or photo.get("src", {}).get("original")
            if url:
                print(f"✅ Pexels: {url}")
                return url
    except Exception as e:
        print(f"  [Pexels] помилка: {e}")
    return None


def get_image(fixture_name):
    """
    Отримує фото для поста.
    Пробує кілька варіантів запиту від конкретного до загального.
    Порядок джерел: Pixabay → Wikimedia → Openverse → статичний fallback
    """
    # Розбиваємо "Team A vs Team B" → пробуємо спочатку першу команду
    parts = re.split(r' vs\.? ', fixture_name, flags=re.IGNORECASE)
    team1 = parts[0].strip() if parts else fixture_name
    team2 = parts[1].strip() if len(parts) > 1 else ""

    # Список запитів від конкретного до загального
    queries = [
        f"{team1} football match",    # "Athletic Club football match"
        f"{team2} football match",    # "Barcelona football match"
        f"{team1} {team2}",           # "Athletic Club Barcelona"
        f"{team1} soccer",            # для Pexels
        "football match stadium",     # загальний
    ]

    for q in queries:
        if not q:
            continue
        print(f"  🔍 Шукаємо фото: '{q}'")

        img = get_image_pexels(q)
        if img:
            return img

        img = get_image_pixabay(q)
        if img:
            return img

        img = get_image_wikimedia(q)
        if img:
            return img

        img = get_image_openverse(q)
        if img:
            return img

    return get_fallback_image()


def get_todays_fixtures():
    # Вчора — для постматч новин, +3 дні — для преметч
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    in_3_days = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")

    url = f"https://api.sportmonks.com/v3/football/fixtures/between/{yesterday}/{in_3_days}"
    params = {
        "api_token": SPORTMONKS_TOKEN,
        "include": "prematchNews.lines;postmatchNews.lines;participants;league",
        "per_page": 50
    }

    try:
        all_fixtures = []
        page = 1
        while True:
            params["page"] = page
            r = requests.get(url, params=params, timeout=20)
            if r.status_code != 200:
                print(f"Помилка API Sportmonks: {r.status_code}")
                break

            resp = r.json()
            batch = resp.get("data", [])
            all_fixtures.extend(batch)

            # Перевіряємо чи є ще сторінки
            pagination = resp.get("pagination", {})
            if not pagination.get("has_more", False):
                break
            page += 1
            if page > 10:  # максимум 10 сторінок на всяк випадок
                break

        filtered = [f for f in all_fixtures if f.get("league_id") in LEAGUE_IDS]
        print(f"[{datetime.now().strftime('%H:%M')}] Знайдено матчів: {len(filtered)} (всього в API: {len(all_fixtures)})")
        return filtered
    except Exception as e:
        print(f"Помилка отримання матчів: {e}")
        return []


def translate(text):
    """Перекладає текст через DeepL. Timeout 15с — без нього зависає."""
    try:
        r = requests.post(
            "https://api-free.deepl.com/v2/translate",
            headers={"Authorization": f"DeepL-Auth-Key {DEEPL_TOKEN}"},
            json={"text": [text], "target_lang": "UK"},
            timeout=15  # КРИТИЧНО: без цього DeepL може зависнути назавжди
        )
        return r.json()["translations"][0]["text"]
    except requests.exceptions.Timeout:
        print("⏱ DeepL timeout, повертаємо оригінал")
        return text
    except Exception as e:
        print(f"Помилка перекладу: {e}")
        return text


def publish_to_telegraph(title, text, image_url=None):
    """
    Публікує повний текст на Telegraph і повертає URL статті.
    Telegraph не потребує реєстрації — використовуємо анонімний акаунт.
    """
    try:
        # Створюємо акаунт один раз (або використовуємо збережений токен)
        token = _get_telegraph_token()
        if not token:
            return None

        # Формуємо контент сторінки у форматі Telegraph
        nodes = []
        if image_url:
            nodes.append({"tag": "img", "attrs": {"src": image_url}})

        # Розбиваємо текст на параграфи
        for para in text.split("\n\n"):
            para = para.strip()
            if para:
                # Очищаємо HTML теги для Telegraph
                clean = re.sub(r'<[^>]+>', '', para)
                nodes.append({"tag": "p", "children": [clean]})

        r = requests.post(
            "https://api.telegra.ph/createPage",
            json={
                "access_token": token,
                "title": title[:256] if title else "Футбольні новини",
                "content": nodes,
                "return_content": False
            },
            timeout=10
        )
        data = r.json()
        if data.get("ok"):
            url = data["result"]["url"]
            print(f"✅ Telegraph: {url}")
            return url
        else:
            print(f"Telegraph помилка: {data.get('error')}")
    except Exception as e:
        print(f"Telegraph помилка: {e}")
    return None


def _get_telegraph_token():
    """Зберігає Telegraph токен у файл щоб не створювати акаунт щоразу."""
    token_file = "telegraph_token.txt"
    if os.path.exists(token_file):
        with open(token_file, "r") as f:
            return f.read().strip()
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
            print(f"✅ Telegraph акаунт створено")
            return token
    except Exception as e:
        print(f"Telegraph акаунт помилка: {e}")
    return None


def post_to_telegram(text, image_url=None, force_photo=False, telegraph_url=None):
    """
    Відправка поста:
    - force_photo=True → завжди спочатку sendPhoto (фото зверху)
    - Якщо текст > 1024 символів → надсилаємо фото окремо, потім текст
    - Якщо sendPhoto не вдався → fallback на sendMessage з прев'ю
    """
    try:
        CAPTION_LIMIT = 1024

        if image_url and (force_photo or len(text) <= CAPTION_LIMIT):
            if len(text) <= CAPTION_LIMIT:
                # Фото + підпис одним повідомленням
                url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
                payload = {
                    "chat_id": TELEGRAM_CHANNEL,
                    "photo": image_url,
                    "caption": text,
                    "parse_mode": "HTML"
                }
                if telegraph_url:
                    payload["reply_markup"] = {
                        "inline_keyboard": [[{
                            "text": "📖 Читати повністю",
                            "url": telegraph_url
                        }]]
                    }
                r = requests.post(url, json=payload, timeout=15)
                if r.status_code == 200:
                    print("📤 Відправлено як фото+підпис")
                    return

                # URL не спрацював — скачуємо фото і відправляємо як файл
                print(f"⚠️ sendPhoto URL помилка: {r.json().get('description')}. Скачуємо файл...")
                try:
                    import json as _json
                    img_data = requests.get(image_url, timeout=10).content
                    files = {"photo": ("photo.jpg", img_data, "image/jpeg")}
                    data = {
                        "chat_id": TELEGRAM_CHANNEL,
                        "caption": text,
                        "parse_mode": "HTML"
                    }
                    if telegraph_url:
                        data["reply_markup"] = _json.dumps({
                            "inline_keyboard": [[{
                                "text": "📖 Читати повністю",
                                "url": telegraph_url
                            }]]
                        })
                    r2 = requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                        data=data, files=files, timeout=30
                    )
                    if r2.status_code == 200:
                        print("📤 Відправлено як завантажений файл")
                        return
                    print(f"⚠️ Файл теж не спрацював: {r2.json().get('description')}")
                except Exception as e:
                    print(f"⚠️ Помилка скачування фото: {e}")
                _send_as_text(text, None)
                return

            # Текст довший 1024 — надсилаємо фото окремо, потім текст
            url_photo = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
            r = requests.post(url_photo, json={
                "chat_id": TELEGRAM_CHANNEL,
                "photo": image_url
            }, timeout=15)
            if r.status_code == 200:
                print("📤 Фото надіслано окремо")
            _send_as_text(text, image_url=None)
        else:
            _send_as_text(text, image_url)

    except Exception as e:
        print(f"Помилка відправки в Telegram: {e}")


def _send_as_text(text, image_url=None):
    """Відправляє як текст, з прев'ю фото через невидимий символ."""
    if image_url:
        text = f'<a href="{image_url}">&#8205;</a>' + text

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHANNEL,
        "text": text,
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": False}
    }
    r = requests.post(url, json=payload, timeout=15)
    if r.status_code != 200:
        print(f"❌ Помилка sendMessage: {r.text}")
    else:
        print("📤 Відправлено як текст")


def process_fixture(fixture):
    fixture_name = fixture.get("name", "Невідомий матч")
    league_name = fixture.get("league", {}).get("name", "")
    starting_at = fixture.get("starting_at", "")

    # Збираємо обидва типи новин з міткою типу
    now_check = datetime.now()
    match_dt = None
    if starting_at:
        try:
            match_dt = datetime.strptime(starting_at[:16], "%Y-%m-%d %H:%M")
        except:
            pass

    # Преметч — тільки якщо до матчу більше 4 годин
    prematch = []
    if match_dt is None or match_dt > now_check + timedelta(hours=4):
        prematch = [("pre", n) for n in fixture.get("prematchnews", [])]

    # Постматч — тільки якщо матч вже закінчився
    postmatch = []
    if match_dt is not None and match_dt < now_check - timedelta(hours=2):
        postmatch = [("post", n) for n in fixture.get("postmatchnews", [])]

    news_items = prematch + postmatch

    if not news_items:
        print(f"Немає новин для: {fixture_name}")
        return

    for news_type, news in news_items:
        news_id = news.get("id")

        if str(news_id) in published_ids:
            continue

        title = news.get("title", "")
        lines = news.get("lines", [])

        if not title and not lines:
            continue

        title_ua = format_form(translate(title)) if title else ""
        lines_ua = []
        for line in lines:
            text = line.get("text", "")
            if text:
                lines_ua.append(format_form(translate(text)))

        # Дата у форматі "07.03 19:30"
        date_str = ""
        if starting_at:
            try:
                dt = datetime.strptime(starting_at[:16], "%Y-%m-%d %H:%M")
                date_str = dt.strftime("%d.%m %H:%M")
            except:
                date_str = starting_at[:16]

        # Повний текст для Telegraph
        full_text = "\n\n".join(lines_ua) if lines_ua else ""

        # Анонс — перші 300 символів тексту
        preview = ""
        if full_text:
            clean = re.sub(r'<[^>]+>', '', full_text)
            preview = clean[:300].rsplit(" ", 1)[0] + "…" if len(clean) > 300 else clean

        # Мітка типу новини
        type_label = "📊 Підсумок матчу" if news_type == "post" else "🔮 Прев'ю матчу"

        # Формуємо пост
        post = f"📌 <b>{fixture_name}</b>\n"
        post += f"⚽ {league_name} • 🗓 {date_str} UTC\n"
        post += f"{type_label}\n"
        if title_ua:
            post += f"\n<b>{title_ua}</b>\n"
        if preview:
            post += f"\n{preview}"

        image_url = get_image(fixture_name)
        print(f"🖼 Фото: {image_url}")

        # Публікуємо повний текст на Telegraph
        telegraph_url = None
        if full_text:
            telegraph_url = publish_to_telegraph(
                title=title_ua or fixture_name,
                text=full_text,
                image_url=image_url
            )

        post_to_telegram(post, image_url, telegraph_url=telegraph_url)

        published_ids[str(news_id)] = datetime.now().strftime("%Y-%m-%d")
        save_published_ids(published_ids)
        print(f"✅ Опубліковано [{news_type}]: {title_ua or fixture_name}")

        time.sleep(2 * 60)  # 2 хвилини між постами


def print_daily_summary(fixtures):
    """Показує скільки новин реально доступно з урахуванням фільтрів."""
    now = datetime.now()
    pre_list = []
    post_list = []

    for f in fixtures:
        name = f.get("name", "?")
        starting_at = f.get("starting_at", "")
        date_str = ""
        match_dt = None
        if starting_at:
            try:
                match_dt = datetime.strptime(starting_at[:16], "%Y-%m-%d %H:%M")
                date_str = match_dt.strftime("%d.%m %H:%M")
            except:
                pass

        prematch = f.get("prematchnews", [])
        postmatch = f.get("postmatchnews", [])

        # Тільки ті що пройдуть фільтр — превью за 4+ години до матчу
        if match_dt and match_dt > now + timedelta(hours=4):
            for n in prematch:
                if str(n.get("id")) not in published_ids:
                    hours_left = (match_dt - now).seconds // 3600
                    pre_list.append(f"  🔮 {date_str} UTC — {name} (через ~{hours_left}г)")
                    break

        # Підсумки — тільки за останні 24 години
        if match_dt and now - timedelta(hours=24) < match_dt < now - timedelta(hours=2):
            for n in postmatch:
                if str(n.get("id")) not in published_ids:
                    post_list.append(f"  📊 {date_str} UTC — {name}")
                    break

    total = len(pre_list) + len(post_list)
    # Статистика по лігах
    league_stats = {}
    for f in fixtures:
        lid = f.get("league_id")
        lname = f.get("league", {}).get("name", str(lid)) if isinstance(f.get("league"), dict) else str(lid)
        has_news = bool(f.get("prematchnews") or f.get("postmatchnews"))
        if lname not in league_stats:
            league_stats[lname] = {"total": 0, "with_news": 0}
        league_stats[lname]["total"] += 1
        if has_news:
            league_stats[lname]["with_news"] += 1

    print(f"\n📊 Матчі по лігах (з новинами / всього):")
    for lname, s in sorted(league_stats.items(), key=lambda x: -x[1]["with_news"]):
        if s["with_news"] > 0:
            print(f"  {lname}: {s['with_news']}/{s['total']}")

    print(f"\n📋 Реально доступно для публікації: {total}")
    if pre_list:
        print(f"  Прев'ю ({len(pre_list)}):")
        for x in pre_list[:15]:
            print(x)
    if post_list:
        print(f"  Підсумки ({len(post_list)}):")
        for x in post_list[:10]:
            print(x)
    if total == 0:
        print("  Нових новин немає.")
    else:
        runs_needed = -(-total // MAX_POSTS_PER_RUN)
        print(f"  Потрібно запусків: ~{runs_needed} (по {MAX_POSTS_PER_RUN} поста)")


def run_all():
    print(f"\n{'='*40}")
    print(f"Запуск: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    fixtures = get_todays_fixtures()
    print_daily_summary(fixtures)

    # Спочатку публікуємо сьогоднішні, якщо є — завтрашні не чіпаємо
    now_dt = datetime.now()
    today_str = now_dt.strftime("%Y-%m-%d")

    yesterday_str = (now_dt - timedelta(days=1)).strftime("%Y-%m-%d")

    # Сьогоднішні матчі (преметч + постматч)
    # + вчорашні матчі (тільки постматч — новини з'являються після гри)
    today_fixtures = [f for f in fixtures if f.get("starting_at", "").startswith(today_str)]
    yesterday_postmatch = [
        f for f in fixtures
        if f.get("starting_at", "").startswith(yesterday_str) and f.get("postmatchnews")
    ]
    fixtures = today_fixtures + yesterday_postmatch
    print(f"📅 Матчів для публікації: {len(today_fixtures)} сьогодні + {len(yesterday_postmatch)} вчорашніх постматч")

    # Сортуємо: спочатку топ-ліги, потім решта
    def league_priority(f):
        lid = f.get("league_id", 9999)
        return PRIORITY_LEAGUES.index(lid) if lid in PRIORITY_LEAGUES else len(PRIORITY_LEAGUES)

    fixtures.sort(key=league_priority)

    now = datetime.now()
    filtered = []
    for f in fixtures:
        if not f.get("starting_at"):
            continue
        match_time = datetime.strptime(f["starting_at"][:16], "%Y-%m-%d %H:%M")
        has_prematch = bool(f.get("prematchnews"))
        has_postmatch = bool(f.get("postmatchnews"))
        # Преметч — тільки якщо до матчу більше 4 годин
        # Постматч — тільки якщо матч вже закінчився (за останні 24 години)
        if (has_prematch and match_time > now + timedelta(hours=4)) or            (has_postmatch and now - timedelta(hours=24) < match_time < now - timedelta(hours=2)):
            filtered.append(f)
    fixtures = filtered
    print(f"Матчів з новинами: {len(fixtures)}")

    # Динамічний ліміт: якщо новин багато і мало часу — публікуємо більше за раз
    now_dyn = datetime.now()
    urgent = sum(
        1 for f in fixtures
        if f.get("starting_at") and
        now_dyn + timedelta(hours=4) < datetime.strptime(f["starting_at"][:16], "%Y-%m-%d %H:%M") < now_dyn + timedelta(hours=6)
    )
    dynamic_limit = MAX_POSTS_PER_RUN + urgent  # +1 за кожен терміновий матч
    if urgent:
        print(f"⚡ Терміново: {urgent} матчів через 4-6 годин, ліміт збільшено до {dynamic_limit}")

    published_count = 0
    for fixture in fixtures:
        if published_count >= dynamic_limit:
            print(f"⏹ Досягнуто ліміт {dynamic_limit} постів за запуск")
            break
        before = len(published_ids)
        process_fixture(fixture)
        if len(published_ids) > before:
            published_count += 1

    print(f"Готово. Опубліковано: {published_count}. Наступний запуск о 09:00 або 18:00.")


# ========== ЗАПУСК ==========
# Топ-ліги — публікуються в першу чергу
PRIORITY_LEAGUES = [2, 5, 8, 82, 564]  # UCL, UEL, PL, Bundesliga, La Liga
MAX_POSTS_PER_RUN = 3  # максимум постів за один запуск (запуск кожні 2 години)

if __name__ == "__main__":
    print("Бот запущено!")
    print("📅 Розклад: 09:00 та 18:00 щодня")
    run_all()

    # Кожні 2 години з 06:00 UTC (= 08:00 Київ, UTC+2) до 21:00 UTC (= 23:00 Київ)
    for hour in range(6, 22, 2):
        schedule.every().day.at(f"{hour:02d}:00").do(run_all)
    print(f"📅 Розклад: кожні 2 години з 06:00 до 20:00 UTC (08:00-22:00 Київ)")

    while True:
        try:
            schedule.run_pending()
            time.sleep(60)
        except KeyboardInterrupt:
            print("\nЗупинка бота користувачем.")
            break
