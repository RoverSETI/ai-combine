#!/bin/bash
# ============================================================
#  apply_swarm.sh — применение конфигов роя агентов.
#
#  НИЧЕГО НЕ МЕНЯЕТ БЕЗ ЯВНОЙ КОМАНДЫ ПОЛЬЗОВАТЕЛЯ.
#
#  Команды:
#    apply_swarm.sh diff          — показать, что изменится (ничего не трогает)
#    apply_swarm.sh apply [блок]  — применить (all | tools | glm | stt); спросит "да"
#    apply_swarm.sh rollback      — откат из backup/
#    apply_swarm.sh status        — что применено, что нет
#
#  Бэкапы: ~/SwarmConfig/backup/<дата-время>/
# ============================================================
set -euo pipefail

CFG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AISTART="/home/rover/AiStart"
BACKUP="$CFG_DIR/backup"
STAMP=$(date +%Y%m%d-%H%M%S)
MCP_PY="$AISTART/mcp_server.py"
MCP_PIDFILE="$AISTART/run/mcp_server.pid"

say()  { echo -e "$1"; }
die()  { echo -e "❌ $1" >&2; exit 1; }
ask_yes() {
    read -r -p "$1 [да/нет]: " ans
    [[ "$ans" == "да" || "$ans" == "y" || "$ans" == "yes" ]] || die "отменено пользователем"
}

backup_file() { # $1 путь
    [ -f "$1" ] || die "нет файла: $1"
    mkdir -p "$BACKUP/$STAMP"
    cp "$1" "$BACKUP/$STAMP/$(basename "$1")"
    say "   💾 бэкап: $BACKUP/$STAMP/$(basename "$1")"
}

restart_mcp() {
    say "   ↻ перезапуск MCP-сервера..."
    local pid
    pid=$(cat "$MCP_PIDFILE" 2>/dev/null || true)
    [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
    sleep 2
    (cd "$AISTART" && nohup /home/rover/OpenWebUI-Local/.venv/bin/python3 mcp_server.py >> logs/mcp_server.log 2>&1 &
     echo $! > "$MCP_PIDFILE")
    sleep 6
    local code
    code=$(curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:8089/mcp \
        -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
        -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"apply","version":"1.0"}}}')
    [[ "$code" == "200" ]] && say "   ✅ MCP поднят (:8089)" || die "MCP не поднялся (HTTP $code) — смотрите logs/mcp_server.log"
}

# ------------------------------------------------ diff
cmd_diff() {
    say "════════ ЧТО БУДЕТ ИЗМЕНЕНО (пока ничего не тронуто) ════════"
    say ""
    say "▸ Блок TOOLS (mcp_local.json → swarm_tools.py):"
    grep -oE '"(web_search|web_fetch|glm_think|glm_review|stt_transcribe|stt_listen)"' "$CFG_DIR/mcp_local.json" 2>/dev/null | tr -d '"' | sed 's/^/    + инструмент: /'
    if [ -f "$AISTART/swarm_tools.py" ]; then
        say "    (файл уже существует — будет ЗАМЕНЁН, бэкап сделается)"
    fi
    say "    → интеграция в mcp_server.py: одна строка import + регистрация"
    say ""
    say "▸ Блок GLM:"
    say "    + endpoint https://api.z.ai/api/paas/v4/chat/completions (ключ из PolzaKey>Z.ai)"
    say "    + счётчик вызовов ~/SwarmConfig/state/glm_usage.json (бюджет)"
    say "    ⚠ требуется доступ в интернет (есть)"
    say ""
    say "▸ Блок STT (stt.json):"
    say "    + проверка локальных весов ~/giga_speech/model (уже скачаны, ~450 МБ)"
    say "    + тест загрузки и транскрибации через stt_model.py (обход meta-device)"
    if [ -f /home/rover/giga_speech/file_transcribe.py ]; then
        say "    (скрипты ~/giga_speech уже есть — перезаписываться НЕ будут)"
    fi
    say ""
    say "Подробное описание: $(dirname "$0")/ИНСТРУКЦИЯ-РОЙ-АГЕНТЫ.md"
}

# ------------------------------------------------ apply tools (+glm)
apply_tools() {
    say "════════ ПРИМЕНЕНИЕ: TOOLS+GLM ════════"
    say "Будет:"
    say "  1. Создан $AISTART/swarm_tools.py (web_search, web_fetch, glm_think, glm_review)"
    say "  2. В mcp_server.py добавлена одна строка подключения"
    say "  3. MCP перезапущен"
    ask_yes "Применяем?"
    backup_file "$MCP_PY"

    # 1. генерируем swarm_tools.py из JSON-спецификации
    "$CFG_DIR/gen_swarm_tools.py" || die "генерация swarm_tools.py не удалась"
    say "   ✅ создан $AISTART/swarm_tools.py"

    # 2. подключение в mcp_server.py (если ещё не подключён)
    if ! grep -q "swarm_tools" "$MCP_PY"; then
        python3 - "$MCP_PY" <<'EOF'
import sys, re
p = sys.argv[1]
src = open(p).read()
anchor = '# ============================================================ запуск'
block = ('# ---- swarm_tools: инструменты роя (web/glm) из SwarmConfig ----\n'
         'try:\n'
         '    import swarm_tools  # noqa: F401  регистрирует свои @mcp.tool() на этот же сервер\n'
         'except Exception as _e:\n'
         '    import sys as _sys; print(f"swarm_tools не загрузился: {_e}", file=_sys.stderr)\n\n\n')
assert anchor in src, "якорь не найден"
src = src.replace(anchor, block + anchor, 1)
open(p, 'w').write(src)
print("   ✅ mcp_server.py дополнен (import swarm_tools)")
EOF
    else
        say "   ℹ️ mcp_server.py уже подключает swarm_tools"
    fi

    # 3. mkdir state + счётчик
    mkdir -p "$CFG_DIR/state"
    [ -f "$CFG_DIR/state/glm_usage.json" ] || echo '{"calls_today":0,"date":"","total":0}' > "$CFG_DIR/state/glm_usage.json"

    restart_mcp
    say "   ✅ Проверьте в WebUI: меню «+» → инструменты local-tools → появятся web_search и др."
}

