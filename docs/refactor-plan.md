# BliSolver 重构计划：基于 2phite/blisolver 的多源视频字幕摄取系统

> 状态：阶段 A（transcribe 后端替换为 whisper.cpp/Metal）已完成并端到端验证；
> 阶段 B-1（删除死代码 player-API 字幕回退、默认 cookie 改 chrome）已完成；
> 阶段 B-2（schema 1.0→1.1 增量扩展：Segment.source/confidence、Bundle.ocr）已完成；
> 阶段 C（硬字幕 OCR 子进程隔离 + detect_hardsubs）已完成并端到端验证
>   （BV1LD7U65Ew2, 109 烧录字幕 cues, 置信 ~0.99；引擎用 rapidocr-onnxruntime，
>   避开 paddlepaddle native runtime）。
> 阶段 D（fuse.py 多源融合）已完成并端到端验证：
>   provenance 落地（segments 带 source/confidence）+ ASR 幻觉检测 + OCR 异源交叉验证
>   （BV1LD7U65Ew2: ASR 67% 重复幻觉，69 个窗口被 OCR 证伪，source_reason 注记诊断）。
> 完整 7 阶段重构已完成；同源择优（§5.3）已实测删除（真实双源场景粒度不对齐、质量门控已做源选择、该层 ROI 为负）。
> 阶段 E（MCP 接口层）已完成并端到端验证：
>   probe_video / extract_transcript(异步 job) / get_transcript / get_timeline / get_visual_context
>   五工具全部调通（BV1dSKJ6wEVz：11s 完成，human-sub 86 段带 provenance）。
> 阶段 F（quality 门控定标）已完成并端到端验证：
>   punct_density 改为 source-aware（auto-sub 跳过——实测 AI 字幕天然无标点，
>   该指标只分来源不分质量；9 视频采样确认 dup/nonzh/cps 能区分好坏 AI）。
>   连带修复 _pick_track 把 ai-zh 错标 human-sub 的 bug（bilibili 把 ai-zh 放
>   subtitles 字段，字段不是来源信号，key 名才是）。
>   BV111o6BAEg4 重跑：ai-zh 328 段真字幕过门控，不再降级 whisper。
>   **计划 §9 全部 7 阶段完成。**
> 所有判定均有真实代码实测支撑。
> 本计划基于**真实代码实测**，非 README 假设。所有“保留/替换”判定均有实证支撑。

## 0. 一句话定位

以 `2phite/blisolver` 为骨架，换掉它的两个承重实现（bilibili fetcher 已是 yt-dlp+cookie 无需改；transcribe 已换 whisper.cpp/Metal），外挂它没有的两个 stage（硬字幕 OCR、多源融合）和一层 MCP 接口，做成一个 URL → 带溯源的可信逐字稿 的 Agent 摄取服务。

## 1. 实测确立的事实（阶段 A 产出）

以下结论全部来自对本仓库 `blisolver/` 的真实代码 + 真实视频 BV111o6BAEg4 的运行验证。

### 1.1 "有登录态就不需要爬"——成立，且比预想更彻底

`blisolver/subtitles.py` 的 `_acquire()` 本来就是 yt-dlp 路径：

```python
pick = _pick_track(info)              # 1) yt-dlp automatic_captions 拿 ai-zh
if pick is not None:
    return ...                        # 2) 拿到就返回，完事
from .player_api import part_segments # 3) 只有为空才回退到手爬
got = part_segments(canonical, settings, view=view)
```

`ydl_opts` 默认 `browser_cookies=True`。README 里"bilibili 不向 yt-dlp 暴露 AI 字幕"那句**已被推翻**：用 `BLISOLVER_COOKIES_BROWSER=chrome` 时 yt-dlp 直接返回 `ai-zh/ai-en/ai-ja/ai-es/ai-ar`。

→ **CC + AI 字幕两层零改写**。`player_api.part_segments`（字幕手爬回退）在我们环境下是死代码，可选删除以降维护面（`fetch_view`/`fetch_danmaku` 仍要保留）。

### 1.2 "能拿到字幕" ≠ "字幕可信"——质量门控必须保留

