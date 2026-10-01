"""Authenticated loopback bridge; all project access runs on the Qt GUI thread."""
import json
import os
from pathlib import Path
import secrets

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import QObject, QTimer, QLockFile
from qgis.PyQt.QtNetwork import QHostAddress, QTcpServer

from .context_builder import build_context, _optional
from .mcp_server import MAX_MESSAGE, validate_call
from .utils import sanitize_source


def connection_path():
    return Path(QgsApplication.qgisSettingsDirPath()) / "gis_assistant_ai" / "mcp-connection.json"


class QGISBridge(QObject):
    def __init__(self, dock):
        super().__init__(dock)
        self.dock = dock
        self.server = QTcpServer(self)
        self.server.setMaxPendingConnections(8)
        self.server.newConnection.connect(self._accept)
        self.clients = {}
        self.token = ""
        self.file = connection_path()
        self.lock = QLockFile(str(self.file.with_suffix(".lock")))
        self.lock.setStaleLockTime(0)

    def start(self):
        if self.server.isListening():
            return
        self.file.parent.mkdir(parents=True, exist_ok=True)
        if not self.lock.tryLock(0):
            raise OSError("Most MCP działa już w innym oknie QGIS tego profilu.")
        if not self.server.listen(QHostAddress(QHostAddress.SpecialAddress.LocalHost), 0):
            self.lock.unlock()
            raise OSError("Nie można uruchomić lokalnego mostu MCP.")
        self.token = secrets.token_hex(32)
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            temp = self.file.with_suffix(".%s.tmp" % os.getpid())
            fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"host": "127.0.0.1", "port": self.server.serverPort(), "token": self.token,
                           "pid": os.getpid()}, handle)
            os.replace(str(temp), str(self.file))
        except OSError:
            self.stop()
            raise

    def stop(self):
        self.server.close()
        for socket in list(self.clients):
            socket.abort()
        try:
            if self.file.is_file() and json.loads(self.file.read_text(encoding="utf-8")).get("token") == self.token:
                self.file.unlink()
        except (OSError, ValueError):
            pass
        self.token = ""
        self.lock.unlock()

    def _accept(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if len(self.clients) >= 8:
                socket.abort()
                socket.deleteLater()
                continue
            self.clients[socket] = bytearray()
            timer = QTimer(socket)
            timer.setSingleShot(True)
            timer.timeout.connect(socket.abort)
            timer.start(15000)
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(lambda s=socket: self._closed(s))

    def _closed(self, socket):
        self.clients.pop(socket, None)
        socket.deleteLater()

    def _reply(self, socket, status, payload):
        self.clients.pop(socket, None)
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(data) > MAX_MESSAGE:
            status, data = 413, b'{"error":"Response too large"}'
        header = ("HTTP/1.1 %d Result\r\nContent-Type: application/json\r\n"
                  "Content-Length: %d\r\nConnection: close\r\n\r\n") % (status, len(data))
        socket.write(header.encode("ascii") + data)
        socket.disconnectFromHost()

    def _read(self, socket):
        if socket not in self.clients:
            return
        buf = self.clients[socket]
        buf.extend(bytes(socket.readAll()))
        if len(buf) > MAX_MESSAGE + 8192:
            self._reply(socket, 413, {"error": "Request too large"})
            return
        split = buf.find(b"\r\n\r\n")
        if split > 8192:
            self._reply(socket, 413, {"error": "Headers too large"})
            return
        if split < 0:
            if len(buf) > 8192:
                self._reply(socket, 413, {"error": "Headers too large"})
            return
        try:
            lines = bytes(buf[:split]).decode("ascii").split("\r\n")
            if lines[0] != "POST /tool HTTP/1.1":
                raise ValueError("Invalid method")
            headers = {}
            for line in lines[1:]:
                key, value = line.split(":", 1)
                key = key.strip().lower()
                if key in headers:
                    raise ValueError("Duplicate header")
                headers[key] = value.strip()
            if "origin" in headers or "transfer-encoding" in headers:
                raise ValueError("Browser requests and chunked encoding are not supported")
            if not secrets.compare_digest(headers.get("authorization", ""), "Bearer " + self.token):
                self._reply(socket, 401, {"error": "Unauthorized"})
                return
            length = int(headers["content-length"])
            if not 0 < length <= MAX_MESSAGE:
                raise ValueError("Invalid size")
            body = bytes(buf[split + 4:])
            if len(body) < length:
                return
            if len(body) != length:
                raise ValueError("Invalid body")
            request = json.loads(body)
            if not isinstance(request, dict):
                raise ValueError("Invalid request")
            validate_call(request.get("name"), request.get("arguments", {}))
            try:
                result = self._dispatch(request["name"], request.get("arguments", {}))
            except ValueError as exc:
                self._reply(socket, 200, {"error": sanitize_source(str(exc)[:1500], "ogr")})
                return
            self._reply(socket, 200, {"result": result})
        except (ValueError, KeyError, TypeError, UnicodeError):
            self._reply(socket, 400, {"error": "Invalid tool request. Check the QGIS panel."})
        except Exception:
            self._reply(socket, 500, {"error": "QGIS could not complete this tool."})

    def _dispatch(self, name, args):
        dock = self.dock
        reg = QgsApplication.processingRegistry()
        if name == "qgis_get_context":
            return build_context(dock.iface, dock.settings)
        if name == "qgis_list_algorithms":
            query = args.get("query", "").casefold()
            matches = [{"id": alg.id(), "name": alg.displayName()} for alg in reg.algorithms()
                       if query in (alg.id() + " " + alg.displayName()).casefold()]
            return {"algorithms": matches[:args.get("limit", 30)], "total": len(matches)}
        if name == "qgis_describe_algorithm":
            alg = reg.algorithmById(args["algorithm"])
            if alg is None:
                raise ValueError("Algorithm not installed")
            parameters = []
            for param in alg.parameterDefinitions():
                default = param.defaultValue()
                if not isinstance(default, (str, int, float, bool, type(None))):
                    default = str(default)[:200]
                if isinstance(default, str):
                    default = sanitize_source(default, "ogr")
                item = {"name": param.name(), "description": param.description(), "type": param.type(),
                        "optional": _optional(param), "destination": param.isDestination(), "default": default}
                if hasattr(param, "options"):
                    item["options"] = list(param.options())
                parameters.append(item)
            return {"id": alg.id(), "name": alg.displayName(), "parameters": parameters,
                    "outputs": [{"name": out.name(), "type": out.type()} for out in alg.outputDefinitions()]}
        if name == "qgis_submit_plan":
            return dock.external.accept(args["plan"])
        if name == "qgis_get_status":
            return dock.external.status(args["plan_id"])
        if name == "qgis_revise_plan":
            return dock.external.revise(args["plan_id"], args["steps"])
        raise ValueError("Unknown tool")
