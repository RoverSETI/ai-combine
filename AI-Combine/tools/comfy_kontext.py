#!/usr/bin/env python3
"""FLUX.1-Kontext-dev через ComfyUI API (:8188): редактирование фото текстом.

«Сделай снег белым», «убери розовый оттенок», «замени небо на ясное» —
инструкция по картинке. Модель: flux1-kontext-dev-Q5_K_M.gguf (QuantStack).

Граф (официальная схема Kontext):
  LoadImage → FluxKontextImageScale → VAEEncode → CLIPTextEncode (инструкция)
  → ReferenceLatent → FluxGuidance 2.5 → KSampler (denoise 1.0, latent исходный)
  → VAEDecodeTiled → SaveImage

Сетевая безопасность: URL — строковые литералы в самих вызовах (только
локальный ComfyUI 127.0.0.1:8188), параметры запросов — kwargs, seed через
secrets. Файл уходит multipart-формой в /upload/image этого же сервера.
"""
import json
import secrets
import time
from pathlib import Path

import requests

UNET = 'flux1-kontext-dev-Q5_K_M.gguf'
DEFAULT_OUT_DIR = '/home/rover/Pictures/swarm'
_POLL = 2.0


def build_graph(instruction: str, image_name: str, steps: int, seed: int,
                guidance: float = 2.5) -> dict:
    return {
        '1': {'class_type': 'UnetLoaderGGUF', 'inputs': {'unet_name': UNET}},
        '2': {'class_type': 'DualCLIPLoaderGGUF', 'inputs': {
            'clip_name1': 't5-v1_1-xxl-encoder-Q5_K_M.gguf',
            'clip_name2': 'clip_l.safetensors', 'type': 'flux'}},
        '3': {'class_type': 'VAELoader', 'inputs': {'vae_name': 'ae.safetensors'}},
        '4': {'class_type': 'LoadImage', 'inputs': {'image': image_name}},
        '5': {'class_type': 'FluxKontextImageScale', 'inputs': {'image': ['4', 0]}},
        '6': {'class_type': 'VAEEncode', 'inputs': {'pixels': ['5', 0], 'vae': ['3', 0]}},
        '7': {'class_type': 'CLIPTextEncode', 'inputs': {'text': instruction, 'clip': ['2', 0]}},
        '8': {'class_type': 'ReferenceLatent', 'inputs': {'conditioning': ['7', 0], 'latent': ['6', 0]}},
        '9': {'class_type': 'FluxGuidance', 'inputs': {'conditioning': ['8', 0], 'guidance': guidance}},
        '10': {'class_type': 'CLIPTextEncode', 'inputs': {'text': '', 'clip': ['2', 0]}},
        '11': {'class_type': 'KSampler', 'inputs': {
            'seed': seed, 'steps': steps, 'cfg': 1.0, 'sampler_name': 'euler',
            'scheduler': 'simple', 'denoise': 1.0, 'model': ['1', 0],
            'positive': ['9', 0], 'negative': ['10', 0], 'latent_image': ['6', 0]}},
        '12': {'class_type': 'VAEDecodeTiled', 'inputs': {
            'samples': ['11', 0], 'vae': ['3', 0], 'tile_size': 512,
            'overlap': 64, 'temporal_size': 64, 'temporal_overlap': 8}},
        '13': {'class_type': 'SaveImage', 'inputs': {'images': ['12', 0], 'filename_prefix': 'kontext'}},
    }


def kontext_edit(image_path: str, instruction: str, output: str = '',
                 steps: int = 20, seed: int | None = None,
                 poll_timeout: int = 900) -> str:
    """Загружает картинку, применяет инструкцию, сохраняет результат."""
    from datetime import datetime
    src = Path(image_path).expanduser()
    if not src.is_file():
        return '⛔ файл не найден: ' + str(src)
    if seed is None:
        seed = secrets.randbits(48)
    steps = max(4, min(int(steps or 20), 30))
    with src.open('rb') as f:
        r = requests.post('http://127.0.0.1:8188/upload/image', timeout=120,
                          files={'image': (src.name, f, 'application/octet-stream')})
    r.raise_for_status()
    up = r.json().get('name') or src.name
    try:
        r = requests.post('http://127.0.0.1:8188/prompt', timeout=30, json={
            'prompt': build_graph(instruction, up, steps, seed)})
        r.raise_for_status()
        q = r.json()
    except requests.HTTPError as e:
        return '⛔ Kontext /prompt: HTTP ' + str(e.response.status_code) + ' ' + e.response.text[:300]
    pid = q.get('prompt_id') or ''
    if not pid:
        return '⛔ не вернул prompt_id: ' + json.dumps(q)[:300]
    t0 = time.time()
    while time.time() - t0 < poll_timeout:
        time.sleep(_POLL)
        try:
            hist = requests.get('http://127.0.0.1:8188/history', timeout=30).json().get(pid) or {}
        except Exception:
            continue
        if not hist:
            continue
        if hist.get('status', {}).get('status_str') == 'error':
            return '⛔ Kontext error: ' + json.dumps(hist['status'].get('messages', []))[:400]
        for node_out in hist.get('outputs', {}).values():
            for img in node_out.get('images', []):
                r = requests.get('http://127.0.0.1:8188/view', timeout=120, params={
                    'filename': img['filename'], 'subfolder': img.get('subfolder', ''),
                    'type': img.get('type', 'output')})
                out_dir = Path(output).expanduser().parent if output else Path(DEFAULT_OUT_DIR)
                out_dir.mkdir(parents=True, exist_ok=True)
                out = Path(output).expanduser() if output else out_dir / (
                    'kontext-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.png')
                out.write_bytes(r.content)
                return '✅ картинка сохранена: ' + str(out)
    return '⛔ Kontext: таймаут ' + str(poll_timeout) + ' с'


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='FLUX Kontext: редактирование фото текстом')
    ap.add_argument('image')
    ap.add_argument('instruction', help='например: make the snow white and neutral, remove pink tint')
    ap.add_argument('--out', default='')
    ap.add_argument('--steps', type=int, default=20)
    ap.add_argument('--seed', type=int, default=None)
    a = ap.parse_args()
    print(kontext_edit(a.image, a.instruction, a.out, a.steps, a.seed))
