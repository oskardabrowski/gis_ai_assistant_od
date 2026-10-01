"""Standalone MCP stdio server for an open QGIS session (Python standard library only)."""
import argparse
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request

MAX_MESSAGE = 1024 * 1024
PROTOCOLS = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}

STEP_SCHEMA = {"type": "object", "required": ["id", "action", "title"], "properties": {
    "id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$"},
    "action": {"type": "string", "enum": ["processing", "load_layer", "uldk_boundary", "user_action", "zoom",
                                              "processing_dialog", "python", "note"]},
    "title": {"type": "string"}, "description": {"type": "string"},
    "algorithm": {"type": "string", "description": "Exact installed Processing algorithm id"},
    "params": {"type": "object", "description": "Processing parameters. INPUT = layer id or {{step.OUTPUT}}. "
               "Use TEMPORARY_OUTPUT for destinations. Distances use layer CRS units; reproject degrees first."},
    "output_name": {"type": "string"},
    "layer": {"type": "string", "description": "For zoom: layer id or {{step.OUTPUT}}"},
    "uri": {"type": "string", "description": "For load_layer: known source URI; never invent credentials"},
    "provider": {"type": "string"}, "name": {"type": "string"},
    "message": {"type": "string", "description": "For user_action: instructions for the human"},
    "expects_layer": {"type": "object", "properties": {"geometry": {"type": "string"}, "hint": {"type": "string"}}},
    "replan_after": {"type": "boolean"},
    "code": {"type": "string", "description": "Only if context.python_allowed; separate QGIS confirmation required"},
}}
STEPS_SCHEMA = {"type": "array", "items": STEP_SCHEMA, "minItems": 1, "maxItems": 60}
PLAN_SCHEMA = {"type": "object", "required": ["type", "steps"], "properties": {
    "type": {"const": "plan", "type": "string"}, "title": {"type": "string"},
    "summary": {"type": "string"}, "steps": STEPS_SCHEMA}}


class ToolError(ValueError):
    """Safe user-facing rejection returned by our authenticated QGIS bridge."""


def _tool(name, description, properties=None, required=(), read_only=True):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties or {},
                            "required": list(required), "additionalProperties": False},
            "annotations": {"readOnlyHint": read_only, "destructiveHint": False,
                            "idempotentHint": read_only, "openWorldHint": False}}


TOOLS = [
    _tool("qgis_get_context", "Read layers, fields, CRS and available providers of the open QGIS project."),
    _tool("qgis_list_algorithms", "Find installed QGIS Processing algorithms. Inspect one before planning.",
          {"query": {"type": "string", "maxLength": 200},
           "limit": {"type": "integer", "minimum": 1, "maximum": 50}}),
    _tool("qgis_describe_algorithm", "Read the exact parameters, enum choices and outputs of an algorithm.",
          {"algorithm": {"type": "string", "minLength": 1, "maxLength": 200}}, ["algorithm"]),
    _tool("qgis_submit_plan", "Show a plan in QGIS for HUMAN approval. Does NOT execute it. "
          "Use processing, zoom, load_layer, user_action, note, processing_dialog or python actions. "
          "Use unique step ids and {{step.OUTPUT}} references. Processing steps need algorithm and params. "
          "Read context and algorithm specs first. The user clicks RUN in QGIS; do not poll repeatedly.",
          {"plan": PLAN_SCHEMA}, ["plan"], False),
    _tool("qgis_get_status", "Read the status of your submitted plan. Differentiate skipped, failed and done. "
          "Poll only when needed; do not repeatedly poll while waiting for human approval.",
          {"plan_id": {"type": "string", "minLength": 1, "maxLength": 64}}, ["plan_id"]),
    _tool("qgis_revise_plan", "Replace the remaining steps when status is awaiting_revision or failed. "
          "Use NEW unique ids; references to completed steps remain valid. "
          "A human must approve continuation in QGIS. This does not restart completed steps.",
          {"plan_id": {"type": "string", "minLength": 1, "maxLength": 64},
           "steps": STEPS_SCHEMA},
          ["plan_id", "steps"], False),
]


