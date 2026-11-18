#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stt_model.py — транскрибация через GigaAM-v3 RNNT + голосовая диаризация pyannote.

Использование:
    python3 stt_model.py <аудиофайл> [--json] [--diarize]

Модель собирается из model/ (config.json + pytorch_model.bin + tokenizer.model).
Не-wav форматы декодируются torchaudio (встроенный ffmpeg-бэкенд), сохраняются
во временный 16к-моно wav и подаются в модель.

Диаризация (--diarize): pyannote segmentation+embedding делит аудио на реплики
по ГОЛОСУ; GigaAM транскрибирует каждую реплику отдельно. Нужен HF-токен
(файл ~/AiStart/HfToken или env HF_TOKEN) — модели pyannote gated.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

MODEL_DIR = Path(__file__).resolve().parent / "model"
ALLOWED_EXT = {".wav", ".mp3", ".ogg", ".m4a", ".flac", ".opus", ".webm"}
_HF_TOKEN_FILE = Path("/home/rover/AiStart/HfToken")
_MODEL = None
_DIAR_PIPELINE = None


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


def _hf_token() -> str | None:
    """HF-токен: env HF_TOKEN, иначе файл ~/AiStart/HfToken, иначе None."""
    tok = os.environ.get("HF_TOKEN")
    if tok:
        return tok.strip()
    if _HF_TOKEN_FILE.is_file():
        try:
            return _HF_TOKEN_FILE.read_text(encoding="utf-8").strip().split()[0]
        except Exception:
            return None
    return None


def load_diar_pipeline():
    """pyannote-пайплайн диаризации из компонентов (segmentation + wespeaker).

    Собирается вручную, БЕЗ репозитория speaker-diarization-community-1:
    pyannote 4.0.7 тянет его по умолчанию, но при AgglomerativeClustering
    PLDA не используется — передаём заглушку. Параметры из официального
    config.yaml pyannote/speaker-diarization-3.1.
    """
    global _DIAR_PIPELINE
    if _DIAR_PIPELINE is not None:
        return _DIAR_PIPELINE
    from pyannote.audio import Model
    from pyannote.audio.pipelines import SpeakerDiarization
    from pyannote.audio.core.plda import PLDA

    token = _hf_token()
    if not token:
        raise RuntimeError(
            "HF-токен не найден: файл " + str(_HF_TOKEN_FILE) +
            " не существует. Модели pyannote gated — нужен read-токен "
            "(https://hf.co/settings/tokens), условия принять на страницах "
            "pyannote/segmentation-3.0 и pyannote/wespeaker-voxceleb-resnet34-LM"
        )
    plda_stub = object.__new__(PLDA)  # не вызывается при AgglomerativeClustering
    plda_stub.lda_dimension = 128
    seg = Model.from_pretrained("pyannote/segmentation-3.0", token=token)
    emb = Model.from_pretrained("pyannote/wespeaker-voxceleb-resnet34-LM", token=token)
    pipe = SpeakerDiarization(
        segmentation=seg, embedding=emb,
        clustering="AgglomerativeClustering",
        plda=plda_stub,
        embedding_exclude_overlap=True,
    )
    pipe.instantiate({
        "clustering": {"method": "centroid", "min_cluster_size": 12,
                       "threshold": 0.7045654963945799},
        "segmentation": {"min_duration_off": 0.0},
    })
    _DIAR_PIPELINE = pipe
    return _DIAR_PIPELINE


def _prepare_wav16_path(src: Path) -> tuple[str, bool]:
    """Вернуть (путь к 16к-моно wav, флаг_временного). wav 16к не перекодируем."""
    import torchaudio

    if src.suffix.lower() == ".wav":
        # проверяем sr/channels через load (в новых torchaudio нет .info)
        wav_check, sr_check = torchaudio.load(str(src))
        del wav_check
        if sr_check == 16000 and src.stat().st_size > 44:
            # уточняем число каналов из заголовка без полной загрузки
            import wave
            try:
                with wave.open(str(src), "rb") as w:
                    if w.getnchannels() == 1:
                        return str(src), False
            except Exception:
                pass
        tmp_src = str(src)
    else:
        tmp_src = str(src)
    wav, sr = torchaudio.load(tmp_src)
    if sr != 16000:
        wav = torchaudio.functional.resample(wav, sr, 16000)
    if wav.shape[0] > 1:
        wav = wav.mean(dim=0, keepdim=True)
    fd, dst = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    torchaudio.save(dst, wav, 16000)
    return dst, True


