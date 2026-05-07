const state = {
  backgroundMode: "edges",
  processMode: "clean",
  previewUrl: "",
  selectedFile: null,
  openHistory: "",
};

const steps = {
  ingest: {
    phase: "阶段 01",
    title: "导入与标准化",
    summary:
      "统一输入图像格式，建立调试输出目录，为后续 mask、trimap、像素恢复留下可检查中间件。",
    reuse: "Pillow, pathlib",
    input: "AI 生成 PNG / JPG",
    output: "RGBA 图像, debug 目录",
    artifacts: ["00_input_rgba.png", "metadata.json"],
  },
  background: {
    phase: "阶段 02",
    title: "纯色背景识别",
    summary:
      "从四边估计近似背景色，只保留和画布边缘连通的近背景区域，避免误删主体里的白色高光。",
    reuse: "OpenCV connectedComponents / floodFill, Lab color distance",
    input: "RGBA 图像, 背景容差, edge/corner seed",
    output: "near_bg.png, connected_bg_mask.png",
    artifacts: ["01_near_bg.png", "02_connected_bg_mask.png"],
  },
  trimap: {
    phase: "阶段 03",
    title: "Mask 与 Trimap",
    summary:
      "用开闭运算修 mask，腐蚀得到 sure foreground，并导出主体 mask 与外扩描边 mask。",
    reuse: "OpenCV erode, dilate, morphologyEx",
    input: "connected background mask",
    output: "sure_fg.png, edge_band.png, trimap.png, outline_mask.png",
    artifacts: ["03_sure_fg.png", "04_edge_band.png", "05_trimap.png", "06_subject_mask.png", "06_outline_mask.png"],
  },
  defringe: {
    phase: "阶段 04",
    title: "白边去污染",
    summary:
      "对白底混色的边缘像素做去污染：优先用 PyMatting 估计前景色，MVP 可先用最近 sure foreground 颜色替换。",
    reuse: "PyMatting foreground estimation, scipy distance transform",
    input: "RGBA, trimap, sure foreground",
    output: "clean_rgba.png, hard_alpha.png",
    artifacts: ["06_foreground_estimate.png", "07_clean_rgba.png"],
  },
  unfake: {
    phase: "阶段 05",
    title: "unfake 像素恢复",
    summary:
      "复用你本机已安装的 unfake CLI，做 scale detection、grid snapping、downscale 和 morph/jaggy 清理。",
    reuse: "unfake CLI",
    input: "clean_rgba.png",
    output: "pixel_raw.png",
    artifacts: ["08_unfake_pixel_raw.png", "unfake.log"],
  },
  palette: {
    phase: "阶段 06",
    title: "调色板收敛",
    summary:
      "把低分辨率像素图压到目标色数或固定 palette，关闭抖动，避免重新生成半透明或脏色。",
    reuse: "Pillow Image.quantize, pngquant/libimagequant optional",
    input: "pixel_raw.png, palette.hex optional",
    output: "pixel_quantized.png",
    artifacts: ["09_pixel_quantized.png", "palette_used.hex"],
  },
  qa: {
    phase: "阶段 07",
    title: "导出与 QA",
    summary:
      "导出 true-res PNG、最近邻预览、mask、trimap 与流程 metadata；每一步都可回看，方便调容差和白边强度。",
    reuse: "Pillow nearest resize, JSON metadata",
    input: "pixel_quantized.png",
    output: "sprite.png, sprite_x16.png, report.json",
    artifacts: ["sprite.png", "sprite_x16.png", "report.json"],
  },
};

const qs = (selector) => document.querySelector(selector);
const qsa = (selector) => Array.from(document.querySelectorAll(selector));

const controls = {
  dropZone: qs("#dropZone"),
  filePicker: qs("#filePicker"),
  dropPreview: qs("#dropPreview"),
  dropInitial: qs("#dropInitial"),
  dropTitle: qs("#dropTitle"),
  dropMeta: qs("#dropMeta"),
  runPipelineButton: qs("#runPipelineButton"),
  runStatus: qs("#runStatus"),
  resultBody: qs("#resultBody"),
  pickOutputDirButton: qs("#pickOutputDirButton"),
  inputHistoryButton: qs("#inputHistoryButton"),
  inputHistoryPanel: qs("#inputHistoryPanel"),
  outputHistoryButton: qs("#outputHistoryButton"),
  outputHistoryPanel: qs("#outputHistoryPanel"),
  inputPath: qs("#inputPath"),
  outputDir: qs("#outputDir"),
  outputSubdir: qs("#outputSubdir"),
  decrementSubdirButton: qs("#decrementSubdirButton"),
  incrementSubdirButton: qs("#incrementSubdirButton"),
  nextFreeSubdirButton: qs("#nextFreeSubdirButton"),
  effectiveOutputDir: qs("#effectiveOutputDir"),
  presetName: qs("#presetName"),
  presetList: qs("#presetList"),
  savePresetButton: qs("#savePresetButton"),
  clearPresetsButton: qs("#clearPresetsButton"),
  bgTolerance: qs("#bgTolerance"),
  alphaThreshold: qs("#alphaThreshold"),
  edgeContract: qs("#edgeContract"),
  outlineWidth: qs("#outlineWidth"),
  method: qs("#method"),
  detect: qs("#detect"),
  autoColors: qs("#autoColors"),
  colors: qs("#colors"),
  colorsField: qs("#colorsField"),
  cleanupMorph: qs("#cleanupMorph"),
  cleanupJaggy: qs("#cleanupJaggy"),
  useTransparent: qs("#useTransparent"),
};

