# -*- coding: utf-8 -*-
"""E2E: чат Open WebUI с tool-calling — модель сама вызывает habr_search / kb_search.

Проверяет требование пользователя:
- «Хабр: …» → модель вызывает habr_search (поиск по сайту по любым словам);
- «Локал: …» → модель вызывает kb_search (локальная база: название+конспект);
- при пустом результате инструмент возвращает подсказку «где искать дальше».

Запуск: python3 e2e_habr_kb_tools.py   (без аргументов — 3 зашитых сценария)
Все URL — литеральные константы loopback 127.0.0.1:8080, проверяются при старте.
"""
import json
import time
import urllib.parse
import urllib.request
import uuid

import socketio

URL_WS = "http://127.0.0.1:8080"
URL_CHAT = "http://127.0.0.1:8080/api/chat/completions"
MODEL = "obsidian-knowledge"

# Зашитые сценарии (константы; никаких данных из argv/env в запросах)
SCENARIOS = [
    "Хабр: настройка CUDA llama.cpp — найди статьи на habr.com и дай ссылки",
    "Локал: что в моей базе знаний про TURN сервер для WebRTC?",
    "Найди в моей базе знаний заметку про взлом корпоративной цензуры нейросети",
]


def _assert_loopback(url: str) -> None:
    p = urllib.parse.urlsplit(url)
    if p.scheme != "http" or p.hostname != "127.0.0.1" or p.port != 8080:
        raise ValueError(f"Запрещённый адрес: {url}")


for _u in (URL_WS, URL_CHAT):
    _assert_loopback(_u)

with open("/tmp/webui_jwt.txt", encoding="utf-8") as fh:
    TOKEN = fh.read().strip()

sio = socketio.Client(logger=False, engineio_logger=False)
state = {"done": False, "content": "", "statuses": []}


def reset_state():
    state["done"] = False
    state["content"] = ""
    state["statuses"] = []


def handle(ev):
    if not isinstance(ev, dict):
        return
    typ = ev.get("type")
    if typ == "chat:completion":
        body = ev.get("data", {}) or {}
        if isinstance(body, dict):
            content = body.get("content") or ""
            if content:
                state["content"] += content
            if body.get("done"):
                state["done"] = True
    elif typ == "status":
        d = ev.get("data", {}) or {}
        state["statuses"].append(str(d.get("action") or d)[:80])


@sio.on("events")
def on_events(data, ack=None):
    if isinstance(data, dict):
        handle(data.get("data"))
    if ack is not None:
        ack({})


@sio.event
def connect():
    print("[WS] sid:", sio.get_sid())


def run_chat(question: str) -> None:
    reset_state()
    message_id = str(uuid.uuid4())
    chat_id = "temporary:" + str(uuid.uuid4())
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": question}],
        "chat_id": chat_id,
        "id": message_id,
        "session_id": sio.get_sid(),
        "stream": True,  # нативные инструменты работают только на streaming-пути
    }
    print(f"\n{'='*70}\n[СЦЕНАРИЙ] {question}\n{'='*70}")
    req = urllib.request.Request(
        "http://127.0.0.1:8080/api/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": "Bearer " + TOKEN,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        print("[HTTP]", r.read().decode()[:120])

    t0 = time.time()
    while time.time() - t0 < 420 and not state["done"]:
        sio.sleep(1)

    print("[WS] статусы:", state["statuses"][:15])
    print("[RESULT] done:", state["done"])
    print("[RESULT] ответ:")
    print(state["content"][:2500])


def main() -> None:
    sio.connect(
        "http://127.0.0.1:8080",
        auth={"token": TOKEN},
        transports=["websocket"],
        socketio_path="/ws/socket.io",
        wait_timeout=10,
    )
    for q in SCENARIOS:
        run_chat(q)
    sio.disconnect()


if __name__ == "__main__":
    main()
