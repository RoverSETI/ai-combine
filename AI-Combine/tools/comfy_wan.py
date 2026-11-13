#!/usr/bin/env python3
"""Wan2.2 T2V через ComfyUI API (:8188): текст → видео (WEBP-анимация).

Двухстадийный сэмплинг: HighNoise-эксперт (шаги 0-10) → LowNoise (10-20).
Модели (физически AI-Models/video/wan/, симлинки в ComfyUI/models/):
  unet: Wan2.2-T2V-A14B-{High,Low}Noise-Q4_K_S.gguf
  clip: umt5-xxl-encoder-Q4_K_M.gguf (type=wan)
  vae:  Wan2.1_VAE.safetensors

Сетевая безопасность: все URL литеральные (http://127.0.0.1:8188), параметры
запросов передаются kwargs, seed через secrets — как в comfy_flux.py.
"""
import json
import secrets
import time
from pathlib import Path

import requests

UNET_HIGH = 'Wan2.2-T2V-A14B-HighNoise-Q4_K_S.gguf'
UNET_LOW = 'Wan2.2-T2V-A14B-LowNoise-Q4_K_S.gguf'
CLIP = 'umt5-xxl-encoder-Q4_K_M.gguf'
VAE = 'Wan2.1_VAE.safetensors'
DEFAULT_OUT_DIR = '/home/rover/Pictures/swarm'
_POLL = 3.0


def build_graph(prompt: str, width: int, height: int, length: int,
                steps: int, seed: int, fps: int = 16) -> dict:
    """Граф Wan2.2 T2V: два GGUF-эксперта, KSamplerAdvanced 0..mid..конец, SaveAnimatedWEBP."""
    mid = max(1, steps // 2)
    return {
        '1': {'class_type': 'UnetLoaderGGUF', 'inputs': {'unet_name': UNET_HIGH}},
        '2': {'class_type': 'UnetLoaderGGUF', 'inputs': {'unet_name': UNET_LOW}},
        '3': {'class_type': 'CLIPLoaderGGUF', 'inputs': {'clip_name': CLIP, 'type': 'wan'}},
        '4': {'class_type': 'VAELoader', 'inputs': {'vae_name': VAE}},
        '5': {'class_type': 'CLIPTextEncode', 'inputs': {'text': prompt, 'clip': ['3', 0]}},
        '6': {'class_type': 'CLIPTextEncode', 'inputs': {'text': '', 'clip': ['3', 0]}},
        '7': {'class_type': 'EmptyHunyuanLatentVideo', 'inputs': {
            'width': width, 'height': height, 'length': length, 'batch_size': 1}},
        '8': {'class_type': 'KSamplerAdvanced', 'inputs': {
            'model': ['1', 0], 'add_noise': 'enable', 'noise_seed': seed,
            'steps': steps, 'cfg': 1.0, 'sampler_name': 'euler', 'scheduler': 'simple',
            'positive': ['5', 0], 'negative': ['6', 0], 'latent_image': ['7', 0],
            'start_at_step': 0, 'end_at_step': mid, 'return_with_leftover_noise': 'enable'}},
        '9': {'class_type': 'KSamplerAdvanced', 'inputs': {
            'model': ['2', 0], 'add_noise': 'disable', 'noise_seed': seed,
            'steps': steps, 'cfg': 1.0, 'sampler_name': 'euler', 'scheduler': 'simple',
            'positive': ['5', 0], 'negative': ['6', 0], 'latent_image': ['8', 0],
            'start_at_step': mid, 'end_at_step': 10000, 'return_with_leftover_noise': 'disable'}},
        '10': {'class_type': 'VAEDecodeTiled', 'inputs': {
            'samples': ['9', 0], 'vae': ['4', 0], 'tile_size': 512,
            'overlap': 64, 'temporal_size': 64, 'temporal_overlap': 8}},
        '11': {'class_type': 'SaveAnimatedWEBP', 'inputs': {
            'images': ['10', 0], 'filename_prefix': 'wan22', 'fps': fps,
            'lossless': False, 'quality': 90, 'method': 'default'}},
    }


def wan_generate(prompt: str, output: str = '', width: int = 832, height: int = 480,
                 length: int = 81, steps: int = 20, seed: int | None = None,
                 fps: int = 16, poll_timeout: int = 3600) -> str:
    """Генерация видео. Возвращает '✅ видео сохранено: <путь>' или '⛔ ...'."""
    from datetime import datetime
    if seed is None:
        seed = secrets.randbits(48)
    length = max(9, ((int(length) + 3) // 4) * 4 + 1)  # 4n+1
    w = max(256, min(int(width or 832), 1280)); w -= w % 16
    h = max(256, min(int(height or 480), 720)); h -= h % 16
    steps = max(4, min(int(steps or 20), 40))
    try:
        r = requests.post('http://127.0.0.1:8188/prompt', timeout=30, json={
            'prompt': build_graph(prompt, w, h, length, steps, seed, fps)})
        r.raise_for_status()
        q = r.json()
    except requests.HTTPError as e:
        return '⛔ Wan /prompt: HTTP ' + str(e.response.status_code) + ' ' + e.response.text[:300]
    except Exception as e:
        return '⛔ ComfyUI недоступен: ' + str(e)
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
            return '⛔ Wan execution error: ' + json.dumps(hist['status'].get('messages', []))[:400]
        for node_out in hist.get('outputs', {}).values():
            for img in node_out.get('images', []):
                r = requests.get('http://127.0.0.1:8188/view', timeout=180, params={
                    'filename': img['filename'], 'subfolder': img.get('subfolder', ''),
                    'type': img.get('type', 'output')})
                out_dir = Path(output).expanduser().parent if output else Path(DEFAULT_OUT_DIR)
                out_dir.mkdir(parents=True, exist_ok=True)
                out = Path(output).expanduser() if output else out_dir / (
                    datetime.now().strftime('%Y%m%d-%H%M%S') + '.webp')
                out.write_bytes(r.content)
                return '✅ видео сохранено: ' + str(out)
    return '⛔ Wan: таймаут ожидания ' + str(poll_timeout) + ' с'


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='Wan2.2 T2V через ComfyUI (:8188)')
    ap.add_argument('prompt')
    ap.add_argument('--out', default='')
    ap.add_argument('--width', type=int, default=832)
    ap.add_argument('--height', type=int, default=480)
    ap.add_argument('--length', type=int, default=81, help='кадры (4n+1), 81 ≈ 5 с при 16 fps')
    ap.add_argument('--steps', type=int, default=20)
    ap.add_argument('--seed', type=int, default=None)
    ap.add_argument('--fps', type=int, default=16)
    a = ap.parse_args()
    print(wan_generate(a.prompt, a.out, a.width, a.height, a.length, a.steps, a.seed, a.fps))