实测 BV111o6BAEg4：yt-dlp+chrome cookie 拿到的 ai-zh 只有 1 条 cue，内容是广告垃圾（"敲重点↓↓↓投降 包村 拥 威信 扫"），时长 27s vs 视频 893s。blisolver 的 tier-1 时长 sanity check（`last_end/duration ∈ [0.70, 1.10]`）**正确拒掉它**，降级到 whisper。

→ **`quality.py` 是 cookie 路径唯一缺的安全网，原样保留**。它纯跑 `list[Segment]`，与抓取格式完全解耦，换路径它一行不改。这是"不爬"之后仍需要的把关层——两件事不矛盾。

> ⚠️ **阶段 F 修正（2026-07）**：本节的"ai-zh 只有 1 条 cue 广告垃圾"实证基础已被阶段 F 推翻。
> 那是 bilibili 字幕惰性提取 + data-embedded 两个 bug 修复**前**的采样假象（详见 commit `97d3af5`）。
> bug 修复后重测 BV111o6BAEg4：ai-zh 是 **328 段真内容**（"觉得自己时间多的需要打发这件事情"…），
> 不是广告垃圾；但被 `punct_density=0.04` 误拒（AI 字幕天然无标点）。阶段 F 把 punct_density 改为
> source-aware（auto-sub 跳过）后，328 段过门控、不再降级 whisper。
> **质量门控仍需保留**的结论不变——`dup_ratio`/`nonzh_ratio`/`cps` 仍正确拒掉真坏的 AI（如歌曲
> 循环 ♪ 音乐符号 dup 0.88）。但"被污染成广告垃圾"这个具体场景实测未复现，风险表中相应条目降级。

### 1.3 transcribe 后端已替换为 whisper.cpp/Metal（drop-in）

原 `transcribe.py` 硬绑 faster-whisper + CUDA（`_register_cuda_dlls`、`device="cuda"`、`float16`），在 Apple M1 上跑不了。已替换为：

- `transcribe()` 函数体 → shell out 到 `whisper-cli`（whisper.cpp，Metal 后端），签名不变
- 新增 `_to_wav16k()`：ffmpeg 把缓存的音频容器转 16kHz 单声道 PCM WAV（whisper.cpp 前端要求）
- 复用 `subtitles.parse_srt` 解析 whisper-cli 的 SRT 输出回 `list[Segment]`
- 删除 `_register_cuda_dlls()`（CUDA 死代码）
- 修了重入 bug：输入已是 `.16k.wav` 时不再叠加后缀二次转码
- 新增 env：`BLISOLVER_WHISPER_MODEL`（GGML .bin 路径，默认 `/tmp/ggml-medium.bin`）、`BLISOLVER_WHISPER_CLI`

**验证**：363 segments，Metal 加速，与手动跑 whisper.cpp 结果逐字一致；端到端 ingest 产出 `out/BV111o6BAEg4-p1/{bundle.md, bundle.json}`。

### 1.4 降级链已端到端跑通

`blisolver ingest <url> --no-vision --no-frame-images` 完整流程：

```
URL → resolve → probe(元数据) → provider.fetch_subtitle(yt-dlp+cookie + 质量门控)
     → 字幕被拒 → _whisper(download_audio + _to_wav16k + whisper-cli)
     → build_bundle → write_bundle(out/<id>-p<n>/{bundle.md, bundle.json})
```

bundle.md frontmatter 记录了完整溯源：
```yaml
transcript_source: 'whisper (no usable subtitle (subtitle rejected: duration sanity 0.01 ...))'
```
bundle.json: `transcript.source=whisper`, `meta.cookies_used=True`, 363 segments 带时间戳。

## 2. 文件级 保留 / 替换 / 新增

基于实测，按文件落定。

