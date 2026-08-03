<div align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=timeGradient&height=250&section=header&text=BliSolver&fontSize=90&animation=fadeIn&fontAlignY=38&desc=Atlas%20Knowledge%20Base%20Ingestion%20Front-Door&descAlignY=55&descAlign=50" alt="BliSolver Banner">
  <br>
  <p>
    <a href="README.md">English</a> | <a href="README_zh.md">简体中文</a>
  </p>
</div>

---

**BliSolver** 是为 **bilibili.com** 和 **YouTube** 打造的强大视频摄取工具（受上游 Harvest 启发）。给定一个视频 URL，它能生成一个时间轴对齐、自包含的**数据包 (Bundle)**：

- 📝 **原声字幕** (复用平台高可信字幕，或降级使用 faster-whisper 离线转录)
- 🖼️ **逐帧视觉笔记** (通过本地视觉大模型实现 OCR + 图表/幻灯片描述)

它的流程始于 URL，终于 `out/<安全的视频标题> [<id>-p<part>]/` 目录，其中包含 `bundle.md`、`bundle.json` 和 `frames/`。它**不会**在当前阶段做总结或实体提取 —— 这些工作交由下游的 Atlas 处理。

详细设计请参见 [SPEC.md](SPEC.md)，向 Atlas 交付的数据契约请参考 [PROTOCOL.md](PROTOCOL.md)。

## 🌟 为什么需要专属工具？

数据源的获取是最困难的环节，且各平台差异巨大：
- **Bilibili** 对普通爬虫有严格限制（直接访问 `bilibili.com/video/…` 会返回 HTTP 412；无 `Referer` 时流/字幕 URL 报 403；大部分内容需要登录 Cookie），且不会向 yt-dlp 暴露其 AI 字幕。
- **YouTube** 相对友好，但仍需谨慎处理原声字幕的选择。

BliSolver 将每个数据源封装在一个 **Provider (提供程序)** 背后，最终转化为结构统一、干净利落的数据包。

## 🛠️ 环境要求

- **Python 3.11**
- **ffmpeg** 需要在环境变量 PATH 中（被 yt-dlp 和帧提取阶段依赖）。
- **JavaScript 运行时** (推荐 **deno**，或 node) 用于 **YouTube** — yt-dlp 需要它来驱动 YouTube 真实的 Web 播放器客户端。可从 PATH 自动检测，或检查标准安装位置。
- **NVIDIA GPU** 或 **Apple Silicon** (Metal) 用于加速 Whisper 转录。
- **LM Studio** 需在后台运行，并加载了视觉模型 (VL) 及其 mmproj 投影文件，用于视觉标注阶段。
- **授权 (分数据源):**
  - **Bilibili:** 一个已登录的 **Firefox** 浏览器配置 (默认)，或使用 `SESSDATA` 作为备用方案。
  - **YouTube:** 公开视频无需授权；对于年龄限制或机器人检测视频，可选配置浏览器配置文件。

## 📦 安装与配置

```bash
python -m venv .venv
source .venv/bin/activate          # Linux/macOS
# .venv\Scripts\activate           # Windows

pip install -e .                   # 核心依赖
pip install -e ".[transcribe]"     # + faster-whisper (Mac下支持 whisper.cpp)
pip install -e ".[frames,vision]"  # + 抽帧与视觉大模型支持

cp .env.example .env               # 配置您的环境变量
```

> **注意：** 在 `.env` 中配置 LM Studio 接口地址/Token/模型名称，以及各数据源的授权变量。请勿提交您的 `.env` 文件。

## 🚀 使用方法

```bash
harvest ingest <url> [选项]
harvest probe  <url>
```

### 常用选项

| 标志参数 | 描述 |
|------|-------------|
| `--part N` | 处理指定的分 P (从 1 开始) |
| `--all-parts` | 处理所有可用的分 P (Bilibili) |
| `--force-whisper` | 跳过复用字幕，强制使用本地模型转录 |
| `--lang CODE` | 指定转录/提取的语言 (在 Agent 环境中默认与用户的对话语言对齐) |
| `--robust` | 禁用 `condition_on_previous_text` (适合重复性幻灯片课程) |
| `--no-vision` | 跳过画面帧视觉标注 |
| `--dedup-threshold N` | 用于去重的 pHash 汉明距离阈值 (默认值: 10) |
| `--no-frame-images` | 不在 `out/` 输出 PNG 图片文件 (依然保留图片描述文本) |
| `--ocr` / `--force-ocr` | 执行硬字幕 (内嵌字幕) 的 OCR 提取 |
| `--danmaku` | 抓取 Bilibili 弹幕轨道 |
| `--interactions` | 抓取 Bilibili 互动投票和评分 |

### 🔍 探针 (Probe)

`probe` 仅接收 URL 并打印一行的 JSON 格式 `ProbeResult` (包含标题、UP主、时长、分P、`original_language` 原声语言及 `available_subtitles` 所有可选字幕轨)。该功能使上层 Agent 能在 `ingest` 摄取前准确预估工作量。

## 📖 输出格式

产物将被写入：`out/<安全的视频标题> [<id>-p<part>]/`

- `bundle.md`: 交付给 Atlas 的核心产物 (包含来源元数据 + 与时间轴对应的字幕/视觉笔记)
- `bundle.json`: 精准的后台 JSON 记录 (契约详见 PROTOCOL.md)
- `frames/`: 包含视觉抽帧 PNG 图片的目录

### 字幕提取策略

- **Bilibili:** 优先选择人工或 AI 字幕并经过质量门限过滤；如无有效字幕则降级使用 Whisper。复用的自动字幕将标注为 `auto-sub`。
- **YouTube:** 遵循优先级 `human-sub > auto-sub > whisper`。优先获取对应原声语言的人工字幕。
