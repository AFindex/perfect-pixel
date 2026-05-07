# Perfect Pixel Workflow Visualizer

一个离线可打开的 Web 原型，用来可视化 AI 像素图到完美像素图的工作流。

CLI 参考见 [cli.md](./cli.md)。

## 打开

直接用浏览器打开：

```text
D:\Godot\projs\perfect_pixel\index.html
```

输入区支持拖入图片或点击选择图片。静态 `file://` 页面只能读取文件名和预览图，浏览器不会暴露完整磁盘路径，也不能直接执行 `python` 或 `unfake`。

Web 参数面板支持悬停 `?` 查看参数含义，也可以把当前算法参数保存为预设并随时读取。

`postcheck.py` 默认会请求最近邻预览放大 16 倍，但会把预览图最长边限制在 4096 像素以内。这样小像素图仍然能 x16 检查，大尺寸 clean 图不会因为生成超大预览拖慢 QA。

要启用“运行管线”，先初始化环境：

```powershell
python tools/cli.py init
```

如果要提前准备 RMBG-2.0 推理环境：

```powershell
python tools/cli.py init --with-rmbg
python tools/rmbg2.py doctor --json
```

需要预先下载并加载一次模型时可运行：

```powershell
python tools/cli.py init --with-rmbg --rmbg-warmup
```

RMBG-2.0 的 Hugging Face 权重需要接受模型许可；如果是 gated 访问，请先配置 `HF_TOKEN` 或完成 `huggingface-cli login`。

然后启动本地后端：

```powershell
python tools/server.py --host 127.0.0.1 --port 8765
```

然后打开：

```text
http://127.0.0.1:8765/
```

如果你继续使用 `file://` 页面，只要后端在 `127.0.0.1:8765` 运行，按钮也会通过本地接口执行。

## CLI

先初始化环境：

```powershell
python tools/cli.py init
```

之后可以直接用命令行跑整条管线：

```powershell
.\.venv\Scripts\perfect-pixel.exe run input.png -o build/perfect-pixel --subdir 1234
.\.venv\Scripts\perfect-pixel.exe web --host 127.0.0.1 --port 8765
.\.venv\Scripts\perfect-pixel.exe doctor
```

如果不想依赖全局命令，也可以继续直接：

```powershell
python tools/cli.py run input.png
```

## 处理模式

- 只清理：默认模式，只跑 `preclean.py -> postcheck.py`，把 `07_clean_rgba.png` 作为正式结果，不调用 `unfake`。
- 保真 unfake：调用 `unfake --scale 1 --no-snap --colors 256`，避免自动降采样和自动降色。
- 像素恢复：允许 `unfake` 自动检测 scale，用于确实要恢复像素网格的图；默认仍用 `--colors 256`，只有勾选“自动降色”才让 unfake 自己压色。

输出目录旁边的“选择”按钮需要本地后端运行，它会打开系统目录选择器。

## 描边相关输出

- `06_subject_mask.png`：主体实心 mask，白色是主体，黑色是背景。
- `06_subject_mask_rgba.png`：透明背景的主体填充 mask，主体区域白色不透明。
- `06_outline_mask_rgba.png`：透明背景的描边环，描边区域白色不透明，后续做描边效果优先用这个。
- `06_outline_mask.png`：黑底白线版描边环，主要用于检查。
- `04_edge_band.png`：白边清理用的内部边缘带，不建议拿它做最终描边。

## 可选精灵表重排

勾选 Web 里的“按 mask 切分并重排”，或在 CLI 加 `--arrange-sprites`，会在 `postcheck.py` 后追加第 08 步：

- `10_arranged_sprite.png`：按最终透明 mask 切分并统一 cell 后的整理版精灵图。
- `10_arranged_sprite_x16.png`：最近邻放大预览。
- `10_arranged_mask_rgba.png`：透明背景的同布局 mask，后续做描边或碰撞区域时优先用这个。
- `10_arranged_mask.png`：黑底白色版同布局 mask，主要用于检查。
- `10_arranged_elements/element_0001.png`：聚类后的单个大元素裁切图，每个元素单独保存。
- `10_arranged_elements/element_0001_mask_rgba.png`：对应单件的透明背景 mask，用于单件描边、碰撞或后续批处理。
- `10_clusters_debug.png`：聚类调试图，同色表示被判断为同一个元素。
- `10_components_debug.png`：原始 mask 小组件调试图。
- `10_arrange_report.json`：每个组件的原 bbox、目标 bbox、cell 尺寸、行列信息和单件导出路径。

常用 CLI 示例：

```powershell
python tools/cli.py run input.png --arrange-sprites --arrange-columns 4 --arrange-padding 2
```

默认 `--arrange-split-mode auto` 会先找 mask 小岛，再用 OpenCV 形态学合并和连通组件把同一元素里断开的装饰、箭头、震动线聚到一起。需要对照聚类算法时可试 `--arrange-split-mode agglomerative`、`--arrange-split-mode hdbscan` 或 `--arrange-split-mode affinity`；需要手动距离时用 `--arrange-split-mode clustered --arrange-cluster-gap 14`；需要旧逻辑时可改用 `--arrange-split-mode connected`。

## 当前管线

1. 导入与标准化：Pillow 读取输入，统一 RGBA。
2. 纯色背景识别：OpenCV 从边缘连通区域识别近似白底。
3. Mask 与 Trimap：OpenCV morphology 生成 sure foreground、edge band、trimap、主体 mask、描边 mask。
4. 白边去污染：PyMatting 或最近 sure foreground 颜色替换。
5. unfake 像素恢复：复用本机 `unfake` CLI。
6. 调色板收敛：Pillow quantize 或 pngquant。
7. 导出与 QA：true-res PNG、最近邻预览、debug 图、metadata。
8. 可选 Mask 切分重排：OpenCV 形态学合并/连通组件切分，可切换 scikit-learn 聚类对照，Pillow 统一 cell 排列，导出整理版 sprite、mask 和聚类单件。

## 可跑的最小命令链

```powershell
python tools/preclean.py input.png --output-dir build/perfect-pixel --bg-tolerance 14 --background-mode edges --alpha-threshold 128 --edge-contract 1 --outline-width 2
unfake build/perfect-pixel/07_clean_rgba.png -o build/perfect-pixel/08_unfake_pixel_raw.png --detect auto --method dominant --transparent-background --background-tolerance 14 --background-mode edges --cleanup morph,jaggy --auto-colors
python tools/postcheck.py build/perfect-pixel/08_unfake_pixel_raw.png --output-dir build/perfect-pixel --preview-scale 16
```

## unfake 参数来源

已按本机 `unfake --help` 确认可用参数：

- `--transparent-background`
- `--background-tolerance`
- `--background-mode edges|corners|midpoints`
- `--detect auto|runs|edge`
- `--method dominant|median|mode|mean|nearest|content-adaptive`
- `--cleanup morph,jaggy`
- `--auto-colors`
- `--colors`
- `--alpha-threshold`
