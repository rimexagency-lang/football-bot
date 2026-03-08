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

PRIORITY_LEAGUES = [2, 5, 8, 82, 564]
MAX_POSTS_PER_RUN = 3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLISHED_FILE = os.path.join(BASE_DIR, "published_ids.json")

FALLBACK_IMAGES = [
    "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1d/Football_Pallo_valmiina-cropped.jpg/320px-Football_Pallo_valmiina-cropped.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/5/5c/Football_iu_002.jpg/320px-Football_iu_002.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e3/FCB-DFB-Pokal-Finale2014.jpg/320px-FCB-DFB-Pokal-Finale2014.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/9/9e/FIFA_World_Cup_2010_final.jpg/320px-FIFA_World_Cup_2010_final.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/2/2e/UEFA_Euro_2016_final.jpg/320px-UEFA_Euro_2016_final.jpg",
]
_fallback_index = 0
_used_images = set()

TEAM_SEARCH_NAMES = {
    "Athletic Club": "Athletic Bilbao",
    "FC Barcelona": "Barcelona football",
    "Real Madrid": "Real Madrid football",
    "Manchester City": "Manchester City football",
    "Manchester United": "Manchester United football",
    "Liverpool": "Liverpool football",
    "Arsenal": "Arsenal football",
    "Chelsea": "Chelsea football",
    "Tottenham Hotspur": "Tottenham football",
    "Paris Saint Germain": "PSG Paris football",
    "Olympique de Marseille": "Marseille football",
    "Olympique Lyonnais": "Lyon football",
    "Bayern Munich": "Bayern Munich football",
    "FC Bayern München": "Bayern Munich football",
    "Borussia Dortmund": "Dortmund football",
    "Juventus": "Juventus football",
    "AC Milan": "AC Milan football",
    "Inter": "Inter Milan football",
    "AS Roma": "Roma football",
    "Napoli": "Napoli football",
    "Atletico Madrid": "Atletico Madrid football",
    "Sevilla": "Sevilla football",
    "Ajax": "Ajax Amsterdam football",
    "Benfica": "Benfica football",
    "Porto": "Porto football",
    "Genoa": "Genoa CFC football 2025",
    "Roma": "AS Roma football 2025",
}


# ========== PUBLISHED IDS ==========

def load_published_ids():
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
        except Exception:
            pass
    return _load_from_gist()


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
                print(f"📦 Завантажено {len(data)} ID з GitHub Gist")
                return data
    except Exception as e:
        print(f"⚠️ Gist помилка: {e}")
    return {}


def save_published_ids(published_dict):
    try:
        with open(PUBLISHED_FILE, "w", encoding="utf-8") as f:
            json.dump(published_dict, f)
    except Exception as e:
        print(f"⚠️ Файл: {e}")

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
        except Exception as e:
            print(f"⚠️ Gist помилка: {e}")


def cleanup_old_ids(published_dict, days=7):
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


def get_image_google(query):
    """Google Custom Search — тільки свіжі фото."""
    api_key = os.getenv("GOOGLE_SEARCH_KEY")
    cx = os.getenv("GOOGLE_SEARCH_CX")
    if not api_key or not cx:
        return None
    try:
        year = datetime.now().year
        r = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "key": api_key,
                "cx": cx,
                "q": f"{team1} OR {team2} football player match {year} -camera -equipment -stadium -empty",
                "searchType": "image",
                "num": 5,
                "imgType": "photo",
                "imgSize": "large",
                "safe": "active",
                "dateRestrict": "m6",  # тільки за останні 6 місяців
            },
            timeout=10
        )
        if r.status_code != 200:
            print(f"  [Google] помилка: {r.status_code}")
            return None
        items = r.json().get("items", [])
        for item in items:
            url = item.get("link", "")
            if url and url.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                if url not in _used_images:
                    _used_images.add(url)
                    print(f"✅ Google: {url}")
                    return url
    except Exception as e:
        print(f"  [Google] помилка: {e}")
    return None


