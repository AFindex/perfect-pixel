# Perfect Pixel CLI

这套 CLI 和 Web 共用同一条管线，区别只是入口不同。

## 初始化

```powershell
python tools/cli.py init
```

这会做两件事：

1. 创建 `.venv`
2. 安装 Python 依赖和本项目的可编辑安装

初始化后，你可以直接用：

```powershell
.venv\Scripts\perfect-pixel.exe doctor
.venv\Scripts\perfect-pixel.exe run input.png -o build/perfect-pixel --subdir 1234
```

如果你先激活 `.venv`，就可以直接输入 `perfect-pixel ...`。

如果你想完全不激活 venv，也可以继续直接：

```powershell
python tools/cli.py run input.png
```

## 常用命令

### `doctor`

检查环境、`unfake`、虚拟环境状态。

```powershell
.\.venv\Scripts\perfect-pixel.exe doctor
```

### `run`

直接跑预处理、`unfake`、后处理。

```powershell
.\.venv\Scripts\perfect-pixel.exe run input.png `
  -o build/perfect-pixel `
  --subdir 1234
```

常用参数：

- `--process-mode clean|safe|pixel`
- `--next-subdir`
- `--bg-tolerance`
- `--background-mode`
- `--outline-width`
- `--auto-colors`
- `--arrange-sprites`
- `--arrange-columns`
- `--arrange-padding`
- `--arrange-min-area`
- `--arrange-merge-gap`
- `--max-preview-side`
- `--json`

QA 预览默认请求 `--preview-scale 16`，同时用 `--max-preview-side 4096` 自动限制最长边。大图会降成 x1/x2 预览，避免导出阶段生成几亿像素的大 PNG。

把 AI 生成时散乱排布的多个元素整理成规则精灵表：

```powershell
.\.venv\Scripts\perfect-pixel.exe run input.png `
  -o build/perfect-pixel `
  --subdir 1234 `
  --arrange-sprites `
  --arrange-columns 4 `
  --arrange-padding 2 `
  --arrange-merge-gap 2
```

重排阶段会根据最终 `sprite.png` 的透明 mask 找组件，输出：

- `10_arranged_sprite.png`
- `10_arranged_sprite_x16.png`
- `10_arranged_mask_rgba.png`
- `10_arranged_mask.png`
- `10_arrange_report.json`

### `web`

启动网页版本。

```powershell
.\.venv\Scripts\perfect-pixel.exe web --host 127.0.0.1 --port 8765
```

## 输出目录规则

CLI 默认使用：

```text
输出根目录 / 批次子目录
```

例如：

```text
build/perfect-pixel/1234
```

如果子目录已经存在，可以直接用：

```powershell
perfect-pixel run input.png --output-root build/perfect-pixel --next-subdir
```

## 依赖

- Python
- `unfake` 命令行
- `flask`, `numpy`, `opencv-python`, `pillow`, `scipy`
