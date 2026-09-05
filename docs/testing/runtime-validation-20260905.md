# 端侧运行时与失败路径复测（2026-09-05）

本次交付修复下载进度污染 JSON、ASR 缺依赖却先下载媒体、字幕探针吞掉错误，以及裸 BV 与文档不一致的问题。运行设备为 Apple M3 / 16 GB MacBook Air；没有 NVIDIA 实机验证，也没有完成中文准确率评估。

## 结果与边界

| 验证 | 结果 |
|---|---|
| 完整默认离线回归 | 635 passed，4 deselected，51.84 秒；未运行被排除的 live 测试 |
| 独立 SubAgent 复现 | JSON、ASR 前置检查、探针错误、裸 BV 四项通过；额外复核 MCP 强制 ASR 在短链接展开前拒绝缺依赖任务 |
| 下载器输出 | 真实 yt-dlp 使用本机 HTTP 音频服务，成功/失败都保持 stdout 可解析；aria2c 分支用真实子进程替身验证参数与输出，未冒充已安装 aria2c 的实测 |
| 缓存与隔离 | auto 模式已有转录缓存可在 ASR 依赖缺失时复用；多分 P 继续保持失败隔离 |
| MCP | 真实 stdio 协商 2026-07-28，发现 11 个资源；按需读取 SKILL.md 与 mcp-contract，Skill 正文与仓库权威文件一致 |
| 修改文件 Ruff | 19 个 Python 文件与原 HEAD 对比：10 项既有发现，0 项新增；不宣称仓库全局 lint 干净 |
| 本地 Metal | whisper.cpp 官方 11 秒示例音频成功输出 SRT，报告总耗时 2341.68 ms，退出码 0 |

独立复现的初次快照发生在并行修改期间，只用于同一脚本前后观察，不是原始 HEAD 的冻结基线。

## 实际 B 站复测

从 [候选桶](video-bucket-20260905.md) 选择 [心理拾光的 4 分 25 秒视频](https://www.bilibili.com/video/BV1bWVH6VE8p)。真实 MCP 探针获取到标题、作者和 265 秒时长，但字幕接口返回 HTTP 412：

```json
{
  "status": "partial",
  "subtitle_status": "error",
  "available_subtitles": [],
  "warnings": [{
    "stage": "subtitles",
    "code": "subtitle_access_blocked",
    "http_status": 412,
    "retryable": true
  }]
}
```

上面只摘录关键字段；实际 warning 还含不暴露凭据的解释与下一步建议。探针耗时约 5.13 秒；测试调用方读取警告后停止，没有创建 ingest 任务、下载该视频或启动 ASR。该行为验证错误可见及调用流程遵守停止建议；没有证明平台限制已解除，也没有生成完整视频转录。尚未确定 412 与登录、代理或频率之间的因果关系。

没有并发运行 10 个候选。候选集的信息密度与实际转录准确率仍为待测。

## whisper.cpp 安装记录

查询 [GitHub 官方最新 Release API](https://api.github.com/repos/ggml-org/whisper.cpp/releases/latest)，返回 [b4938](https://github.com/ggml-org/whisper.cpp/releases/tag/b4938)，发布时间 2026-08-20T11:33:19Z，非 prerelease。下载源码固定到提交 `371b5a7561823ab2bb32142d2751e35e7534727b`；本地该提交同时具有 `b4938` 和 `v1.9.3` 标签。源码 CMake 版本为 1.9.3，默认 `WHISPER_BUILD_IS_DEV=ON`，所以本次构建版本带 `-dev` 后缀。

本次按 Release 构建配置启用 Metal，以 2 个编译任务构建 `whisper-cli`；没有构建或安装 CUDA 运行时。安装产物均留在被 Git 忽略的目录：

- 源码：`cache/runtime/whisper.cpp-b4938/`
- 可执行文件：`cache/runtime/whisper.cpp-b4938/build/bin/whisper-cli`
- 模型：`cache/models/ggml-small-q5_1.bin`，约 181 MiB，来自 [官方模型仓库](https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small-q5_1.bin)

已在本机忽略的 `.env` 中配置可执行文件和模型的绝对路径，保留其他配置。`doctor` 的两项 ASR 检查均通过。路径不会随重启清空，但手动清理这些 cache 目录会移除运行时。

首次受限执行环境下的 Metal 示例运行因缓冲区分配失败退出；在正常执行权限下重试同一命令成功。该短音频只证明当前模型与 Metal 能实际推理，不是视频准确率、持续负载或 RTX 性能基准。测试结束后没有遗留 Whisper、ffmpeg 或本次 MCP 测试进程。

## 本机证据

原始材料保留在忽略目录中，不提交媒体、字幕或凭据：

- `out/live-retest-20260905/`：MCP 发现、doctor、真实探针、调用日志。
- `out/independent-recheck-20260905/`：独立复现脚本及前后检查报告。
- `out/whisper-build-20260905.log`、`out/whisper-metal-smoke.*`：编译及实际模型运行记录。
- `out/doctor-after-whisper-install.json`、`out/touched-ruff-comparison-20260905.json`：依赖与静态检查。
