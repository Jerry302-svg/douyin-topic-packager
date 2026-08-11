# douyin-topic-packager

`douyin-topic-packager` 是一个抖音对标账号选题包生成 CLI 工具。

它从抖音博主主页分享链接开始，完成：

```text
Cookie 登录/导入
-> 解析主页分享链接
-> 分页扫描账号视频
-> 按评论数排序
-> 采集视频评论
-> 分析评论痛点
-> 生成角度候选
-> 角度验证评分
-> 输出选题包 JSON + Markdown
```

本项目不内置任何 LLM API Key。你需要自己选择模型供应商，并在 `.env` 里填写 Key。

报告会把真实评论痛点、标题内容假设和弱证据观察分开展示，并为每个选题包生成封面文案、前三秒口播、拍摄结构、评论引导和素材准备。只有证据达到门槛的选题才会标成可直接使用，其余会明确标成探索性选题。

每次分析还会自动生成机器可读的质量门禁和模型缓存元数据，方便判断报告是否可用，以及本次结果来自 LLM 还是规则降级。

质量不合格时会直接列出失败检查和修复动作；评论采集会同步写入独立原子检查点，首次运行中断也能继续。

## 环境要求

- Python 3.10+
- Playwright Chromium

安装：

```bash
pip install -r requirements.txt
pip install -e .
python -m playwright install chromium
cp .env.example .env
```

查看当前工具版本：

```bash
python -m douyin_topic_packager --version
```

如果不确定环境是否准备好，可以先运行：

```bash
python -m douyin_topic_packager doctor
```

它会检查 Python 版本、Playwright、Cookie 文件和 LLM 配置完整性。LLM 只有使用 `--llm` 时才必须配置。

## 配置 LLM

只有使用 `--llm` 时才需要配置：

```env
LLM_PROVIDER=minimax
LLM_MODEL=你的模型名
LLM_API_KEY=你的 API Key
LLM_BASE_URL=
```

支持：

- `openai`
- `deepseek`
- `qwen`
- `kimi` / `moonshot`
- `zhipu`
- `minimax`
- `minimax-cn`（MiniMax 国内区订阅 Key 推荐）
- `anthropic`
- `gemini`
- `openai-compatible`

MiniMax 说明：`minimax` 使用全球端点 `https://api.minimax.io`；如果你的 Key 是国内区订阅 Key，请使用 `LLM_PROVIDER=minimax-cn`，模型可填 `MiniMax-M3`。

## 使用方法

首次登录并保存 Cookie：

```bash
python -m douyin_topic_packager login
```

一条命令跑完整流程：

```bash
python -m douyin_topic_packager run \
  --profile-url "抖音主页分享链接" \
  --top-n 20 \
  --scan-pages 10 \
  --llm
```

工具会先分页扫描主页，再从扫描结果里按评论数选择 Top20，避免只在最新一页里排序。评论采集默认并发数为 2，并对临时网络错误自动重试：

```bash
python -m douyin_topic_packager run \
  --profile-url "抖音主页分享链接" \
  --comment-concurrency 2 \
  --include-replies \
  --llm
```

可以控制选题包里的 CTA 转化强度：

```bash
# 默认：平衡，不激进也不太弱
python -m douyin_topic_packager run --profile-url "抖音主页分享链接" --llm --conversion-mode balanced

# 克制：适合知识分享、合规要求更高的账号
python -m douyin_topic_packager run --profile-url "抖音主页分享链接" --llm --conversion-mode conservative

# 强转化：适合希望评论区更容易留下具体线索的账号
python -m douyin_topic_packager run --profile-url "抖音主页分享链接" --llm --conversion-mode strong
```

三个模式的区别：

- `balanced`：默认模式，引导用户描述具体场景，但不承诺结果。
- `conservative`：更克制，不做个案判断，不主动索要敏感金额。
- `strong`：更直接，引导用户留下阶段、障碍或决策点，但仍不允许保证结果。

如果只想保留高分选题，或控制最终选题包数量，可以加筛选参数：

