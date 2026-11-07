"""
title: Context Compressor (swarm)
author: RoverSETI + ZCode
version: 1.0.0
description: Сжимает историю чата и гигантские вставки, чтобы длинные диалоги не переполняли контекст локальных моделей (:8081). Суммаризатор — сама модель запроса (без переключений VRAM); при сбое — гарантированное отбрасывание старых сообщений.
"""
import json
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # любой редирект = ошибка (адреса константы-липбек)


_opener = urllib.request.build_opener(NoRedirect)
ROUTER = "http://127.0.0.1:8081"  # ЕДИНСТВЕННЫЙ разрешённый хост


class Filter:
    class Valves:
        max_ctx_tokens: int = 23000   # порог по умолчанию (у больших моделей ctx 32768)
        reserve_tokens: int = 3000    # запас на ответ
        keep_recent: int = 4          # сколько последних сообщений не сжимать
        chunk_tokens: int = 7000      # кусок истории для суммаризации
        max_single_input_tokens: int = 10000  # лимит одиночного сообщения (усечение)
        summarizer_model: str = "qwen2.5-coder-7b"  # НЕ reasoning — иначе резюме уходят в thinking
        enabled: bool = True

    # индивидуальные пороги под ctx из models.ini: порог ≈ ctx - запас
    MODEL_LIMITS = {
        "qwen3-4b-instruct-2507": 9500,       # ctx 12288
        "qwen2.5-coder-3b": 13500,            # ctx 16384
        "qwen2.5-coder-0.5b": 1800,           # ctx 2048
        "qwen3.5-9b-instruct": 23000,         # ctx 32768
        "qwen2.5-coder-7b": 23000,            # ctx 32768
        "llama-3.1-8b-instruct": 23000,       # ctx 32768
        "nvidia-nemotron-12b-vision": 23000,  # ctx 32768
    }

    def __init__(self):
        self.valves = self.Valves()

    def _budget(self, model):
        limit = self.MODEL_LIMITS.get(model, self.valves.max_ctx_tokens)
        return limit - self.valves.reserve_tokens

    def _single_limit(self, model):
        return min(self.valves.max_single_input_tokens, max(600, int(self._budget(model) * 0.55)))

    # ------------------------------------------------ helpers
    def _post_json(self, path, payload, timeout):
        url = ROUTER + path
        req = urllib.request.Request(
            url, json.dumps(payload).encode(), {"Content-Type": "application/json"}
        )
        with _opener.open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())

    def _count_tokens(self, text, model):
        try:
            r = self._post_json("/tokenize", {"model": model, "content": text}, 10)
            return len(r.get("tokens", []))
        except Exception:
            return max(1, len(text) // 3)  # оценка: ру/эн смесь ~3 симв/токен

    def _truncate_middle(self, text, max_tokens, model):
        """Голова + хвост, середина — с пометкой. Голове 60% лимита, хвосту 25%."""
        total = self._count_tokens(text, model)
        if total <= max_tokens:
            return text
        chars = len(text)
        ratio = max_tokens / total
        head_end = int(chars * ratio * 0.70)
        tail_start = int(chars - chars * ratio * 0.30)
        removed = tail_start - head_end
        return (
            text[:head_end]
            + f"\n\n[...усечено {removed} символов середины для экономии контекста...]\n\n"
            + text[tail_start:]
        )

    def _summarize_msgs(self, msgs, model):
        """Суммаризировать список сообщений кусками ≤ chunk_tokens. Возврат текста резюме."""
        sys_p = (
            "Ты сжиматель контекста. Сожми переданный фрагмент диалога в краткое резюме: "
            "факты, решения, имена файлов/пути, числа, команды, незавершённые задачи. "
            "Без вежливости и воды. Язык — русский. Максимум 250 слов."
        )
        parts, buf, buf_tok = [], [], 0
        for m in msgs:
            content = m.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            t = f"[{m.get('role')}] {content}"
            tok = self._count_tokens(t, model)
            if buf and buf_tok + tok > self.valves.chunk_tokens:
                parts.append("\n\n".join(buf))
                buf, buf_tok = [], 0
            buf.append(t)
            buf_tok += tok
        if buf:
            parts.append("\n\n".join(buf))

        summaries = []
        for chunk in parts:
            try:
                out = self._post_json(
                    "/v1/chat/completions",
                    {
                        "model": self.valves.summarizer_model,
                        "messages": [
                            {"role": "system", "content": sys_p},
                            {"role": "user", "content": chunk},
                        ],
                        "max_tokens": 900,
                        "temperature": 0.2,
                        "stream": False,
                    },
                    180,
                )
                text = (out["choices"][0]["message"].get("content") or "").strip()
                if not text:
                    return None  # пустое резюме = считаем сжатие несостоявшимся
                summaries.append(text)
            except Exception:
                return None  # сигнал: суммаризация не удалась
        if not summaries:
            return None
        return " • ".join(summaries)

    def _total_tokens(self, msgs, model):
        return sum(
            self._count_tokens(m.get("content"), model)
            for m in msgs
            if isinstance(m.get("content"), str)
        )

    # ------------------------------------------------ inlet
    def inlet(self, body, __user__=None):
        if not self.valves.enabled:
            return body
        msgs = body.get("messages") or []
        if len(msgs) < 3:
            return body
        model = body.get("model") or "qwen3.5-9b-instruct"
        budget = self._budget(model)
        single = self._single_limit(model)

        # 1) одиночные гигантские сообщения → усечение середины
        for m in msgs:
            c = m.get("content")
            if isinstance(c, str) and m.get("role") in ("user", "assistant"):
                if self._count_tokens(c, model) > single:
                    m["content"] = self._truncate_middle(c, single, model)

        # 2) суммарный бюджет
        if self._total_tokens(msgs, model) <= budget:
            return body

        # 3) сжатие старых сообщений в резюме (system сохраняем)
        keep = self.valves.keep_recent
        for _attempt in range(3):
            split = max(1, len(msgs) - keep)
            old, recent = msgs[:split], msgs[split:]
            system_msgs = [m for m in old if m.get("role") == "system"]
            dialog = [m for m in old if m.get("role") != "system"]
            if not dialog:
                break
            summary = self._summarize_msgs(dialog, model)
            if summary is None:
                # fallback: отбрасываем старую историю, помечаем
                note = {
                    "role": "system",
                    "content": f"[Предыдущая история из {len(dialog)} сообщений опущена компрессором контекста]",
                }
                body["messages"] = system_msgs + [note] + recent
                return body
            note = {
                "role": "system",
                "content": f"Резюме предыдущего диалога (сжато компрессором):\n{summary}",
            }
            msgs = system_msgs + [note] + recent
            if self._total_tokens(msgs, model) <= budget:
                break
            keep = max(2, keep - 1)  # ещё агрессивнее на следующем проходе

        # 4) последняя гарантия: режем резюме, если и оно велико
        if self._total_tokens(msgs, model) > budget:
            for m in msgs:
                c = m.get("content")
                if isinstance(c, str) and m["role"] == "system" and c.startswith("Резюме"):
                    m["content"] = self._truncate_middle(c, budget // 2, model)
        body["messages"] = msgs
        return body
