#!/bin/bash
# ============================================================
#  start_ai.sh — запуск ИИ-комбайна bare-metal (RTX 3060 12 ГБ)
#
#  Стек:
#    1) llama-server РОУТЕР (models.ini, 6 моделей)  → 127.0.0.1:8081
#    2) llama-server ЭМБЕДДИНГИ (gte-modernbert)     → 127.0.0.1:8082
#    3) Open WebUI 0.12 (чат, OpenAI-протокол)        → 127.0.0.1:8080
#
#  Особенности:
#    - без sudo: все процессы пользовательские
#    - PID-файлы в AiStart/run/, логи в AiStart/logs/
#    - ожидание готовности каждого сервиса (health-check, не «sleep 5»)
#    - CUDA: сборка ~/llama требует CUDA 12 (libcudart.so.12/libcublas.so.12),
#      совместимые библиотеки ставятся pip-колёсами в AiStart/.venv-cuda12
#
#  Остановка: ./stop_ai.sh    Статус: ./status_ai.sh
# ============================================================

# ------------------------- настройки -------------------------
ROUTER_BIN="/home/rover/llama/llama-server"
MODELS_INI="/home/rover/AiStart/models.ini"
EMBED_MODEL="/home/rover/AI-Models/gte-modernbert-base-q8_0.gguf"
WEBUI_DIR="/home/rover/OpenWebUI-Local"
CUDA12_VENV="/home/rover/AiStart/.venv-cuda12"

ROUTER_BIND="127.0.0.1"; ROUTER_PORT=8081
EMBED_BIND="127.0.0.1";  EMBED_PORT=8082
WEBUI_BIND="127.0.0.1";  WEBUI_PORT=8080

THREADS=6
MODELS_MAX=1                        # моделей одновременно в VRAM (12 ГБ → 1)
WARMUP_MODEL="qwen2.5-coder-0.5b"   # прогрев конвейера ("" = выключить)
WARMUP_TIMEOUT=120
CORS_ORIGINS="http://localhost:8080,http://127.0.0.1:8080,http://localhost:8081,http://127.0.0.1:8081"

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$BASE_DIR/run"; LOG_DIR="$BASE_DIR/logs"
mkdir -p "$RUN_DIR" "$LOG_DIR"

log() { echo -e "$1"; }
die() { echo "❌ ОШИБКА: $1" >&2; exit 1; }

# ожидание HTTP-URL: 0=готово, 2=процесс умер, 1=таймаут
wait_http() {
    local url="$1" timeout="$2" pid="$3"
    for _ in $(seq 1 "$timeout"); do
        curl -sf "$url" >/dev/null 2>&1 && return 0
        kill -0 "$pid" 2>/dev/null || return 2
        sleep 1
    done
    return 1
}

report_fail() { echo "   последние строки $1:"; tail -n 25 "$1" 2>/dev/null | sed 's/^/      /'; }

# проверка MCP (Streamable HTTP): голый GET даёт 406 — нужен POST initialize
# с Accept: application/json И text/event-stream; 200/400-совместимый ответ = сервер жив
wait_mcp() {
    local url="$1" timeout="$2" pid="$3"
    for _ in $(seq 1 "$timeout"); do
        code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$url" \
            -H 'Accept: application/json, text/event-stream' \
            -H 'Content-Type: application/json' \
            -H 'MCP-Protocol-Version: 2025-06-18' \
            -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"start_ai-health","version":"1.0"}}}' 2>/dev/null)
        case "$code" in
            200) return 0 ;;
        esac
        kill -0 "$pid" 2>/dev/null || return 2
        sleep 1
    done
    return 1
}

# ---------------- 0. Выбор сборки llama-server (CUDA 13 или CUDA 12) ----------------
# Приоритет: ~/llama-cuda13 (собрана под текущую системную CUDA, не ломается
# от обновлений cuda). Фолбэк: ~/llama (старая, собрана под CUDA 12 — ей нужны
# libcudart.so.12/libcublas.so.12 из .venv-cuda12, иначе молча работает на CPU).
LLAMA13_DIR="/home/rover/llama-cuda13"
if [ -x "$LLAMA13_DIR/llama-server" ]; then
    ROUTER_BIN="$LLAMA13_DIR/llama-server"
    LLAMA_LIB_DIR="$LLAMA13_DIR"
    # Убираем пути CUDA12 из окружения — они ломают загрузку libggml-cuda.so (v13)
    unset LD_LIBRARY_PATH
    log "🎮 Сборка llama-server: CUDA 13 ($LLAMA13_DIR)"
