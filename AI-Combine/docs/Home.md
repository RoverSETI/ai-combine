# AI-Combine — ИИ-комбайн (Manjaro, RTX 3060 12GB)

**Полностью локальный ИИ-конвейер**: роутер LLM (llama.cpp CUDA) + Open WebUI + MCP-сервер
(32 инструмента) + рой агентов + GigaAM-транскрибация + рисование (FLUX, GIMP, Inkscape) +
семантическая база знаний из vault Obsidian.

Все компоненты собраны в одной папке: **`/home/rover/AI-Combine/`** — см. [[Структура]].

## Быстрые команды

```bash
~/AiStart/start_ai.sh     # старт всего стека (симлинк на AI-Combine/AiStart)
~/AiStart/stop_ai.sh      # остановка
~/AiStart/status_ai.sh    # статус
```

## Страницы

- [[Структура]] — состав папок, алиасы старых путей
- [[Запуск-и-порты]] — сервисы, порты, health-проверки
- [[Роли-агентов]] — fast/medium/hard/mini/coder/vision/mini + маршрутизация
- [[MCP-инструменты]] — 32 инструмента :8089, фильтр 18 для WebUI
- [[Знания-и-RAG]] — база знаний из vault (165 файлов), sync/компрессор
- [[GLM-каналы]] — polza / z.ai, статусы, переключение
- [[Диагностика]] — типовые проблемы и решения

## Ключевые решения (2026-11)

- Vault Obsidian **только** `/run/media/rover/BACK/GoogleDrive/Obsidian` (бекап в ~/GoogleDrive не использовать)
- Компоненты перемещены в `AI-Combine`, старые пути — симлинки-алиасы
- ulimit fd 8192 (перманентно в start_ai.sh) — иначе RAG-батчи молча теряют файлы
- ctx qwen3.5-9b = 49152 (V-квант KV-кэша) — промпт с 18 MCP-спеками + RAG ≈ 41–42k
- Фильтр инструментов в WebUI: 18 из 32 (function_name_filter_list)
- context_compressor — глобальная WebUI-функция сжатия длинных чатов

## Git

`/home/rover` (remotes: `back` — внешний диск, `github` — RoverSETI/ai-combine, приватный).
В Git ведутся только скрипты/конфиги/доки. Модели, venv, БД, бинари, ключи — игнор.
