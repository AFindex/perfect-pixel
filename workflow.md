# Perfect Pixel Workflow

```mermaid
flowchart LR
  A["AI image<br/>PNG/JPG"] --> B["Preclean<br/>Pillow RGBA"]
  B --> C["Coarse background mask<br/>OpenCV / RMBG-2.0 / hybrid"]
  C --> D["Trimap<br/>OpenCV morphology"]
  D --> M["Outline masks<br/>subject + outline ring"]
  M --> E["Defringe<br/>PyMatting or nearest foreground"]
  E --> F["Pixel restore<br/>unfake CLI"]
  F --> G["Palette converge<br/>Pillow or pngquant"]
  G --> H["QA export<br/>sprite, preview, mask, report"]

  C -. debug .-> C1["01_near_bg.png<br/>02_connected_bg_mask.png"]
  D -. debug .-> D1["03_sure_fg.png<br/>04_edge_band.png<br/>05_trimap.png<br/>06_subject_mask.png<br/>06_subject_mask_rgba.png<br/>06_outline_mask.png<br/>06_outline_mask_rgba.png"]
  E -. debug .-> E1["07_clean_rgba.png"]
  H -. debug .-> H1["sprite.png<br/>sprite_x16.png<br/>report.json"]
```

## Reuse Map

| Stage | Reuse | Purpose |
| --- | --- | --- |
| Input normalization | Pillow | Read/write PNG and convert RGBA |
| Coarse background mask | OpenCV / RMBG-2.0 | Connected components, RMBG alpha matte, hybrid fusion, morphology |
| Outline masks | OpenCV | Export solid subject mask and dilated outline-ring mask |
| Defringe | PyMatting / scipy | Remove white matte color contamination around edges |
| Pixel restore | unfake CLI | Scale detection, grid snap, dominant/content-adaptive downscale |
| Palette converge | Pillow / pngquant | Reduce colors without dithering |
| QA export | Pillow / JSON | Nearest-neighbor preview and metadata |

This same pipeline powers both `perfect-pixel run` and `perfect-pixel web`.
