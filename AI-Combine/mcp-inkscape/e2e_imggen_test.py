#!/usr/bin/env python3
"""E2E: локальная qwen3.5-9b в чате WebUI сама вызывает image_generate (с ai_unload)."""
import asyncio
import json
import uuid

import aiohttp
import socketio

BASE = "http://localhost:8080"


async def signin() -> str:
    async with aiohttp.ClientSession() as s:
        async with s.post(f"{BASE}/api/v1/auths/signin",
                          json={"email": "admin@localhost", "password": "admin"}) as r:
            return (await r.json())["token"]


async def run_chat(question: str, model: str, tool_ids: list[str], timeout_s: int = 900):
    token = await signin()
    chat_id = f"temporary:{uuid.uuid4()}"
    session_id = str(uuid.uuid4())

    sio = socketio.AsyncClient()
    tool_calls, outputs, content_parts, done = [], [], [], asyncio.Event()

    @sio.on("events")
    async def on_events(data):
        d = data.get("data") or {}
        etype = d.get("type")
        if etype in ("chat:completion", "response:completion"):
            for item in (d.get("data") or {}).get("output", []) or []:
                t = item.get("type")
                if t == "function_call":
                    tool_calls.append((item.get("name"), item.get("arguments", "")[:150]))
                elif t == "function_call_output":
                    txt = " ".join(p.get("text", "") for p in item.get("output", [])
                                   if isinstance(p, dict) and p.get("type") == "input_text")
                    outputs.append(txt)
                elif t == "message":
                    for part in item.get("content", []) or []:
                        if isinstance(part, dict) and part.get("type") == "output_text":
                            content_parts.append(part.get("text", ""))
        elif etype == "chat:message:error":
            outputs.append("ОШИБКА: " + json.dumps(d.get("data", {}), ensure_ascii=False)[:200])
            done.set()
        elif etype == "chat:active" and d.get("data", {}).get("active") is False:
            done.set()

    await sio.connect(BASE, auth={"token": token}, transports=["websocket"],
                      socketio_path="/ws/socket.io")
    await asyncio.sleep(0.5)

    async with aiohttp.ClientSession() as http:
        async with http.post(f"{BASE}/api/chat/completions",
                             headers={"Authorization": f"Bearer {token}"},
                             json={
                                 "model": model,
                                 "id": str(uuid.uuid4()),
                                 "chat_id": chat_id,
                                 "session_id": session_id,
                                 "tool_ids": tool_ids,
                                 "messages": [{"id": str(uuid.uuid4()), "role": "user",
                                               "content": question}],
                                 "stream": True,
                             }) as r:
            ack = await r.json()
    if not ack.get("task_ids"):
        print("нет task_ids:", ack)
        await sio.disconnect()
        return
    try:
        await asyncio.wait_for(done.wait(), timeout=timeout_s)
    except asyncio.TimeoutError:
        pass
    await sio.disconnect()

    print(f"ВОПРОС: {question[:80]}...")
    for i, (name, args) in enumerate(tool_calls, 1):
        print(f"TOOL {i}: {name}({args})")
    if outputs:
        print("RESULTS:", (outputs[0][:250]).replace("\n", " | "))
    print("ANSWER:", ("".join(content_parts)).strip()[:700])


async def main():
    await run_chat(
        "Сгенерируй картинку: фотореалистичный рыжий кот в скафандре космонавта на фоне Марса. "
        "Если не хватает видеопамяти — сначала вызови ai_unload all, затем повтори генерацию "
        "и верни мне путь к файлу.",
        "qwen3.5-9b-instruct",
        ["server:mcp:local-tools"],
    )


if __name__ == "__main__":
    asyncio.run(main())
