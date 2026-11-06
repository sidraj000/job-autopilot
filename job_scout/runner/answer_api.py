"""Model backend for screening, form answers, essays and cover letters.

Two interchangeable backends:
  - Anthropic API (official `anthropic` SDK) when ANTHROPIC_API_KEY is set.
    The request carries no tools at all.
  - otherwise the local `claude` CLI (Claude Code, uses its own login), run
    from an empty temp dir with every tool, MCP server and connector
    disabled (LOCKDOWN; answerer.assert_sandboxed verifies it at startup).
Either way, text injected into a job posting can at most produce a bad
answer; it can't read files, run commands, browse, or message anyone.
"""
import json
import re
import subprocess
import tempfile

from . import config

LOCKDOWN = ["--tools", "", "--restricted", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
EFFORT = config.get("RUNNER_EFFORT", "medium")


def use_api():
    return bool(config.get("ANTHROPIC_API_KEY"))


def _client():
    import anthropic  # only needed for the API backend
    return anthropic.Anthropic()


def _strict(schema):
    """Structured outputs need additionalProperties: false on every object."""
    if isinstance(schema, dict):
        out = {k: _strict(v) for k, v in schema.items()}
        if out.get("type") == "object" or (isinstance(out.get("type"), list) and "object" in out["type"]):
            out.setdefault("additionalProperties", False)
        return out
    if isinstance(schema, list):
        return [_strict(x) for x in schema]
    return schema


def _api(prompt, system=None, schema=None):
    output_config = {"effort": EFFORT}
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": _strict(schema)}
    kw = {"system": system} if system else {}
    resp = _client().beta.messages.create(
        model=config.CLAUDE_MODEL, max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config=output_config,
        messages=[{"role": "user", "content": prompt}], **kw)
    if resp.stop_reason == "refusal":
        raise RuntimeError("model declined the request")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("model reply truncated (max_tokens)")
    return "".join(b.text for b in resp.content if b.type == "text")


def _cli(args, prompt, timeout):
    with tempfile.TemporaryDirectory() as empty:
        proc = subprocess.run([config.CLAUDE_BIN, "-p", *LOCKDOWN, "--model", config.CLAUDE_MODEL, *args],
                              input=prompt, capture_output=True, text=True, cwd=empty, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI failed ({proc.returncode}): {(proc.stderr or proc.stdout)[-500:]}")
    return proc.stdout


def ask(prompt, timeout=600):
    """The model's plain-text reply."""
    if use_api():
        return _api(prompt)
    return _cli([], prompt, timeout)


def ask_structured(system, prompt, schema, timeout=600):
    """A dict matching `schema`."""
    if use_api():
        return json.loads(_api(prompt, system=system, schema=schema))
    out = json.loads(_cli(["--output-format", "json", "--system-prompt", system,
                           "--json-schema", json.dumps(schema)], prompt, timeout))
    if out.get("is_error") or not out.get("structured_output"):
        raise RuntimeError(f"claude CLI returned no structured output: {str(out.get('result'))[:500]}")
    return out["structured_output"]


def ask_json(prompt, timeout=600):
    """ask() but parse a JSON object out of the reply (handles code fences)."""
    txt = ask(prompt, timeout)
    m = re.search(r"```(?:json)?\s*(\{.*\}|\[.*\])\s*```", txt, re.S) or \
        re.search(r"(\{.*\}|\[.*\])", txt, re.S)
    return json.loads(m.group(1) if m else txt)
