from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .cookies import save_douyin_login_state
from .io_utils import read_json
from .llm import LLMClient, load_dotenv
from .packager import CONVERSION_MODE_INSTRUCTIONS
from .pipeline import analyze_comments_step, collect_comments_step, collect_profile_step, run_topic_package_pipeline


def _add_llm_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--llm", action="store_true", help="使用用户配置的 LLM 生成更专业的选题包")
    parser.add_argument("--llm-provider", default="", help="LLM Provider，例如 openai/deepseek/qwen/kimi/zhipu/minimax/anthropic/gemini/openai-compatible")
    parser.add_argument("--llm-model", default="", help="模型名称，由用户按自己的账号填写")
    parser.add_argument("--llm-api-key", default="", help="API Key；推荐写入 .env 的 LLM_API_KEY")
    parser.add_argument("--llm-base-url", default="", help="自定义 API 地址；openai-compatible 或私有网关需要填写")


def _build_llm_client(args: argparse.Namespace) -> LLMClient | None:
    if not getattr(args, "llm", False):
        return None
    return LLMClient(
        provider=getattr(args, "llm_provider", "") or "",
        model=getattr(args, "llm_model", "") or "",
        api_key=getattr(args, "llm_api_key", "") or "",
        base_url=getattr(args, "llm_base_url", "") or "",
    )


def _add_conversion_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--conversion-mode",
        choices=sorted(CONVERSION_MODE_INSTRUCTIONS),
        default="balanced",
        help="CTA conversion strength: balanced/conservative/strong",
    )


def _print_outputs(outputs: dict) -> None:
    print("输出文件：")
    for key, value in outputs.items():
        print(f"- {key}: {value}")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="douyin-topic-packager", description="抖音对标账号选题包生成工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    login = subparsers.add_parser("login", help="用 Playwright 登录抖音并保存 Cookie")
    login.add_argument("--state", default="runtime/douyin_storage_state.json", help="Cookie 保存路径")
    login.add_argument("--headless", action="store_true", help="无头浏览器模式，不建议首次登录使用")
    login.add_argument("--wait-seconds", type=int, default=0, help="打开浏览器后等待指定秒数再自动保存 Cookie")

    collect = subparsers.add_parser("collect", help="解析主页链接，采集 Top20 并按评论数排序")
    collect.add_argument("--profile-url", required=True, help="抖音博主主页分享链接或包含链接的整段分享文本")
    collect.add_argument("--top-n", type=int, default=20, help="采集数量，默认 Top20")
    collect.add_argument("--state", default="runtime/douyin_storage_state.json", help="Cookie 路径")
    collect.add_argument("--output-dir", default="outputs/topic_packages", help="输出目录")

    comments = subparsers.add_parser("comments", help="采集 Top 视频评论")
    comments.add_argument("--input", default="outputs/topic_packages/profile_videos.json", help="collect 生成的视频 JSON")
    comments.add_argument("--state", default="runtime/douyin_storage_state.json", help="Cookie 路径")
    comments.add_argument("--output-dir", default="outputs/topic_packages", help="输出目录")
    comments.add_argument("--max-comments-per-video", type=int, default=0, help="每条视频最多采集多少评论，0 表示不限")

    analyze = subparsers.add_parser("analyze", help="根据评论生成痛点、角度和选题包")
    analyze.add_argument("--videos", default="outputs/topic_packages/profile_videos.json", help="视频 JSON")
    analyze.add_argument("--comments", default="outputs/topic_packages/comments.json", help="评论 JSON")
    analyze.add_argument("--meta", default="outputs/topic_packages/profile_meta.json", help="collect 生成的元数据 JSON")
    analyze.add_argument("--source-url", default="", help="原始主页链接；留空时优先读取 meta")
    analyze.add_argument("--resolved-url", default="", help="解析后的主页链接；留空时优先读取 meta")
    analyze.add_argument("--sec-uid", default="", help="账号 sec_uid；留空时优先读取 meta")
    analyze.add_argument("--output-dir", default="outputs/topic_packages", help="输出目录")
    _add_llm_args(analyze)
    _add_conversion_args(analyze)

    run = subparsers.add_parser("run", help="从主页分享链接直接生成选题包")
    run.add_argument("--profile-url", required=True, help="抖音博主主页分享链接或包含链接的整段分享文本")
    run.add_argument("--top-n", type=int, default=20, help="采集数量，默认 Top20")
    run.add_argument("--state", default="runtime/douyin_storage_state.json", help="Cookie 路径")
    run.add_argument("--output-dir", default="outputs/topic_packages", help="输出目录")
    run.add_argument("--max-comments-per-video", type=int, default=0, help="每条视频最多采集多少评论，0 表示不限")
    _add_llm_args(run)
    _add_conversion_args(run)

    args = parser.parse_args()
    if args.command == "login":
        path = asyncio.run(save_douyin_login_state(args.state, headless=args.headless, wait_seconds=args.wait_seconds))
        print(f"Cookie 已保存：{path}")
        return

    if args.command == "collect":
        outputs = asyncio.run(
            collect_profile_step(
                args.profile_url,
                output_dir=args.output_dir,
                top_n=args.top_n,
                storage_state_path=args.state,
            )
        )
        _print_outputs(outputs)
        return

    if args.command == "comments":
        outputs = asyncio.run(
            collect_comments_step(
                args.input,
                output_dir=args.output_dir,
                storage_state_path=args.state,
                max_comments_per_video=args.max_comments_per_video,
            )
        )
        _print_outputs(outputs)
        return

    if args.command == "analyze":
        meta = {}
        if Path(args.meta).exists():
            meta = read_json(args.meta)
        outputs = analyze_comments_step(
            source_url=args.source_url or meta.get("source_url") or "",
            resolved_url=args.resolved_url or meta.get("resolved_url") or "",
            sec_uid=args.sec_uid or meta.get("sec_uid") or "",
            videos_path=args.videos,
            comments_path=args.comments,
            output_dir=args.output_dir,
            llm_client=_build_llm_client(args),
            conversion_mode=args.conversion_mode,
        )
        _print_outputs(outputs)
        return

    if args.command == "run":
        outputs = asyncio.run(
            run_topic_package_pipeline(
                profile_url=args.profile_url,
                output_dir=args.output_dir,
                top_n=args.top_n,
                storage_state_path=args.state,
                max_comments_per_video=args.max_comments_per_video,
                llm_client=_build_llm_client(args),
                conversion_mode=args.conversion_mode,
            )
        )
        _print_outputs(outputs)
        return


if __name__ == "__main__":
    main()
