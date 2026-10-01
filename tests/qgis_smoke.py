"""Run with a QGIS Python installation, e.g. python-qgis-ltr.bat tests/qgis_smoke.py <QGIS root>.

Uses an isolated profile, offscreen UI, in-memory layers and fake agent processes. No LLM requests.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

if len(sys.argv) > 1 and os.name == "nt":
    qroot = Path(sys.argv[1])
    dirs = [qroot / "bin", qroot / "apps/qt5/bin", qroot / "apps/Qt6/bin",
            qroot / "apps/qgis-ltr/bin", qroot / "apps/qgis/bin"]
    dirs.extend((qroot / "apps/grass").glob("*/lib"))
    os.environ["PATH"] = os.pathsep.join(str(p) for p in dirs if p.is_dir()) + os.pathsep + os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    dll_handles = [os.add_dll_directory(str(p)) for p in dirs if p.is_dir()]
os.environ["QT_QPA_PLATFORM"] = "offscreen"

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gis_assistant_ai", root / "__init__.py",
                                            submodule_search_locations=[str(root)])
pkg = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pkg
spec.loader.exec_module(pkg)

from qgis.core import QgsApplication, QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY
from qgis.PyQt.QtCore import QProcess, QSettings
from qgis.PyQt.QtWidgets import QMainWindow
from qgis.gui import QgsMapCanvas, QgsMessageBar
from qgis.analysis import QgsNativeAlgorithms

profile = tempfile.TemporaryDirectory(prefix="qgis-integration-test-")
os.environ["QGIS_AUTH_DB_DIR_PATH"] = profile.name
QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, profile.name)
app = QgsApplication([], True, profile.name)
app.initQgis()
app.processingRegistry().addProvider(QgsNativeAlgorithms())
sys.path.insert(0, str(Path(QgsApplication.prefixPath()) / "python/plugins"))

from gis_assistant_ai import agent_client, qgis_bridge
from gis_assistant_ai.mcp_server import BridgeClient
from gis_assistant_ai.ui import dock as dock_module
from gis_assistant_ai.ui.settings_dialog import SettingsDialog
from gis_assistant_ai.constants import PROVIDERS
from gis_assistant_ai.context_builder import context_json

if "--snapshot" in sys.argv and os.name == "nt":
    from qgis.PyQt.QtGui import QFont, QFontDatabase
    font_file = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Fonts/segoeui.ttf"
    font_id = QFontDatabase.addApplicationFont(str(font_file))
    families = QFontDatabase.applicationFontFamilies(font_id)
    if families:
        app.setFont(QFont(families[0], 9))


def spin_until(predicate, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError("Qt condition timed out")


class MemorySettings:
    def __init__(self):
        self.values = {"provider": "codex"}

    def value(self, key, default=None, kind=None):
        return self.values.get(key, default)

    def set_value(self, key, value):
        self.values[key] = value

    @property
    def provider(self):
        return self.values["provider"]

    def model(self, p):
        return self.values.get("model/" + p, PROVIDERS[p]["models"][0])

    def endpoint(self, p):
        return PROVIDERS[p]["url"]

    def workspace(self, p):
        return ""


class Interface:
    def __init__(self):
        self.window = QMainWindow()
        self.canvas = QgsMapCanvas(self.window)
        self.bar = QgsMessageBar(self.window)
        self.active = None

    def mainWindow(self): return self.window
    def mapCanvas(self): return self.canvas
    def messageBar(self): return self.bar
    def activeLayer(self): return self.active
    def setActiveLayer(self, layer): self.active = layer


def run_model_picker():
    original_resolver = agent_client.resolve_command
    fake = [str(root / "tests/fake_agent.py")]
    agent_client.resolve_command = lambda *args: (sys.executable, fake)
    settings = MemorySettings()
    dialog = SettingsDialog(settings)
    try:
        assert dialog.model.itemText(0) == "Domyślny model agenta", "Default choice must not be an empty row"
        dialog.model.setEditText("my-custom-model")
        spin_until(lambda: not dialog._models_client.busy)
        choices = [dialog.model.itemData(i) for i in range(dialog.model.count())]
        assert "test-alpha" in choices and "test-beta" in choices, choices
        assert "test-hidden" not in choices
        assert dialog._current_config()["model"] == "my-custom-model", "Refresh overwrote manual input"
        dialog.model.setCurrentIndex(0)
        assert dialog._current_config()["model"] == "", "Default label must not be sent as a model ID"
        dialog.model.setCurrentIndex(dialog.model.findData("test-beta"))
        if "--snapshot" in sys.argv:
            dialog.show()
            app.processEvents()
            dialog.grab().save(str(root.parent / "model-picker.png"))
        dialog._save()
        assert settings.model("codex") == "test-beta"

        restored = SettingsDialog(settings)
        spin_until(lambda: not restored._models_client.busy)
        assert restored._current_config()["model"] == "test-beta"
        restored._refresh_models()
        restored.provider.setCurrentIndex(restored.provider.findData("claude_code"))
        for _ in range(20):
            app.processEvents()
            time.sleep(.005)
        assert restored.model.itemText(0) == "Domyślny model agenta"
        assert restored.model.findData("sonnet") >= 0
        assert restored.model.findData("test-alpha") == -1, "Late Codex response contaminated Claude choices"
        restored.reject()

        agent_client.resolve_command = lambda *args: (sys.executable, fake + ["--no-login"])
        failed = SettingsDialog(settings)
        spin_until(lambda: not failed._models_client.busy)
        assert "logowania" in failed.model_status.text()
        assert failed._current_config()["model"] == "test-beta"
        failed.reject()

        agent_client.resolve_command = lambda *args: (sys.executable, fake + ["--slow-catalog"])
        timed = SettingsDialog(settings)
        spin_until(lambda: timed._models_client._phase == "models")
        timed._models_client._timer.start(30)
        spin_until(lambda: not timed._models_client.busy)
        assert "czas" in timed.model_status.text()
        assert timed._current_config()["model"] == "test-beta"
        timed._refresh_models()
        timed.reject()
        assert not timed._models_client.busy
        for item in (dialog, restored, failed, timed):
            spin_until(lambda d=item: all(p.state() == QProcess.ProcessState.NotRunning
                                         for p in d.findChildren(QProcess)))
        print("PASS model picker: async pages, hidden models, default, custom input, save/reopen, "
              "provider switch, auth failure, timeout, close cancellation")
    finally:
        dialog.reject()
        agent_client.resolve_command = original_resolver


def run_live_model_picker():
    """Opt-in account metadata request: no model inference or project data is sent."""
    dialog = SettingsDialog(MemorySettings())
    try:
        spin_until(lambda: not dialog._models_client.busy, 50)
        ids = [dialog.model.itemData(i) for i in range(1, dialog.model.count())]
        assert ids, dialog.model_status.text()
        assert dialog._current_config()["model"] == ""
        dialog.model.setCurrentIndex(1)
        assert dialog._current_config()["model"] == ids[0]
        if "--snapshot" in sys.argv:
            dialog.show()
            app.processEvents()
            dialog.grab().save(str(root.parent / "model-picker.png"))
        print("PASS live Codex model/list:", json.dumps(ids))
    finally:
        dialog.reject()
        spin_until(lambda: all(p.state() == QProcess.ProcessState.NotRunning for p in dialog.findChildren(QProcess)))


def run():
    original_resolver = agent_client.resolve_command
    agent_client.resolve_command = lambda *args: (sys.executable, [str(root / "tests/fake_agent.py")])
    client = agent_client.AgentClient()
    answers, errors = [], []
    client.finished.connect(answers.append)
    client.failed.connect(errors.append)
    for provider in ("codex", "claude_code"):
        before = len(answers)
        client.request("test-marker", [], {"provider": provider})
        spin_until(lambda: not client.busy)
        assert len(answers) == before + 1 and not errors, errors
        assert json.loads(answers[-1])["text"] == "Zażółć"
    client.request("test-marker", [], {"provider": "codex", "model": "slow"})
    spin_until(lambda: client._phase == "request")
    client.abort()
    before = len(answers)
    client.request("test-marker", [], {"provider": "claude_code"})
    spin_until(lambda: not client.busy)
    assert len(answers) == before + 1 and not errors
    client.request("test-marker", [], {"provider": "codex", "model": "slow"})
    spin_until(lambda: client._phase == "request")
    client._timer.start(30)
    spin_until(lambda: bool(errors))
    assert "czas" in errors.pop()
    agent_client.resolve_command = lambda *args: (str(root / "missing.exe"), [])
    client.request("test-marker", [], {"provider": "codex"})
    spin_until(lambda: bool(errors))
    assert not client.busy
    agent_client.resolve_command = original_resolver
    spin_until(lambda: all(p.state() == QProcess.ProcessState.NotRunning for p in client.findChildren(QProcess)))
    print("PASS Qt agent processes: both protocols, cancellation/restart, timeout, missing CLI")

    dock_module.Settings = MemorySettings
    qgis_bridge.connection_path = lambda: Path(profile.name) / "bridge/connection.json"
    iface = Interface()
    dock = dock_module.GISAssistantAIDock(iface)
    dialog = SettingsDialog(dock.settings, dock)
    for provider in ("codex", "claude_code", "ollama", "openai", "anthropic"):
        dialog.provider.setCurrentIndex(dialog.provider.findData(provider))
        cfg = dialog._current_config()
        assert cfg["provider"] == provider
        if provider in {"codex", "claude_code"}:
            assert not cfg["api_key"] and cfg["timeout_s"] == 300
    dialog.reject()
    print("PASS settings UI: two agent providers and three existing providers")

    dock.bridge.start()
    connection = BridgeClient(dock.bridge.file)
    pool = ThreadPoolExecutor(1)

    def tool(name, args=None):
        future = pool.submit(connection.call, name, args or {})
        spin_until(future.done)
        return future.result()

    points = QgsVectorLayer("Point?crs=EPSG:2180", "Test points", "memory")
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(500000, 500000)))
    points.dataProvider().addFeatures([feature])
    points.updateExtents()
    QgsProject.instance().addMapLayer(points)
    iface.setActiveLayer(points)
    context = tool("qgis_get_context")
    assert any(layer["id"] == points.id() for layer in context["layers"])
    assert tool("qgis_list_algorithms", {"query": "buffer"})["algorithms"]
    description = tool("qgis_describe_algorithm", {"algorithm": "native:buffer"})
    assert any(param["name"] == "DISTANCE" for param in description["parameters"])
    plan = {"type": "plan", "title": "100 m buffer", "steps": [
        {"id": "buffer", "title": "Buffer", "action": "processing", "algorithm": "native:buffer",
         "params": {"INPUT": points.id(), "DISTANCE": 100, "SEGMENTS": 8, "END_CAP_STYLE": 0,
                    "JOIN_STYLE": 0, "MITER_LIMIT": 2, "DISSOLVE": False, "OUTPUT": "TEMPORARY_OUTPUT"}},
    ]}
    status = tool("qgis_submit_plan", {"plan": plan})
    plan_id = status["plan_id"]
    assert status["status"] == "awaiting_approval"
    assert len(QgsProject.instance().mapLayers()) == 1
    try:
        tool("qgis_submit_plan", {"plan": plan})
        raise AssertionError("Concurrent plan accepted")
    except ValueError:
        pass
    dock._execute()
    spin_until(lambda: not dock.executor.running)
    assert tool("qgis_get_status", {"plan_id": plan_id})["status"] == "completed"
    assert len(QgsProject.instance().mapLayers()) == 2
    result = [layer for layer in QgsProject.instance().mapLayers().values() if layer.id() != points.id()][0]
    assert result.featureCount() == 1
    assert abs(next(result.getFeatures()).geometry().boundingBox().width() - 200) < .01
    print("PASS MCP + QGIS: context, algorithm schema, approval, actual 100 m native buffer, result status")

    dock._new_conversation()
    repair_plan = {"type": "plan", "steps": [
        {"id": "done", "action": "note", "title": "Completed"},
        {"id": "ask", "action": "user_action", "title": "Pick a layer", "replan_after": True},
        {"id": "old", "action": "zoom", "title": "Old remainder", "layer": points.id()},
    ]}
    repair_id = tool("qgis_submit_plan", {"plan": repair_plan})["plan_id"]
    dock._execute()
    spin_until(lambda: dock.executor.current().get("_status") == "waiting")
    dock.executor.user_done(points)
    assert tool("qgis_get_status", {"plan_id": repair_id})["status"] == "awaiting_revision"
    before = dict(dock.executor.results)
    replacement = [{"id": "new", "action": "zoom", "title": "New remainder", "layer": "{{ask.OUTPUT}}"}]
    revised = tool("qgis_revise_plan", {"plan_id": repair_id, "steps": replacement})
    assert revised["status"] == "awaiting_revision_approval"
    assert dock.executor.steps[0]["_status"] == "done" and dock.executor.results == before
    dock._execute()
    spin_until(lambda: not dock.executor.running)
    assert tool("qgis_get_status", {"plan_id": repair_id})["status"] == "completed"
    print("PASS MCP revision: completed results preserved; second human approval required")

    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "qgis_get_context"}},
    ]
    command = [sys.executable, str(root / "mcp_server.py"), "--connection-file", str(dock.bridge.file)]
    future = pool.submit(subprocess.run, command,
                         input="\n".join(map(json.dumps, requests)).encode(), capture_output=True, timeout=15)
    spin_until(future.done, 20)
    response = future.result()
    assert response.returncode == 0, response.stderr
    assert not json.loads(response.stdout.splitlines()[1])["result"]["isError"]
    print("PASS MCP end-to-end: agent stdio -> helper -> authenticated Qt bridge -> live QGIS")

    file = dock.bridge.file
    original = file.read_text()
    altered = json.loads(original)
    altered["token"] = "0" * 64
    file.write_text(json.dumps(altered))
    try:
        tool("qgis_get_context")
        raise AssertionError("Bad token accepted")
    except ValueError:
        pass
    file.write_text(original)
    other = qgis_bridge.QGISBridge(dock)
    try:
        other.start()
        raise AssertionError("Second bridge overwrote discovery")
    except OSError:
        pass
    dock.cleanup()
    assert not file.exists() and not dock.bridge.server.isListening()
    pool.shutdown()
    print("PASS bridge: invalid token, profile lock, unload cleanup")

    try:
        context_json({"value": "x" * 50000})
        raise AssertionError("Oversized context silently cut")
    except ValueError:
        pass
    print("ALL QGIS SMOKE CHECKS PASSED")


try:
    if "--live-model-picker" in sys.argv:
        run_live_model_picker()
    elif "--model-picker" in sys.argv:
        run_model_picker()
    else:
        run()
finally:
    QgsProject.instance().clear()
    app.processEvents()
    app.exitQgis()
    profile.cleanup()
