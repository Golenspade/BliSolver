<div align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=timeGradient&height=250&section=header&text=BliSolver&fontSize=90&animation=fadeIn&fontAlignY=38&desc=%E8%A7%86%E9%A2%91%E6%91%84%E5%8F%96%20Agent%20Skill&descAlignY=55&descAlign=50" alt="Skill Banner">
  <p><strong>用于操作 BliSolver <code>blisolver</code> 视频摄取管道的 Agent Skill。</strong></p>
  <p>
    <a href="README.md">English</a> | <a href="README_zh.md">简体中文</a>
  </p>
</div>

---

本目录是 BliSolver [Agent Plugin](https://agent-plugins.org) 中的一个 Skill。它教会兼容的 Agent：

- 🩺 **离线诊断** 在花钱处理媒体之前先跑 `blisolver doctor`
- 🔍 **廉价探测** bilibili.com 与 YouTube 的元数据，不下载任何媒体
- 📝 **执行摄取** 并读回它实际写入的准确路径
- 🧠 **理解溯源** 字幕、视觉笔记、OCR、弹幕与互动数据的可信度层级
- 🔬 **检查与校验** schema 1.1 数据包

> **注意：** BliSolver 是数据摄取的前门，本身不做总结，也不做实体提取。

## 📦 打包方式

仓库根目录即 plugin root，所以 Skill 与它所驱动的应用并存于同一棵树：

```text
<plugin-root>/
├── plugin.json                 # Agent Plugins 1.0.0 清单
├── mcp.json                    # stdio MCP 服务声明
├── bin/blisolver-mcp           # 负责解析可用解释器的启动器
├── blisolver/                  # 本 Skill 驱动的应用
└── skills/
    └── blisolver-video-ingestion/     ← 你在这里
        ├── SKILL.md
        ├── references/
        └── scripts/
```

符合规范的 Agent Plugins 客户端会读取 `plugin.json`，从固定位置 `skills/` 发现本 Skill，并从
`mcp.json` 读取 MCP 服务配置。安装方式是把这个 plugin 目录交给你的客户端 —— clone 仓库，或复制到客户端
加载 plugin 的目录下。具体命令由各客户端自行定义，不在此处记录。

Skill 包**刻意不**内置 Python 环境、模型、媒体、缓存或任何密钥。它只提供指令和三个脚本。

## 🚀 使用

可以这样让 Agent 干活：

> *"探测这个 B 站链接，然后在不启用视觉的情况下摄取它，并校验产出的 bundle。"*

对外公开的脚本：

| 脚本 | 用途 |
|---|---|
| `scripts/blisolver_cli.py` | 用装有 BliSolver 依赖的解释器运行 CLI，并原样转发全部参数 |
| `scripts/inspect_bundle.py` | Bundle 摘要 —— 只报数量与身份信息，绝不输出正文 |
| `scripts/validate_bundle.py` | 依据实时 schema 与产物校验 bundle |

`scripts/blisolver_cli.py` 是**直通**而非适配器：它不重复声明 CLI 的任何 flag，因此不可能落后于 CLI。
解释器解析顺序为 `$BLISOLVER_PYTHON`、`$PLUGIN_DATA/venv/bin/python`、
`<plugin-root>/.venv/bin/python`，最后是 PATH 上已安装的 `blisolver`；它**绝不**回退到恰好启动它的那个
`python3`。

```bash
S=skills/blisolver-video-ingestion/scripts

python3 "$S/blisolver_cli.py" doctor                       # 离线预检
python3 "$S/blisolver_cli.py" probe 'https://...'           # stdout 上一个 JSON 对象
python3 "$S/blisolver_cli.py" --show-command ingest 'https://...'   # 只预览命令，不执行
python3 "$S/blisolver_cli.py" ingest 'https://...' --json   # 执行；从 envelope 里读路径
```

## ⚙️ 运行时要求

指令本身是可移植的，但媒体处理依赖目标机器：

- Python 3.11+，以及一个装有 BliSolver 依赖的 Python 环境
- ffmpeg —— 每个媒体阶段都要调用它
- `whisper-cli` **和** 一份 GGML 模型 —— 这是两件独立的事，`doctor` 会分别检查
- deno 或 node —— yt-dlp 需要其中之一来驱动 YouTube 真正的 web player 客户端
- LM Studio 及视觉模型**和它的 projector** —— 仅帧画面描述需要
- 独立的 `.ocr-venv` —— 仅硬字幕 OCR 需要
- `BLISOLVER_DANMAKU_MODEL` 里的 LM Studio 模型 id —— 仅 `--danmaku` 需要

不要靠猜，直接跑 `blisolver doctor`：每一项检查都标注了它把守的管道阶段，所以一条 warning 会告诉你**具体
哪个能力不可用**，而不是"哪里有问题"。

## 🛠️ 模型配置

**Whisper 权重** —— 当没有可信字幕可用时需要：

```bash
curl -sL -o /tmp/ggml-medium.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.bin
```

`/tmp` 会在重启后被清空。想长期保留请把 `BLISOLVER_WHISPER_MODEL` 指向持久路径。

**OCR 沙箱** —— 仅 `--ocr` 需要。保持隔离是为了让 RapidOCR 与 OpenCV 永不进入应用环境：

```bash
uv venv .ocr-venv
.ocr-venv/bin/pip install rapidocr-onnxruntime opencv-python
```

## 🔐 凭据

数据源凭据应放在环境变量或 `.env` 中。**严禁**把 `SESSDATA`、`LMSTUDIO_API_KEY` 或 cookie 内容写进命令
行参数、URL、日志或 `mcp.json` —— Agent Plugins 规范明确把配置里的 `env` 值视为可见的包数据，而
`mcp.json` 是要提交进仓库的。

## ⚠️ 当前限制

- `bilibili.tv` 处于延后状态，会被显式报错拒绝。
- ASR 后端是通过 `whisper-cli` 调用的 whisper.cpp。`pyproject.toml` 里 `transcribe` 可选依赖组仍列着
  faster-whisper 和 CUDA wheel，那是历史遗留，已不生效。
- **交付的字幕可能不是视频的原语言。** B 站有时会返回被删改的中文 ASR 轨，此时管道会向下回退到外语轨。
  请读 `source_reason` 里的 `language proxy` —— 它会指明被弃的轨道和触发的标记。`bundle.md` 的
  `transcript_language` 告诉你实际收到的是什么语言；`original_language` 对 B 站是平台默认值 `zh`，
  不是探测结果，不能当判据。
- 视觉、OCR、弹幕、互动弹幕都是可选阶段，各有独立的外部依赖。

## 📂 目录结构

```text
SKILL.md                  # Agent Skills 清单与指令
scripts/                  # 三个公开脚本 + 一个内部 helper
references/               # 渐进式披露文档
LICENSE.txt               # MIT
README.md / README_zh.md  # 本指南
```

## 📜 标准与许可

- [Agent Plugins 规范](https://agent-plugins.org/specification)
- [Agent Skills 规范](https://agentskills.io/specification)
- [Model Context Protocol](https://modelcontextprotocol.io/specification)
- **MIT License.** 见 [`LICENSE.txt`](LICENSE.txt)。
