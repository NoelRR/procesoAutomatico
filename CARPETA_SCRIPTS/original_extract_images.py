"""
extract_images.py
=================
Extrae UNA imagen del panel de datos por cada lote vendido en un video de
subasta del canal Remates Beni (Facebook). No hace OCR: solo detecta
cuándo cambia el número de lote (usando hash perceptual) y guarda un
screenshot limpio del panel en ese momento.

Las imágenes resultantes son consumidas después por un agente de visión
(Claude, GPT-4o, etc.) que extrae los datos estructurados.

Uso:
    python extract_images.py subasta.mp4
    python extract_images.py subasta.mp4 --out /tmp/subasta_22_abril
    python extract_images.py subasta.mp4 --sample-fps 2 --stability 3

Salida:
    Carpeta con un PNG por lote detectado, nombrado secuencialmente:
        lote_001.png
        lote_002.png
        ...
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


# ---------------------------------------------------------------------------
# CONFIGURACIÓN DEL LAYOUT (Remates Beni, panel 1280x720)
# ---------------------------------------------------------------------------
PANEL_REL = {
    'x1': 0.7406, 'y1': 0.6847,
    'x2': 1.0000, 'y2': 1.0000,
}

LOTE_ROI_REL = (0.122, 0.302, 0.030, 0.226)  # (y1, y2, x1, x2)


# ---------------------------------------------------------------------------
# UTILIDADES
# ---------------------------------------------------------------------------
def rel_box(rel, shape):
    h, w = shape[:2]
    y1, y2, x1, x2 = rel
    return int(y1 * h), int(y2 * h), int(x1 * w), int(x2 * w)


def get_panel(frame: np.ndarray) -> np.ndarray:
    h, w = frame.shape[:2]
    x1 = int(PANEL_REL['x1'] * w)
    y1 = int(PANEL_REL['y1'] * h)
    x2 = int(PANEL_REL['x2'] * w)
    y2 = int(PANEL_REL['y2'] * h)
    return frame[y1:y2, x1:x2]


def is_panel_visible(panel: np.ndarray, min_dark_frac: float = 0.30) -> bool:
    if panel.size == 0:
        return False
    gray = cv2.cvtColor(panel, cv2.COLOR_BGR2GRAY) if len(panel.shape) == 3 else panel
    dark_frac = (gray < 40).mean()
    return dark_frac >= min_dark_frac


def perceptual_hash(img: np.ndarray, size: int = 16) -> str:
    if img.size == 0:
        return ''
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
    small = cv2.resize(gray, (size, size), interpolation=cv2.INTER_AREA)
    mean = small.mean()
    bits = (small > mean).astype(np.uint8).flatten()
    packed = np.packbits(bits)
    return packed.tobytes().hex()


def hash_distance(h1: str, h2: str) -> int:
    if not h1 or not h2 or len(h1) != len(h2):
        return 999
    b1 = bytes.fromhex(h1)
    b2 = bytes.fromhex(h2)
    return sum(bin(a ^ b).count('1') for a, b in zip(b1, b2))


# ---------------------------------------------------------------------------
# EXTRACCIÓN
# ---------------------------------------------------------------------------
def process_video(
    video_path: str | Path,
    output_dir: str | Path,
    sample_fps: float = 0.2,
    stability_frames: int = 2,
    hash_threshold: int = 8,
    min_lote_s: float = 20.0,
    verbose: bool = True,
) -> int:
    """
    Procesa un video, detecta cambios de lote, y guarda UN PNG del panel
    por cada lote detectado.

    Returns: número de lotes extraídos.
    """
    video_path = Path(video_path)
    output_dir = Path(output_dir)

    if not video_path.exists():
        raise FileNotFoundError(f"No existe: {video_path}")
    output_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"No se pudo abrir: {video_path}")

    video_fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration = total_frames / video_fps if video_fps else 0
    sample_every = max(1, int(round(video_fps / sample_fps)))

    if verbose:
        print(f"[extract] Video: {video_path.name}", file=sys.stderr)
        print(f"[extract]   {width}x{height} @ {video_fps:.1f}fps, "
              f"{duration/60:.1f} min", file=sys.stderr)
        print(f"[extract]   Muestreando cada {sample_every} frames "
              f"(~{sample_fps:.1f} Hz)", file=sys.stderr)

    last_saved_hash: str = ''
    last_save_t: float = -min_lote_s
    candidate_hash: str = ''
    candidate_count: int = 0
    current_lote_hash: str = ''
    last_stable_frame: Optional[np.ndarray] = None
    last_stable_t: float = 0.0
    orden = 0

    frame_idx = 0
    while frame_idx < total_frames:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if not ret:
            break

        panel = get_panel(frame)

        if not is_panel_visible(panel):
            frame_idx += sample_every
            continue

        y1, y2, x1, x2 = rel_box(LOTE_ROI_REL, panel.shape[:2])
        lote_roi = panel[y1:y2, x1:x2]
        current_hash = perceptual_hash(lote_roi)

        if not current_hash:
            frame_idx += sample_every
            continue

        if candidate_hash and hash_distance(current_hash, candidate_hash) < hash_threshold:
            candidate_count += 1
        else:
            candidate_hash = current_hash
            candidate_count = 1

        if candidate_count >= stability_frames:
            if current_lote_hash and hash_distance(candidate_hash, current_lote_hash) < hash_threshold:
                last_stable_frame = frame.copy()
                last_stable_t = frame_idx / video_fps
            else:
                is_different = (
                    not last_saved_hash
                    or hash_distance(current_lote_hash, last_saved_hash) >= hash_threshold
                )
                cooldown_ok = (last_stable_t - last_save_t) >= min_lote_s
                if last_stable_frame is not None and is_different and cooldown_ok:
                    orden += 1
                    fname = f"lote_{orden:03d}.png"
                    cv2.imwrite(str(output_dir / fname), get_panel(last_stable_frame))
                    last_saved_hash = current_lote_hash
                    last_save_t = last_stable_t
                    if verbose:
                        print(f"[extract]   #{orden:3d} → {fname}", file=sys.stderr)
                elif last_stable_frame is not None and is_different and not cooldown_ok:
                    if verbose:
                        print(f"[extract]   (cambio ignorado — cooldown {min_lote_s:.0f}s)",
                              file=sys.stderr)

                current_lote_hash = candidate_hash
                last_stable_frame = frame.copy()
                last_stable_t = frame_idx / video_fps

        frame_idx += sample_every

    # Último lote
    if last_stable_frame is not None and current_lote_hash:
        is_different = (
            not last_saved_hash
            or hash_distance(current_lote_hash, last_saved_hash) >= hash_threshold
        )
        cooldown_ok = (last_stable_t - last_save_t) >= min_lote_s
        if is_different and cooldown_ok:
            orden += 1
            fname = f"lote_{orden:03d}.png"
            cv2.imwrite(str(output_dir / fname), get_panel(last_stable_frame))
            if verbose:
                print(f"[extract]   #{orden:3d} → {fname} (último lote)", file=sys.stderr)

    cap.release()

    if verbose:
        print(f"[extract] ✅ {orden} lotes extraídos → {output_dir}/", file=sys.stderr)

    return orden


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Extrae una imagen del panel por cada lote vendido.")
    ap.add_argument('video', help="Ruta al video descargado")
    ap.add_argument('--out', default=None,
                    help="Carpeta de salida (default: <video>_lotes/)")
    ap.add_argument('--sample-fps', type=float, default=0.2)
    ap.add_argument('--stability', type=int, default=2)
    ap.add_argument('--hash-threshold', type=int, default=8)
    ap.add_argument('--min-lote-secs', type=float, default=20.0)
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args()

    video_path = Path(args.video)
    out_dir = Path(args.out) if args.out else video_path.parent / f"{video_path.stem}_lotes"

    total = process_video(
        video_path,
        out_dir,
        sample_fps=args.sample_fps,
        stability_frames=args.stability,
        hash_threshold=args.hash_threshold,
        min_lote_s=args.min_lote_secs,
        verbose=not args.quiet,
    )
    print(f"output_dir={out_dir} total_lotes={total}")


if __name__ == '__main__':
    main()