def diarize_turns(path: str, min_len: float = 0.4, merge_gap: float = 0.5,
                  max_chunk: float = 25.0) -> list[dict]:
    """Голосовая диаризация: pyannote делит аудио на реплики по голосу,
    GigaAM транскрибирует каждую реплику.

    Returns: [{"start": float, "end": float, "speaker": "SPEAKER_00", "text": str}, ...]
    Реплики короче min_len сливаются с соседями; пауза > merge_gap разделяет
    смену реплики; длинные реплики (> max_chunk) режутся для GigaAM.
    """
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError("файл не найден: " + str(p))
    if p.suffix.lower() not in ALLOWED_EXT:
        raise ValueError("неподдерживаемый тип: " + p.suffix)

    wav16, is_temp = _prepare_wav16_path(p)
    try:
        pipe = load_diar_pipeline()
        diar = pipe(wav16)
        # pyannote 4.0.7 возвращает DiarizeOutput (обёртка над Annotation)
        annotation = getattr(diar, "speaker_diarization", diar)
        # делим на спикер-сегменты (TURN-ы)
        turns = []  # (start, end, speaker)
        for turn, _, speaker in annotation.itertracks(yield_label=True):
            turns.append((turn.start, turn.end, speaker))
        turns.sort(key=lambda t: (t[0], t[2]))
        if not turns:
            return []

        # слияние коротких сегментов одного спикера подряд
        merged = []
        for st, en, sp in turns:
            if merged and merged[-1][2] == sp and st - merged[-1][1] <= merge_gap:
                merged[-1][1] = max(merged[-1][1], en)
            else:
                merged.append([st, en, sp])

        # GigaAM по каждой реплике (модель загружаем один раз)
        import torch
        import torchaudio as ta
        asr = load_model()
        waveform, sr = ta.load(wav16)  # sr == 16000
        out = []
        for st, en, sp in merged:
            dur = en - st
            if dur < min_len:
                continue
            # нарезка длинной реплики на куски <= max_chunk
            t0 = st
            while t0 < en - 0.01:
                t1 = min(t0 + max_chunk, en)
                s_i = int(t0 * sr)
                e_i = int(t1 * sr)
                chunk = waveform[:, s_i:e_i]
                fd, tmp = tempfile.mkstemp(suffix=".wav")
                os.close(fd)
                try:
                    ta.save(tmp, chunk, sr)
                    with torch.no_grad():
                        text = asr.transcribe(tmp).strip()
                finally:
                    Path(tmp).unlink(missing_ok=True)
                if text:
                    out.append({"start": round(t0, 2), "end": round(t1, 2),
                                "speaker": sp, "text": text})
                else:
                    # пустой декод — реплика слышима (pyannote её нашёл), но ASR
                    # не распознал: помечаем, чтобы не терять сегмент молча
                    out.append({"start": round(t0, 2), "end": round(t1, 2),
                                "speaker": sp, "text": "[речь не распознана]"})
                t0 = t1
        # выгружаем GigaAM, чтобы освободить VRAM для последующего qwen
        global _MODEL
        del asr, _MODEL
        _MODEL = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        return out
    finally:
        if is_temp:
            Path(wav16).unlink(missing_ok=True)


def main():
    if len(sys.argv) < 2:
        print("Использование: stt_model.py <аудиофайл> [--json] [--diarize]")
        sys.exit(1)
    path = sys.argv[1]
    as_json = "--json" in sys.argv
    do_diarize = "--diarize" in sys.argv
    try:
        if do_diarize:
            turns = diarize_turns(path)
            if as_json:
                print(json.dumps({"status": "ok", "segments": turns,
                                  "text": " ".join(t["text"] for t in turns)},
                                 ensure_ascii=False))
            else:
                for t in turns:
                    m, s = divmod(int(t["start"]), 60)
                    print(f"[{m:02d}:{s:02d}] {t['speaker']}: {t['text']}")
        else:
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