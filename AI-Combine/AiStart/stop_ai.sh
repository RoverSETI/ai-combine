#!/bin/bash
# ============================================================
#  stop_ai.sh — остановка ИИ-комбайна (БЕЗ sudo!)
#
#  1) SIGTERM по PID-файлам → до 5 с на корректный выход
#  2) SIGKILL выжившим
#  3) страховка по ТОЧНЫМ образцам имён процессов
#
#  ВНИМАНИЕ: старый скрипт делал «sudo pkill -9 -f python3» и убивал
#  ВСЕ процессы python3 в системе. Здесь такого нет и быть не должно.
# ============================================================

QUIET=${1:-}
say() { [ "$QUIET" = "--quiet" ] || echo -e "$1"; }

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$BASE_DIR/run"

say "🛑 Останавливаю ИИ-комбайн..."

# --- 1. мягкая остановка по PID-файлам ---
pids=()
for pf in "$RUN_DIR"/*.pid; do
    [ -f "$pf" ] || continue
    pid=$(cat "$pf" 2>/dev/null)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        pids+=("$pid")
        kill "$pid" 2>/dev/null
    fi
done

if [ ${#pids[@]} -gt 0 ]; then
    say "   SIGTERM → ${pids[*]}"
    for _ in 1 2 3 4 5; do
        alive=0
        for pid in "${pids[@]}"; do kill -0 "$pid" 2>/dev/null && alive=1; done
        [ "$alive" = 0 ] && break
        sleep 1
    done
    for pid in "${pids[@]}"; do
        kill -0 "$pid" 2>/dev/null && { kill -9 "$pid" 2>/dev/null; say "   SIGKILL → $pid"; }
    done
fi
rm -f "$RUN_DIR"/*.pid

# --- 2. страховка: процессы, запущенные старыми скриптами вне PID-файлов ---
pkill -9 -f 'llama-server' 2>/dev/null && say "   добиты llama-server вне PID-файлов"
pkill -9 -f 'open-webui serve' 2>/dev/null
pkill -9 -f 'python[0-9.]* -m open_webui' 2>/dev/null
pkill -9 -f 'mcp_server.py' 2>/dev/null && say "   добит mcp_server вне PID-файлов"

# --- 3. проверка портов ---
sleep 1
for port in 8080 8081 8082 8089; do
    if ss -tln 2>/dev/null | grep -q ":$port "; then
        say "   ⚠️ порт $port всё ещё занят: $(ss -tlnp 2>/dev/null | grep ":$port " | head -1)"
    fi
done

if command -v nvidia-smi >/dev/null 2>&1; then
    say "🧹 VRAM после остановки: $(nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader)"
fi
exit 0
