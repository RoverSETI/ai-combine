#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
photo_fix.py — реставрация фотографий: обрезка, поворот, яркость/контраст,
шумоподавление, удаление трещин/царапин (ИИ-заливка LaMa на RTX 3060).

Запуск (CLI):
  /home/rover/OpenWebUI-Local/.venv/bin/python photo_fix.py фото.jpg --preset old
  /home/rover/OpenWebUI-Local/.venv/bin/python photo_fix.py папка/ --preset modern
Флаги:
  --preset old|modern      базовый набор операций (по умолчанию old)
  --rotate N               поворот на N градусов (по часовой, любой угол)
  --auto-straighten        выровнять горизонт/скан по линиям
  --crop x,y,w,h           обрезка в пикселях ИЛИ --crop auto (рамки скана)
  --denoise off|light|medium|strong
  --fix-cracks off|auto    auto = авто-маска повреждений + заливка
  --mask file.png          своё выделение повреждений (белое = закрасить)
  --brightness N           -100..100 (яркость)
  --contrast N             -100..100 (контраст)
  --gamma F                гамма-коррекция (0.5 темнее контрастно, 2.0 светлее)
  --scale F                масштаб (например 2 = x2)
  --quality N              JPEG-качество (по умолчанию 95)
  --dry-run                показать план без записи
Результат: <папка оригинала>/restored/<имя>_fixed.<расширение>.
Оригиналы не изменяются.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

try:  # сохранение EXIF, если пакет установлен
    import piexif
    HAVE_PIEXIF = True
except Exception:
    HAVE_PIEXIF = False

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}

DENOISE_H = {"light": 3, "medium": 7, "strong": 12}


# ---------------------------------------------------------------- загрузка/сохранение
def load_image(path: Path) -> np.ndarray:
    """BGR-массив с учётом EXIF-поворота."""
    pil = Image.open(path)
    pil = ImageOps.exif_transpose(pil)
    if pil.mode in ("RGBA", "LA", "P"):
        pil = pil.convert("RGB")
    arr = np.array(pil)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def save_image(img: np.ndarray, src: Path, out: Path, quality: int) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    ext = out.suffix.lower()
    params = []
    if ext in (".jpg", ".jpeg"):
        params = [cv2.IMWRITE_JPEG_QUALITY, quality]
    elif ext == ".png":
        params = [cv2.IMWRITE_PNG_COMPRESSION, 6]
    elif ext in (".webp",):
        params = [cv2.IMWRITE_WEBP_QUALITY, max(quality, 80)]
    ok = cv2.imwrite(str(out), img, params)
    if not ok:
        raise RuntimeError(f"не удалось записать {out}")
    # перенос EXIF из оригинала (простые теги, без ориентации — она уже применена)
    if HAVE_PIEXIF and ext in (".jpg", ".jpeg"):
        try:
            exif_dict = piexif.load(str(src))
            exif_dict.pop("0th", None) or None
            if "0th" in exif_dict and 274 in exif_dict["0th"]:
                del exif_dict["0th"][274]
            piexif.insert(piexif.dump(exif_dict), str(out))
        except Exception:
            pass


