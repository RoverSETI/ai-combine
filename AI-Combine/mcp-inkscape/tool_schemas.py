#!/usr/bin/env python3
"""Схемы ключевых инструментов inkscape-mcp."""
import json
import subprocess
import sys

proc = subprocess.Popen(
    ["uv", "run", "inkscape-mcp", "--mode", "stdio"],
    cwd="/home/rover/mcp-inkscape/inkscape-mcp",
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
            raise RuntimeError(f"закрылся ({hint})")
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("id") == _id:
            return d.get("result", {})


rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "t", "version": "1.0"}})
rpc("notifications/initialized", {}, notify=True)
tools = {t["name"]: t for t in rpc("tools/list", {}).get("tools", [])}

for name in ("generate_svg", "inkscape_render", "inkscape_file"):
    t = tools.get(name)
    if not t:
        print(f"=== {name}: НЕТ ===", file=sys.stderr)
        continue
    print(f"=== {name} ===", file=sys.stderr)
    print(t.get("description", "")[:300], file=sys.stderr)
    print("параметры:", json.dumps(t.get("inputSchema", {}).get("properties", {}),
                                   ensure_ascii=False)[:700], file=sys.stderr)
    print(file=sys.stderr)

proc.stdin.close()
proc.terminate()
