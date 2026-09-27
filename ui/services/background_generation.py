"""Small background generations follow Anam's selected provider, without chat tools.

Identity jobs honor identity overrides. CLI jobs are ephemeral and never fall back
to a paid API. Research enables only web search; callers own all persistence.
"""

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path

import config as cfg
from services.log_redaction import redact_text

log = logging.getLogger(__name__)
VALID_PROVIDERS = {"auto", "codex", "claude-code", "anthropic", "openai", "openrouter", "lmstudio", "ollama"}


async def resolve_background_provider(provider="auto", model="", identity=None):
    from services.provider_router import _load_provider, resolve_provider_for_identity

    selected, options = await (
        resolve_provider_for_identity(identity) if identity else _load_provider()
    )
    following = provider in ("auto", "")
    provider = selected if following else provider
    options = dict(options) if provider == selected else {}
    # A stored Scribe model belongs to its explicit provider, never to auto.
    if following:
        model = ""
    if not model:
        if provider in {"claude-code", "anthropic"}:
            model = cfg.SCRIBE_MODEL
        elif provider == "codex":
            from services.codex_cli import get_codex_default_model_id

            model = options.get("model") or get_codex_default_model_id()
        elif provider == "openrouter":
            model = options.get("model") or cfg.SCRIBE_OPENROUTER_MODEL
        else:
            model = options.get("model") or ("gpt-4o" if provider == "openai" else "")
    return provider, model, options


async def generate_background_text(prompt, *, system_prompt, identity=None,
                                   provider="auto", model="", research=False):
    provider, model, options = await resolve_background_provider(provider, model, identity)
    log.info("Background generation: identity=%s provider=%s model=%s research=%s",
             identity or "scribe", provider, model, research)
    if provider == "codex":
        return await _generate_with_codex(model, prompt, system_prompt, research=research)
    if research:
        raise ValueError(f"Background web research is not supported by {provider}; no provider fallback was used")
    if provider == "claude-code":
        from services.scribe import _generate_with_claude_code

        return await _generate_with_claude_code(model, prompt, system_prompt=system_prompt)
    if provider == "anthropic":
        from services.claude_api import _get_client

        response = await (await _get_client()).messages.create(
            model=model, max_tokens=2000, system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "\n".join(block.text for block in response.content if block.type == "text")
    elif provider in {"openai", "openrouter", "lmstudio", "ollama"}:
        from openai import AsyncOpenAI

        defaults = {"openrouter": "https://openrouter.ai/api/v1",
                    "lmstudio": "http://localhost:1234/v1", "ollama": "http://localhost:11434/v1"}
        key = options.get("api_key") or os.environ.get(f"{provider.upper()}_API_KEY", "")
        if not key and provider in {"lmstudio", "ollama"}:
            key = provider
        if not key:
            raise RuntimeError(f"{provider} background generation needs its configured API key")
        async with AsyncOpenAI(api_key=key, base_url=options.get("base_url") or defaults.get(provider), timeout=180) as client:
            response = await client.chat.completions.create(
                model=model, max_completion_tokens=2000,
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}],
            )
        text = response.choices[0].message.content or ""
    else:
        raise ValueError(f"Background generation is not supported by {provider}; no provider fallback was used")
    if not text.strip():
        raise RuntimeError(f"{provider} returned empty background output")
    return text.strip()


async def _generate_with_codex(model, prompt, system_prompt, *, research=False):
    from services.codex_cli import find_codex_executable, normalize_codex_model

    executable = find_codex_executable()
    if not executable:
        raise RuntimeError("Codex CLI not found")
    # Run outside the repository so its chat/companion instructions cannot turn
    # a summary into an interactive session. Auth still comes from CODEX_HOME.
    with tempfile.TemporaryDirectory(prefix="anam-background-") as directory:
        output = Path(directory) / "reply.txt"
        command = [executable, "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
                   "--skip-git-repo-check", "--sandbox", "read-only", "--json",
                   "--output-last-message", str(output), "-c", "project_doc_max_bytes=0",
                   "-c", "mcp_servers={}", "-c", 'approval_policy="never"',
                   "-c", 'model_reasoning_effort="medium"',
                   "-c", f'web_search="{"live" if research else "disabled"}"']
        for feature in ("shell_tool", "unified_exec", "apps", "plugins", "hooks", "memories",
                        "multi_agent", "browser_use", "computer_use", "image_generation", "skill_search"):
            command += ["--disable", feature]
        if model:
            command += ["--model", normalize_codex_model(model)]
        env = dict(os.environ)
        for name in ("CLAUDECODE", "OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            env.pop(name, None)
        proc = await asyncio.create_subprocess_exec(
            *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, cwd=directory, env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(f"{system_prompt}\n\n{prompt}".encode("utf-8")), timeout=600,
            )
        except (TimeoutError, asyncio.CancelledError) as exc:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            if isinstance(exc, TimeoutError):
                raise RuntimeError("Codex background generation timed out after 600 seconds") from exc
            raise
        if proc.returncode:
            detail = redact_text((stdout + b"\n" + stderr).decode("utf-8", errors="replace"))
            raise RuntimeError(f"Codex background generation exited with {proc.returncode}: {detail[-2400:]}")
        searches = 0
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "item.completed" and event.get("item", {}).get("type") == "web_search":
                searches += 1
        log.info("Codex background generation completed: research=%s web_searches=%d", research, searches)
        if research and not searches:
            raise RuntimeError("Codex research returned without a verified web search; tray was not saved")
        text = output.read_text(encoding="utf-8").strip() if output.exists() else ""
        if not text:
            raise RuntimeError("Codex returned empty background output")
        return text
