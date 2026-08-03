# 阶段 C 实施计划：硬字幕 OCR（子进程隔离）

> 状态：**已完成并端到端验证**（BV1LD7U65Ew2, 109 烧录字幕 cues, 置信 0.99）。
> 测试 318 passed。
>
> 验证视频：`https://www.bilibili.com/video/BV1LD7U65Ew2`（单 part, 261s, 1280x720, 30fps）
> 视频含底部烧录中文字幕（创作者旁白）+ 间夹 GitHub UI 英文。实测 RapidOCR 置信 0.94–0.99。

## 0. 真值已实测

- `.ocr-venv`（uv python 3.12）装 `rapidocr-onnxruntime` + `opencv-python-headless`：OK。
- `rapidocr_onnxruntime.RapidOCR` 在 M1 加载 0.1s、推理 0.58s。
- 下载视频底栏（0.78H–0.96H）采样 OCR 出烧录字幕句。

## 1. OCR 引擎选型（对计划的偏离，记录在案）

计划写"PaddleOCR"，但 `paddlepaddle` 原生 wheels 在 macOS arm16 支持差。改用
**`rapidocr-onnxruntime`**：基于 PaddleOCR 的 det/rec ONNX 模型，纯 `onnxruntime`+`opencv`+`numpy`，
**不引入 paddlepaddle native runtime**——正好规避计划风险清单里那个"native runtime 污染"。
质量等同（同模型），跨平台更干净。视为对计划 §4.3/§4.4 引擎层的实现选择，原则不变（子进程隔离）。

## 2. 架构：子进程隔离（与 transcribe→whisper-cli 同模式）

```
blisolver 主进程 (py3.14 venv, 无 paddle/ocr 依赖)
        │ subprocess(stdin=JSON req, stdout=JSON resp)
        ▼
scripts/ocr_worker.py  (跑在 .ocr-venv py3.12，唯一依赖 rapidocr+cv2+numpy)
```

协议：单行 JSON 请求 → 单行 JSON 响应（NB: 响应必须只一行，worker 的诊断走 stderr）。

## 3. 文件

- `scripts/ocr_worker.py`（新增，独立 venv 跑）
  - `mode=detect`：每 ~30s 采底栏 N 帧，OCR，测文本持久性 → `{has_hardsubs, confidence, sampled, positive}`
  - `mode=ocr`：按 fps（默认 3）采底栏，OCR 每帧，时序去重相邻相似文本 → `{segments:[{start,end,text,confidence}], frames}`
- `blisolver/detect_hardsubs.py`（新增 shim）：subprocess 调 worker detect，返回 `HardsubResult`
- `blisolver/ocr.py`（新增 shim）：subprocess 调 worker ocr，返回 `list[Segment]`（source="ocr"）
- `config.py`：加 OCR 字段（fps、band、detect 间隔、worker 路径、venv python、置信阈值）
- `cli.py`：加 `--ocr {auto,on,off}`（默认 auto：detect 先行，has_hardsubs 才跑全量 ocr），产物写 `Bundle.ocr`
- 测试：mock subprocess 验协议；测去重逻辑；测 config 默认值

## 4. 时序去重算法（worker 内）

1. 采样帧序列 `[(ts, boxes_texts_confs)]`
2. 归一化文本（去空白/标点）
3. 相邻帧文本相似度（difflib ratio）> 阈值 → 同组
4. 段：start=组首 ts，end=组尾 ts + 采样间隔；text=组内最高置信帧的文本
5. 过滤掉过短（<0.3s）或置信 < 阈值的段

## 5. 验收

- 端到端：`blisolver ingest <url> --ocr --no-vision --no-frame-images` → `Bundle.ocr` 非空，
  segments 带 source="ocr"、时间戳合理、文本与实测真值对齐。
- 测试套件全绿。