# ---------------------------------------------------------------- геометрия
def rotate_image(img: np.ndarray, degrees: float) -> np.ndarray:
    if abs(degrees % 360) < 0.01:
        return img
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), degrees, 1.0)
    cos, sin = abs(M[0, 0]), abs(M[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    M[0, 2] += nw / 2 - w / 2
    M[1, 2] += nh / 2 - h / 2
    return cv2.warpAffine(img, M, (nw, nh), flags=cv2.INTER_LANCZOS4,
                          borderMode=cv2.BORDER_REPLICATE)


def estimate_skew(img: np.ndarray) -> float:
    """Угол наклона по доминирующим линиям (для выравнивания сканов)."""
    small = cv2.resize(img, None, fx=0.35, fy=0.35)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 60, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=110,
                            minLineLength=min(gray.shape) // 3, maxLineGap=12)
    if lines is None:
        return 0.0
    angs = []
    for x1, y1, x2, y2 in lines[:, 0]:
        a = np.degrees(np.arctan2(y2 - y1, x2 - x1)) % 180
        if a < 25 or a > 155:  # только почти-горизонтальные
            angs.append(a if a < 90 else a - 180)
    if not angs:
        return 0.0
    ang = float(np.median(angs))
    return -ang if abs(ang) > 0.15 else 0.0


def auto_border_crop(img: np.ndarray) -> tuple[int, int, int, int]:
    """Найти и отрезать однотонные рамки скана (белые/чёрные поля)."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    thr = 22  # отклонение от края
    def row_is_border(y: int) -> bool:
        return int(np.abs(gray[y].astype(int) - int(gray[y].mean())).std() < 6) and \
               (abs(int(gray[y].mean()) - int(gray[min(y + 8, h - 1)].mean())) < thr)
    def col_is_border(x: int) -> bool:
        return int(np.abs(gray[:, x].astype(int) - int(gray[:, x].mean())).std() < 6) and \
               (abs(int(gray[:, x].mean()) - int(gray[:, min(x + 8, w - 1)].mean())) < thr)
    top, bottom, left, right = 0, h - 1, 0, w - 1
    margin = min(h, w) // 12
    while top < margin and row_is_border(top):
        top += 1
    while h - 1 - bottom < margin and row_is_border(bottom):
        bottom -= 1
    while left < margin and col_is_border(left):
        left += 1
    while w - 1 - right < margin and col_is_border(right):
        right -= 1
    if (bottom - top) < h * 0.4 or (right - left) < w * 0.4:
        return 0, 0, w, h  # не похоже на рамку — не трогаем
    return left, top, right - left + 1, bottom - top + 1


def crop_image(img: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    x, y, w, h = box
    h_img, w_img = img.shape[:2]
    x, y = max(0, x), max(0, y)
    w, h = min(w, w_img - x), min(h, h_img - y)
    return img[y:y + h, x:x + w].copy()


# ---------------------------------------------------------------- тон и контраст
def auto_levels(img: np.ndarray, cut: float = 0.5) -> np.ndarray:
    """Растяжка контраста по перцентилям (лечит выцветшие сканы)."""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l = lab[:, :, 0]
    lo, hi = np.percentile(l, cut), np.percentile(l, 100 - cut)
    if hi - lo > 10:
        l = np.clip((l.astype(np.float32) - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)
        lab[:, :, 0] = l
        return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    return img


def gray_world_wb(img: np.ndarray) -> np.ndarray:
    """Баланс белого «серый мир» — убирает желтизну старых фото."""
    b, g, r = cv2.split(img.astype(np.float32))
    mb, mg, mr = b.mean(), g.mean(), r.mean()
    mg = max(mg, 1.0)
    b *= mg / mb
    r *= mg / mr
    out = cv2.merge([b, g, r])
    return np.clip(out, 0, 255).astype(np.uint8)


def clahe(img: np.ndarray, clip: float = 2.0) -> np.ndarray:
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    clahe = cv2.createCLAHE(clipLimit=clip, tileGridSize=(8, 8))
    lab[:, :, 0] = clahe.apply(lab[:, :, 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


def brightness_contrast_gamma(img: np.ndarray, brightness: float = 0.0,
                              contrast: float = 0.0, gamma: float = 1.0) -> np.ndarray:
    out = img.astype(np.float32)
    if contrast:
        out = out * (1 + contrast / 100.0) - 128 * (contrast / 100.0)
    if brightness:
        out = out + brightness * 2.55
    out = np.clip(out, 0, 255)
    if gamma and abs(gamma - 1.0) > 0.01:
        lut = ((np.linspace(0, 255, 256) / 255.0) ** (1.0 / gamma) * 255).astype(np.uint8)
        out = cv2.LUT(out.astype(np.uint8), lut)
    return out


def denoise(img: np.ndarray, level: str) -> np.ndarray:
    h = DENOISE_H.get(level, 0)
    if h <= 0:
        return img
    return cv2.fastNlMeansDenoisingColored(img, None, h, h, 7, 21)


# ---------------------------------------------------------------- трещины/царапины
def build_damage_mask(img: np.ndarray, extra_mask: Path | None = None) -> np.ndarray:
    """Авто-маска повреждений: тонкие светлые (царапины) и тёмные (трещины) структуры."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    smooth = cv2.medianBlur(gray, 3)  # лёгкое сглаживание остаточного шума
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13))
    bright = cv2.morphologyEx(smooth, cv2.MORPH_TOPHAT, kernel)
    dark = cv2.morphologyEx(smooth, cv2.MORPH_BLACKHAT, kernel)
    # Otsu здесь не работает (структуры редкие). Порог — от 99.9-перцентиля:
    # самонастройка под силу сигнала (0.25*p99.9 держит бледные трещины,
    # но отсеивает шум ровного фона)
    m1 = (bright > max(12, 0.25 * float(np.percentile(bright, 99.9)))).astype(np.uint8) * 255
    m2 = (dark > max(15, 0.25 * float(np.percentile(dark, 99.9)))).astype(np.uint8) * 255
    mask = cv2.bitwise_or(m1, m2)
    # убрать крупные ПЛОТНЫЕ области (объекты, а не линии)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    h, w = mask.shape
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        fill = area / float(bw * bh)
        if area > h * w * 0.01 and fill > 0.45:
            mask[labels == i] = 0
    mask = cv2.medianBlur(mask, 3)
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)
    if extra_mask is not None:
        user = cv2.imread(str(extra_mask), cv2.IMREAD_GRAYSCALE)
        if user is not None:
            user = cv2.resize(user, (w, h))
            mask = cv2.bitwise_or(mask, (user > 127).astype(np.uint8) * 255)
    return mask


