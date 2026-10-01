# -*- coding: utf-8 -*-
"""Asynchroniczny klient modeli językowych.

Zapytania idą przez QgsNetworkAccessManager, więc respektują ustawienia proxy
i certyfikatów QGIS, a interfejs nie zamarza podczas oczekiwania na odpowiedź.
Klucz API jest wysyłany wyłącznie w nagłówku do adresu dostawcy wybranego
w ustawieniach – nigdzie indziej i nigdy nie trafia do logów.
"""
import json

from qgis.core import QgsNetworkAccessManager
from qgis.PyQt.QtCore import QByteArray, QObject, QTimer, QUrl, pyqtSignal
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest

from .utils import redact_key
from .i18n import tr
from .agent_protocol import AGENT_PROVIDERS


def _extend_qgis_timeout(reply, ms):
    """Menedżer sieci QGIS przerywa zapytanie po własnym limicie bezczynności (Opcje › Sieć, domyślnie 60 s),
    liczonym do pierwszego bajtu odpowiedzi. Model może myśleć dłużej (np. Ollama na CPU), więc wydłużamy
    ten limit dla naszego zapytania do wartości wtyczki. Strumieniowanie dodatkowo odnawia go z każdym fragmentem."""
    try:
        timer = reply.findChild(QTimer, "timeoutTimer")
        if timer is not None and 0 < timer.interval() < ms:
            timer.setInterval(ms)
            timer.start()
    except Exception:  # noqa: BLE001 – brak timera w danej wersji QGIS nie jest błędem
        pass


