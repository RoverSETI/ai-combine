#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stt_model.py — транскрибация через GigaAM-v3 RNNT (локальные веса, без HF и без subprocess).

Использование:
    python3 stt_model.py <аудиофайл> [--json]

Модель собирается из model/ (config.json + pytorch_model.bin + tokenizer.model).
Не-wav форматы декодируются torchaudio (встроенный ffmpeg-бэкенд), сохраняются
во временный 16к-моно wav и подаются в модель.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

MODEL_DIR = Path(__file__).resolve().parent / "model"
ALLOWED_EXT = {".wav", ".mp3", ".ogg", ".m4a", ".flac", ".opus", ".webm"}
_MODEL = None


def load_model():
    """Загрузка GigaAMASR из локальных весов (кэш на процесс)."""
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    import torch
    import omegaconf
    if str(MODEL_DIR) not in sys.path:
        sys.path.insert(0, str(MODEL_DIR))
    from modeling_gigaam import GigaAMASR

    os.chdir(str(MODEL_DIR))  # tokenizer.model ищется в cwd
    cfg = json.loads((MODEL_DIR / "config.json").read_text())
    oc = omegaconf.OmegaConf.create(cfg["cfg"])
    model = GigaAMASR(oc.model.cfg)
    sd = torch.load(MODEL_DIR / "pytorch_model.bin", map_location="cpu", weights_only=True)
    model.load_state_dict({k.replace("model.", "", 1): v for k, v in sd.items()}, strict=True)
    model.eval().cuda()
    _MODEL = model
    return model


def _prepare_wav16(src: Path) -> str:
    """Декодировать аудио torchaudio → 16к моно → временный wav (без subprocess)."""
    import torchaudio

    wav, sr = torchaudio.load(str(src))
    if sr != 16000:
        wav = torchaudio.functional.resample(wav, sr, 16000)
    if wav.shape[0] > 1:
        wav = wav.mean(dim=0, keepdim=True)
    fd, dst = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    torchaudio.save(dst, wav, 16000)
    return dst


def transcribe(path: str) -> str:
    """Файл → текст. wav 16к — напрямую, остальные — через временный wav."""
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError("файл не найден: " + str(p))
    if p.suffix.lower() not in ALLOWED_EXT:
        raise ValueError("неподдерживаемый тип: " + p.suffix)
    if p.suffix.lower() == ".wav":
        return load_model().transcribe(str(p))
    tmp = _prepare_wav16(p)
    try:
        return load_model().transcribe(tmp)
    finally:
        Path(tmp).unlink(missing_ok=True)


def main():
    if len(sys.argv) < 2:
        print("Использование: stt_model.py <аудиофайл> [--json]")
        sys.exit(1)
    path = sys.argv[1]
    as_json = "--json" in sys.argv
    try:
        text = transcribe(path)
        if as_json:
            print(json.dumps({"text": text, "status": "ok"}, ensure_ascii=False))
        else:
            print(text)
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
