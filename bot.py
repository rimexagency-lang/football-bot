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


def load_published_ids():
    # 1. Спробуємо з файлу
    if os.path.exists(PUBLISHED_FILE):
        try:
            with open(PUBLISHED_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                today = datetime.now().strftime("%Y-%m-%d")
                return {str(i): today for i in data}
            return data
        except (json.JSONDecodeError, Exception):
            pass

    # 2. Спробуємо з змінної середовища (резервна копія для Railway)
    env_data = os.getenv("PUBLISHED_IDS_BACKUP")
    if env_data:
        try:
            data = json.loads(env_data)
            print(f"📦 Завантажено {len(data)} ID з env backup")
            return data
        except Exception:
            pass

    return {}


def save_published_ids(published_dict):
    # Зберігаємо у файл
    try:
        with open(PUBLISHED_FILE, "w", encoding="utf-8") as f:
            json.dump(published_dict, f)
    except Exception as e:
        print(f"⚠️ Не вдалося зберегти файл: {e}")

    # Зберігаємо резервну копію в змінну Railway через API
    railway_token = os.getenv("RAILWAY_API_TOKEN")
    railway_service = os.getenv("RAILWAY_SERVICE_ID")
    railway_env = os.getenv("RAILWAY_ENVIRONMENT_ID")
    if railway_token and railway_service and railway_env:
        try:
            backup_str = json.dumps(published_dict)
            # Оновлюємо змінну через Railway API
            query = """
            mutation($serviceId: String!, $environmentId: String!, $name: String!, $value: String!) {
              variableUpsert(input: {
                serviceId: $serviceId,
                environmentId: $environmentId,
                name: $name,
                value: $value
              })
            }
            """
            requests.post(
                "https://backboard.railway.app/graphql/v2",
                headers={"Authorization": f"Bearer {railway_token}"},
                json={"query": query, "variables": {
                    "serviceId": railway_service,
                    "environmentId": railway_env,
                    "name": "PUBLISHED_IDS_BACKUP",
                    "value": backup_str
                }},
                timeout=10
            )
            print(f"☁️ Backup збережено в Railway ({len(published_dict)} ID)")
        except Exception as e:
            print(f"⚠️ Railway backup помилка: {e}")


def cleanup_old_ids(published_dict, days=7):
    """Видаляє ID старші ніж days днів щоб файл не розростався."""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    cleaned = {k: v for k, v in published_dict.items() if v >= cutoff}
    removed = len(published_dict) - len(cleaned)
    if removed:
        print(f"🧹 Видалено старих ID: {removed}")
    return cleaned


published_ids = cleanup_old_ids(load_published_ids())


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
    """Шукає фото через Wikimedia Commons API. Без ключів, прямі посилання."""
    try:
        search_url = "https://commons.wikimedia.org/w/api.php"
        params = {
            "action": "query",
            "generator": "search",
            "gsrnamespace": "6",
            "gsrsearch": f"{query} football match",
            "gsrlimit": "5",
            "prop": "imageinfo",
            "iiprop": "url",
            "iiurlwidth": "800",
            "format": "json"
        }
        headers = {"User-Agent": "FootballNewsBot/1.0 (contact: admin@example.com)"}
        r = requests.get(search_url, params=params, headers=headers, timeout=8)
        r.raise_for_status()

        if not r.text.strip():
            print("  [Wikimedia] порожня відповідь")
            return None

        data = r.json()
        pages = data.get("query", {}).get("pages", {})
        print(f"  [Wikimedia] знайдено сторінок: {len(pages)}")
        for page in pages.values():
            info = page.get("imageinfo", [])
            if info:
                url = info[0].get("thumburl") or info[0].get("url")
                print(f"  [Wikimedia] кандидат: {url}")
                if url and url.lower().endswith((".jpg", ".jpeg", ".png", ".webp")) and ".pdf" not in url.lower():
                    print(f"✅ Wikimedia: {url}")
                    return url
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
        team1,                        # "Heidenheim"
        team2,                        # "Hoffenheim"
        f"{team1} football",          # "Heidenheim football"
        "football stadium match",     # загальний
    ]

    for q in queries:
        if not q:
            continue
        print(f"  🔍 Шукаємо фото: '{q}'")

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
        r = requests.get(url, params=params, timeout=20)
        if r.status_code != 200:
            print(f"Помилка API Sportmonks: {r.status_code}")
            return []

        fixtures = r.json().get("data", [])
        filtered = [f for f in fixtures if f.get("league_id") in LEAGUE_IDS]
        print(f"[{datetime.now().strftime('%H:%M')}] Знайдено матчів: {len(filtered)}")
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

    # Преметч — тільки якщо матч ще не почався
    prematch = []
    if match_dt is None or match_dt > now_check + timedelta(hours=1):
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


def run_all():
    print(f"\n{'='*40}")
    print(f"Запуск: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    fixtures = get_todays_fixtures()

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
        # Преметч — тільки якщо матч ЩЕ НЕ почався (більш ніж 1 година до старту)
        # Постматч — тільки якщо матч ВЖЕ ЗІГРАНО (за останні 24 години)
        if (has_prematch and match_time > now + timedelta(hours=1)) or            (has_postmatch and now - timedelta(hours=24) < match_time < now - timedelta(hours=2)):
            filtered.append(f)
    fixtures = filtered
    print(f"Матчів з новинами: {len(fixtures)}")

    published_count = 0
    for fixture in fixtures:
        if published_count >= MAX_POSTS_PER_RUN:
            print(f"⏹ Досягнуто ліміт {MAX_POSTS_PER_RUN} постів за запуск")
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

    # Кожні 2 години з 8:00 до 23:00
    for hour in range(8, 24, 2):
        schedule.every().day.at(f"{hour:02d}:00").do(run_all)

    while True:
        try:
            schedule.run_pending()
            time.sleep(60)
        except KeyboardInterrupt:
            print("\nЗупинка бота користувачем.")
            break
