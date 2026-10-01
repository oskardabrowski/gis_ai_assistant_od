"""Opt-in real Claude Code protocol test backed by LOCAL Ollama, without an Anthropic subscription.

python tests/claude_ollama_smoke.py --model qwen3.5:9b
No model downloads, global configuration changes, or subscription-authentication claims.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import urllib.request

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("agent_protocol", root / "agent_protocol.py")
protocol = importlib.util.module_from_spec(spec)
spec.loader.exec_module(protocol)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--executable", default="")
    args = parser.parse_args()
    if "cloud" in args.model.lower():
        parser.error("This smoke test only permits a locally installed model.")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request("http://127.0.0.1:11434/api/show",
                                     json.dumps({"model": args.model}).encode(),
                                     {"Content-Type": "application/json"})
    with opener.open(request, timeout=10) as reply:
        model_info = json.load(reply)
    if model_info.get("remote_model") or model_info.get("remote_host"):
        parser.error("A remote model is not allowed in this local smoke test.")
    env = protocol.agent_environment(os.environ)
    env.update({"ANTHROPIC_BASE_URL": "http://127.0.0.1:11434", "ANTHROPIC_AUTH_TOKEN": "ollama",
                "ANTHROPIC_API_KEY": "", "ANTHROPIC_DEFAULT_HAIKU_MODEL": args.model,
                "ANTHROPIC_SMALL_FAST_MODEL": args.model, "CLAUDE_CODE_SUBAGENT_MODEL": args.model,
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_TELEMETRY": "1"})
    program, prefix = protocol.resolve_command("claude_code", args.executable, env)
    cli_args = protocol.request_arguments("claude_code", args.model)
    setting_index = cli_args.index("--settings") + 1
    settings = json.loads(cli_args[setting_index])
    # Explicit test-only override. The production provider still requires claude.ai authentication.
    settings.pop("forceLoginMethod")
    cli_args[setting_index] = json.dumps(settings)
    cli_args.extend(["--max-turns", "1"])
    payload = protocol.request_payload(
        'Reply with exactly {"type":"answer","text":"LOCAL_OK"}. No explanation.', [])
    print("Testing installed Claude Code with local Ollama model:", args.model, flush=True)
    with tempfile.TemporaryDirectory(prefix="qgis-claude-ollama-") as work:
        result = subprocess.run([program] + prefix + cli_args, input=payload, capture_output=True,
                                env=env, cwd=work, timeout=180)
    stream = protocol.AgentStream("claude_code")
    stream.feed(result.stdout)
    try:
        answer = json.loads(stream.finish(result.returncode))
    except (protocol.AgentError, ValueError):
        print("CLI exit:", result.returncode, "stdout bytes:", len(result.stdout))
        # This test supplies only a public synthetic prompt and the dummy Ollama credential.
        print(result.stderr.decode("utf-8", "replace")[-2000:])
        raise
    assert answer == {"type": "answer", "text": "LOCAL_OK"}, answer
    print("PASS: real Claude Code -> local Ollama -> stream-json -> production parser; complete JSON response.")
    print("Subscription login and Anthropic-hosted models were not tested.")


if __name__ == "__main__":
    main()