def validate_call(name, arguments):
    tool = next((item for item in TOOLS if item["name"] == name), None)
    if tool is None:
        raise ValueError("Unknown tool")
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be an object")
    schema = tool["inputSchema"]
    if set(arguments) - set(schema["properties"]) or set(schema["required"]) - set(arguments):
        raise ValueError("Missing or unknown tool arguments")
    for key, value in arguments.items():
        rule = schema["properties"][key]
        expected = {"string": str, "integer": int, "object": dict, "array": list}[rule["type"]]
        if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
            raise ValueError("Invalid type for " + key)
        if isinstance(value, str) and not rule.get("minLength", 0) <= len(value) <= rule.get("maxLength", MAX_MESSAGE):
            raise ValueError("Invalid length for " + key)
        if expected is int and not rule.get("minimum", value) <= value <= rule.get("maximum", value):
            raise ValueError("Invalid value for " + key)
        if expected is list and (not rule.get("minItems", 0) <= len(value) <= rule.get("maxItems", 60)
                                 or not all(isinstance(item, dict) for item in value)):
            raise ValueError("Invalid array for " + key)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class BridgeClient:
    def __init__(self, connection_file):
        self.connection_file = Path(connection_file)

    def call(self, name, arguments):
        validate_call(name, arguments)
        with self.connection_file.open("rb") as handle:
            raw = handle.read(8193)
        if len(raw) > 8192:
            raise ValueError("Invalid connection file")
        connection = json.loads(raw)
        if not isinstance(connection, dict):
            raise ValueError("Invalid connection file")
        port, token = connection.get("port"), connection.get("token")
        if connection.get("host") != "127.0.0.1" or type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Connection must point to local QGIS")
        if not isinstance(token, str) or len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
            raise ValueError("Invalid connection token")
        body = json.dumps({"name": name, "arguments": arguments}, ensure_ascii=False).encode("utf-8")
        if len(body) > MAX_MESSAGE:
            raise ValueError("Request too large")
        request = urllib.request.Request("http://127.0.0.1:%d/tool" % port, body,
                                         {"Content-Type": "application/json", "Authorization": "Bearer " + token})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        try:
            with opener.open(request, timeout=15) as reply:
                data = reply.read(MAX_MESSAGE + 1)
        except urllib.error.HTTPError as exc:
            exc.close()
            raise ValueError("QGIS bridge rejected the request (HTTP %d). Check the QGIS panel." % exc.code) from exc
        if len(data) > MAX_MESSAGE:
            raise ValueError("QGIS response too large")
        payload = json.loads(data)
        if "error" in payload:
            raise ToolError(str(payload["error"])[:1500])
        return payload["result"]


class MCPServer:
    def __init__(self, bridge):
        self.bridge = bridge
        self.initialized = False

    def handle(self, message):
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return self._error(None, -32600, "Invalid Request")
        request_id = message.get("id")
        if isinstance(request_id, (dict, list, bool)):
            return self._error(None, -32600, "Invalid id")
        if "id" not in message:  # notifications have no response
            return None
        method, params = message.get("method"), message.get("params", {})
        if not isinstance(params, dict):
            return self._error(request_id, -32602, "Invalid params")
        if method == "initialize":
            self.initialized = True
            version = params.get("protocolVersion")
            if not isinstance(version, str):
                return self._error(request_id, -32602, "protocolVersion must be a string")
            result = {"protocolVersion": version if version in PROTOCOLS else "2025-03-26",
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "qgis-assistant", "version": "0.5.0"},
                      "instructions": "QGIS tools act on the currently open project. Inspect context and algorithm "
                      "parameters before proposing a plan. Submission and revision only stage work for human "
                      "approval in QGIS. Never claim a plan ran before checking status. Never poll in a tight loop."}
        elif method == "ping":
            result = {}
        elif not self.initialized:
            return self._error(request_id, -32000, "Initialize first")
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            try:
                output = self.bridge.call(params.get("name"), params.get("arguments", {}))
                result = {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}],
                          "isError": False}
            except ToolError as exc:
                result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
            except (ValueError, OSError, KeyError, TypeError):
                result = {"content": [{"type": "text", "text": "QGIS rejected the request or is unavailable. "
                                      "Check the plan, open the plugin panel, enable MCP, and inspect QGIS messages."}],
                          "isError": True}
        elif method in ("resources/list", "prompts/list"):
            result = {"resources" if method.startswith("resources") else "prompts": []}
        else:
            return self._error(request_id, -32601, "Method not found")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id, code, message):
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection-file", required=True)
    args = parser.parse_args()
    server = MCPServer(BridgeClient(args.connection_file))
    while True:
        line = sys.stdin.buffer.readline(MAX_MESSAGE + 1)
        if not line:
            return
        if len(line) > MAX_MESSAGE:
            print("MCP input exceeds size limit", file=sys.stderr)
            return
        if not line.strip():
            continue
        try:
            response = server.handle(json.loads(line))
        except (ValueError, UnicodeError):
            response = server._error(None, -32700, "Parse error")
        if response is not None:
            sys.stdout.buffer.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
            sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