const API_BASE = window.location.protocol.startsWith("http")
  ? ""
  : "http://127.0.0.1:8765";

const HISTORY_LIMIT = 12;
const SUBDIR_STORAGE_KEY = "perfectPixel.outputSubdir";
const PRESET_STORAGE_KEY = "perfectPixel.parameterPresets";
const PRESET_LIMIT = 20;
const historyConfig = {
  input: {
    key: "perfectPixel.inputHistory",
    empty: "还没有输入文件历史",
    panel: () => controls.inputHistoryPanel,
    button: () => controls.inputHistoryButton,
    target: () => controls.inputPath,
  },
  output: {
    key: "perfectPixel.outputHistory",
    empty: "还没有输出目录历史",
    panel: () => controls.outputHistoryPanel,
    button: () => controls.outputHistoryButton,
    target: () => controls.outputDir,
  },
};

const helpTips = [
  {
    selector: "#outputDir",
    help: "只表示输出根目录。真正的产物会写入它下面的批次子目录，避免多次运行互相覆盖。",
  },
  {
    selector: "#outputSubdir",
    help: "每次运行的批次文件夹名，例如 1234。加减按钮只更新数字部分，下一空号会跳过已经存在的目录。",
  },
  {
    selector: "#processModeField",
    help: "只清理会保留 07_clean_rgba 作为正式结果；保真 unfake 会禁止缩放和网格吸附；像素恢复会让 unfake 尝试识别像素网格，可能更像像素图但也更容易改变细节。",
  },
  {
    selector: "#backgroundModeField",
    help: "决定从哪里取背景种子。edges 用整圈边缘，适合白底/纯色底；corners 更保守；midpoints 适合角落被主体占住但边中仍是背景的图。",
  },
  {
    selector: "#bgTolerance",
    help: "背景颜色容差。值越大，越多接近背景色的噪点会被抠除；过大可能把主体里的白色高光、浅色边缘也当背景删掉。",
  },
  {
    selector: "#alphaThreshold",
    help: "判断像素是否可见的 alpha 阈值。高一点会丢掉半透明脏边，低一点会保留更多边缘，但也可能留下灰白毛边。",
  },
  {
    selector: "#edgeContract",
    help: "对白边处理时把可靠前景向内收缩的像素数。值越大越强力去白边，但过大可能让角色/物体边缘变瘦或缺口变多。",
  },
  {
    selector: "#outlineWidth",
    help: "透明描边 mask 的外扩宽度。只影响 06_outline_mask_rgba 等 mask 输出，不会改变 sprite 本体；值越大后续描边越厚。",
  },
  {
    selector: "#method",
    help: "对应 unfake 的 method：从模糊/放大的区域回落到像素格时如何取色。nearest 最保守；dominant/mode 更像选主色；median/mean 更平滑但可能糊；content-adaptive 会更主动猜内容。",
  },
  {
    selector: "#detect",
    help: "对应 unfake 的像素 scale 检测方式。auto 自动选择；runs 看连续色块规律；edge 看边缘周期。检测错会导致网格错位、尺寸变化或细节被采样坏。",
  },
  {
    selector: "#autoColors",
    help: "让 unfake 自动决定降色。可能让颜色更像传统像素图，但也容易损失 AI 图里的微妙色阶；需要保真时建议关闭。",
  },
  {
    selector: "#colors",
    help: "限制最大可见颜色数。数值越低越像低色数像素图，文件也更规整；过低会断渐变、丢阴影和小色块。",
  },
  {
    selector: "#cleanupMorph",
    help: "开启形态学清理，倾向于修掉孤立噪点和小洞。适合脏白底；过强可能吃掉细小装饰、发丝或尖角。",
  },
  {
    selector: "#cleanupJaggy",
    help: "开启 jaggy 边缘修正，尝试整理锯齿边。可能让边缘更干净，也可能改变本来就需要保留的像素阶梯。",
  },
  {
    selector: "#useTransparent",
    help: "让 unfake 输出透明背景。关闭后可能保留背景色，适合某些需要实底贴图的流程；做 sprite 或抠图通常保持开启。",
  },
];

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 KB";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** index;
  return `${value.toFixed(value >= 10 || index === 0 ? 0 : 1)} ${units[index]}`;
}