_lama = None


def get_lama():
    """Ленивая инициализация LaMa (CUDA, ~200 МБ VRAM). None — если недоступна."""
    global _lama
    if _lama is not None:
        return _lama
    try:
        from simple_lama_inpainting import SimpleLama
        _lama = SimpleLama()
    except Exception:
        _lama = None
    return _lama


def inpaint_cracks(img: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, str]:
    """Залить повреждения: LaMa (ИИ) или cv2.inpaint (классика)."""
    if mask.max() == 0 or mask.mean() / 255 > 0.35:
        return img, "маска пуста или повреждений слишком много — заливка пропущена"
    lama = get_lama()
    if lama is not None:
        try:
            limit = 2048
            h, w = img.shape[:2]
            scale = min(1.0, limit / max(h, w))
            if scale < 1.0:
                img_s = cv2.resize(img, (int(w * scale), int(h * scale)))
                mask_s = cv2.resize(mask, (int(w * scale), int(h * scale)),
                                    interpolation=cv2.INTER_NEAREST)
            else:
                img_s, mask_s = img, mask
            rgb = cv2.cvtColor(img_s, cv2.COLOR_BGR2RGB)
            res = lama(rgb, mask_s)
            res = cv2.cvtColor(np.array(res), cv2.COLOR_RGB2BGR)
            if scale < 1.0:
                res = cv2.resize(res, (w, h), interpolation=cv2.INTER_LANCZOS4)
            return res, f"LaMa (GPU), закрашено {mask.mean() / 255 * 100:.1f}% кадра"
        except Exception:
            pass
    res = cv2.inpaint(img, mask, 5, cv2.INPAINT_TELEA)
    return res, "cv2.inpaint (fallback, LaMa недоступна)"