```bash
python -m douyin_topic_packager run \
  --profile-url "抖音主页分享链接" \
  --llm \
  --min-evidence-count 2 \
  --min-fit-score 80 \
  --package-limit 5
```

`--min-evidence-count` 表示只保留证据数不低于该值的痛点信号；`--min-fit-score` 表示只保留适配分不低于该值的选题包；`--package-limit` 表示最多输出多少个选题包。这些参数也可以用于 `analyze` 子命令。

如果上一次已经生成了 `profile_videos.json` 或 `comments.json`，可以断点续跑，避免重复采集主页和评论：

```bash
python -m douyin_topic_packager run \
  --profile-url "抖音主页分享链接" \
  --output-dir outputs/topic_packages \
  --resume \
  --llm
```

`--resume` 会校验主页链接、账号身份、采集参数和文件哈希。比如本次 `--top-n`、`--scan-pages`、评论上限、回复设置或饱和停止参数与上次不同，工具不会盲目复用旧文件。评论每完成一条视频都会更新状态；状态缺失、文件被修改或检查点不完整时，会自动补采受影响的视频。缺少新版身份与哈希信息的历史输出会安全地重新采集一次。

生成的 Markdown 报告会包含“运行摘要”和“下一步动作”，展示痛点数量、证据强弱、选题包数量、筛选条件，以及应直接拍摄、先核验还是继续补证据。

### 0.6 首次中断恢复和质量修复建议

评论采集现在会额外维护 `comments_checkpoint.json`。它把主页视频哈希、评论采集参数、已采评论、逐视频状态及内容哈希放进同一个原子快照；即使程序在第一次运行中途退出、还没有生成 `run_manifest.json`，再次使用相同参数执行 `--resume` 也只会补采失败或未完成的视频。账号、视频文件、内容哈希或采集参数不匹配时不会复用。

`quality_report.json` 新增 `failed_checks` 和中文 `recommendations`。Markdown 报告会在证据不落地、标题过长、受众过泛、LLM 降级或核验状态错误时，直接展示对应修复动作。

### 0.5 自动质量门禁和模型缓存

启用 LLM 后，模型原始响应会缓存在输出目录的 `.analysis_cache/` 中。缓存键包含完整输入、提示词版本、服务商和模型，不包含 API Key；相同输入重跑可直接复用。复用后仍会重新执行证据绑定、风险审计、效果校准和参数筛选，缓存不会跳过质量控制。

每次 `analyze` 或 `run` 都会生成 `quality_report.json` 和 `analysis_metadata.json`。如果明确使用 `--llm`，但模型调用或输出校验失败并降级为规则结果，质量门禁会标记为“需要复核”，避免把降级结果误当成模型实测。

### 0.4 通用意图和稳健校准

评论信号不再围绕单一法律场景写死标签，而是归纳为流程不清、选择困难、风险担忧、结果不确定和资源限制等通用意图。法律、医疗、金融、人身安全等需要专业判断的内容仍会根据原始证据进入外部核验，不会影响普通教育、创作、生活方式和消费场景。

历史效果数据只有达到最低曝光量后才参与评分，并使用时间衰减、异常值限制和低/中/高可信度控制调整幅度。报告展示中文使用建议和证据状态，不再直接暴露 `publish_ready`、`audience_pain` 等内部枚举。

### 0.3 采集与证据增强

按“有效问题评论”而不是原始评论数自适应停止：

```bash
python -m douyin_topic_packager run \
  --profile-url "抖音主页分享链接" \
  --target-valid-comments 30 \
  --max-comment-pages 20 \
  --saturation-pages 3 \
  --saturation-min-new-ratio 0.08 \
  --include-replies \
  --llm
```

工具会统计独立用户、涉及视频、重复证据和语义变体。至少两条不同证据并获得多用户或跨视频支持，才会进入 `publish_ready`。只有涉及需要专业判断的高风险事实，才会标记为 `review_required`，并要求补充公开来源或对应领域审核；普通教育、创作、生活方式等场景不受这一门槛影响。

评论导出默认移除昵称、IP 属地、用户 ID、手机号和联系方式，只保留不可逆的短哈希用于独立用户计数。仅在本地确有需要时使用 `--keep-user-data`，不要把这类结果提交到 GitHub。

