#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""swarm_tools.py — инструменты роя агентов (сгенерировано из SwarmConfig/mcp_local.json).

Правьте mcp_local.json и перезапускайте apply_swarm.sh apply tools;
ручные правки этого файла затрутся при следующем apply.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import subprocess
import tempfile
import time
import urllib.parse
from datetime import date
from pathlib import Path

import requests

import sys as _sys
_main = _sys.modules.get('__main__')
mcp = getattr(_main, 'mcp', None) or globals().get('_mcp_ref')
if mcp is None:
    raise RuntimeError('swarm_tools: не найден общий MCPServer (запускайте через mcp_server.py)')

AISTART = Path('/home/rover/AiStart')
SWARM_CFG = Path('/home/rover/SwarmConfig')
GLM_KEY_FILE = AISTART / "PolzaKey>Z.ai"
GLM_USAGE = SWARM_CFG / "state" / "glm_usage.json"
GLM_ENDPOINT = "https://api.polza.ai/v1/chat/completions"
GLM_MODEL = "z-ai/glm-4.6"
GLM_DAILY_LIMIT = 20
DDG_URL = "https://duckduckgo.com/html/"
WEB_UA = "Mozilla/5.0 (X11; Linux x86_64) swarm/1.0"
MAX_WEB_BYTES = 5000000
DDG_LAST = {'ts': 0.0}