| 文件 | 行数 | 判定 | 理由（实证） |
|---|---|---|---|
| `providers/bilibili.py` | 128 | **保留** | 委托给 subtitles.py，已是 yt-dlp 路径；fetch_metadata/fetch_danmaku 仍用 |
| `subtitles.py` | 308 | **保留**（可选删一段） | 已是 yt-dlp+cookie；`_acquire` 的 player_api 回退是死代码，可删 `part_segments` 调用 |
| `quality.py` | 66 | **保留原样** | 纯跑 `list[Segment]`，与抓取格式解耦；实测正确拒掉被污染字幕 |
| `schema.py` | 212 | **加字段**（additive, 1.0→1.1） | 见 §3，加 provenance/confidence/ocr track |
| `transcribe.py` | ✅**已替换** | 完成 | whisper.cpp/Metal shim，签名不变 |
| `frames.py` + `vision.py` | 137+148 | **拆分** | 6s抽帧+phash+VL描述 = "幻灯片笔记"stage，保留；但要和"硬字幕OCR"拆成两个独立 stage（见 §4） |
| `merge.py` | 306 | **扩展** | 已有时间轴对齐（chunk by frame boundaries）；但假定单一 transcript 源，要支持多源融合（见 §5） |
| `probe.py` | 44 | **保留** | 是 `probe_video` MCP 工具的好底座 |
| `player_api.py` | 370 | **部分可删** | `part_segments`（字幕手爬）死代码可删；`fetch_view`/`fetch_danmaku` 保留 |
| `config.py` | 196 | **加字段** | 加 OCR/融合/MCP 相关配置；默认 `cookies_browser` 从 firefox 改 chrome（或读 env） |
| `cache.py` | 45 | **保留** | fs_key/load_json/save_json 通用 |
| `cli.py` | 299 | **扩展** | 加 `--ocr`/`--fuse` 等开关；MCP 模式入口 |
| — | — | **新增 `ocr.py`** | 从 SubtitleExtractor 移植（见 §4） |
| — | — | **新增 `detect_hardsubs.py`** | 前置检测：底部区域文本持久性探测（见 §4） |
| — | — | **新增 `fuse.py`** | CC/ASR/OCR 多源逐句融合 + provenance + confidence（见 §5） |
| — | — | **新增 `mcp/`** | MCP 工具层（见 §6） |

## 3. Schema 扩展（1.0 → 1.1，additive）

`schema.py` 顶部写着"downstream Atlas depends on this shape — treat as stable API. Bump SCHEMA_VERSION on any breaking change"。扩展必须 additive。

### 3.1 Segment 加溯源（向后兼容）

```python
class Segment(BaseModel):
    start: float
    end: float
    text: str
    # 新增（1.1），默认 null，老消费者无感
    source: str | None = None       # "cc" | "ai-sub" | "whisper" | "ocr"
    confidence: float | None = None # [0,1]，OCR/ASR 可给，CC 默认 1.0
```

### 3.2 新增独立 OCR track（不塞进 Frame.ocr）

`Frame.ocr` 是幻灯片 OCR（画面文字：标题、图表、UI），语义 ≠ 烧录字幕。硬字幕是独立时间轴 track：

```python
class Bundle(BaseModel):
    # ... 原字段不变 ...
    transcript: Transcript           # 择优后的主逐字稿（不变）
    ocr: list[Segment] | None = None # 新增：硬字幕 track（独立时间轴，source="ocr"）
    # 或者：transcripts: list[Transcript] | None = None  # 多源并存，主 transcript 仍指择优那个
```

→ `SCHEMA_VERSION = "1.1"`，纯加字段，老 bundle.json 仍可读。

## 4. 新增 stage：硬字幕 OCR（移植 SubtitleExtractor）

### 4.1 为什么 blisolver 原视觉 stage 不够

blisolver 的 `frames.py`：每 6s 抽一帧 → phash 去重 → VL 模型读画面。这对 PPT/教程录屏合适，但**抓不到烧录字幕**：一条硬字幕只显示 2–4s，6s 采样可能整段跳过；VL 模型生成的是画面说明，不是可复现的 OCR 字幕轨道。

→ **把"幻灯片笔记"和"硬字幕 OCR"拆成两个独立 stage**，采样策略和模型都不同。

### 4.2 `detect_hardsubs.py`（前置检测，降级链里缺的一步）

在跑 OCR 前先廉价判断视频有没有烧录字幕，避免无意义全片 OCR：

