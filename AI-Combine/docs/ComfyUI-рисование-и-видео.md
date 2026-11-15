# ComfyUI — рисование и видео в комбайне (установлен 13.11.2026)

## Обзор

ComfyUI — основной движок генерации изображений и видео. Заменяет sd.cpp в
MCP-инструментах (sd.cpp остался фолбэком). Порт **127.0.0.1:8188**, свой
web-интерфейс (редактор нод) открывается в браузере по тому же адресу.

- Установка: `AI-Combine/ComfyUI/` (venv uv, Python 3.12, torch 2.14.1+cu126)
- Custom node: `custom_nodes/ComfyUI-GGUF` (GGUF-загрузчики unet/clip)
- Автозапуск: блок 5.1 в `AiStart/start_ai.sh` (флаг `--reserve-vram 1.5`,
  не пускается повторно, если уже работает)
- Лог: `AiStart/logs/comfyui.log`, pid: `AiStart/run/comfyui.pid`
- Веб-UI: http://127.0.0.1:8188 — полноценный редактор нод, можно работать
  отдельно от всего комбайна; новые ноды = git clone в `custom_nodes/` + рестарт

## Модели: одна копия на диске + симлинки

Все весы физически лежат в `AI-Models/`, движкам раздаются симлинками.

| Файл | Физически | Симлинк в ComfyUI | Назначение |
|---|---|---|---|
| flux1-schnell-Q4_K_S.gguf (6,4 ГБ) | `AI-Models/flux/` | models/unet | быстрое рисование, 4 шага |
| flux1-dev-Q5_K_S.gguf (7,7 ГиБ) | `AI-Models/flux/` | models/unet | качество, 20 шагов (unsloth-зеркало) |
| flux1-kontext-dev-Q5_K_M.gguf | `AI-Models/flux/` | models/unet | редактирование фото текстом |
| t5-v1_1-xxl-encoder-Q5_K_M.gguf (3,2 ГБ) | `AI-Models/flux/` | models/clip | энкодер FLUX |
| clip_l.safetensors (235 МБ) | `AI-Models/flux/` | models/clip | энкодер FLUX |
| ae.safetensors (fp16, 167 МБ) | `AI-Models/flux/` | models/vae | декодер FLUX (244 тензора — годен) |
| Wan2.2-T2V-A14B-{High,Low}Noise-Q4_K_S.gguf | `AI-Models/video/wan/` | models/unet | видео T2V, два эксперта |
| umt5-xxl-encoder-Q4_K_M.gguf (3,4 ГиБ) | `AI-Models/video/wan/` | models/clip | энкодер Wan (type=wan) |
| Wan2.1_VAE.safetensors | `AI-Models/video/wan/` | models/vae | декодер Wan |

Источник зеркал (все без лицензионных гейтов): unsloth/FLUX.1-dev-GGUF,
QuantStack/FLUX.1-Kontext-dev-GGUF, QuantStack/Wan2.2-T2V-A14B-GGUF,
city96/umt5-xxl-encoder-gguf. Оригинал city96/FLUX.1-dev-gguf — gated.

## Три канала использования

### 1. OpenWebUI (рисование из чата)
Админка → Настройки → Изображения: движок **ComfyUI**, `http://127.0.0.1:8188`,
модель `flux1-dev-Q5_K_S.gguf`, размер 1024×1024, шаги 20. Workflow в API-формате
+ типизированные nodes-переопределения (prompt/width/height/steps/seed/model) —
список моделей подтягивается из `/object_info` автоматически.
Ставилось через админ-API: `POST /api/v1/images/config/update` (admin@localhost).

### 2. MCP image_generate (ZCode / рой)
`AiStart/swarm_tools.py` → сначала ComfyUI (`tools/comfy_flux.py`), если сервер
не поднят — фолбэк sd.cpp. VRAM освобождает сам (выгрузка моделей роутера при
<5 ГБ свободных). Таймаут MCP-клиента 15 с — файл появляется позже, проверять
путь/лог.

### 3. CLI
```bash
~/AI-Combine/ComfyUI/.venv/bin/python ~/AI-Combine/tools/comfy_flux.py "промпт" --width 1024 --height 1024
~/AI-Combine/ComfyUI/.venv/bin/python ~/AI-Combine/tools/comfy_wan.py "промпт" --length 81
```

## Параметры-законы (проверено)