def _assert_public_url(url: str) -> None:
    """Только http(s) и только публичные хосты (анти-SSRF, редиректы проверяем вручную)."""
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ('http', 'https'):
        raise ValueError('запрещённая схема: ' + str(p.scheme))
    host = p.hostname or ''
    if not host:
        raise ValueError('нет хоста')
    port = p.port or (443 if p.scheme == 'https' else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise ValueError('хост не резолвится: ' + host) from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise ValueError('запрещённый адрес (не публичный): ' + host + ' -> ' + str(ip))


def _strip_tags(html: str) -> str:
    html = re.sub(r'<(script|style|nav|footer|header|aside)[^>]*>.*?</\\1>', ' ', html, flags=re.S | re.I)
    html = re.sub(r'<br[^>]*>|</p>|</div>|</li>|</h[1-6]>', chr(10), html, flags=re.I)
    text = re.sub(r'<[^>]+>', ' ', html)
    for a, b in (('&nbsp;', ' '), ('&amp;', '&'), ('&quot;', chr(34)),
                 ('&lt;', '<'), ('&gt;', '>'), ('&#x27;', chr(39))):
        text = text.replace(a, b)
    text = re.sub(r'[ ' + chr(92) + 't]+', ' ', text)
    text = re.sub(chr(10) + r'[ ]*' + chr(10) + r'+', chr(10) + chr(10), text)
    return text.strip()


def _glm_budget() -> tuple:
    try:
        st = json.loads(GLM_USAGE.read_text())
    except Exception:
        st = {'calls_today': 0, 'date': '', 'total': 0}
    today = date.today().isoformat()
    if st.get('date') != today:
        st = {'calls_today': 0, 'date': today, 'total': st.get('total', 0)}
    if st['calls_today'] >= GLM_DAILY_LIMIT:
        return False, 'дневной лимит ' + str(GLM_DAILY_LIMIT) + ' вызовов GLM исчерпан'
    st['calls_today'] += 1
    st['total'] += 1
    GLM_USAGE.parent.mkdir(parents=True, exist_ok=True)
    GLM_USAGE.write_text(json.dumps(st, ensure_ascii=False))
    return True, 'вызовов сегодня: ' + str(st['calls_today']) + '/' + str(GLM_DAILY_LIMIT)


def _glm_key() -> str:
    # Приоритет: ~/AiStart/ZaiKey (GLM 5.3, z.ai start-plan), затем старый polza-ключ.
    zai_key_file = AISTART / 'ZaiKey'
    f = zai_key_file if zai_key_file.is_file() else GLM_KEY_FILE
    lines = f.read_text(encoding='utf-8').strip().splitlines()
    key = lines[0].strip() if lines else ''
    if not key:
        raise ValueError('ключ GLM пуст (' + f.name + ')')
    return key


def _glm_endpoint_and_model():
    """Endpoint и модель: если есть ZaiKey — z.ai coding-plan, иначе polza (legacy)."""
    if (AISTART / 'ZaiKey').is_file():
        return ('https://api.z.ai/api/coding/paas/v4/chat/completions', 'glm-5.3')
    return (GLM_ENDPOINT, GLM_MODEL)


def _glm_call(messages, max_tokens):
    ok, note = _glm_budget()
    if not ok:
        return '⛔ GLM: ' + note
    endpoint, model = _glm_endpoint_and_model()
    r = requests.post(
        endpoint,
        headers={'Authorization': 'Bearer ' + _glm_key(), 'Content-Type': 'application/json'},
        json={'model': model, 'messages': messages, 'max_tokens': max_tokens, 'stream': False},
        timeout=180,
    )
    r.raise_for_status()
    return r.json()['choices'][0]['message']['content']


@mcp.tool()
def web_search(query: str, limit: int = 5) -> str:
    """Поиск в интернете через DuckDuckGo (локально, без API-ключей).

    Возвращает заголовки, URL и сниппеты. Для чтения страницы используйте web_fetch.

    Args:
        query: строка поиска
        limit: сколько результатов (1-10)
    """
    limit = max(1, min(int(limit or 5), 10))
    wait = 2.0 - (time.time() - DDG_LAST['ts'])
    if wait > 0:
        time.sleep(wait)
    DDG_LAST['ts'] = time.time()
    try:
        r = requests.get(DDG_URL, params={'q': query}, timeout=20,
                         headers={'User-Agent': WEB_UA})
        r.raise_for_status()
    except Exception as e:
        if 'timed out' in str(e):
            return '⏱ DDG не ответил (таймаут) — повторите позже или переформулируйте.'
        return '⛔ web_search: ' + str(e)
    html = r.text
    out = []
    pat = re.compile(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>')
    snip = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.S)
    titles = pat.finditer(html)
    snips = [m.group(1) for m in snip.finditer(html)]
    idx = 0
    for m in titles:
        url = m.group(1)
        title = _strip_tags(m.group(2)).strip()
        s = snips[idx] if idx < len(snips) else ''
        idx += 1
        uq = re.search(r'uddg=([^&]+)', url)
        if uq:
            url = urllib.parse.unquote(uq.group(1))
        out.append({'url': url, 'title': title, 'snippet': _strip_tags(s).strip()})
    results = out[:limit]
    if not results:
        return 'Ничего не найдено (или DDG показал капчу — повторите позже).'
    lines = ['Результатов: ' + str(len(results))]  
    for i, x in enumerate(results, 1):
        lines.append(str(i) + '. ' + x['title'])
        lines.append('   ' + x['url'])
        lines.append('   ' + x['snippet'][:200])
        lines.append('   (прочитать страницу: web_fetch url=' + chr(34) + x['url'] + chr(34) + ')')
    return chr(10).join(lines)


@mcp.tool()
def web_fetch(url: str, max_chars: int = 6000) -> str:
    """Скачать страницу и вернуть чистый текст (без скриптов и навигации).

    Args:
        url: http(s):// адрес (только публичные сайты)
        max_chars: лимит вывода (макс 20000)
    """
    max_chars = max(200, min(int(max_chars or 6000), 20000))
    try:
        _assert_public_url(url)
    except ValueError as e:
        return '⛔ web_fetch: ' + str(e)
    current = url
    data = None
    try:
        for _hop in range(3):
            r = requests.get(current, timeout=20, stream=True, allow_redirects=False,
                             headers={'User-Agent': WEB_UA})
            if r.is_redirect or r.is_permanent_redirect:
                loc = r.headers.get('Location', '')
                if not loc:
                    return '⛔ web_fetch: пустой редирект'
                if loc.startswith('/'):
                    pp = urllib.parse.urlsplit(current)
                    loc = pp.scheme + '://' + pp.netloc + loc
                _assert_public_url(loc)
                current = loc
                continue
            r.raise_for_status()
            data = b''
            for chunk in r.iter_content(65536):
                data += chunk
                if len(data) > MAX_WEB_BYTES:
                    break
            break
        if data is None:
            return '⛔ web_fetch: слишком много редиректов'
    except ValueError as e:
        return '⛔ web_fetch: ' + str(e)
    except Exception as e:
        return '⛔ web_fetch: ' + str(e)
    text = _strip_tags(data.decode('utf-8', 'replace'))
    return text[:max_chars]


def _glm_json_review(task: str, draft: str, strict: bool) -> str:
    sysmsg = ('Ты — строгий контролёр качества. Проверь черновой ответ другого ИИ-агента '
              'на соответствие задаче. Ответ верни СТРОГО в формате JSON: '
              + chr(123) + chr(34) + 'verdict' + chr(34) + ':' + chr(34)
              + 'ok|issues|reject' + chr(34) + ',' + chr(34) + 'проблемы' + chr(34)
              + ':[...],' + chr(34) + 'исправления' + chr(34) + ':' + chr(34) + '...' + chr(34) + chr(125))
    if not strict:
        sysmsg = sysmsg + ' Ищи только грубые ошибки.'
    messages = [
        {'role': 'system', 'content': sysmsg},
        {'role': 'user', 'content': 'ЗАДАЧА:' + chr(10) + task + chr(10) + chr(10) + 'ЧЕРНОВИК:' + chr(10) + draft[:12000]},
    ]
    return _glm_call(messages, 1500)


@mcp.tool()
def glm_think(question: str, system: str = '', max_tokens: int = 2000) -> str:
    """ГЛУБОКОЕ РАЗМЫШЛЕНИЕ через облачный GLM (z.ai).

    Использовать ТОЛЬКО для самых сложных задач: стратегия, сложный код-ревью,
    глубокий анализ — когда локальной модели не хватает. НЕ для рутин!
    Бюджет: не более 20 вызовов в день (счётчик в SwarmConfig/state).

    Args:
        question: полный вопрос со сжатым контекстом
        system: опциональная системная инструкция
        max_tokens: лимит ответа (по умолчанию 2000, максимум 8000)
    """
    try:
        messages = ([{'role': 'system', 'content': system}] if system else [])
        messages = messages + [{'role': 'user', 'content': question}]
        answer = _glm_call(messages, max(200, min(int(max_tokens or 2000), 8000)))
        return '🧠 GLM (облако) отвечает:' + chr(10) + chr(10) + answer
    except requests.HTTPError as e:
        code = getattr(e.response, 'status_code', '?')
        return '⛔ glm_think: HTTP ' + str(code) + ' (проверьте ключ/лимиты z.ai)'
    except Exception as e:
        return '⛔ glm_think: ' + str(e)


@mcp.tool()
def glm_review(task: str, draft: str, strict: bool = True) -> str:
    """ПРОВЕРКА работы локального агента через GLM: вердикт + исправления.

    Обязательна для кода и системных изменений (если пользователь не отключил).

    Args:
        task: исходная задача
        draft: ответ/код на проверку (до 12000 символов)
        strict: True — искать все проблемы; False — только грубые
    """
    try:
        return _glm_json_review(task, draft, strict)
    except requests.HTTPError as e:
        code = getattr(e.response, 'status_code', '?')
        return '⛔ glm_review: HTTP ' + str(code)
    except Exception as e:
        return '⛔ glm_review: ' + str(e)


GIGA_PY = '/home/rover/giga_speech/.venv/bin/python'
GIGA_DIR = '/home/rover/giga_speech'
GIGA_SCRIPT = GIGA_DIR + '/stt_model.py'


@mcp.tool()
def stt_transcribe(path: str, clean: bool = False) -> str:
    """Транскрибация аудиофайла локально (GigaAM-v3, русский).

    При первом запуске скачивает веса модели (~1-2 ГБ). Перед запуском
    агент должен освободить VRAM: ai_unload all.

    Args:
        path: путь к файлу wav/mp3/ogg/m4a
        clean: True — дополнительно вычистить текст (медленнее)
    """
    p = Path(path).expanduser()
    if not p.exists():
        return '⛔ файл не найден: ' + path
    try:
        import os as _os
        env = dict(_os.environ)
        env.pop('LD_LIBRARY_PATH', None)  # AppImage-библиотеки ZCode ломают torch в giga-venv
        r = subprocess.run([GIGA_PY, GIGA_SCRIPT, str(p)], env=env,
                           capture_output=True, text=True, timeout=1800, cwd=GIGA_DIR)
        out = (r.stdout or '').strip()
        if r.returncode != 0:
            return '⛔ stt: ' + (r.stderr or out)[-500:]
        if clean:
            try:
                from mcp_server import _chat_once
                from mcp_server import _agents_cfg
                role = _agents_cfg()['roles']['medium']
                msg = _chat_once(role, [{'role': 'user', 'content': 'Вычисти текст от паразитов и заиканий, сохрани смысл, верни только текст:' + chr(10) + out[-6000:]}], use_tools=False)
                cleaned = (msg.get('content') or '').strip()
                if cleaned:
                    out = out + chr(10) + chr(10) + '--- вычищенная версия ---' + chr(10) + cleaned
            except Exception:
                pass  # fallback: сырая транскрипция
        return out
    except subprocess.TimeoutExpired:
        return '⛔ stt: таймаут 30 минут'
    except Exception as e:
        return '⛔ stt: ' + str(e)


@mcp.tool()
def stt_listen(seconds: int = 10, device: int = -1) -> str:
    """Записать микрофон N секунд и вернуть текст (голосовая команда).

    Args:
        seconds: длительность записи 1-60
        device: номер аудиовхода (-1 = авто)
    """
    seconds = max(1, min(int(seconds or 10), 60))
    rec_code_lines = [
        'import sounddevice as sd',
        'import scipy.io.wavfile as wav',
        'import tempfile, os',
        'sr = 16000',
        'frames = int(sr * ' + str(seconds) + ')',
        "kw = {'samplerate': sr, 'channels': 1}",
        ('if ' + str(device) + ' >= 0:'),
        ('    kw[' + chr(39) + 'device' + chr(39) + '] = ' + str(device)),
        'data = sd.rec(frames, **kw)',
        'sd.wait()',
        "fd, path = tempfile.mkstemp(suffix='.wav')",
        'os.close(fd)',
        'wav.write(path, sr, data)',
        'print(path)',
    ]
    try:
        r = subprocess.run([GIGA_PY, '-c', chr(10).join(rec_code_lines)],
                           capture_output=True, text=True, timeout=seconds + 30)
        wav_out = (r.stdout or '').strip().splitlines()[-1] if r.stdout else ''
        if not wav_out.endswith('.wav'):
            return '⛔ запись не удалась: ' + (r.stderr or '')[-300:]
        try:
            return stt_transcribe(wav_out, clean=False)
        finally:
            try:
                os.unlink(wav_out)
            except OSError:
                pass
    except Exception as e:
        return '⛔ stt_listen: ' + str(e)



# ---------- графические агенты (VL + рисование + GLM-4.6V) ----------

VL_MODEL = 'nvidia-nemotron-12b-vision'
ROUTER_URL = 'http://127.0.0.1:8081/v1/chat/completions'
SD_CLI = '/home/rover/sd.cpp/build/bin/sd-cli'
SD_DIR = '/home/rover/AI-Models/flux'
GLM_VISION_MODEL = 'z-ai/glm-4.6v'
IMG_OUT_DIR = '/home/rover/Pictures/swarm'


def _post_fixed(url: str, payload: dict, headers: dict, timeout: int) -> dict:
    """POST строго на разрешённый URL (allowlist) — защита от SSRF.

    Разрешены: единственный loopback-эндпоинт роутера и https-домены
    GLM-облака из фиксированного списка. Редиректы запрещены, реквизиты
    в URL запрещены.
    """
    parsed = urllib.parse.urlsplit(url)
    allowed_hosts = {'api.z.ai', 'api.polza.ai'}
    ok = (
        (parsed.scheme == 'http' and parsed.netloc == '127.0.0.1:8081'
         and parsed.path == '/v1/chat/completions')
        or (parsed.scheme == 'https'
            and parsed.hostname in allowed_hosts
            and not parsed.username and not parsed.password)
    )
    if not ok:
        raise ValueError('запрещённый URL: ' + url)
    r = requests.post(url, json=payload, headers=headers, timeout=timeout,
                      allow_redirects=False)
    r.raise_for_status()
    return r.json()


@mcp.tool()
def image_describe(path: str, question: str = 'Подробно опиши изображение') -> str:
    """Распознавание картинки локальной VL-моделью NVIDIA-Nemotron-12B-v2 (роутер :8081).

    Читает текст на картинке, описывает сцены, объекты, цвета, скриншоты.

    Args:
        path: путь к файлу png/jpg/webp
        question: что спросить о картинке
    """
    p = Path(path).expanduser()
    if not p.exists():
        return '⛔ файл не найден: ' + path
    import base64
    b64 = base64.b64encode(p.read_bytes()).decode()
    ext = p.suffix.lower().lstrip('.') or 'png'
    try:
        data = _post_fixed(ROUTER_URL, {'model': VL_MODEL, 'max_tokens': 600, 'messages': [{'role': 'user', 'content': [
            {'type': 'text', 'text': question},
            {'type': 'image_url', 'image_url': {'url': 'data:image/' + ext + ';base64,' + b64}},
        ]}]}, {}, 180)
        return data['choices'][0]['message']['content']
    except Exception as e:
        return '⛔ image_describe: ' + str(e)


@mcp.tool()
def image_generate(prompt: str, output: str = '', width: int = 512, height: int = 512, steps: int = 4) -> str:
    """Нарисовать картинку ЛОКАЛЬНО: FLUX.1-schnell через stable-diffusion.cpp.

    Сам освобождает VRAM: если модели роутера заняли память — выгружает их;
    модель агента WebUI вернётся автоматически на следующем шаге цикла.

    Args:
        prompt: описание картинки (лучше на английском)
        output: путь сохранения (по умолчанию /home/rover/Pictures/swarm/<штамп>.png)
        width: ширина (кратно 8, по умолчанию 512)
        height: высота (по умолчанию 512)
        steps: шаги FLUX-schnell (4 достаточно)
    """
    from datetime import datetime
    out_dir = Path(IMG_OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = output or str(out_dir / (datetime.now().strftime('%Y%m%d-%H%M%S') + '.png'))
    op = Path(out).expanduser()
    try:
        free = subprocess.run(['nvidia-smi', '--query-gpu=memory.free', '--format=csv,noheader,nounits'],
                              capture_output=True, text=True, timeout=10)
        free_mb = int(free.stdout.strip().splitlines()[0])
    except Exception:
        free_mb = -1
    if free_mb != -1 and free_mb < 5000:
        # инструмент сам освобождает VRAM: выгружаем загруженные модели роутера;
        # модель агента WebUI роутер вернёт автоматически на следующем шаге цикла
        n = 0
        try:
            r = requests.get('http://127.0.0.1:8081/v1/models', timeout=10)
            models = r.json().get('data', [])
        except Exception:
            models = []
        for m in models:
            if (m.get('status') or {}).get('value') == 'loaded':
                try:
                    requests.post('http://127.0.0.1:8081/models/unload',
                                  json={'model': m.get('id')}, timeout=60)
                    n += 1
                except Exception:
                    pass
        for _ in range(10):
            time.sleep(1.5)
            try:
                free = subprocess.run(['nvidia-smi', '--query-gpu=memory.free',
                                       '--format=csv,noheader,nounits'],
                                      capture_output=True, text=True, timeout=10)
                free_mb = int(free.stdout.strip().splitlines()[0])
            except Exception:
                break
            if free_mb == -1 or free_mb >= 5000:
                break
        unloaded = f' (авто-выгружено моделей: {n})'
    else:
        unloaded = ''
    cmd = [SD_CLI,
        '--diffusion-model', SD_DIR + '/flux1-schnell-Q4_K_S.gguf',
        '--clip_l', SD_DIR + '/clip_l.safetensors',
        '--t5xxl', SD_DIR + '/t5-v1_1-xxl-encoder-Q5_K_M.gguf',
        '--vae', SD_DIR + '/ae.safetensors',
        '--prompt', prompt, '--cfg-scale', '1.0',
        '--steps', str(max(1, min(int(steps or 4), 8))),
        '--width', str(max(128, min(int(width or 512), 1024))),
        '--height', str(max(128, min(int(height or 512), 1024))),
        '-v', '-o', str(op)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, cwd=SD_DIR)
        if op.exists() and op.stat().st_size > 10000:
            return '✅ картинка сохранена: ' + str(op) + unloaded
        err = (r.stderr or r.stdout or '')[-400:]
        return '⛔ image_generate: ' + err
    except subprocess.TimeoutExpired:
        return '⛔ image_generate: таймаут 30 минут'
    except Exception as e:
        return '⛔ image_generate: ' + str(e)


def _glm_vision_call(image_path: str, question: str) -> str:
    import base64
    import urllib.parse
    p = Path(image_path).expanduser()
    if not p.exists():
        return '⛔ файл не найден: ' + image_path
    b64 = base64.b64encode(p.read_bytes()).decode()
    endpoint, model = _glm_endpoint_and_model()
    vision_model = 'glm-4.6v' if 'z.ai' in endpoint else GLM_VISION_MODEL
    data = _post_fixed(endpoint, {'model': vision_model, 'max_tokens': 1500, 'messages': [{'role': 'user', 'content': [
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + b64}},
        {'type': 'text', 'text': question},
    ]}]}, {'Authorization': 'Bearer ' + _glm_key(), 'Content-Type': 'application/json'}, 180)
    return data['choices'][0]['message']['content']


