# 视频测试候选集（2026-09-05）

通过 Computer Use 从 B 站首页、知识区和 Vlog 分区收集。共 10 个：知识类 5 个、Vlog 5 个。标题、UP 主和时长来自页面；场景描述是选样理由，实际信息密度、语言、字幕与质量尚未测量。

| 类别 | 视频 | UP 主 | 时长 | 测试关注点 |
|---|---|---|---|---|
| knowledge | [什么人能为了三个汉堡劫机？【硬核狠人89】](https://www.bilibili.com/video/BV1SDR3BJEJ3) | 小约翰可汗 | 40:53 | 长篇叙述、人物专名 |
| knowledge | [你长大了，该学会看懂挑拨离间了](https://www.bilibili.com/video/BV1bWVH6VE8p) | 心理拾光 | 04:25 | 短口播基线 |
| knowledge | [26下四级阅读 第一讲-有机食品](https://www.bilibili.com/video/BV155tq6iEVj) | 我是瑞斯拜 | 44:35 | 长课程、中英混合与停顿 |
| knowledge | [【中气爱】全球变暖、海平面上升……寒潮可能会更猛！](https://www.bilibili.com/video/BV1kWtB6EEyo) | 中气爱 | 05:39 | 专业术语与短科普 |
| knowledge | [火药味十足的读评论！尽管来攻击我，我还顶得住！](https://www.bilibili.com/video/BV1DC4r6MEsR) | 蔡武年 | 12:22 | 问答、引用与口语重复 |
| vlog | [小伙在日本乡下，下班去镇上吃个咖喱猪排饭，简单快乐的小生活](https://www.bilibili.com/video/BV1rwtU6VEXC) | 小轩今天下班了 | 11:20 | 生活记录、环境声与停顿 |
| vlog | [当搞艺术的一切需求得到满足](https://www.bilibili.com/video/BV1DybY6pEiA) | 想想工作室 | 05:45 | 日常活动与画外音 |
| vlog | [欢迎收看09年高中生勇闯清华当插班生的一天](https://www.bilibili.com/video/BV1rSjq6HEhE) | 不叫睡神 | 08:05 | 校园记录与多说话人候选 |
| vlog | [广西高中生记录洪水被困到返校重建的全过程](https://www.bilibili.com/video/BV1ZC8i6CE5Z) | 殷花红 | 36:59 | 长时间记录、噪声与叙述候选 |
| vlog | [贫民窟vs顶级海景别墅，美国居住环境差距有多大？](https://www.bilibili.com/video/BV1Zp3M6UEvT) | 杰克小兔 | 27:12 | 实地记录、场景切换与长口播 |

机器可读清单：`video-bucket-20260905.json`。先串行 probe；短样本用于冒烟测试，长课程/记录用于后续负载与准确率测试。发生 412/429 时停止连续请求并记录错误，不把错误当作无字幕。不要直接并发转录全部候选。

首次复测：`BV1bWVH6VE8p` 元数据成功、字幕 HTTP 412，返回 `partial` 后停止，未启动 ingest。其余九项尚未运行探针。详见 [运行时与失败路径复测](runtime-validation-20260905.md)。
