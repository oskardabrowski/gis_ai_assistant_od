import pytest

from gis_assistant_ai.agent_protocol import AgentError
from gis_assistant_ai.codex_models import CodexModelStream, catalog_arguments, encode_message


def initialized():
    stream = CodexModelStream()
    replies = stream.feed(encode_message({"id": 0, "result": {}}))
    assert [r["method"] for r in replies] == ["initialized", "model/list"]
    assert replies[1]["params"] == {"limit": 100, "includeHidden": False}
    return stream


def test_fragmented_handshake_pages_and_visible_models():
    stream = CodexModelStream()
    reply = encode_message({"id": 0, "result": {}})
    assert not stream.feed(reply[:8])
    assert len(stream.feed(reply[8:])) == 2
    rows = [{"model": "model-a", "displayName": "Zażółć", "isDefault": True},
            {"model": "hidden", "hidden": True}, {"model": ""}, "invalid"]
    page = encode_message({"id": 1, "result": {"data": rows, "nextCursor": "next"}})
    sent = []
    for byte in page:
        sent.extend(stream.feed(bytes([byte])))
    assert sent == [{"id": 2, "method": "model/list",
                     "params": {"limit": 100, "includeHidden": False, "cursor": "next"}}]
    assert not stream.complete
    stream.feed(encode_message({"id": 2, "result": {
        "data": [{"model": "model-a"}, {"model": "model-b"}], "nextCursor": None}}))
    assert stream.complete
    assert [m["model"] for m in stream.models] == ["model-a", "model-b"]
    assert stream.models[0]["displayName"] == "Zażółć"


@pytest.mark.parametrize("reply", [
    {"id": 1, "error": {"message": "untrusted server text"}},
    {"id": 1, "result": []}, {"id": 1, "result": {}},
    {"id": 1, "result": {"data": []}},
    {"id": 1, "result": {"data": [], "nextCursor": []}},
])
def test_failed_empty_and_invalid_catalogs(reply):
    stream = initialized()
    with pytest.raises(AgentError) as error:
        stream.feed(encode_message(reply))
    assert "untrusted server text" not in str(error.value)
    assert not stream.complete


def test_repeated_cursor_rejected():
    stream = initialized()
    page = {"data": [{"model": "a"}], "nextCursor": "same"}
    stream.feed(encode_message({"id": 1, "result": page}))
    with pytest.raises(AgentError):
        stream.feed(encode_message({"id": 2, "result": page}))


def test_unrelated_notifications_and_truncated_page_are_not_success():
    stream = initialized()
    assert not stream.feed(encode_message({"method": "account/updated", "params": {}}))
    assert not stream.feed(encode_message({"id": 99, "result": {}}))
    assert not stream.feed(b'{"id":1,"result":{"data":[{"model":"a"}')
    assert not stream.complete


def test_output_limit(monkeypatch):
    monkeypatch.setattr("gis_assistant_ai.codex_models.MAX_AGENT_OUTPUT", 10)
    with pytest.raises(AgentError):
        CodexModelStream().feed(b" " * 11)


def test_catalog_is_metadata_only():
    assert CodexModelStream.initialize()["method"] == "initialize"
    args = catalog_arguments()
    assert args[0] == "app-server" and "stdio://" in args
    assert 'forced_login_method="chatgpt"' in args
    assert "exec" not in args and "--model" not in args
