"""DeepSeek provider: PDF ingest, OpenAI message translation, routing, pricing."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.config import settings
from backend.services.llm.pdf_ingest import extract_pdf_text, make_text_pdf
from backend.services.llm.deepseek_provider import (
    _is_vision_model,
    _to_openai_tool,
    _to_openai_tool_choice,
    completion_from_openai,
    messages_to_openai,
)
from backend.services.llm.local_skill import load_skill_markdown, load_skill_validator
from backend.services.llm.pricing import PRICING, cost_for_entry
from backend.services.llm.types import (
    Message,
    PdfBlock,
    TextBlock,
    ToolCall,
    ToolResultBlock,
    ToolSchema,
)


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    pdf = tmp_path / "ds.pdf"
    pdf.write_bytes(make_text_pdf([
        "Pin configuration\n1 VCC Power\n2 GND Ground\n3 TXD UART transmit",
        "Absolute maximum ratings\nVCC 6.0 V",
    ]))
    return pdf


def test_extract_pdf_text_includes_page_markers(sample_pdf: Path):
    text = extract_pdf_text(sample_pdf)
    assert "page 1" in text.lower() or "--- page 1 ---" in text
    assert "VCC" in text
    assert sample_pdf.name in text


def test_vision_model_detection():
    assert _is_vision_model("deepseek-v4-flash-vision-exp")
    assert not _is_vision_model("deepseek-v4-pro")
    assert not _is_vision_model("deepseek-v4-flash")


def test_messages_to_openai_pdf_becomes_text(sample_pdf: Path):
    messages = [
        Message("user", [
            PdfBlock(path=sample_pdf, cacheable=True),
            TextBlock("Extract the pin table."),
        ]),
    ]
    out = messages_to_openai(messages, vision=False)
    assert len(out) == 1
    assert out[0]["role"] == "user"
    content = out[0]["content"]
    if isinstance(content, str):
        blob = content
    else:
        blob = " ".join(p.get("text", "") for p in content if p.get("type") == "text")
        assert not any(p.get("type") == "image_url" for p in content)
    assert "VCC" in blob
    assert "Extract the pin table" in blob


def test_thinking_only_assistant_sends_empty_content():
    """Thinking with no visible text and no tools must still set content."""
    out = messages_to_openai(
        [Message("assistant", [
            TextBlock("", reasoning_content="Need to inspect pin 3 first."),
        ])],
        vision=False,
    )
    assert out[0]["role"] == "assistant"
    assert out[0]["content"] == ""
    assert out[0]["reasoning_content"] == "Need to inspect pin 3 first."
    assert "tool_calls" not in out[0]


def test_tool_call_assistant_may_have_null_content():
    out = messages_to_openai(
        [Message("assistant", [
            ToolCall(id="c1", name="get_pintable", input={"ref": "U2"},
                     reasoning_content="look up pins"),
        ])],
        vision=False,
    )
    assert out[0]["content"] is None
    assert out[0]["tool_calls"][0]["function"]["name"] == "get_pintable"
    assert out[0]["reasoning_content"] == "look up pins"


def test_messages_to_openai_tool_roundtrip():
    messages = [
        Message("assistant", [
            TextBlock("checking", reasoning_content="I should query the net."),
            ToolCall(id="call_1", name="get_net_for_pin", input={"ref": "U2", "pin": "3"}),
        ]),
        Message("user", [
            ToolResultBlock(tool_use_id="call_1", name="get_net_for_pin", content="UART_TX"),
            TextBlock("continue"),
        ]),
    ]
    out = messages_to_openai(messages, vision=False)
    assert out[0]["role"] == "assistant"
    assert out[0]["reasoning_content"] == "I should query the net."
    assert out[0]["tool_calls"][0]["function"]["name"] == "get_net_for_pin"
    args = json.loads(out[0]["tool_calls"][0]["function"]["arguments"])
    assert args["ref"] == "U2"
    assert out[1]["role"] == "tool"
    assert out[1]["tool_call_id"] == "call_1"
    assert out[1]["content"] == "UART_TX"
    assert out[2]["role"] == "user"


def test_forced_tool_choice_disables_thinking():
    captured: dict = {}

    class FakeCompletions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            fn = SimpleNamespace(name="save_resolved_specs", arguments="{}")
            tc = SimpleNamespace(id="c1", function=fn)
            msg = SimpleNamespace(content=None, reasoning_content=None, tool_calls=[tc])
            usage = SimpleNamespace(
                prompt_tokens=10, completion_tokens=5,
                prompt_cache_hit_tokens=0, prompt_tokens_details=None,
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=msg, finish_reason="tool_calls")],
                usage=usage,
            )

    class FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    from backend.services.llm.deepseek_provider import DeepSeekSession
    session = DeepSeekSession(
        client=FakeClient(),
        model="deepseek-v4-flash",
        system="sys",
        max_tokens=256,
        thinking=True,
        reasoning_effort="medium",
    )
    import asyncio
    from backend.services.llm.types import Message, TextBlock, ToolSchema
    asyncio.run(session.complete(
        messages=[Message("user", [TextBlock("resolve")])],
        tools=[ToolSchema(name="save_resolved_specs", description="x", input_schema={"type": "object"})],
        tool_choice={"name": "save_resolved_specs"},
    ))
    assert captured["extra_body"]["thinking"]["type"] == "disabled"
    assert "reasoning_effort" not in captured["extra_body"]
    assert captured["tool_choice"]["function"]["name"] == "save_resolved_specs"


def test_tool_schema_and_choice():
    schema = ToolSchema(
        name="save_pintable",
        description="Save pins",
        input_schema={"type": "object", "properties": {}},
    )
    tool = _to_openai_tool(schema)
    assert tool["type"] == "function"
    assert tool["function"]["name"] == "save_pintable"
    assert _to_openai_tool_choice("auto") == "auto"
    forced = _to_openai_tool_choice({"name": "save_pintable"})
    assert forced["function"]["name"] == "save_pintable"


def test_completion_from_openai_parses_tools_and_cache():
    fn = SimpleNamespace(name="submit_review", arguments='{"findings":[]}')
    tc = SimpleNamespace(id="c1", function=fn)
    msg = SimpleNamespace(
        content="done",
        reasoning_content="step by step",
        tool_calls=[tc],
    )
    usage = SimpleNamespace(
        prompt_tokens=1000,
        completion_tokens=50,
        prompt_cache_hit_tokens=400,
        prompt_tokens_details=None,
    )
    resp = SimpleNamespace(
        choices=[SimpleNamespace(message=msg, finish_reason="tool_calls")],
        usage=usage,
    )
    completion = completion_from_openai(resp)
    assert completion.text == "done"
    assert completion.tool_calls[0].name == "submit_review"
    assert completion.tool_calls[0].input == {"findings": []}
    assert completion.usage.input_tokens == 600
    assert completion.usage.cache_read_tokens == 400
    assert completion.raw_assistant_blocks[0].reasoning_content == "step by step"


def test_local_skills_load():
    md = load_skill_markdown("extract-pintable")
    assert "pin table" in md.lower()
    validate = load_skill_validator("extract-pintable")
    assert validate is not None
    errors = validate({
        "mpn": "MSPM0G3507SPTR",
        "component_subtype": "ic.mcu",
        "component_subtype_description": "MCU",
        "package_info": {"base_family": "MSPM0", "package": "LQFP-48", "pin_count": 2},
        "pintable": [
            {"number": 1, "name": "VCC"},
            {"number": 2, "name": "GND"},
        ],
    })
    assert errors == []


def test_wroom_rejects_bare_soc_pin1_ant():
    from backend.services.llm.local_skill import load_skill_validator
    validate = load_skill_validator("extract-pintable")
    errors = validate({
        "mpn": "ESP32-S31-WROOM-3",
        "component_subtype": "ic.mcu",
        "package_info": {"base_family": "ESP32-S31", "package": "module", "pin_count": 2},
        "pintable": [
            {"number": 1, "name": "ANT"},
            {"number": 2, "name": "CHIP_PU"},
            {"number": 78, "name": "XTAL_N"},
            {"number": 79, "name": "XTAL_P"},
        ],
    })
    assert any("module" in e.lower() or "pad" in e.lower() or "SoC" in e or "WROOM" in e for e in errors)


def test_factory_routes_deepseek(monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "sk-test")
    from backend.services.llm.factory import get_provider_by_name
    get_provider_by_name.cache_clear()
    try:
        provider = get_provider_by_name("deepseek")
        assert provider.name == "deepseek"
    finally:
        get_provider_by_name.cache_clear()


def test_config_defaults_are_deepseek():
    assert settings.provider_default == "deepseek"
    assert settings.model_for_stage("validation") == settings.model_validation_deepseek
    assert "vision" in settings.model_for_stage("pintable")
    assert settings.provider_for_stage("pintable") == "deepseek"


def test_deepseek_pricing_positive():
    cost = cost_for_entry({
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
        "input_tokens": 1_000_000,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    })
    assert cost == pytest.approx(1.32)
    assert "default" in PRICING["deepseek"]
