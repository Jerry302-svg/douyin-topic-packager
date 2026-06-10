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

## 注意事项

- 默认采集 Top20，并按评论数排序。
- 本工具不下载视频、不转写视频，只使用主页视频信息和评论信号生成选题包。
- 评论较少时也会生成选题包，但报告里会标记证据强弱。
- 不默认任何行业、身份或立场；选题包只基于采集到的标题、描述、评论和用户配置生成。

## 第三方声明

本项目的 Cookie 登录体验参考公开抖音下载工具的通用做法：使用 Playwright 打开浏览器，由用户自行登录后把 Cookie 保存到本地。

`src/douyin/` 下包含来自公开 Douyin/TikTok 下载生态的 API 客户端、X-Bogus、msToken 等实现思路或许可证头部。使用、修改或分发时请保留相关版权和许可证声明。
