# douyin-topic-packager

`douyin-topic-packager` 是一个抖音对标账号选题包生成 CLI 工具。

它从抖音博主主页分享链接开始，完成：

```text
Cookie 登录/导入
-> 解析主页分享链接
-> 采集账号 Top20 视频
-> 按评论数排序
-> 采集视频评论
-> 分析评论痛点
-> 生成角度候选
-> 角度验证评分
-> 输出选题包 JSON + Markdown
```

本项目不内置任何 LLM API Key。你需要自己选择模型供应商，并在 `.env` 里填写 Key。

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
- `anthropic`
- `gemini`
- `openai-compatible`

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

生成的 Markdown 报告会包含“运行摘要”，展示痛点数量、选题包数量和筛选条件。

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
pain_signals.json
angle_candidates.json
validation_scorecards.json
topic_packages.json
topic_packages.md
```

`topic_packages.md` 是给人看的干净报告；`topic_packages.json` 适合接入其他自动化流程。

## 测试

开发或维护时建议安装开发依赖后运行测试：

```bash
pip install -e ".[dev]"
python -m pytest -q
python -m compileall -q src tests
```

这些测试只覆盖链接解析、选题包生成、LLM 输出清洗、报告渲染和项目配置，不会真实登录抖音、采集评论或安装 Playwright 浏览器。

## 注意事项

- 默认采集 Top20，并按评论数排序。
- 本工具不下载视频、不转写视频，只使用主页视频信息和评论信号生成选题包。
- 评论较少时也会生成选题包，但报告里会标记证据强弱。
- 不默认任何行业、身份或立场；选题包只基于采集到的标题、描述、评论和用户配置生成。

## 第三方声明

本项目的 Cookie 登录、主页解析、Douyin Web API 调用、X-Bogus / msToken 等部分复用或参考了公开 `douyin-downloader` / Douyin-TikTok downloader 生态里的通用实现思路。

`src/douyin/` 下包含来自相关开源下载工具生态的 API 客户端、签名参数和 Cookie 使用逻辑。使用、修改或分发时请保留原项目的版权、许可证和 NOTICE 声明；本项目本身以 Apache-2.0 发布。
