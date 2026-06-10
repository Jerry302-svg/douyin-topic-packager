from douyin_topic_packager.llm import LLMClient, parse_json_from_llm_text, provider_presets


def test_parse_json_from_llm_text_with_think_and_markdown():
    raw = "<think>hidden</think>\n```json\n{\"topic_packages\": [{\"brief_title\": \"A\"}]}\n```"
    assert parse_json_from_llm_text(raw)["topic_packages"][0]["brief_title"] == "A"


def test_parse_json_from_llm_text_with_prefix_after_think():
    raw = "<think>hidden</think>\nHere is JSON:\n{\"topic_packages\": [{\"brief_title\": \"A\"}]}"
    assert parse_json_from_llm_text(raw)["topic_packages"][0]["brief_title"] == "A"


def test_provider_presets_include_mainstream_models():
    presets = provider_presets()
    for key in ["openai", "deepseek", "qwen", "kimi", "minimax", "anthropic", "gemini"]:
        assert key in presets


def test_llm_client_accepts_direct_config():
    client = LLMClient(provider="openai", model="gpt-4o", api_key="test")
    assert client.config.provider == "openai"
    assert client.config.model == "gpt-4o"
