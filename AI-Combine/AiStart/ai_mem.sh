#!/bin/bash
# ============================================================
#  ai_mem.sh — диспетчер VRAM/RAM комбайна (12 ГБ VRAM / 32 ГБ RAM)
#
#  «Последний запущенный получает память»: тяжёлые слои
#  выгружаются перед запуском следующего тяжёлого.
#
#  Команды:
#    ./ai_mem.sh status   — что сейчас в VRAM/RAM и кто съел память
#    ./ai_mem.sh chat     — освободить VRAM для чата/LLM (стоп ComfyUI)
#    ./ai_mem.sh art      — освободить VRAM для ComfyUI (выгрузить LLM)
#    ./ai_mem.sh heavy    — ТЯЖЁЛЫЙ 35B (MoE на CPU): LLM в RAM+VRAM
#    ./ai_mem.sh free     — минимальный режим: только роутер+эмбеддер
#    ./ai_mem.sh offload  — выгрузить ВСЕ LLM из VRAM (максимальная свобода)
#
#  Обратно модели не грузим руками — роутер сам поднимет по запросу
#  (модель = «зелёная точка», WebUI свопает при отправке сообщения).
# ============================================================
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROUTER="http://127.0.0.1:8081"

log() { echo -e "$1"; }
vr()  { nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader 2>/dev/null; }
ram() { free -h | awk '/Mem:/{print $3" / "$2}' ; }

# выгрузить модель через API роутера (мягко), если не отвечает — kill
unload() {
    local mid="$1"
    curl -sf --max-time 60 -X POST "$ROUTER/models/unload" \
        -H 'Content-Type: application/json' -d "{\"model\":\"$mid\"}" >/dev/null 2>&1 \
        && return 0
    return 1
}

stop_comfy() {
    local pid
    pid=$(cat "$BASE_DIR/run/comfyui.pid" 2>/dev/null || true)
    if curl -s --max-time 3 http://127.0.0.1:8188/system_stats >/dev/null 2>&1; then
        [ -n "$pid" ] && kill -9 "$pid" 2>/dev/null
        sleep 2
    fi
}

case "$1" in
    status)
        log "🎮 VRAM: $(vr)   |   🐏 RAM: $(ram)"
        log ""
        log "Модели роутера (:8081):"
        curl -sf --max-time 5 "$ROUTER/models" | python3 -c "
import json,sys
d=json.load(sys.stdin)
for m in d['data']:
    s=m.get('status',{}).get('value','?')
    mark='🟢 в VRAM' if s=='loaded' else '⚪ выгружена'
    print(f'  {mark:10s} {m[\"id\"]}')"
        curl -s --max-time 3 http://127.0.0.1:8188/system_stats >/dev/null 2>&1 \
            && log "  🟢 ComfyUI     (рисование, :8188)" \
            || log "  ⚪ ComfyUI     не запущен"
        log ""
        log "Тяжёлые GPU-процессы:"
        nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader 2>/dev/null \
            | sed 's|/home/rover/||g' | sed 's/^/  /'
        ;;
    chat)
        log "💬 Режим ЧАТ: ComfyUI стоп, все LLM выгружены → VRAM свободна"
        stop_comfy
        for m in nvidia-nemotron-12b-vision qwen3.5-9b-instruct qwen3.6-35b-a3b; do unload "$m"; done
        sleep 2
        log "✅ VRAM: $(vr) — чат посылкой активирует нужную модель (роутер сам поднимет)"
        ;;
    art)
        log "🎨 Режим РИСОВАНИЕ: LLM выгружаются, VRAM отдана ComfyUI"
        for m in nvidia-nemotron-12b-vision qwen3.5-9b-instruct qwen3.6-35b-a3b; do unload "$m"; done
        if ! curl -s --max-time 3 http://127.0.0.1:8188/system_stats >/dev/null 2>&1; then
            "$BASE_DIR/comfy_start.sh"
        fi
        sleep 2
        log "✅ VRAM: $(vr)"
        ;;
    heavy)
        log "⚠️  ТЯЖЁЛЫЙ 35B: ComfyUI СТОП (правило: графика не работает вместе с 35B)"
        stop_comfy
        curl -sf --max-time 60 -X POST "$ROUTER/models/unload" \
            -H 'Content-Type: application/json' -d '{"model":"nvidia-nemotron-12b-vision"}' >/dev/null 2>&1
        log "🔥 Поднимаю qwen3.6-35b (прогрев 20 ГБ mmap с NVMe — первая загрузка медленная, НОРМА)..."
        curl -sf --max-time 300 "$ROUTER/v1/chat/completions" -H 'Content-Type: application/json' \
            -d '{"model":"qwen3.6-35b-a3b","messages":[{"role":"user","content":"привет"}],"max_tokens":4}' >/dev/null \
            && log "✅ 35B готов" || log "⚠️  35B не поднялся — смотри logs/llama_router.log"
        log "🎮 VRAM: $(vr)  RAM: $(ram)"
        ;;
    free)
        log "🧹 Минимальный режим: LLM + ComfyUI выгружены, роутер и эмбеддер остаются"
        stop_comfy
        for m in nvidia-nemotron-12b-vision qwen3.5-9b-instruct qwen3-4b-instruct-2507 qwen3.6-35b-a3b; do unload "$m"; done
        sleep 2
        log "✅ VRAM: $(vr)  RAM: $(ram)"
        ;;
    offload)
        log "🚀 Полная выгрузка LLM (VRAM отдана системе)"
        for m in nvidia-nemotron-12b-vision qwen3.5-9b-instruct qwen3-4b-instruct-2507 qwen3.6-35b-a3b; do unload "$m"; done
        sleep 2
        log "✅ VRAM: $(vr)  RAM: $(ram)"
        ;;
    *)
        log "Использование: $0 {status|chat|art|heavy|free|offload}"
        log "  status  — карта памяти (кто что съел)"
        log "  chat    — VRAM для чата (ComfyUI стоп)"
        log "  art     — VRAM для ComfyUI (LLM стоп)"
        log "  heavy   — ТЯЖЁЛЫЙ 35B (ComfyUI стоп, MoE на CPU)"
        log "  free    — минимум памяти (только роутер+эмбеддер)"
        log "  offload — все LLM из VRAM"
        ;;
esac
