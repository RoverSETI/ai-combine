# -*- coding: utf-8 -*-
"""Финальный E2E: модель obsidian-knowledge + Знание через WebSocket.

Ключевые требования 0.12.5 (найдены экспериментально и по коду):
- session_id = Socket.IO sid текущего соединения (как фронтенд $socket?.id);
- в теле запроса обязателен "id" — id assistant-сообщения (message_id),
  иначе событие не маршрутизируется (event_emitter = None);
- события приходят в socket-событии 'events' (комната user:{user_id}).

Безопасность: URL — полные константы loopback (127.0.0.1:8080), секреты
только через переменные окружения.
"""
import json
import os
import time
import urllib.parse
import urllib.request
import uuid

import socketio

URL_WS = "http://127.0.0.1:8080"
URL_CHAT = "http://127.0.0.1:8080/api/chat/completions"


def _assert_loopback(url: str) -> None:
    p = urllib.parse.urlsplit(url)
    if p.scheme != "http" or p.hostname != "127.0.0.1" or (p.port not in (8080, None)):
        raise ValueError(f"Запрещённый адрес: {url}")


for _u in (URL_WS, URL_CHAT):
    _assert_loopback(_u)

TOKEN = os.environ["WEBUI_JWT"]

sio = socketio.Client(logger=False, engineio_logger=False)
event_types = set()
chat_done = {"done": False, "content": "", "statuses": [], "sources": []}


def handle(ev):
    if not isinstance(ev, dict):
        return
    typ = ev.get("type")
    event_types.add(typ)
    if typ == "chat:completion":
        body = ev.get("data", {}) or {}
        if isinstance(body, dict):
            content = body.get("content") or ""
            if content:
                chat_done["content"] += content
            if body.get("done"):
                chat_done["done"] = True
                for s in body.get("sources") or []:
                    chat_done["sources"].append(json.dumps(s, ensure_ascii=False)[:200])
    elif typ == "status":
        d = ev.get("data", {}) or {}
        chat_done["statuses"].append(str(d.get("action") or d)[:70])


@sio.on("events")
def on_events(data, ack=None):
    if isinstance(data, dict):
        handle(data.get("data"))
    if ack is not None:
        ack({})


@sio.event
def connect():
    print("[WS] подключён, sid:", sio.get_sid())


@sio.event
def disconnect():
    print("[WS] отключён")


def main():
    sio.connect(
        URL_WS,
        auth={"token": TOKEN},
        transports=["websocket"],
        socketio_path="/ws/socket.io",
        wait_timeout=10,
    )
    sid = sio.get_sid()
    message_id = str(uuid.uuid4())
    chat_id = "temporary:" + str(uuid.uuid4())
    payload = {
        "model": "obsidian-knowledge",
        "messages": [
            {
                "role": "user",
                "content": "Какая команда в Manjaro показывает инфу о железе? Найди ответ в моих заметках и процитируй.",
            }
        ],
        "chat_id": chat_id,
        "id": message_id,
        "session_id": sid,
        "stream": False,
    }
    print("[HTTP] POST /api/chat/completions ...")
    _assert_loopback(URL_CHAT)
    req = urllib.request.Request(
        URL_CHAT,
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        print("[HTTP]", r.read().decode()[:150])

    t0 = time.time()
    while time.time() - t0 < 300 and not chat_done["done"]:
        sio.sleep(1)

    print("[WS] типы событий:", sorted(t for t in event_types if t))
    print("[WS] статусы:", chat_done["statuses"][:12])
    print("[RESULT] done:", chat_done["done"])
    if chat_done["sources"]:
        print("[RESULT] sources:")
        for s in chat_done["sources"][:5]:
            print("   -", s)
    print("[RESULT] content (первые 1200 символов):")
    print(chat_done["content"][:1200])
    sio.disconnect()


if __name__ == "__main__":
    main()
