#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mcp_server.py — MCP-сервер «local-tools» for Open WebUI.

Streamable HTTP на http://127.0.0.1:8089/mcp (без auth, только локаль).
Инструменты: run_command, obsidian_search, obsidian_read, habr_scan,
habr_search, habr_read, kb_search, ask_kb, photo_fix, agent_task,
+ агенты по сложности: ai_ask, ai_agent, ai_status, ai_load, ai_unload
(конфиг ролей — agents.json).

Запуск: /home/rover/OpenWebUI-Local/.venv FastMCP из venv WebUI.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import requests
from mcp.server.mcpserver import MCPServer

BASE = Path(__file__).resolve().parent          # /home/rover/AI-Combine/AiStart
VAULT = Path("/run/media/rover/BACK/GoogleDrive/Obsidian")
ROUTER = "http://127.0.0.1:8081/v1"
EMBED = "http://127.0.0.1:8082/v1"
TOOLS_PY = "/home/rover/AI-Combine/OpenWebUI-Local/.venv/bin/python"  # photo_fix (torch/LaMa)
SCAN_PY = str(BASE / ".venv-tools" / "bin" / "python")          # habr/ask_kb (requests)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# ------------------------------------------------------------ опасные команды
DENY_PATTERNS = [
    r"\bsudo\b", r"\bsu\b\s", r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)*(/|~|\$HOME)\b",
    r"rm\s+-[a-zA-Z]*r[a-zA-Z]*f", r"\bmkfs", r"\bdd\s+if=", r"\bshutdown\b",
    r"\breboot\b", r"\bhalt\b", r"\binit\s+0", r"\bfork\b.*\{.*\};.*\};.*\};",
    r">\s*/dev/sd", r"\bmkfs\.", r"\bwipefs\b", r"\bshred\s+/dev",
    r"chmod\s+-R\s+777\s+/( |$)", r"\bcrontab\b", r"\busermod\b", r"\buserdel\b",
    r"pkill\s+-9\s+-f\s+(llama|open-webui|mcp_server)",
    # --- 21.11: ключи, дотфайлы и типовые обходы чёрного списка ---
    r"\.ssh\b",
    r"(polzakey|grok_key|openrouteai_key|hftoken|huggtoken|ghtoken)",
    r"(>|>>|tee)\s+\S*\.(bashrc|bash_profile|zshrc|zprofile|profile)\b",
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;",
    r"(curl|wget)[^|;]*\|\s*(sudo\s+)?(ba|z|da)?sh\b",
    r"base64\s+(-d|--decode)[^|;]*\|\s*(ba)?sh\b",
    r"shutil\.rmtree",
    r"\bdd\b[^|;]*of=/dev/(sd|nvme|hd|vd|mmcblk)",
    r"\bmv\s+[^;|>]*\s+/dev/null\s*$",
]


def _deny(command: str) -> str | None:
    low = command.lower()
    for pat in DENY_PATTERNS:
        if re.search(pat, low):
            return pat
    return None


mcp = MCPServer(
    "local-tools",
    instructions=(
        "Локальные инструменты компьютера пользователя (Manjaro, RTX 3060). "
        "run_command — выполнение shell-команд (без root). "
        "obsidian_search/obsidian_read — grep-поиск и чтение по хранилищу Obsidian "
        f"({VAULT}); для ЗАПИСИ заметок используй MCP-сервер 'obsidian' (плагин Local REST API). "
        "habr_scan — сканер RSS-ленты по ключевым словам с ИИ-конспектами в Obsidian. "
        "habr_search — поиск по ВСЕМУ сайту habr.com по произвольной фразе (любые слова/темы). "
        "habr_bookmarks — закладки пользователя Habr за период (7/30/90/365 дней): "
        "заголовок + суммари 5-10 строк + статистика; в каждом результате id для скачивания. "
        "habr_save — скачать статью Habr в Obsidian (заметка с картинками, суммари, obsidian://-ссылкой). "
        "habr_fetch_start — ФОНОВОЕ скачивание закладок за период (1-2 часа, с предупреждением "
        "о перезагрузке); habr_fetch_status — прогресс; habr_fetch_resume — продолжить после "
        "перезагрузки (готовое не перекачивается); habr_fetch_log — лог; habr_fetch_stop — пауза. "
        "habr_read — читать полную статью habr.com по ссылке (для конспекта/цитаты). "
        "kb_search — поиск по локальной базе: конспекты Habr + заметки vault Obsidian "
        "(семантический поиск по конспекту и названию, эмбеддер :8082; при недоступности — grep-фолбэк). "
        "ask_kb — вопрос к базе знаний (конспекты Habr) с ходом решения. "
        "photo_fix — реставрация фото: обрезка, поворот, яркость/контраст, шум, "
        "удаление трещин (ИИ-заливка LaMa). Результат пишется в restored/ рядом с оригиналом. "
        "photo_restore_comfy — глубокая ИИ-реставрация старых фото (BOPBTL: царапины, "
        "шум, лица, апскейл UltraSharp; GPU, требует запущенный ComfyUI :8188). "
        "photo_colorize — раскраска ч/б фото и реставрация промптом (Qwen-Image 2.1 Edit; "
        "GPU, требует запущенный ComfyUI :8188). "
        "photo_outpaint — дополнение оторванных краёв фото (Qwen-Edit + маска: "
        "исходная часть не меняется, дорисовываются только поля). "
        "Результаты — в ~/Pictures/comfy или по output. "
        "image_search — поиск референсов-картинок (Openverse CC + NASA, без ключей); "
        "stt_listen — голосовая команда с микрофона (запись N секунд → текст); "
        "vectorize_file — растровое изображение → векторный SVG (vtracer). "
        "agent_task — многошаговая задача: модель сама составляет план и выполняет его "
        "по шагам, вызывая эти же инструменты. "
        "АГЕНТЫ ПО СЛОЖНОСТИ (конфиг agents.json): ai_ask — один проход с авто-выбором "
        "агента по сложности (fast/medium/hard/coder/vision); ai_agent — многошаговый "
        "агент на qwen3.5-9b; ai_status — статус ролей и VRAM; ai_load/ai_unload — "
        "загрузка/выгрузка моделей в VRAM (заранее прогреть или освободить память)."
    ),
)

# ============================================================ терминал


@mcp.tool()
def run_command(command: str, cwd: str = "", timeout: int = 60) -> str:
    """Выполнить shell-команду (bash) на компьютере пользователя.

    Args:
        command: команда bash, например "df -h /home" или "ls ~/Изображения | head -20"
        cwd: рабочий каталог (пусто = домашний)
        timeout: таймаут в секундах, максимум 300
    """
    timeout = max(1, min(int(timeout), 300))
    bad = _deny(command)
    if bad:
        return f"⛔ Команда заблокирована (шаблон '{bad}'). Root и деструктивные операции запрещены."
    workdir = cwd or str(Path.home())
    if not Path(workdir).is_dir():
        return f"⛔ Рабочий каталог не найден: {workdir}"
    try:
        p = subprocess.run(["bash", "-c", command], cwd=workdir,
                           capture_output=True, text=True, timeout=timeout)
        out = (p.stdout or "").strip()
        err = (p.stderr or "").strip()
        if len(out) > 8000:
            out = out[:8000] + f"\n…(обрезано, всего {len(out)} симв.)"
        result = f"код: {p.returncode}"
        if out:
            result += f"\nstdout:\n{out}"
        if err:
            result += f"\nstderr:\n{err[:2000]}"
        return result
    except subprocess.TimeoutExpired:
        return f"⛔ Таймаут {timeout} с — команда прервана"
    except Exception as e:
        return f"⛔ Ошибка: {e}"


# ============================================================ Obsidian (grep-fallback)


def _safe_vault_path(rel: str) -> Path | None:
    p = (VAULT / rel).resolve()
    try:
        p.relative_to(VAULT.resolve())
    except ValueError:
        return None
    return p


@mcp.tool()
def obsidian_search(query: str, folder: str = "", limit: int = 10, is_regex: bool = False) -> str:
    """Поиск по заметкам хранилища Obsidian (быстрый grep, работает без открытого Obsidian).

    Args:
        query: текст или регулярное выражение (без учёта регистра)
        folder: подпапка хранилища для сужения поиска (например "ИИ/Habr")
        limit: максимум результатов (по умолчанию 10)
        is_regex: True если query — регулярное выражение
    """
    limit = max(1, min(limit, 30))
    root = VAULT / folder if folder else VAULT
    if not root.is_dir():
        return f"⛔ Папка не найдена или диск не смонтирован: {root}"
    try:
        if is_regex:
            rx = re.compile(query, re.IGNORECASE)
        else:
            needle = query.lower()
    except re.error as e:
        return f"⛔ Некорректный regex: {e}"
    hits = []
    skip = {".obsidian", ".smart-env", ".git", "_resources", ".trash"}
    for md in root.rglob("*.md"):
        if any(part in skip for part in md.parts):
            continue
        try:
            text = md.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        line_hit = None
        for line in text.splitlines():
            if is_regex:
                if rx.search(line):
                    line_hit = line.strip()
                    break
            elif needle in line.lower():
                line_hit = line.strip()
                break
        if line_hit is not None:
            rel = md.relative_to(VAULT)
            hits.append(f"📄 {rel}\n   {line_hit[:220]}")
            if len(hits) >= limit:
                break
    if not hits:
        return f"Ничего не найдено по «{query}» в {root.name}"
    return f"Найдено {len(hits)}:\n" + "\n".join(hits)


@mcp.tool()
def obsidian_read(path: str, max_chars: int = 6000) -> str:
    """Прочитать заметку из хранилища Obsidian.

    Args:
        path: путь относительно хранилища, например "ИИ/Habr/2026-10-24 — Сможет ли ИИ решить физику.md"
        max_chars: максимум символов в ответе
    """
    p = _safe_vault_path(path)
    if p is None or not p.is_file():
        return f"⛔ Файл не найден в хранилище (или путь вне хранилища): {path}"
    text = p.read_text(encoding="utf-8", errors="ignore")
    if len(text) > max_chars:
        return text[:max_chars] + f"\n…(обрезано, всего {len(text)} симв.)"
    return text


# ============================================================ Векторизация (vtracer)

_ALLOWED_IMG_DIRS = ("/home/rover",)
_IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


