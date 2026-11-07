# MCP-инструменты (:8089)

Сервер: `AI-Combine/AiStart/mcp_server.py` + `swarm_tools.py` (транспорт streamable-http).
Полный набор — **32 инструмента**; в чатах WebUI через `function_name_filter_list`
включены **18** (остальные — служебные, вызывают пинг-понг у reasoning-моделей).

## Основные (в чате WebUI)

| Инструмент | Что делает |
|---|---|
| habr_search / habr_read / habr_save / habr_bookmarks | поиск по habr.com, чтение статьи, сохранение в Obsidian, закладки за период |
| kb_search / ask_kb | семантический поиск по базе Знаний + конспектам Хабра |
| obsidian_search / obsidian_read | grep и чтение vault (`<VAULT-диск>`) |
| web_search / web_fetch | DuckDuckGo без ключа + чтение страниц (SSRF-гвард) |
| glm_think / glm_review / glm_vision | облачный GLM: сложное / проверка / картинки |
| stt_transcribe | GigaAM-v3 транскрибация (локально, GPU) |
| image_describe / image_generate | nemotron-VL и FLUX (sd.cpp) |
| ai_status / ai_ask | статус ролей / вопрос к рою |

## Служебные (вне чата, полный список на :8089)

`run_command`, `habr_scan` (таймер systemd), `habr_fetch_start/status/resume/stop/log`,
`photo_fix` (LaMa), `agent_task`, `ai_agent`, `ai_load`, `ai_unload`, `stt_listen`,
`image_generate_cloud`.

## Правила вызовов GLM

- max 2 вызова на задачу (счётчик `SwarmConfig/state/glm_usage.json`)
- «проверка локального агента» — главная роль; помечать ответ «— GLM (облако)»
- переключение каналов — только пользователь ([[GLM-каналы]])

## Проверка

```bash
curl -X POST http://127.0.0.1:8089/mcp -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"1.0"}}}'
```
