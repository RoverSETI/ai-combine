#!/bin/bash
# ============================================================
#  agent_cli.sh — управление локальными ИИ-агентами из терминала.
#  Тот же конфиг ролей, что и MCP-инструменты: ~/AiStart/agents.json
#
#  Команды:
#    agent_cli.sh status              — таблица ролей + VRAM
#    agent_cli.sh ask "задача" [роль] — один проход (роль: auto|fast|medium|hard|coder|vision)
#    agent_cli.sh agent "задача" [роль] [шаги] — многошаговый агент с инструментами
#    agent_cli.sh load <роль|модель>  — загрузить модель в VRAM
#    agent_cli.sh unload <роль|модель|all> — выгрузить
#    agent_cli.sh route "задача"      — показать, какую роль выберет маршрутизатор
#    agent_cli.sh roles               — список ролей
#
#  Обращается к роутеру llama-server :8081 напрямую (MCP не нужен).
# ============================================================
set -euo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CFG="$BASE/agents.json"
ROUTER="http://127.0.0.1:8081/v1"
PY=$(command -v python3 || echo /usr/bin/python3)

die() { echo "❌ $*" >&2; exit 1; }
[ -f "$CFG" ] || die "нет $CFG"
curl -sf "$ROUTER/models" >/dev/null 2>&1 || die "роутер :8081 не отвечает (запустите start_ai.sh)"