- **cfg 1.0** для всех FLUX/Wan-дистиллятов — иначе выгорит
- sampler **euler**, scheduler **simple**
- шаги: schnell 4, dev 20, Kontext 20 (guidance 2.5), Wan2.2 20 (10+10)
- размер кратен 8 (лучше 16 для Wan), batch_size 1 на 12 ГБ VRAM
- декодер — **VAEDecodeTiled** (tile 512) — защита от OOM в конце
- длина видео Wan: 4n+1 кадров (81 ≈ 5 с при 16 fps), стартовый тест 480p
- VRAM-политика: ComfyUI умеет dynamic offloading (работает даже при занятой
  LLM, медленнее); для скорости выгружать LLM (`ai_unload all`)
- выход по умолчанию: `~/Pictures/swarm/`

## Kontext (редактирование фото текстом) — ноды

Local workflow: LoadImage → FluxKontextImageScale → VAEEncode →
CLIPTextEncode (инструкция) → **ReferenceLatent** (conditioning+latent) →
FluxGuidance 2.5 → KSampler (denoise 1.0, latent = кодированная картинка) →
VAEDecodeTiled → Save. MCP-обёртка — следующий шаг (см. vault-раздел «Инструменты»).

## Новые семейства 14.11: Z-Image Turbo, Qwen-Image 2.1 (T2I + Edit), MOSS

Физика по семействам: `AI-Models/z-image/`, `AI-Models/qwen-image/`,
`AI-Models/moss-image/` (curl сразу в целевую папку, без дублей).

- **Z-Image Turbo** (int8_convrot 5,8 ГБ + bf16 11,5): CLIPLoaderGGUF
  (type=qwen_image) + **GGUF-энкодер Qwen3-4B из стека** (AI-Models/text/,
  auto-детект TEModel.QWEN3_4B→z_image.te) + ModelSamplingAuraFlow shift 3.0 +
  TextEncodeZImageOmni + KSampler 8 шагов cfg 1.0. VAE общий с FLUX.
  Тест: 1024², ~50 с. bf16-вариант — макс. качество, первый кандидат на удаление.
- **Qwen-Image 2.1 Turbo** (int8 6,8 ГБ + qwen3vl_8b энкодер 8,7 + VAE 0,63):
  CLIPLoader type=qwen_image + TextEncodeQwenImage21 (prompt+negative в одной
  ноде, negative=выход [4,1]) + **БЕЗ ModelSamplingSD3** (shift даёт шум) +
  KSampler 8 шагов cfg 1.0. Русский промпт понимает нативно. Тест ~55 с.
- **Edit-режимы**: Qwen-Image 2.1 Edit (база int8 + pe_i2i энкодер qwen3.5_9b
  int8), MOSS-Image (sft int8 + edit-LoRA + qwen3.5_9b int8 энкодер +
  flux2-vae) — под встроенные шаблоны Templates; докачка сразу в AI-Models.
  P-H3-Image отложен (20B, медленный на 3060).
- Готовые workflow: `ComfyUI/user/default/workflows/` (Z-Image-Turbo-1024,
  Qwen-Image-21-Turbo-1024, API-формат).
- **VRAM-политика (пользователь)**: ComfyUI — главный по графике; LLM-рой
  выгружается перед генерацией (ai_unload / kill инстанса; multi-load роутера
  поднимает обратно при обращении). «Последний запущенный получает память».

## Перевод промптов ru→en (14.11)

**AlekPet Nodes** (custom_nodes) + **Argos Translate** офлайн в venv
(`uv pip install --python .venv/bin/python argostranslate`, модель ru→en
установлена). Нода **ArgosTranslateCLIPTextEncodeNode** — вместо CLIP Text
Encode, промпт по-русски. GoogleTranslateNode не работает из РФ (gtx «Sorry»).
Custom-Scripts (pythongosssss) — тоже стоит (автокомплит/ShowText), перевода
в нём нет. Qwen-Image русский понимает нативно — перевод для FLUX/Wan/Z-Image.

## Реставрация и раскраска фото (15.11)

Что это: фотография проходит цепочку из 4 ступеней — быстрая чистка →
ИИ-реставрация царапин и лиц → детализация → раскраска/правки промптом.

### Для человека (ComfyUI UI и чат WebUI)

