#!/usr/bin/env python3
"""FLUX через ComfyUI API (:8188): txt2img с GGUF-моделями.

Клиент для swarm_tools.image_generate (ComfyUI-ветка) и приёмочных тестов.
Зависимость: requests (есть в venv AiStart и venv ComfyUI).

Сетевая безопасность: все URL — литеральные константы на локальный ComfyUI
(только http://127.0.0.1:8188); серверные значения (prompt_id, имена файлов)
передаются параметрами запроса, а не конкатенацией в URL.

Модели (симлинки в ComfyUI/models/):
  unet: AI-Models/flux/flux1-schnell-Q4_K_S.gguf (dev-Q5_K_S — при появлении HF-токена)
  clip: t5-v1_1-xxl-encoder-Q5_K_M.gguf + clip_l.safetensors
  vae:  ae.safetensors (fp16)
"""
import json
import secrets
import time
from pathlib import Path

import requests

BASE = 'http://127.0.0.1:8188'
UNET_DIR = '/home/rover/AI-Combine/ComfyUI/models/unet'
DEFAULT_UNET = 'flux1-schnell-Q4_K_S.gguf'
DEFAULT_OUT_DIR = '/home/rover/Pictures/swarm'
_POLL = 2.0


def server_alive() -> bool:
    try:
        requests.get('http://127.0.0.1:8188/system_stats', timeout=5)
        return True
    except Exception:
        return False


def build_graph(prompt: str, width: int, height: int, steps: int,
                seed: int, unet: str = DEFAULT_UNET, guidance: float = 3.5) -> dict:
    """Граф FLUX txt2img: GGUF-unet + DualCLIPLoaderGGUF(t5+clip_l) + VAE + KSampler."""
    return {
        '1': {'class_type': 'UnetLoaderGGUF', 'inputs': {'unet_name': unet}},
        '2': {'class_type': 'DualCLIPLoaderGGUF', 'inputs': {
            'clip_name1': 't5-v1_1-xxl-encoder-Q5_K_M.gguf',
            'clip_name2': 'clip_l.safetensors', 'type': 'flux'}},
        '3': {'class_type': 'VAELoader', 'inputs': {'vae_name': 'ae.safetensors'}},
        '4': {'class_type': 'CLIPTextEncode', 'inputs': {'text': prompt, 'clip': ['2', 0]}},
        '5': {'class_type': 'FluxGuidance', 'inputs': {'conditioning': ['4', 0], 'guidance': guidance}},
        '6': {'class_type': 'CLIPTextEncode', 'inputs': {'text': '', 'clip': ['2', 0]}},
        '7': {'class_type': 'EmptySD3LatentImage', 'inputs': {
            'width': width, 'height': height, 'batch_size': 1}},
        '8': {'class_type': 'KSampler', 'inputs': {
            'seed': seed, 'steps': steps, 'cfg': 1.0, 'sampler_name': 'euler',
            'scheduler': 'simple', 'denoise': 1.0, 'model': ['1', 0],
            'positive': ['5', 0], 'negative': ['6', 0], 'latent_image': ['7', 0]}},
        '9': {'class_type': 'VAEDecode', 'inputs': {'samples': ['8', 0], 'vae': ['3', 0]}},
        '10': {'class_type': 'SaveImage', 'inputs': {'images': ['9', 0], 'filename_prefix': 'flux'}},
    }


def flux_generate(prompt: str, output: str = '', width: int = 1024, height: int = 1024,
                  steps: int = 4, seed: int | None = None, unet: str = DEFAULT_UNET,
                  poll_timeout: int = 900) -> str:
    """Генерация. Возвращает '✅ картинка сохранена: <путь>' или '⛔ ...' с причиной."""
    from datetime import datetime
    if seed is None:
        seed = secrets.randbits(48)
    w = max(128, min(int(width or 1024), 2048))
    h = max(128, min(int(height or 1024), 2048))
    w -= w % 8
    h -= h % 8
    steps = max(1, min(int(steps or 4), 512))
    try:
        r = requests.post('http://127.0.0.1:8188/prompt', timeout=30,
                          json={'prompt': build_graph(prompt, w, h, steps, seed, unet)})
        r.raise_for_status()
        q = r.json()
    except requests.HTTPError as e:
        return '⛔ ComfyUI /prompt: HTTP ' + str(e.response.status_code) + ' ' + e.response.text[:300]
    except Exception as e:
        return '⛔ ComfyUI недоступен (' + BASE + '): ' + str(e)
    pid = q.get('prompt_id') or ''
    if not pid:
        return '⛔ ComfyUI не вернул prompt_id: ' + json.dumps(q)[:300]
    t0 = time.time()
    while time.time() - t0 < poll_timeout:
        time.sleep(_POLL)
        try:
            r = requests.get('http://127.0.0.1:8188/history', timeout=30)
            hist = r.json().get(pid) or {}
        except Exception:
            continue
        if not hist:
            continue
        if hist.get('status', {}).get('status_str') == 'error':
            return '⛔ ComfyUI execution error: ' + json.dumps(hist['status'].get('messages', []))[:400]
        for node_out in hist.get('outputs', {}).values():
            for img in node_out.get('images', []):
                r = requests.get('http://127.0.0.1:8188/view', timeout=120, params={
                    'filename': img['filename'], 'subfolder': img.get('subfolder', ''),
                    'type': img.get('type', 'output')})
                out_dir = Path(output).expanduser().parent if output else Path(DEFAULT_OUT_DIR)
                out_dir.mkdir(parents=True, exist_ok=True)
                out = Path(output).expanduser() if output else out_dir / (
                    datetime.now().strftime('%Y%m%d-%H%M%S') + '.png')
                out.write_bytes(r.content)
                return '✅ картинка сохранена: ' + str(out)
        # ещё нет картинок — ждём дальше
    return '⛔ ComfyUI: таймаут ожидания ' + str(poll_timeout) + ' с'


def available_unets() -> list:
    return sorted(p.name for p in Path(UNET_DIR).glob('*.gguf'))


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='FLUX txt2img через ComfyUI (:8188)')
    ap.add_argument('prompt')
    ap.add_argument('--out', default='')
    ap.add_argument('--width', type=int, default=1024)
    ap.add_argument('--height', type=int, default=1024)
    ap.add_argument('--steps', type=int, default=4)
    ap.add_argument('--seed', type=int, default=None)
    ap.add_argument('--unet', default=DEFAULT_UNET)
    args = ap.parse_args()
    print(flux_generate(args.prompt, args.out, args.width, args.height, args.steps,
                        args.seed, args.unet))