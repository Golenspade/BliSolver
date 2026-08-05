<div align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=timeGradient&height=250&section=header&text=BliSolver&fontSize=90&animation=fadeIn&fontAlignY=38&desc=%E4%BE%BF%E6%90%BA%E5%BC%8FAgent%20Skill&descAlignY=55&descAlign=50" alt="Skill Banner">
  <p><strong>用于操作 BliSolver <code>harvest</code> 视频摄取管道的便携式 Agent Skill。</strong></p>
  <p>
    <a href="README.md">English</a> | <a href="README_zh.md">简体中文</a>
  </p>
</div>

---

此 Skill 指导兼容的编程代码 Agent (Coding Agents) 如何进行以下操作：

- 🔍 **探测与摄取 (Probe and ingest)** 公开的 bilibili.com 和 YouTube 视频
- 📝 **智能决策 (Choose)** 选择平台自带字幕，或降级使用本地 Whisper 离线转录
- 🩺 **环境诊断 (Diagnose)** 检查 ffmpeg、JavaScript 运行时、whisper.cpp、LM Studio、OCR 及授权配置
- 🔬 **校验与阅读 (Inspect and validate)** 检查产出的 Atlas 兼容 Schema 1.1 格式的 Bundle 数据包
- 🧠 **理解溯源 (Understand)** 掌握字幕、视觉笔记、OCR、弹幕及互动数据的来源可信度

> **注意：** Harvest 是一个数据的摄取前门 (ingestion front-door)，它本身不是总结器或实体提取器。

## 📦 安装

### 推荐方式：为所有支持的 Agent 安装

```bash
npx skills add alttina/bilibili-get-content \
  --global \
  --agent '*' \
  --yes
```

**仅为单一 Agent 安装：**

```bash
npx skills add alttina/bilibili-get-content \
  --global \
  --agent codex \
  --yes
```

您可以将 `codex` 替换为 `claude-code`、`cursor`、`github-copilot` 或其他受支持的 Agent。如需项目级本地安装，请移除 `--global` 参数。

在 [`for_agents_download.md`](for_agents_download.md) 中提供了一键复制/粘贴的快速安装命令。

## 🚀 使用方法

安装完成后，您可以直接对 Agent 下达类似如下的指令：

> *"Probe this bilibili URL, then ingest it without vision and validate the resulting bundle."*
> （探测这个 Bilibili URL，然后无视觉摄取它，并验证生成的 bundle 数据包。）

此 Skill 提供给 Agent 的公开包装器脚本有：

| 脚本 | 用途 |
|---|---|
| `scripts/doctor.py` | 离线运行时与依赖环境诊断报告 |
| `scripts/probe.py` | JSON 安全的 `harvest probe` (探针) 包装器 |
| `scripts/ingest.py` | 安全的 `harvest ingest` (摄取) 包装器，支持干跑 (dry-run) |
| `scripts/inspect_bundle.py` | 本地数据包 (bundle) 内容摘要输出 |
| `scripts/validate_bundle.py` | Schema 1.1 数据结构及产物校验 |

这些包装脚本会通过 `--project-root`、`HARVEST_PROJECT_ROOT` 环境变量、上级目录，或者系统中已安装的 `harvest` 命令来定位 BliSolver 的核心代码。它们本身并不包含 harvest 主程序。

## ⚙️ 运行时要求

此 Skill 安装包是完全便携的，但媒体处理依赖于您的目标执行环境：

- Python 3.11+;
- 一份完整的 BliSolver 源码或已安装在系统内的 `harvest` 命令;
- 用于音视频处理阶段的 ffmpeg;
- 用于本地转录降级的 `whisper-cli` 及其 GGML 模型;
- 用于可靠提取 YouTube 内容的 deno 或 node 运行时;
- 用于画面视觉标注的 LM Studio（需配置好视觉模型和相应的 projector）;
- 仅当需要硬字幕 OCR 时，才需要独立的 `.ocr-venv` 工作环境。

数据源提供商的授权凭据 (Credentials) 应保存在目标环境中或浏览器配置内。**严禁**将 Cookies、API 密钥或 `SESSDATA` 放在命令行参数、URL、README 文件或日志中打印。

## 🛠️ 本地模型与沙箱配置 (Model Setup)

BliSolver 采用 100% 本地运行策略以确保隐私并避免高昂的 API 成本开销。在首次运行视频解析前，请务必完成以下模型和环境的下载与配置：

**1. Whisper ASR 语音模型**  
当视频没有官方提供的字幕时，系统需要回退到本地进行 AI 听写。请使用以下命令下载 1.5GB 的 GGML 模型到默认路径：
```bash
curl -sL -o /tmp/ggml-medium.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.bin
```
*(你可以通过设置 `BLISOLVER_WHISPER_MODEL` 环境变量来更改模型的存放路径)。*

**2. 隔离的 OCR 沙箱环境**  
只有在运行带有 `--ocr` 参数的命令时才需要此项配置。为了避免沉重的视觉处理框架（如 OpenCV 和 ONNX）污染主工程依赖，BliSolver 要求 OCR 引擎运行在独立的虚拟环境中，请在项目根目录下执行：
```bash
uv venv .ocr-venv
.ocr-venv/bin/pip install rapidocr-onnxruntime opencv-python
```

## 🔄 更新与卸载

**更新已安装的 skill：**
```bash
npx skills update harvest-video-ingestion
```

**移除已安装的 skill：**
```bash
npx skills remove harvest-video-ingestion
```

## ⚠️ 当前已知限制

- `bilibili.tv` 目前被延期支持并会有意拒绝处理。
- 当前的 ASR (语音识别) 后端是 `whisper-cli`/whisper.cpp；文档中较老的关于 faster-whisper/CUDA 的引用仅作为历史遗留参考，若与当前源码冲突，以当前源码为准。
- 视觉 (Vision)、OCR、弹幕 (danmaku) 以及互动投票 (command-danmaku) 均为可选阶段，需满足独立的外部依赖要求。

## 📂 仓库结构

```text
SKILL.md                    # 必需的 Agent Skills 清单与行为规范指令
scripts/                    # 公开的包装器脚本与一个内部帮助类
references/                 # 渐进式展开的详细运维与架构文档
LICENSE.txt                 # MIT 开源协议
README.md                   # 面向人类用户的安装指南
for_agents_download.md      # 用于复制/粘贴的快速安装命令
```

## 📜 标准与开源协议

- [Agent Skills 规范](https://agentskills.io/specification)
- [`npx skills` 安装器](https://github.com/vercel-labs/skills)
- **MIT 开源协议。** 详情请见 [`LICENSE.txt`](LICENSE.txt)。
