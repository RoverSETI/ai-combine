#!/bin/bash
# ============================================================
#  comfy_start.sh / comfy_stop.sh — ComfyUI по требованию
#
#  ComfyUI по умолчанию НЕ автозапускается (START_COMFY=0 в
#  start_ai.sh): VRAM 12 ГБ свободна для LLM. Когда нужно
#  рисование — стартует этим скриптом, после работы
#  останавливается и VRAM освобождается полностью.
#
#  Использование:
#    ./comfy_start.sh   # старт (если уже работает — просто скажет)
#    ./comfy_stop.sh    # стоп + освобождение VRAM
# ============================================================
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$BASE_DIR/run"; LOG_DIR="$BASE_DIR/logs"
COMFY_PID_FILE="$RUN_DIR/comfyui.pid"

COMFY_PY="/home/rover/AI-Combine/ComfyUI/.venv/bin/python"
COMFY_DIR="/home/rover/AI-Combine/ComfyUI"

log() { echo -e "$1"; }
vr()  { nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader 2>/dev/null; }

alive() { curl -s --max-time 3 http://127.0.0.1:8188/system_stats >/dev/null 2>&1; }

case "$(basename "$0")" in
    comfy_stop.sh)
        PID=$(cat "$COMFY_PID_FILE" 2>/dev/null || true)
        if alive; then
            log "🛑 Останавливаю ComfyUI..."
            # ТЯЖЁЛЫЙ процесс: SIGTERM ComfyUI переживает и не освобождает VRAM,
            # kill -9 гарантирует выгрузку (проверено 14.11)
            [ -n "$PID" ] && kill -9 "$PID" 2>/dev/null
            sleep 3
            alive && log "⚠️  ComfyUI ещё жив — процесс чужой, смотри: ps aux | grep main.py" \
                    || log "✅ VRAM освобождена: $(vr)"
        else
            log "⚪ ComfyUI не запущен (VRAM уже свободна: $(vr))"
        fi
        rm -f "$COMFY_PID_FILE"
        ;;
    comfy_start.sh)
        if alive; then
            log "✅ ComfyUI уже работает (:8188)"
        elif [ ! -x "$COMFY_PY" ]; then
            log "❌ Нет $COMFY_PY"
            exit 1
        else
            log "🎨 Старт ComfyUI (:8188)..."
            mkdir -p "$RUN_DIR" "$LOG_DIR"
            : > "$LOG_DIR/comfyui.log"
            nohup "$COMFY_PY" "$COMFY_DIR/main.py" --listen 127.0.0.1 --port 8188 --reserve-vram 1.5 \
                > "$LOG_DIR/comfyui.log" 2>&1 < /dev/null &
            echo $! > "$COMFY_PID_FILE"
            for _ in $(seq 1 30); do
                curl -s --max-time 3 http://127.0.0.1:8188/system_stats >/dev/null 2>&1 && break
                sleep 2
            done
            alive && log "✅ ComfyUI готов (:8188). После работы: ./comfy_stop.sh" \
                    || { log "❌ Не поднялся — лог:"; tail -n 20 "$LOG_DIR/comfyui.log"; }
        fi
        log "🎮 VRAM: $(vr)"
        ;;
esac
