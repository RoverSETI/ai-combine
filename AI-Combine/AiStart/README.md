# ИИ-комбайн bare-metal: llama.cpp + Open WebUI + MCP-инструменты + база знаний Habr→Obsidian

Локальный ИИ-комплекс на RTX 3060 12 ГБ. Всё офлайн, без облаков.

## Архитектура

```
                        ┌────────────────────────────┐
  браузер :8080 ──────► │  Open WebUI 0.12 (чат)     │
                        └──┬──────────────┬──────────┘
                           │ OpenAI       │ MCP (tool_servers)
              ┌────────────▼───┐   ┌──────▼───────────────┐
              │ llama-server   │   │ MCP local-tools :8089│  run_command, obsidian_search/read,
              │ РОУТЕР :8081   │   │ (mcp_server.py)      │  habr_scan, ask_kb, photo_fix, agent_task
              │ 6 моделей      │   └──────┬───────────────┘
              └────────────────┘          │
              ┌────────────────┐          │ subprocess
              │ llama-server   │          ▼
              │ ЭМБЕДДИНГИ:8082│   ┌──────────────────────┐
              │ gte-modernbert │   │ habr_scan.py / ask_kb│
              └────────────────┘   │ photo_fix.py (LaMa)  │
                                   └──────────────────────┘
                        ┌────────────────────────────┐
  Obsidian :27123 ────► │ плагин Local REST API+MCP  │  поиск, чтение, ЗАПИСЬ заметок
  (когда запущен)       └────────────────────────────┘

  habr_scan.py ──► RSS Habr ─► фильтр по словам ─► конспект (qwen3.5-9b)
       └────► заметки .md в Obsidian (ИИ/Habr) + векторный индекс
  ask_kb.py  ──► вопрос ─► семант. поиск по конспектам ─► ответ модели
                  с пошаговым ходом решения и источниками
```

## MCP-инструменты в чате

В чате: «+» → **Инструменты** → выберите **Локальные инструменты** (local-tools)
и/или **Obsidian** (server:mcp:obsidian). Модели, умеющие вызывать инструменты:
`qwen3.5-9b-instruct` и `llama-3.1-8b-instruct` (нативный tool-calling проверен).
`qwen2.5-coder-*` инструментами НЕ пользуются (квант не соблюдает формат `<tool_call>`) —
для задач с инструментами выбирайте qwen3.5-9b.

| Инструмент | Что делает |
|---|---|
| `run_command` | терминал: bash без root, таймаут до 300 с, чёрный список (sudo/rm -rf/mkfs/dd/shutdown…) |
| `obsidian_search` / `obsidian_read` | быстрый grep-поиск и чтение заметок (работает без открытого Obsidian, vault на /BACK) |
| `habr_scan` | сканер Habr по ключевым словам → ИИ-конспекты в Obsidian (dry_run/limit/reindex) |
| `ask_kb` | вопрос к базе знаний конспектов Habr: ответ с пошаговым ходом решения и источниками |
| `photo_fix` | реставрация фото: пресеты old/modern, поворот, обрезка, шум, трещины (LaMa на GPU). Результат в `restored/` рядом с оригиналом |
| `agent_task` | мини-агент: сам составляет план и выполняет его по шагам этими же инструментами |
| подключение **obsidian** | полный доступ к ТЕКУЩЕМУ открытому vault в приложении Obsidian: поиск, чтение, **создание и правка заметок** (плагин Local REST API, порт 27123; работает только когда Obsidian запущен) |

## Примеры фраз для чата (с включёнными инструментами)

- «Выполни `df -h /` и объясни, что значит заполнение 79%»
- «Найди в обсидиане заметки про llama и перескажи кратко»
- «Проверь, что нового на Хабре» (dry-run) / «Просканируй Хабр, максимум 2 статьи»
- «Спроси базу знаний: как настроить CUDA для llama.cpp на Manjaro?»
- «Отреставрируй фото /home/rover/Изображения/ae1.jpeg, пресет old»
- «Выполни agent_task: узнай занятое место на диске и доложи»
- (при запущенном Obsidian) «Создай заметку "Покупки" с текстом …»

## Команды

| Действие | Команда |
|---|---|
| Запуск всего комплекса | `./start_ai.sh` |
| Остановка | `./stop_ai.sh` |
| Диагностика (модели/VRAM/сервисы) | `./status_ai.sh` |
| Чат | http://localhost:8080 (автовход admin@localhost/admin) |
| Сканировать Habr вручную | `.venv-tools/bin/python habr_scan.py` |
| Только показать совпадения | `.venv-tools/bin/python habr_scan.py --dry-run` |
| Спросить базу знаний | `.venv-tools/bin/python ask_kb.py "вопрос"` |
| Спросить другой моделью | `... ask_kb.py "вопрос" --model qwen3.5-9b-instruct` |
| Переиндексировать заметки | `.venv-tools/bin/python habr_scan.py --reindex` |
| Расписание сканера | `systemctl --user list-timers habr-scan.timer` |