class LLMClient(QObject):
    finished = pyqtSignal(str)   # surowy tekst odpowiedzi modelu
    failed = pyqtSignal(str)     # komunikat błędu (bez sekretów)
    progress = pyqtSignal(int)   # liczba odebranych znaków (odpowiedź strumieniowa)

    # Parametry, których dany model nie przyjmuje – uczymy się tego z odpowiedzi API (HTTP 400)
    # i przy kolejnych zapytaniach od razu wysyłamy poprawną wersję.
    _no_temperature = set()          # np. nowsze modele Claude, modele rozumujące OpenAI
    _completion_tokens = set()       # OpenAI: max_completion_tokens zamiast max_tokens
    _no_stream = set()               # serwery zgodne z API, które nie obsługują strumieniowania

    def __init__(self, parent=None):
        super().__init__(parent)
        self._reply = None
        self._aborted = False
        self._config = None
        self._request_args = None
        self._fixes = set()
        self._agent = None

    @property
    def busy(self):
        return self._reply is not None or (self._agent is not None and self._agent.busy)

    def abort(self):
        if self._agent is not None:
            self._agent.abort()
        if self._reply is not None:
            self._aborted = True
            self._reply.abort()

    # ------------------------------------------------------------------ wysyłka
    def request(self, system, messages, config):
        """messages: lista {"role": "user"|"assistant", "content": str}."""
        self.abort()
        if config["provider"] in AGENT_PROVIDERS:
            if self._agent is None:
                from .agent_client import AgentClient
                self._agent = AgentClient(self)
                self._agent.finished.connect(self.finished.emit)
                self._agent.failed.connect(self.failed.emit)
                self._agent.progress.connect(self.progress.emit)
            self._agent.request(system, messages, config)
            return
        self._aborted = False
        self._config = config
        self._request_args = (system, messages)
        self._fixes = set()  # każda automatyczna poprawka parametrów – najwyżej raz na zapytanie
        self._send()

    def _send(self):
        system, messages = self._request_args
        config = self._config
        provider = config["provider"]
        secret = config.get("api_key", "")
        if provider != "ollama" and not secret:
            self.failed.emit(tr("Brak klucza API – uzupełnij go w ustawieniach wtyczki (ikona ⚙)."))
            return
        try:
            url, headers, body = getattr(self, "_build_" + provider)(system, messages, config)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(tr("Nie udało się przygotować zapytania: %s") % redact_key(str(e), secret))
            return

        req = QNetworkRequest(QUrl(url))
        req.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        for k, v in headers.items():
            req.setRawHeader(k.encode("ascii"), v.encode("utf-8"))
        try:
            # limit bezczynności – przy strumieniowaniu każdy fragment odpowiedzi go odnawia
            req.setTransferTimeout(int(config.get("timeout_s", 120)) * 1000)
        except AttributeError:
            pass
        self._raw = bytearray()
        self._pending = b""
        self._text = []
        self._streamed = False
        self._truncated = False
        self._stream_error = None
        payload = QByteArray(json.dumps(body, ensure_ascii=False).encode("utf-8"))
        reply = QgsNetworkAccessManager.instance().post(req, payload)
        self._reply = reply
        _extend_qgis_timeout(reply, int(config.get("timeout_s", 120)) * 1000)
        # obiekt reply przekazujemy jawnie – odpowiedź z przerwanego zapytania nie pomyli się z nową
        reply.readyRead.connect(lambda r=reply: self._on_ready(r))
        reply.finished.connect(lambda r=reply: self._on_finished(r))

    # ------------------------------------------------------------------ formaty API
    @staticmethod
    def _model_key(cfg):
        return (cfg.get("provider"), cfg.get("endpoint"), cfg.get("model"))

    @staticmethod
    def _add_temperature(body, cfg):
        if cfg.get("temperature") is None or LLMClient._model_key(cfg) in LLMClient._no_temperature:
            return
        body["temperature"] = float(cfg["temperature"])

    @staticmethod
    def _stream(cfg):
        return LLMClient._model_key(cfg) not in LLMClient._no_stream

    @staticmethod
    def _build_anthropic(system, messages, cfg):
        body = {
            "model": cfg["model"],
            "max_tokens": int(cfg.get("max_tokens", 4096)),
            "system": system,
            "messages": messages,
        }
        if LLMClient._stream(cfg):
            body["stream"] = True
        LLMClient._add_temperature(body, cfg)
        headers = {"x-api-key": cfg["api_key"], "anthropic-version": "2023-06-01"}
        if cfg.get("workspace_id"):
            headers["anthropic-workspace-id"] = cfg["workspace_id"]
        return cfg["endpoint"], headers, body

    @staticmethod
    def _build_openai(system, messages, cfg):
        tokens_key = "max_completion_tokens" if LLMClient._model_key(cfg) in LLMClient._completion_tokens \
            else "max_tokens"
        body = {
            "model": cfg["model"],
            tokens_key: int(cfg.get("max_tokens", 4096)),
            "messages": [{"role": "system", "content": system}] + messages,
        }
        if LLMClient._stream(cfg):
            body["stream"] = True
        LLMClient._add_temperature(body, cfg)
        if "api.openai.com" in cfg["endpoint"]:
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": "Bearer " + cfg["api_key"]}
        return cfg["endpoint"], headers, body

    @staticmethod
    def _build_ollama(system, messages, cfg):
        body = {
            "model": cfg["model"],
            "stream": LLMClient._stream(cfg),
            "format": "json",
            "options": {"temperature": float(cfg.get("temperature") if cfg.get("temperature") is not None else 0.2),
                        "num_predict": int(cfg.get("max_tokens", 4096))},
            "messages": [{"role": "system", "content": system}] + messages,
        }
        return cfg["endpoint"], {}, body

    # ------------------------------------------------------------------ odpowiedź strumieniowa
    def _on_ready(self, reply):
        if reply is not self._reply:
            return
        chunk = bytes(reply.readAll())
        self._raw.extend(chunk)
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        ctype = str(reply.header(QNetworkRequest.KnownHeaders.ContentTypeHeader) or "").lower()
        if (status and int(status) >= 400) or not ("event-stream" in ctype or "ndjson" in ctype):
            return  # błąd lub zwykły JSON – całość interpretujemy po zakończeniu
        self._streamed = True
        data = self._pending + chunk
        lines = data.split(b"\n")
        self._pending = lines.pop()  # niedokończona linia czeka na kolejny fragment
        for line in lines:
            self._stream_line(line.decode("utf-8", "replace").strip())
        self.progress.emit(sum(len(t) for t in self._text))

    def _stream_line(self, line):
        """Linia SSE (Anthropic, OpenAI: „data: {…}”) lub NDJSON (Ollama)."""
        if not line or line.startswith((":", "event:")):
            return
        if line.startswith("data:"):
            line = line[5:].strip()
        if line == "[DONE]":
            return
        try:
            ev = json.loads(line)
        except ValueError:
            return
        if not isinstance(ev, dict):
            return
        if ev.get("error"):
            err = ev["error"]
            self._stream_error = err.get("message") if isinstance(err, dict) else str(err)
            return
        provider = self._config["provider"]
        if provider == "anthropic":
            if ev.get("type") == "content_block_delta" and (ev.get("delta") or {}).get("type") == "text_delta":
                self._text.append(ev["delta"].get("text", ""))
            elif ev.get("type") == "message_delta" and (ev.get("delta") or {}).get("stop_reason") == "max_tokens":
                self._truncated = True
        elif provider == "openai":
            for ch in ev.get("choices") or []:
                piece = (ch.get("delta") or {}).get("content")
                if piece:
                    self._text.append(piece)
                if ch.get("finish_reason") == "length":
                    self._truncated = True
        elif provider == "ollama":
            piece = (ev.get("message") or {}).get("content")
            if piece:
                self._text.append(piece)
            if ev.get("done_reason") == "length":
                self._truncated = True

    # ------------------------------------------------------------------ zakończenie
    def _on_finished(self, reply):
        if reply is not self._reply:
            reply.deleteLater()  # odpowiedź z przerwanego lub zastąpionego zapytania
            return
        self._reply = None
        secret = (self._config or {}).get("api_key", "")
        try:
            if self._aborted:
                return
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            self._raw.extend(bytes(reply.readAll()))
            if self._streamed and self._pending:
                self._stream_line(self._pending.decode("utf-8", "replace").strip())
                self._pending = b""
            raw = bytes(self._raw).decode("utf-8", "replace")
            if reply.error() != QNetworkReply.NetworkError.NoError:
                detail = self._api_error(raw) or reply.errorString()
                if self._retry_fixed(status, detail):
                    return
                self.failed.emit(redact_key(self._error_message(reply, status, detail), secret))
                return
            if self._stream_error:
                self.failed.emit(redact_key(tr("Błąd API%s: %s%s") % ("", self._stream_error, ""), secret))
                return
            try:
                text = self._stream_text() if self._streamed else self._parse(raw)
            except Exception as e:  # noqa: BLE001
                self.failed.emit(redact_key(tr("Nieoczekiwany format odpowiedzi API: %s") % e, secret))
                return
            self.finished.emit(text)
        finally:
            reply.deleteLater()

    def _stream_text(self):
        return "".join(self._text) + ("\n" if self._truncated else "")

    def _retry_fixed(self, status, detail):
        """Odrzucony parametr (HTTP 400) – zapamiętaj poprawkę dla modelu i ponów zapytanie raz."""
        if status != 400:
            return False
        low = (detail or "").lower()
        key = self._model_key(self._config)
        if "temperature" in low and key not in LLMClient._no_temperature and "temperature" not in self._fixes:
            LLMClient._no_temperature.add(key)
            fix = "temperature"
        elif "max_tokens" in low and "max_completion_tokens" in low and "tokens" not in self._fixes:
            # OpenAI: modele rozumujące wymagają max_completion_tokens, starsze serwery – max_tokens
            LLMClient._completion_tokens.symmetric_difference_update({key})
            fix = "tokens"
        elif "stream" in low and key not in LLMClient._no_stream and "stream" not in self._fixes:
            LLMClient._no_stream.add(key)
            fix = "stream"
        else:
            return False
        self._fixes.add(fix)
        self._send()
        return True

    def _error_message(self, reply, status, detail):
        low = (detail or "").lower()
        hint = ""
        if status in (401, 403):
            hint = tr(" – sprawdź klucz API w ustawieniach.")
        elif status == 400 and "workspace" in low:
            hint = (tr(" – ten klucz nie jest przypisany do workspace. Wpisz ID workspace (wrkspc_…) "
                       "w ustawieniach wtyczki albo utwórz w Claude Console klucz przypisany do workspace."))
        elif status == 429:
            hint = tr(" – przekroczony limit zapytań, spróbuj za chwilę.")
        elif reply.error() == QNetworkReply.NetworkError.OperationCanceledError:
            # przerwanie nie przez użytkownika – minął limit czasu (wtyczki lub sieci QGIS, większy z nich)
            hint = tr(" – serwer nie odpowiedział w wyznaczonym czasie (%d s). Spróbuj ponownie lub wybierz "
                      "szybszy model; limit czasu sieci można też zwiększyć w Ustawienia › Opcje › Sieć.") % max(
                int((self._config or {}).get("timeout_s", 120)), QgsNetworkAccessManager.timeout() // 1000)
        elif status is None:
            hint = tr(" – brak połączenia z serwerem (sieć/proxy/adres API).")
        return tr("Błąd API%s: %s%s") % (" (HTTP %s)" % status if status else "", detail, hint)

    def _parse(self, raw):
        data = json.loads(raw)
        provider = self._config["provider"]
        if provider == "anthropic":
            parts = [c.get("text", "") for c in data.get("content", []) if c.get("type") == "text"]
            if data.get("stop_reason") == "max_tokens":
                parts.append("\n")  # sygnał dla parsera – odpowiedź może być ucięta
            return "".join(parts)
        if provider == "openai":
            return data["choices"][0]["message"]["content"] or ""
        if provider == "ollama":
            return data["message"]["content"]
        raise ValueError(tr("nieznany dostawca"))

    @staticmethod
    def _api_error(raw):
        try:
            data = json.loads(raw)
        except ValueError:
            return raw[:300] if raw else ""
        err = data.get("error")
        if isinstance(err, dict):
            return err.get("message") or json.dumps(err)[:300]
        if isinstance(err, str):
            return err
        return ""
