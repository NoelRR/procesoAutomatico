## Rutas del proyecto

- Scripts: `/home/nole/Documents/Programacion/ProcesoAutomatico/CARPETA_SCRIPTS/`
- Videos: `/home/nole/Documents/Programacion/ProcesoAutomatico/CARPETA_VIDEOS/`
- Imágenes generadas: `/home/nole/Documents/Programacion/ProcesoAutomatico/imagenes/<fecha>/`
- Excel maestro: `/home/nole/Documents/Programacion/ProcesoAutomatico/ARCHIVO_MAESTRO.xlsx`
- Python del venv: `/home/nole/Documents/Programacion/ProcesoAutomatico/venv/bin/python3`

Respondé siempre en español. Usá siempre rutas absolutas en los comandos.

---

## Flujo principal: procesar un video

### Paso 1 — Extraer imágenes

```bash
/home/nole/Documents/Programacion/ProcesoAutomatico/venv/bin/python3 \
  /home/nole/Documents/Programacion/ProcesoAutomatico/CARPETA_SCRIPTS/extract_images.py \
  "/home/nole/Documents/Programacion/ProcesoAutomatico/CARPETA_VIDEOS/<video>.mp4" \
  --out /home/nole/Documents/Programacion/ProcesoAutomatico/imagenes/<fecha>/
```

Las imágenes se nombran por orden de aparición en el video: `lote_001.png`, `lote_002.png`, etc. Avisá al usuario cuando empiece y cuando termine.

### Paso 2 — Leer imágenes y escribir al Excel (tandas de 20)

Procesá de a 20 imágenes por sesión de Claude Code, en orden secuencial según el número en el nombre del archivo. Cuando terminás las 20, escribís al Excel y **te detenés**. Le decís al usuario exactamente esto:

> "✅ lotes X-Y guardados. **Abrí una sesión nueva** para continuar desde el lote Y+1."

Esto es obligatorio — nunca procesés más de 20 imágenes por sesión.

Para cada imagen leé el **recuadro negro en la esquina inferior derecha**:

```
┌─────────────────────────────────────────────────┐
│ LOTE  │ CANTIDAD │ PROCEDENCIA │ H. LLEGADA    │
│  65   │    1     │   MARBAN    │               │
│       │          │ PESO TOTAL  │    21:30      │
│       │          │    110      │               │
├─────────────────────────────────────────────────┤
│ PRECIO     Bs.      │     PESO PROMEDIO        │
│   24,90             │       110,00             │
├─────────────────────────────────────────────────┤
│ MONTO X UNIDAD      │   MONTO TOTAL A PAGAR    │
│   2.739,00  Bs      │       2.739,00           │
│     393,53  $us     │         393,53           │
│  Vaquilla │ NELORE  │    1 año                 │
└─────────────────────────────────────────────────┘
```

Si hay una **banda verde** con texto amarillo grande (LOTE X | CANTIDAD Y | EDAD Z / CATEGORIA W | RAZA V), usala para verificar raza, categoría y edad — si contradice al recuadro negro en esos campos, gana la banda verde.

**Formato JSON por lote:**

```json
{
  "lote": <entero>,
  "cantidad": <entero>,
  "categoria": "<ver lista de categorías>",
  "raza": "<Nelore, Brahman, Angus, Brangus, Mestiza, Gyr, etc.>",
  "edad": "<literal: '1 año', '2 meses', '1.5 años'>",
  "procedencia": "<ver lista abajo>",
  "peso_total": <decimal kg>,
  "peso_promedio": <decimal kg>,
  "precio": <decimal Bs/kg>,
  "monto_unidad_bs": <decimal>,
  "monto_unidad_us": <decimal>,
  "monto_total_bs": <decimal>,
  "monto_total_us": <decimal>,
  "h_llegada": "<HH:MM>"
}
```

Envolvé los lotes en `{"lotes": [...]}`.

**Escritura del JSON — sin pasar por el chat:**

NUNCA uses la herramienta `Write` para escribir el JSON. Construí los datos como variables Python y escribilos al archivo con un script inline via `Bash`. MUY IMPORTANTE: NO MOSTRAR NINGÚN JSON EN EL CHAT.

Usá este patrón:

```bash
/home/nole/Documents/Programacion/ProcesoAutomatico/venv/bin/python3 - <<'PYEOF'
import json

lotes = [
    {
        "lote": 52,
        # ... resto de campos
    },
    # ... más lotes
]

out = "/home/nole/Documents/Programacion/ProcesoAutomatico/imagenes/<fecha>/lotes_parcial.json"
with open(out, "w", encoding="utf-8") as f:
    json.dump({"lotes": lotes}, f, ensure_ascii=False, indent=2)

print(f"✅ {len(lotes)} lotes escritos a {out}")
PYEOF
```

**Llamada al writer después de cada tanda de 20:**

```bash
/home/nole/Documents/Programacion/ProcesoAutomatico/venv/bin/python3 \
  /home/nole/Documents/Programacion/ProcesoAutomatico/CARPETA_SCRIPTS/excel_writer.py \
  --json-file /home/nole/Documents/Programacion/ProcesoAutomatico/imagenes/<fecha>/lotes_parcial.json \
  --master /home/nole/Documents/Programacion/ProcesoAutomatico/ARCHIVO_MAESTRO.xlsx \
  --video <video>.mp4 \
  --date <YYYY-MM-DD>
```

### Paso 3 — Confirmar al usuario

Avisá cuando terminaste de procesar todas las imágenes.

---

## Reglas de lectura

**Números:** formato boliviano — coma decimal, punto de miles. `2.739,00` → `2739.00` en JSON.

**`procedencia`** — lista cerrada, siempre hay una provincia visible en el panel:
`Cercado`, `Vaca Diez`, `Ballivián`, `Yacuma`, `Moxos`, `Marban`, `Mamore`, `Itenez`

**Política de `null`:** el panel siempre tiene datos en todos los campos. Nunca pongas `null` por dificultad de lectura — leé con atención. Los únicos casos válidos son imagen cortada o campo físicamente tapado. Cuando el precio es `0,00`, los montos también son `0.0` (no `null`).

**Duplicados esperados:** es normal que aparezcan 2 imágenes del mismo lote (una con precio 0 al inicio, una con precio real al final). Procesalas todas — los ceros no afectan el sistema.

**Respuesta:** cuando procesés imágenes, **escribí el JSON via Bash inline** (ver patrón arriba) — nunca con la herramienta `Write`. Después llamá al writer. Al usuario solo confirmale con una línea corta: "✅ lotes X-Y guardados".

---

## Reglas de normalización de categoría

**Categorías válidas (lista cerrada):**
`Terneros`, `Toretes`, `Torillos`, `Vacas`, `Toros`, `Bueyes`, `Vaquillas`, `Novillos`

Solo se permite usar las categorías de la lista cerrada. Cualquier variante detectada se mapea a su categoría base:

- `Ternero`, `Ternera`, `Terneros`, `Terneras` → **Terneros**
- `Torete`, `Toretes` → **Toretes**
- `Torillo`, `Torillo rengo`, `Torillo invierno`, etc. → **Torillos**
- `Vaca`, `Vaca parida`, etc. → **Vacas**
- `Toro`, variantes → **Toros**
- `Buey`, variantes → **Bueyes**
- `Vaquilla`, variantes → **Vaquillas**
- `Novillo`, variantes → **Novillos**

**Regla general:** eliminar descriptores adicionales (estado, condición, estación, etc.) y conservar solo la categoría base válida.
