# 阶段 D 实施计划：多源融合 fuse.py

> 状态：**已完成并端到端验证**（BV1LD7U65Ew2）。
> 驱动数据：BV1LD7U65Ew2 — whisper 69 段里 46 段是"冰淇淋-6 颗"、21 段"5 颗"（97% 重复幻觉），
> 同时段 OCR 是真实内容（"今天也是日常看看GitHub"、"AstrBot PR..."）。
> 这推翻了"transcript 永远是主权威"的隐含假设，定义了 fuse 的真正职责。

## 1. 对计划 §5.3 的实测修正

计划 §5.3 算法预设"CC/AI/ASR 多语音源按时间重叠择优"。但降级链是**择一**的
（CC 不可用→AI→whisper），正常只有一个语音源。同源择优场景（CC+AI 都可用）rare 且无现成
测试视频。**真正的高频痛点是 ASR 幻觉**——阶段 C 实测撞上的。

因此 fuse.py 的优先级重排（基于实测，仍兼容 §5.3 原始 scope 作为接口预留）：

| 职责 | 优先级 | §5.3 对应 | 实测驱动 |
|---|---|---|---|
| provenance 落地（segments 打 source/confidence） | P0 前置 | §3.1 字段真正生效 | 当前全 None |
| ASR 幻觉检测 + 异源交叉（OCR 验真） | P0 核心 | §5.3 未明写，实测发现 | BV1LD7U65Ew2 97% 幻觉 |
| 同源择优（CC+AI 多语音源） | P1 接口骨架 | §5.3 原始 scope | 无测试视频，单测覆盖 |

## 2. D-1：provenance 落地（数据出口打标签）

让 schema 1.1 的 `Segment.source/confidence` 真正生效，不在 fuse 事后猜，而在源头标：

- `transcribe.py:transcribe()` 出口：返回的 segments 标 `source="whisper"`（confidence 不设，
  whisper.cpp 不暴露 per-seg conf，None 诚实）。
- `providers/bilibili.py:fetch_subtitle` accepted 分支：segments 标 `source=sub.source`
  （"human-sub"/"auto-sub"）+ `confidence=1.0`（CC 默认 1.0 per §3.1）。
- `cli._whisper` cache 加载兜底：老 cache（source=None）加载后补打 `source="whisper"`，
  兼容存量；新 cache 天然带标签。

## 3. D-2：fuse.py 核心 — ASR 幻觉检测 + 异源交叉

### 3.1 幻觉检测算法（whisper 重复模式）

```
1. 取 ASR transcript.segments 的归一化文本序列
2. 计算重复率 = max(text 出现次数) / total_segs
3. 若 > 0.40（BV1LD7U65Ew2 = 46/69 = 0.67）→ hallucination 信号
4. 同时检测连续重复（同一文本相邻出现 >=5 次）→ 强信号
```

### 3.2 异源交叉验证（OCR 验真）

当 OCR track 存在：
```
1. 对每个 ASR segment，找时间重叠的 OCR cues
2. 若 ASR 是重复幻觉且 OCR 在同时段有不同真实内容 → 确认 ASR 幻觉
3. 输出诊断：ASR 在 [t1,t2] 段判幻觉，OCR 为真值
```

### 3.3 产出契约（不破坏 §5.2）

- 主 `transcript` 字段**内容不变**（向后兼容契约）。
- 诊断写到 `transcript.source_reason`（追加幻觉注记）+ 可选 `transcript.robust` 标志。
- **不篡改主 transcript 内容、不把 OCR 塞进 transcript**（§5.2 OCR 独立 track 不变）。
- 下游（Atlas）通过 source_reason 知道"此 transcript 低可信，OCR 是真值"。
- `FusionResult` 暴露 diagnostics 列表供 bundle.md 渲染诊断段。

### 3.4 同源择优（P1 接口骨架）

`fuse(transcript, ocr, *, candidates=None)` 接受可选 `candidates: list[Transcript]`。
当存在多语音源（CC+AI 都通过门控）：按 §5.3 算法——时间重叠 + 文本相似度择优。
无测试视频，单测用合成数据覆盖算法正确性。

## 4. CLI 集成

- `--fuse` 开关（默认 off，保持现状）。开时在 build_bundle 前 `fuse.fuse(transcript, ocr)`。
- 有 OCR track 时自动做异源交叉（即使 `--fuse` off，幻觉检测值得跑——降级到 OCR 是
  下游决策依据）。倾向：`--fuse` 控同源择优；异源交叉在 `--ocr` 跑过时自动启用。
- bundle.md 渲染 `## Fusion diagnostics` 段（当有诊断）。

## 5. 验收

- BV1LD7U65Ew2：`--ocr --fuse` → transcript.segments 全带 source="whisper"；
  幻觉检测命中（46/69 重复）+ OCR 交叉确认；source_reason 注记幻觉；bundle.md 有诊断段。
- 同源择优：合成多源单测覆盖。
- 测试套件全绿。