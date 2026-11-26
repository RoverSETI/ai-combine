#!/usr/bin/env bash
# ============================================================
# wireshark_capture_toggle.sh — capture-профиль Wireshark-MCP по сессии
#
# Правило пользователя (26.11): capture ВКЛЮЧАЕТСЯ только по явной просьбе,
# на одну сессию, ВЫКЛЮЧАЕТСЯ по слову. Не в автозапуске!
#
# Использование:
#   wireshark_capture_toggle.sh on      # поднять capture-инстанс :8096
#   wireshark_capture_toggle.sh off     # убить capture-инстанс (analysis вернёт start_ai.sh)
#   wireshark_capture_toggle.sh status  # что сейчас слушает :8096
#
# Механика: analysis-инстанс (start_ai.sh, :8096) останавливается,
# поднимается full-профиль на тот же порт. ZCode/WebUI подключение не меняют.
# Запись — только в AI-Combine/wireshark-captures/.
# ============================================================
set -u

RUN_DIR="/home/rover/AI-Combine/AiStart/run"
LOG_DIR="/home/rover/AI-Combine/AiStart/logs"
WSMCP_BIN="/home/rover/AI-Combine/wireshark-mcp/.venv/bin/wireshark-mcp"
CAPTURES="/home/rover/AI-Combine/wireshark-captures"
PORT=8096

mkdir -p "$CAPTURES"

wsmcp_pids() {
    pgrep -f "wireshark-mcp serve" 2>/dev/null || true
}

kill_all_wsmcp() {
    for p in $(wsmcp_pids); do
        kill -9 "$p" 2>/dev/null || true
    done
    sleep 1
}

start_instance() { # $1 = profile name, $2 = log tag
    : > "$LOG_DIR/wireshark_mcp_bridge.log"
    WIRESHARK_MCP_ALLOWED_DIRS="$CAPTURES" \
    WIRESHARK_MCP_TSHARK_PATH="/usr/sbin/tshark" \
    WIRESHARK_MCP_CAPINFOS_PATH="/usr/sbin/capinfos" \
    nohup "$WSMCP_BIN" serve \
        --transport streamable-http --host 127.0.0.1 --port $PORT \
        --profile "$1" \
        >> "$LOG_DIR/wireshark_mcp_bridge.log" 2>&1 < /dev/null &
    local pid=$!
    sleep 2
    if curl -sf -m 3 "http://127.0.0.1:$PORT/mcp" -o /dev/null 2>/dev/null; then
        echo "$2-инстанс поднят (PID $pid, профиль $1, порт $PORT)"
        return 0
    fi
    # MCP может отвечать только на POST — проверяем, что порт слушает
    if ss -tlnp 2>/dev/null | grep -q ":$PORT .*pid=$pid"; then
        echo "$2-инстанс поднят (PID $pid, профиль $1, порт $PORT)"
        return 0
    fi
    echo "ОШИБКА: $2-инстанс не поднялся — лог $LOG_DIR/wireshark_mcp_bridge.log"
    return 1
}

case "${1:-}" in
    on)
        kill_all_wsmcp
        start_instance full "Capture"
        echo "ЗАПИСЬ РАЗРЕШЕНА ТОЛЬКО В: $CAPTURES"
        echo "Выключение: wireshark_capture_toggle.sh off"
        ;;
    off)
        kill_all_wsmcp
        echo "capture-инстансы убиты. analysis вернётся сам при следующем start_ai.sh,"
        echo "или поднимите вручную:"
        echo "  WIRESHARK_MCP_ALLOWED_DIRS=$CAPTURES nohup $WSMCP_BIN serve --transport streamable-http --host 127.0.0.1 --port 8096 --profile analysis &"
        echo "(или просто перезапустите start_ai.sh)"
        ;;
    status)
        if [ -z "$(wsmcp_pids)" ]; then
            echo ":8096 молчит — Wireshark-MCP не запущен"
        else
            for p in $(wsmcp_pids); do
                prof=$(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null | grep -oE '\-\-profile [a-z]+' | awk '{print $2}')
                echo "PID $p: профиль $prof"
            done
            echo "Захват: dumpcap интерфейсы: $(dumpcap -D 2>/dev/null | head -4 | tr '\n' '; ')"
            echo "Папка записи: $CAPTURES"
        fi
        ;;
    *)
        echo "Использование: $0 {on|off|status}"
        exit 1
        ;;
esac