## Файлы

- `models.ini` — 6 моделей роутера (qwen3.5-9b, coder-7b/3b/0.5b, llama-3.1-8b, nemotron-12b-vision). Ключи = полные имена опций llama-server; шаблон чата НЕ задаётся — берётся из GGUF каждой модели. qwen3.5-9b: `ctx-size = 16384` (для MCP-инструментов: specs+builtin ~6-9к токенов).
- `start_ai.sh / stop_ai.sh / status_ai.sh` — управление комплексом (MCP-сервер запускается и проверяется в start_ai.sh, health-check через MCP initialize, не голый GET).
- `mcp_server.py` — MCP-сервер local-tools (FastMCP, 127.0.0.1:8089/mcp, 7 инструментов). Запускается start_ai.sh, python из venv WebUI (там есть mcp, uvicorn, torch).
- `photo_fix.py` — реставрация фото (CLI + MCP-инструмент в одном файле). LaMa: simple-lama-inpainting (TorchScript, веса `~/.cache/torch/hub/checkpoints/big-lama.pt`), fallback cv2.inpaint.
- `habr_config.json` — ключевые слова сканера, модели, пути, лимиты. **Слова для отслеживания меняйте здесь.**
- `habr_scan.py` — сканер Habr → ИИ-конспекты → Obsidian (`ИИ/Habr` в хранилище).
- `ask_kb.py` — вопросы к базе знаний с пошаговым решением.
- `habr_state.json` — обработанные статьи (не трогать). `habr_index.json` — векторный индекс.
- `.venv-cuda12` — pip-колёса с библиотеками CUDA 12 (критично для GPU, см. ниже).
- `.venv-tools` — python-окружение сканера (requests + bs4).
- `logs/` — логи всех сервисов. `run/` — PID-файлы.
- `backup_2026-10-24/` — исходные версии всего, что переписано.
- `obsidian.json` — прежний MCP-конфиг Obsidian (формат npx-сервера; не используется этим стеком, оставлен).

## Конфигурация MCP-подключений WebUI

Хранится в БД `webui.db` (в `OpenWebUI-Local/.venv/.../open_webui/data/`), ключ `tool_server.connections`
(таблица `config`). Бэкапы: `webui.db.bak-20261024`, `webui.db.bak-20261031`. Два подключения:

1. **local-tools** → `http://127.0.0.1:8089/mcp`, `auth_type: none`.
2. **obsidian** → `http://127.0.0.1:27123/mcp/`, `auth_type: bearer` (ключ — из data.json плагина).
   В `config.function_name_filter_list` задан белый список из 9 инструментов (vault_list/read/write/append/patch/delete, search_simple/query, tag_list) — остальные 11 specs вырезаются, чтобы промпт влезал в контекст.

Чтобы подключение obsidian работало, Obsidian должен быть запущен (плагин слушает порт только тогда).
Плагин работает с ТЕКУЩИМ активным vault: сейчас это `/home/rover/GoogleDrive/Obsidian` (не /BACK!).

OpenAI-соединение WebUI: ключ `sk-local-dummy` (роутер ключи не проверяет, но пустой ключ WebUI считает офлайн), `provider: llama.cpp` в `OPENAI_API_CONFIGS["0"]`.

## Что было сломано и как исправлено

