#!/bin/bash
# ============================================================
#  status_ai.sh — диагностика ИИ-комбайна
#  Процессы, роутер, модели, эмбеддер, WebUI, VRAM
# ============================================================
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$BASE_DIR/run"; LOG_DIR="$BASE_DIR/logs"
ROUTER_BIND="127.0.0.1"; ROUTER_PORT=8081
EMBED_BIND="127.0.0.1";  EMBED_PORT=8082
WEBUI_BIND="127.0.0.1";  WEBUI_PORT=8080
MCP_BIND="127.0.0.1";    MCP_PORT=8089
OBSIDIAN_PORT=27123      # плагин Obsidian «Local REST API with MCP» (HTTP)

echo "================== СТАТУС ИИ-КОМБАЙНА =================="

# --- процессы ---
echo
echo "── Процессы (из PID-файлов) ──"
any=0
for pf in "$RUN_DIR"/*.pid; do
    [ -f "$pf" ] || continue
    pid=$(cat "$pf" 2>/dev/null)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        echo "  ✅ $(basename "$pf" .pid): PID $pid"
        any=1
    else
        echo "  ⛔ $(basename "$pf" .pid): PID ${pid:-?} не отвечает"
    fi
done
[ "$any" = 0 ] && echo "  (комплекс не запущен или стартован без start_ai.sh)"
pgrep -f 'llama-server' >/dev/null 2>&1 && echo "  ℹ️  доп. процессы llama-server — это дочерние серверы роутера (норма)"

# --- роутер ---
echo
echo "── Роутер llama-server ($ROUTER_BIND:$ROUTER_PORT) ──"
if curl -sf "http://$ROUTER_BIND:$ROUTER_PORT/health" >/dev/null 2>&1; then
    echo "  ✅ отвечает"
    models=$(curl -s "http://$ROUTER_BIND:$ROUTER_PORT/v1/models" \
        | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)["data"]
    for m in data:
        st = m.get("status", {}).get("value", "?")
        mark = "🟢" if st == "loaded" else "⚪"
        print(mark, m["id"])
except Exception:
    pass' 2>/dev/null)
    echo "$models" | sed 's/^/     /'
    echo "  (🟢 = в VRAM, ⚪ = выгружена, загрузится по первому запросу)"
else
    echo "  ⛔ не отвечает"
    [ -f "$LOG_DIR/llama_router.log" ] && echo "     последние строки лога:" && tail -n 5 "$LOG_DIR/llama_router.log" | sed 's/^/       /'
fi

# --- эмбеддер ---
echo
echo "── Эмбеддинг-сервер ($EMBED_BIND:$EMBED_PORT) ──"
if curl -sf "http://$EMBED_BIND:$EMBED_PORT/v1/models" >/dev/null 2>&1; then
    echo "  ✅ отвечает (gte-modernbert, для базы знаний)"
else
    echo "  ⛔ не отвечает (семантический поиск недоступен)"
    [ -f "$LOG_DIR/llama_embed.log" ] && tail -n 5 "$LOG_DIR/llama_embed.log" | sed 's/^/       /'
fi

# --- webui ---
echo
echo "── Open WebUI ($WEBUI_BIND:$WEBUI_PORT) ──"
if curl -sf "http://$WEBUI_BIND:$WEBUI_PORT/health" >/dev/null 2>&1; then
    echo "  ✅ отвечает"
else
    echo "  ⛔ не отвечает"
    [ -f "$LOG_DIR/open_webui.log" ] && echo "     последние строки лога:" && tail -n 5 "$LOG_DIR/open_webui.log" | sed 's/^/       /'
fi

# --- mcp local-tools ---
echo
echo "── MCP local-tools ($MCP_BIND:$MCP_PORT/mcp) ──"
mcp_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
    "http://$MCP_BIND:$MCP_PORT/mcp" \
    -H 'Accept: application/json, text/event-stream' \
    -H 'Content-Type: application/json' \
    -H 'MCP-Protocol-Version: 2025-06-18' \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"status_ai","version":"1.0"}}}' 2>/dev/null)
if [ "$mcp_code" = "200" ]; then
    echo "  ✅ отвечает (run_command, obsidian_search/read, habr_scan, ask_kb, photo_fix, agent_task)"
else
    echo "  ⛔ не отвечает (код $mcp_code) — MCP-инструменты в чате недоступны"
    [ -f "$LOG_DIR/mcp_server.log" ] && echo "     последние строки лога:" && tail -n 5 "$LOG_DIR/mcp_server.log" | sed 's/^/       /'
fi

# --- obsidian-плагин (только когда Obsidian запущен) ---
if ss -tln 2>/dev/null | grep -q ":$OBSIDIAN_PORT "; then
    echo
    echo "── Obsidian Local REST API (:$OBSIDIAN_PORT) ──"
    echo "  ✅ запущен — MCP-подключение 'obsidian' доступно в чате"
else
    echo
    echo "── Obsidian Local REST API (:$OBSIDIAN_PORT) ──"
    echo "  ⚪ не запущен (Obsidian закрыт) — запись в заметки из чата недоступна,"
    echo "     obsidian_search/obsidian_read (local-tools) работают и без него"
fi

# --- vram ---
if command -v nvidia-smi >/dev/null 2>&1; then
    echo
    echo "── Видеопамять RTX 3060 ──"
    nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv,noheader | sed 's/^/  /'
    nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader 2>/dev/null | sed 's/^/  /'
fi
echo
echo "========================================================="
