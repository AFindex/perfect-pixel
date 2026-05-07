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
- `--json`

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