@mcp.tool()
def vectorize_file(file_path: str, color_precision: int = 7,
                   filter_speckle: int = 10, layer_difference: int = 24,
                   mode: str = "spline") -> str:
    """Векторизовать растровое изображение в SVG (vtracer, цветная трассировка).

    Рисунки/логотипы/скриншоты → векторные кривые (spline) с палитрой цветов.
    Работает локально, за секунды, без нейросетей.

    Args:
        file_path: абсолютный путь к изображению (.jpg/.jpeg/.png/.webp/.bmp/.tif/.tiff)
        color_precision: точность палитры 1–8 (меньше — меньше цветов)
        filter_speckle: фильтр мусорных пятен 0–100 (выше — чище)
        layer_difference: шаг цветовых слоёв 0–64 (выше — крупнее зоны)
        mode: spline (гладкие кривые) | polygon (полигоны) | none (узлы по контуру)
    """
    import time as _t
    try:
        import vtracer
    except ImportError:
        return "⛔ vtracer не установлен в venv WebUI (uv pip install vtracer)"
    src = Path(file_path).expanduser().resolve()
    if not src.as_posix().startswith(_ALLOWED_IMG_DIRS):
        return "⛔ Файл должен лежать внутри /home/rover"
    if src.suffix.lower() not in _IMG_EXT:
        return f"⛔ Неверное расширение: {src.suffix} (нужно одно из {sorted(_IMG_EXT)})"
    if not src.is_file():
        return f"⛔ Файл не найден: {src}"
    if mode not in ("spline", "polygon", "none"):
        return "⛔ mode: spline | polygon | none"
    out = src.with_suffix(".svg")
    t0 = _t.time()
    try:
        vtracer.convert_image_to_svg_py(
            str(src), str(out), colormode="color", hierarchical="stacked",
            mode=mode, filter_speckle=int(filter_speckle),
            color_precision=int(color_precision),
            layer_difference=int(layer_difference),
            corner_threshold=60, length_threshold=4.0,
            splice_threshold=45, path_precision=2)
    except Exception as e:
        return f"⛔ Ошибка трассировки: {e}"
    dt = _t.time() - t0
    kb = out.stat().st_size // 1024
    return (f"✅ SVG готов за {dt:.1f} с: {out} ({kb} КБ)\n"
            f"Параметры: color_precision={color_precision}, filter_speckle={filter_speckle}, "
            f"layer_difference={layer_difference}, mode={mode}\n"
            f"Подстройка: меньше color_precision — проще палитра; выше filter_speckle — "
            f"меньше мусора; ниже layer_difference — точнее цвета.")


# ============================================================ Habr / база знаний


@mcp.tool()
def habr_scan(dry_run: bool = True, limit: int = 4, reindex: bool = False) -> str:
    """Сканер Habr: новые статьи по ключевым словам → ИИ-конспекты в Obsidian.

    Args:
        dry_run: True = только показать совпадения, ничего не писать
        limit: максимум статей за прогон (конспекты пишутся медленно, ~1-2 мин/статья)
        reindex: перестроить поисковый индекс по заметкам вместо сканирования
    """
    if not (BASE / "habr_config.json").exists():
        return "⛔ habr_config.json не найден"
    cmd = [SCAN_PY, str(BASE / "habr_scan.py")]
    if reindex:
        cmd.append("--reindex")
    else:
        cmd += ["--limit", str(max(1, min(limit, 10)))]
        if dry_run:
            cmd.append("--dry-run")
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=900, cwd=str(BASE))
        out = ANSI_RE.sub("", p.stdout or "")
        return (out + ("\nstderr: " + p.stderr[-500:] if p.returncode != 0 and p.stderr else "")).strip()
    except subprocess.TimeoutExpired:
        return "⛔ Таймаут сканера (15 мин)"
    except Exception as e:
        return f"⛔ {e}"


@mcp.tool()
def ask_kb(question: str, model: str = "", top: int = 5) -> str:
    """Задать вопрос базе знаний (конспекты статей Habr в Obsidian).

    Возвращает ответ модели с пошаговым ходом решения и ссылками на источники.

    Args:
        question: ваш вопрос, например "как настроить CUDA для llama.cpp?"
        model: модель роутера (пусто = qwen2.5-coder-7b; можно qwen3.5-9b-instruct)
        top: сколько заметок подавать в контекст
    """
    cmd = [SCAN_PY, str(BASE / "ask_kb.py"), question]
    if model:
        cmd += ["--model", model]
    cmd += ["--top", str(max(1, min(top, 10)))]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=str(BASE))
        out = ANSI_RE.sub("", p.stdout or "").strip()
        return out if out else f"пустой ответ (код {p.returncode}) {p.stderr[-300:]}"
    except Exception as e:
        return f"⛔ {e}"


# ============================================================ Habr: закладки + сохранение в Obsidian