else
    CUDART_DIR=$(find "$CUDA12_VENV" -name 'libcudart.so.12' -printf '%h' -quit 2>/dev/null)
    CUBLAS_DIR=$(find "$CUDA12_VENV" -name 'libcublas.so.12' -printf '%h' -quit 2>/dev/null)
    if [ -n "$CUDART_DIR" ] && [ -n "$CUBLAS_DIR" ]; then
        export LD_LIBRARY_PATH="$CUDART_DIR:$CUBLAS_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
        log "🎮 Сборка llama-server: CUDA 12 (~/llama) + совместимые библиотеки из venv"
    else
        log "⚠️  Ни CUDA 13-сборки, ни CUDA 12-библиотек не найдено — llama-server будет на CPU!"
    fi
fi

# ---------------- 1. останов старых копий ----------------
log "🛑 Остановка старых копий (если были)..."
"$BASE_DIR/stop_ai.sh" --quiet

# ---------------- 2. проверки окружения ----------------
[ -x "$ROUTER_BIN" ] || die "не найден $ROUTER_BIN"
[ -f "$MODELS_INI" ] || die "не найден $MODELS_INI"
[ -d "$WEBUI_DIR" ]  || die "не найден каталог $WEBUI_DIR"
[ -f "$WEBUI_DIR/.venv/bin/activate" ] || die "нет .venv в $WEBUI_DIR"

# все файлы моделей из models.ini должны существовать
while IFS= read -r f; do
    [ -f "$f" ] || die "файл из models.ini не найден: $f"
done < <(grep -E '^[[:space:]]*(model|mmproj)[[:space:]]*=' "$MODELS_INI" | sed -E 's/^[^=]*=[[:space:]]*//')

for port in "$ROUTER_PORT" "$EMBED_PORT" "$WEBUI_PORT"; do
    ss -tln 2>/dev/null | grep -q ":$port " && die "порт $port занят чужим процессом (см.: ss -tlnp | grep :$port)"
done

# ---------------- 3. роутер моделей ----------------
log "🧠 Запуск роутера llama-server (шаблон чата берётся из каждой модели)..."
: > "$LOG_DIR/llama_router.log"
nohup "$ROUTER_BIN" \
    --models-preset "$MODELS_INI" \
    --models-max "$MODELS_MAX" \
    --host "$ROUTER_BIND" --port "$ROUTER_PORT" \
    -t "$THREADS" --jinja \
    --cors-origins "$CORS_ORIGINS" \
    > "$LOG_DIR/llama_router.log" 2>&1 < /dev/null &
ROUTER_PID=$!
echo "$ROUTER_PID" > "$RUN_DIR/llama_router.pid"

log "⏳ Ожидание API роутера (до 60 с)..."
wait_http "http://$ROUTER_BIND:$ROUTER_PORT/v1/models" 60 "$ROUTER_PID"
case $? in
    0) log "✅ Роутер готов (PID $ROUTER_PID)" ;;
    2) report_fail "$LOG_DIR/llama_router.log"; die "роутер упал при старте" ;;
    *) report_fail "$LOG_DIR/llama_router.log"; die "роутер не поднялся за 60 с" ;;
esac

MODELS_LIST=$(curl -s "http://$ROUTER_BIND:$ROUTER_PORT/v1/models" \
    | python3 -c 'import json,sys; print(", ".join(m["id"] for m in json.load(sys.stdin)["data"]))' 2>/dev/null)
log "📦 Модели: ${MODELS_LIST:-<пусто!>}"
[ -n "$MODELS_LIST" ] || die "роутер не отдал список моделей"

# ---------------- 4. эмбеддинг-сервер (база знаний) ----------------
if [ -f "$EMBED_MODEL" ]; then
    log "🔢 Запуск эмбеддинг-серверa gte-modernbert (для базы знаний Habr)..."
    : > "$LOG_DIR/llama_embed.log"
    nohup "$ROUTER_BIN" \
        --model "$EMBED_MODEL" --embeddings --alias gte-modernbert \
        -ngl 99 -c 4096 -ub 2048 --host "$EMBED_BIND" --port "$EMBED_PORT" \
        > "$LOG_DIR/llama_embed.log" 2>&1 < /dev/null &
    EMBED_PID=$!
    echo "$EMBED_PID" > "$RUN_DIR/llama_embed.pid"
    wait_http "http://$EMBED_BIND:$EMBED_PORT/v1/models" 60 "$EMBED_PID"
    case $? in
        0) log "✅ Эмбеддер готов (PID $EMBED_PID)" ;;
        *) report_fail "$LOG_DIR/llama_embed.log"; log "⚠️ продолжаю без семантического поиска" ;;
    esac
else
    log "⚠️  Нет $EMBED_MODEL — семантический поиск базы знаний недоступен"
fi

# ---------------- 5. MCP-сервер local-tools (фото/Habr/Obsidian/терминал/агент) ----------------
log "🛠  Запуск MCP-сервера local-tools (:8089/mcp)..."
: > "$LOG_DIR/mcp_server.log"
nohup "$WEBUI_DIR/.venv/bin/python" "$BASE_DIR/mcp_server.py" \
    > "$LOG_DIR/mcp_server.log" 2>&1 < /dev/null &
