#!/usr/bin/env python3
"""Проверка inkscape-mcp (stdio): initialize + tools/list + пробное рисование SVG."""
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


rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "zcode-test", "version": "1.0"}})
rpc("notifications/initialized", {}, notify=True)

tools = rpc("tools/list", {})
names = sorted(t["name"] for t in tools.get("tools", []))
print(f"инструментов: {len(names)}", file=sys.stderr)
print(" ".join(names), file=sys.stderr)

proc.stdin.close()
proc.terminate()
