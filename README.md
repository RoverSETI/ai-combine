# ai-combine — локальный ИИ-комбайн (bare-metal)

Локальный стек ИИ на одном ПК (Manjaro, RTX 3060 12GB, Xeon E5-2686 v4):
чат-модели, рисование, речь, зрение, видео и поиск — без облака в основном контуре.

## Архитектура

```text
 Open WebUI :8080  ── OpenAI-протокол ──►  Роутер llama-server :8081 (models.ini)
        │                                        │
        │ tool_server (MCP)                      ├── эмбеддер gte-modernbert :8082
        ▼                                        ├── ComfyUI :8188 (рисование/видео, по требованию)
 MCP local-tools :8089                           └── sd.cpp (FLUX-fолбэк рисования)
   run_command, obsidian_*, kb_search,
   photo_*/video_*, agent_task/ai_agent,
   web/glm/stt/image_* (swarm_tools)
```

- **Роутер :8081** — llama.cpp (`~/llama-cuda13`, CUDA 13), модели из `AiStart/models.ini`;
  прод-чаты — `qwen3.5-9b-instruct`, быстрые — `qwen3-4b`, тяжёлые — Qwen3.6-35B-A3B (MoE на CPU).
- **Open WebUI :8080** — интерфейс человека; конфиг — env в `start_ai.sh` + persistent-config
  в `webui.db` (целостность проверяет страж `patch_webui_db.py` при каждом старте).
- **MCP local-tools :8089** — `AiStart/mcp_server.py` + генерируемый `swarm_tools.py`
  (источник — `SwarmConfig/mcp_local.json` + `gen_swarm_tools.py`).
- **Знания** — vault Obsidian на BACK-диске + конспекты Habr; поиск — `kb_search`
  (эмбеддер :8082) с grep-фолбэком.

Подробнее: [AI-Combine/AiStart/README.md](AI-Combine/AiStart/README.md) — файлы, порты,
что было сломано и как исправлено, диагностика.

## Быстрые команды

```bash
AiStart/start_ai.sh     # поднять всё (роутер, WebUI, MCP, страж конфига)
AiStart/status_ai.sh    # статус сервисов и VRAM
AiStart/stop_ai.sh      # остановить
AiStart/ai_mem.sh status  # диспетчер VRAM (chat/art/heavy/offload)
```

## Приватность

Репозиторий приватный; ключи и токены (`*_Key`, `*Token`, `PolzaKey*`) в git НЕ ведутся
(whitelist-`.gitignore`) и хранятся в `AiStart/` с правами 600.
