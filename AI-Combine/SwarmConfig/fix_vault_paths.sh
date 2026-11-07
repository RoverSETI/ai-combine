#!/bin/bash
# ============================================================
#  fix_vault_paths.sh — перевод vault Obsidian на правильный путь.
#
#  ПРАВИЛЬНЫЙ vault:  /run/media/rover/BACK/GoogleDrive/Obsidian
#  НЕВЕРНЫЙ (бекап):  /home/rover/GoogleDrive/Obsidian  — НЕ ИСПОЛЬЗОВАТЬ!
#
#  Что правится (3 файла с зашитым неверным путём):
#    1. ~/AiStart/habr_bookmarks.py            — сохранение полных статей Хабра
#    2. ~/OpenWebUI-Local/scripts/sync_knowledge.py — синхр. Знаний WebUI
#    3. ~/OpenWebUI-Local/scripts/load_knowledge.py — первичная загрузка Знаний
#
#  Плюс: перенос полных статей Хабра от 06.11 из бекапа в правильный vault.
#
#  Команды:
#    fix_vault_paths.sh diff      — показать изменения (ничего не трогает)
#    fix_vault_paths.sh apply     — применить (бэкап + подтверждение)
#    fix_vault_paths.sh rollback  — вернуть 3 файла из бэкапа
#    fix_vault_paths.sh status    — текущее состояние путей
# ============================================================
set -euo pipefail

RIGHT="/run/media/rover/BACK/GoogleDrive/Obsidian"
WRONG="/home/rover/GoogleDrive/Obsidian"
AISTART="/home/rover/AiStart"
WEBUI_SCRIPTS="/home/rover/OpenWebUI-Local/scripts"
CFG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP="$CFG_DIR/backup"
STAMP=$(date +%Y%m%d-%H%M%S)-vault-path
MCP_PY="$AISTART/mcp_server.py"
MCP_PIDFILE="$AISTART/run/mcp_server.pid"

# файлы с неверным путём
TARGETS=(
    "$AISTART/habr_bookmarks.py"
    "$WEBUI_SCRIPTS/sync_knowledge.py"
    "$WEBUI_SCRIPTS/load_knowledge.py"
)

HABR_LOCAL="$WRONG/ИИ/Habr"
HABR_RIGHT="$RIGHT/ИИ/Habr"

say()  { echo -e "$1"; }
die()  { echo -e "❌ $1" >&2; exit 1; }
ask_yes() {
    read -r -p "$1 [да/нет]: " ans
    [[ "$ans" == "да" || "$ans" == "y" || "$ans" == "yes" ]] || die "отменено пользователем"
}

check_back_mounted() {
    [ -d "$RIGHT" ] || die "$RIGHT не существует — диск BACK смонтирован?"
    if ! mountpoint -q /run/media/rover/BACK && ! mount | grep -q "on /run/media/rover/BACK "; then
        die "/run/media/rover/BACK не смонтирован!"
    fi
}

backup_file() {
    [ -f "$1" ] || die "нет файла: $1"
    mkdir -p "$BACKUP/$STAMP"
    cp "$1" "$BACKUP/$STAMP/$(basename "$1")"
    say "   💾 бэкап: $BACKUP/$STAMP/$(basename "$1")"
}

restart_mcp() {
    say "   ↻ перезапуск MCP-сервера (:8089)..."
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
        -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"fixpath","version":"1.0"}}}')
    [[ "$code" == "200" ]] && say "   ✅ MCP поднят (:8089)" || die "MCP не поднялся (HTTP $code) — смотрите ~/AiStart/logs/mcp_server.log"
}

