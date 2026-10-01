"""Deterministic child process used by the Qt integration smoke test. No network."""
import json
import sys
import time

args = sys.argv[1:]
claude = "auth" in args or "-p" in args
if "status" in args:
    if "--no-login" in args:
        print("Not logged in", file=sys.stderr)
        sys.exit(1)
    if claude:
        print(json.dumps({"loggedIn": True, "authMethod": "claude.ai"}))
    else:
        print("Logged in using ChatGPT", file=sys.stderr)
elif "app-server" in args:
    for line in sys.stdin:
        message = json.loads(line)
        method = message["method"]
        assert method in {"initialize", "initialized", "model/list"}
        if method == "initialized":
            continue
        if method == "initialize":
            result = {}
        else:
            if "--slow-catalog" in args:
                time.sleep(30)
            more = message["params"].get("cursor") is None
            data = ([{"model": "test-alpha", "displayName": "Alpha", "isDefault": True},
                     {"model": "test-hidden", "hidden": True}] if more else [{"model": "test-beta"}])
            result = {"data": data, "nextCursor": "page2" if more else None}
        print(json.dumps({"id": message["id"], "result": result}), flush=True)
else:
    request = sys.stdin.read()
    assert "test-marker" in request
    if "slow" in args:
        time.sleep(30)
    text = json.dumps({"type": "answer", "text": "Zażółć"}, ensure_ascii=False)
    if claude:
        print(json.dumps({"type": "result", "subtype": "success", "result": text}))
    else:
        print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}}))
        print(json.dumps({"type": "turn.completed"}))
