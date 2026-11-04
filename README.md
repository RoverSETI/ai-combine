# ai-combine — локальный ИИ-комбайн

Локальная офлайн-система ИИ на Manjaro + RTX 3060: чат с локальными LLM,
поиск по личным заметкам (RAG), bash-инструменты, работа с Obsidian
и автоматическая выкачка статей с Хабра.

> Репозиторий ведётся «белым списком»: корень = `/home/rover`,
> всё вне перечисленного в `.gitignore` игнорируется. Секреты
> (`PolzaKey*`, `.webui_token`, `.webui_secret_key`) в Git не попадают —
> проверено двойным фильтром.

## Состав

| Каталог | Что внутри |
|---|---|
| `AiStart/` | ядро: start/stop/status, MCP-сервер `mcp_server.py` (22 инструмента), инструменты Хабра (`habr_scan/bookmarks/fetch`), `ask_kb`, `photo_fix`, конфиги (`models.ini`, `agents.json`) |
| `OpenWebUI-Local/` | скрипты Знаний (`sync_knowledge.py` — инкрементальная синхронизация vault ↔ база по sha256), E2E-тесты, инструкции (`ИНСТРУКЦИЯ-ИИ.md`, графическая HTML-версия) |
| `llama-cuda13/ОТКАТ.txt` | инструкция отката llama.cpp (сборка b12057, CUDA 13) |
| `GoogleDrive/Obsidian/ИИ/ИИ-комбайн — полная инструкция ….md` | полная инструкция со схемами (base64-картинки) — живёт в vault, версия в Git |

## Быстрый старт

```bash
~/AiStart/start_ai.sh        # роутер :8081 + эмбеддер :8082 + MCP :8089 + WebUI :8080
# чат: http://localhost:8080  (модель «Obsidian Знания (qwen3.5-9b)»)
```

## Ключевые технологии (кратко)

- **llama.cpp b12057, CUDA 13** — сборка под системный toolkit; авто-выбор
  сборки при старте, откат на CUDA 12 за 5 секунд (см. `AiStart/ОТКАТ.txt`).
- **RAG**: гибрид семантики (gte-modernbert :8082) и grep со стеммингом;
  база «База знаний Obsidian» в Open WebUI 0.12.5.
- **MCP local-tools** (:8089/mcp): run_command (блок-лист деструктива),
  obsidian_search/read, habr_search/bookmarks/save/fetch_*, kb_search,
  agent_task, ai_ask/agent/status/load/unload, photo_fix.
- **Фоновая закачка Хабра**: очередь `habr_fetch_jobs.json` с чекпоинтом
  после каждой статьи, resume после перезагрузки, лог `logs/habr_fetch.log`.
  Заметки — самодостаточные .md с картинками base64 в теле.

## Документация

- `OpenWebUI-Local/ИНСТРУКЦИЯ-ИИ.md` — текстовая шпаргалка (порты, API, обслуживание).
- `OpenWebUI-Local/ИНСТРУКЦИЯ-ГРАФИКА.html` — графическая версия (офлайн, base64).
- `AiStart/GIT-УЧЕБНИК.md` — как работать с этим репозиторием.

## Хранилища

| Имя remote | Адрес | Назначение |
|---|---|---|
| `back` | `/run/media/rover/BACK/git/ai-combine.git` (bare) | локальная резервная копия, офлайн |
| `github` | `git@github.com:RoverSETI/ai-combine.git` | облачная копия (приватная) |
