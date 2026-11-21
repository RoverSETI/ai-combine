#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""patch_webui_db.py — страж конфигурации Open WebUI перед стартом (21.11).

Схема конфига WebUI 0.13.x: настройки держатся env-переменными из start_ai.sh
(WEBUI_AUTH, ENABLE_*_GENERATION, OPENAI_API_*). Таблицы config в webui.db нет;
если она ПОЯВИЛАСЬ (апгрейд/правка через админку) — она перекроет env. Страж
ловит это до старта сервиса.

Что делает (только stdlib, без SQL-литералов):
  1) PRAGMA quick_check — целостность webui.db;
  2) PRAGMA table_info(config) — появилась ли persistent-таблица config:
       - нет  → конфиг env-ный (норма), дальше только env-сверка;
       - есть → ⚠️ отчёт с ключами и инструкцией (правка через админку WebUI);
  3) env-сверка start_ai.sh со спекой webui_asserts.json.

Запуск:  python3 patch_webui_db.py [--apply] [--db PATH] [--start-ai PATH] [--spec PATH]
Выход:   0 — всё в порядке; 1 — БД повреждена/проблемы в env; 2 — есть таблица config (нужна ручная починка через админку).
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = ("/home/rover/AI-Combine/OpenWebUI-Local/.venv/lib/python3.11/site-packages"
              "/open_webui/data/webui.db")
DEFAULT_START_AI = "/home/rover/AI-Combine/AiStart/start_ai.sh"
DEFAULT_SPEC = "/home/rover/AI-Combine/AiStart/webui_asserts.json"


def load_spec(path: Path) -> dict:
    if not path.is_file():
        print("⚠️ спека " + str(path) + " не найдена — env-сверка пропускается")
        return {"env_start_ai": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def check_db_integrity(db_path: Path) -> tuple[bool, str]:
    try:
        con = sqlite3.connect(str(db_path))
        row = con.execute("PRAGMA quick_check").fetchone()
        con.close()
        ok = bool(row) and row[0] == "ok"
        return ok, (row[0] if row else "нет ответа")
    except Exception as e:
        return False, str(e)


def config_table_columns(con: sqlite3.Connection):
    """Список колонок таблицы config (пусто → таблицы нет). PRAGMA, без SQL."""
    return con.execute("PRAGMA table_info(config)").fetchall()


def check_env(start_ai: Path, spec: dict) -> list[str]:
    report: list[str] = []
    if not start_ai.is_file():
        return ["⛔ start_ai.sh не найден: " + str(start_ai)]
    text = start_ai.read_text(encoding="utf-8", errors="replace")
    for key, rule in spec.get("env_start_ai", {}).items():
        m = re.search(r"^export\s+" + re.escape(key) + r"\s*=\s*\"?([^\"\s#]*)", text, re.M)
        if not m:
            report.append("⛔ start_ai.sh: export " + key + " ОТСУТСТВУЕТ — WebUI потеряет настройку")
            continue
        expected = rule.get("value") if isinstance(rule, dict) else rule
        if expected is not None and m.group(1) != str(expected):
            report.append("🟡 export " + key + "=" + repr(m.group(1)) + " — ожидалось " + repr(str(expected)))
        else:
            report.append("🟢 export " + key + " — на месте")
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="Страж конфига Open WebUI (webui.db + start_ai.sh)")
    ap.add_argument("--apply", action="store_true",
                    help="сейчас совпадает с проверкой: правка env вручную, config — через админку WebUI")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--start-ai", default=DEFAULT_START_AI)
    ap.add_argument("--spec", default=DEFAULT_SPEC)
    args = ap.parse_args()

    db_path = Path(args.db).expanduser()
    print("== Страж webui: проверка перед стартом ==")

    if not db_path.is_file():
        print("⛔ webui.db не найден: " + str(db_path))
        return 1

    ok, note = check_db_integrity(db_path)
    print(("🟢 quick_check: " if ok else "⛔ quick_check: ") + note)
    if not ok:
        print("   → БД повреждена; восстановление из webui.db.bak-* вручную")
        return 1

    con = sqlite3.connect(str(db_path))
    try:
        cols = config_table_columns(con)
    finally:
        con.close()

    if cols:
        print("⚠️ В webui.db появилась persistent-таблица config — она ПЕРЕКРЫВАЕТ env start_ai.sh.")
        print("   Проверь и приведи ключи к контрольным значениям через админку WebUI")
        print("   (или сверяй вручную со спекой webui_asserts.json, ключ db_config_keys):")
        for item in json.loads(Path(args.spec).expanduser().read_text(encoding="utf-8")).get("db_config_keys", []):
            print("     - " + str(item.get("key")) + " = " + repr(item.get("value")))
        print("   Отчёт сохранён: /tmp/webui_config_report.txt")
        report_path = Path("/tmp/webui_config_report.txt")
        report_path.write_text(
            "persistent-таблица config в " + str(db_path) + "\n"
            "колонки: " + str([c[1] for c in cols]) + "\n"
            "контрольные ключи: webui_asserts.json → db_config_keys\n",
            encoding="utf-8")
        return 2

    print("⚪ таблица config отсутствует — конфиг env-ный (start_ai.sh), БД-сверка не требуется")

    problems = 0
    spec = load_spec(Path(args.spec).expanduser())
    for line in check_env(Path(args.start_ai).expanduser(), spec):
        print("  " + line)
        if line.startswith(("⛔", "🟡")):
            problems += 1

    if problems:
        print("== Итог: в start_ai.sh " + str(problems) + " отклонение(й) — править руками, файл в git")
        return 1
    print("== Итог: всё на месте ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