def get_image_wikimedia(query):
    """Wikimedia Commons — тільки спортивні фото."""
    try:
        BAD_KEYWORDS = [
            ".pdf", ".ogv", ".svg", ".tif", ".gif",
            "logo", "badge", "emblem", "crest", "shield",
            "coat_of", "flag", "map", "portrait", "newspaper",
            "kit", "jersey", "wappen", "fc_logo", "club_logo",
        ]
        r = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "generator": "search",
                "gsrnamespace": "6",
                "gsrsearch": f"{query} football",
                "gsrlimit": "10",
                "prop": "imageinfo",
                "iiprop": "url|size|mime",
                "iiurlwidth": "960",
                "format": "json"
            },
            headers={"User-Agent": "FootballNewsBot/1.0"},
            timeout=8
        )
        r.raise_for_status()
        pages = r.json().get("query", {}).get("pages", {})
        for page in pages.values():
            info = page.get("imageinfo", [])
            if not info:
                continue
            url = info[0].get("thumburl") or info[0].get("url", "")
            url_lower = url.lower()
            if not url_lower.endswith((".jpg", ".jpeg", ".png", ".webp")):
                continue
            if any(bad in url_lower for bad in BAD_KEYWORDS):
                continue
            width = info[0].get("thumbwidth", 0) or 0
            if width and width < 400:
                continue
            print(f"✅ Wikimedia: {url}")
            return url
    except Exception as e:
        print(f"  [Wikimedia] помилка: {e}")
    return None


def get_image_openverse(query):
    """Openverse — безкоштовно без ключів."""
    try:
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
        for item in results:
            url = item.get("url", "")
            if url and url.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                print(f"✅ Openverse: {url}")
                return url
    except Exception as e:
        print(f"  [Openverse] помилка: {e}")
    return None


def get_image_pexels(query):
    """Pexels — якісні спортивні фото."""
    import random
    token = os.getenv("PEXELS_TOKEN")
    if not token:
        return None
    try:
        page = random.randint(1, 3)
        r = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": token},
            params={
                "query": query,
                "per_page": 10,
                "orientation": "landscape",
                "page": page
            },
            timeout=10
        )
        if r.status_code != 200:
            return None
        photos = r.json().get("photos", [])
        random.shuffle(photos)
        for photo in photos:
            url = photo.get("src", {}).get("large") or photo.get("src", {}).get("original")
            if url and url not in _used_images:
                _used_images.add(url)
                print(f"✅ Pexels: {url}")
                return url
    except Exception as e:
        print(f"  [Pexels] помилка: {e}")
    return None


def get_fallback_image():
    global _fallback_index
    img = FALLBACK_IMAGES[_fallback_index % len(FALLBACK_IMAGES)]
    _fallback_index += 1
    print(f"⚠️ Fallback фото: {img}")
    return img


def get_image(fixture_name, fixture=None):
    """Отримує релевантне фото — Google → Wikimedia → Pexels → Openverse → Fallback."""
    parts = re.split(r' vs\.? ', fixture_name, flags=re.IGNORECASE)
    team1 = parts[0].strip() if parts else fixture_name
    team2 = parts[1].strip() if len(parts) > 1 else ""

    search1 = TEAM_SEARCH_NAMES.get(team1, f"{team1} football")
    search2 = TEAM_SEARCH_NAMES.get(team2, f"{team2} football") if team2 else ""

    year = datetime.now().year

    # 1. Google — найрелевантніші свіжі фото
    for q in [
    f"{team1} football player {year}",
    f"{team2} football player {year}",
    f"{team1} vs {team2} match action {year}"
]:
        if not q.strip():
            continue
        print(f"  🔍 Google: '{q}'")
        img = get_image_google(q)
        if img:
            return img

    # 2. Wikimedia
    for q in [search1, search2]:
        if not q:
            continue
        img = get_image_wikimedia(q)
        if img:
            return img

    # 3. Pexels
    for q in [search1, search2]:
        if not q:
            continue
        img = get_image_pexels(q)
        if img:
            return img

    # 4. Openverse
    for q in [search1, search2]:
        if not q:
            continue
        img = get_image_openverse(q)
        if img:
            return img

    return get_fallback_image()


# ========== SPORTMONKS ==========