@mcp.tool()
def glm_vision(path: str, question: str) -> str:
    """Глубокий анализ картинки облачным GLM-4.6V (z.ai): схемы, рукописи, мелкий текст.

    ТОЛЬКО если локальная VL не справилась или нужен глубокий разбор.
    Бюджет общий с glm_think (20 вызовов/день).

    Args:
        path: путь к картинке
        question: что разобрать на картинке
    """
    try:
        ok, note = _glm_budget()
        if not ok:
            return '⛔ GLM: ' + note
        answer = _glm_vision_call(path, question)
        return '🧠 GLM-4.6V (облако):' + chr(10) + chr(10) + answer
    except requests.HTTPError as e:
        return '⛔ glm_vision: HTTP ' + str(getattr(e.response, 'status_code', '?'))
    except Exception as e:
        return '⛔ glm_vision: ' + str(e)



CLOUD_IMG_MODEL = 'qwen/image-2.1'
IMG_EXT_OK = ('.png', '.jpg', '.jpeg', '.webp')


@mcp.tool()
def image_generate_cloud(prompt: str, output: str = '') -> str:
    """Рисование через облако polza (qwen/image-2.1) — резерв, если локальный flux недоступен.

    По умолчанию НЕ использовать: локальное рисование быстрее и бесплатнее.
    Требует баланс polza (иначе вернёт ошибку 402).

    Args:
        prompt: описание картинки
        output: путь сохранения (по умолчанию /home/rover/Pictures/swarm/<штамп>.png)
    """
    from datetime import datetime
    out_dir = Path(IMG_OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = output or str(out_dir / ('cloud_' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.png'))
    op = Path(out).expanduser()
    try:
        ok, note = _glm_budget()
        if not ok:
            return '⛔ GLM: ' + note
        data = _post_fixed(GLM_ENDPOINT, {'model': CLOUD_IMG_MODEL, 'messages': [{'role': 'user', 'content': prompt}]}, {'Authorization': 'Bearer ' + _glm_key(), 'Content-Type': 'application/json'}, 180)
        # image-модели отдают base64 в choices[0].message.content или images[]
        msg = data['choices'][0]['message']
        b64 = None
        if isinstance(msg.get('content'), list):
            for part in msg['content']:
                if part.get('type') in ('image_url', 'image'):  
                    u = (part.get('image_url') or {}).get('url', '')
                    if 'base64,' in u:
                        b64 = u.split('base64,', 1)[1]
        elif isinstance(msg.get('images'), list) and msg['images']:
            u = msg['images'][0]
            b64 = u.split('base64,', 1)[1] if 'base64,' in u else u
        if not b64:
            return '⛔ image_generate_cloud: формат ответа без картинки: ' + json.dumps(data)[:200]
        import base64 as _b64
        op.write_bytes(_b64.b64decode(b64))
        return '✅ облачная картинка сохранена: ' + str(op)
    except requests.HTTPError as e:
        return '⛔ image_generate_cloud: HTTP ' + str(getattr(e.response, 'status_code', '?')) + ' (проверьте баланс polza)'
    except Exception as e:
        return '⛔ image_generate_cloud: ' + str(e)


print('swarm_tools загружен: web_search, web_fetch, glm_think, glm_review, stt_transcribe, stt_listen, image_describe, image_generate, glm_vision, image_generate_cloud')