function readHistory(kind) {
  const config = historyConfig[kind];
  if (!config) return [];
  try {
    const parsed = JSON.parse(localStorage.getItem(config.key) || "[]");
    return Array.isArray(parsed) ? parsed.filter((item) => typeof item === "string" && item.trim()) : [];
  } catch {
    return [];
  }
}

function writeHistory(kind, values) {
  const config = historyConfig[kind];
  if (!config) return;
  try {
    localStorage.setItem(config.key, JSON.stringify(values.slice(0, HISTORY_LIMIT)));
  } catch {
    // localStorage can be unavailable in some embedded contexts; the UI still works without history.
  }
}

function normalizeHistoryValue(value) {
  return String(value || "").trim();
}

function addHistory(kind, value) {
  const normalized = normalizeHistoryValue(value);
  if (!normalized) return;
  const next = [
    normalized,
    ...readHistory(kind).filter((item) => item.toLowerCase() !== normalized.toLowerCase()),
  ].slice(0, HISTORY_LIMIT);
  writeHistory(kind, next);
  renderHistoryPanel(kind);
}

function displayHistoryName(value) {
  const trimmed = value.replace(/[\\/]+$/, "");
  const parts = trimmed.split(/[\\/]/);
  return parts[parts.length - 1] || value;
}

function displayHistoryParent(value) {
  const trimmed = value.replace(/[\\/]+$/, "");
  const index = Math.max(trimmed.lastIndexOf("\\"), trimmed.lastIndexOf("/"));
  return index > 0 ? trimmed.slice(0, index) : "当前工作目录";
}

function closeHistoryPanels() {
  state.openHistory = "";
  Object.values(historyConfig).forEach((config) => {
    const panel = config.panel();
    const button = config.button();
    panel.hidden = true;
    button.classList.remove("is-active");
    button.setAttribute("aria-expanded", "false");
  });
}

function renderHistoryPanel(kind) {
  const config = historyConfig[kind];
  if (!config) return;
  const panel = config.panel();
  const values = readHistory(kind);
  const fragment = document.createDocumentFragment();

  if (!values.length) {
    const empty = document.createElement("p");
    empty.className = "history-empty";
    empty.textContent = config.empty;
    fragment.append(empty);
  } else {
    const list = document.createElement("div");
    list.className = "history-list";
    values.forEach((value) => {
      const item = document.createElement("button");
      item.className = "history-item";
      item.type = "button";
      item.title = value;

      const name = document.createElement("strong");
      name.textContent = displayHistoryName(value);
      const parent = document.createElement("span");
      parent.textContent = displayHistoryParent(value);
      item.append(name, parent);

      item.addEventListener("click", () => {
        config.target().value = value;
        addHistory(kind, value);
        closeHistoryPanels();
        renderCommand();
        showToast(kind === "input" ? "已填入历史输入" : "已填入历史目录");
      });
      list.append(item);
    });
    fragment.append(list);

    const footer = document.createElement("div");
    footer.className = "history-footer";
    const clear = document.createElement("button");
    clear.className = "history-clear";
    clear.type = "button";
    clear.textContent = "清空历史";
    clear.addEventListener("click", () => {
      writeHistory(kind, []);
      renderHistoryPanel(kind);
      showToast("历史已清空");
    });
    footer.append(clear);
    fragment.append(footer);
  }

  panel.replaceChildren(fragment);
}

function toggleHistoryPanel(kind) {
  const config = historyConfig[kind];
  if (!config) return;
  const shouldOpen = state.openHistory !== kind;
  closeHistoryPanels();
  if (!shouldOpen) return;

  renderHistoryPanel(kind);
  state.openHistory = kind;
  config.panel().hidden = false;
  config.button().classList.add("is-active");
  config.button().setAttribute("aria-expanded", "true");
}

function fieldLabelFor(target) {
  const wrapper = target.closest(".field, .range-field, .check-row, .preset-panel");
  if (!wrapper) return null;
  const labelRow = wrapper.querySelector(".field-label-row");
  if (labelRow) return labelRow.querySelector("span");
  return Array.from(wrapper.children).find((child) => child.tagName === "SPAN") || null;
}

function createHelpButton(help) {
  const button = document.createElement("button");
  button.className = "help-button";
  button.type = "button";
  button.textContent = "?";
  button.dataset.help = help;
  button.setAttribute("aria-label", help);
  ["click", "mousedown"].forEach((eventName) => {
    button.addEventListener(eventName, (event) => {
      event.preventDefault();
      event.stopPropagation();
    });
  });
  return button;
}

function attachHelpTips() {
  helpTips.forEach(({ selector, help }) => {
    const target = qs(selector);
    if (!target) return;
    const label = fieldLabelFor(target);
    if (!label || label.querySelector(".help-button")) return;
    label.append(createHelpButton(help));
  });
}

