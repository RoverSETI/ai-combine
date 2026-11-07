#!/usr/bin/env python3
"""Живой тест: GIMP headless рисует кистью и печатает снапшот холста (base64 PNG) в stdout.

Сохранение файла делает вызывающая сторона (bash: ... | base64 -d > картинка.png).
"""
import json
import subprocess
import sys

proc = subprocess.Popen(
    ["gimp-mcp", "serve"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    text=True,
)
_id = 0


def rpc(method, params=None, notify=False, hint=""):
    global _id
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    if not notify:
        _id += 1
        msg["id"] = _id
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    if notify:
        return None
    while True:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError(f"процесс закрылся ({hint})")
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("id") == _id:
            if "error" in d:
                return {"error": d["error"]}
            return d.get("result", {})


def call(name, args):
    r = rpc("tools/call", {"name": name, "arguments": args}, hint=name)
    content = r.get("content", [])
    return "\n".join(c.get("text", "") for c in content if c.get("type") == "text")


rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "zcode-test", "version": "1.0"}})
rpc("notifications/initialized", {}, notify=True)

print("new_image:", call("new_image", {"width": 640, "height": 400})[:120], file=sys.stderr)

# небо — полосами (points = [x1,y1,x2,y2,...], цвет прямо в вызове)
for y, color in [(0, "#2c3e7a"), (80, "#4a5fa5"), (160, "#7a6fb0")]:
    call("paintbrush", {"points": [0, y, 640, y], "size": 90, "color": color})

# солнце
call("paintbrush", {"points": [480, 110, 481, 111], "size": 55, "color": "#f5c542"})

# силуэты гор
call("paintbrush", {"points": [0, 330, 200, 250], "size": 60, "color": "#1a1a2e"})
call("paintbrush", {"points": [150, 280, 400, 360], "size": 60, "color": "#1a1a2e"})
call("paintbrush", {"points": [380, 360, 640, 270], "size": 60, "color": "#1a1a2e"})

# снапшот холста — GIMP сам сохраняет PNG через save_to
bmp = call("get_bitmap", {"max_size": 640,
                          "save_to": "/home/rover/mcp-gimp/gimp_ai_painting.png"})
try:
    d = json.loads(bmp)
    print("снапшот сохранён в:", d.get("saved_to"), "| inline:", d.get("inline"), file=sys.stderr)
except Exception:
    print("bitmap не JSON: " + str(bmp)[:200], file=sys.stderr)

proc.stdin.close()
proc.terminate()
