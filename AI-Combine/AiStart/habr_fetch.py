#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""habr_fetch.py — фоновое скачивание статей Habr в Obsidian с очередью и логом.

Команды:
  start  --days 30 --query "" --limit 0 --user roverseti   создать задание и запустить воркер
  status              прогресс текущего задания (или «нет активных»)
  stop                мягко остановить воркер (задание перейдёт в paused)
  resume              возобновить незавершённые задания (после перезагрузки)
  log [N]             последние N строк лога (по умолчанию 30)

Гарантии:
  - каждый скачанный файл сразу фиксируется в jobs.json (атомарная запись) —
    после сбоя/перезагрузки воркер пропускает готовое и продолжает с прерванного;
  - resume: воркер при старте подхватывает задания pending/paused/running;
  - лог: logs/habr_fetch.log.

Безопасность: сетевые запросы только через habr_bookmarks (гвард https+habr.com).
"""
import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent          # /home/rover/AiStart
RUN_DIR = BASE / "run"
LOG_DIR = BASE / "logs"
STATE_FILE = BASE / "habr_fetch_jobs.json"
LOG_FILE = LOG_DIR / "habr_fetch.log"
PID_FILE = RUN_DIR / "habr_fetch.pid"
PYTHON = str(BASE / ".venv-tools" / "bin" / "python")
DELAY_SEC = 3          # пауза между статьями (вежливость к Хабру)
ETA_PER_ARTICLE = 25   # секунд на статью для оценки ETA (с картинками)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(msg: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(f"[{_now()}] {msg}\n")


def _load_jobs() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {"jobs": []}
    return {"jobs": []}


def _save_jobs(data: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE_FILE)


def worker_alive() -> bool:
    """Жив ли воркер: PID-файл + процесс с habr_fetch.py в cmdline."""
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            cmdline = fh.read().decode("utf-8", "replace")
        if "habr_fetch.py" in cmdline:
            return True
    except Exception:
        pass
    return False


def _start_worker() -> bool:
    """Двойной fork + execv (без subprocess/shell). Воркер сам запишет PID."""
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    pid = os.fork()
    if pid == 0:
        # первый ребёнок
        try:
            os.setsid()
        except OSError:
            pass
        pid2 = os.fork()
        if pid2 == 0:
            # второй ребёнок → exec воркера
            try:
                fd = os.open(LOG_FILE, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                os.dup2(fd, 1)
                os.dup2(fd, 2)
                os.close(0)
            except Exception:
                pass
            os.execv(PYTHON, [PYTHON, str(BASE / "habr_fetch.py"), "_worker"])
            os._exit(127)  # exec не удался
        os._exit(0)
    os.waitpid(pid, 0)
    _log("запрошен запуск воркера (двойной fork)")
    return True


def cmd_start(args) -> str:
    """Создать задание и запустить воркер."""
    jobs = _load_jobs()
    for j in jobs["jobs"]:
        if (j["status"] in ("pending", "paused", "running")
                and j.get("user") == args.user and j.get("days") == args.days
                and (j.get("query") or "") == (args.query or "")):
            return (f"ℹ️ Такое задание уже в очереди (id {j['id'][:8]}, статус {j['status']}, "
                    f"готово {len(j.get('done_ids', {}))}/{len(j['article_ids'])}). "
                    f"Прогресс: habr_fetch status")
    sys.path.insert(0, str(BASE))
    import habr_bookmarks as hb
    items = hb.fetch_bookmarks(args.user)
    items = hb.filter_period(items, args.days)
    if args.query:
        items = hb.search_in(items, args.query)
    items = sorted(items, key=lambda x: x.get("timePublished") or "", reverse=True)
    if args.limit > 0:
        items = items[: args.limit]
    ids = [str(it.get("id")) for it in items if it.get("id")]

    # глобальная история скачанного: id, сохранённые в ЛЮБОМ предыдущем задании
    already = {}
    for j in jobs["jobs"]:
        for aid, res in (j.get("done_ids") or {}).items():
            if not str(res).startswith("ERROR"):
                already[str(aid)] = res
    prefill = {aid: already[aid] for aid in ids if aid in already}

    job = {
        "id": f"{_now()}_{os.getpid()}",
        "user": args.user,
        "days": args.days,
        "query": args.query or "",
        "status": "pending",
        "article_ids": ids,
        "done_ids": prefill,
        "current_id": "",
        "created": _now(),
        "updated": _now(),
    }
    jobs["jobs"].insert(0, job)
    _save_jobs(jobs)

    if not worker_alive():
        _start_worker()
    todo = len(ids) - len(prefill)
    if todo == 0:
        return (f"✅ Все {len(ids)} статей этого задания уже скачаны ранее — "
                f"перекачивать нечего. Список: habr_fetch status; лог: {LOG_FILE}")
    eta_min = max(1, round(todo * ETA_PER_ARTICLE / 60))
    return (
        f"⚠️ СКАЧИВАНИЕ ЗАПУЩЕНО В ФОНЕ\n"
        f"Задание: {todo} новых статей (из {len(ids)} за период {args.days} дн."
        + (f", фильтр «{args.query}»" if args.query else "")
        + f"; {len(prefill)} уже были скачаны — пропускаются). Оценка: ~{eta_min} мин"
        + (" (может занять 1-2 часа)" if eta_min > 60 else "") + "\n"
        f"❗ НЕ ПЕРЕЗАГРУЖАЙТЕ компьютер до окончания. Если перезагрузка случилась — "
        f"напишите «продолжить скачивание habr» (или habr_fetch resume), воркер "
        f"продолжит с прерванного места: готовые статьи не перекачиваются.\n"
        f"Лог: {LOG_FILE}\n"
        f"Прогресс: habr_fetch status"
    )


def _worker_loop() -> None:
    """Основной цикл воркера: очередь заданий, чекпоинт после каждой статьи."""
    sys.path.insert(0, str(BASE))
    import habr_bookmarks as hb

    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))
    _log(f"воркер начал работу (pid {os.getpid()})")

    stopping = {"flag": False}

    def _term(_sig, _frm):
        stopping["flag"] = True

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)

    while not stopping["flag"]:
        jobs = _load_jobs()
        job = next((j for j in jobs["jobs"] if j["status"] in ("pending", "paused", "running")), None)
        if job is None:
            _log("очередь пуста — воркер завершается")
            break
        job["status"] = "running"
        job["updated"] = _now()
        _save_jobs(jobs)

        done = job.setdefault("done_ids", {})
        total = len(job["article_ids"])
        for aid in job["article_ids"]:
            if stopping["flag"]:
                job["status"] = "paused"
                job["updated"] = _now()
                _save_jobs(jobs)
                _log(f"воркер остановлен по сигналу; задание {job['id'][:8]} → paused")
                return
            if aid in done:
                continue
            job["current_id"] = aid
            _save_jobs(jobs)
            try:
                res = hb.save_article(aid)
                if "error" in res:
                    done[aid] = f"ERROR: {res['error'][:120]}"
                    _log(f"FAIL id={aid}: {res['error'][:120]}")
                else:
                    done[aid] = res["vault_relative"]
                    _log(f"OK id={aid}: {res['title'][:60]} → {res['vault_relative'][:70]}")
            except Exception as e:
                done[aid] = f"ERROR: {str(e)[:120]}"
                _log(f"EXC id={aid}: {str(e)[:140]}")
            job["updated"] = _now()
            _save_jobs(jobs)
            n_ok = sum(1 for v in done.values() if not str(v).startswith("ERROR"))
            _log(f"прогресс: {len(done)}/{total} (успешно {n_ok})")
            time.sleep(DELAY_SEC)

        job["status"] = "done"
        job["current_id"] = ""
        job["updated"] = _now()
        _save_jobs(jobs)
        _log(f"задание {job['id'][:8]} завершено: {len(done)} статей")
    PID_FILE.unlink(missing_ok=True)


def cmd_status(as_json: bool = False) -> str:
    jobs = _load_jobs()
    active = [j for j in jobs["jobs"] if j["status"] in ("pending", "paused", "running")]
    if not active:
        done_recent = [j for j in jobs["jobs"] if j["status"] == "done"][:1]
        if done_recent:
            j = done_recent[0]
            ok = sum(1 for v in j["done_ids"].values() if not str(v).startswith("ERROR"))
            return f"✅ Активных заданий нет. Последнее (от {j['created']}) завершено: {ok} статей."
        return "ℹ️ Активных заданий нет."
    out = []
    for j in active[:3]:
        done = j.get("done_ids", {})
        ok = sum(1 for v in done.values() if not str(v).startswith("ERROR"))
        err = len(done) - ok
        total = len(j["article_ids"])
        remain = total - len(done)
        eta_min = max(1, round(remain * ETA_PER_ARTICLE / 60))
        cur = j.get("current_id") or ""
        out.append(
            f"⏳ Задание {j['id'][:8]} [{j['status']}] «{j['user']}», {j['days']} дн."
            + (f", фильтр «{j['query']}»" if j.get("query") else "") + "\n"
            f"   готово: {len(done)}/{total} (успешно {ok}, ошибок {err}), осталось ~{eta_min} мин\n"
            f"   текущая статья: id={cur or '—'}"
            + (f"\n   ⚠️ воркер НЕ запущен — вызовите habr_fetch_start (resume)"
               if not worker_alive() else "")
        )
    text = "\n".join(out)
    if as_json:
        return json.dumps({"worker_alive": worker_alive(), "text": text}, ensure_ascii=False)
    return text


def cmd_stop() -> str:
    if not worker_alive():
        return "ℹ️ Воркер не запущен."
    pid = int(PID_FILE.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        _log(f"команда stop → SIGTERM воркеру {pid}")
        time.sleep(1)
        return ("🛑 Воркер остановлен. Задание в статусе paused; продолжить — "
                "habr_fetch_start (resume: готовое не перекачивается).")
    except Exception as e:
        return f"⛔ Не удалось остановить: {e}"


def cmd_log(n: int) -> str:
    if not LOG_FILE.exists():
        return "ℹ️ Лог пуст."
    lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-n:])


def cmd_resume(_args) -> str:
    jobs = _load_jobs()
    active = [j for j in jobs["jobs"] if j["status"] in ("pending", "paused", "running")]
    if not active:
        return "ℹ️ Незавершённых заданий нет — продолжать нечего."
    if not worker_alive():
        _start_worker()
        total = sum(len(j["article_ids"]) - len(j.get("done_ids", {})) for j in active)
        return (f"▶️ Возобновляю: задание(й): {len(active)}, осталось статей: {total}. "
                f"❗ Не перезагружайте компьютер до окончания. Прогресс: habr_fetch status")
    return "ℹ️ Воркер уже работает.\n" + cmd_status()


def main() -> None:
    ap = argparse.ArgumentParser(description="Фоновое скачивание статей Habr в Obsidian")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_start = sub.add_parser("start", help="создать задание и запустить воркер")
    p_start.add_argument("--days", type=int, default=30)
    p_start.add_argument("--query", default="")
    p_start.add_argument("--limit", type=int, default=0, help="0 = все за период")
    p_start.add_argument("--user", default="roverseti")

    sub.add_parser("status", help="прогресс")
    sub.add_parser("stop", help="остановить воркер")
    p_log = sub.add_parser("log", help="хвост лога")
    p_log.add_argument("n", type=int, nargs="?", default=30)
    sub.add_parser("resume", help="возобновить незавершённые задания")
    sub.add_parser("_worker", help="внутреннее: запустить цикл воркера")

    args = ap.parse_args()
    if args.cmd == "start":
        print(cmd_start(args))
    elif args.cmd == "status":
        print(cmd_status())
    elif args.cmd == "stop":
        print(cmd_stop())
    elif args.cmd == "log":
        print(cmd_log(args.n))
    elif args.cmd == "resume":
        print(cmd_resume(args))
    elif args.cmd == "_worker":
        _worker_loop()


if __name__ == "__main__":
    main()
