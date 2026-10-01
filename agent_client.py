"""Nonblocking Qt process adapter for subscription-authenticated agent CLIs."""
import tempfile
import os

from qgis.PyQt.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, pyqtSignal

from .agent_protocol import (
    AgentError, AgentStream, agent_environment, auth_arguments, request_arguments,
    request_payload, require_subscription, resolve_command,
)
from .codex_models import CodexModelStream, catalog_arguments, encode_message


class AgentClient(QObject):
    finished = pyqtSignal(str)
    failed = pyqtSignal(str)
    progress = pyqtSignal(int)
    models_ready = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process = None
        self._workspace = None
        self._generation = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self._fail("Przekroczono czas oczekiwania na agenta."))

    @property
    def busy(self):
        return self._process is not None

    def request(self, system, messages, config):
        self._begin(config, "request", request_payload(system, messages))

    def request_models(self, config):
        if config.get("provider") != "codex":
            self._fail("Automatyczna lista modeli jest dostępna dla Codex.")
            return
        self._begin(config, "models")

    def _begin(self, config, operation, payload=b""):
        self.abort()
        self._config = dict(config)
        self._operation = operation
        self._payload = payload
        try:
            current = QProcessEnvironment.systemEnvironment()
            env = agent_environment({key: current.value(key) for key in current.keys()})
            self._program, self._prefix = resolve_command(config["provider"], config.get("executable", ""), env)
            self._environment = QProcessEnvironment()
            for key, value in env.items():
                self._environment.insert(key, value)
            self._workspace = tempfile.TemporaryDirectory(prefix="gis-assistant-agent-")
            self._launch("auth")
        except (AgentError, OSError) as exc:
            self._fail(str(exc))

    def _launch(self, phase):
        self._phase = phase
        self._stdout = bytearray()
        self._stderr = bytearray()
        self._stream = CodexModelStream() if phase == "models" else AgentStream(self._config["provider"])
        process = QProcess(self)
        self._process = process
        generation = self._generation
        process.setProcessEnvironment(self._environment)
        process.setWorkingDirectory(self._workspace.name)
        process.readyReadStandardOutput.connect(lambda: self._read(process, generation))
        process.readyReadStandardError.connect(lambda: self._read_error(process, generation))
        process.started.connect(lambda: self._started(process, generation))
        process.finished.connect(lambda code, status: self._done(process, generation, code))
        process.errorOccurred.connect(lambda error: self._process_error(process, generation, error))
        if phase == "auth":
            args = auth_arguments(self._config["provider"])
        elif phase == "models":
            args = catalog_arguments()
        else:
            args = request_arguments(self._config["provider"], self._config.get("model", ""))
        seconds = 20 if phase in {"auth", "models"} else max(30, int(self._config.get("timeout_s", 300)))
        self._timer.start(seconds * 1000)
        process.start(self._program, self._prefix + args)

    def _active(self, process, generation):
        return process is self._process and generation == self._generation

    def _started(self, process, generation):
        if not self._active(process, generation):
            return
        if self._phase == "models":
            process.write(encode_message(self._stream.initialize()))
            return  # Keep stdin open for the handshake and subsequent catalog pages.
        if self._phase == "request":
            process.write(self._payload)
        process.closeWriteChannel()

    def _read(self, process, generation):
        if not self._active(process, generation):
            return
        data = bytes(process.readAllStandardOutput())
        if self._phase == "auth":
            self._stdout.extend(data)
            if len(self._stdout) > 65536:
                self._fail("Niepoprawna odpowiedź polecenia sprawdzającego konto.")
        elif self._phase == "models":
            try:
                for message in self._stream.feed(data):
                    process.write(encode_message(message))
                if self._stream.complete:
                    models = self._stream.models
                    self.abort()
                    self.models_ready.emit(models)
            except AgentError as exc:
                self._fail(str(exc))
        else:
            try:
                self._stream.feed(data)
                self.progress.emit(self._stream.chars)
            except AgentError as exc:
                self._fail(str(exc))

    def _read_error(self, process, generation):
        if self._active(process, generation):
            # Account status is read locally, never copied to the QGIS log.
            self._stderr.extend(bytes(process.readAllStandardError()))
            del self._stderr[:-16384]

    def _done(self, process, generation, code):
        if not self._active(process, generation):
            return
        self._read(process, generation)
        self._read_error(process, generation)
        if not self._active(process, generation):
            return
        self._timer.stop()
        self._process = None
        process.deleteLater()
        try:
            if self._phase == "auth":
                require_subscription(self._config["provider"], self._stdout.decode("utf-8", "replace"),
                                     self._stderr.decode("utf-8", "replace"), code)
                self._launch(self._operation)
                return
            if self._phase == "models":
                raise AgentError("Codex zakończył pracę przed pobraniem listy modeli.")
            result = self._stream.finish(code)
        except AgentError as exc:
            self._fail(str(exc))
            return
        self._cleanup_workspace()
        self.finished.emit(result)

    def _process_error(self, process, generation, error):
        if self._active(process, generation) and error == QProcess.ProcessError.FailedToStart:
            self._fail("Nie można uruchomić agenta. Sprawdź ścieżkę programu i jego instalację.")

    def _cleanup_workspace(self):
        if self._workspace is not None:
            self._cleanup(self._workspace)
            self._workspace = None

    @staticmethod
    def _cleanup(workspace):
        try:
            workspace.cleanup()
        except OSError:
            pass  # A retiring CLI can briefly retain a Windows file handle.

    def _fail(self, message):
        self.abort()
        self.failed.emit(message)

    def abort(self):
        self._generation += 1
        self._timer.stop()
        process, self._process = self._process, None
        workspace, self._workspace = self._workspace, None
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            # No waitForFinished on the GUI thread. Clean up after the OS closes handles.
            def retired(*_):
                if workspace is not None:
                    self._cleanup(workspace)
                process.deleteLater()
            process.finished.connect(retired)
            process.errorOccurred.connect(
                lambda error: retired() if error == QProcess.ProcessError.FailedToStart else None)
            def force_kill(*_):
                try:
                    if process.state() != QProcess.ProcessState.NotRunning:
                        process.kill()
                except RuntimeError:
                    pass  # The retired process has already been deleted.

            def terminate_tree():
                pid = int(process.processId())
                if os.name == "nt" and pid:
                    # Kill only this owned CLI tree; never search processes by executable name.
                    killer = QProcess(self)
                    def killed(code, *_):
                        if code:
                            force_kill()
                        killer.deleteLater()
                    killer.finished.connect(killed)
                    killer.errorOccurred.connect(force_kill)
                    killer.start(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe"),
                                 ["/PID", str(pid), "/T", "/F"])
                    QTimer.singleShot(2500, force_kill)
                else:
                    force_kill()

            if process.state() == QProcess.ProcessState.Starting:
                # kill() during Starting can be a no-op: cancel as soon as the OS supplies a PID.
                process.started.connect(terminate_tree)
            else:
                terminate_tree()
        else:
            if process is not None:
                process.deleteLater()
            if workspace is not None:
                self._cleanup(workspace)