function sanitizeSubdir(value) {
  const cleaned = String(value || "")
    .trim()
    .replace(/[<>:"/\\|?*\x00-\x1f]/g, "-")
    .replace(/^\.+$/, "");
  return cleaned || "0001";
}

function getOutputBaseDir() {
  return controls.outputDir.value.trim() || "build/perfect-pixel";
}

function getOutputSubdir() {
  return sanitizeSubdir(controls.outputSubdir.value);
}

function joinOutputPath(base, subdir) {
  const normalizedBase = String(base || "build/perfect-pixel").replace(/[\\/]+$/, "");
  const separator = normalizedBase.includes("\\") && !normalizedBase.includes("/") ? "\\" : "/";
  return `${normalizedBase}${separator}${subdir}`;
}

function getEffectiveOutputDir() {
  return joinOutputPath(getOutputBaseDir(), getOutputSubdir());
}

function parseIndexedName(value) {
  const name = sanitizeSubdir(value);
  const match = name.match(/^(.*?)(\d+)(\D*)$/);
  if (!match) {
    return { name, prefix: "", numberText: "0001", number: 1, suffix: "" };
  }
  return {
    name,
    prefix: match[1],
    numberText: match[2],
    number: Number.parseInt(match[2], 10),
    suffix: match[3],
  };
}

function formatIndexedName(parts, number) {
  const nextNumber = Math.max(0, number);
  const padded = String(nextNumber).padStart(parts.numberText.length, "0");
  return `${parts.prefix}${padded}${parts.suffix}`;
}

function stepOutputSubdir(delta) {
  const parts = parseIndexedName(controls.outputSubdir.value);
  controls.outputSubdir.value = formatIndexedName(parts, parts.number + delta);
  rememberOutputSubdir();
  renderCommand();
}

function rememberOutputSubdir() {
  try {
    localStorage.setItem(SUBDIR_STORAGE_KEY, getOutputSubdir());
  } catch {
    // Non-critical; the current field value still drives this run.
  }
}

function restoreOutputSubdir() {
  try {
    const value = localStorage.getItem(SUBDIR_STORAGE_KEY);
    if (value) controls.outputSubdir.value = sanitizeSubdir(value);
  } catch {
    // Ignore storage failures in embedded browsers.
  }
}

function readPresets() {
  try {
    const parsed = JSON.parse(localStorage.getItem(PRESET_STORAGE_KEY) || "[]");
    return Array.isArray(parsed) ? parsed.filter((preset) => preset && preset.settings) : [];
  } catch {
    return [];
  }
}

function writePresets(presets) {
  try {
    localStorage.setItem(PRESET_STORAGE_KEY, JSON.stringify(presets.slice(0, PRESET_LIMIT)));
  } catch {
    showToast("预设保存失败");
  }
}

function collectParameterSettings() {
  return {
    processMode: state.processMode,
    backgroundMode: state.backgroundMode,
    bgTolerance: controls.bgTolerance.value,
    alphaThreshold: controls.alphaThreshold.value,
    edgeContract: controls.edgeContract.value,
    outlineWidth: controls.outlineWidth.value,
    method: controls.method.value,
    detect: controls.detect.value,
    autoColors: controls.autoColors.checked,
    colors: controls.colors.value,
    cleanupMorph: controls.cleanupMorph.checked,
    cleanupJaggy: controls.cleanupJaggy.checked,
    useTransparent: controls.useTransparent.checked,
  };
}

function setSegmentValue(setting, value) {
  state[setting] = value;
  qsa(`[data-setting="${setting}"]`).forEach((item) => {
    item.classList.toggle("is-active", item.dataset.value === value);
  });
}

function applyParameterSettings(settings) {
  setSegmentValue("processMode", settings.processMode || "clean");
  setSegmentValue("backgroundMode", settings.backgroundMode || "edges");
  controls.bgTolerance.value = settings.bgTolerance ?? "14";
  controls.alphaThreshold.value = settings.alphaThreshold ?? "128";
  controls.edgeContract.value = settings.edgeContract ?? "1";
  controls.outlineWidth.value = settings.outlineWidth ?? "2";
  controls.method.value = settings.method || "nearest";
  controls.detect.value = settings.detect || "auto";
  controls.autoColors.checked = Boolean(settings.autoColors);
  controls.colors.value = settings.colors ?? "256";
  controls.cleanupMorph.checked = Boolean(settings.cleanupMorph);
  controls.cleanupJaggy.checked = Boolean(settings.cleanupJaggy);
  controls.useTransparent.checked = settings.useTransparent !== false;
  renderCommand();
}

function presetSummary(settings) {
  const modeLabel = {
    clean: "只清理",
    safe: "保真 unfake",
    pixel: "像素恢复",
  }[settings.processMode] || settings.processMode;
  return [
    modeLabel,
    `bg ${settings.bgTolerance}`,
    `alpha ${settings.alphaThreshold}`,
    `edge ${settings.edgeContract}`,
    `outline ${settings.outlineWidth}`,
    settings.processMode === "clean" ? "" : `${settings.method}/${settings.detect}`,
  ]
    .filter(Boolean)
    .join(" · ");
}

function renderPresetList() {
  const presets = readPresets();
  const fragment = document.createDocumentFragment();

  if (!presets.length) {
    const empty = document.createElement("p");
    empty.className = "preset-empty";
    empty.textContent = "还没有保存过参数预设";
    fragment.append(empty);
    controls.presetList.replaceChildren(fragment);
    return;
  }

  presets.forEach((preset) => {
    const item = document.createElement("div");
    item.className = "preset-item";

    const meta = document.createElement("div");
    meta.className = "preset-meta";
    const name = document.createElement("strong");
    name.textContent = preset.name || "未命名预设";
    const detail = document.createElement("span");
    detail.textContent = presetSummary(preset.settings);
    detail.title = detail.textContent;
    meta.append(name, detail);

    const load = document.createElement("button");
    load.className = "mini-button";
    load.type = "button";
    load.textContent = "读取";
    load.addEventListener("click", () => {
      applyParameterSettings(preset.settings);
      showToast(`已读取预设：${preset.name || "未命名预设"}`);
    });

    item.append(meta, load);
    fragment.append(item);
  });

  controls.presetList.replaceChildren(fragment);
}

function saveCurrentPreset() {
  const timestamp = new Date();
  const fallbackName = `预设 ${timestamp.toLocaleString("zh-CN", { hour12: false })}`;
  const name = controls.presetName.value.trim() || fallbackName;
  const preset = {
    id: `${timestamp.getTime()}-${Math.random().toString(16).slice(2)}`,
    name,
    savedAt: timestamp.toISOString(),
    settings: collectParameterSettings(),
  };
  const next = [
    preset,
    ...readPresets().filter((item) => (item.name || "").toLowerCase() !== name.toLowerCase()),
  ];
  writePresets(next);
  controls.presetName.value = "";
  renderPresetList();
  showToast("参数预设已保存");
}

function clearPresets() {
  writePresets([]);
  renderPresetList();
  showToast("参数预设已清空");
}

function quotePath(value) {
  const trimmed = value.trim() || "input.png";
  return `"${trimmed.replaceAll('"', '\\"')}"`;
}

function getCleanupOptions() {
  const options = [];
  if (controls.cleanupMorph.checked) options.push("morph");
  if (controls.cleanupJaggy.checked) options.push("jaggy");
  return options;
}

function shouldRunUnfake() {
  return state.processMode !== "clean";
}

function buildCommand() {
  const input = controls.inputPath.value.trim() || "input.png";
  const outputDir = getEffectiveOutputDir();
  const outputBase = outputDir.replace(/[\\/]$/, "");
  const cleanOutput = `${outputBase}/07_clean_rgba.png`;
  const unfakeOutput = `${outputBase}/08_unfake_pixel_raw.png`;
  const postInput = shouldRunUnfake() ? unfakeOutput : cleanOutput;
  const cleanup = getCleanupOptions();
  const postColors = controls.autoColors.checked ? "" : ` --colors ${controls.colors.value}`;
  const lines = [
    "# 01-04 先跑 preclean.py，生成干净透明底 07_clean_rgba.png",
    `python tools/preclean.py ${quotePath(input)} --output-dir ${quotePath(outputDir)} --bg-tolerance ${controls.bgTolerance.value} --background-mode ${state.backgroundMode} --alpha-threshold ${controls.alphaThreshold.value} --edge-contract ${controls.edgeContract.value} --outline-width ${controls.outlineWidth.value}`,
  ];

  if (shouldRunUnfake()) {
    const unfakeCommand = [
      "unfake",
      quotePath(cleanOutput),
      "-o",
      quotePath(unfakeOutput),
      "--method",
      controls.method.value,
      "--background-mode",
      state.backgroundMode,
      "--background-tolerance",
      controls.bgTolerance.value,
      "--alpha-threshold",
      controls.alphaThreshold.value,
      controls.useTransparent.checked ? "--transparent-background" : "",
      controls.autoColors.checked ? "--auto-colors" : `--colors ${controls.colors.value}`,
      cleanup.length ? `--cleanup ${cleanup.join(",")}` : "",
    ];

    if (state.processMode === "safe") {
      unfakeCommand.push("--scale", "1", "--no-snap");
    } else {
      unfakeCommand.push("--detect", controls.detect.value);
    }

    lines.push(
      "",
      state.processMode === "safe"
        ? "# 05 保真 unfake：强制 scale=1，不降采样，不自动降色"
        : "# 05 像素恢复：允许 unfake 自动检测 scale 并恢复像素网格",
      unfakeCommand
      .filter(Boolean)
      .join(" "),
    );
  } else {
    lines.push("", "# 05 跳过 unfake，直接把 07_clean_rgba.png 作为正式结果");
  }

  lines.push(
    "",
    "# 06-07 跑 postcheck.py 生成 sprite、最近邻预览和 report",
    `python tools/postcheck.py ${quotePath(postInput)} --output-dir ${quotePath(outputDir)} --preview-scale 16${shouldRunUnfake() ? postColors : ""}`,
  );

  return lines.join("\n");
}

function syncModeControls() {
  const disabled = !shouldRunUnfake();
  [
    controls.method,
    controls.detect,
    controls.autoColors,
    controls.colors,
    controls.cleanupMorph,
    controls.cleanupJaggy,
  ].forEach((control) => {
    control.disabled = disabled;
  });
  controls.colorsField.hidden = disabled || controls.autoColors.checked;
}

function renderCommand() {
  const effectiveOutputDir = getEffectiveOutputDir();
  qs("#commandOutput").textContent = buildCommand();
  controls.effectiveOutputDir.textContent = `实际输出：${effectiveOutputDir}`;
  controls.effectiveOutputDir.title = effectiveOutputDir;
  qs("#bgToleranceValue").textContent = controls.bgTolerance.value;
  qs("#alphaThresholdValue").textContent = controls.alphaThreshold.value;
  qs("#edgeContractValue").textContent = controls.edgeContract.value;
  qs("#outlineWidthValue").textContent = controls.outlineWidth.value;
  syncModeControls();
}

function useDroppedFile(file) {
  if (!file || !file.type.startsWith("image/")) {
    showToast("只接受图片文件");
    return;
  }

  if (state.previewUrl) {
    URL.revokeObjectURL(state.previewUrl);
  }
  state.previewUrl = URL.createObjectURL(file);
  state.selectedFile = file;

  controls.dropPreview.src = state.previewUrl;
  controls.dropPreview.hidden = false;
  controls.dropInitial.hidden = true;
  controls.dropTitle.textContent = file.name;
  controls.dropMeta.textContent = `${file.type || "image"} · ${formatBytes(file.size)}`;
  controls.inputPath.value = file.name;
  addHistory("input", file.name);
  renderCommand();
  showToast("图片已载入预览");
}

function appendRunSettings(form) {
  form.append("input_path", controls.inputPath.value.trim() || "input.png");
  form.append("output_dir", getEffectiveOutputDir());
  form.append("process_mode", state.processMode);
  form.append("bg_tolerance", controls.bgTolerance.value);
  form.append("background_mode", state.backgroundMode);
  form.append("alpha_threshold", controls.alphaThreshold.value);
  form.append("edge_contract", controls.edgeContract.value);
  form.append("outline_width", controls.outlineWidth.value);
  form.append("method", controls.method.value);
  form.append("detect", controls.detect.value);
  form.append("auto_colors", controls.autoColors.checked ? "true" : "false");
  form.append("colors", controls.colors.value);
  form.append("transparent_background", controls.useTransparent.checked ? "true" : "false");
  form.append("cleanup", getCleanupOptions().join(","));
}

function setRunStatus(label) {
  controls.runStatus.textContent = label;
}

function commandToString(command) {
  return command
    .map((part) => {
      const text = String(part);
      return /\s/.test(text) ? `"${text.replaceAll('"', '\\"')}"` : text;
    })
    .join(" ");
}

function renderResult(data) {
  const artifacts = data.artifacts || {};
  const report = data.report || {};
  const links = [
    ["clean", "干净抠图 07"],
    ["sprite", state.processMode === "clean" ? "正式源图" : "像素源图"],
    ["preview", "最近邻预览"],
    ["pixelRaw", "unfake 输出"],
    ["subjectMaskRgba", "透明填充 mask"],
    ["subjectMask", "黑白填充 mask"],
    ["outlineMaskRgba", "透明描边 mask"],
    ["outlineMask", "黑白描边 mask"],
    ["mask", "背景 mask"],
    ["trimap", "trimap"],
    ["report", "report.json"],
  ].filter(([key]) => artifacts[key]);
  const visuals = [
    ["preview", "最近邻预览", "放大检查像素边缘"],
    ["clean", "07 clean", "干净透明源图"],
    ["sprite", state.processMode === "clean" ? "正式源图" : "像素源图", "最终进入项目的 PNG"],
    ["subjectMaskRgba", "填充 mask", "透明背景，主体区域"],
    ["outlineMaskRgba", "描边 mask", "透明背景，外扩描边环"],
    ["mask", "背景 mask", "黑白调试图"],
    ["trimap", "trimap", "前景 / 边缘 / 背景"],
  ].filter(([key]) => artifacts[key]);

  const body = document.createDocumentFragment();

  const summary = document.createElement("div");
  summary.className = "run-summary";
  [
    ["模式", data.processMode === "clean" ? "只清理" : data.processMode],
    ["尺寸", Array.isArray(report.size) ? `${report.size[0]} x ${report.size[1]}` : "-"],
    ["可见色", Number.isFinite(report.visible_color_count) ? String(report.visible_color_count) : "-"],
    ["输出", data.outputDir || "-"],
  ].forEach(([label, value]) => {
    const item = document.createElement("div");
    item.className = "summary-item";
    const title = document.createElement("span");
    title.textContent = label;
    const detail = document.createElement("strong");
    detail.textContent = value;
    detail.title = value;
    item.append(title, detail);
    summary.append(item);
  });
  body.append(summary);

  if (visuals.length) {
    const visualGrid = document.createElement("div");
    visualGrid.className = "visual-grid";
    visuals.forEach(([key, label, note]) => {
      const item = artifacts[key];
      const card = document.createElement("a");
      card.className = "visual-card";
      card.href = `${API_BASE}${item.url}`;
      card.target = "_blank";
      card.rel = "noreferrer";

      const thumb = document.createElement("div");
      thumb.className = "visual-thumb";
      const img = document.createElement("img");
      img.src = `${API_BASE}${item.url}`;
      img.alt = label;
      img.loading = "lazy";
      thumb.append(img);

      const meta = document.createElement("div");
      meta.className = "visual-meta";
      const title = document.createElement("strong");
      title.textContent = label;
      const detail = document.createElement("span");
      detail.textContent = note;
      meta.append(title, detail);

      card.append(thumb, meta);
      visualGrid.append(card);
    });
    body.append(visualGrid);
  }

  const linkBox = document.createElement("div");
  linkBox.className = "result-links";
  links.forEach(([key, label]) => {
    const item = artifacts[key];
    const link = document.createElement("a");
    link.href = `${API_BASE}${item.url}`;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = label;
    const path = document.createElement("span");
    path.textContent = item.displayPath;
    link.append(path);
    linkBox.append(link);
  });
  body.append(linkBox);

  const log = document.createElement("pre");
  log.className = "result-log";
  log.textContent = (data.logs || [])
    .map((entry) => {
      const stdout = entry.stdout?.trim();
      const stderr = entry.stderr?.trim();
      return [
        `$ ${commandToString(entry.command)}`,
        stdout ? stdout : "",
        stderr ? stderr : "",
      ]
        .filter(Boolean)
        .join("\n");
    })
    .join("\n\n");
  body.append(log);

  controls.resultBody.replaceChildren(body);
}

function renderRunError(message, data) {
  const log = document.createElement("pre");
  log.className = "result-log";
  const detail = data?.logs
    ? data.logs
        .map((entry) => [commandToString(entry.command), entry.stdout, entry.stderr].filter(Boolean).join("\n"))
        .join("\n\n")
    : "";
  log.textContent = [message, detail].filter(Boolean).join("\n\n");
  controls.resultBody.replaceChildren(log);
}

async function runPipeline() {
  if (!state.selectedFile && !controls.inputPath.value.trim()) {
    showToast("先拖入图片或填写输入路径");
    return;
  }

  const form = new FormData();
  if (state.selectedFile) {
    form.append("image", state.selectedFile, state.selectedFile.name);
  }
  appendRunSettings(form);

  controls.runPipelineButton.disabled = true;
  controls.runPipelineButton.textContent = "运行中";
  setRunStatus("运行中");
  controls.resultBody.innerHTML = '<p class="empty-state">正在执行 preclean -> unfake -> postcheck...</p>';

  try {
    const response = await fetch(`${API_BASE}/api/run`, {
      method: "POST",
      body: form,
    });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      throw Object.assign(new Error(data.error || "运行失败"), { data });
    }
    setRunStatus("完成");
    addHistory("input", data.input || controls.inputPath.value);
    addHistory("output", getOutputBaseDir());
    rememberOutputSubdir();
    renderResult(data);
    showToast("管线执行完成");
  } catch (error) {
    setRunStatus("失败");
    renderRunError(
      `${error.message || "运行失败"}。如果当前是 file:// 页面，请先启动 python tools/server.py 并打开 http://127.0.0.1:8765/。`,
      error.data,
    );
    showToast("管线执行失败");
  } finally {
    controls.runPipelineButton.disabled = false;
    controls.runPipelineButton.textContent = "运行管线";
  }
}

async function pickOutputDirectory() {
  controls.pickOutputDirButton.disabled = true;
  try {
    const url = new URL(`${API_BASE}/api/pick-output-dir`);
    url.searchParams.set("initial", controls.outputDir.value.trim() || "build/perfect-pixel");
    const response = await fetch(url);
    const data = await response.json();
    if (!response.ok || !data.ok) {
      throw new Error(data.error || "选择目录失败");
    }
    if (data.path) {
      controls.outputDir.value = data.path;
      addHistory("output", data.path);
      renderCommand();
      showToast("输出目录已选择");
    }
  } catch (error) {
    showToast("请先启动本地后端再选择目录");
  } finally {
    controls.pickOutputDirButton.disabled = false;
  }
}

async function pickNextFreeSubdir() {
  controls.nextFreeSubdirButton.disabled = true;
  try {
    const url = new URL(`${API_BASE}/api/next-output-subdir`);
    url.searchParams.set("base", getOutputBaseDir());
    url.searchParams.set("current", getOutputSubdir());
    const response = await fetch(url);
    const data = await response.json();
    if (!response.ok || !data.ok) {
      throw new Error(data.error || "查找空号失败");
    }
    controls.outputSubdir.value = sanitizeSubdir(data.subdir);
    rememberOutputSubdir();
    renderCommand();
    showToast("已切到下一空号");
  } catch {
    stepOutputSubdir(1);
    showToast("已本地加一");
  } finally {
    controls.nextFreeSubdirButton.disabled = false;
  }
}

function selectStep(key) {
  const step = steps[key];
  if (!step) return;

  qsa(".stage-card").forEach((card) => {
    card.classList.toggle("is-selected", card.dataset.step === key);
  });

  qs("#detailPhase").textContent = step.phase;
  qs("#detailTitle").textContent = step.title;
  qs("#detailSummary").textContent = step.summary;
  qs("#detailReuse").textContent = step.reuse;
  qs("#detailInput").textContent = step.input;
  qs("#detailOutput").textContent = step.output;

  const strip = qs("#artifactStrip");
  strip.replaceChildren(
    ...step.artifacts.map((artifact) => {
      const item = document.createElement("span");
      item.className = "artifact";
      item.textContent = artifact;
      return item;
    }),
  );
}

function showToast(message) {
  const toast = qs("#toast");
  toast.textContent = message;
  toast.classList.add("is-visible");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("is-visible"), 1800);
}