**Инструкция:**
1. Открой ComfyUI (http://127.0.0.1:8188) и в меню Workflow загрузи нужный
   сценарий из `ComfyUI/user/default/workflows/`:
   - «Реставрация-старых-фото-BOPBTL» — царапины, шум, потёртости + лица;
   - «Раскраска-и-реставрация-Qwen-21-Edit» — раскрасить ч/б или убрать
     повреждения промптом;
   - «Дополнение-краёв-Qwen-21-Edit» — дорисовать оторванные края (outpaint);
   - «SeedVR2-детализация-фото» — вернуть мелкие детали и резкость.
2. В ноде Load Image нажми «choose file to upload» и выбери фото.
3. Проверь текст промпта (можно писать по-русски), нажми Run.
4. Результат — в папке `ComfyUI/output/` (SaveImage).

**Пример:** в чате WebUI (модель qwen3.5-9b-instruct, тулсет local-tools)
просто напиши: «Отреставрируй /home/rover/Фото/1950.jpg — убери царапины и
улучши лица, потом раскрась». Модель сама вызовет `photo_restore_comfy`,
затем `photo_colorize`, и вернёт путь к готовым файлам.

**Пояснение:** быстрая чистка (обрезка, поворот, шум) — тул `photo_fix`,
он не использует нейросети и почти не занимает видеокарту; тяжёлые ступени
(BOPBTL, SeedVR2, Qwen-Edit) требуют запущенного ComfyUI и свободной VRAM —
LLM-рой выгружай перед генерацией («последний запущенный получает память»).

### Для ИИ (API: MCP :8089 и ComfyUI :8188)

**Инструкция:** два MCP-тула на сервере local-tools (`http://127.0.0.1:8089/mcp`):
- `photo_restore_comfy(input_path, output?, mode="scratch"|"quality",
  face_enhance=true, scale=1|2|4)` — BOPBTL: царапины+качество, лица,
  апскейл 4x-UltraSharp;
- `photo_colorize(input_path, output?, mode="colorize"|"restore"|"edit",
  prompt?, steps=8, seed=0, resolution=1024)` — Qwen-Image 2.1 Turbo Edit:
  раскраска, реставрация промптом, любые правки своим текстом.
Результат: `~/Pictures/comfy/<время>_<prefix>.png` или путь из `output`.

**Пример:**
```bash
# вызов тула через MCP (сокращённо; полный handshake: initialize → initialized → tools/call)
curl -s http://127.0.0.1:8089/mcp -H 'mcp-session-id: <SID>' \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{
       "name":"photo_colorize","arguments":{"input_path":"/tmp/старое.jpg",
       "mode":"colorize","steps":8}}}'
# прямой граф в ComfyUI: POST http://127.0.0.1:8188/prompt {"prompt": {...}}
# ключ edit-энкода: TextEncodeQwenImage21 → "images": {"image_1": ["1", 0]}
```

**Пояснение:** референс-картинка для Qwen-Edit подаётся в autogrow-поле
`images` **словарём** (`{"image_1": [node,slot]}`) — строкой `image_1` не
работает; латент у этой ноды пустой (размер = референс), поэтому denoise 1.0.
pe_i2i-энкодер — это промпт-энхансер, НЕ conditioning. BOPBTL-модели лежат в
подпапках (`models/checkpoints/restoration/mapping_scratch/latest_net_mapping_net.pth`)
— при переносе не складывать плоско: все файлы называются `latest_net_*.pth`
и перезапишут друг друга. Физика моделей — в официальных папках
`ComfyUI/models/` (политика 15.11), `AI-Models/` держит симлинки-алиасы для
скриптов. Workflows: канонично `ComfyUI/user/default/workflows/` (ComfyUI
читает оттуда; это вложенный git-репо — в комбайн не коммитится), копии для
git — `docs/workflows-фото/`, при правках синхронизировать. Бэкап правок
mcp_server.py: `Sys_Backup/photo-restoration-20261115/`.

## Откат

`gio trash ~/AI-Combine/ComfyUI` + убрать блок 5.1 из start_ai.sh (бэкап
`~/Sys_Backup/start_ai.sh.bak-20261113-2044`) → image_generate сам вернётся на
sd.cpp. Настройки WebUI переключаются в админке без правки файлов.

## Гайд с картинками и историями настроек

Vault: `<VAULT>/GoogleDrive/Obsidian/ИИ/Инструменты-Инструкции/` — полные
инструкции по OpenWebUI, рою Z.ai и ComfyUI (создан 13.11.2026).
