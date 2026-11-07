# GLM-каналы (облачная «тяжёлая артиллерия» роя)

**Правило пользователя:** выбор канала — только вручную, ZCode не тратит и не переключает
без явного разрешения. Счётчик: `AI-Combine/SwarmConfig/state/glm_usage.json`.

## Каналы

| Канал | Ключ (файл) | Endpoint / модель | Статус на 07.11 |
|---|---|---|---|
| **z.ai / Start-Coding plan** — приоритет | `~/AiStart/ZaiKey` | `api.z.ai/api/coding/paas/v4`, glm-5.3 | 🔶 ключ валиден, ждёт ресурс-пакет (ошибка 1113) |
| **polza.ai** — резерв | `~/AiStart/PolzaKey>Z.ai` | `api.polza.ai/v1`, z-ai/glm-4.6 | 🟢 проверен живым вызовом (баланс пополнен) |

## Переключение (одна команда, без рестартов)

```bash
# на polza:
mv ~/AiStart/ZaiKey ~/AiStart/ZaiKey.off
# обратно на z.ai:
mv ~/AiStart/ZaiKey.off ~/AiStart/ZaiKey
```
Файл ключа читается при каждом вызове; ключ + русская приписка в файле допустимы —
код берёт первый ASCII-токен.

## Коды ошибок

- `402 INSUFFICIENT_BALANCE` — polza: пополнить баланс на polza.ai
- `1113 Insufficient balance or no resource package` — z.ai: нужен Coding Plan
  (`z.ai/pricing`, Lite) или ресурс-пакет; оплата из РФ картой может не пройти
- `429` — лимит частоты; повторить с паузой
- `401/403` — ключ неверный/отозван

## Известные особенности

- glm-4.6 — reasoning-модель: маленький max_tokens уходит в «мышление» и ответ пуст;
  тестировать с max_tokens ≥ 500
- billing-страницы: `z.ai/manage-apikey/apikey-list`, `z.ai/pricing`
  (platform.z.ai — мёртвый домен, не искать)