function exportPlan() {
  const payload = {
    name: "perfect-pixel-workflow",
    stages: Object.values(steps).map((step) => ({
      phase: step.phase,
      title: step.title,
      reuse: step.reuse,
      input: step.input,
      output: step.output,
      artifacts: step.artifacts,
    })),
    command: buildCommand(),
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "perfect-pixel-workflow.json";
  link.click();
  URL.revokeObjectURL(url);
  showToast("方案 JSON 已生成");
}

function bindEvents() {
  document.addEventListener("dragover", (event) => {
    event.preventDefault();
  });
  document.addEventListener("drop", (event) => {
    event.preventDefault();
  });
  document.addEventListener("click", closeHistoryPanels);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeHistoryPanels();
  });

  Object.keys(historyConfig).forEach((kind) => {
    const config = historyConfig[kind];
    config.button().addEventListener("click", (event) => {
      event.stopPropagation();
      toggleHistoryPanel(kind);
    });
    config.panel().addEventListener("click", (event) => {
      event.stopPropagation();
    });
  });

  controls.dropZone.addEventListener("click", () => controls.filePicker.click());
  controls.dropZone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      controls.filePicker.click();
    }
  });
  controls.filePicker.addEventListener("change", () => {
    useDroppedFile(controls.filePicker.files?.[0]);
    controls.filePicker.value = "";
  });
  ["dragenter", "dragover"].forEach((eventName) => {
    controls.dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      controls.dropZone.classList.add("is-dragging");
    });
  });
  ["dragleave", "drop"].forEach((eventName) => {
    controls.dropZone.addEventListener(eventName, () => {
      controls.dropZone.classList.remove("is-dragging");
    });
  });
  controls.dropZone.addEventListener("drop", (event) => {
    event.preventDefault();
    event.stopPropagation();
    useDroppedFile(event.dataTransfer?.files?.[0]);
  });

  qsa(".stage-card").forEach((card) => {
    card.addEventListener("click", () => selectStep(card.dataset.step));
  });

  qsa(".segment").forEach((segment) => {
    segment.addEventListener("click", () => {
      const { setting, value } = segment.dataset;
      setSegmentValue(setting, value);
      renderCommand();
    });
  });

  Object.values(controls).forEach((control) => {
    if (!control || control === controls.colorsField) return;
    control.addEventListener("input", renderCommand);
    control.addEventListener("change", renderCommand);
  });
  controls.inputPath.addEventListener("change", () => addHistory("input", controls.inputPath.value));
  controls.outputDir.addEventListener("change", () => addHistory("output", controls.outputDir.value));
  controls.outputSubdir.addEventListener("change", () => {
    controls.outputSubdir.value = getOutputSubdir();
    rememberOutputSubdir();
    renderCommand();
  });
  controls.decrementSubdirButton.addEventListener("click", () => stepOutputSubdir(-1));
  controls.incrementSubdirButton.addEventListener("click", () => stepOutputSubdir(1));
  controls.nextFreeSubdirButton.addEventListener("click", pickNextFreeSubdir);
  controls.savePresetButton.addEventListener("click", saveCurrentPreset);
  controls.clearPresetsButton.addEventListener("click", clearPresets);
  controls.presetName.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      saveCurrentPreset();
    }
  });

  qs("#copyCommandButton").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(buildCommand());
      showToast("命令已复制");
    } catch {
      showToast("复制失败，请手动选中命令");
    }
  });

  controls.runPipelineButton.addEventListener("click", runPipeline);
  controls.pickOutputDirButton.addEventListener("click", pickOutputDirectory);
  qs("#exportPlanButton").addEventListener("click", exportPlan);
}

bindEvents();
attachHelpTips();
restoreOutputSubdir();
renderHistoryPanel("input");
renderHistoryPanel("output");
renderPresetList();
selectStep("ingest");
renderCommand();