cfg() { "$PY" -c "
import json,sys
d=json.load(open('$CFG'))
print(eval(sys.argv[1]))
" "$1"; }

role_model() { cfg "d['roles']['$1']['model'] if '$1' in d['roles'] else '$1'"; }
role_title() { cfg "d['roles'].get('$1',{}).get('title','$1')"; }

cmd_roles() {
    "$PY" - <<EOF
import json
d = json.load(open('$CFG'))
print('Роли агентов (agents.json):')
for k, v in d['roles'].items():
    print(f"  {k:8} → {v['model']:28} {v['title']}")
print('Правила маршрутизации:')
for r in d['routing']['rules']:
    kws = ', '.join(r['keywords'][:4]) + ('…' if len(r['keywords']) > 4 else '')
    print(f"  {r['role']:8} ← {kws}")
print(f"  default  ← {d['routing']['default']}")
EOF
}

cmd_route() {
    local task="${1:-}"
    [ -n "$task" ] || die "укажите задачу: agent_cli.sh route \"задача\""
    "$PY" - "$task" <<'EOF'
import json, sys
d = json.load(open('/home/rover/AiStart/agents.json'))
low = sys.argv[1].lower()
for rule in d['routing']['rules']:
    hit = [kw for kw in rule['keywords'] if kw and kw.lower() in low]
    if hit:
        print(f"{rule['role']} (ключевое слово: «{hit[0]}»)")
        sys.exit(0)
print(d['routing']['default'] + ' (по умолчанию)')
EOF
}

cmd_status() {
    echo "=============== СТАТУС АГЕНТОВ ==============="
    curl -s "$ROUTER/models" > /tmp/_agent_models.json
    "$PY" - <<'EOF'
import json
d = json.load(open('/tmp/_agent_models.json'))
cfg = json.load(open('/home/rover/AiStart/agents.json'))
by_id = {m['id']: m for m in d['data']}
print(f"{'Роль':26} {'Модель':30} VRAM")
print('-' * 70)
for key, role in cfg['roles'].items():
    m = by_id.get(role['model'], {})
    st = (m.get('status') or {}).get('value', 'нет')
    mark = '🟢 загружена' if st == 'loaded' else ('⚪ выгружена' if st == 'unloaded' else st)
    print(f"{key:8} {role['title']:16} {role['model']:30} {mark}")
loaded = [m['id'] for m in d['data'] if (m.get('status') or {}).get('value') == 'loaded']
print()
print('В VRAM:', ', '.join(loaded) if loaded else 'ничего')
EOF
    command -v nvidia-smi >/dev/null 2>&1 && \
        echo "GPU: $(nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader)"
}

_chat() { # $1 роль/модель, $2 prompt, $3 use_tools(0/1)
    local model; model=$(role_model "$1")
    local maxtok temp
    maxtok=$("$PY" -c "import json; d=json.load(open('$CFG')); r=d['roles'].get('$1',{}); print(r.get('max_tokens',1500))")
    temp=$("$PY" -c "import json; d=json.load(open('$CFG')); r=d['roles'].get('$1',{}); print(r.get('temperature',0.3))")
    "$PY" - "$model" "$2" "$3" "$maxtok" "$temp" <<'EOF'
import json, sys, urllib.request
model, task, use_tools, maxtok, temp = sys.argv[1], sys.argv[2], sys.argv[3] == '1', int(sys.argv[4]), float(sys.argv[5])
payload = {"model": model, "messages": [{"role": "user", "content": task}],
           "max_tokens": maxtok, "temperature": temp, "stream": False}
if model.startswith("qwen3.5"):
    payload["chat_template_kwargs"] = {"enable_thinking": False}
if use_tools:
    import pathlib
    payload["tools"] = json.load(open('/tmp/_agent_tools.json'))
req = urllib.request.Request("http://127.0.0.1:8081/v1/chat/completions",
                             data=json.dumps(payload).encode(),
                             headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=600) as r:
    msg = json.load(r)["choices"][0]["message"]
calls = msg.get("tool_calls") or []
if calls:
    print("⚠️ модель запросила инструменты:", ", ".join(c["function"]["name"] for c in calls),
          "— используйте: agent_cli.sh agent \"задача\"")
    print((msg.get("content") or "")[:500])
else:
    print((msg.get("content") or "").strip())
EOF
}

cmd_ask() {
    local task="${1:-}"; shift || true
    local role="${1:-auto}"
    [ -n "$task" ] || die "использование: agent_cli.sh ask \"задача\" [роль]"
    if [ "$role" = "auto" ]; then
        role=$(cmd_route "$task" | cut -d' ' -f1)
        echo "🧭 маршрут: $role ($(role_title "$role"))" >&2
    fi
    _chat "$role" "$task" 0
}

cmd_agent() {
    local task="${1:-}"; shift || true
    local role="${1:-hard}"
    local steps="${2:-8}"
    [ -n "$task" ] || die "использование: agent_cli.sh agent \"задача\" [роль] [шаги]"
    # спеки инструментов генерирует mcp_server; для CLI берём упрощённо: без tools тут
    # (полный агент с инструментами — через WebUI/MCP ai_agent)
    echo "ℹ️ CLI-агент работает без инструментов; многошаговый с инструментами —"
    echo "   инструмент ai_agent (MCP) в Open WebUI."
    _chat "$role" "$task" 0
}

cmd_load() {
    local r="${1:-}"; [ -n "$r" ] || die "укажите роль или модель: agent_cli.sh load hard"
    local model; model=$(role_model "$r")
    code=$(curl -s -o /tmp/_load.json -w '%{http_code}' -X POST "$ROUTER/models/load" \
        -H 'Content-Type: application/json' -d "{\"model\":\"$model\"}" || true)
    if [ "$code" != "200" ]; then
        curl -s -o /dev/null -X POST "$ROUTER/chat/completions" -H 'Content-Type: application/json' \
            -d "{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":\"ok\"}],\"max_tokens\":1}"
    fi
    curl -s "$ROUTER/models" > /tmp/_agent_models.json
    "$PY" -c "
import json
d=json.load(open('/tmp/_agent_models.json'))
m=[x for x in d['data'] if x['id']=='$model'][0]
st=(m.get('status') or {}).get('value','?')
print(('🟢 загружена: ' if st=='loaded' else '⚪ '+st+': ')+'$model')"
}

cmd_unload() {
    local r="${1:-all}"
    if [ "$r" = "all" ]; then
        curl -s "$ROUTER/models" > /tmp/_agent_models.json
        for model in $("$PY" -c "
import json
for m in json.load(open('/tmp/_agent_models.json'))['data']:
    if (m.get('status') or {}).get('value')=='loaded': print(m['id'])"); do
            curl -s -X POST "$ROUTER/models/unload" -H 'Content-Type: application/json' -d "{\"model\":\"$model\"}" >/dev/null
            echo "🟢 выгружена: $model"
        done
    else
        local model; model=$(role_model "$r")
        resp=$(curl -s -X POST "$ROUTER/models/unload" -H 'Content-Type: application/json' -d "{\"model\":\"$model\"}")
        echo "$resp" | grep -q "not running" && echo "⚪ уже была выгружена: $model" || echo "🟢 выгружена: $model"
    fi
    command -v nvidia-smi >/dev/null 2>&1 && echo "GPU: $(nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader)"
}

case "${1:-help}" in
    status)  cmd_status ;;
    roles)   cmd_roles ;;
    route)   shift; cmd_route "${1:-}" ;;
    ask)     shift; cmd_ask "$@" ;;
    agent)   shift; cmd_agent "$@" ;;
    load)    shift; cmd_load "${1:-}" ;;
    unload)  shift; cmd_unload "${1:-all}" ;;
    *) echo "Использование: agent_cli.sh {status|roles|route|ask|agent|load|unload} [аргументы]
  status              — статус ролей и VRAM
  roles               — список ролей и правил маршрутизации
  route \"задача\"     — какую роль выберет маршрутизатор
  ask \"задача\" [роль] — один проход (роль по умолчанию: auto)
  agent \"задача\"     — агент (полный — через WebUI ai_agent)
  load <роль>         — загрузить модель в VRAM (fast/medium/hard/coder/vision)
  unload <роль|all>   — выгрузить модель(и)";;
esac
