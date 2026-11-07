# Знания и RAG

## База знаний «Obsidian»

- KB id: `dfe4a423-90f1-4db7-909c-206d599b8f58` (Open WebUI 0.12.5)
- **165 файлов в structured-режиме** — зеркалит vault по папкам (19 директорий)
- модель `obsidian-knowledge` (type=collection) привязана к KB

## Vault — ЕДИНСТВЕННЫЙ правильный путь

```
`<VAULT>`/
```
`~/GoogleDrive/` — бекап, НЕ использовать и НЕ восстанавливать из него без явной просьбы.

## Скрипты синхронизации (пути уже на `<VAULT-диск>`)

```bash
~/OpenWebUI-Local/scripts/sync_knowledge.py --dry-run   # diff без изменений
~/OpenWebUI-Local/scripts/sync_knowledge.py --mode structured
```
- нужен JWT: `/tmp/webui_jwt.txt` (POST /api/v1/auths/signin)
- после перемещения компонентов — всегда `--mode structured`
- если привязка «теряет» файлы: проверить `ulimit -n 8192` у процесса WebUI
  и ошибки «Too many open files» в `~/AiStart/logs/open_webui.log`

## Три канала доступа к знаниям

1. **grep vault** — точный поиск по файлам (мгновенно)
2. **RAG WebUI** — по смыслу, модель obsidian-knowledge (+kb_search MCP)
3. **MCP Obsidian :27123** — чтение+запись, требует открытого приложения Obsidian

## context_compressor

Глобальная WebUI-функция (фильтр): сжимает историю длинных чатов и усекает
гигантские вставки. Суммаризатор — qwen2.5-coder-7b. Пороги под ctx каждой модели
(mini 9.5k, 3b — 13.5k, большие — 23k). Исходник: `SwarmConfig/functions/context_compressor.py`.
Проверено: 26 339 токенов истории → 1 902 в промпт.