# ---------------------------------------------------------------- конвейер
def process_image(src: Path, args) -> tuple[Path, dict]:
    img = load_image(src)
    steps = []

    if args.rotate:
        img = rotate_image(img, args.rotate)
        steps.append(f"поворот {args.rotate}°")
    if args.auto_straighten:
        ang = estimate_skew(img)
        if ang:
            img = rotate_image(img, ang)
            steps.append(f"выравнивание {ang:+.1f}°")

    if args.crop == "auto":
        box = auto_border_crop(img)
        h0, w0 = img.shape[:2]
        if box != (0, 0, w0, h0):
            img = crop_image(img, box)
            steps.append(f"обрезка рамок {box}")
    elif args.crop:
        img = crop_image(img, tuple(int(v) for v in args.crop.split(",")))
        steps.append(f"обрезка {args.crop}")

    preset = args.preset
    dn = args.denoise if args.denoise != "default" else ("medium" if preset == "old" else "strong")
    if dn != "off":
        img = denoise(img, dn)
        steps.append(f"шумоподавление {dn}")

    if preset == "old" and not args.no_auto:
        img = gray_world_wb(img)
        img = auto_levels(img)
        steps.append("тон: баланс белого + уровни")

    # маска повреждений строится ДО CLAHE: CLAHE усиливает шум и засоряет маску
    damage_mask = None
    fix = args.fix_cracks if args.fix_cracks != "default" else ("auto" if preset == "old" else "off")
    if fix == "auto":
        damage_mask = build_damage_mask(img, args.mask)

    if preset == "old" and not args.no_auto:
        img = clahe(img, 1.8)
        steps.append("CLAHE")
    elif preset == "modern" and not args.no_auto:
        img = clahe(img, 1.5)
        steps.append("тон: CLAHE")

    if args.brightness or args.contrast or args.gamma != 1.0:
        img = brightness_contrast_gamma(img, args.brightness, args.contrast, args.gamma)
        steps.append(f"яркость {args.brightness}/контраст {args.contrast}/гамма {args.gamma}")

    if damage_mask is not None:
        img, how = inpaint_cracks(img, damage_mask)
        steps.append(f"трещины: {how}")

    if args.scale and abs(args.scale - 1.0) > 0.01:
        img = cv2.resize(img, None, fx=args.scale, fy=args.scale,
                         interpolation=cv2.INTER_LANCZOS4)
        steps.append(f"масштаб x{args.scale}")

    out = src.parent / "restored" / f"{src.stem}_fixed{src.suffix.lower()}"
    if args.out:
        out = Path(args.out)
        if out.is_dir():
            out = out / f"{src.stem}_fixed{src.suffix.lower()}"
    if not args.dry_run:
        save_image(img, src, out, args.quality)
    return out, {"file": str(src), "output": str(out), "size": f"{img.shape[1]}x{img.shape[0]}", "steps": steps}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Реставрация фотографий")
    ap.add_argument("input", help="файл или папка с изображениями")
    ap.add_argument("--preset", choices=["old", "modern"], default="old")
    ap.add_argument("--rotate", type=float, default=0.0)
    ap.add_argument("--auto-straighten", action="store_true")
    ap.add_argument("--crop", default="", help="x,y,w,h или 'auto'")
    ap.add_argument("--denoise", choices=["off", "light", "medium", "strong", "default"], default="default")
    ap.add_argument("--fix-cracks", choices=["off", "auto", "default"], default="default")
    ap.add_argument("--mask", default=None, help="PNG-маска повреждений (белое = закрасить)")
    ap.add_argument("--brightness", type=float, default=0.0)
    ap.add_argument("--contrast", type=float, default=0.0)
    ap.add_argument("--gamma", type=float, default=1.0)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--quality", type=int, default=95)
    ap.add_argument("--out", default="", help="папка/файл результата (по умолчанию restored/ рядом)")
    ap.add_argument("--no-auto", action="store_true", help="не применять авто-тон пресета")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    src = Path(args.input).expanduser()
    files = sorted(p for p in (src.iterdir() if src.is_dir() else [src])
                   if p.is_file() and p.suffix.lower() in IMAGE_EXTS)
    if not files:
        print(f"❌ Изображений не найдено: {src}")
        return 1
    print(f"🖼  К обработке: {len(files)} файл(ов), пресет {args.preset}"
          + (" (dry-run)" if args.dry_run else ""))
    results = []
    for f in files:
        try:
            out, info = process_image(f, args)
            results.append(info)
            print(f"  ✅ {f.name} → {out.name} [{'; '.join(info['steps'])}]")
        except Exception as e:
            print(f"  ⚠️ {f.name}: {e}")
    print(json.dumps(results, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