# ------------------------------------------------ apply stt
STT_DIR="/home/rover/giga_speech"
STT_MODEL_DIR="$STT_DIR/model"

apply_stt() {
    say "════════ ПРИМЕНЕНИЕ: STT (GigaAM-v3) ════════"
    say "Весов в кэше HF НЕ нужно: полные веса уже лежат локально"
    say "  ($STT_MODEL_DIR — pytorch_model.bin, загружаются в обход meta-device бага)"
    ask_yes "Применяем?"

    # 1. проверка локального комплекта весов (скачивание не требуется)
    for f in config.json pytorch_model.bin modeling_gigaam.py tokenizer.model; do
        [ -f "$STT_MODEL_DIR/$f" ] || die "нет $STT_MODEL_DIR/$f — комплект неполон"
    done
    say "   ✅ локальные веса на месте: $(du -sh "$STT_MODEL_DIR" | cut -f1)"

    # 2. тест: генерируем 2 сек. шума и транскрибируем через stt_model.py
    #    (прямая сборка GigaAMASR из model/ — БЕЗ transformers.from_pretrained,
    #     который падает с 'Tensor on device cpu is not on the expected device meta!')
    "$STT_DIR/.venv/bin/python" - <<'EOF' || die "загрузка/тест GigaAM не удалась"
import os, sys, tempfile, wave
import numpy as np

sys.path.insert(0, "/home/rover/giga_speech")
import stt_model

# 2 секунды тихого шума, 16 кГц моно
fd, tmp = tempfile.mkstemp(suffix=".wav")
os.close(fd)
with wave.open(tmp, "w") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(16000)
    w.writeframes((np.random.randn(16000 * 2) * 30).astype(np.int16).tobytes())
try:
    text = stt_model.transcribe(tmp)
    print(f"✅ GigaAM-v3 загружена и работает, тест: {text!r}")
finally:
    os.unlink(tmp)
EOF
    say "   ✅ STT готов. Проверка в WebUI: «транскрибируй <файл>» (MCP stt_transcribe)"
}

cmd_rollback() {
    local latest
    latest=$(ls -1t "$BACKUP" 2>/dev/null | head -1) || die "бэкапов нет"
    say "Последний бэкап: $BACKUP/$latest"
    ls "$BACKUP/$latest" | sed 's/^/   - /'
    ask_yes "Восстановить эти файлы?"
    for f in "$BACKUP/$latest"/*; do
        case "$(basename "$f")" in
            mcp_server.py) cp "$f" "$MCP_PY"; say "   ↩ восстановлен mcp_server.py";;
            *) cp "$f" "$AISTART/$(basename "$f")"; say "   ↩ восстановлен $(basename "$f") в $AISTART/";;
        esac
    done
    restart_mcp
}

cmd_status() {
    say "════════ СТАТУС РАЗВЁРТЫВАНИЯ ════════"
    grep -q "swarm_tools" "$MCP_PY" && say "✅ tools+glm: применены" || say "⚪ tools+glm: НЕ применены"
    if [ -f "$STT_MODEL_DIR/pytorch_model.bin" ]; then
        say "✅ stt: локальные веса есть ($STT_MODEL_DIR)"
    else
        say "⚪ stt: весов нет в $STT_MODEL_DIR"
    fi
    say ""
    say "Конфиги (не применяются автоматически):"
    ls -1 "$CFG_DIR"/*.json "$CFG_DIR"/*.md 2>/dev/null | sed 's/^/   /'
}

case "${1:-help}" in
    diff)     cmd_diff ;;
    apply)    shift
              case "${1:-all}" in
                  tools|glm) apply_tools ;;
                  stt)       apply_stt ;;
                  all)       apply_tools; apply_stt ;;
                  *) die "блок: all | tools | stt (glm входит в tools)" ;;
              esac ;;
    rollback) cmd_rollback ;;
    status)   cmd_status ;;
    *) say "Использование: apply_swarm.sh {diff|apply [all|tools|stt]|rollback|status}
diff     — показать изменения, ничего не трогая
apply    — применить (с подтверждением и бэкапом)
rollback — откат последнего apply
status   — что применено";;
esac
