import json

import pytest

from gis_assistant_ai.agent_protocol import (
    AgentError, AgentStream, agent_environment, auth_arguments, request_arguments,
    request_payload, require_subscription, resolve_command,
)


def events(*items):
    return b"\n".join(json.dumps(item, ensure_ascii=False).encode() for item in items)


@pytest.mark.parametrize("provider", ["codex", "claude_code"])
def test_utf8_fragmented_response(provider):
    text = '{"type":"answer","text":"Zażółć 🗺"}'
    raw = events({"type": "item.completed", "item": {"type": "agent_message", "text": text}},
                 {"type": "turn.completed"}) if provider == "codex" else events(
        {"type": "stream_event", "event": {"delta": {"text": "Zażółć"}}},
        {"type": "result", "subtype": "success", "is_error": False, "result": text})
    stream = AgentStream(provider)
    for byte in raw:
        stream.feed(bytes([byte]))
    assert stream.finish(0) == text
    assert stream.chars >= len(text)


@pytest.mark.parametrize("provider,data,exit_code", [
    ("codex", events({"type": "item.completed", "item": {"type": "agent_message", "text": "partial"}}), 0),
    ("codex", events({"type": "turn.failed"}), 0),
    ("codex", events({"type": "turn.completed"}), 1),
    ("claude_code", events({"type": "result", "subtype": "error_max_turns", "is_error": True}), 0),
    ("claude_code", b'not json', 0),
    ("claude_code", b'{"type":"result","subtype":"success","result":123}', 0),
])
def test_incomplete_failed_and_malformed_output_rejected(provider, data, exit_code):
    with pytest.raises(AgentError):
        stream = AgentStream(provider)
        stream.feed(data)
        stream.finish(exit_code)


def test_output_limit(monkeypatch):
    monkeypatch.setattr("gis_assistant_ai.agent_protocol.MAX_AGENT_OUTPUT", 10)
    with pytest.raises(AgentError):
        AgentStream("codex").feed(b" " * 11)


@pytest.mark.parametrize("provider,stdout,stderr,code", [
    ("codex", "", "Logged in using an API key", 0),
    ("codex", "", "Not logged in", 1),
    ("codex", "", "Logged in using ChatGPT", 1),
    ("claude_code", '{"loggedIn":true,"authMethod":"api_key"}', "", 0),
    ("claude_code", '{"loggedIn":false,"authMethod":"claude.ai"}', "", 0),
    ("claude_code", '{"loggedIn":true,"authMethod":"claude.ai","apiKeySource":"env"}', "", 0),
    ("claude_code", '[]', "", 0),
])
def test_no_api_fallback(provider, stdout, stderr, code):
    with pytest.raises(AgentError):
        require_subscription(provider, stdout, stderr, code)


def test_subscription_auth():
    require_subscription("codex", "", "Logged in using ChatGPT", 0)
    require_subscription("claude_code", '{"loggedIn":true,"authMethod":"claude.ai"}', "", 0)
    assert auth_arguments("claude_code") == ["auth", "status", "--json"]


def test_environment_and_arguments_are_subscription_only():
    env = agent_environment({"OPENAI_API_KEY": "secret", "ANTHROPIC_AUTH_TOKEN": "secret",
                             "CLAUDE_CODE_USE_BEDROCK": "1", "HOME": "home", "PATH": "path"})
    assert env == {"HOME": "home", "PATH": "path"}
    claude = request_arguments("claude_code")
    assert "--bare" not in claude
    assert claude[claude.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in claude and "--setting-sources" in claude
    codex = request_arguments("codex")
    assert 'forced_login_method="chatgpt"' in codex
    assert "--ignore-user-config" in codex and codex[-1] == "-"


def test_prompt_goes_to_stdin_not_command_line():
    prompt = 'Działki $(echo secret); "test"'
    payload = request_payload("system", [{"role": "user", "content": prompt}])
    assert prompt == json.loads(payload.split(b"\n", 1)[1])["messages"][0]["content"]
    assert prompt not in request_arguments("codex")


def test_missing_command_and_native_path(tmp_path):
    with pytest.raises(AgentError):
        resolve_command("codex", str(tmp_path / "missing.exe"))
    exe = tmp_path / "space path" / "codex.exe"
    exe.parent.mkdir()
    exe.touch()
    assert resolve_command("codex", str(exe)) == (str(exe), [])


def test_unknown_provider_rejected():
    with pytest.raises(AgentError):
        resolve_command("other")


def test_named_command_uses_path(tmp_path, monkeypatch):
    executable = tmp_path / "claude.exe"
    executable.touch()
    monkeypatch.setattr("gis_assistant_ai.agent_protocol.shutil.which", lambda *args, **kwargs: str(executable))
    assert resolve_command("claude_code", "claude") == (str(executable), [])


def test_codex_npm_launcher_resolves_native_process(tmp_path, monkeypatch):
    monkeypatch.setattr("gis_assistant_ai.agent_protocol.platform.machine", lambda: "AMD64")
    launcher = tmp_path / "codex.cmd"
    launcher.touch()
    native = (tmp_path / "node_modules/@openai/codex/node_modules/@openai/codex-win32-x64" /
              "vendor/x86_64-pc-windows-msvc/bin/codex.exe")
    native.parent.mkdir(parents=True)
    native.touch()
    assert resolve_command("codex", str(launcher)) == (str(native), [])