def get_todays_fixtures():
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
                print(f"Помилка API: {r.status_code}")
                break
            resp = r.json()
            batch = resp.get("data", [])
            all_fixtures.extend(batch)
            if not resp.get("pagination", {}).get("has_more", False):
                break
            page += 1
            if page > 10:
                break

        filtered = [f for f in all_fixtures if f.get("league_id") in LEAGUE_IDS]
        print(f"[{datetime.now().strftime('%H:%M')}] Матчів: {len(filtered)} / {len(all_fixtures)}")
        return filtered
    except Exception as e:
        print(f"Помилка отримання матчів: {e}")
        return []


# ========== ПЕРЕКЛАД ==========

def translate(text):
    try:
        r = requests.post(
            "https://api-free.deepl.com/v2/translate",
            headers={"Authorization": f"DeepL-Auth-Key {DEEPL_TOKEN}"},
            json={"text": [text], "target_lang": "UK"},
            timeout=15
        )
        return r.json()["translations"][0]["text"]
    except requests.exceptions.Timeout:
        print("⏱ DeepL timeout")
        return text
    except Exception as e:
        print(f"Помилка перекладу: {e}")
        return text


# ========== TELEGRAPH ==========

def _get_telegraph_token():
    token_file = os.path.join(BASE_DIR, "telegraph_token.txt")
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
            print("✅ Telegraph акаунт створено")
            return token
    except Exception as e:
        print(f"Telegraph помилка: {e}")
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
            para = para.strip()
            if para:
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
    except Exception as e:
        print(f"Telegraph помилка: {e}")
    return None


# ========== TELEGRAM ==========

def _send_as_text(text, image_url=None, telegraph_url=None):
    if image_url:
        text = f'<a href="{image_url}">&#8205;</a>' + text
    payload = {
        "chat_id": TELEGRAM_CHANNEL,
        "text": text,
        "parse_mode": "HTML",
    }
    if telegraph_url:
        payload["reply_markup"] = {
            "inline_keyboard": [[{
                "text": "📖 Читати повністю",
                "url": telegraph_url
            }]]
        }
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json=payload, timeout=15
    )
    if r.status_code != 200:
        print(f"❌ sendMessage помилка: {r.text}")
    else:
        print("📤 Відправлено як текст")


def post_to_telegram(text, image_url=None, telegraph_url=None):
    try:
        CAPTION_LIMIT = 1024

        if image_url and len(text) <= CAPTION_LIMIT:
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
            r = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                json=payload, timeout=15
            )
            if r.status_code == 200:
                print("📤 Відправлено як фото+підпис")
                return

            # Якщо URL не спрацював — скачуємо і відправляємо файлом
            print(f"⚠️ sendPhoto URL помилка. Скачуємо...")
            try:
                import json as _json
                img_data = requests.get(image_url, timeout=10).content
                files = {"photo": ("photo.jpg", img_data, "image/jpeg")}
                data = {"chat_id": TELEGRAM_CHANNEL, "caption": text, "parse_mode": "HTML"}
                if telegraph_url:
                    data["reply_markup"] = _json.dumps({
                        "inline_keyboard": [[{"text": "📖 Читати повністю", "url": telegraph_url}]]
                    })
                r2 = requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                    data=data, files=files, timeout=30
                )
                if r2.status_code == 200:
                    print("📤 Відправлено як завантажений файл")
                    return
            except Exception as e:
                print(f"⚠️ Помилка скачування фото: {e}")

            _send_as_text(text, None, telegraph_url)

        elif image_url and len(text) > CAPTION_LIMIT:
            # Фото окремо, потім текст
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                json={"chat_id": TELEGRAM_CHANNEL, "photo": image_url},
                timeout=15
            )
            _send_as_text(text, None, telegraph_url)
        else:
            _send_as_text(text, image_url, telegraph_url)

    except Exception as e:
        print(f"Помилка відправки: {e}")


# ========== ОБРОБКА МАТЧІВ ==========

