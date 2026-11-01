"""
extract_images.py
=================
Extrae UNA imagen del panel de datos por cada lote vendido en un video de
subasta del canal Remates Beni (Facebook).

Estrategia (OCR + confirmación):
1. Recorta el ROI del NÚMERO de lote (esquina superior izquierda del panel).
2. OCR con tesseract: lee el número como entero.
3. Compara enteros directamente (no hashes, no thresholds frágiles).
4. Para evitar guardar capturas en frames donde el OCR falla esporádicamente
   o lee mal por una transición visual, exige que el nuevo número se lea
   consistente durante `stability_frames` muestreos consecutivos antes de
   declarar cambio de lote.
5. Cuando el número confirmadamente cambia, guarda la última frame
   estable del lote anterior (la que tenía el número antiguo).

Garantía: exactamente UNA imagen por número de lote distinto.

Uso:
    python extract_images.py subasta.mp4
    python extract_images.py subasta.mp4 --out /tmp/subasta_22_abril
    python extract_images.py subasta.mp4 --sample-fps 0.2 --stability 3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np
import pytesseract


# ---------------------------------------------------------------------------
# CONFIGURACIÓN DEL LAYOUT (Remates Beni, panel 1280x720)
# ---------------------------------------------------------------------------
PANEL_REL = {
    'x1': 0.7406, 'y1': 0.6847,
    'x2': 1.0000, 'y2': 1.0000,
}

# ROI del NÚMERO de lote dentro del panel (NO incluye "LOTE" ni "PRECIO").
# Coordenadas relativas al panel: (y1, y2, x1, x2)
LOTE_ROI_REL: Tuple[float, float, float, float] = (0.10, 0.38, 0.00, 0.22)

# Configuración de tesseract: una sola línea, solo dígitos.
TESSERACT_CONFIG = '--psm 7 -c tessedit_char_whitelist=0123456789'

# Rango razonable de números de lote (para descartar lecturas absurdas).
MIN_LOTE = 1
MAX_LOTE = 9999


# ---------------------------------------------------------------------------
# UTILIDADES DE IMAGEN
# ---------------------------------------------------------------------------
def get_panel(frame: np.ndarray) -> np.ndarray:
    h, w = frame.shape[:2]
    x1 = int(PANEL_REL['x1'] * w)
    y1 = int(PANEL_REL['y1'] * h)
    x2 = int(PANEL_REL['x2'] * w)
    y2 = int(PANEL_REL['y2'] * h)
    return frame[y1:y2, x1:x2]


def is_panel_visible(panel: np.ndarray, min_dark_frac: float = 0.30) -> bool:
    """El panel real es ~70% píxeles negros. Si vemos pocos píxeles oscuros,
    el panel está cubierto u oculto."""
    if panel.size == 0:
        return False
    gray = cv2.cvtColor(panel, cv2.COLOR_BGR2GRAY) if len(panel.shape) == 3 else panel
    return (gray < 40).mean() >= min_dark_frac


def get_lote_roi(panel: np.ndarray) -> np.ndarray:
    h, w = panel.shape[:2]
    y1, y2, x1, x2 = LOTE_ROI_REL
    return panel[int(y1 * h):int(y2 * h), int(x1 * w):int(x2 * w)]


# ---------------------------------------------------------------------------
# OCR DEL NÚMERO DE LOTE
# ---------------------------------------------------------------------------
def ocr_lote_number(roi: np.ndarray) -> Optional[int]:
    """
    Lee el número de lote del ROI usando tesseract.

    Pipeline:
    1. Binariza (texto blanco brillante -> blanco; resto -> negro).
    2. Aísla el bounding box del texto (descarta padding variable).
    3. Pega centrado en un canvas con padding generoso (tesseract lo necesita).
    4. Escala 4x e invierte (negro sobre blanco).
    5. OCR con whitelist de dígitos.
    6. Valida que el resultado esté en [MIN_LOTE, MAX_LOTE].

    Returns: int con el número leído, o None si la lectura no es confiable.
    """
    if roi is None or roi.size == 0:
        return None

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if len(roi.shape) == 3 else roi
    _, bw = cv2.threshold(gray, 140, 255, cv2.THRESH_BINARY)

    coords = cv2.findNonZero(bw)
    if coords is None or len(coords) < 20:
        return None

    x, y, w, h = cv2.boundingRect(coords)
    if w < 6 or h < 10:
        return None

    crop = bw[y:y + h, x:x + w]

    # Padding generoso para tesseract
    pad = max(h // 3, 8)
    canvas = np.zeros((h + 2 * pad, w + 2 * pad), dtype=np.uint8)
    canvas[pad:pad + h, pad:pad + w] = crop

    # Escalar 4x
    big = cv2.resize(canvas, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    # Invertir (negro sobre blanco)
    inv = cv2.bitwise_not(big)

    text = pytesseract.image_to_string(inv, config=TESSERACT_CONFIG).strip()

    if not text or not text.isdigit():
        return None
    n = int(text)
    if n < MIN_LOTE or n > MAX_LOTE:
        return None
    return n


# ---------------------------------------------------------------------------
# EXTRACCIÓN
# ---------------------------------------------------------------------------
def process_video(
    video_path: str | Path,
    output_dir: str | Path,
    sample_fps: float = 0.2,
    stability_frames: int = 2,
    verbose: bool = True,
) -> int:
    """
    Procesa un video y guarda UN PNG del panel por cada número de lote
    distinto detectado. La imagen guardada es la última frame estable
    antes de que cambie el número de lote.

    Args:
        sample_fps: cuántas frames por segundo de video se inspeccionan.
                    0.2 = una cada 5 segundos (cada lote dura >= 1 minuto).
        stability_frames: cuántas frames consecutivas deben mostrar el
                          mismo número nuevo antes de declarar cambio
                          de lote. >= 2 recomendado para descartar errores
                          puntuales del OCR.

    Returns:
        Número de lotes extraídos.
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
              f"{duration / 60:.1f} min", file=sys.stderr)
        print(f"[extract]   Muestreando cada {sample_every} frames "
              f"(~1 frame cada {1 / sample_fps:.0f}s de video)",
              file=sys.stderr)
        print(f"[extract]   Stability frames: {stability_frames}", file=sys.stderr)

    # Estado:
    #   nro_lote:           número del lote actualmente "en curso"
    #   pending_nro:        número candidato a ser un nuevo lote
    #   pending_count:      cuántas frames consecutivas leyeron pending_nro
    #   last_stable_panel:  última frame del lote en curso (la que se guardará)
    #   saved_lotes:        set de números ya guardados (defensa extra contra
    #                       guardar el mismo lote dos veces)
    nro_lote: Optional[int] = None
    pending_nro: Optional[int] = None
    pending_count: int = 0
    last_stable_panel: Optional[np.ndarray] = None
    saved_lotes: set = set()
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

        nro_leido = ocr_lote_number(get_lote_roi(panel))
        if nro_leido is None:
            # OCR falló en esta frame: no hacemos nada (no se actualiza
            # last_stable_panel, no se cuenta como cambio).
            frame_idx += sample_every
            continue

        if nro_lote is None:
            # Aún no hemos confirmado el primer lote del video.
            # Exigimos stability_frames lecturas consecutivas iguales.
            if pending_nro == nro_leido:
                pending_count += 1
            else:
                pending_nro = nro_leido
                pending_count = 1

            if pending_count >= stability_frames:
                nro_lote = pending_nro
                last_stable_panel = panel.copy()
                pending_nro = None
                pending_count = 0
                if verbose:
                    print(f"[extract]   Primer lote detectado: #{nro_lote}",
                          file=sys.stderr)

        else:
            if nro_leido == nro_lote:
                # Mismo lote: actualizar la "última frame estable" y
                # resetear cualquier candidato pendiente.
                last_stable_panel = panel.copy()
                pending_nro = None
                pending_count = 0

            else:
                # Número distinto: confirmar antes de actuar.
                if pending_nro == nro_leido:
                    pending_count += 1
                else:
                    pending_nro = nro_leido
                    pending_count = 1

                if pending_count >= stability_frames:
                    # Cambio confirmado: guardar la última frame del lote anterior.
                    if last_stable_panel is not None and nro_lote not in saved_lotes:
                        orden += 1
                        fname = f"lote_{orden:03d}_n{nro_lote}.png"
                        cv2.imwrite(str(output_dir / fname), last_stable_panel)
                        saved_lotes.add(nro_lote)
                        if verbose:
                            print(f"[extract]   #{orden:3d} → {fname} "
                                  f"(lote {nro_lote} → {pending_nro})",
                                  file=sys.stderr)
                    elif nro_lote in saved_lotes and verbose:
                        print(f"[extract]   ⚠ lote {nro_lote} ya estaba guardado, "
                              f"se omite", file=sys.stderr)

                    nro_lote = pending_nro
                    last_stable_panel = panel.copy()
                    pending_nro = None
                    pending_count = 0

        frame_idx += sample_every

    # Guardar el último lote activo.
    if last_stable_panel is not None and nro_lote is not None \
            and nro_lote not in saved_lotes:
        orden += 1
        fname = f"lote_{orden:03d}_n{nro_lote}.png"
        cv2.imwrite(str(output_dir / fname), last_stable_panel)
        saved_lotes.add(nro_lote)
        if verbose:
            print(f"[extract]   #{orden:3d} → {fname} (último lote {nro_lote})",
                  file=sys.stderr)

    cap.release()

    if verbose:
        print(f"[extract] ✅ {orden} lotes extraídos → {output_dir}/",
              file=sys.stderr)

    return orden


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Extrae una imagen del panel por cada lote vendido "
                    "(usando OCR sobre el número de lote).")
    ap.add_argument('video', help="Ruta al video descargado")
    ap.add_argument('--out', default=None,
                    help="Carpeta de salida (default: <video>_lotes/)")
    ap.add_argument('--sample-fps', type=float, default=0.2,
                    help="Frames de video muestreadas por segundo (default 0.2 = 1 cada 5s)")
    ap.add_argument('--stability', type=int, default=2,
                    help="Frames consecutivas con un número nuevo necesarias "
                         "para declarar cambio de lote (default 2)")
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args()

    video_path = Path(args.video)
    out_dir = (Path(args.out) if args.out
               else video_path.parent / f"{video_path.stem}_lotes")

    total = process_video(
        video_path,
        out_dir,
        sample_fps=args.sample_fps,
        stability_frames=args.stability,
        verbose=not args.quiet,
    )
    print(f"output_dir={out_dir} total_lotes={total}")


if __name__ == '__main__':
    main()
