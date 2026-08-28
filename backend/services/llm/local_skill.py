"""Provider-agnostic local skill runner.

Anthropic Console Skills have no equivalent on DeepSeek (or Gemini). This
module inlines ``skills/<name>/SKILL.md`` as the system prompt, drives a
normal tool-calling session, and runs ``validate.py`` locally after each
``output_tool`` call. Used by DeepSeek and Gemini; Anthropic falls back
here when no Console skill id is configured.
"""

from __future__ import annotations

import importlib.util
import logging
import re
import time
from pathlib import Path

from backend.config import settings
from backend.services.llm.base import LLMProvider
from backend.services.llm.types import (
    Completion,
    Message,
    PdfBlock,
    TextBlock,
    ToolResultBlock,
    ToolSchema,
    Usage,
)

log = logging.getLogger(__name__)

_SKILL_MAX_TURNS = 10
_FRONTMATTER = re.compile(r"^---\n.*?\n---\n", re.DOTALL)

_LOCAL_SKILL_TAIL = """
You cannot run shell commands or Python. Do not try to execute validate.py.
After extracting the data, call the `{tool}` tool with the structured result.
The server validates the payload. If validation fails you will receive the
errors and must call `{tool}` again with a corrected payload.
Do NOT write files to disk.
"""


def skills_dir() -> Path:
    return Path(settings.skills_dir)


def load_skill_markdown(skill_name: str) -> str:
    path = skills_dir() / skill_name / "SKILL.md"
    if not path.is_file():
        raise FileNotFoundError(
            f"Skill {skill_name!r} not found at {path}. "
            f"Expected skills/{skill_name}/SKILL.md in the repo."
        )
    raw = path.read_text(encoding="utf-8")
    return _FRONTMATTER.sub("", raw).strip()


def load_skill_validator(skill_name: str):
    """Import ``skills/<name>/validate.py`` and return its ``validate`` fn."""
    path = skills_dir() / skill_name / "validate.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(
        f"pinscope_skill_{skill_name.replace('-', '_')}_validate", path,
    )
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fn = getattr(mod, "validate", None)
    return fn if callable(fn) else None


def _sum_usage(total: Usage, piece: Usage) -> Usage:
    return Usage(
        input_tokens=total.input_tokens + piece.input_tokens,
        output_tokens=total.output_tokens + piece.output_tokens,
        cache_creation_tokens=total.cache_creation_tokens + piece.cache_creation_tokens,
        cache_read_tokens=total.cache_read_tokens + piece.cache_read_tokens,
    )


async def run_skill_locally(
    provider: LLMProvider,
    *,
    skill_name: str,
    model: str,
    system: str,
    user_text: str,
    pdf_path: str | None,
    output_tool: ToolSchema,
    max_turns: int = _SKILL_MAX_TURNS,
) -> tuple[dict, Completion]:
    """Run ``skill_name`` as an in-process tool loop on ``provider``."""
    skill_md = load_skill_markdown(skill_name)
    validator = load_skill_validator(skill_name)
    full_system = (
        skill_md
        + "\n\n"
        + system.strip()
        + "\n"
        + _LOCAL_SKILL_TAIL.format(tool=output_tool.name)
    )

    user_blocks: list = []
    if pdf_path:
        user_blocks.append(PdfBlock(path=Path(pdf_path), cacheable=True))
    user_blocks.append(TextBlock(text=user_text, cacheable=True))
    messages: list[Message] = [Message(role="user", content=user_blocks)]

    total = Usage()
    t0 = time.monotonic()
    last_completion: Completion | None = None

    session = await provider.create_session(
        model=model, system=full_system, max_tokens=16384, temperature=0.0,
    )
    try:
        for turn in range(max_turns):
            force = turn >= max_turns - 2
            completion = await session.complete(
                messages=messages,
                tools=[output_tool],
                tool_choice={"name": output_tool.name} if force else "auto",
            )
            last_completion = completion
            total = _sum_usage(total, completion.usage)

            payload: dict | None = None
            for tc in completion.tool_calls:
                if tc.name == output_tool.name:
                    payload = dict(tc.input)
                    break

            messages.append(Message(
                role="assistant", content=completion.raw_assistant_blocks,
            ))

            if payload is None:
                messages.append(Message(role="user", content=[TextBlock(
                    text=(
                        f"You did not call {output_tool.name}. "
                        f"Call it now with the extracted data."
                    ),
                )]))
                continue

            errors: list[str] = []
            if validator is not None:
                check = dict(payload)
                mpn_hint = re.search(r"MPN:\s*(\S+)", user_text or "", re.I)
                if mpn_hint and "mpn" not in check:
                    check["mpn"] = mpn_hint.group(1).rstrip(".,;")
                try:
                    errors = list(validator(check) or [])
                except Exception as exc:
                    log.warning(
                        "Skill %s validate.py raised: %s", skill_name, exc,
                    )
                    errors = [f"validator crashed: {exc}"]

            if not errors:
                completion.usage = total
                completion.turns = turn + 1  # type: ignore[attr-defined]
                completion.duration_ms = int((time.monotonic() - t0) * 1000)  # type: ignore[attr-defined]
                return payload, completion

            messages.append(Message(
                role="user",
                content=[
                    ToolResultBlock(
                        tool_use_id=completion.tool_calls[0].id,
                        name=output_tool.name,
                        content="VALIDATION FAILED:\n" + "\n".join(
                            f"- {e}" for e in errors
                        ),
                    ),
                    TextBlock(
                        text=(
                            "Fix the payload and call "
                            f"{output_tool.name} again."
                        ),
                    ),
                ],
            ))
    finally:
        await session.close()

    raise RuntimeError(
        f"Skill {skill_name!r} did not produce a valid {output_tool.name} "
        f"in {max_turns} turns"
        + (f" (last stop_reason={last_completion.stop_reason})"
           if last_completion else "")
    )
