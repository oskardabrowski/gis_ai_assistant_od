"""Read the account's model picker through Codex app-server model/list (no inference).

Protocol: https://learn.chatgpt.com/docs/app-server#models
"""
import json

from .agent_protocol import AgentError, MAX_AGENT_OUTPUT


def catalog_arguments():
    return ["app-server", "--listen", "stdio://", "-c", 'forced_login_method="chatgpt"',
            "-c", 'model_provider="openai"', "-c", "features.apps=false",
            "-c", "features.hooks=false", "-c", "features.remote_plugin=false",
            "-c", "analytics.enabled=false"]


def encode_message(message):
    return (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")


class CodexModelStream:
    def __init__(self):
        self.pending = b""
        self.total = 0
        self.expected_id = 0
        self.models = []
        self.ids = set()
        self.cursors = set()
        self.complete = False

    @staticmethod
    def initialize():
        return {"id": 0, "method": "initialize", "params": {
            "clientInfo": {"name": "gis_assistant_ai", "version": "0.4.3"}}}

    def feed(self, chunk):
        self.total += len(chunk)
        if self.total > MAX_AGENT_OUTPUT:
            raise AgentError("Lista modeli Codex przekroczyła dozwolony rozmiar.")
        lines = (self.pending + chunk).split(b"\n")
        self.pending = lines.pop()
        outgoing = []
        for line in lines:
            if not line.strip() or self.complete:
                continue
            try:
                message = json.loads(line)
            except (ValueError, UnicodeError) as exc:
                raise AgentError("Niepoprawna odpowiedź listy modeli. Zaktualizuj Codex CLI.") from exc
            if not isinstance(message, dict):
                raise AgentError("Niepoprawny format listy modeli Codex.")
            if message.get("id") != self.expected_id:
                continue  # Unrelated app-server notifications are not catalog responses.
            if "error" in message or not isinstance(message.get("result"), dict):
                raise AgentError("Codex nie udostępnił listy modeli. Sprawdź logowanie i wersję CLI.")
            result = message["result"]
            if self.expected_id == 0:
                outgoing.append({"method": "initialized", "params": {}})
                outgoing.append(self._next_page())
                continue
            if not isinstance(result.get("data"), list):
                raise AgentError("Brak danych w odpowiedzi listy modeli Codex.")
            for entry in result["data"]:
                if not isinstance(entry, dict) or entry.get("hidden"):
                    continue
                model = entry.get("model")
                if not isinstance(model, str) or not model.strip() or len(model) > 200:
                    continue
                model = model.strip()
                if model in self.ids:
                    continue
                self.ids.add(model)
                label = entry.get("displayName")
                self.models.append({"model": model, "displayName": label if isinstance(label, str) else model,
                                    "isDefault": entry.get("isDefault") is True})
            cursor = result.get("nextCursor")
            if cursor is not None:
                if not isinstance(cursor, str) or not cursor or cursor in self.cursors or self.expected_id >= 10:
                    raise AgentError("Niepoprawne stronicowanie listy modeli Codex.")
                self.cursors.add(cursor)
                outgoing.append(self._next_page(cursor))
            elif not self.models:
                raise AgentError("Codex zwrócił pustą listę modeli dla tego konta.")
            else:
                self.complete = True
        return outgoing

    def _next_page(self, cursor=None):
        self.expected_id += 1
        params = {"limit": 100, "includeHidden": False}
        if cursor is not None:
            params["cursor"] = cursor
        return {"id": self.expected_id, "method": "model/list", "params": params}
