#!/usr/bin/env python3
"""Живой тест inkscape-mcp: validate SVG → export_preview в PNG (headless Inkscape)."""
import json
import subprocess
import sys

proc = subprocess.Popen(
    ["/home/rover/mcp-inkscape/inkscape-mcp/.venv/bin/inkscape-mcp", "--mode", "stdio"],
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
            if "error" in d:
                return {"error": d["error"]}
            return d.get("result", {})


def call(name, args):
    r = rpc("tools/call", {"name": name, "arguments": args}, hint=name)
    parts = [c.get("text", "") for c in r.get("content", []) if c.get("type") == "text"]
    return "\n".join(parts)


rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "zcode-test", "version": "1.0"}})
rpc("notifications/initialized", {}, notify=True)

print("validate:", call("inkscape_file", {
    "operation": "validate",
    "input_path": "/home/rover/mcp-inkscape/zvezda.svg",
})[:250], file=sys.stderr)

print("render:", call("inkscape_render", {
    "operation": "export_preview",
    "input_path": "/home/rover/mcp-inkscape/zvezda.svg",
    "output_path": "/home/rover/mcp-inkscape/zvezda.png",
    "dpi": 96,
})[:300], file=sys.stderr)

proc.stdin.close()
proc.terminate()
