# 阶段 E 实施计划：MCP 接口层

> 状态：**已完成并端到端验证**（BV1dSKJ6wEVz：probe→extract(11s)→get_transcript/get_timeline/get_visual_context 五工具全通）。
> 当前实现：`mcp` SDK 2.x 装入主 venv，`MCPServer` 工具注册 OK。

## 1. 目标

把已验证的 blisolver CLI 核心能力（probe / ingest / bundle 读取）包装成 MCP 工具，
供 Agent（Atlas 等）调用。从"本地 CLI 单体"变成"Agent 可调服务"。

## 2. 五个工具（对应计划 §6）

| 工具 | 模式 | 入参 | 出参 | 复用 |
|---|---|---|---|---|
| probe_video | 同步 | url | ProbeResult JSON | blisolver.probe |
| extract_transcript | 异步 | url, mode∈{auto,force_whisper,force_ocr} | job_id | ingest 子进程 |
| get_transcript | 同步 | job_id | {status, source, segments, quality_gate} | 读 bundle.json |
| get_timeline | 同步 | job_id | 统一时间轴 + provenance + fuse 诊断 | bundle.transcript + ocr + source_reason |
| get_visual_context | 同步 | job_id | {frames, ocr} | bundle.frames + ocr |

## 3. 异步 job 设计（最小可行）

- job store：`cache/mcp-jobs/<job_id>.json`，记 {job_id, url, canonical_id, part, mode,
  pid, started_at, bundle_path, log_path}。
- extract_transcript：resolve URL → 生成 job_id → `Popen` 启动
  `python -m blisolver.cli ingest <url> --no-vision --no-frame-images [flags]`（mode→flags）→
  返 job_id（不阻塞）。
- status 推断（get_*）：
  - pid 还活着 → running
  - pid 死了 + bundle.json 存在 → done
  - pid 死了 + bundle.json 不存在 → failed（读 log_path 尾部）
- mode→flags：auto=(无)；force_whisper=--force-whisper；force_ocr=--ocr --force-ocr。
- 子进程继承父 env（BLISOLVER_COOKIES_BROWSER 等由 MCP server 进程带入）。

## 4. 文件

- `blisolver/mcp/__init__.py`
- `blisolver/mcp/server.py`：MCPServer + 5 工具 + job store（纯函数逻辑，可单测）
- `blisolver/cli.py`：加 `blisolver mcp` 子命令启动 server（stdio）
- `pyproject.toml`：加 `mcp` optional dependency group
- 测试：job store 状态推断 + 工具逻辑（mock subprocess，不真跑 ingest）

## 5. 验收

- `blisolver mcp` 启动 stdio server。
- 端到端：probe_video(BV1dSKJ6wEVz) → 返 ProbeResult；
  extract_transcript(url, auto) → job_id；get_transcript(job_id) 轮询到 done → human-sub 86 段。
- 测试套件全绿。
