"""
excel_writer.py
===============
Recibe un JSON con datos de lotes (producidos por un agente de visión que
leyó las imágenes generadas por extract_images.py) y los appendea al
archivo Excel maestro.

Uso desde CLI:
    python excel_writer.py --json '<JSON>' --video subasta.mp4 --date 2026-04-22
    python excel_writer.py --json-file lotes.json --video subasta.mp4

Uso programático:
    from excel_writer import append_from_json
    n = append_from_json(data, master='subastas.xlsx',
                         source_video='subasta.mp4',
                         auction_date='2026-04-22')

Formato del JSON esperado:
{
  "lotes": [
    {
      "lote": 94,
      "cantidad": 1,
      "categoria": "Terneros",
      "raza": "Nelore",
      "edad": "2 meses",
      "procedencia": "Marban",
      "peso_total": 65,
      "peso_promedio": 65.00,
      "precio": 23.00,
      "monto_unidad_bs": 1495.00,
      "monto_unidad_us": 214.80,
      "monto_total_bs": 1495.00,
      "monto_total_us": 214.80,
      "h_llegada": "01:00"
    },
    ...
  ]
}
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd


MASTER_COLUMNS = [
    'fecha_subasta',
    'video_origen',
    'lote',
    'cantidad',
    'categoria',
    'raza',
    'edad',
    'procedencia',
    'peso_total',
    'peso_promedio',
    'precio',
    'monto_unidad_bs',
    'monto_unidad_us',
    'monto_total_bs',
    'monto_total_us',
    'h_llegada',
    'procesado_en',
]


def _to_int(v) -> Optional[int]:
    if v is None or v == '': return None
    try: return int(float(v))
    except (ValueError, TypeError): return None


def _to_float(v) -> Optional[float]:
    if v is None or v == '': return None
    try: return float(v)
    except (ValueError, TypeError): return None


def _to_str(v) -> Optional[str]:
    if v is None: return None
    s = str(v).strip()
    return s if s else None


def _normalize_lote(d: dict) -> dict:
    return {
        'lote':             _to_int(d.get('lote')),
        'cantidad':         _to_int(d.get('cantidad')),
        'categoria':        _to_str(d.get('categoria')),
        'raza':             _to_str(d.get('raza')),
        'edad':             _to_str(d.get('edad')),
        'procedencia':      _to_str(d.get('procedencia')),
        'peso_total':       _to_float(d.get('peso_total')),
        'peso_promedio':    _to_float(d.get('peso_promedio')),
        'precio':           _to_float(d.get('precio')),
        'monto_unidad_bs':  _to_float(d.get('monto_unidad_bs')),
        'monto_unidad_us':  _to_float(d.get('monto_unidad_us')),
        'monto_total_bs':   _to_float(d.get('monto_total_bs')),
        'monto_total_us':   _to_float(d.get('monto_total_us')),
        'h_llegada':        _to_str(d.get('h_llegada')),
    }


def append_from_json(
    data: dict | list,
    master: str | Path = 'subastas_maestro.xlsx',
    source_video: str = 'unknown.mp4',
    auction_date: Optional[str] = None,
) -> int:
    """
    Agrega filas al Excel maestro desde un JSON.
    Deduplica por (fecha_subasta, lote): si ya existe esa combinación,
    la sobrescribe con los datos nuevos.

    Returns: número de filas agregadas.
    """
    if isinstance(data, dict):
        lotes = data.get('lotes', [])
    elif isinstance(data, list):
        lotes = data
    else:
        raise ValueError("data debe ser dict con 'lotes' o lista")

    if not lotes:
        return 0

    master = Path(master)
    now = datetime.now().isoformat(timespec='seconds')
    auction_date = auction_date or datetime.now().strftime('%Y-%m-%d')

    rows = []
    for d in lotes:
        row = _normalize_lote(d)
        row['fecha_subasta'] = auction_date
        row['video_origen']  = source_video
        row['procesado_en']  = now
        rows.append(row)

    new_df = pd.DataFrame(rows)
    for col in MASTER_COLUMNS:
        if col not in new_df.columns:
            new_df[col] = None
    new_df = new_df[MASTER_COLUMNS]

    if master.exists():
        try:
            existing = pd.read_excel(master)
        except Exception as e:
            raise RuntimeError(f"No se pudo leer el Excel maestro: {e}")
    else:
        existing = pd.DataFrame(columns=MASTER_COLUMNS)

    # Deduplicar por (fecha_subasta, lote)
    if not existing.empty and 'fecha_subasta' in existing.columns \
            and 'lote' in existing.columns:
        keys_new = set(zip(
            new_df['fecha_subasta'].astype(str),
            new_df['lote'].astype('Int64').astype(str),
        ))
        mask = existing.apply(
            lambda r: (str(r.get('fecha_subasta')),
                       str(int(r['lote'])) if pd.notna(r.get('lote')) else 'nan')
                      not in keys_new,
            axis=1,
        )
        existing = existing[mask]

    combined = pd.concat([existing, new_df], ignore_index=True)
    combined['fecha_subasta'] = pd.to_datetime(combined['fecha_subasta'], errors='coerce')
    combined = combined.sort_values(
        by=['fecha_subasta', 'lote'],
        ascending=[True, True],
        na_position='last',
    ).reset_index(drop=True)

    master.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(master, engine='openpyxl') as writer:
        combined.to_excel(writer, index=False, sheet_name='Subastas')

    return len(new_df)


def summarize(data: dict | list) -> str:
    if isinstance(data, dict):
        lotes = data.get('lotes', [])
    else:
        lotes = data
    if not lotes:
        return "⚠️ No se procesó ningún lote."

    norm = [_normalize_lote(d) for d in lotes]
    n = len(norm)
    precios  = [x['precio'] for x in norm if x['precio'] is not None]
    pesos    = [x['peso_promedio'] for x in norm if x['peso_promedio'] is not None]
    lotes_n  = [x['lote'] for x in norm if x['lote'] is not None]
    total_bs = [x['monto_total_bs'] for x in norm if x['monto_total_bs'] is not None]

    lines = [f"✅ {n} lote{'s' if n != 1 else ''} procesados"]
    if precios:
        lines.append(f"💰 Precio: prom {sum(precios)/len(precios):.2f} Bs/kg "
                     f"(min {min(precios):.2f}, max {max(precios):.2f})")
    if pesos:
        lines.append(f"⚖️  Peso promedio: {sum(pesos)/len(pesos):.1f} kg")
    if lotes_n:
        lines.append(f"🏷️  Lotes: {min(lotes_n)} – {max(lotes_n)}")
    if total_bs:
        lines.append(f"💵 Total facturado: {sum(total_bs):,.2f} Bs")
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Appendea datos de lotes al Excel maestro desde JSON.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--json', dest='json_str')
    g.add_argument('--json-file', dest='json_file')
    ap.add_argument('--master', default='subastas_maestro.xlsx')
    ap.add_argument('--video', required=True)
    ap.add_argument('--date', default=None)
    args = ap.parse_args()

    if args.json_file:
        with open(args.json_file, encoding='utf-8') as f:
            data = json.load(f)
    else:
        data = json.loads(args.json_str)

    n = append_from_json(data, master=args.master,
                         source_video=args.video,
                         auction_date=args.date)

    print(f"✅ {n} filas agregadas a {args.master}", file=sys.stderr)
    print(summarize(data))


if __name__ == '__main__':
    main()
