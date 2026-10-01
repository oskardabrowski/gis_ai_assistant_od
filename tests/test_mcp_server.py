import json
from pathlib import Path
import subprocess
import sys

import pytest

from gis_assistant_ai.mcp_server import BridgeClient, MCPServer, validate_call


class Bridge:
    def call(self, name, arguments):
        validate_call(name, arguments)
        return {"layers": []}


def rpc(method, params=None, request_id=1):
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}


def test_initialize_and_tools():
    server = MCPServer(Bridge())
    assert "error" in server.handle(rpc("tools/list"))
    init = server.handle(rpc("initialize", {"protocolVersion": "2025-11-25"}))["result"]
    assert init["protocolVersion"] == "2025-11-25"
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert len(server.handle(rpc("tools/list"))["result"]["tools"]) == 6
    result = server.handle(rpc("tools/call", {"name": "qgis_get_context"}))["result"]
    assert not result["isError"]
    assert json.loads(result["content"][0]["text"]) == {"layers": []}


@pytest.mark.parametrize("name,args", [
    ("shell", {}), ("qgis_get_context", {"unknown": 1}), ("qgis_list_algorithms", {"limit": True}),
    ("qgis_list_algorithms", {"limit": 51}), ("qgis_list_algorithms", {"query": "x" * 201}),
    ("qgis_submit_plan", {"plan": []}), ("qgis_get_status", {}), ("qgis_get_context", []),
    ("qgis_revise_plan", {"plan_id": "x", "steps": []}),
])
def test_bad_tool_arguments(name, args):
    with pytest.raises(ValueError):
        validate_call(name, args)


@pytest.mark.parametrize("connection", [
    {"host": "example.com", "port": 80, "token": "a" * 64},
    {"host": "127.0.0.1", "port": True, "token": "a" * 64},
    {"host": "127.0.0.1", "port": 42, "token": "short"}, [],
])
def test_discovery_cannot_target_remote_host(tmp_path, connection):
    file = tmp_path / "connection.json"
    file.write_text(json.dumps(connection))
    with pytest.raises(ValueError):
        BridgeClient(file).call("qgis_get_context", {})


def test_malformed_rpc_does_not_crash():
    server = MCPServer(Bridge())
    for message in ([], {}, rpc("initialize", {"protocolVersion": []}),
                    rpc("ping", request_id={"bad": "id"})):
        assert "error" in server.handle(message)


def test_stdio_handshake_and_unavailable_bridge(tmp_path):
    script = Path(__file__).parents[1] / "mcp_server.py"
    messages = [rpc("initialize", {"protocolVersion": "2025-03-26"}),
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                rpc("tools/list", request_id=2), rpc("tools/call", {"name": "qgis_get_context"}, 3)]
    run = subprocess.run([sys.executable, str(script), "--connection-file", str(tmp_path / "missing")],
                         input="\n".join(map(json.dumps, messages)).encode(), capture_output=True, timeout=10)
    assert run.returncode == 0 and not run.stderr
    answers = list(map(json.loads, run.stdout.splitlines()))
    assert [answer["id"] for answer in answers] == [1, 2, 3]
    assert answers[-1]["result"]["isError"]
    assert str(tmp_path) not in str(answers)