- 底部 15% 区域采 N 帧（~每 30s 一帧）
- 对该区域做轻量文本检测（PaddleOCR det only，不识别）
- 测文本持久性：同一区域多帧有稳定文本 → 判定有硬字幕
- 输出 `has_hardsubs: bool` + 置信度

### 4.3 `ocr.py`（从 3aKHP/SubtitleExtractor 移植）

移植内容：
- 字幕区域检测（底部条带，可配）
- **2–5 FPS 动态采样**（不是 blisolver 的 6s；字幕显示 2–4s，必须密采）
- PaddleOCR（rec + det）
- 时序去重：相邻帧文本相似度合并，取代表帧时间戳
- 输出 `list[Segment]`（source="ocr"）

### 4.4 ⚠️ 依赖冲突风险（已从 SubtitleExtractor README 确认）

SubtitleExtractor 让 ASR 跑在**独立子进程**，注释明说"以隔离 Faster-Whisper/ctranslate2 与 PaddleOCR 的原生运行时依赖"。移植 OCR stage 时必须沿用进程隔离，否则 PaddleOCR 的 native runtime 会污染 blisolver 的 Python 环境。

→ `ocr.py` 设计为**子进程 shim**（和 transcribe.py shell out 到 whisper-cli 同模式）：blisolver 主进程通过 subprocess 调一个独立的 OCR worker 脚本，传视频路径、收 JSON。这样 PaddleOCR 装在独立 venv 里，不碰 blisolver 的依赖图。

## 5. 新增 stage：多源融合 `fuse.py`

这是整个系统里最 research-y 的核心，不是脚注。blisolver 现在假定单一 transcript 源（`Transcript` 单数），融合要支持多源。

### 5.1 三类源的融合语义不同

| 源对 | 关系 | 融合策略 |
|---|---|---|
| CC ↔ AI 字幕 | 同源择优 | ~~时间重叠 + 置信度加权，择优或拼接~~ **已删除**：真实双源测试（BV1dSKJ6wEVz）显示粒度不对齐（CC 长整句 vs AI 短词），0.80 相似阈值 95% 拒真；质量门控已做源选择，无需第二层。 |
| CC/ASR ↔ OCR | **异源插入**（画面文字 ≠ 语音） | OCR 作为独立 track，不合并进 transcript，按时间轴对齐 |
| ASR 自身多段 | 去重/平滑 | 已由 whisper segment 处理 |

### 5.2 产出契约

统一时间轴，每段带 `source` + `confidence`（见 §3.1）。主 `transcript` 字段仍是择优后的单一逐字稿（向后兼容），`ocr` 作为独立 track 并存。

### 5.3 融合算法（初版，可迭代）

> **阶段 F 删除注**：下方第 1-2 步描述的 CC/AI 同源择优已实现后又删除（见上表）。
> 删除原因：真实多源场景罕见、质量门控已择优、该合并层对下游 chunking 产生副作用。
> 当前 fuse 只保留：
>   - ASR 幻觉检测（whisper 重复模式）
>   - OCR 异源交叉验证（OCR track 独立）
> 其他部分保留作为历史设计记录。

1. CC/AI 按 start 时间排序合并；
2. 时间重叠段：若文本相似度 > 阈值 → 择置信度高者；不相似 → 都保留（可能一个是正文本一个是补充）；
3. OCR 段单独成 track，`merge.py` 的 `chunk()` 已支持按 frame boundaries 对齐，OCR 段可复用同一套 chunking。

## 6. MCP 接口层

把 blisolver 的 CLI 能力包装成 MCP 工具，供 Agent 调用。

| 工具 | 入参 | 出参 | 对应 blisolver 能力 |
|---|---|---|---|
| `probe_video(url)` | B站/YouTube URL | `ProbeResult` JSON（元数据，不碰媒体） | `probe.py` |
| `extract_transcript(url, mode)` | url, mode∈{auto,force_whisper,force_ocr} | job_id | `ingest`（异步） |
| `get_transcript(job_id)` | job_id | `{source, segments, quality_gate}` | 读 bundle.json |
| `get_timeline(job_id)` | job_id | 统一时间轴（含多源 provenance） | `fuse.py` 产出 |
| `get_visual_context(job_id)` | job_id | frames + ocr track | `frames.py` + `ocr.py` |

