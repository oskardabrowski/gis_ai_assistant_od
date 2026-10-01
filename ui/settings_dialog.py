# -*- coding: utf-8 -*-

from pathlib import Path
import json
import shutil

from qgis.PyQt.QtCore import QUrl, Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QFileDialog, QPlainTextEdit, QSizePolicy,
)

from ..constants import ICON_PATH, OLLAMA_TEMPERATURE, PLUGIN_NAME, PROVIDERS
from ..i18n import tr
from ..llm_client import LLMClient
from ..prompts import TEST_SYSTEM, TEST_USER
from ..settings import SecretStore
from ..agent_protocol import AGENT_PROVIDERS
from ..agent_client import AgentClient
from ..qgis_bridge import connection_path
from . import styles


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle(PLUGIN_NAME + tr(" – ustawienia"))
        self.setWindowIcon(QIcon(ICON_PATH))
        self.setMinimumWidth(520)
        self.pal = styles.palette_for(self)
        self._client = LLMClient(self)
        self._client.finished.connect(self._test_ok)
        self._client.failed.connect(self._test_fail)
        self.finished.connect(lambda *_: self._client.abort())
        self._models_client = AgentClient(self)
        self._models_client.models_ready.connect(self._models_ready)
        self._models_client.failed.connect(self._models_failed)
        self.finished.connect(lambda *_: self._models_client.abort())
        self._build()
        self._load()

    # ------------------------------------------------------------------ UI
    def _build(self):
        lay = QVBoxLayout(self)

        box = QGroupBox(tr("Model językowy"))
        form = QFormLayout(box)
        self.provider = QComboBox()
        for key, meta in PROVIDERS.items():
            self.provider.addItem(tr(meta["label"]), key)
        self.provider.currentIndexChanged.connect(self._provider_changed)
        form.addRow(tr("Dostawca:"), self.provider)

        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setMinimumContentsLength(22)
        self.model.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        model_row = QHBoxLayout()
        model_row.addWidget(self.model, 1)
        self.refresh_models = QPushButton("Odśwież")
        self.refresh_models.setObjectName("secondary")
        self.refresh_models.setToolTip("Pobierz modele dostępne przez zalogowany Codex CLI")
        self.refresh_models.clicked.connect(self._refresh_models)
        model_row.addWidget(self.refresh_models)
        self.models_link = QLabel()
        self.models_link.setOpenExternalLinks(True)
        model_row.addWidget(self.models_link)
        form.addRow(tr("Model:"), model_row)
        self.model_status = QLabel()
        self.model_status.setWordWrap(True)
        self.model_status.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.model_status.setStyleSheet("color:%s;font-size:10px;" % self.pal["muted"])
        form.addRow("", self.model_status)

        ep_row = QHBoxLayout()
        self.endpoint = QLineEdit()
        reset = QPushButton(tr("Domyślny"))
        reset.setObjectName("secondary")
        self.endpoint_reset = reset
        reset.clicked.connect(lambda: self.endpoint.setText(PROVIDERS[self._prov()]["url"]))
        ep_row.addWidget(self.endpoint, 1)
        ep_row.addWidget(reset)
        self.endpoint_label = QLabel(tr("Adres API:"))
        form.addRow(self.endpoint_label, ep_row)
        self.http_warn = QLabel()
        self.http_warn.setWordWrap(True)
        self.http_warn.hide()
        form.addRow("", self.http_warn)
        self.endpoint.textChanged.connect(self._check_endpoint)

        key_row = QHBoxLayout()
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.del_key = QPushButton(tr("Usuń klucz"))
        self.del_key.setObjectName("danger")
        self.del_key.clicked.connect(self._delete_key)
        key_row.addWidget(self.api_key, 1)
        key_row.addWidget(self.del_key)
        self.key_label = QLabel(tr("Klucz API:"))
        form.addRow(self.key_label, key_row)
        self.persist = QCheckBox(tr("Zapamiętaj klucz (zaszyfrowany w QGIS)"))
        self.persist.setToolTip(tr("Klucz trafia do zaszyfrowanej bazy menedżera uwierzytelniania QGIS. "
                                "Bez zaznaczenia jest pamiętany tylko do zamknięcia QGIS."))
        self.persist.setChecked(True)
        form.addRow("", self.persist)
        self.workspace = QLineEdit()
        self.workspace.setPlaceholderText(tr("opcjonalnie, np. wrkspc_01AbCd… – wymagane dla kluczy bez workspace"))
        self.workspace_label = QLabel(tr("ID workspace:"))
        form.addRow(self.workspace_label, self.workspace)
        self.key_info = QLabel()
        self.key_info.setWordWrap(True)
        form.addRow("", self.key_info)
        self.executable_label = QLabel("Program CLI:")
        self.executable = QLineEdit()
        self.executable.setPlaceholderText("Automatyczne wykrywanie; opcjonalnie pełna ścieżka")
        self.executable.textChanged.connect(self._executable_changed)
        self.browse_cli = QPushButton("Wybierz…")
        self.browse_cli.clicked.connect(self._browse_cli)
        cli_row = QHBoxLayout()
        cli_row.addWidget(self.executable, 1)
        cli_row.addWidget(self.browse_cli)
        form.addRow(self.executable_label, cli_row)
        self.agent_info = QLabel()
        self.agent_info.setWordWrap(True)
        self.agent_info.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        form.addRow("", self.agent_info)
        lay.addWidget(box)

        external = QGroupBox("Codex / Claude Code poza QGIS")
        external_layout = QVBoxLayout(external)
        self.mcp_enabled = QCheckBox("Włącz lokalny most MCP dla tego profilu QGIS")
        external_layout.addWidget(self.mcp_enabled)
        mcp_info = QLabel("Agent odczyta opis projektu i zaproponuje plan. Wykonanie oraz każda korekta "
                          "wymagają kliknięcia w panelu QGIS. Zapisz ustawienia, aby uruchomić most.")
        mcp_info.setWordWrap(True)
        external_layout.addWidget(mcp_info)
        config_button = QPushButton("Pokaż konfigurację MCP…")
        config_button.clicked.connect(self._show_mcp_config)
        external_layout.addWidget(config_button)
        lay.addWidget(external)

        priv = QGroupBox(tr("Prywatność i bezpieczeństwo"))
        v = QVBoxLayout(priv)
        self.send_samples = QCheckBox(tr("Wysyłaj przykładowe wartości atrybutów"))
        self.send_samples.setToolTip(
            tr("Maks. 5 wartości tekstowych na pole – model trafniej buduje wyrażenia filtrujące."))
        self.allow_python = QCheckBox(tr("Pozwól AI proponować kod PyQGIS (zawsze po Twoim zatwierdzeniu)"))
        info = QLabel(
            tr("Do modelu wysyłane są: treść polecenia oraz opis projektu (nazwy i pola warstw, układy "
            "współrzędnych, zasięg mapy). Hasła, loginy, tokeny i pełne ścieżki dysków są usuwane. "
            "Wybierając Ollama, wszystko pozostaje na Twoim komputerze."))
        info.setWordWrap(True)
        info.setStyleSheet("color:%s;font-size:10px;" % self.pal["muted"])
        v.addWidget(self.send_samples)
        v.addWidget(self.allow_python)
        v.addWidget(info)
        lay.addWidget(priv)

        test_row = QHBoxLayout()
        self.test_btn = QPushButton(tr("Testuj połączenie"))
        self.test_btn.setObjectName("secondary")
        self.test_btn.clicked.connect(self._test)
        self.test_label = QLabel()
        self.test_label.setWordWrap(True)
        test_row.addWidget(self.test_btn)
        test_row.addWidget(self.test_label, 1)
        lay.addLayout(test_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.setStyleSheet(styles.stylesheet(self.pal))

    def _prov(self):
        return self.provider.currentData()

    def _load(self):
        idx = self.provider.findData(self.settings.provider)
        self.provider.setCurrentIndex(max(idx, 0))
        self._provider_changed()
        self.send_samples.setChecked(self.settings.value("send_samples", False, bool))
        self.allow_python.setChecked(self.settings.value("allow_python", False, bool))
        self.mcp_enabled.setChecked(self.settings.value("mcp_enabled", False, bool))

    def _provider_changed(self, *_):
        p = self._prov()
        meta = PROVIDERS[p]
        self._client.abort()
        self._models_client.abort()
        self.refresh_models.setVisible(p == "codex")
        self.refresh_models.setEnabled(True)
        self.model_status.setVisible(p == "codex")
        self.model_status.clear()
        self.test_btn.setEnabled(True)
        self.test_label.clear()
        agent = p in AGENT_PROVIDERS
        for widget in (self.endpoint, self.endpoint_label, self.endpoint_reset):
            widget.setVisible(not agent)
        for widget in (self.executable, self.executable_label, self.browse_cli, self.agent_info):
            widget.setVisible(agent)
        self.executable.setText(self.settings.value("executable/" + p, "", str))
        login = "claude auth login" if p == "claude_code" else "codex login"
        self.agent_info.setText("To pole jest opcjonalne. Wskaż CLI ręcznie, jeśli automat wybierze starszą wersję.\n\n"
                               "Zainstaluj oficjalny CLI i zaloguj się: " + login + ".\n"
                               "Zapytania i test zużywają limity konta; brak przełączania na API.")
        self.model.lineEdit().setPlaceholderText("Domyślny model agenta" if agent else "")
        self._set_models(meta["models"], self.settings.model(p))
        self.endpoint.setText(self.settings.endpoint(p))
        self.models_link.setText("<a href='%s' style='color:%s;'>%s</a>" % (
            meta["models_url"], self.pal["primary"], "Dokumentacja"))
        self._check_endpoint()
        needs = meta["needs_key"]
        for w in (self.api_key, self.del_key, self.persist, self.key_label, self.key_info):
            w.setVisible(needs)
        is_anthropic = p == "anthropic"
        self.workspace.setVisible(is_anthropic)
        self.workspace_label.setVisible(is_anthropic)
        self.workspace.setText(self.settings.workspace(p) if is_anthropic else "")
        self.api_key.clear()
        has = SecretStore.has(p)
        self.api_key.setPlaceholderText(
            tr("•••••••• (klucz zapisany – wpisz nowy, aby zmienić)") if has else tr("wklej klucz API"))
        self.del_key.setEnabled(has)
        self.key_info.setText(
            tr("Klucz jest wysyłany wyłącznie w nagłówku zapytania do powyższego adresu API. "
            "Nie jest zapisywany jawnym tekstem w profilu QGIS."))
        self.key_info.setStyleSheet("color:%s;font-size:10px;" % self.pal["muted"])
        if p == "codex":
            self._refresh_models()

    def _model_value(self):
        index = self.model.currentIndex()
        if index >= 0 and self.model.currentText() == self.model.itemText(index):
            return self.model.itemData(index) or ""
        return self.model.currentText().strip()

    def _set_models(self, models, current):
        self.model.clear()
        if self._prov() in AGENT_PROVIDERS:
            self.model.addItem("Domyślny model agenta", "")
        for model in models:
            if model:
                self.model.addItem(model, model)
        index = self.model.findData(current)
        if index >= 0:
            self.model.setCurrentIndex(index)
        else:
            self.model.setEditText(current)

    def _executable_changed(self, *_):
        self._models_client.abort()
        if self._prov() == "codex":
            self.refresh_models.setEnabled(True)
            self.model_status.setText("Po zmianie programu kliknij Odśwież, aby pobrać jego listę modeli.")

    def _refresh_models(self):
        if self._prov() != "codex":
            return
        self.refresh_models.setEnabled(False)
        self.model_status.setText("Pobieram listę modeli z Codex…")
        self._models_client.request_models({"provider": "codex", "executable": self.executable.text().strip()})

    def _models_ready(self, models):
        if self._prov() != "codex":
            return
        self._set_models([item["model"] for item in models], self._model_value())
        for item in models:
            self.model.setItemData(self.model.findData(item["model"]), item["displayName"], Qt.ItemDataRole.ToolTipRole)
        self.refresh_models.setEnabled(True)
        self.model_status.setText("Pobrano %d modeli z CLI. Możesz też wpisać identyfikator ręcznie." % len(models))

    def _models_failed(self, message):
        if self._prov() == "codex":
            self.refresh_models.setEnabled(True)
            self.model_status.setText(message + " Możesz użyć modelu domyślnego lub wpisać identyfikator ręcznie.")

    def _insecure_endpoint(self):
        """Klucz API przez nieszyfrowane http:// do innego komputera (audyt B4)."""
        if not PROVIDERS[self._prov()]["needs_key"]:
            return False
        url = QUrl(self.endpoint.text().strip())
        return url.scheme().lower() == "http" and url.host().lower() not in ("localhost", "127.0.0.1", "::1")

    def _check_endpoint(self, *_):
        bad = self._insecure_endpoint()
        self.http_warn.setVisible(bad)
        if bad:
            self.http_warn.setText("⚠ " + tr("Adres nie używa szyfrowania (http://) – klucz API zostałby wysłany "
                                             "jawnym tekstem. Użyj https://."))
            self.http_warn.setStyleSheet("color:%s;font-size:10px;font-weight:600;" % self.pal["error"])

    # ------------------------------------------------------------------ akcje
    def _browse_cli(self):
        path, _ = QFileDialog.getOpenFileName(self, "Wybierz program Codex / Claude Code")
        if path:
            self.executable.setText(path)

    def _show_mcp_config(self):
        script = str(Path(__file__).resolve().parent.parent / "mcp_server.py")
        python = shutil.which("python") or "python"
        args = [script, "--connection-file", str(connection_path())]
        config = {"mcpServers": {"qgis": {"command": python, "args": args}}}
        # JSON for Claude; TOML strings/arrays use compatible JSON escaping for these paths.
        toml = '[mcp_servers.qgis]\ncommand = %s\nargs = %s\n' % (json.dumps(python), json.dumps(args))
        dlg = QDialog(self)
        dlg.setWindowTitle("Konfiguracja MCP — skopiuj do ustawień agenta")
        layout = QVBoxLayout(dlg)
        hint = QLabel("Claude Code: konfiguracja MCP JSON. Codex: config.toml. Wymagany Python 3.10+. "
                      "Most działa, gdy panel wtyczki jest uruchomiony. Nie kopiuj pliku połączenia: zawiera token.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText("Claude Code\n" + json.dumps(config, indent=2, ensure_ascii=False) + "\n\nCodex\n" + toml)
        layout.addWidget(editor)
        dlg.resize(700, 420)
        dlg.exec()

    def _delete_key(self):
        SecretStore.delete(self._prov())
        self._provider_changed()
        self.test_label.setText(tr("Klucz usunięty."))

    def _current_config(self):
        p = self._prov()
        key = self.api_key.text().strip() or (SecretStore.get(p) if PROVIDERS[p]["needs_key"] else "")
        return {"provider": p, "model": self._model_value(), "endpoint": self.endpoint.text().strip(),
                "workspace_id": self.workspace.text().strip() if p == "anthropic" else "",
                "api_key": key, "temperature": OLLAMA_TEMPERATURE if p == "ollama" else None,
                "executable": self.executable.text().strip(),
                "max_tokens": 200, "timeout_s": 300 if p in AGENT_PROVIDERS else 30}

    def _test(self):
        self.test_label.setStyleSheet("color:%s;" % self.pal["muted"])
        self.test_label.setText(tr("Łączę…"))
        self.test_btn.setEnabled(False)
        self._client.request(TEST_SYSTEM, [{"role": "user", "content": TEST_USER}], self._current_config())

    def _test_ok(self, text):
        self.test_btn.setEnabled(True)
        self.test_label.setStyleSheet("color:%s;" % self.pal["ok"])
        self.test_label.setText(tr("✔ Połączenie działa."))

    def _test_fail(self, msg):
        self.test_btn.setEnabled(True)
        self.test_label.setStyleSheet("color:%s;" % self.pal["error"])
        self.test_label.setText("✖ " + msg)

    def _save(self):
        p = self._prov()
        if self._insecure_endpoint():
            ans = QMessageBox.warning(
                self, PLUGIN_NAME, tr("Adres API używa nieszyfrowanego połączenia http://, więc klucz API "
                                      "może zostać przechwycony. Zapisać mimo to?"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if ans != QMessageBox.StandardButton.Yes:
                return
        s = self.settings
        s.set_value("provider", p)
        s.set_value("model/" + p, self._model_value() or PROVIDERS[p]["models"][0])
        s.set_value("endpoint/" + p, self.endpoint.text().strip() or PROVIDERS[p]["url"])
        if p == "anthropic":
            s.set_value("workspace/" + p, self.workspace.text().strip())
        s.set_value("send_samples", self.send_samples.isChecked())
        s.set_value("allow_python", self.allow_python.isChecked())
        s.set_value("mcp_enabled", self.mcp_enabled.isChecked())
        if p in AGENT_PROVIDERS:
            s.set_value("executable/" + p, self.executable.text().strip())
        key = self.api_key.text().strip()
        if key and PROVIDERS[p]["needs_key"]:
            _, where = SecretStore.set(p, key, persist=self.persist.isChecked())
            self.api_key.clear()
            self.setProperty("key_storage_info", where)
        self.accept()