MCP_PID=$!
echo "$MCP_PID" > "$RUN_DIR/mcp_server.pid"
wait_mcp "http://127.0.0.1:8089/mcp" 30 "$MCP_PID"
case $? in
    0) log "✅ MCP-сервер готов (PID $MCP_PID)" ;;
    *) report_fail "$LOG_DIR/mcp_server.log"; log "⚠️ продолжаю без MCP-инструментов" ;;
esac

# ---------------- 6. прогрев конвейера ----------------
if [ -n "$WARMUP_MODEL" ]; then
    log "🔥 Прогрев: $WARMUP_MODEL (первая загрузка модели в VRAM)..."
    if curl -sf --max-time "$WARMUP_TIMEOUT" \
         "http://$ROUTER_BIND:$ROUTER_PORT/v1/chat/completions" \
         -H 'Content-Type: application/json' \
         -d "{\"model\":\"$WARMUP_MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"привет\"}],\"max_tokens\":4}" \
         > /dev/null; then
        log "✅ Прогрев успешен: конвейер OpenAI-API работает"
    else
        log "⚠️ Прогрев не удался — детали в $LOG_DIR/llama_router.log"
        tail -n 10 "$LOG_DIR/llama_router.log" | sed 's/^/      /'
    fi
fi

# ---------------- 7. Open WebUI ----------------
log "🌐 Запуск Open WebUI..."
cd "$WEBUI_DIR" || die "не могу войти в $WEBUI_DIR"
# shellcheck disable=SC1091
source .venv/bin/activate

export WEBUI_AUTH=False
export ENABLE_OLLAMA_API=False                    # только OpenAI-протокол роутера
export OPENAI_API_BASE_URL="http://$ROUTER_BIND:$ROUTER_PORT/v1"
export OPENAI_API_KEY="sk-local-dummy"            # роутер без --api-key ключ игнорирует, но WebUI без него может считать соединение офлайн

# фоновые генерации WebUI выключены — не дёргают роутер лишними запросами
export ENABLE_TITLE_GENERATION=False
export ENABLE_TAGS_GENERATION=False
export ENABLE_AUTOCOMPLETE_GENERATION=False
export ENABLE_FOLLOW_UP_GENERATION=False

export HOST="$WEBUI_BIND" PORT="$WEBUI_PORT"

: > "$LOG_DIR/open_webui.log"
nohup open-webui serve --host "$WEBUI_BIND" --port "$WEBUI_PORT" \
    > "$LOG_DIR/open_webui.log" 2>&1 < /dev/null &
WEBUI_PID=$!
echo "$WEBUI_PID" > "$RUN_DIR/open_webui.pid"

log "⏳ Ожидание Open WebUI (до 120 с, первый старт дольше)..."
wait_http "http://$WEBUI_BIND:$WEBUI_PORT/health" 120 "$WEBUI_PID"
case $? in
    0) log "✅ Open WebUI готов (PID $WEBUI_PID)" ;;
    2) report_fail "$LOG_DIR/open_webui.log"; die "Open WebUI упал при старте" ;;
    *) report_fail "$LOG_DIR/open_webui.log"; die "Open WebUI не поднялся за 120 с" ;;
esac

# ---------------- 7. сводка ----------------
VRAM=""
command -v nvidia-smi >/dev/null 2>&1 && VRAM="$(nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader)"
echo ""
log "🎉 ИИ-комбайн запущен и проверен:"
log "   💬 Чат:         http://localhost:$WEBUI_PORT"
log "   🔌 Роутер API:  http://$ROUTER_BIND:$ROUTER_PORT/v1"
[ -n "$MODELS_LIST" ] && log "   📦 Модели:      $MODELS_LIST"
[ -f "$RUN_DIR/llama_embed.pid" ] && log "   🔢 Эмбеддинги:  http://$EMBED_BIND:$EMBED_PORT/v1 (gte-modernbert)"
[ -n "$VRAM" ] && log "   🎮 VRAM:        $VRAM"
log "   📋 Логи:        $LOG_DIR/"
log "   ⏹  Остановка:  $BASE_DIR/stop_ai.sh    📊 Статус: $BASE_DIR/status_ai.sh"

# ---------------- 8. авто-resume фонового скачивания Habr ----------------
# После перезагрузки воркер habr_fetch не жив: если есть незавершённые задания —
# продолжаем автоматически (готовые статьи пропускаются).
FETCH_JOBS="$BASE_DIR/habr_fetch_jobs.json"
if [ -f "$FETCH_JOBS" ] && grep -qE '"status": "(pending|paused|running)"' "$FETCH_JOBS"; then
    log "📥 Найдено незавершённое скачивание Habr — возобновляю в фоне..."
    "$BASE_DIR/.venv-tools/bin/python" "$BASE_DIR/habr_fetch.py" resume 2>&1 | while IFS= read -r line; do log "   $line"; done
else
    log "📥 Фоновое скачивание Habr: незавершённых заданий нет"
fi
exit 0