def process_fixture(fixture):
    fixture_name = fixture.get("name", "Невідомий матч")
    league_name = fixture.get("league", {}).get("name", "")
    starting_at = fixture.get("starting_at", "")

    now_check = datetime.now()
    match_dt = None
    if starting_at:
        try:
            match_dt = datetime.strptime(starting_at[:16], "%Y-%m-%d %H:%M")
        except Exception:
            pass

    today_check = now_check.strftime("%Y-%m-%d")
    match_date_check = match_dt.strftime("%Y-%m-%d") if match_dt else today_check
    pre_threshold = timedelta(hours=2) if match_date_check == today_check else timedelta(hours=4)

    prematch = []
    if match_dt is None or match_dt > now_check + pre_threshold:
        prematch = [("pre", n) for n in fixture.get("prematchnews", [])]

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

        date_str = ""
        if starting_at:
            try:
                dt = datetime.strptime(starting_at[:16], "%Y-%m-%d %H:%M")
                date_str = dt.strftime("%d.%m %H:%M")
            except Exception:
                date_str = starting_at[:16]

        full_text = "\n\n".join(lines_ua) if lines_ua else ""

        preview = ""
        if full_text:
            clean = re.sub(r'<[^>]+>', '', full_text)
            preview = clean[:300].rsplit(" ", 1)[0] + "…" if len(clean) > 300 else clean

        type_label = "📊 Підсумок матчу" if news_type == "post" else "🔮 Прев'ю матчу"

        post = f"📌 <b>{fixture_name}</b>\n"
        post += f"⚽ {league_name} • 🗓 {date_str} UTC\n"
        post += f"{type_label}\n"
        if title_ua:
            post += f"\n<b>{title_ua}</b>\n"
        if preview:
            post += f"\n{preview}"

        image_url = get_image(fixture_name, fixture=fixture)
        print(f"🖼 Фото: {image_url}")

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

        time.sleep(2 * 60)


# ========== ГОЛОВНА ФУНКЦІЯ ==========

def run_all():
    global _used_images
    _used_images = set()

    print(f"\n{'='*40}")
    print(f"Запуск: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    fixtures = get_todays_fixtures()

    now_dt = datetime.now()
    today_str = now_dt.strftime("%Y-%m-%d")
    yesterday_str = (now_dt - timedelta(days=1)).strftime("%Y-%m-%d")

    today_fixtures = [f for f in fixtures if f.get("starting_at", "").startswith(today_str)]
    yesterday_postmatch = [
        f for f in fixtures
        if f.get("starting_at", "").startswith(yesterday_str) and f.get("postmatchnews")
    ]
    fixtures = today_fixtures + yesterday_postmatch

    def league_priority(f):
        lid = f.get("league_id", 9999)
        return PRIORITY_LEAGUES.index(lid) if lid in PRIORITY_LEAGUES else len(PRIORITY_LEAGUES)

    fixtures.sort(key=league_priority)

    now = datetime.now()
    filtered = []
    for f in fixtures:
        if not f.get("starting_at"):
            continue
        try:
            match_time = datetime.strptime(f["starting_at"][:16], "%Y-%m-%d %H:%M")
        except Exception:
            continue
        has_prematch = bool(f.get("prematchnews"))
        has_postmatch = bool(f.get("postmatchnews"))
        match_date = match_time.strftime("%Y-%m-%d")
        today_date = now.strftime("%Y-%m-%d")
        prematch_threshold = timedelta(hours=2) if match_date == today_date else timedelta(hours=4)
        if (has_prematch and match_time > now + prematch_threshold) or \
           (has_postmatch and now - timedelta(hours=24) < match_time < now - timedelta(hours=2)):
            filtered.append(f)

    print(f"Матчів з новинами: {len(filtered)}")

    published_count = 0
    for fixture in filtered:
        before = len(published_ids)
        process_fixture(fixture)
        if len(published_ids) > before:
            published_count += 1

    print(f"Готово. Опубліковано: {published_count}.")


# ========== ЗАПУСК ==========

if __name__ == "__main__":
    print("Бот запущено!")
    run_all()

    for hour in range(6, 22):
        schedule.every().day.at(f"{hour:02d}:00").do(run_all)
    print("📅 Розклад: щогодини з 06:00 до 21:00 UTC")

    while True:
        try:
            schedule.run_pending()
            time.sleep(60)
        except KeyboardInterrupt:
            print("\nЗупинка бота.")
            break

