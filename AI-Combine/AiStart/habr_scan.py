#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
habr_scan.py — сканер Habr: ключевые слова → ИИ-конспект → Obsidian.

Как работает:
  1. Читает RSS-ленты Habr (свежие статьи с заголовками и тегами)
  2. Отбирает статьи по ключевым словам из habr_config.json
  3. Загружает полный текст статьи, составляет конспект через
     локальную модель (роутер :8081, по умолчанию qwen3.5-9b)
  4. Пишет markdown-заметку с frontmatter в папку хранилища Obsidian
  5. Индексирует заметку эмбеддингом (gte-modernbert :8082)
     для семантического поиска (ask_kb.py)

Запуск:  .venv-tools/bin/python habr_scan.py [--limit N]
Требует: запущенный комбайн (./start_ai.sh) и смонтированный диск хранилища.
"""
import argparse
import html as htmllib
import ipaddress
import json
import os
import re
import socket
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).resolve().parent
CONFIG = json.loads((BASE / "habr_config.json").read_text(encoding="utf-8"))

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"}
STATE_FILE = BASE / CONFIG["state_file"]
INDEX_FILE = BASE / CONFIG["index_file"]
NOTES_DIR = Path(CONFIG["vault_notes_dir"])

# --- Сетевая защита (SSRF-гвард) -------------------------------------------
# Все внешние запросы этого модуля идут только на https://habr.com.
# Гвард: https + точный хост + резолв в публичные IP + лимит размера +
# ручной цикл редиректов с проверкой хоста на каждом шаге.
HABR_ORIGIN = "https://habr.com"
MAX_BYTES = 10 * 1024 * 1024
_public_dns_cache: dict = {}


def _habr_url_ok(url: str) -> bool:
    """https + хост habr.com + все резолвы хоста — публичные IP."""
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https" or p.hostname != "habr.com" or p.username or p.password:
        return False
    try:
        ips = _public_dns_cache.get(p.hostname)
        if ips is None:
            infos = socket.getaddrinfo(p.hostname, 443, proto=socket.IPPROTO_TCP)
            ips = [i[4][0] for i in infos]
            _public_dns_cache[p.hostname] = ips
        for ip in ips:
            addr = ipaddress.ip_address(ip)
            if (addr.is_private or addr.is_loopback or addr.is_link_local
                    or addr.is_reserved or addr.is_multicast or not addr.is_global):
                return False
        return True
    except Exception:
        return False


def _habr_get(url: str) -> "requests.Response":
    """GET с гвардом: проверка URL, ручные редиректы same-host, лимит размера."""
    if not _habr_url_ok(url):
        raise ValueError(f"Запрещённый адрес: {url}")
    resp = requests.get(url, timeout=30, headers=UA, allow_redirects=False)
    for _ in range(3):  # до 3 редиректов, каждый — same-host https
        if resp.is_redirect or resp.is_permanent_redirect:
            loc = resp.headers.get("Location", "")
            nxt = urllib.parse.urljoin(url, loc)
            if not _habr_url_ok(nxt):
                raise ValueError(f"Редирект на запрещённый адрес: {nxt}")
            url = nxt
            resp = requests.get(url, timeout=30, headers=UA, allow_redirects=False)
        else:
            break
    resp.raise_for_status()
    if len(resp.content) > MAX_BYTES:
        raise ValueError(f"Ответ больше {MAX_BYTES // (1024*1024)} МБ: {url}")
    return resp


def _parse_rss(xml_text: str):
    """Безопасный парсинг RSS: отсечь DOCTYPE/ENTITY, затем stdlib ET."""
    head = xml_text[:1000].lower()
    if "<!doctype" in head or "<!entity" in head:
        raise ValueError("XML с DOCTYPE/ENTITY отклонён")
    if len(xml_text) > MAX_BYTES:
        raise ValueError("XML больше лимита")
    return ET.fromstring(xml_text)


# --- конец блока сетевой защиты ---------------------------------------------

SUMMARY_SYSTEM = (
    "Ты — ассистент базы знаний. Из статьи Habr составь КОHСПЕКТ на русском в markdown:\n"
    "1. **Резюме** — 2-3 предложения, о чём статья.\n"
    "2. **Ключевые тезисы** — маркированный список (5-8 пунктов).\n"
    "3. **Код и команды** — важные фрагменты кода/команд из статьи в блоках ``` (если есть).\n"
    "4. **Практическая польза** — 1-2 предложения: когда это применять.\n"
    "Пиши только по содержимому статьи, выдумывать нельзя."
)


def log(msg: str) -> None:
    print(msg, flush=True)


def check_services() -> bool:
    """Комбайн должен быть запущен: роутер обязателен, эмбеддер желателен."""
    ok = True
    try:
        requests.get(f"{CONFIG['router_api']}/models", timeout=5, headers=UA).raise_for_status()
    except Exception as e:
        log(f"❌ Роутер недоступен ({e})")
        log("   Запустите комбайн: /home/rover/AiStart/start_ai.sh")
        ok = False
    try:
        requests.get(f"{CONFIG['embed_api']}/models", timeout=5, headers=UA).raise_for_status()
    except Exception:
        log("⚠️  Эмбеддер недоступен — заметки будут без семантического индекса")
    return ok


def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def fetch_feeds() -> list:
    """Все <item> из всех лент (уникальные по id статьи)."""
    items, seen = [], set()
    for feed in CONFIG["feeds"]:
        try:
            p = urllib.parse.urlsplit(feed)
            if p.scheme != "https" or p.hostname != "habr.com":
                log(f"⚠️  Лента {feed}: запрещённый хост, пропуск")
                continue
            r = _habr_get(feed)
            root = _parse_rss(r.text)
            for it in root.find("channel").findall("item"):
                link = it.findtext("link", "")
                m = re.search(r"/articles/(\d+)", link)
                if not m:
                    continue
                aid = m.group(1)
                if aid in seen:
                    continue
                seen.add(aid)
                cats = [c.text for c in it.findall("category") if c.text]
                items.append({
                    "id": aid,
                    "title": htmllib.unescape(it.findtext("title", "").strip()),
                    "link": f"https://habr.com/ru/articles/{aid}/",
                    "description": re.sub(r"<[^>]+>", " ", it.findtext("description", "")),
                    "categories": cats,
                    "author": it.findtext("creator", ""),
                    "pubDate": it.findtext("pubDate", ""),
                })
        except Exception as e:
            log(f"⚠️  Лента {feed}: {e}")
    return items


def habr_rss_search(query: str, limit: int = 8, order_by: str = "relevance") -> list:
    """Поиск по ВСЕМУ habr.com через штатный RSS-поиск (не только свежее/ИИ).

    Возвращает список dict: {id, title, link, author, pubDate, categories, summary}.
    Query — любые слова/фраза, как в поиске на сайте; каждое слово ищется
    независимо; order_by: relevance|date|rank.
    """
    q = query.strip()
    if not q:
        return []
    params = urllib.parse.urlencode({
        "q": q,
        "order_by": order_by if order_by in ("relevance", "date", "rank") else "relevance",
        "fl": "ru",
    })
    url = HABR_ORIGIN + "/ru/rss/search/?" + params
    try:
        r = _habr_get(url)
        root = _parse_rss(r.text)
        channel = root.find("channel")
        if channel is None:
            return []
        out = []
        for it in channel.findall("item")[: max(1, min(limit, 20))]:
            link = it.findtext("link", "").split("?")[0]
            m = re.search(r"/articles/(\d+)", link)
            aid = m.group(1) if m else ""
            out.append({
                "id": aid,
                "title": htmllib.unescape(it.findtext("title", "").strip()),
                "link": link,
                "author": it.findtext("creator", ""),
                "pubDate": it.findtext("pubDate", ""),
                "categories": [c.text for c in it.findall("category") if c.text],
                "summary": re.sub(r"<[^>]+>", " ", it.findtext("description", "")).strip()[:400],
            })
        return out
    except Exception as e:
        log(f"⚠️  RSS-поиск «{q}»: {e}")
        return []


def match_keywords(item: dict) -> list:
    """Список сработавших keywords из конфига (регистронезависимо, по подстроке)."""
    haystack = " ".join([
        item["title"], " ", " ".join(item["categories"]), " ", item["description"]
    ]).lower()
    return [k for k in CONFIG["keywords"] if k.lower() in haystack]


def extract_article(url: str) -> dict:
    """Полный текст статьи + код-блоки со страницы (через гвард _habr_get)."""
    r = _habr_get(url)
    soup = BeautifulSoup(r.text, "html.parser")
    body = soup.select_one("div.article-body") or soup.find("article") or soup.body

    for tag in body(["script", "style", "nav", "aside"]):
        tag.decompose()

    code_blocks = [pre.get_text("\n").strip() for pre in body.find_all("pre")]
    text = re.sub(r"\n{3,}", "\n\n", body.get_text("\n")).strip()
    return {"text": text, "code": [c for c in code_blocks if c][:10]}


def summarize(title: str, text: str, code_blocks: list) -> str:
    """Конспект через локальную модель. Возвращает markdown или ''."""
    article = text[: CONFIG["article_char_limit"]]
    if code_blocks:
        article += "\n\nКод из статьи:\n" + "\n\n".join(f"```\n{c[:1500]}\n```" for c in code_blocks[:5])
    payload = {
        "model": CONFIG["model_summarize"],
        "messages": [
            {"role": "system", "content": SUMMARY_SYSTEM},
            {"role": "user", "content": f"Статья: «{title}»\n\n{article}"},
        ],
        "max_tokens": 1400,
        "temperature": 0.3,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    r = requests.post(f"{CONFIG['router_api']}/chat/completions",
                      json=payload, timeout=600, headers=UA)
    r.raise_for_status()
    msg = r.json()["choices"][0]["message"]
    content = (msg.get("content") or "").strip()
    if not content:  # «думающая» модель потратила лимит на размышления
        reasoning = (msg.get("reasoning_content") or "").strip()
        if reasoning:
            content = "*Конспект составлен из цепочки рассуждений модели (лимит токенов исчерпан до финального ответа):*\n\n" + reasoning[-3000:]
    return content


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|#^\[\]]', "", name).strip()
    return re.sub(r"\s+", " ", name)[:80]


def write_note(item: dict, matched: list, summary: str, article_text: str) -> Path:
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    fname = f"{today} — {sanitize_filename(item['title'])}.md"
    path = NOTES_DIR / fname
    tags = sorted(set(item["categories"]) | set(matched) | {"habr"})
    front = [
        "---",
        f'title: "{item["title"].replace(chr(34), chr(39))}"',
        f"source: {item['link']}",
        f"author: {item['author']}",
        f"published: {item['pubDate']}",
        f"created: {today}",
        "type: habr-конспект",
        f"tags: [{', '.join(tags)}]",
        "matched: [" + ", ".join(matched) + "]",
        "---",
        "",
    ]
    body = front + [
        f"# {item['title']}",
        "",
        summary if summary else "*(конспект не получен — начало текста статьи)*\n\n" + article_text[:600],
        "",
        "## Оригинал",
        f"[{item['title']}]({item['link']}) — {item['author']}",
        "",
        f"> Конспект составлен локальной моделью `{CONFIG['model_summarize']}` {today}.",
        "",
    ]
    path.write_text("\n".join(body), encoding="utf-8")
    return path


def embed(text: str):
    """Вектор текста или None, если эмбеддер недоступен."""
    try:
        r = requests.post(f"{CONFIG['embed_api']}/embeddings",
                          json={"model": CONFIG["embed_model"], "input": text[:4000]},
                          timeout=120, headers=UA)
        r.raise_for_status()
        return [round(x, 6) for x in r.json()["data"][0]["embedding"]]
    except Exception:
        return None


def reindex() -> int:
    """Перестроить поисковый индекс по всем .md-заметкам в папке Obsidian."""
    if not check_services():
        return 1
    notes = sorted(NOTES_DIR.glob("*.md")) if NOTES_DIR.exists() else []
    if not notes:
        log(f"📭 В {NOTES_DIR} заметок нет — сначала обычный прогон сканера.")
        return 1
    index = []
    for note in notes:
        text = note.read_text(encoding="utf-8")
        title = note.stem
        url, tags = "", []
        m = re.search(r"^source:\s*(\S+)", text, re.M)
        if m:
            url = m.group(1)
        t = re.search(r"^tags:\s*\[(.*)\]", text, re.M)
        if t:
            tags = [x.strip() for x in t.group(1).split(",") if x.strip()]
        body = text.split("---", 2)[-1] if text.startswith("---") else text
        if "## Оригинал" in body:
            body = body.split("## Оригинал")[0]
        vec = embed(f"{title}\n{body[:3000]}")
        if vec:
            index.append({"file": str(note), "title": title, "url": url or str(note),
                          "date": "", "tags": tags, "vec": vec})
            log(f"   🔢 {note.name}")
        else:
            log(f"   ⚠️ не удалось индексировать {note.name}")
    save_json(INDEX_FILE, index)
    log(f"✅ Индекс: {len(index)} заметок из {len(notes)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Сканер Habr → конспекты в Obsidian")
    ap.add_argument("--limit", type=int, default=CONFIG["max_articles_per_run"],
                    help="максимум статей за прогон")
    ap.add_argument("--dry-run", action="store_true", help="только показать совпадения")
    ap.add_argument("--reindex", action="store_true",
                    help="переиндексировать все заметки в папке Obsidian (поиск по базе)")
    args = ap.parse_args()

    if args.reindex:
        return reindex()

    if not check_services():
        return 1
    if not NOTES_DIR.parent.exists():
        log(f"❌ Хранилище не смонтировано: {NOTES_DIR.parent}")
        log("   Подключите диск BACK (хранилище Obsidian) и повторите.")
        return 1

    state = load_json(STATE_FILE, {})
    index = load_json(INDEX_FILE, [])

    items = fetch_feeds()
    log(f"📡 В лентах {len(items)} статей; в базе уже обработано {len(state)}")

    fresh = []
    for it in items:
        if it["id"] in state:
            continue
        matched = match_keywords(it)
        if matched:
            fresh.append((it, matched))

    if not fresh:
        log("✨ Новых статей по ключевым словам нет.")
        return 0

    log(f"🎯 Совпадений: {len(fresh)}:")
    for it, matched in fresh:
        log(f"   • {it['title']}  [{', '.join(matched)}]")

    if args.dry_run:
        return 0

    todo = fresh[: args.limit]
    if len(fresh) > args.limit:
        log(f"⚠️  Обрабатываю первые {args.limit} (лимит прогона), остальные — в следующий раз")

    written = 0
    for it, matched in todo:
        log(f"\n📄 «{it['title']}»")
        try:
            art = extract_article(it["link"])
            time.sleep(CONFIG["request_delay_sec"])
            log(f"   текст: {len(art['text'])} симв., код-блоков: {len(art['code'])}")
            summary = summarize(it["title"], art["text"], art["code"])
            note = write_note(it, matched, summary, art["text"])
            log(f"   📝 заметка: {note.name}")
            vec = embed(f"{it['title']}\n{summary or art['text'][:800]}")
            if vec:
                index.append({"file": str(note), "title": it["title"], "url": it["link"],
                              "date": it["pubDate"], "tags": matched, "vec": vec})
                save_json(INDEX_FILE, index)
                log("   🔢 индексировано для семантического поиска")
            state[it["id"]] = {"title": it["title"], "date": it["pubDate"]}
            save_json(STATE_FILE, state)
            written += 1
        except Exception as e:
            log(f"   ⚠️ ошибка: {e} — статья пропущена, попробуем в следующий раз")
            continue

    log(f"\n✅ Готово: конспектов написано {written}, всего заметок в базе {len(index)}.")
    log("   Определение моделей: спросите базу — ask_kb.py \"ваш вопрос\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