1. **GPU не работал вовсе**: сборка `~/llama` слинкована с CUDA 12 (`libcudart.so.12`, `libcublas.so.12`), а в системе CUDA 13.3 — бэкенд молча не грузился, все модели шли на CPU (4–5 ток/с). → В `.venv-cuda12` установлены подлинные pip-колёса `nvidia-cuda-runtime-cu12` + `nvidia-cublas-cu12`, `start_ai.sh` подставляет их через `LD_LIBRARY_PATH`. Теперь 9B Q8_0 даёт ~33 ток/с в VRAM 9.1/12 ГБ. (Альтернатива навсегда: пересобрать llama.cpp из исходников под CUDA 13.)
2. **Глобальный `--chat-template qwen2`** ломал Llama-3.1 и Nemotron (чужие спец-токены). → Убран; каждая модель использует свой шаблон из GGUF.
3. **Open WebUI вообще не запускался**: команды `python3 -m open_webui.apps.webui.main` в 0.12.3 не существует, а вывод лился в `/dev/null`. → Запуск через `open-webui serve`.
4. **`sudo pkill -9 -f python3`** в старом старте убивал ВСЕ процессы python3 в системе. → stop_ai.sh бьёт точечно по PID-файлам и точным шаблонам, sudo не нужен.
5. **`sleep 5` вместо готовности** → health-check каждого сервиса с таймаутом и выводом лога при падении.
6. **Несуществующие env-переменные** (`ENABLE_MCP`, `COMPREG_AUTODETECT`, `AVOID_CAPABILITIES_DETECTION`, `ENABLE_DYNAMIC_MODELS`) игнорировались, а реальные флаги фоновых генераций хранятся в БД WebUI. → Выключены прямо в БД (`task.title/tags/follow_up/query.*`), Ollama-коннект отключён, RAG WebUI переключён на локальный эмбеддер :8082.
7. **ini-ключи моделей** (`c`, `ngl`) были сокращёнными → заменены на полные (`ctx-size`, `n-gpu-layers`, `flash-attn`, `cache-type-k`), экономия VRAM: KV-кэш в q8_0 + FlashAttention.

## Проблемы v2 (MCP) и их решения

1. **Health-check MCP в start_ai.sh**: GET `/mcp` без заголовков даёт 406 от FastMCP. → проверка через полноценный MCP `initialize` (POST + `Accept: application/json, text/event-stream`).
2. **`qwen2.5-coder-*` не соблюдают формат `<tool_call>`** своего шаблона (квант Q4_K_M): модель отвечает ```json/```xml в контенте, llama.cpp не распознаёт вызов. → для задач с инструментами использовать `qwen3.5-9b-instruct` или `llama-3.1-8b-instruct` (проверено: нативные tool_calls работают).
3. **Спеки плагина Obsidian (20 шт., ~15 кБ) не влезали в контекст 8192** → `function_name_filter_list` (9 нужных инструментов) + `ctx-size = 16384` у qwen3.5-9b.
4. **API-обновление конфига OpenAI записало `openai.enable: null`** (в форме не передали ENABLE_OPENAI_API) → модели исчезли; вылечено записью `true` в БД. Урок: при /openai/config/update передавать все поля.
5. **Мини-агент вызывал run_command без аргументов** (пустые properties в specs) → в `_tool_specs()` свойства описаны явно с required.
6. **Заметки через плагин пишутся в активный vault Obsidian** — он может отличаться от vault в mcp-конфигах. Сверять по `GET :27123/vault/`.

## Модели — что чем делать

| Задача | Модель |
|---|---|
| Общие вопросы, конспекты, русский язык | `qwen3.5-9b-instruct` («думающая»: сначала размышляет — это норма) |
| Код, отладка, задачи с решением | `qwen2.5-coder-7b` (быстрая, по умолчанию в ask_kb) |
| Быстрые мелочи | `qwen2.5-coder-0.5b / 3b` |
| Картинки/скриншоты | `nvidia-nemotron-12b-vision` |
| Похоже на Llama | `llama-3.1-8b-instruct` |

## Решение проблем

- **Медленно генерирует** → `./status_ai.sh`: если VRAM не растёт при запросах, значит CUDA-колёса не подхватились (см. сообщение в начале `start_ai.sh`).
- **Не влезает модель** (ошибка CUDA OOM при загрузке) → уменьшить `ctx-size` этой модели в `models.ini` или взять менее тяжёлый квант.
- **Сканер: «Хранилище не смонтировано»** → подключить диск BACK (Obsidian-хранилище на `/run/media/rover/BACK`).
- **Сканер: «Роутер недоступен»** → сначала `./start_ai.sh` (таймер просто повторит в следующий слот).
- **Первый ответ на новой модели медленный** — она грузится в VRAM с диска (5–15 с), это нормально при `models-max 1`.
- **Модель игнорирует инструменты / вызывает их текстом** → переключитесь на `qwen3.5-9b-instruct` или `llama-3.1-8b-instruct`.
- **Ошибка "request (N tokens) exceeds the available context size"** → увеличить `ctx-size` модели в `models.ini` (у qwen3.5-9b уже 16384) и перезапустить `./start_ai.sh`.
- **"Failed to connect to MCP server 'obsidian'"** → запустить Obsidian (плагин слушает порт только при запущенном приложении).
- **Забыть токен/права WebUI** — автовход `admin@localhost` / `admin`; БД в `site-packages/open_webui/data/` (бэкапы `webui.db.bak-*` там же).