每个选题会生成两个开头实验，并给出应观察的前三秒留存率或完播率。也可以用历史发布数据校准评分：

```bash
python -m douyin_topic_packager analyze \
  --videos outputs/topic_packages/profile_videos.json \
  --comments outputs/topic_packages/comments.json \
  --performance-feedback performance.json \
  --llm
```

`performance.json` 可以是数组，每条包含 `title` 或 `pain_point`，以及 `impressions`、`three_second_rate`、`completion_rate`、`save_rate`、`comment_rate`。样本不足 1000 曝光时只用 10% 权重，避免小样本覆盖证据评分。

`run_manifest.json` 还会保存输入文件哈希、采集停止原因、有效评论数、模型耗时、重试次数和 token 用量，但不会保存 API Key。

也可以分步跑：

```bash
python -m douyin_topic_packager collect --profile-url "抖音主页分享链接"
python -m douyin_topic_packager comments --input outputs/topic_packages/profile_videos.json
python -m douyin_topic_packager analyze --comments outputs/topic_packages/comments.json --llm
```

## 输出文件

默认输出到 `outputs/topic_packages/`：

```text
profile_videos.json
comments.json
comments_status.json
comments_checkpoint.json
pain_signals.json
angle_candidates.json
validation_scorecards.json
topic_packages.json
quality_report.json
analysis_metadata.json
run_manifest.json
topic_packages.md
```

`topic_packages.md` 是给人看的干净报告；`topic_packages.json` 适合接入其他自动化流程。

## 质量回归

生成选题包后，可以运行完全离线的质量门禁：

```bash
python -m douyin_topic_packager evaluate \
  --pain-signals outputs/topic_packages/pain_signals.json \
  --topic-packages outputs/topic_packages/topic_packages.json \
  --require-generator llm
```

它会检查证据溯源率、未知痛点、虚构素材指令、越界个案判断、生成器来源和可发布选题数量，并为失败项返回修复建议。`--require-generator llm` 可以防止模型输出被规则版静默降级后仍误判为模型实测成功。发布新版本或更换 LLM 时，建议用同一份脱敏输入连续运行三次；详细标准见 `evals/README.md`。

需要验收整次运行时，使用 manifest 一次检查所有已记录产物的哈希和质量门禁：

```bash
python -m douyin_topic_packager verify-run \
  --manifest outputs/topic_packages/run_manifest.json
```

命令通过时退出码为 0；产物缺失、被覆盖或质量门禁未通过时退出码为 1。只想核对文件完整性、允许报告进入人工复核时，可增加 `--allow-quality-review`。

## 测试

开发或维护时建议安装开发依赖后运行测试：

```bash
pip install -e ".[dev]"
python -m pytest -q
python -m compileall -q src tests
python -m ruff check src/douyin_topic_packager tests
python -m build
```

这些测试只覆盖链接解析、选题包生成、LLM 输出清洗、报告渲染和项目配置，不会真实登录抖音、采集评论或安装 Playwright 浏览器。

## 注意事项

- 默认采集 Top20，并按评论数排序。
- 本工具不下载视频、不转写视频，只使用主页视频信息和评论信号生成选题包。
- 评论较少时仍可生成探索性选题，但不会把标题假设冒充成真实用户痛点。
- 模型生成的证据必须匹配已采集标题或评论；匹配失败时会替换为真实来源或移除该选题。
- 不默认任何行业、身份或立场；选题包只基于采集到的标题、描述、评论和用户配置生成。

## 第三方声明

本项目的 Cookie 登录、主页解析、Douyin Web API 调用、X-Bogus / msToken 等部分复用或参考了公开 `douyin-downloader` / Douyin-TikTok downloader 生态里的通用实现思路。

`src/douyin/` 下包含来自相关开源下载工具生态的 API 客户端、签名参数和 Cookie 使用逻辑。使用、修改或分发时请保留原项目的版权、许可证和 NOTICE 声明；本项目本身以 Apache-2.0 发布。