# ------------------------------------------------ diff
cmd_diff() {
    say "════════ ЧТО БУДЕТ ИЗМЕНЕНО (пока ничего не тронуто) ════════"
    say ""
    say "▸ Правка 3 файлов: путь $WRONG → $RIGHT:"
    local f
    for f in "${TARGETS[@]}"; do
        if grep -q "$WRONG" "$f"; then
            say "   🔧 $f:"
            grep -n "$WRONG" "$f" | sed 's/^/        /'
        else
            say "   ⚪ $f — уже верно (или не найдено вхождение)"
        fi
    done
    say ""
    say "▸ Перенос полных статей Хабра из бекапа в vault BACK:"
    if [ -d "$HABR_LOCAL" ]; then
        local n=0
        local file base
        for file in "$HABR_LOCAL"/*.md; do
            [ -f "$file" ] || continue
            base=$(basename "$file")
            if [ ! -f "$HABR_RIGHT/$base" ]; then
                say "   📤 $(basename "$file") ($(du -h "$file" | cut -f1))"
                n=$((n+1))
            fi
        done
        [ "$n" -eq 0 ] && say "   ⚪ переносить нечего (всё уже в BACK)"
    else
        say "   ⚪ $HABR_LOCAL не существует"
    fi
    say ""
    say "▸ После правки: перезапуск MCP :8089 (модуль habr_bookmarks перечитает константу)"
    say "▸ Бэкапы: $BACKUP/$STAMP/"
}

# ------------------------------------------------ apply
cmd_apply() {
    check_back_mounted
    say "════════ ПРИМЕНЕНИЕ ИСПРАВЛЕНИЯ ПУТЕЙ ════════"
    say "  1) Бэкап 3 файлов в $BACKUP/$STAMP/"
    say "  2) Замена $WRONG → $RIGHT (3 файла)"
    say "  3) Перенос статей Хабра из бекапа в BACK (копия+сверка+удаление оригинала)"
    say "  4) Перезапуск MCP :8089"
    ask_yes "Применяем?"

    say ""
    say "▸ Шаг 1: бэкапы"
    local f
    for f in "${TARGETS[@]}"; do backup_file "$f"; done

    say ""
    say "▸ Шаг 2: замена путей"
    for f in "${TARGETS[@]}"; do
        if grep -q "$WRONG" "$f"; then
            sed -i "s|$WRONG|$RIGHT|g" "$f"
            grep -q "$WRONG" "$f" && die "в $f осталось неверное вхождение!" \
                || say "   ✅ $(basename "$f"): путь заменён"
        else
            say "   ⚪ $(basename "$f"): нечего менять"
        fi
    done

    say ""
    say "▸ Шаг 3: перенос статей Хабра из бекапа"
    if [ -d "$HABR_LOCAL" ]; then
        mkdir -p "$HABR_RIGHT"
        local n=0 file base
        for file in "$HABR_LOCAL"/*.md; do
            [ -f "$file" ] || continue
            base=$(basename "$file")
            if [ -f "$HABR_RIGHT/$base" ]; then
                say "   ⚪ уже в BACK: $base"
                continue
            fi
            cp -p "$file" "$HABR_RIGHT/$base"
            if cmp -s "$file" "$HABR_RIGHT/$base"; then
                rm "$file"
                say "   📤 перенесено: $base"
                n=$((n+1))
            else
                rm "$HABR_RIGHT/$base"
                say "   ⚠️ сверка не прошла, файл НЕ тронут: $base"
            fi
        done
        say "   Итого перенесено: $n"
    else
        say "   ⚪ $HABR_LOCAL не существует — пропускаю"
    fi

    say ""
    say "▸ Шаг 4: перезапуск MCP"
    restart_mcp

    say ""
    say "▸ Контрольная проверка констант:"
    (cd "$AISTART" && python3 -c "import habr_bookmarks as hb; print('   habr_bookmarks.VAULT_DIR =', hb.VAULT_DIR)")
    grep -n '^VAULT' "$WEBUI_SCRIPTS/sync_knowledge.py" | sed 's/^/   sync_knowledge.py /'
    grep -n '^VAULT' "$WEBUI_SCRIPTS/load_knowledge.py" | sed 's/^/   load_knowledge.py /'

    say ""
    say "════════ ГОТОВО ════════"
    say "Дальше (вручную, см. ИНСТРУКЦИЯ-ШАГИ-2026-11-06.md):"
    say "  • пересинхронизация Знаний WebUI: $WEBUI_SCRIPTS/sync_knowledge.py"
    say "  • тест сохранения: в WebUI «Хабр: сохрани статью …» → файл должен лечь в BACK"
}

# ------------------------------------------------ rollback
cmd_rollback() {
    local latest
    latest=$(ls -1dt "$BACKUP"/*vault-path 2>/dev/null | head -1) || die "бэкапов vault-path нет"
    say "Последний бэкап: $BACKUP/$latest"
    ls "$BACKUP/$latest" | sed 's/^/   - /'
    ask_yes "Восстановить эти файлы (пути станут СТАРЫМИ)?"
    for f in "$BACKUP/$latest"/*; do
        case "$(basename "$f")" in
            habr_bookmarks.py)  cp "$f" "$AISTART/";;
            sync_knowledge.py)  cp "$f" "$WEBUI_SCRIPTS/";;
            load_knowledge.py)  cp "$f" "$WEBUI_SCRIPTS/";;
        esac
        say "   ↩ восстановлен $(basename "$f")"
    done
    restart_mcp
}

# ------------------------------------------------ status
cmd_status() {
    say "════════ СТАТУС ПУТЕЙ VAULT ════════"
    local f
    for f in "${TARGETS[@]}"; do
        if [ ! -f "$f" ]; then say "❓ нет файла: $f"; continue; fi
        if grep -q "$WRONG" "$f"; then
            say "❌ $(basename "$f") — НЕВЕРНЫЙ путь:"
            grep -n "$WRONG" "$f" | sed 's/^/      /'
        elif grep -q "$RIGHT" "$f"; then
            say "✅ $(basename "$f") — BACK"
        else
            say "❓ $(basename "$f") — путь не найден (смотрите файл)"
        fi
    done
    echo ""
    if [ -d "$HABR_LOCAL" ]; then
        local n=0 file base
        for file in "$HABR_LOCAL"/*.md; do
            [ -f "$file" ] || continue
            base=$(basename "$file")
            [ -f "$HABR_RIGHT/$base" ] || { say "📤 к переносу: $base"; n=$((n+1)); }
        done
        [ "$n" -eq 0 ] && say "✅ в бекапе нет статей, отсутствующих в BACK"
    fi
    echo ""
    say "Константа в живом MCP-модуле:"
    (cd "$AISTART" && python3 -c "import habr_bookmarks as hb; print('   ', hb.VAULT_DIR)")
}

case "${1:-help}" in
    diff)     cmd_diff ;;
    apply)    cmd_apply ;;
    rollback) cmd_rollback ;;
    status)   cmd_status ;;
    *) say "Использование: fix_vault_paths.sh {diff|apply|rollback|status}
diff     — показать изменения, ничего не трогая
apply    — применить (бэкап + подтверждение + перенос статей + перезапуск MCP)
rollback — вернуть 3 файла из последнего бэкапа vault-path
status   — текущее состояние путей во всех файлах";;
esac
