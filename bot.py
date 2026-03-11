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

load_dotenv()

SPORTMONKS_TOKEN = os.getenv("SPORTMONKS_TOKEN")
DEEPL_TOKEN      = os.getenv("DEEPL_TOKEN")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL")

KYIV_TZ = ZoneInfo("Europe/Kyiv")

LEAGUE_IDS = [
    2, 5, 8, 9, 24, 27, 82, 301, 384, 387, 390,
    564, 567, 570, 72, 462, 208, 453, 501, 600, 609,
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

# Кеш fixture — щоб не дублювати запити до API
_fixture_cache = {}


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


def is_date_relevant(starting_at, news_type="pre"):
    """Перевіряє чи дата матчу в межах вікна.
    pre-match: від -1 до +7 днів
    post-match: від -3 до +1 день
    """
    if not starting_at:
        return True  # якщо дати немає — не фільтруємо
    try:
        match_dt = datetime.strptime(starting_at[:10], "%Y-%m-%d")
        now = datetime.now()
        if news_type == "post":
            return (now - timedelta(days=3)) <= match_dt <= (now + timedelta(days=1))
        else:
            return (now - timedelta(days=1)) <= match_dt <= (now + timedelta(days=7))
    except Exception:
        return True


# ========== FIXTURE (з кешем) ==========

def get_fixture(fixture_id):
    """Отримує fixture з кешу або API."""
    if fixture_id in _fixture_cache:
        return _fixture_cache[fixture_id]
    try:
        r = requests.get(
            f"https://api.sportmonks.com/v3/football/fixtures/{fixture_id}",
            params={"api_token": SPORTMONKS_TOKEN, "include": "participants;league"},
            timeout=10
        )
        if r.status_code == 200:
            data = r.json().get("data", {})
            _fixture_cache[fixture_id] = data
            return data
    except Exception as e:
        print(f"⚠️ Fixture {fixture_id}: {e}", flush=True)
    return {}


# ========== ФОТО ==========

_logo_cache = {}  # кеш логотипів Wikipedia за назвою команди

def upgrade_sportmonks_url(url):
    """Спробувати отримати більший розмір з Sportmonks CDN."""
    if not url or "cdn.sportmonks.com" not in url:
        return url
    # Sportmonks CDN: .../teams/22/22.png → спробуємо /big/ або /400/
    # Також прибираємо можливі маленькі суфікси типу _small
    url = url.replace("_small", "").replace("_thumb", "").replace("_150", "").replace("_100", "")
    return url


def get_wikipedia_logo(team_name):
    """Отримати логотип команди з Wikipedia (400px)."""
    if not team_name:
        return None
    cache_key = team_name.lower().strip()
    if cache_key in _logo_cache:
        return _logo_cache[cache_key]
    try:
        # Шукаємо сторінку на Wikipedia
        search_url = "https://en.wikipedia.org/w/api.php"
        r = requests.get(search_url, params={
            "action": "query",
            "titles": team_name,
            "prop": "pageimages",
            "format": "json",
            "pithumbsize": 400,
            "redirects": 1
        }, timeout=8)
        if r.status_code == 200:
            pages = r.json().get("query", {}).get("pages", {})
            for page in pages.values():
                thumb = page.get("thumbnail", {}).get("source")
                if thumb:
                    _logo_cache[cache_key] = thumb
                    return thumb
    except Exception as e:
        print(f"⚠️ Wikipedia logo ({team_name}): {e}", flush=True)
    _logo_cache[cache_key] = None
    return None


def get_image(fixture):
    global _fallback_index

    participants = fixture.get("participants", [])
    sorted_p = sorted(
        participants,
        key=lambda p: 0 if str((p.get("meta") or {}).get("location", "")).lower() == "home" else 1
    )

    # 1. Спочатку пробуємо Sportmonks з покращеним URL
    for p in sorted_p:
        img = p.get("image_path") or p.get("logo_path")
        if img and img.startswith("http"):
            return upgrade_sportmonks_url(img)

    # 2. Fallback — Wikipedia за назвою команди (home team)
    for p in sorted_p:
        name = p.get("name") or p.get("display_name")
        if name:
            wiki_img = get_wikipedia_logo(name)
            if wiki_img:
                return wiki_img

    # 3. Логотип ліги
    league = fixture.get("league") or {}
    img = league.get("image_path") or league.get("logo_path")
    if img and img.startswith("http"):
        return upgrade_sportmonks_url(img)

    img = FALLBACK_IMAGES[_fallback_index % len(FALLBACK_IMAGES)]
    _fallback_index += 1
    return img


# ========== SPORTMONKS NEWS via FIXTURES ==========

def get_fixtures_with_news(date_str):
    """Отримати fixtures з вбудованими новинами для конкретної дати."""
    url = "https://api.sportmonks.com/v3/football/fixtures/date/" + date_str
    params = {
        "api_token": SPORTMONKS_TOKEN,
        "include": "prematchnews.lines;postmatchnews.lines;participants;league",
        "per_page": 100,
    }
    fixtures = []
    page = 1
    while True:
        params["page"] = page
        try:
            r = requests.get(url, params=params, timeout=20)
            if r.status_code != 200:
                break
            resp = r.json()
            data = resp.get("data", [])
            fixtures.extend(data)
            if not resp.get("pagination", {}).get("has_more", False):
                break
            page += 1
            if page > 10:
                break
        except Exception as e:
            print(f"⚠️ fixtures {date_str}: {e}", flush=True)
            break
    return fixtures


def get_all_news():
    """Збираємо новини з fixtures за вчора, сьогодні і завтра."""
    all_news = []
    now = datetime.now()
    dates = [
        (now - timedelta(days=1)).strftime("%Y-%m-%d"),
        now.strftime("%Y-%m-%d"),
        (now + timedelta(days=1)).strftime("%Y-%m-%d"),
        (now + timedelta(days=2)).strftime("%Y-%m-%d"),
        (now + timedelta(days=3)).strftime("%Y-%m-%d"),
        (now + timedelta(days=4)).strftime("%Y-%m-%d"),
        (now + timedelta(days=5)).strftime("%Y-%m-%d"),
        (now + timedelta(days=6)).strftime("%Y-%m-%d"),
        (now + timedelta(days=7)).strftime("%Y-%m-%d"),
    ]

    total_fixtures = 0
    for date_str in dates:
        fixtures = get_fixtures_with_news(date_str)
        total_fixtures += len(fixtures)
        for f in fixtures:
            # Зберігаємо fixture в кеш для подальшого use
            fid = f.get("id")
            if fid:
                _fixture_cache[fid] = f

            # Прематч новини
            for news in f.get("prematchnews", []) or []:
                news["_fixture"] = f  # вбудовуємо fixture
                all_news.append(news)

            # Постматч новини
            for news in f.get("postmatchnews", []) or []:
                news["_fixture"] = f
                all_news.append(news)

    print(f"  📡 fixtures: {total_fixtures} матчів", flush=True)

    seen = set()
    unique = []
    for n in all_news:
        if n.get("id") not in seen:
            seen.add(n.get("id"))
            unique.append(n)

    print(f"📰 Всього унікальних новин: {len(unique)}", flush=True)
    return unique


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
    news_id = news_item.get("id")
    if not news_id or str(news_id) in published_ids:
        return 0

    fixture_id = news_item.get("fixture_id")
    title = (news_item.get("title") or "").strip()
    news_type_raw = news_item.get("type", "prematch")
    news_type = "post" if "post" in str(news_type_raw).lower() else "pre"

    fixture_name = f"Матч ID:{fixture_id}"
    league_name = ""
    starting_at = ""
    image_url = None

    # Беремо fixture з вбудованого поля або з кешу/API
    f = news_item.get("_fixture") or (get_fixture(fixture_id) if fixture_id else {})
    if f:
        fixture_name = f.get("name", fixture_name)
        league_name = (f.get("league") or {}).get("name", "")
        starting_at = f.get("starting_at", "")
        image_url = get_image(f)

    lines = news_item.get("lines", [])

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



# ========== RSS НОВИНИ ==========

RSS_FEEDS = [
    ("https://www.goal.com/feeds/en/news", "Goal.com"),
    ("https://feeds.bbci.co.uk/sport/football/rss.xml", "BBC Sport"),
]

def parse_rss(url, source_name):
    """Парсить RSS стрічку і повертає список новин."""
    items = []
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return items
        # Простий XML парсинг без зовнішніх бібліотек
        content = r.text
        entries = re.findall(r'<item>(.*?)</item>', content, re.DOTALL)
        for entry in entries[:10]:  # максимум 10 з кожного джерела
            title = re.search(r'<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>', entry, re.DOTALL)
            link  = re.search(r'<link>(.*?)</link>|<link\s[^>]*href="([^"]+)"', entry, re.DOTALL)
            desc  = re.search(r'<description><!\[CDATA\[(.*?)\]\]></description>|<description>(.*?)</description>', entry, re.DOTALL)
            pub   = re.search(r'<pubDate>(.*?)</pubDate>', entry)

            title_text = (title.group(1) or title.group(2) or "").strip() if title else ""
            link_url   = (link.group(1) or link.group(2) or "").strip() if link else ""
            desc_text  = re.sub(r'<[^>]+>', '', (desc.group(1) or desc.group(2) or "").strip()) if desc else ""
            pub_text   = pub.group(1).strip() if pub else ""

            if title_text:
                items.append({
                    "title": title_text,
                    "link": link_url,
                    "description": desc_text[:500],
                    "pubDate": pub_text,
                    "source": source_name,
                })
    except Exception as e:
        print(f"⚠️ RSS {source_name}: {e}", flush=True)
    return items


def is_rss_recent(pub_date_str, hours=6):
    """Перевіряє чи новина свіжа (не старіша за N годин)."""
    if not pub_date_str:
        return True
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(pub_date_str)
        now = datetime.now(tz=dt.tzinfo)
        return (now - dt).total_seconds() < hours * 3600
    except Exception:
        return True


def fetch_article_text(url):
    """Спробувати отримати повний текст статті."""
    if not url:
        return ""
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return ""
        # Витягуємо параграфи з HTML
        text = r.text
        paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', text, re.DOTALL)
        clean = []
        for p in paragraphs:
            p = re.sub(r'<[^>]+>', '', p).strip()
            if len(p) > 50:  # ігноруємо короткі службові параграфи
                clean.append(p)
        return "\n\n".join(clean[:20])  # максимум 20 параграфів
    except Exception:
        return ""


def run_rss():
    """Перевіряє RSS і публікує нові статті через Telegraph."""
    total = 0
    for feed_url, source in RSS_FEEDS:
        items = parse_rss(feed_url, source)
        for item in items:
            rss_id = "rss_" + str(abs(hash(item["link"] or item["title"])))
            if rss_id in published_ids:
                continue
            if not is_rss_recent(item["pubDate"], hours=6):
                continue

            title_ua = translate(item["title"])

            # Намагаємось отримати повний текст статті
            full_raw = fetch_article_text(item["link"]) or item["description"]
            full_ua = translate(full_raw) if full_raw else ""

            # Публікуємо в Telegraph
            telegraph_url = None
            if full_ua:
                telegraph_url = publish_to_telegraph(
                    title=title_ua or item["title"],
                    text=full_ua,
                )

            # Короткий пост для Telegram
            preview = full_ua[:400].rsplit(" ", 1)[0] + "…" if len(full_ua) > 400 else full_ua
            post = f"📰 <b>{title_ua}</b>\n"
            post += f"🌐 {source}\n"
            if preview:
                post += f"\n{preview}"

            if send_telegram(post, telegraph_url=telegraph_url):
                published_ids[rss_id] = datetime.now().strftime("%Y-%m-%d")
                total += 1
                time.sleep(3)

    if total:
        save_published_ids(published_ids)
        print(f"📰 RSS опубліковано: {total}", flush=True)
    return total


# ========== РОЗКЛАД МАТЧІВ ==========

SCHEDULE_LEAGUES = {
    2: "🏆 Ліга Чемпіонів",
    5: "🥈 Ліга Європи",
    8: "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Прем'єр-ліга",
    82: "🇪🇸 Ла Ліга",
    564: "🇩🇪 Бундесліга",
    384: "🇮🇹 Серія А",
    301: "🇫🇷 Ліг 1",
    9: "🇵🇹 Прімейра",
}

def get_todays_schedule():
    """Отримує і публікує розклад матчів на сьогодні (раз на добу о 8:00 Київ)."""
    now_kyiv = datetime.now(KYIV_TZ)
    today = now_kyiv.strftime("%Y-%m-%d")
    schedule_id = f"schedule_{today}"
    if schedule_id in published_ids:
        return 0

    # Публікуємо тільки якщо зараз 8:00-9:00 за Києвом
    if now_kyiv.hour != 8:
        return 0

    fixtures = get_fixtures_with_news(today)
    if not fixtures:
        return 0

    # Групуємо по лігах (тільки ті що нас цікавлять)
    by_league = {}
    for f in fixtures:
        lid = f.get("league_id")
        if lid not in SCHEDULE_LEAGUES:
            continue
        if lid not in by_league:
            by_league[lid] = []
        by_league[lid].append(f)

    if not by_league:
        return 0

    lines = [f"📅 <b>Розклад матчів на {now_kyiv.strftime('%d.%m.%Y')}</b>\n"]
    for lid in PRIORITY_LEAGUES:
        if lid not in by_league:
            continue
        lines.append(f"\n{SCHEDULE_LEAGUES[lid]}")
        for f in by_league[lid]:
            name = f.get("name", "")
            sa = f.get("starting_at", "")
            time_str = to_kyiv_str(sa) if sa else "?"
            # Показуємо лише час (не дату)
            time_only = time_str.split(" ")[-1] if " " in time_str else time_str
            lines.append(f"  ⚽ {name} — {time_only} за Києвом")

    post = "\n".join(lines)
    if send_telegram(post):
        published_ids[schedule_id] = today
        save_published_ids(published_ids)
        print(f"📅 Розклад опубліковано", flush=True)
        return 1
    return 0


# ========== ТАБЛИЦІ ЛІГ ==========

STANDINGS_LEAGUES = [8, 82, 564, 384, 301]  # АПЛ, Ла Ліга, Бундесліга, Серія А, Ліг 1

def get_league_standings(league_id):
    """Отримує поточну таблицю ліги."""
    try:
        r = requests.get(
            f"https://api.sportmonks.com/v3/football/standings/latest/{league_id}",
            params={"api_token": SPORTMONKS_TOKEN, "include": "participant"},
            timeout=15
        )
        if r.status_code == 200:
            return r.json().get("data", [])
    except Exception as e:
        print(f"⚠️ Standings {league_id}: {e}", flush=True)
    return []


def run_standings():
    """Публікує таблиці топ-ліг (раз на тиждень у понеділок о 10:00 Київ)."""
    now_kyiv = datetime.now(KYIV_TZ)
    # Тільки понеділок (weekday=0) о 10:00
    if now_kyiv.weekday() != 0 or now_kyiv.hour != 10:
        return 0

    week_id = f"standings_{now_kyiv.strftime('%Y-W%W')}"
    if week_id in published_ids:
        return 0

    league_names = {
        8: "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Прем'єр-ліга",
        82: "🇪🇸 Ла Ліга",
        564: "🇩🇪 Бундесліга",
        384: "🇮🇹 Серія А",
        301: "🇫🇷 Ліг 1",
    }

    published_any = False
    for lid in STANDINGS_LEAGUES:
        standings = get_league_standings(lid)
        if not standings:
            continue

        top10 = sorted(standings, key=lambda x: x.get("position", 99))[:10]
        lines = [f"📊 <b>Таблиця — {league_names.get(lid, '')}</b>\n"]
        for row in top10:
            pos  = row.get("position", "?")
            name = (row.get("participant") or {}).get("name", row.get("team_name", "?"))
            pts  = row.get("points", "?")
            won  = row.get("won", 0)
            draw = row.get("draw", 0)
            lost = row.get("lost", 0)
            lines.append(f"{pos}. {name} — {pts} очок ({won}П {draw}Н {lost}П)")

        post = "\n".join(lines)
        if send_telegram(post):
            published_any = True
            time.sleep(3)

    if published_any:
        published_ids[week_id] = now_kyiv.strftime("%Y-%m-%d")
        save_published_ids(published_ids)
        print(f"📊 Таблиці опубліковано", flush=True)
        return 1
    return 0


# ========== СТАТИСТИКА БОМБАРДИРІВ ==========

def run_top_scorers():
    """Публікує топ бомбардирів (раз на тиждень у п'ятницю о 12:00 Київ)."""
    now_kyiv = datetime.now(KYIV_TZ)
    # Тільки п'ятниця (weekday=4) о 12:00
    if now_kyiv.weekday() != 4 or now_kyiv.hour != 12:
        return 0

    week_id = f"scorers_{now_kyiv.strftime('%Y-W%W')}"
    if week_id in published_ids:
        return 0

    league_names = {8: "🏴󠁧󠁢󠁥󠁮󠁧󠁿 АПЛ", 82: "🇪🇸 Ла Ліга", 564: "🇩🇪 Бундесліга"}
    published_any = False

    for lid, lname in league_names.items():
        try:
            r = requests.get(
                f"https://api.sportmonks.com/v3/football/topscorers/season/latest/{lid}",
                params={"api_token": SPORTMONKS_TOKEN, "include": "player;participant", "per_page": 10},
                timeout=15
            )
            if r.status_code != 200:
                continue
            scorers = r.json().get("data", [])
            if not scorers:
                continue

            lines = [f"⚽ <b>Топ бомбардири — {lname}</b>\n"]
            for i, s in enumerate(scorers[:10], 1):
                player = (s.get("player") or {}).get("display_name") or (s.get("player") or {}).get("name", "?")
                team   = (s.get("participant") or {}).get("name", "")
                goals  = s.get("total", s.get("goals", "?"))
                lines.append(f"{i}. {player} ({team}) — {goals} голів")

            post = "\n".join(lines)
            if send_telegram(post):
                published_any = True
                time.sleep(3)
        except Exception as e:
            print(f"⚠️ TopScorers {lid}: {e}", flush=True)

    if published_any:
        published_ids[week_id] = now_kyiv.strftime("%Y-%m-%d")
        save_published_ids(published_ids)
        print(f"⚽ Бомбардири опубліковано", flush=True)
        return 1
    return 0



def run_all():
    global published_ids, _fixture_cache
    print(f"\n{'='*40}", flush=True)
    print(f"Запуск: {datetime.now().strftime('%Y-%m-%d %H:%M')}", flush=True)

    # Очищаємо кеш fixture на кожен запуск
    _fixture_cache = {}

    if not published_ids:
        published_ids = cleanup_old_ids(load_published_ids())
        print(f"📦 Активних published_ids: {len(published_ids)}", flush=True)

    fresh = _load_from_gist()
    if fresh:
        published_ids.update(fresh)
        print(f"🔄 published_ids оновлено: {len(published_ids)}", flush=True)

    all_news = get_all_news()

    all_news.sort(key=lambda n: (
        PRIORITY_LEAGUES.index(n.get("league_id")) if n.get("league_id") in PRIORITY_LEAGUES
        else len(PRIORITY_LEAGUES)
    ))

    skipped_published = 0
    skipped_empty = 0
    total = 0

    for n in all_news:
        nid = n.get("id")
        if not nid or str(nid) in published_ids:
            skipped_published += 1
            continue

        result = process_news(n)
        if result == 0:
            skipped_empty += 1
        total += result

    print(f"📊 Пропущено: вже опубліковано={skipped_published}, порожні={skipped_empty}", flush=True)
    print(f"Готово. Опубліковано: {total}.", flush=True)

    # Додатковий контент
    run_rss()
    get_todays_schedule()
    run_standings()
    run_top_scorers()


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
