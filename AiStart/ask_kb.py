#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ask_kb.py — вопрос к базе знаний (конспекты Habr в Obsidian) с ответом
локальной модели и пошаговым ходом решения.

Как работает:
  1. Ваш вопрос превращается в вектор (gte-modernbert :8082)
  2. Косинусный поиск подбирает самые релевантные конспекты
     (fallback: поиск по ключевым словам, если эмбеддер недоступен)
  3. Модель (по умолчанию qwen2.5-coder-7b) отвечает ПО КОНТЕКСТУ базы,
     показывая ход решения по шагам и ссылаясь на источники

Запуск:
  .venv-tools/bin/python ask_kb.py "как примонтировать NTFS диск в arch?"
  .venv-tools/bin/python ask_kb.py "..." --model qwen3.5-9b-instruct --top 7
"""
import argparse
import json
import math
import sys
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
CONFIG = json.loads((BASE / "habr_config.json").read_text(encoding="utf-8"))
INDEX_FILE = BASE / CONFIG["index_file"]
UA = {"User-Agent": "ask_kb/1.0"}

QA_SYSTEM = (
    "Ты — инженерный ассистент с локальной базой знаний (конспекты статей Habr).\n"
    "Правила ответа:\n"
    "- Отвечай по-русски, кратко и по делу.\n"
    "- По задачам с кодом показывай ХОД РЕШЕНИЯ по шагам: 1) суть проблемы, "
    "2) план, 3) шаги с командами/кодом, 4) как проверить результат.\n"
    "- Опирайся на контекст из базы знаний; в конце ответа приведи раздел "
    "«Источники» со списком ссылок, которые реально использовал.\n"
    "- Если в базе нет нужного — скажи об этом прямо и отвечай из общих знаний."
)


def load_index() -> list:
    if INDEX_FILE.exists():
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    return []


def embed(text: str):
    try:
        r = requests.post(f"{CONFIG['embed_api']}/embeddings",
                          json={"model": CONFIG["embed_model"], "input": text[:4000]},
                          timeout=60, headers=UA)
        r.raise_for_status()
        return r.json()["data"][0]["embedding"]
    except Exception:
        return None


def cosine(a, b) -> float:
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a)) or 1.0
    db = math.sqrt(sum(x * x for x in b)) or 1.0
    return num / (da * db)


def search_semantic(question: str, index: list, top: int) -> list:
    qv = embed(question)
    if qv is None:
        return None
    scored = sorted(
        ((cosine(qv, e["vec"]), e) for e in index if e.get("vec")),
        key=lambda t: -t[0],
    )
    return scored[:top]


def search_keywords(question: str, index: list, top: int) -> list:
    """Скоринг по всем словам вопроса: заголовок и теги ×3, тело заметки ×1."""
    words = [w for w in "".join(c if c.isalnum() else " " for c in question.lower()).split() if len(w) > 2]
    scored = []
    for e in index:
        hay_title = (e.get("title", "") + " " + " ".join(e.get("tags", []))).lower()
        try:
            body = Path(e.get("file", "")).read_text(encoding="utf-8", errors="ignore").lower()
        except Exception:
            body = ""
        score = 0.0
        matched_words = []
        for w in words:
            hits_title = hay_title.count(w)
            hits_body = body.count(w)
            if hits_title + hits_body:
                score += 3.0 * min(hits_title, 3) + 1.0 * min(hits_body, 10)
                matched_words.append(w)
        if score:
            e2 = dict(e)
            e2["_words"] = matched_words
            scored.append((score, e2))
    return sorted(scored, key=lambda t: -t[0])[:top]


def note_excerpt(path: str, limit: int = 1600) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8")
        if text.startswith("---"):  # срезать frontmatter
            parts = text.split("---", 2)
            text = parts[2] if len(parts) == 3 else text
        text = text.strip()
        # приоритет: до раздела «Оригинал» — там конспект
        if "## Оригинал" in text:
            text = text.split("## Оригинал")[0]
        return text[:limit]
    except Exception:
        return "(файл заметки недоступен)"


def answer(question: str, hits, model: str, top: int):
    if hits:
        ctx_blocks = [
            f"<источник {i + 1}>\nЗаголовок: {e['title']}\nСсылка: {e['url']}\nКонспект:\n{note_excerpt(e['file'])}\n</источник {i + 1}>"
            for i, (score, e) in enumerate(hits)
        ]
        context = "\n\n".join(ctx_blocks)
    else:
        context = "(в базе знаний нет релевантных заметок)"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": QA_SYSTEM},
            {"role": "user", "content": f"Вопрос: {question}\n\nКонтекст базы знаний:\n{context}"},
        ],
        "max_tokens": 2000,
        "temperature": 0.4,
        "stream": True,
    }
    print(f"\n🧠 Модель: {model}; релевантных заметок: {len(hits)}\n" + "─" * 60)
    sources_printed = False
    with requests.post(f"{CONFIG['router_api']}/chat/completions",
                       json=payload, timeout=600, headers=UA, stream=True) as r:
        r.raise_for_status()
        r.encoding = "utf-8"  # SSE-чанки всегда UTF-8, иначе mojibake
        for line in r.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            data = line[6:]
            if data == "[DONE]":
                break
            try:
                delta = json.loads(data)["choices"][0].get("delta", {})
            except Exception:
                continue
            content = delta.get("content")
            if content:
                print(content, end="", flush=True)
            # reasoning «думающих» моделей показываем приглушённо
            reason = delta.get("reasoning_content")
            if reason and not sources_printed:
                print(f"\x1b[2m{reason}\x1b[0m", end="", flush=True)
    print("\n" + "─" * 60)
    if hits:
        print("📚 Найдено в базе:")
        for score, e in hits:
            sim = f"{score:.3f}" if score <= 1 else f"{int(score)} совпад."
            print(f"   • [{sim}] {e['title']}")
            print(f"     {e['url']}")
    else:
        print("📚 База знаний пуста или ничего релевантного — запустите habr_scan.py")


def main() -> int:
    ap = argparse.ArgumentParser(description="Вопрос к базе знаний Habr-конспектов")
    ap.add_argument("question", help="ваш вопрос")
    ap.add_argument("--model", default=CONFIG["model_qa_default"],
                    help=f"модель (по умолчанию {CONFIG['model_qa_default']})")
    ap.add_argument("--top", type=int, default=5, help="сколько заметок подавать в контекст")
    args = ap.parse_args()

    index = load_index()
    if not index:
        print("📭 База знаний пуста. Сначала запустите сканер: habr_scan.py")
        print("   (или проверьте, что комбайн запущен: /home/rover/AiStart/start_ai.sh)")
        return 1

    hits = search_semantic(args.question, index, args.top)
    if hits is None:
        print("⚠️  Эмбеддер недоступен — использую поиск по ключевым словам")
        hits = search_keywords(args.question, index, args.top)
    try:
        answer(args.question, hits, args.model, args.top)
    except Exception as e:
        print(f"\n❌ Ошибка запроса к модели: {e}")
        print("   Проверьте, что комбайн запущен: /home/rover/AiStart/start_ai.sh")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
