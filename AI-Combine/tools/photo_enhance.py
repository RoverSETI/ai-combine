"""photo_enhance.py — конвейер реставрации архивных фото (классика, без генератива):
1) авто-обрезка чёрных полос скана (глубина >= 8px);
2) баланс белого по светлым областям (снег -> белый), клип коэффициентов;
3) лёгкая растяжка уровней (клип 0.05%);
4) мягкий unsharp (радиус 1.2, 60%).

Использование: python3 photo_enhance.py <вход.jpg> <выход.jpg> [--hard|--soft]
--hard  = клип WB [0.70, 1.35] (сильный каст, зимние сканы);
--soft  = клип WB [0.85, 1.15] (лёгкие сдвиги, летние/осенние кадры).
"""
from pathlib import Path
import sys
import numpy as np
from PIL import Image, ImageFilter

MIN_STRIPE = 8      # минимальная глубина чёрной полосы для обрезки (px)
STRIPE_THR = 22.0   # средняя яркость линии, ниже которой линия = чёрная полоса
MARGIN = 2          # px доп. отступа в контент после обрезки

def find_stripes(arr) -> dict:
    """Глубина чёрной однотонной полосы по каждому краю."""
    lum = arr[..., :3].mean(axis=2)
    h, w = lum.shape
    out = {}
    for side in ("left", "right", "top", "bottom"):
        depth = 0
        limit = min(200, h // 2, w // 2)
        for d in range(1, limit + 1):
            line = {"left": lum[:, d-1], "right": lum[:, w-d],
                    "top": lum[d-1, :], "bottom": lum[h-d, :]}[side]
            if line.mean() < STRIPE_THR:
                depth = d
            else:
                break
        out[side] = depth
    return out

def crop_stripes(img: Image.Image, arr) -> Image.Image:
    stripes = find_stripes(arr)
    w, h = img.size
    l = min(stripes["left"] + MARGIN, w // 4)
    r = min(stripes["right"] + MARGIN, w // 4)
    t = min(stripes["top"] + MARGIN, h // 4)
    b = min(stripes["bottom"] + MARGIN, h // 4)
    box = (l, t, w - r, h - b)
    if box != (0, 0, w, h):
        img = img.crop(box)
    return img, stripes

def white_balance(img: Image.Image, lo: float, hi: float) -> tuple[Image.Image, list]:
    """WB по светлым областям: средние каналы p90-светов приводим к нейтрали."""
    rgb = np.asarray(img).astype(np.float32)
    lum = rgb.mean(axis=2)
    thr = np.percentile(lum, 90)
    bright = rgb[lum >= thr]
    mean = bright.mean(axis=0)
    k = mean.max() / np.maximum(mean, 1e-6)
    k = np.clip(k, lo, hi)
    out = rgb * k
    out = np.clip(out, 0, 255)
    return Image.fromarray(out.astype(np.uint8)), k.tolist()

def levels_stretch(img: Image.Image, clip_pct: float = 0.05) -> Image.Image:
    """Лёгкая растяжка уровней: чёрная/белая точка по перцентилям."""
    arr = np.asarray(img).astype(np.float32)
    lo = np.percentile(arr, clip_pct)
    hi = np.percentile(arr, 100 - clip_pct)
    if hi - lo < 1:
        return img
    out = (arr - lo) * (255.0 / (hi - lo))
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))

def process(src: Path, dst: Path, mode: str) -> dict:
    lo, hi = (0.70, 1.35) if mode == "hard" else (0.85, 1.15)
    img = Image.open(src).convert("RGB")
    orig_size = img.size
    img, stripes = crop_stripes(img, np.asarray(img))
    img, k = white_balance(img, lo, hi)
    img = levels_stretch(img)
    img = img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=60, threshold=3))
    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst, quality=92, optimize=True)
    return {"src": str(src), "dst": str(dst), "size_before": orig_size,
            "size_after": img.size, "stripes": stripes, "wb_k": [round(x, 3) for x in k]}

if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("usage: photo_enhance.py <in> <out> [--hard|--soft]")
    mode = "hard"
    if len(sys.argv) > 3 and sys.argv[3] == "--soft":
        mode = "soft"
    info = process(Path(sys.argv[1]), Path(sys.argv[2]), mode)
    for key, val in info.items():
        print(f"{key}: {val}")
