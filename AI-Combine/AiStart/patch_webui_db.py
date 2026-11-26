#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""patch_webui_db.py — страж конфигурации Open WebUI перед стартом (21.11, расширен 26.11).

Схема конфига WebUI 0.13.3+: настройки, изменённые через админку, пишутся в
persistent-таблицу config в webui.db и ПЕРЕКРЫВАЮТ env из start_ai.sh.
Таблицы нет = конфиг env-ный (норма). Если таблица есть, страж сам сверяет
контрольные ключи со спекой webui_asserts.json (db_config_keys):
совпадение → выход 0, расхождение → выход 2 (правка через админку).

Что делает (только stdlib):
  1) PRAGMA quick_check — целостность webui.db;
  2) persistent-конфиг: сверка ключей db_config_keys со спекой (поштучно);
  3) env-сверка start_ai.sh со спекой webui_asserts.json.

Запуск:  python3 patch_webui_db.py [--apply] [--db PATH] [--start-ai PATH] [--spec PATH]
Выход:   0 — всё в порядке; 1 — БД повреждена/проблемы в env/сбой сверки;
         2 — persistent-конфиг разъехался со спекой.
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


def config_table_columns(con: sqlite3.Connection) -> list:
    """Список колонок таблицы config (пусто → таблицы нет). PRAGMA, без SQL."""
    return con.execute("PRAGMA table_info(config)").fetchall()


def check_db_config(con: sqlite3.Connection, spec: dict) -> tuple[int, list[str]]:
    """Сверка persistent-таблицы config со спекой (db_config_keys).

    Возвращает (число расхождений/сбоев, список строк-подробностей).
    Чтение БД — поштучно: на каждый ключ параметризованный запрос
    «key = ?» с биндингом ключа из спеки (без конкатенаций).
    """
    report: list[str] = []
    mismatches = 0
    expected_keys = spec.get("db_config_keys", [])
    if not expected_keys:
        return 0, ["⚪ в спеке нет db_config_keys — сверять нечего"]

    single_select = "SELECT value FROM config WHERE key = ?"
    actual: dict[str, str | None] = {}
    missing: set[str] = set()
    for item in expected_keys:
        key = str(item.get("key"))
        try:
            row = con.execute(single_select, (key,)).fetchone()
        except Exception as e:
            report.append("⛔ config[" + key + "]: запрос не удался: " + str(e))
            mismatches += 1
            continue
        if row is None:
            missing.add(key)
        else:
            actual[key] = row[0]

    for item in expected_keys:
        key = str(item.get("key"))
        expected = item.get("value")
        if key in missing:
            report.append("🟡 config[" + key + "]: ОТСУТСТВУЕТ в БД — ожидалось " + repr(expected))
            mismatches += 1
            continue
        raw = actual.get(key)
        try:
            actual_val = json.loads(raw) if raw is not None else None
        except (ValueError, TypeError):
            actual_val = raw
        if actual_val == expected or str(actual_val) == str(expected):
            report.append("🟢 config[" + key + "] = " + repr(actual_val))
        else:
            report.append("🟡 config[" + key + "] = " + repr(actual_val) + " — ожидалось " + repr(expected))
            mismatches += 1
    return mismatches, report


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

    spec = load_spec(Path(args.spec).expanduser())

    con = sqlite3.connect(str(db_path))
    try:
        cols = config_table_columns(con)
        if cols:
            print("ℹ️ persistent-таблица config есть (0.13.3+) — сверяю со спекой (db_config_keys):")
            mismatches, db_report = check_db_config(con, spec)
            for line in db_report:
                print("  " + line)
            db_broken = mismatches > 0
        else:
            print("⚪ таблица config отсутствует — конфиг env-ный (start_ai.sh), БД-сверка не требуется")
            mismatches = 0
            db_broken = False
    finally:
        con.close()

    problems = 0
    for line in check_env(Path(args.start_ai).expanduser(), spec):
        print("  " + line)
        if line.startswith(("⛔", "🟡")):
            problems += 1

    if db_broken:
        print("== Итог: persistent-конфиг разъехался со спекой (" + str(mismatches) +
              ") — правка через админку WebUI, контрольные значения в webui_asserts.json")
        return 2
    if problems:
        print("== Итог: в start_ai.sh " + str(problems) + " отклонение(й) — править руками, файл в git")
        return 1
    print("== Итог: всё на месте ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
