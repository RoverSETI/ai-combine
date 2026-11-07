#!/usr/bin/env python3
"""Печать всех имён инструментов gimp-mcp (stdio)."""
import json
import subprocess

proc = subprocess.Popen(["gimp-mcp", "serve"],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, text=True)
_id = 0


def rpc(method, params=None, notify=False):
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
            raise RuntimeError("закрылся")
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
                   "clientInfo": {"name": "list", "version": "1.0"}})
rpc("notifications/initialized", {}, notify=True)
tools = rpc("tools/list", {})
names = sorted(t["name"] for t in tools.get("tools", []))
print(len(names))
print(" ".join(names))
proc.stdin.close()
proc.terminate()
