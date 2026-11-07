#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""habr_bookmarks.py — закладки Habr (kek/v2) с периодами + сохранение статей в Obsidian.

API (по мотивам GVCoder09/HabrExporter, публичный недокументированный API):
  GET https://habr.com/kek/v2/articles/?user={alias}&user_bookmarks=true&page=N&perPage=100
      → {pagesCount, publicationRefs: {id: {...}}}
  GET https://habr.com/kek/v2/articles/{id}/  → textHtml (полная статья)

Фильтра дат на сервере нет — фильтруем на клиенте по timePublished.
Сохранение: заметка Obsidian в активном vault (ИИ/Habr), YAML frontmatter.

Безопасность (SSRF-гвард): только https://habr.com, резолв-проверка публичных IP,
редиректы запрещены, лимит размера ответа.
"""
import base64
import html as htmllib
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HABR = "https://habr.com"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"}
VAULT_DIR = Path("/run/media/rover/BACK/GoogleDrive/Obsidian/ИИ/Habr")
VAULT_NAME = "Obsidian"
DNS_CACHE = {}
MAX_BYTES = 20 * 1024 * 1024


def _habr_url_ok(url: str) -> bool:
    """https + host habr.com + все резолвы — публичные IP (анти-SSRF)."""
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https" or p.hostname != "habr.com" or p.username or p.password:
        return False
    try:
        ips = DNS_CACHE.get(p.hostname)
        if ips is None:
            infos = socket.getaddrinfo(p.hostname, 443, proto=socket.IPPROTO_TCP)
            ips = [i[4][0] for i in infos]
            DNS_CACHE[p.hostname] = ips
        for ip in ips:
            a = ipaddress.ip_address(ip)
            if (a.is_private or a.is_loopback or a.is_link_local
                    or a.is_reserved or a.is_multicast or not a.is_global):
                return False
        return True
    except Exception:
        return False


def _habr_get_json(path_qs: str, timeout: int = 30):
    """GET JSON с гвардом; редиректы запрещены (urllib не идёт за 30x без опций)."""
    url = HABR + path_qs
    if not _habr_url_ok(url):
        raise ValueError(f"Запрещённый адрес: {url}")
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("Ответ больше лимита")
        return json.loads(raw.decode("utf-8", "replace"))


def _strip_html(html: str, limit: int = 0) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = htmllib.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return text[:limit] if limit else text


IMG_HOSTS = ("habrastorage.org", "habr.com")
IMG_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
            ".bmp": "image/bmp"}


def _img_mime(url: str, content_type: str = "") -> str:
    path = urllib.parse.urlsplit(url).path.lower()
    for ext, mime in IMG_MIME.items():
        if ext in path:
            return mime
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct.startswith("image/"):
        return ct
    return "image/png"


def embed_images(html: str, limit: int = 30, per_image_mb: float = 8,
                 total_mb: float = 40) -> dict:
    """Скачать картинки статьи и вернуть {url: 'data:mime;base64,...'} для вставки в .md.

    Гвард: только https и хосты из IMG_HOSTS; лимит размера на картинку и на сумму.
    """
    mapping = {}
    seen = set()
    total = 0
    for url in re.findall(r'<img\s[^>]*src="([^"]+)"', html or ""):
        if url in seen or len(mapping) >= limit:
            continue
        seen.add(url)
        p = urllib.parse.urlsplit(url)
        if p.scheme != "https" or p.hostname not in IMG_HOSTS:
            continue
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as resp:
                ct = resp.headers.get("Content-Type", "")
                data = resp.read(int(per_image_mb * 1024 * 1024) + 1)
            if len(data) > int(per_image_mb * 1024 * 1024):
                continue
            total += len(data)
            if total > int(total_mb * 1024 * 1024):
                break
            mime = _img_mime(url, ct)
            b64 = base64.b64encode(data).decode("ascii")
            mapping[url] = f"data:{mime};base64,{b64}"
        except Exception:
            continue
    return mapping


def html_to_markdown(html: str, img_map: dict = None) -> str:
    """Конверсия textHtml → markdown; картинки → wiki-ссылки на локальные файлы."""
    s = html or ""
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", s)
    for lvl in range(6, 0, -1):
        s = re.sub(
            rf"(?is)<h{lvl}(?:\s[^>]*)?>(.*?)</h{lvl}>",
            lambda m, l=lvl: "\n" + "#" * l + " " + re.sub(r"<[^>]+>", "", m.group(1)).strip() + "\n",
            s,
        )
    s = re.sub(
        r"(?is)<pre(?:\s[^>]*)?><code(?:\s[^>]*)?>(.*?)</code></pre>",
        lambda m: "\n```\n" + htmllib.unescape(m.group(1)).strip() + "\n```\n",
        s,
    )
    s = re.sub(r"(?is)<code(?:\s[^>]*)?>(.*?)</code>", lambda m: "`" + htmllib.unescape(m.group(1)) + "`", s)
    s = re.sub(
        r"(?is)<a\s[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>",
        lambda m: f"[{re.sub(r'<[^>]+>', '', m.group(2)).strip()}]({m.group(1)})",
        s,
    )
    img_map = img_map or {}

    def _img_repl(m):
        url = m.group(1)
        data_uri = img_map.get(url)
        if data_uri:
            return f"![image]({data_uri})"
        return ""  # недоступную картинку не оставляем как битую ссылку

    s = re.sub(r"(?is)<img\s[^>]*src=\"([^\"]+)\"[^>]*>", _img_repl, s)
    s = re.sub(r"(?is)<li(?:\s[^>]*)?>", "- ", s)
    s = re.sub(r"(?is)</t[dh]>", " | ", s)
    s = re.sub(r"(?is)</tr>", "\n", s)
    s = re.sub(r"(?is)<br\s*/?>", "\n", s)
    s = re.sub(r"(?is)</p>", "\n\n", s)
    s = re.sub(r"(?is)<(b|strong)>(.*?)</\1>", r"**\2**", s, flags=re.S)
    s = re.sub(r"(?is)<(i|em)>(.*?)</\1>", r"*\2*", s, flags=re.S)
    s = re.sub(r"(?is)<blockquote(?:\s[^>]*)?>", "> ", s)
    s = re.sub(r"(?is)<[^>]+>", "", s)
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s


def fetch_bookmarks(user: str, max_pages: int = 25) -> list:
    """Все закладки пользователя (list of dict, как вернул API)."""
    out = []
    page = 1
    pages_count = None
    while True:
        d = _habr_get_json(
            f"/kek/v2/articles/?user={urllib.parse.quote(user)}"
            f"&user_bookmarks=true&page={page}&perPage=100"
        )
        refs = d.get("publicationRefs") or {}
        out.extend(refs.values() if isinstance(refs, dict) else [])
        pages_count = d.get("pagesCount") or pages_count or 1
        if page >= min(pages_count, max_pages):
            break
        page += 1
    return out


def filter_period(items: list, days: int) -> list:
    """Только статьи, опубликованные за последние days дней (0 = без фильтра)."""
    if days <= 0:
        return items
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    for it in items:
        tp = it.get("timePublished")
        if not tp:
            continue
        try:
            if datetime.fromisoformat(tp) > cutoff:
                out.append(it)
        except ValueError:
            continue
    out.sort(key=lambda x: x.get("timePublished") or "", reverse=True)
    return out


def search_in(items: list, query: str) -> list:
    """Grep по заголовку+lead: все слова запроса (регистронезависимо)."""
    if not query:
        return items
    words = [w.lower() for w in re.split(r"\W+", query) if len(w) > 2]
    if not words:
        return items
    out = []
    for it in items:
        hay = " ".join([
            it.get("titleHtml") or "",
            (it.get("leadData") or {}).get("textHtml") or "",
        ]).lower()
        if all(w in hay for w in words):
            out.append(it)
    return out


def present(items: list, limit: int = 15) -> str:
    """Формат вывода: заголовок, метаданные, суммари 5-10 строк, хинт закачки."""
    if not items:
        return ("❌ Ничего не найдено. Измените период (days), слова запроса "
                "или проверьте ник (закладки должны быть публичными).")
    lines = [
        f"📎 Найдено закладок: {len(items)} (показаны {min(limit, len(items))})",
        "📥 Скачать: скажите «скачай №N» или habr_save(id=\"<id>\")",
    ]
    for i, it in enumerate(items[:limit], 1):
        title = re.sub(r"<[^>]+>", "", it.get("titleHtml") or "").strip()
        lead_html = (it.get("leadData") or {}).get("textHtml") or ""
        lead = _strip_html(lead_html)
        if len(lead) > 500:
            lead = lead[:500].rsplit(" ", 1)[0] + "…"
        st = it.get("statistics") or {}
        hubs = [h.get("title") for h in (it.get("hubs") or []) if h.get("title")][:3]
        date = (it.get("timePublished") or "")[:10]
        lines.append(
            f"\n**{i}. {title}**\n"
            f"   id: {it.get('id')} | {date} | ⭐ {st.get('score', '?')} | "
            f"💬 {st.get('commentsCount', '?')} | 🔖 {st.get('favoritesCount', '?')} | "
            f"{it.get('readingTime', '?')} мин"
            + (f" | хабы: {', '.join(hubs)}" if hubs else "")
            + f"\n   Суммари: {lead or '(интро отсутствует)'}\n"
            + f"   📥 Скачать в Obsidian: habr_save(id=\"{it.get('id')}\")"
        )
    return "\n".join(lines)


def _safe_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|#^\[\]]', "", name).strip()
    return re.sub(r"\s+", " ", name)[:90]


def save_article(article_id: str, note_dir: Path = VAULT_DIR) -> dict:
    """Скачать статью (kek/v2) и сохранить заметкой Obsidian."""
    art = _habr_get_json(f"/kek/v2/articles/{article_id}/")
    if art.get("errorCode") or art.get("httpCode") == 404:
        return {"error": f"Статья {article_id} недоступна: {art.get('message', 'not found')}"}

    title = re.sub(r"<[^>]+>", "", art.get("titleHtml") or "").strip()
    raw_html = art.get("textHtml") or ""
    img_map = embed_images(raw_html)
    md_body = html_to_markdown(raw_html, img_map)
    lead = _strip_html((art.get("leadData") or {}).get("textHtml") or "", limit=700)
    author = (art.get("author") or {}).get("alias") or ""
    st = art.get("statistics") or {}
    hubs = [h.get("title") for h in (art.get("hubs") or []) if h.get("title")]
    tags = [t.get("title") for t in (art.get("tags") or []) if t.get("title")]
    date_pub = (art.get("timePublished") or "")[:10]
    today = datetime.now().strftime("%Y-%m-%d")

    fname = f"{today} — {_safe_filename(title)}.md"
    note_dir.mkdir(parents=True, exist_ok=True)
    path = note_dir / fname

    fm = [
        "---",
        f'title: "{title.replace(chr(34), chr(39))}"',
        f"source: {HABR}/ru/articles/{article_id}/",
        f"habr_id: {article_id}",
        f"author: {author}",
        f"published: {date_pub}",
        f"saved: {today}",
        "type: habr-статья",
        f"score: {st.get('score', '')}",
        f"readingTime: {art.get('readingTime', '')}",
        f"tags: [{', '.join((tags or hubs)[:8] + ['habr'])}]",
        "matched: []",
        "---",
        "",
        f"# {title}",
        "",
        "> [!info] Источник",
        f"> [{title}]({HABR}/ru/articles/{article_id}/) — {author}, {date_pub}. "
        f"⭐ {st.get('score', '?')}, чтение ~{art.get('readingTime', '?')} мин.",
        "",
    ]
    if lead:
        fm += ["## Кратко", lead, ""]
    fm += ["## Текст статьи", "", md_body, ""]
    path.write_text("\n".join(fm), encoding="utf-8", errors="replace")

    rel = f"ИИ/Habr/{fname}"
    obsidian_uri = f"obsidian://open?vault={urllib.parse.quote(VAULT_NAME)}&file={urllib.parse.quote(rel)}"
    return {
        "id": article_id,
        "title": title,
        "note_path": str(path),
        "vault_relative": rel,
        "obsidian_uri": obsidian_uri,
        "summary": lead[:500],
        "size_chars": len(md_body),
    }


if __name__ == "__main__":
    import sys
    user = sys.argv[1] if len(sys.argv) > 1 else "roverseti"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    q = sys.argv[3] if len(sys.argv) > 3 else ""
    items = fetch_bookmarks(user)
    print("всего закладок:", len(items))
    recent = filter_period(items, days)
    print("за", days, "дней:", len(recent))
    if q:
        recent = search_in(recent, q)
        print("по запросу", repr(q), ":", len(recent))
    print(present(recent, limit=5)[:3000])