`probe_video` 同步、廉价，让 Agent 先估工作量再决定是否全量 ingest。`extract_transcript` 异步（whisper 要几十分钟），返 job_id，配 `get_transcript` 轮询。

## 7. 完整降级链（重构后）

```
人工 CC（yt-dlp+cookie）
  ↓ 不存在或质量门控不合格
B站 AI 字幕（yt-dlp+cookie，ai-zh 等）
  ↓ 不存在或质量门控不合格（实测：可能被污染成广告垃圾）
ASR（whisper.cpp/Metal，已跑通）
  ↓ 检测到硬字幕时并行执行（detect_hardsubs 判定）
OCR（PaddleOCR，子进程隔离）
  ↓
CC / ASR / OCR 时间轴融合（fuse.py，带 provenance + confidence）
  ↓
bundle.md + bundle.json（schema 1.1）+ MCP 可查
```

## 8. 风险登记（实测 + 推断）

| 风险 | 来源 | 状态 | 缓解 |
|---|---|---|---|
| faster-whisper/CUDA 在 M1 跑不了 | 实测 | ✅已解决 | 换 whisper.cpp/Metal shim |
| AI 字幕被污染成广告垃圾 | 实测 BV111o6BAEg4 | ✅已修正结论 | 阶段 F 重测推翻：是采样 bug 假象，真 ai-zh 是好内容；quality.py 仍拒真坏的（歌曲循环♪，dup 0.88） |
| PaddleOCR native runtime 污染 blisolver 依赖 | SubtitleExtractor README | 待验证 | ocr.py 子进程隔离 |
| 字幕时间戳超视频时长（1.86x） | 实测 | 待定 | tier-1 区间 [0.70,1.10] 可能偏严，需多视频采样后定标 |
| whisper medium 在 M1 要 ~40 分钟 | 实测 | 待优化 | 可换 small/ggml-large-v3 量化版；或后续接 SenseVoice |
| blisolver 是 0 star 单人项目 | GitHub 核实 | 接受 | 借设计不借代码栈；fork 后自主维护 |
| schema 破坏下游 | schema.py 注释 | 待定 | 1.0→1.1 纯加字段，老 bundle 仍可读 |

## 9. 执行顺序（建议）

1. ✅ **阶段 A**：transcribe.py 换 whisper.cpp — **已完成**
2. ✅ **阶段 B-1**：删 `player_api.part_segments` 死代码 + 默认 cookie browser 改 chrome — **已完成**
3. ✅ **阶段 B-2**：schema 1.0→1.1（加 Segment.source/confidence、Bundle.ocr）— **已完成**
4. ✅ **阶段 C**：移植 `ocr.py` + `detect_hardsubs.py`（子进程隔离）— **已完成并端到端验证**
5. ✅ **阶段 D**：`fuse.py` 多源融合（provenance 落地 + ASR 幻觉检测 + OCR 交叉验证）— **已完成**
6. ✅ **阶段 E**：MCP 接口层（probe_video/extract_transcript/get_transcript/get_timeline/get_visual_context）— **已完成并端到端验证**
7. ✅ **阶段 F**：多视频采样定标 quality 门控阈值（punct_density source-aware + ai-zh 来源标签修复）— **已完成**

每个阶段都应像阶段 A 一样：先用真实视频端到端验证，再固化到计划。

## 10. 关键命令备忘

```bash
# 端到端 ingest（已跑通）——在仓库根目录执行
cd /path/to/BliSolver
BLISOLVER_COOKIES_BROWSER=chrome \
BLISOLVER_WHISPER_MODEL=/tmp/ggml-medium.bin \
BLISOLVER_WHISPER_CLI=/opt/homebrew/bin/whisper-cli \
.venv/bin/blisolver ingest "<bili url>" --no-vision --no-frame-images

# 只 probe（廉价，不碰媒体）
BLISOLVER_COOKIES_BROWSER=chrome .venv/bin/blisolver probe "<bili url>"

# 直接拉 AI 字幕清单（验证 cookie 路径）
yt-dlp --cookies-from-browser chrome --list-subs "<bili url>"
```