def _hb_module():
    """Загрузить модуль habr_bookmarks (логика закладок/сохранения)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("habr_bookmarks", str(BASE / "habr_bookmarks.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@mcp.tool()
def habr_bookmarks(days: int = 30, query: str = "", limit: int = 15, user: str = "roverseti") -> str:
    """Закладки habr.com пользователя за период: заголовок + суммари 5-10 строк + статистика.

    Список формируется с хинтом скачивания: «скачай №N» → модель вызовет habr_save(id).

    Args:
        days: период публикации статьи: 7 | 30 | 90 | 365 | 0 (0 = все закладки)
        query: необязательный поиск по заголовку/интро (все слова)
        limit: сколько статей показать (1-30, по умолчанию 15)
        user: alias на habr.com (по умолчанию roverseti)
    """
    try:
        mod = _hb_module()
        items = mod.fetch_bookmarks(user)
        total = len(items)
        items = mod.filter_period(items, max(0, min(days, 3650)))
        if query:
            items = mod.search_in(items, query)
        head = f"📚 Закладки «{user}»: всего {total}" + (
            f", за {days} дн.: {len(items)}" if days > 0 else "")
        return head + "\n" + mod.present(items, max(1, min(limit, 30)))
    except Exception as e:
        return f"⛔ habr_bookmarks: {e}. Проверьте сеть/ник (закладки должны быть публичными)."


@mcp.tool()
def habr_save(article_id: str) -> str:
    """Скачать статью habr.com в Obsidian vault (заметка .md с картинками и суммари).

    Возвращает путь заметки, ссылку obsidian:// и краткое содержание.

    Args:
        article_id: числовой id статьи (например "1086868") или ссылка на неё
    """
    import re as _re
    m = _re.search(r"(\d{4,})", str(article_id))
    if not m:
        return "⛔ Нужен числовой id статьи (habr_save(id=\"1086868\")) или её URL."
    try:
        mod = _hb_module()
        res = mod.save_article(m.group(1))
        if "error" in res:
            return f"⛔ {res['error']}"
        return (
            f"✅ Сохранено в Obsidian: {res['title']}\n"
            f"Заметка: {res['note_path']}\n"
            f"Ссылка: {res['obsidian_uri']}\n"
            f"Объём: {res['size_chars']} символов\n"
            f"Суммари: {res['summary']}\n"
            f"Заметка индексируется в базе знаний при следующем запуске sync_knowledge.py."
        )
    except Exception as e:
        return f"⛔ habr_save: {e}"


# ============================================================ Habr: фоновое скачивание


def _hf_module():
    """Загрузить модуль habr_fetch (очередь фонового скачивания)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("habr_fetch", str(BASE / "habr_fetch.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@mcp.tool()
def habr_fetch_start(days: int = 30, query: str = "", limit: int = 0, user: str = "roverseti") -> str:
    """Запустить ФОНОВОЕ скачивание статей из закладок Habr в Obsidian (может идти 1-2 часа).

    Качает статьи из закладок за период: заметка .md с картинками (base64),
    суммари и obsidian://-ссылкой. Сразу возвращает подтверждение и предупреждение
    о перезагрузке. Уже скачанные статьи не перекачиваются (resume после сбоя).

    Args:
        days: период публикации: 7 | 30 | 90 | 365 | 0 (0 = все закладки)
        query: необязательный фильтр по заголовку/интро (все слова)
        limit: 0 = все за период; N = первые N статей
        user: alias на habr.com (по умолчанию roverseti)
    """
    import argparse as _ap
    try:
        mod = _hf_module()
        args = _ap.Namespace(days=max(0, min(int(days), 3650)),
                             query=str(query or "")[:200],
                             limit=max(0, min(int(limit), 3000)),
                             user=str(user or "roverseti")[:60])
        return mod.cmd_start(args)
    except Exception as e:
        return f"⛔ habr_fetch_start: {e}"


@mcp.tool()
def habr_fetch_status() -> str:
    """Прогресс фонового скачивания Habr: сколько готово, что качается, ETA, жив ли воркер."""
    try:
        return _hf_module().cmd_status()
    except Exception as e:
        return f"⛔ habr_fetch_status: {e}"


@mcp.tool()
def habr_fetch_stop() -> str:
    """Остановить фоновое скачивание Habr (задание станет paused; продолжить — habr_fetch_resume)."""
    try:
        return _hf_module().cmd_stop()
    except Exception as e:
        return f"⛔ habr_fetch_stop: {e}"


@mcp.tool()
def habr_fetch_log(lines: int = 30) -> str:
    """Последние N строк лога фонового скачивания Habr (статьи, ошибки, прогресс)."""
    try:
        return _hf_module().cmd_log(max(5, min(int(lines), 200)))
    except Exception as e:
        return f"⛔ habr_fetch_log: {e}"


@mcp.tool()
def habr_fetch_resume() -> str:
    """Возобновить прерванное фоновое скачивание Habr (после перезагрузки/останова).

    Готовые статьи пропускаются — качается только недостающее.
    """
    import argparse as _ap
    try:
        return _hf_module().cmd_resume(_ap.Namespace())
    except Exception as e:
        return f"⛔ habr_fetch_resume: {e}"


# ============================================================ Habr: поиск по сайту + чтение


def _habr_tools_python() -> str:
    """Путь к python с requests/bs4 (.venv-tools)."""
    return SCAN_PY


@mcp.tool()
def habr_search(query: str, limit: int = 8, order_by: str = "relevance") -> str:
    """Поиск по ВСЕМУ сайту habr.com по произвольной фразе (не только ИИ).

    Возвращает заголовки, ссылки, авторов, даты, теги и краткие описания.

    Args:
        query: поисковая фраза любыми словами, например "настройка CUDA llama.cpp"
               или "ардуино датчики" — как в поиске на сайте
        limit: максимум результатов 1-20 (по умолчанию 8)
        order_by: relevance | date | rank (по умолчанию relevance)
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("habr_scan", str(BASE / "habr_scan.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    results = mod.habr_rss_search(query, limit=limit, order_by=order_by)
    if not results:
        return (
            f"❌ По «{query}» на habr.com ничего не найдено.\n"
            "Попробуйте: переформулировать (другие слова), убрать редкие слова, "
            "искать по одному-двум ключевым словам, или спросите — искать в локальной "
            "базе (kb_search) или на сайте (habr_search другими словами)?"
        )
    lines = [f"🔎 habr.com «{query}» — найдено {len(results)} (показаны {len(results)}):"]
    for r in results:
        lines.append(
            f"• {r['title']}\n"
            f"  {r['link']} | {r['author'] or 'автор ?'} | {r['pubDate'][:16]}\n"
            f"  теги: {', '.join(r['categories'][:5]) or '—'}\n"
            f"  {r['summary'][:200]}"
        )
    return "\n".join(lines)


@mcp.tool()
def habr_read(url: str, max_chars: int = 12000) -> str:
    """Прочитать полную статью habr.com по ссылке (для конспекта или цитаты).

    Args:
        url: ссылка на статью https://habr.com/ru/articles/<id>/
        max_chars: максимум символов текста в ответе (по умолчанию 12000)
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("habr_scan", str(BASE / "habr_scan.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        art = mod.extract_article(url)
    except Exception as e:
        return (
            f"⛔ Не удалось прочитать статью ({e}).\n"
            "Нужна ссылка вида https://habr.com/ru/articles/<id>/ — "
            "найдите статью заново через habr_search."
        )
    text = art["text"]
    out = [f"📄 {url}"]
    if art["code"]:
        out.append(f"код-блоков в статье: {len(art['code'])}")
    out.append("")
    out.append(text[: max(1000, min(max_chars, 40000))])
    if len(text) > max_chars:
        out.append(f"\n…(обрезано, всего {len(text)} симв. — увеличьте max_chars)")
    return "\n".join(out)


_KB_INDEX_FILE = BASE / "habr_index.json"
_KB_EMBED_TIMEOUT = 30


def _kb_embed(text: str):
    """Вектор запроса или None (эмбеддер недоступен)."""
    try:
        r = requests.post(
            f"{EMBED}/embeddings",
            json={"model": "gte-modernbert", "input": text[:4000]},
            timeout=_KB_EMBED_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()["data"][0]["embedding"]
    except Exception:
        return None


def _kb_cosine(a, b) -> float:
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a)) or 1.0
    db = math.sqrt(sum(x * x for x in b)) or 1.0
    return num / (da * db)


def _kb_note_body(path: str) -> str:
    """Тело заметки без frontmatter и хвоста «Оригинал» (там конспект)."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    if text.startswith("---"):
        parts = text.split("---", 2)
        text = parts[2] if len(parts) == 3 else text
    if "## Оригинал" in text:
        text = text.split("## Оригинал")[0]
    return text.strip()


def _kb_snippet(body: str, words: list, width: int = 200) -> str:
    """Фрагмент тела вокруг первого вхождения любого слова."""
    low = body.lower()
    pos = -1
    for w in words:
        pos = low.find(w)
        if pos >= 0:
            break
    if pos < 0:
        return body[:width].replace("\n", " ")
    start = max(0, pos - width // 3)
    return ("…" if start else "") + body[start:start + width].replace("\n", " ") + "…"


def _kb_search_impl(query: str, top: int, mode: str) -> str:
    top = max(1, min(top, 10))
    if not _KB_INDEX_FILE.exists():
        return (
            "📭 Индекс базы пуст (habr_index.json отсутствует).\n"
            "Запустите сканер habr_scan (он наполнит базу конспектами) или уточните, "
            "где искать: локальный vault (obsidian_search) или сайт habr.com (habr_search)."
        )
    index = json.loads(_KB_INDEX_FILE.read_text(encoding="utf-8"))

    words = [w for w in re.split(r"\W+", query.lower()) if len(w) > 2]
    # префиксы для грубого стемминга: «цензура» найдёт «цензуру», «нейросети» → «нейрос»
    stems = sorted({w[:6] for w in words}, key=len, reverse=True)
    want_sem = mode in ("auto", "semantic")
    want_grep = mode in ("auto", "grep")

    qv = _kb_embed(query) if want_sem else None
    if want_sem and qv is None and mode == "semantic":
        return (
            "⛔ Эмбеддер :8082 недоступен, семантический режим невозможен.\n"
            "Повторите с mode='grep' или запустите эмбеддер (start_ai.sh)."
        )

    scored = []
    for e in index:
        sem = 0.0
        if qv is not None and e.get("vec"):
            sem = max(0.0, _kb_cosine(qv, e["vec"]))
        gscore = 0.0
        matched = []
        if want_grep and words:
            title_hay = (e.get("title", "") + " " + " ".join(e.get("tags", []))).lower()
            body = _kb_note_body(e.get("file", ""))
            body_low = body.lower()
            for w in words:
                for form in ([w, w[:6]] if len(w) > 6 else [w]):
                    t = title_hay.count(form)
                    b = body_low.count(form)
                    if t or b:
                        gscore += 3.0 * min(t, 3) + 1.0 * min(b, 10)
                        if w not in matched:
                            matched.append(w)
                        break
            gscore = min(gscore / 30.0, 1.0)  # нормировка: 10 вхождений слова ≈ максимум
        total = 0.7 * sem + 0.3 * gscore
        if total > 0.05:
            scored.append((total, sem, gscore, e, matched))

    scored.sort(key=lambda t: -t[0])
    hits = scored[:top]

    if not hits:
        return (
            f"❌ По «{query}» в локальной базе ничего не найдено.\n"
            "Уточните, где искать дальше:\n"
            "— habr_search(«{q}») — поиск по сайту habr.com;\n"
            "— obsidian_search(«{q}») — grep по всем заметкам vault;\n"
            "— или переформулируйте запрос другими словами.".format(q=query)
        )

    sem_used = hits[0][1] > 0
    lines = [f"📚 Локальная база «{query}» — найдено {len(hits)}"
             + (" (семантика+grep):" if sem_used else " (grep):")]
    for total, sem, gscore, e, matched in hits:
        body = _kb_note_body(e.get("file", ""))
        snippet = _kb_snippet(body, matched or [w for w in words]) if (matched or words) else body[:200]
        parts = [f"• {e['title']}  [{total:.2f}"]
        if sem:
            parts[0] += f" | сем {sem:.2f}"
        if gscore:
            parts[0] += f" | grep {gscore:.2f}"
        parts[0] += "]"
        if e.get("url") and e["url"].startswith("http"):
            parts.append(f"  {e['url']}")
        parts.append(f"  файл: {e.get('file', '?')}")
        if snippet:
            parts.append(f"  фрагмент: {snippet[:260]}")
        lines.append("\n".join(parts))
    return "\n".join(lines)


@mcp.tool()
def kb_search(query: str, top: int = 6, mode: str = "auto") -> str:
    """Поиск по локальной базе знаний: конспекты Habr + заметки vault Obsidian.

    Ищет по названию И конспекту (телу заметки): семантический поиск (эмбеддер
    gte-modernbert :8082) + grep-скоринг по телу. При пустом результате
    возвращает подсказку переспросить, где искать.

    Args:
        query: поисковая фраза любыми словами, например "TURN сервер WebRTC"
        top: максимум результатов 1-10 (по умолчанию 6)
        mode: auto | semantic | grep (auto = семантика с grep-фолбэком)
    """
    try:
        return _kb_search_impl(query, top, mode if mode in ("auto", "semantic", "grep") else "auto")
    except Exception as e:
        return f"⛔ kb_search: {e}. Проверьте эмбеддер (:8082) или повторите с mode='grep'."


# ============================================================ реставрация фото


@mcp.tool()
def photo_fix(
    input_path: str,
    preset: str = "old",
    rotate: float = 0.0,
    auto_straighten: bool = False,
    crop: str = "",
    denoise: str = "default",
    fix_cracks: str = "default",
    mask: str = "",
    brightness: float = 0.0,
    contrast: float = 0.0,
    gamma: float = 1.0,
    scale: float = 1.0,
    dry_run: bool = False,
) -> str:
    """Реставрация фотографий: обрезка, поворот, яркость/контраст, шумоподавление,
    удаление трещин и царапин (ИИ-заливка LaMa на GPU). Оригиналы не изменяются —
    результат в подпапке restored/ рядом с файлом.

    Args:
        input_path: файл или папка с изображениями (jpg/png/tif/webp/bmp)
        preset: "old" — архивные сканы (трещины, выцветание, желтизна); "modern" — телефонные фото (шум, тени)
        rotate: поворот в градусах по часовой (90, 180, любой угол)
        auto_straighten: выровнять наклон скана по линиям
        crop: "auto" (срезать рамки скана) или "x,y,w,h"
        denoise: off|light|medium|strong (по умолчанию из пресета)
        fix_cracks: off|auto (по умолчанию auto для old, off для modern)
        mask: PNG-маска повреждений (белое = закрасить), если есть своя
        brightness: -100..100
        contrast: -100..100
        gamma: гамма-коррекция (1.0 = без изменений, 1.5 = светлее)
        scale: масштаб (2.0 = увеличить вдвое)
        dry_run: True = показать план без записи
    """
    src = Path(input_path).expanduser()
    if not src.exists():
        return f"⛔ Не найдено: {src}"
    class Args:
        pass
    a = Args()
    a.preset = preset if preset in ("old", "modern") else "old"
    a.rotate = float(rotate)
    a.auto_straighten = bool(auto_straighten)
    a.crop = crop or ""
    a.denoise = denoise if denoise in ("off", "light", "medium", "strong", "default") else "default"
    a.fix_cracks = fix_cracks if fix_cracks in ("off", "auto", "default") else "default"
    a.mask = Path(mask).expanduser() if mask else None
    a.brightness = float(brightness)
    a.contrast = float(contrast)
    a.gamma = float(gamma)
    a.scale = float(scale)
    a.quality = 95
    a.out = ""
    a.no_auto = False
    a.dry_run = bool(dry_run)

    sys.path.insert(0, str(BASE))
    try:
        import photo_fix as pf
    except Exception as e:
        return f"⛔ Не удалось загрузить photo_fix: {e}"
    files = sorted(p for p in (src.iterdir() if src.is_dir() else [src])
                   if p.is_file() and p.suffix.lower() in pf.IMAGE_EXTS)
    if not files:
        return f"⛔ Изображений не найдено в {src}"
    if len(files) > 20:
        return f"⛔ За раз максимум 20 файлов (передано {len(files)})"
    report = []
    for f in files:
        try:
            out, info = pf.process_image(f, a)
            info["steps"] = "; ".join(info["steps"])
            report.append(info)
        except Exception as e:
            report.append({"file": str(f), "error": str(e)})
    return json.dumps(report, ensure_ascii=False, indent=1)


# ============================================================ ComfyUI: реставрация/раскраска фото

COMFY = "http://127.0.0.1:8188"
COMFY_INPUT = Path("/home/rover/AI-Combine/ComfyUI/input")
COMFY_OUT = Path("/home/rover/Pictures/comfy")
QWEN_EDIT_PROMPTS = {
    "colorize": "Раскрась это чёрно-белое фото естественными правдоподобными цветами: "
                "сохрани все детали, лица и композицию без изменений. Фотореалистично.",
    "restore": "Отреставрируй это старое повреждённое фото: убери царапины, пятна, шум и "
               "потёртости, восстанови детали и резкость. Сохрани лица и композицию без изменений.",
    "outpaint": "Дополни это фото на пустых (чёрных) полях, естественно продолжив сцену: "
                "фон, землю, небо, одежду. Сохрани исходную часть фото без изменений. Фотореалистично.",
    "edit": "Внеси правку в это фото по тексту пользователя, сохранив композицию и "
            "остальное содержимое без изменений. Фотореалистично.",
}


def _comfy_input_file(src: Path) -> str:
    """Скопировать файл в ComfyUI/input, вернуть имя для LoadImage."""
    COMFY_INPUT.mkdir(parents=True, exist_ok=True)
    dst = COMFY_INPUT / f"in_{os.getpid()}_{src.name}"
    shutil.copy2(src, dst)
    return dst.name


def _comfy_run(graph: dict, poll_timeout: int = 1800) -> tuple[str, list[bytes]]:
    """Отправить граф в ComfyUI и дождаться картинок. ('', [png-байты]) или ('⛔ текст', [])."""
    import time as _t
    try:
        r = requests.post(f"{COMFY}/prompt", timeout=30, json={"prompt": graph})
        r.raise_for_status()
        pid = r.json().get("prompt_id", "")
    except Exception as e:
        return f"⛔ ComfyUI недоступен ({COMFY}): {e}", []
    if not pid:
        return "⛔ ComfyUI не вернул prompt_id", []
    t0 = _t.time()
    while _t.time() - t0 < poll_timeout:
        _t.sleep(3)
        try:
            hist = requests.get(f"{COMFY}/history", timeout=30).json().get(pid) or {}
        except Exception:
            continue
        if not hist:
            continue
        if hist.get("status", {}).get("status_str") == "error":
            return "⛔ ComfyUI: " + json.dumps(hist["status"].get("messages", []))[:500], []
        outs = []
        for node_out in hist.get("outputs", {}).values():
            for img in node_out.get("images", []):
                rr = requests.get(f"{COMFY}/view", timeout=120, params={
                    "filename": img["filename"], "subfolder": img.get("subfolder", ""),
                    "type": img.get("type", "output")})
                outs.append((img["filename"], rr.content))
        if outs:
            return "", outs
    return f"⛔ ComfyUI: таймаут ожидания {poll_timeout} с", []


def _comfy_save(outs: list, output: str, prefix: str) -> str:
    """Сохранить байты; расширение берётся из имени файла ComfyUI (.png/.mp4)."""
    from datetime import datetime
    paths = []
    if output:
        o = Path(output).expanduser()
        base_dir = o.parent if (o.suffix or len(outs) == 1) else o
        base_dir.mkdir(parents=True, exist_ok=True)
    else:
        base_dir = COMFY_OUT
        base_dir.mkdir(parents=True, exist_ok=True)
    stem = (Path(output).expanduser().stem if output else
            datetime.now().strftime("%Y%m%d-%H%M%S") + "_" + prefix)
    for i, (fname, data) in enumerate(outs):
        ext = Path(fname).suffix or ".png"
        name = stem + (f"_{i}" if len(outs) > 1 else "") + ext
        p = base_dir / name
        p.write_bytes(data)
        paths.append(str(p))
    return "✅ сохранено: " + ", ".join(paths)


def _memory_preflight(vram_needed_gb: float, ram_needed_gb: float = 0.0) -> str:
    """Проверка памяти перед GPU-задачей: если VRAM мало — выгружает LLM роутера
    (правило «последний запущенный получает память»). RAM не выгружаем — только
    предупреждение (LLM-рой в RAM выгружается только ai_mem.sh free).

    Returns: строку-note ('' — всё ок без выгрузки)."""
    import time
    notes = []

    def _vram_free_gb() -> float:
        try:
            q = requests.get("http://127.0.0.1:8188/system_stats", timeout=5).json()
            return float(q["devices"][0]["vram_free"]) / 2**30
        except Exception:
            try:
                out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free",
                                      "--format=csv,noheader,nounits"],
                                     capture_output=True, text=True, timeout=10)
                return float(out.stdout.strip().splitlines()[0]) / 1024
            except Exception:
                return -1.0

    def _ram_avail_gb() -> float:
        try:
            with open("/proc/meminfo", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemAvailable"):
                        return int(line.split()[1]) / 2**20
        except Exception:
            pass
        return -1.0

    free = _vram_free_gb()
    if 0 <= free < vram_needed_gb:
        n_un = 0
        try:
            models = requests.get("http://127.0.0.1:8081/models", timeout=10).json().get("data", [])
            for md in models:
                if md.get("status", {}).get("value") == "loaded":
                    requests.post("http://127.0.0.1:8081/models/unload",
                                  json={"model": md.get("id")}, timeout=60)
                    n_un += 1
        except Exception:
            pass
        time.sleep(4)
        after = _vram_free_gb()
        notes.append(f"авто-выгрузка LLM роутера: {n_un} шт. "
                     f"(VRAM {free:.1f} → {after:.1f} ГБ свободно, требовалось {vram_needed_gb:.1f})")

    if ram_needed_gb > 0:
        ram = _ram_avail_gb()
        if 0 <= ram < ram_needed_gb:
            notes.append(f"⚠️ RAM мало: свободно {ram:.1f} ГБ, нужно ~{ram_needed_gb:.1f} — "
                         "выгрузи тяжёлые LLM из RAM (./ai_mem.sh free) или возьми меньший клип/разрешение")
    return (" — " + "; ".join(notes) + " ") if notes else ""


def _bopbtl_graph(image_name: str, mode: str = "scratch",
                  face_enhance: bool = True, scale: float = 1.0) -> dict:
    """API-граф BOPBTL: царапины/качество + лица + (опц.) апскейл UltraSharp."""
    g = {"1": {"class_type": "LoadImage", "inputs": {"image": image_name}}}
    img = ["1", 0]
    nid, mask = 2, None
    if mode == "scratch":
        g[str(nid)] = {"class_type": "BOPBTL_LoadScratchMaskModel",
                       "inputs": {"scratch_model": "detection/FT_Epoch_latest.pt"}}
        sm = [str(nid), 0]; nid += 1
        g[str(nid)] = {"class_type": "BOPBTL_ScratchMask",
                       "inputs": {"scratch_model": sm, "image": img,
                                  "input_size": "full_size", "resize_method": "bilinear"}}
        mask = [str(nid), 0]; nid += 1
        mp = "restoration/mapping_scratch/latest_net_mapping_net.pth"
        vb = "restoration/VAE_B_scratch/latest_net_G.pth"
    else:
        mp = "restoration/mapping_quality/latest_net_mapping_net.pth"
        vb = "restoration/VAE_B_quality/latest_net_G.pth"
    g[str(nid)] = {"class_type": "BOPBTL_LoadRestoreOldPhotosModel",
                   "inputs": {"device_ids": "0",
                              "scratch_detection": "True" if mode == "scratch" else "False",
                              "mapping_patch_attention": "False", "mapping_net": mp,
                              "vae_b": vb,
                              "vae_a": "restoration/VAE_A_quality/latest_net_G.pth"}}
    models = [str(nid), 0]; nid += 1
    restore_in = {"bopbtl_models": models, "image": img,
                  "pad_mode": "edge", "pad_constant_value": 0, "pad_reflect_type": "even"}
    if mask:
        restore_in["scratch_mask"] = mask
    g[str(nid)] = {"class_type": "BOPBTL_RestoreOldPhotos", "inputs": restore_in}
    img = [str(nid), 0]; nid += 1
    if face_enhance:
        g[str(nid)] = {"class_type": "BOPBTL_LoadFaceDetectorModel",
                       "inputs": {"shape_predictor_68_face_landmarks":
                                      "shape_predictor_68_face_landmarks.dat"}}
        dl = [str(nid), 0]; nid += 1
        g[str(nid)] = {"class_type": "BOPBTL_LoadFaceEnhancerModel",
                       "inputs": {"device_ids": "0",
                                  "face_enhance_model": "face/Setting_9_epoch_100/latest_net_G.pth",
                                  "model_face_size": "256"}}
        fe = [str(nid), 0]; nid += 1
        g[str(nid)] = {"class_type": "BOPBTL_DetectEnhanceBlendFaces",
                       "inputs": {"dlib_model": dl, "face_enhance_model": fe, "image": img}}
        img = [str(nid), 0]; nid += 1
    if scale and scale != 1.0:
        g[str(nid)] = {"class_type": "UpscaleModelLoader",
                       "inputs": {"model_name": "4x-UltraSharp.pth"}}
        um = [str(nid), 0]; nid += 1
        g[str(nid)] = {"class_type": "ImageUpscaleWithModel",
                       "inputs": {"upscale_model": um, "image": img}}
        img = [str(nid), 0]; nid += 1
        if scale != 4.0:
            g[str(nid)] = {"class_type": "ImageScaleBy",
                           "inputs": {"image": img, "scale_by": scale / 4.0,
                                      "upscale_method": "lanczos"}}
            img = [str(nid), 0]; nid += 1
    g[str(nid)] = {"class_type": "SaveImage",
                   "inputs": {"images": img, "filename_prefix": "restored"}}
    return g


def photo_restore_comfy(input_path: str, output: str = "", mode: str = "scratch",
                        face_enhance: bool = True, scale: float = 1.0,
                        poll_timeout: int = 1800) -> str:
    """ИИ-реставрация старых фото (BOPBTL через ComfyUI :8188): царапины, шум,
    потёртости + улучшение лиц (dlib+GAN) + (опц.) апскейл 4x-UltraSharp.
    Тяжёлый вариант; для быстрой чистки без VRAM см. photo_fix."""
    src = Path(input_path).expanduser()
    if not src.exists():
        return f"⛔ Не найдено: {src}"
    if mode not in ("scratch", "quality"):
        return "⛔ mode: scratch (царапины+качество) | quality (только качество)"
    name = _comfy_input_file(src)
    pnote = _memory_preflight(3.0, 6.0)
    err, outs = _comfy_run(_bopbtl_graph(name, mode, bool(face_enhance), float(scale)),
                           int(poll_timeout))
    if err:
        return err + ("; " + pnote.strip() if pnote else "")
    return _comfy_save(outs, output, "restored") + pnote


def _qwen_edit_graph(image_name: str, prompt: str, negative: str = "",
                     steps: int = 8, seed: int = 0, resolution: int = 1024) -> dict:
    """API-граф Qwen-Image 2.1 Turbo Edit: раскраска/реставрация/правки по промпту."""
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "2": {"class_type": "UNETLoader",
              "inputs": {"unet_name": "qwen_image_2.1_turbo_int8_convrot.safetensors",
                         "weight_dtype": "default"}},
        "3": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": "qwen3vl_8b_int8_convrot.safetensors",
                         "type": "qwen_image", "device": "default"}},
        "4": {"class_type": "VAELoader",
              "inputs": {"vae_name": "qwen_image_2.1_vae_bf16.safetensors"}},
        "5": {"class_type": "TextEncodeQwenImage21",
              "inputs": {"clip": ["3", 0], "prompt": prompt, "negative_prompt": negative,
                         "vae": ["4", 0], "resolution": int(resolution),
                         "images": {"image_1": ["1", 0]}}},
        "6": {"class_type": "KSampler",
              "inputs": {"seed": int(seed), "steps": max(1, min(int(steps), 30)), "cfg": 1.0,
                         "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
                         "model": ["2", 0], "positive": ["5", 0], "negative": ["5", 1],
                         "latent_image": ["5", 2]}},
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["4", 0]}},
        "8": {"class_type": "SaveImage",
              "inputs": {"images": ["7", 0], "filename_prefix": "photo_edit"}},
    }


def photo_colorize(input_path: str, output: str = "", mode: str = "colorize",
                   prompt: str = "", steps: int = 8, seed: int = 0,
                   resolution: int = 1024, poll_timeout: int = 1800) -> str:
    """Раскраска ч/б фото и ИИ-реставрация промптом (Qwen-Image 2.1 Edit через ComfyUI).
    mode: colorize | restore | edit (edit — свой текст в prompt, по-русски).
    prompt переопределяет дефолт-промпт режима."""
    src = Path(input_path).expanduser()
    if not src.exists():
        return f"⛔ Не найдено: {src}"
    if mode not in QWEN_EDIT_PROMPTS:
        return "⛔ mode: colorize | restore | edit"
    text = (prompt or "").strip() or QWEN_EDIT_PROMPTS[mode]
    name = _comfy_input_file(src)
    pnote = _memory_preflight(7.0, 8.0)
    err, outs = _comfy_run(_qwen_edit_graph(name, text, "", steps, seed, resolution),
                           int(poll_timeout))
    if err:
        return err + ("; " + pnote.strip() if pnote else "")
    return _comfy_save(outs, output, mode) + pnote


def _qwen_outpaint_graph(image_name: str, prompt: str, negative: str = "",
                         left: int = 256, top: int = 256, right: int = 256,
                         bottom: int = 256, feathering: int = 64,
                         steps: int = 8, seed: int = 0) -> dict:
    """API-граф Qwen-Edit outpaint: ImagePadForOutpaint (IMAGE+MASK) →
    VAEEncode → SetLatentNoiseMask (правятся только поля) → KSampler."""
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "2": {"class_type": "UNETLoader",
              "inputs": {"unet_name": "qwen_image_2.1_turbo_int8_convrot.safetensors",
                         "weight_dtype": "default"}},
        "3": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": "qwen3vl_8b_int8_convrot.safetensors",
                         "type": "qwen_image", "device": "default"}},
        "4": {"class_type": "VAELoader",
              "inputs": {"vae_name": "qwen_image_2.1_vae_bf16.safetensors"}},
        "9": {"class_type": "ImagePadForOutpaint",
              "inputs": {"image": ["1", 0], "left": int(left), "top": int(top),
                         "right": int(right), "bottom": int(bottom),
                         "feathering": int(feathering)}},
        "8": {"class_type": "VAEEncode",
              "inputs": {"pixels": ["9", 0], "vae": ["4", 0]}},
        "11": {"class_type": "SetLatentNoiseMask",
               "inputs": {"samples": ["8", 0], "mask": ["9", 1]}},
        "5": {"class_type": "TextEncodeQwenImage21",
              "inputs": {"clip": ["3", 0], "prompt": prompt, "negative_prompt": negative,
                         "vae": ["4", 0], "resolution": 0,
                         "images": {"image_1": ["9", 0]}}},
        "6": {"class_type": "KSampler",
              "inputs": {"seed": int(seed), "steps": max(1, min(int(steps), 30)), "cfg": 1.0,
                         "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
                         "model": ["2", 0], "positive": ["5", 0], "negative": ["5", 1],
                         "latent_image": ["11", 0]}},
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["4", 0]}},
        "10": {"class_type": "SaveImage",
               "inputs": {"images": ["7", 0], "filename_prefix": "outpaint"}},
    }


def photo_outpaint(input_path: str, output: str = "", prompt: str = "",
                   left: int = 256, top: int = 256, right: int = 256,
                   bottom: int = 256, feathering: int = 64,
                   steps: int = 8, seed: int = 0, poll_timeout: int = 1800) -> str:
    """Дополнение оторванных/обрезанных краёв фото (Qwen-Image 2.1 Edit через
    ComfyUI :8188). Поля задаются в пикселях (кратно 8); исходная часть
    сохраняется через SetLatentNoiseMask. Без prompt — универсальный текст."""
    src = Path(input_path).expanduser()
    if not src.exists():
        return f"⛔ Не найдено: {src}"
    text = (prompt or "").strip() or QWEN_EDIT_PROMPTS["outpaint"]
    name = _comfy_input_file(src)
    pnote = _memory_preflight(7.0, 8.0)
    err, outs = _comfy_run(_qwen_outpaint_graph(name, text, "", left, top, right,
                                                bottom, feathering, steps, seed),
                           int(poll_timeout))
    if err:
        return err + ("; " + pnote.strip() if pnote else "")
    return _comfy_save(outs, output, "outpaint") + pnote


# ============================================================ мини-агент

AGENT_SYSTEM = (
    "Ты — агент-оркестратор на локальном компьютере. Реши задачу пользователя "
    "ПО ШАГАМ: составь краткий план, затем вызывай доступные функции по очереди, "
    "анализируй их результаты. Когда задача решена — дай финальный ответ: что сделано, "
    "ход решения, результат. По-русски. Не вызывай функции бессмысленно; "
    "команды терминала — без root и без деструктива."
)



# ============================================================ видео: субтитры/надписи/сглаживание/генерация

GS_PY = Path("/home/rover/AI-Combine/giga_speech/.venv/bin/python")
STT_SCRIPT = Path("/home/rover/AI-Combine/giga_speech/stt_model.py")
FFMPEG = "/usr/bin/ffmpeg"
VIDEO_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".wma"}


def _vsub_run(cmd: list[str], timeout: int = 900):
    run = getattr(subprocess, "run")  # списком, shell=False — без shell-интерполяции
    return run(cmd, capture_output=True, text=True, timeout=timeout, shell=False)


def _srt_time(t: float) -> str:
    if t < 0:
        t = 0.0
    h, rem = divmod(int(t), 3600)
    m, s = divmod(rem, 60)
    ms = int(round((t - int(t)) * 1000))
    if ms == 1000:
        s += 1
        ms = 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _speech_to_srt(src: Path) -> str:
    """Аудио/видео → SRT-текст (GigaAM + pyannote через giga_speech venv)."""
    tmp_wav = None
    wav_in = src
    if src.suffix.lower() not in VIDEO_AUDIO_EXTS:
        fd, tmp = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        tmp_wav = Path(tmp)
        p = _vsub_run([FFMPEG, "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", tmp],
                      timeout=300)
        if p.returncode != 0 or os.path.getsize(tmp) == 0:
            tmp_wav.unlink(missing_ok=True)
            raise RuntimeError("ffmpeg не извлёк аудио: " + (p.stderr or "")[-200:])
        wav_in = tmp_wav
    try:
        proc = _vsub_run([str(GS_PY), str(STT_SCRIPT), str(wav_in), "--json", "--diarize"],
                         timeout=1800)
        out = (proc.stdout or "").strip()
        if proc.returncode != 0:
            raise RuntimeError("giga_speech код " + str(proc.returncode) + ": " + (proc.stderr or "")[-200:])
        pos = out.find("{")
        if pos < 0:
            raise RuntimeError("giga_speech вернул не JSON")
        data, _ = json.JSONDecoder().raw_decode(out[pos:])
        if data.get("status") != "ok":
            raise RuntimeError("giga_speech status: " + json.dumps(data, ensure_ascii=False)[:300])
        lines = []
        for i, seg in enumerate(data.get("segments", []), 1):
            spk = str(seg.get("speaker", "SPK")).replace("SPEAKER_", "Спикер ")
            lines.append(str(i))
            lines.append(f"{_srt_time(float(seg['start']))} --> {_srt_time(float(seg['end']))}")
            lines.append(f"[{spk}] " + str(seg.get("text", "")).strip())
            lines.append("")
        return "\n".join(lines).strip()
    finally:
        if tmp_wav is not None:
            tmp_wav.unlink(missing_ok=True)


def _ffmpeg_out_ok(proc, out: Path):
    if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        raise RuntimeError("ffmpeg ошибка: " + (proc.stderr or "")[-250:])


def video_subs(input_path: str, output: str = "", burn: bool = True,
               fontsize: int = 40, poll_timeout: int = 1800) -> str:
    """Субтитры из речи: GigaAM+pyannote расшифровывает, ffmpeg вписывает текст в видео."""
    src = Path(input_path).expanduser().resolve()
    if not src.exists():
        return "⛔ Не найдено: " + input_path
    base = Path(output).expanduser().resolve() if output.strip() else src.parent / (src.stem + "_субтитры.mp4")
    base.parent.mkdir(parents=True, exist_ok=True)
    srt_path = base.with_suffix(".srt")
    srt_text = _speech_to_srt(src)
    srt_path.write_text(srt_text, encoding="utf-8")
    print("[video_subs] SRT:", srt_path)
    if not burn:
        return "✅ SRT сохранён: " + str(srt_path)
    srt_escaped = str(srt_path).replace("\\", "/").replace(":", "\\:")
    vf = f"subtitles={srt_escaped}:force_style='FontSize={fontsize},Outline=2'"
    proc = _vsub_run([FFMPEG, "-y", "-i", str(src), "-vf", vf, "-c:a", "copy", str(base)],
                     timeout=poll_timeout)
    _ffmpeg_out_ok(proc, base)
    return "✅ видео с субтитрами: " + str(base) + " (SRT: " + str(srt_path) + ")"


def video_text(input_path: str, text: str, output: str = "", fontsize: int = 48,
               position: str = "bottom", duration: float = 0.0, poll_timeout: int = 900) -> str:
    """Свои надписи/титры на видео (ffmpeg drawtext). position: top|bottom|center."""
    src = Path(input_path).expanduser().resolve()
    if not src.exists():
        return "⛔ Не найдено: " + input_path
    base = Path(output).expanduser().resolve() if output.strip() else src.parent / (src.stem + "_титры.mp4")
    base.parent.mkdir(parents=True, exist_ok=True)
    text_file = base.with_suffix(".txt")
    text_file.write_text(text, encoding="utf-8")
    y_expr = {"top": "40", "center": "h/2-th/2", "bottom": "h-th-60"}.get(position, "h-th-60")
    dur_part = f":enable='between(t,0,{duration:.2f})'" if duration > 0 else ""
    vf = (f"drawtext=textfile={text_file}:fontsize={fontsize}:fontcolor=white:"
          f"borderw=2:bordercolor=black:x=(w-tw)/2:y={y_expr}{dur_part}")
    proc = _vsub_run([FFMPEG, "-y", "-i", str(src), "-vf", vf, "-c:a", "copy", str(base)],
                     timeout=poll_timeout)
    text_file.unlink(missing_ok=True)
    _ffmpeg_out_ok(proc, base)
    return "✅ надпись добавлена: " + str(base)


def video_interp(input_path: str, output: str = "", multiplier: int = 2,
                 fps_div: int = 1, rife_model: str = "rife49.pth",
                 poll_timeout: int = 3600) -> str:
    """Сглаживание/слоумо через RIFE в ComfyUI: multiplier=2 (+кадры), fps_div=2 (слоумо)."""
    src = Path(input_path).expanduser().resolve()
    if not src.exists():
        return "⛔ Не найдено: " + input_path
    pnote = _memory_preflight(2.5, 20.0)
    COMFY_INPUT_DIR = Path("/home/rover/AI-Combine/ComfyUI/input")
    COMFY_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    in_name = "ve_" + str(os.getpid()) + "_" + src.name
    shutil.copy2(src, COMFY_INPUT_DIR / in_name)
    graph = {
        "1": {"class_type": "LoadVideo", "inputs": {"file": in_name}},
        "3": {"class_type": "GetVideoComponents", "inputs": {"video": ["1", 0]}},
        "5": {"class_type": "RIFE VFI",
              "inputs": {"ckpt_name": rife_model, "multiplier": int(multiplier),
                         "clear_cache_after_n_frames": 10, "scale_factor": 0.5,
                         "fast_mode": True, "ensemble": True, "dtype": "float32",
                         "batch_size": 1, "torch_compile": False,
                         "frames": ["3", 0]}},
        "6": {"class_type": "CreateVideo",
              "inputs": {"images": ["5", 0], "audio": ["3", 1], "fps": 24.0}},
        "7": {"class_type": "SaveVideo", "inputs": {"video": ["6", 0],
              "filename_prefix": "video/interp", "format": "auto"}},
    }
    err, outs = _comfy_run(graph, int(poll_timeout))
    if err:
        return err + ("; " + pnote.strip() if pnote else "")
    tmp_saved = Path(_comfy_save(outs, "", "interp").split(": ", 1)[1].split(", ")[0])
    base = Path(output).expanduser().resolve() if output.strip() else src.parent / (src.stem + "_сглажено.mp4")
    base.parent.mkdir(parents=True, exist_ok=True)
    fps_cmd = ["-vf", f"setpts={fps_div}*PTS"] if fps_div > 1 else []
    proc = _vsub_run([FFMPEG, "-y", "-i", str(tmp_saved)] + fps_cmd + ["-c:a", "copy", str(base)],
                     timeout=900)
    tmp_saved.unlink(missing_ok=True)
    _ffmpeg_out_ok(proc, base)
    return "✅ сглажено: " + str(base) + pnote


def video_t2v(prompt: str, output: str = "", width: int = 832, height: int = 480,
              frames: int = 81, seed: int = 0, steps: int = 20,
              poll_timeout: int = 3600) -> str:
    """Генерация видео по тексту (Wan 2.1 1.3B, 16fps): frames=81 ≈ 5 сек."""
    pnote = _memory_preflight(7.0, 10.0)
    graph = {
        "2": {"class_type": "UNETLoader",
              "inputs": {"unet_name": "wan2.1_t2v_1.3B_fp16.safetensors",
                         "weight_dtype": "default"}},
        "3": {"class_type": "CLIPLoaderGGUF",
              "inputs": {"clip_name": "umt5-xxl-encoder-Q4_K_M.gguf", "type": "wan"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": "Wan2.1_VAE.safetensors"}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": prompt}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": ""}},
        "7": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["2", 0], "shift": 8.0}},
        "8": {"class_type": "EmptyHunyuanLatentVideo",
              "inputs": {"width": int(width), "height": int(height), "length": int(frames),
                         "batch_size": 1}},
        "9": {"class_type": "KSampler",
              "inputs": {"seed": int(seed), "steps": max(4, min(int(steps), 40)), "cfg": 6.0,
                         "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
                         "model": ["7", 0], "positive": ["5", 0], "negative": ["6", 0],
                         "latent_image": ["8", 0]}},
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["4", 0]}},
        "11": {"class_type": "CreateVideo", "inputs": {"images": ["10", 0], "fps": 16.0}},
        "12": {"class_type": "SaveVideo", "inputs": {"video": ["11", 0],
               "filename_prefix": "video/wan21_13b", "format": "auto"}},
    }
    err, outs = _comfy_run(graph, int(poll_timeout))
    if err:
        return err + ("; " + pnote.strip() if pnote else "")
    return _comfy_save(outs, output, "t2v") + pnote


def video_edit(action: str, input_path: str = "", output: str = "", text: str = "",
               prompt: str = "", fontsize: int = 40, position: str = "bottom",
               multiplier: int = 2, fps_div: int = 1, width: int = 832,
               height: int = 480, frames: int = 81, seed: int = 0, steps: int = 20,
               poll_timeout: int = 3600) -> str:
    """Видео-инструмент (один вход — пять действий):
    subs — субтитры из речи (ГигаСпич STT → вписать в видео);
    text — свои надписи/титры (drawtext);
    interp — сглаживание RIFE ×multiplier;
    slowmo — слоумо (interp + замедление fps_div);
    t2v — генерация видео по промпту (Wan 2.1 1.3B, 16fps, frames=81 ≈ 5 c).
    Требует запущенный ComfyUI :8188 (кроме subs/text)."""
    if action == "subs":
        return video_subs(input_path, output, True, fontsize, poll_timeout)
    if action == "text":
        return video_text(input_path, text, output, fontsize, position, poll_timeout=poll_timeout)
    if action == "interp":
        return video_interp(input_path, output, multiplier, 1, poll_timeout=poll_timeout)
    if action == "slowmo":
        return video_interp(input_path, output, multiplier, max(2, int(fps_div)),
                            poll_timeout=poll_timeout)
    if action == "t2v":
        return video_t2v(prompt, output, width, height, frames, seed, steps, poll_timeout)
    return "⛔ action: subs | text | interp | slowmo | t2v"



def _tool_specs() -> list:
    """OpenAI-спецификации функций, доступных агенту (без agent_task).

    Свойства параметров перечисляем явно: пустые properties некоторые
    шаблоны (qwen) воспринимают как «аргументов нет» и вызывают функцию
    без аргументов.
    """
    def spec(name, desc, props, required=None):
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": desc,
                "parameters": {
                    "type": "object",
                    "properties": props,
                    **({"required": required} if required else {}),
                },
            },
        }

    s_cmd = {"type": "string", "description": "команда bash"}
    s_int = {"type": "integer"}
    s_bool = {"type": "boolean"}
    s_num = {"type": "number"}
    s_str = {"type": "string"}
    return [
        spec("run_command", "Выполнить bash-команду",
             {"command": s_cmd, "cwd": s_str, "timeout": s_int}, ["command"]),
        spec("obsidian_search", "Поиск по заметкам Obsidian (grep)",
             {"query": s_str, "folder": s_str, "limit": s_int, "is_regex": s_bool}, ["query"]),
        spec("obsidian_read", "Прочитать заметку Obsidian",
             {"path": s_str, "max_chars": s_int}, ["path"]),
        spec("habr_scan", "Сканер Habr → конспекты в Obsidian",
             {"dry_run": s_bool, "limit": s_int, "reindex": s_bool}),
        spec("habr_search", "Поиск по сайту habr.com по любой фразе",
             {"query": s_str, "limit": s_int, "order_by": s_str}, ["query"]),
        spec("habr_bookmarks", "Закладки пользователя Habr за период (заголовок+суммари)",
             {"days": s_int, "query": s_str, "limit": s_int, "user": s_str}),
        spec("habr_save", "Скачать статью Habr в Obsidian (заметка с картинками)",
             {"article_id": s_str}, ["article_id"]),
        spec("habr_fetch_start", "Фоновое скачивание закладок Habr в Obsidian (1-2 часа)",
             {"days": s_int, "query": s_str, "limit": s_int, "user": s_str}),
        spec("habr_fetch_status", "Прогресс фонового скачивания Habr", {}),
        spec("habr_fetch_stop", "Остановить фоновое скачивание Habr", {}),
        spec("habr_fetch_log", "Лог фонового скачивания Habr",
             {"lines": s_int}),
        spec("habr_fetch_resume", "Возобновить прерванное скачивание Habr", {}),
        spec("habr_read", "Читать статью habr.com по ссылке",
             {"url": s_str, "max_chars": s_int}, ["url"]),
        spec("kb_search", "Поиск по локальной базе (конспекты Habr + vault)",
             {"query": s_str, "top": s_int, "mode": s_str}, ["query"]),
        spec("ask_kb", "Вопрос к базе знаний конспектов Habr",
             {"question": s_str, "model": s_str, "top": s_int}, ["question"]),
        spec("web_search", "Поиск в интернете (DuckDuckGo): заголовки, URL, сниппеты",
             {"query": s_str, "limit": s_int}, ["query"]),
        spec("web_fetch", "Скачать страницу по URL и вернуть чистый текст",
             {"url": s_str, "max_chars": s_int}, ["url"]),
        spec("image_describe", "Распознать картинку локальной VL-моделью (текст, сцены, объекты)",
             {"path": s_str, "question": s_str}, ["path"]),
        spec("image_generate", "НАРИСОВАТЬ картинку локально (FLUX.1-schnell через sd.cpp; при занятой VRAM сам выгружает модели роутера). До 1024×1024",
             {"prompt": s_str, "output": s_str, "width": s_int, "height": s_int, "steps": s_int},
             ["prompt"]),
        spec("stt_listen", "Записать микрофон N секунд и распознать речь (голосовая команда)",
             {"seconds": s_int, "device": s_int}),
        spec("image_search", "Поиск изображений в интернете (Openverse CC-лицензии + NASA public domain); download=true скачает референсы",
             {"query": s_str, "limit": s_int, "source": s_str, "download": s_bool}, ["query"]),
        spec("vectorize_file", "Растровое изображение → векторный SVG (vtracer: логотипы, рисунки)",
             {"file_path": s_str, "color_precision": s_int, "filter_speckle": s_int,
              "layer_difference": s_int, "mode": s_str}, ["file_path"]),
        spec("glm_think", "Глубокое размышление GLM (облако) — только для самых сложных вопросов",
             {"question": s_str, "system": s_str, "max_tokens": s_int}, ["question"]),
        spec("glm_vision", "Анализ картинки GLM-4.6V (облако) — если локальная VL не справила",
             {"path": s_str, "question": s_str}, ["path", "question"]),
        spec("glm_review", "Проверить свой ответ/код через GLM (контроль качества)",
             {"task": s_str, "draft": s_str, "strict": s_bool}, ["task", "draft"]),
        spec("stt_transcribe", "Транскрибация аудиофайла (GigaAM, русский). Перед вызовом: ai_unload all!",
             {"path": s_str, "clean": s_bool}, ["path"]),
        spec("photo_fix", "Реставрация фото (пресеты old/modern)",
             {"input_path": s_str, "preset": s_str, "rotate": s_num, "crop": s_str,
              "denoise": s_str, "fix_cracks": s_str, "mask": s_str,
              "brightness": s_num, "contrast": s_num, "gamma": s_num,
              "scale": s_num, "dry_run": s_bool}, ["input_path"]),
        spec("photo_restore_comfy", "ИИ-реставрация старых фото (BOPBTL через ComfyUI): царапины, шум, потёртости, улучшение лиц, апскейл. Медленно (GPU)",
             {"input_path": s_str, "output": s_str, "mode": s_str,
              "face_enhance": s_bool, "scale": s_num, "poll_timeout": s_int},
             ["input_path"]),
        spec("photo_colorize", "Раскраска ч/б фото / ИИ-реставрация промптом (Qwen-Image 2.1 Edit через ComfyUI). Медленно (GPU)",
             {"input_path": s_str, "output": s_str, "mode": s_str, "prompt": s_str,
              "steps": s_int, "seed": s_int, "resolution": s_int, "poll_timeout": s_int},
             ["input_path"]),
        spec("photo_outpaint", "Дополнение оторванных/обрезанных краёв фото (Qwen-Edit через ComfyUI, поля в пикселях кратно 8). Медленно (GPU)",
             {"input_path": s_str, "output": s_str, "prompt": s_str,
              "left": s_int, "top": s_int, "right": s_int, "bottom": s_int,
              "feathering": s_int, "steps": s_int, "seed": s_int, "poll_timeout": s_int},
             ["input_path"]),
        spec("video_edit", "Видео-инструмент (5 действий): subs — субтитры из речи (ГигаСпич STT, вписываются в видео); text — свои надписи/титры; interp — сглаживание RIFE ×multiplier; slowmo — слоумо; t2v — генерация по промпту (Wan 2.1 1.3B, 16fps). subs/text работают без ComfyUI, остальное — нужен :8188",
             {"action": s_str, "input_path": s_str, "output": s_str, "text": s_str,
              "prompt": s_str, "fontsize": s_int, "position": s_str,
              "multiplier": s_int, "fps_div": s_int, "width": s_int, "height": s_int,
              "frames": s_int, "seed": s_int, "steps": s_int, "poll_timeout": s_int},
             ["action"]),
    ]


def _dispatch(name: str, args: dict) -> str:
    args = args or {}
    try:
        if name == "run_command":
            return run_command(**args)
        if name == "obsidian_search":
            return obsidian_search(**args)
        if name == "obsidian_read":
            return obsidian_read(**args)
        if name == "vectorize_file":
            return vectorize_file(**args)
        if name == "habr_scan":
            return habr_scan(**args)
        if name == "habr_search":
            return habr_search(**args)
        if name == "habr_bookmarks":
            return habr_bookmarks(**args)
        if name == "habr_save":
            return habr_save(**args)
        if name == "habr_fetch_start":
            return habr_fetch_start(**args)
        if name == "habr_fetch_status":
            return habr_fetch_status(**args)
        if name == "habr_fetch_stop":
            return habr_fetch_stop(**args)
        if name == "habr_fetch_log":
            return habr_fetch_log(**args)
        if name == "habr_fetch_resume":
            return habr_fetch_resume(**args)
        if name == "habr_read":
            return habr_read(**args)
        if name == "kb_search":
            return kb_search(**args)
        if name == "ask_kb":
            return ask_kb(**args)
        if name == "photo_fix":
            return photo_fix(**args)
        if name == "photo_restore_comfy":
            return photo_restore_comfy(**args)
        if name == "photo_colorize":
            return photo_colorize(**args)
        if name == "photo_outpaint":
            return photo_outpaint(**args)
        if name == "video_edit":
            return video_edit(**args)
        # swarm-инструменты (web/glm/stt/image) — делегируем в модуль swarm_tools
        try:
            import swarm_tools as _sw
        except Exception:
            _sw = None
        if _sw is not None and hasattr(_sw, name):
            return str(getattr(_sw, name)(**args))
        return f"неизвестная функция {name}"
    except Exception as e:
        return f"ошибка вызова {name}: {e}"


@mcp.tool()
def agent_task(task: str, model: str = "qwen3.5-9b-instruct", max_steps: int = 6) -> str:
    """Мини-агент: решает задачу по шагам (план → вызовы инструментов → сводка).

    Используй для задач, требующих НЕСКОЛЬКИХ действий подряд: «проверь диск и
    найди, что занимает место», «найди заметки про X и составь сводку», «обработай
    все фото в папке Y и доложи».

    Args:
        task: задача обычным языком
        model: модель-агент (по умолчанию qwen3.5-9b-instruct)
        max_steps: максимум вызовов инструментов (1-10)
    """
    max_steps = max(1, min(max_steps, 10))
    messages = [
        {"role": "system", "content": AGENT_SYSTEM},
        {"role": "user", "content": f"Задача: {task}"},
    ]
    tools = _tool_specs()
    transcript = []
    for step in range(max_steps):
        payload = {
            "model": model, "messages": messages, "tools": tools,
            "max_tokens": 1500, "temperature": 0.3,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        try:
            r = requests.post(f"{ROUTER}/chat/completions", json=payload, timeout=600)
            r.raise_for_status()
            msg = r.json()["choices"][0]["message"]
        except Exception as e:
            return f"⛔ Ошибка модели на шаге {step + 1}: {e}"
        calls = msg.get("tool_calls") or []
        if not calls:
            final = (msg.get("content") or "").strip()
            log = "\n".join(transcript) if transcript else "(вызовы не потребовались)"
            return f"## Ход решения\n{log}\n\n## Итог\n{final}"
        messages.append({"role": "assistant", "content": msg.get("content") or "",
                         "tool_calls": calls})
        for call in calls:
            fn = call.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            result = _dispatch(name, args)
            transcript.append(f"{step + 1}. {name}({json.dumps(args, ensure_ascii=False)[:120]})")
            transcript.append(f"   → {result[:400].replace(chr(10), ' | ')}")
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                             "content": result[:6000]})
    return "## Ход решения\n" + "\n".join(transcript) + \
        "\n\n## Итог\n⚠️ Достигнут лимит шагов — задача не завершена, результаты выше."


# ============================================================ агенты по сложности
# Конфиг ролей — agents.json рядом с этим файлом. Маршрутизация по сложности:
# simple → medium → hard; coder/vision — по ключевым словам задачи.

AGENTS_CFG = BASE / "agents.json"


def _agents_cfg() -> dict:
    with open(AGENTS_CFG, encoding="utf-8") as fh:
        return json.load(fh)


def _pick_role(task: str, cfg: dict) -> tuple[str, str]:
    """(role_key, причина). Первое правило, чей keyword найден в задаче."""
    low = (task or "").lower()
    for rule in cfg.get("routing", {}).get("rules", []):
        for kw in rule.get("keywords", []):
            if kw and kw.lower() in low:
                return rule["role"], f"ключевое слово «{kw}»"
    return cfg.get("routing", {}).get("default", "medium"), "правила не сработали — уровень по умолчанию"


def _router_models() -> list[dict]:
    r = requests.get(f"{ROUTER}/models", timeout=10)
    r.raise_for_status()
    return r.json().get("data", [])


def _role_payload(role: dict, messages: list, use_tools: bool) -> dict:
    payload = {
        "model": role["model"],
        "messages": messages,
        "max_tokens": role.get("max_tokens", 1000),
        "temperature": role.get("temperature", 0.3),
    }
    if role["model"].startswith("qwen3.5"):
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    if use_tools and role.get("tools"):
        payload["tools"] = _tool_specs()
    return payload


def _chat_once(role: dict, messages: list, use_tools: bool = False) -> dict:
    """Один вызов роутера. Возвращает dict сообщения (assistant)."""
    r = requests.post(
        f"{ROUTER}/chat/completions",
        json=_role_payload(role, messages, use_tools),
        timeout=cfg_timeout(),
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]


def cfg_timeout() -> int:
    try:
        return int(_agents_cfg().get("timeout_chat", 600))
    except Exception:
        return 600


@mcp.tool()
def ai_status() -> str:
    """Статус локальных ИИ-агентов: роли из agents.json, что загружено в VRAM, память GPU.

    Используй перед тяжёлой задачей, чтобы понять, нужно ли грузить модель (ai_load),
    или для общего осмотра системы агентов.
    """
    try:
        cfg = _agents_cfg()
    except Exception as e:
        return f"⛔ agents.json не читается: {e}"
    try:
        models = _router_models()
        by_id = {m["id"]: m for m in models}
    except Exception as e:
        return f"⛔ Роутер :8081 не отвечает: {e}"

    lines = ["## Локальные ИИ-агенты", "", "| Роль | Модель | VRAM | Назначение |", "|---|---|---|---|"]
    for key, role in cfg["roles"].items():
        m = by_id.get(role["model"], {})
        st = (m.get("status") or {}).get("value", "нет")
        mark = "🟢 загружена" if st == "loaded" else ("⚪ выгружена" if st == "unloaded" else f"❓ {st}")
        lines.append(f"| {key} — {role['title']} | `{role['model']}` | {mark} | {role['purpose']} |")

    loaded = [m["id"] for m in models if (m.get("status") or {}).get("value") == "loaded"]
    lines += ["", f"**В VRAM сейчас:** {', '.join(loaded) if loaded else 'ничего (загрузятся по запросу)'}"]
    try:
        vram = run_command("nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader", timeout=10)
        lines += ["", f"**GPU:** {vram.strip()}"]
    except Exception:
        pass
    lines += ["", "Управление: ai_load(роль/модель), ai_unload(роль/модель/'all'), ai_ask(задача, role='auto')."]
    return "\n".join(lines)


@mcp.tool()
def ai_load(role: str = "") -> str:
    """Загрузить модель агента в VRAM (заранее, чтобы первый ответ был быстрым).

    Args:
        role: роль из agents.json (fast/medium/hard/coder/vision) ИЛИ id модели
              (например qwen3.5-9b-instruct). Пусто — показать список ролей.
    """
    try:
        cfg = _agents_cfg()
    except Exception as e:
        return f"⛔ agents.json не читается: {e}"
    roles = cfg["roles"]
    if not role or role == "list":
        opts = ", ".join(f"{k} ({v['model']})" for k, v in roles.items())
        return f"Роли: {opts}. Укажи роль или id модели."
    model_id = roles.get(role, {}).get("model", role)  # роль -> модель, иначе считаем что это id модели
    try:
        r = requests.post(
            f"{ROUTER}/models/load",
            json={"model": model_id},
            timeout=cfg_timeout(),
        )
        if r.status_code == 404:
            # роутер без эндпойнта preload — греем реальным запросом
            warm = {"model": model_id, "messages": [{"role": "user", "content": "ok"}], "max_tokens": 1}
            requests.post(f"{ROUTER}/chat/completions", json=warm, timeout=cfg_timeout())
        elif r.status_code >= 400:
            return f"⛔ ai_load: HTTP {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return f"⛔ ai_load: {e}"
    # подтверждаем статус
    try:
        for m in _router_models():
            if m["id"] == model_id:
                st = (m.get("status") or {}).get("value", "?")
                mark = "🟢 загружена" if st == "loaded" else f"⚪ {st}"
                return f"{mark}: {model_id} (роль: {role})"
    except Exception:
        pass
    return f"запрос на загрузку {model_id} отправлен (роль: {role})"


@mcp.tool()
def ai_unload(role: str = "all") -> str:
    """Выгрузить модель(и) из VRAM, освободить память.

    Args:
        role: роль (fast/medium/hard/coder/vision), id модели или 'all' — выгрузить все.
    """
    try:
        cfg = _agents_cfg()
    except Exception as e:
        return f"⛔ agents.json не читается: {e}"
    roles = cfg["roles"]
    try:
        models = _router_models()
    except Exception as e:
        return f"⛔ Роутер не отвечает: {e}"

    targets = []
    if role == "all":
        targets = [m["id"] for m in models if (m.get("status") or {}).get("value") == "loaded"]
    else:
        mid = roles.get(role, {}).get("model", role)
        targets = [mid]

    results = []
    for mid in targets:
        try:
            r = requests.post(f"{ROUTER}/models/unload", json={"model": mid}, timeout=60)
            if r.status_code < 400:
                results.append(f"🟢 выгружена: {mid}")
            elif "not running" in r.text:
                results.append(f"⚪ уже была выгружена: {mid}")
            else:
                results.append(f"⛔ {mid}: HTTP {r.status_code} {r.text[:120]}")
        except Exception as e:
            results.append(f"⛔ {mid}: {e}")
    try:
        vram = run_command("nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader", timeout=10)
        results.append(f"GPU: {vram.strip()}")
    except Exception:
        pass
    return "\n".join(results)


@mcp.tool()
def ai_ask(task: str, role: str = "auto", use_tools: bool = True) -> str:
    """Ответить на задачу подходящим по сложности локальным агентом (один проход).

    Маршрутизация при role='auto' по ключевым словам: зрение/скриншоты → vision,
    «напиши код/скрипт» → coder, мелочь («скажи», «переведи слово») → fast,
    анализ/план/поиск в заметках → hard (qwen3.5-9b), остальное → medium (llama-3.1-8b).

    Args:
        task: задача/вопрос обычным языком
        role: auto | fast | medium | hard | coder | vision (или id модели)
        use_tools: давать агенту инструменты (run_command, obsidian_search и др.)
    """
    try:
        cfg = _agents_cfg()
    except Exception as e:
        return f"⛔ agents.json не читается: {e}"
    roles = cfg["roles"]
    if role == "auto":
        role_key, why = _pick_role(task, cfg)
    else:
        role_key, why = role, "указан явно"
    role_cfg = roles.get(role_key)
    if not role_cfg:
        # считаем, что передан id модели
        role_cfg = {"model": role_key, "max_tokens": 1500, "temperature": 0.3, "tools": use_tools, "title": role_key}
    try:
        msg = _chat_once(role_cfg, [{"role": "user", "content": task}], use_tools)
    except Exception as e:
        return f"⛔ ai_ask ({role_cfg['model']}): {e}"
    text = (msg.get("content") or "").strip()
    calls = msg.get("tool_calls") or []
    head = f"**Агент:** {role_cfg.get('title', role_key)} (`{role_cfg['model']}`) — {why}"
    if calls:
        tool_names = ", ".join(c.get("function", {}).get("name", "?") for c in calls)
        return f"{head}\n\n⚠️ Модель запросила инструменты ({tool_names}), но ai_ask — однопроходный режим.\nДля многошаговой работы используй ai_agent(task).\n\n{text[:1500]}"
    return f"{head}\n\n{text}"


@mcp.tool()
def ai_agent(task: str, role: str = "hard", max_steps: int = 8) -> str:
    """Многошаговый агент: модель сама планирует и выполняет задачу инструментами
    (run_command, obsidian_search, ask_kb, photo_fix...), пока не решит её.

    Отличие от ai_ask: агент может делать СЕРИЮ действий подряд (проверил → нашёл →
    исправил → доложил). Для простых вопросов быстрее ai_ask.

    Args:
        task: задача обычным языком
        role: hard (по умолчанию, qwen3.5-9b) | medium | id модели
        max_steps: максимум шагов 1-15
    """
    try:
        cfg = _agents_cfg()
    except Exception as e:
        return f"⛔ agents.json не читается: {e}"
    role_cfg = cfg["roles"].get(role) or {
        "model": role, "max_tokens": 2000, "temperature": 0.3, "tools": True, "title": role,
    }
    max_steps = max(1, min(max_steps, 15))
    messages = [
        {"role": "system", "content": AGENT_SYSTEM},
        {"role": "user", "content": f"Задача: {task}"},
    ]
    tools = _tool_specs() if role_cfg.get("tools", True) else []
    transcript = []
    for step in range(max_steps):
        try:
            msg = _chat_once(role_cfg, messages, use_tools=bool(tools))
        except Exception as e:
            return f"⛔ Ошибка модели на шаге {step + 1}: {e}"
        calls = msg.get("tool_calls") or []
        if not calls:
            final = (msg.get("content") or "").strip()
            log = "\n".join(transcript) if transcript else "(вызовы не потребовались)"
            head = f"**Агент:** {role_cfg.get('title', role)} (`{role_cfg['model']}`), шагов: {step}\n\n"
            return head + f"## Ход решения\n{log}\n\n## Итог\n{final}"
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for call in calls:
            fn = call.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            result = _dispatch(name, args)
            transcript.append(f"{step + 1}. {name}({json.dumps(args, ensure_ascii=False)[:120]})")
            transcript.append(f"   → {result[:400].replace(chr(10), ' | ')}")
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": result[:6000]})
    return "## Ход решения\n" + "\n".join(transcript) + \
        "\n\n## Итог\n⚠️ Достигнут лимит шагов — задача не завершена, результаты выше."


# ---- swarm_tools: инструменты роя (web/glm/vision/stt) из SwarmConfig ----
try:
    import swarm_tools  # noqa: F401  регистрирует свои @mcp.tool() на этот же сервер
except Exception as _e:
    import sys as _sys
    print(f"swarm_tools не загрузился: {_e}", file=_sys.stderr)


# ============================================================ запуск

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8089)
