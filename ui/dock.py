# -*- coding: utf-8 -*-
"""Panel „GIS Assistant AI” – linia poleceń, plan działań, WYKONAJ, kroki interaktywne."""
import traceback
from urllib.parse import unquote

from qgis.core import Qgis, QgsMessageLog, QgsProject, QgsVectorLayer, QgsWkbTypes
from qgis.gui import QgsDockWidget, QgsMapLayerComboBox
from qgis.PyQt.QtCore import QEvent, QSize, Qt, QTimer, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QDesktopServices, QIcon, QPixmap
from qgis.PyQt.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QTextBrowser, QToolButton, QVBoxLayout, QWidget,
)

from .. import plan_model
from ..constants import (
    CHAT_ICON_PATH, EXAMPLE_PROMPTS, FOOTER_LEFT, FOOTER_LOGO_PATH, FOOTER_LOGO_URL, FOOTER_RIGHT,
    HISTORY_EXCHANGES, ICON_PATH, LOG_TAG,
    PLUGIN_NAME, PROVIDERS,
)
from ..i18n import language_label, tr
from ..context_builder import build_context, context_json, layer_info
from ..executor import PlanExecutor
from ..external_plans import ExternalPlans
from ..integration_validation import checked_plan
from ..qgis_bridge import QGISBridge
from ..llm_client import LLMClient
from ..prompts import SYSTEM_PROMPT, revision_message, user_message
from ..settings import SecretStore, Settings
from ..utils import extract_json, is_web_url, md_to_html, python_risks
from . import icons, render, styles
from .settings_dialog import SettingsDialog
from .confirm_dialog import ConfirmDialog
from .widgets import CollapsibleSection, ElidedLabel


FOOTER_LOGO_HEIGHT = 36


class GISAssistantAIDock(QgsDockWidget):
    closingPlugin = pyqtSignal()

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.settings = Settings()
        self.setObjectName("GISAssistantAIDock")
        self.setWindowTitle(PLUGIN_NAME)
        self.pal = styles.palette_for(iface.mainWindow())

        self.client = LLMClient(self)
        self.client.finished.connect(self._on_llm_text)
        self.client.failed.connect(self._on_llm_error)
        self.client.progress.connect(self._on_llm_progress)

        self.executor = PlanExecutor(iface, self)
        self.executor.changed.connect(self._render_response)
        self.executor.log.connect(self._log)
        self.executor.progress.connect(self._on_progress)
        self.executor.waitingForUser.connect(self._show_user_panel)
        self.executor.confirmPython.connect(self._confirm_python)
        self.executor.confirmLoad.connect(self._confirm_load)
        self.executor.stepFailed.connect(self._show_error_panel)
        self.executor.replanRequested.connect(lambda idx: self._request_revision("replan", idx))
        self.executor.finished.connect(self._on_plan_finished)

        self.response = None        # ostatnia odpowiedź modelu (dict)
        self.history = []           # [(polecenie, surowa odpowiedź)]
        self._purpose = None        # ("query", tekst) | ("replan"/"repair", indeks)
        self._last_query = ""
        self._last_raw = ""
        self._plan_ctx = ""
        self._pending_query = ""
        self._pending_ctx = ""
        self._plan_note = None
        self.external = ExternalPlans(self)

        self._build_ui()
        self._render_welcome()
        QgsProject.instance().layersAdded.connect(self._on_layers_added)
        self.bridge = QGISBridge(self)
        self._configure_bridge()

    # ================================================================== budowa UI
    def _build_ui(self):
        root = QWidget()
        root.setObjectName("gisAssistantAIRoot")
        lay = QVBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._build_header())

        body = QWidget()
        body.setObjectName("panelBody")
        b = QVBoxLayout(body)
        b.setContentsMargins(8, 8, 8, 8)
        b.setSpacing(8)
        self._body_layout = b

        # ---------------------------------------------------------- Polecenie
        self.cmd_section = CollapsibleSection(tr("Polecenie"), "cmd", self.pal)
        c = self.cmd_section.body_layout
        self.prompt = QPlainTextEdit()
        self.prompt.setObjectName("prompt")
        self.prompt.setPlaceholderText(tr("Opisz, co chcesz zrobić w QGIS…\nnp. „Stwórz bufor o promieniu 100 m "
                                       "od szkół w powiecie piaseczyńskim”"))
        self.prompt.setFixedHeight(74)
        self.prompt.installEventFilter(self)
        c.addWidget(self.prompt)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.examples = QToolButton()
        self.examples.setObjectName("examples")
        self.examples.setText(tr("Przykłady"))
        self.examples.setIcon(icons.icon("sparkle", self.pal["accent"], 12))
        self.examples.setIconSize(icons.qsize(12))
        self.examples.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.examples.setCursor(Qt.CursorShape.PointingHandCursor)
        self.examples.setToolTip(tr("Gotowe polecenia do wypróbowania"))
        self.examples.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.examples)
        for ex in EXAMPLE_PROMPTS:
            ex = tr(ex)
            menu.addAction(ex, lambda e=ex: self._use_example(e))
        self.examples.setMenu(menu)
        row.addWidget(self.examples)
        hint = ElidedLabel(tr("Enter – wyślij, Shift+Enter – nowa linia"))
        hint.setObjectName("hint")
        row.addWidget(hint, 1)
        self.ask_btn = QPushButton(tr("Analizuj"))
        self.ask_btn.setObjectName("primary")
        self.ask_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ask_btn.clicked.connect(lambda: self._submit())
        row.addWidget(self.ask_btn)
        c.addLayout(row)
        b.addWidget(self.cmd_section)

        # ---------------------------------------------------------- status
        status_box = QWidget()
        sv = QVBoxLayout(status_box)
        sv.setContentsMargins(2, 0, 2, 0)
        sv.setSpacing(2)
        self.busy = QProgressBar()
        self.busy.setObjectName("busy")
        self.busy.setTextVisible(False)
        self.busy.setFixedHeight(2)
        self.busy.setRange(0, 1)
        self.busy.setValue(0)
        sv.addWidget(self.busy)
        self.status = QLabel("")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        sv.addWidget(self.status)
        b.addWidget(status_box)

        # ---------------------------------------------------------- Wynik
        self.res_section = CollapsibleSection(tr("Wynik"), "res", self.pal)
        self.details_btn = QToolButton()
        self.details_btn.setObjectName("linkButton")
        self.details_btn.setText(tr("Szczegóły techniczne"))
        self.details_btn.setCheckable(True)
        self.details_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.details_btn.setChecked(self.settings.value("show_details", False, bool))
        self.details_btn.toggled.connect(self._toggle_details)
        self.res_section.add_header_widget(self.details_btn)
        r = self.res_section.body_layout
        r.setContentsMargins(4, 4, 4, 6)
        self.result = QTextBrowser()
        self.result.setObjectName("result")
        self.result.setOpenLinks(False)
        self.result.anchorClicked.connect(self._on_anchor)
        self.result.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.result.setMinimumHeight(160)
        r.addWidget(self.result, 1)
        r.addWidget(self._build_user_panel())
        r.addWidget(self._build_error_panel())
        b.addWidget(self.res_section, 1)

        # ---------------------------------------------------------- WYKONAJ
        act = QHBoxLayout()
        act.setSpacing(8)
        self.exec_btn = QPushButton(tr("WYKONAJ"))
        self.exec_btn.setObjectName("execute")
        self.exec_btn.setEnabled(False)
        self.exec_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.exec_btn.setToolTip(tr("Wykonaj plan działań krok po kroku"))
        self.exec_btn.clicked.connect(self._execute)
        act.addWidget(self.exec_btn, 1)
        self.stop_btn = QPushButton(tr("Zatrzymaj"))
        self.stop_btn.setObjectName("danger")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)
        act.addWidget(self.stop_btn)
        b.addLayout(act)

        # ---------------------------------------------------------- Dziennik
        self.log_section = CollapsibleSection(tr("Dziennik"), "log", self.pal)
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("log")
        self.log_view.setReadOnly(True)
        self.log_view.setFixedHeight(130)
        self.log_view.setMaximumBlockCount(500)
        self.log_section.body_layout.setContentsMargins(4, 4, 4, 4)
        self.log_section.body_layout.addWidget(self.log_view)
        b.addWidget(self.log_section)
        b.addStretch(0)  # przejmuje wolne miejsce, gdy „Wynik” jest zwinięty

        # stan zwinięcia sekcji jest zapamiętywany
        for key, sec, default in (("cmd", self.cmd_section, True), ("res", self.res_section, True),
                                  ("log", self.log_section, False)):
            sec.setExpanded(self.settings.value("ui/section_" + key, default, bool))
            sec.toggled.connect(lambda on, k=key: self.settings.set_value("ui/section_" + k, on))
        self.res_section.toggled.connect(self._update_stretch)
        self._update_stretch()

        # Gdy panel jest niższy niż jego zawartość, pojawia się suwak – nagłówek i stopka zostają na miejscu,
        # a przycisk WYKONAJ i Dziennik są zawsze osiągalne.
        scroll = QScrollArea()
        scroll.setObjectName("panelScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        self._scroll = scroll
        lay.addWidget(scroll, 1)
        lay.addWidget(self._build_footer())
        root.setStyleSheet(styles.stylesheet(self.pal))
        # przewijamy tylko w pionie – panel nie może być węższy niż jego zawartość
        body.ensurePolished()
        scroll.setMinimumWidth(body.minimumSizeHint().width() + scroll.verticalScrollBar().sizeHint().width())
        self.setWidget(root)

    def _update_stretch(self, *_):
        b = self._body_layout
        expanded = self.res_section.isExpanded()
        b.setStretch(b.indexOf(self.res_section), 1 if expanded else 0)
        b.setStretch(b.count() - 1, 0 if expanded else 1)

    def _scroll_to(self, widget):
        """Przewija panel tak, by panel akcji lub błędu był widoczny także w niskim oknie."""
        QTimer.singleShot(0, lambda: self._scroll.ensureWidgetVisible(widget, 0, 8))

    def _reveal_result(self):
        """Nowa treść w sekcji „Wynik” – pokaż ją, nawet jeśli była zwinięta."""
        self.res_section.setExpanded(True)

    def _build_header(self):
        header = QFrame()
        header.setObjectName("header")
        h = QHBoxLayout(header)
        h.setContentsMargins(10, 8, 6, 8)
        h.setSpacing(8)
        logo = QLabel()
        logo.setPixmap(self._logo_pixmap(32))
        h.addWidget(logo)
        texts = QVBoxLayout()
        texts.setSpacing(0)
        title = QLabel(PLUGIN_NAME)
        title.setObjectName("title")
        sub = QLabel(tr("Powiedz, co chcesz zrobić w QGIS"))
        sub.setObjectName("subtitle")
        texts.addWidget(title)
        texts.addWidget(sub)
        h.addLayout(texts, 1)
        h.addWidget(self._icon_button("chat", tr("Nowe polecenie"), self._new_conversation))
        h.addWidget(self._icon_button("sliders", tr("Ustawienia"), self._open_settings))
        return header

    @staticmethod
    def _logo_pixmap(size, path=ICON_PATH):
        """Piksmap o zadanej wysokości (logiczne px), ostry także na ekranach HiDPI."""
        ratio = 2.0
        px = QPixmap(path).scaledToHeight(int(size * ratio), Qt.TransformationMode.SmoothTransformation)
        px.setDevicePixelRatio(ratio)
        return px

    def _icon_button(self, name, tip, slot):
        btn = QToolButton()
        btn.setObjectName("iconButton")
        color = self.pal["primary_text"] if self.pal is styles.DARK else self.pal["primary"]
        if name == "chat":
            btn.setIcon(icons.tinted_icon(CHAT_ICON_PATH, color, 18))
        else:
            btn.setIcon(icons.icon(name, color, 18))
        btn.setIconSize(icons.qsize(18))
        btn.setToolTip(tip)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(slot)
        return btn

    def _build_user_panel(self):
        self.user_panel = QFrame()
        self.user_panel.setObjectName("userPanel")
        v = QVBoxLayout(self.user_panel)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(6)
        self.user_title = QLabel()
        self.user_title.setObjectName("panelTitle")
        self.user_title.setStyleSheet("color:%s;" % self.pal["warn"])
        v.addWidget(self.user_title)
        self.user_msg = QLabel()
        self.user_msg.setWordWrap(True)
        self.user_msg.setTextFormat(Qt.TextFormat.RichText)
        self.user_msg.setOpenExternalLinks(True)
        self.user_msg.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        v.addWidget(self.user_msg)

        self.layer_row = QWidget()
        lr = QHBoxLayout(self.layer_row)
        lr.setContentsMargins(0, 0, 0, 0)
        self.layer_label = QLabel(tr("Warstwa:"))
        self.layer_combo = QgsMapLayerComboBox()
        try:
            self.layer_combo.setAllowEmptyLayer(True, tr("— wskaż warstwę —"))
        except TypeError:
            self.layer_combo.setAllowEmptyLayer(True)
        lr.addWidget(self.layer_label)
        lr.addWidget(self.layer_combo, 1)
        v.addWidget(self.layer_row)

        helpers = QHBoxLayout()
        self.help_plugin = QPushButton(tr("↗ Strona wtyczki"))
        self.help_dsm = QPushButton(tr("Menedżer źródeł danych"))
        self.help_alg = QPushButton(tr("Otwórz narzędzie"))
        for w in (self.help_plugin, self.help_dsm, self.help_alg):
            w.setObjectName("secondary")
            helpers.addWidget(w)
        helpers.addStretch(1)
        self.help_plugin.clicked.connect(lambda: self._open_url(self._user_step().get("plugin_url", "")))
        self.help_dsm.clicked.connect(lambda: self._open_dsm(self._user_step().get("dsm_page", "")))
        self.help_alg.clicked.connect(lambda: self._open_alg(self._user_step().get("algorithm_id", "")))
        v.addLayout(helpers)

        btns = QHBoxLayout()
        self.user_ok = QPushButton(tr("Wykonałem – kontynuuj"))
        self.user_ok.setObjectName("primary")
        self.user_ok.clicked.connect(self._user_continue)
        user_skip = QPushButton(tr("Pomiń krok"))
        user_skip.setObjectName("secondary")
        user_skip.clicked.connect(lambda: (self.user_panel.hide(), self.executor.skip()))
        user_abort = QPushButton(tr("Przerwij"))
        user_abort.setObjectName("danger")
        user_abort.clicked.connect(self._stop)
        btns.addWidget(self.user_ok, 1)
        btns.addWidget(user_skip)
        btns.addWidget(user_abort)
        v.addLayout(btns)
        self.user_panel.hide()
        return self.user_panel

    def _build_error_panel(self):
        self.error_panel = QFrame()
        self.error_panel.setObjectName("errorPanel")
        v = QVBoxLayout(self.error_panel)
        v.setContentsMargins(10, 8, 10, 8)
        self.error_title = QLabel()
        self.error_title.setObjectName("panelTitle")
        self.error_title.setStyleSheet("color:%s;" % self.pal["error"])
        v.addWidget(self.error_title)
        self.error_msg = QLabel()
        self.error_msg.setWordWrap(True)
        self.error_msg.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self.error_msg)
        btns = QHBoxLayout()
        fix = QPushButton(tr("Napraw z AI"))
        fix.setObjectName("primary")
        fix.setToolTip(tr("Asystent przeanalizuje błąd i poprawi pozostałe kroki planu"))
        fix.clicked.connect(lambda: self._request_revision("repair", self.executor.idx))
        retry = QPushButton(tr("Ponów"))
        retry.setObjectName("secondary")
        retry.clicked.connect(lambda: (self.error_panel.hide(), self.executor.retry()))
        skip = QPushButton(tr("Pomiń"))
        skip.setObjectName("secondary")
        skip.clicked.connect(lambda: (self.error_panel.hide(), self.executor.skip()))
        abort = QPushButton(tr("Przerwij"))
        abort.setObjectName("danger")
        abort.clicked.connect(self._stop)
        for w in (fix, retry, skip, abort):
            btns.addWidget(w)
        v.addLayout(btns)
        self.error_panel.hide()
        return self.error_panel

    def _build_footer(self):
        footer = QFrame()
        footer.setObjectName("footer")
        row = QHBoxLayout(footer)
        row.setContentsMargins(12, 3, 12, 3)
        row.setSpacing(8)
        link_style = "color:%s;text-decoration:none;letter-spacing:0.5px;font-weight:normal;" % self.pal["primary_text"]

        def link(text, url, align):
            lbl = QLabel("<a href='%s' style='%s'>%s</a>" % (url, link_style, text))
            lbl.setObjectName("footerLink")
            lbl.setOpenExternalLinks(True)
            lbl.setAlignment(align | Qt.AlignmentFlag.AlignVCenter)
            lbl.setToolTip(url.split("?")[0])
            return lbl

        logo = QToolButton()
        logo.setObjectName("footerLogo")
        # logo wypełnia całą wysokość stopki (marginesy 3 px)
        es = self._logo_pixmap(FOOTER_LOGO_HEIGHT, FOOTER_LOGO_PATH)
        logo.setIcon(QIcon(es))
        logo.setIconSize(QSize(round(es.width() / es.devicePixelRatio()), FOOTER_LOGO_HEIGHT))
        logo.setCursor(Qt.CursorShape.PointingHandCursor)
        logo.setToolTip("EnviroSolutions – " + FOOTER_LOGO_URL)
        logo.setAutoRaise(True)
        logo.clicked.connect(lambda: self._open_url(FOOTER_LOGO_URL))
        # równe odstępy po obu stronach logo – leży dokładnie pomiędzy napisami
        row.addWidget(link(tr(FOOTER_LEFT[0]), FOOTER_LEFT[1], Qt.AlignmentFlag.AlignLeft), 0,
                      Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        row.addWidget(logo, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        row.addWidget(link(tr(FOOTER_RIGHT[0]), FOOTER_RIGHT[1], Qt.AlignmentFlag.AlignRight), 0,
                      Qt.AlignmentFlag.AlignVCenter)
        return footer

    # ================================================================== zdarzenia Qt
    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self.prompt and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and \
                    not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                self._submit()
                return True
        return super().eventFilter(obj, event)

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self.prompt.setFocus()

    def closeEvent(self, event):  # noqa: N802
        self.closingPlugin.emit()
        event.accept()

    def cleanup(self):
        self.bridge.stop()
        try:
            QgsProject.instance().layersAdded.disconnect(self._on_layers_added)
        except (TypeError, RuntimeError):
            pass
        self.client.abort()
        self.executor.abort()

    # ================================================================== zapytania do AI
    def _use_example(self, text):
        self.prompt.setPlainText(text)
        self.prompt.setFocus()

    def _config_or_prompt(self):
        cfg = self.settings.llm_config()
        if PROVIDERS[cfg["provider"]]["needs_key"] and not cfg["api_key"]:
            self.iface.messageBar().pushMessage(PLUGIN_NAME, tr("Najpierw podaj klucz API w ustawieniach."),
                                                level=Qgis.MessageLevel.Warning, duration=5)
            self._open_settings()
            cfg = self.settings.llm_config()
            if PROVIDERS[cfg["provider"]]["needs_key"] and not cfg["api_key"]:
                return None
        return cfg

    def _submit(self, text=None):
        query = (text if text is not None else self.prompt.toPlainText()).strip()
        if not query:
            return
        if self.executor.running:
            self.iface.messageBar().pushMessage(PLUGIN_NAME,
                                                tr("Trwa wykonywanie planu – zatrzymaj je, aby zadać nowe pytanie."),
                                                level=Qgis.MessageLevel.Info, duration=4)
            return
        cfg = self._config_or_prompt()
        if cfg is None:
            return
        self._pending_query = query
        try:
            ctx = context_json(build_context(self.iface, self.settings))
        except ValueError as exc:
            self._log("error", str(exc))
            return
        self.external.clear()
        self._pending_ctx = ctx
        messages = self._history_messages() + [{"role": "user", "content": user_message(query, ctx, language_label())}]
        self._purpose = ("query", query)
        self.response = None
        self.exec_btn.setEnabled(False)
        self.exec_btn.setText(tr("WYKONAJ"))
        self.user_panel.hide()
        self.error_panel.hide()
        self.result.setHtml(render.thinking(self.pal, query))
        self._set_busy(True, tr("Analizuję polecenie i projekt…"))
        self._log("info", tr("Polecenie: %s") % query)
        self.client.request(SYSTEM_PROMPT, messages, cfg)

    def _history_messages(self):
        msgs = []
        for q, raw in self.history[-HISTORY_EXCHANGES:]:
            msgs.append({"role": "user", "content": "(wcześniejsze polecenie w tej rozmowie)\n" + q})
            msgs.append({"role": "assistant", "content": raw[:5000]})
        return msgs

    def _on_llm_text(self, text):
        purpose, data = self._purpose or ("query", "")
        self._purpose = None
        self._set_busy(False)
        if purpose == "query":
            self._handle_query_response(data, text)
        else:
            self._handle_revision(purpose, data, text)
            if self.executor.running and not self.error_panel.isVisibleTo(self):
                self.busy.setRange(0, 0)
                self.status.setText(tr("Wykonuję plan…"))

    def _handle_query_response(self, query, text):
        self._reveal_result()
        try:
            raw = extract_json(text)
            if raw.get("type") in ("plan", "plan_revision") or raw.get("steps"):
                raw["type"] = "plan"
                resp = checked_plan(raw)
            else:
                resp = plan_model.normalize(raw)
            resp = plan_model.validate(resp)
        except Exception as e:  # noqa: BLE001
            self._log("error", tr("Nie udało się odczytać odpowiedzi: %s") % e)
            self.result.setHtml(render.error(self.pal, str(e), text))
            self.status.setText(tr("Odpowiedź modelu była niepoprawna."))
            return
        if resp["type"] == "plan_revision":
            resp["type"] = "plan"
        self.response = resp
        self._plan_note = None
        self._last_query, self._last_raw, self._plan_ctx = query, text, self._pending_ctx
        self.history.append((query, text))
        self.history = self.history[-(HISTORY_EXCHANGES + 2):]
        self.prompt.clear()
        self._render_response()
        if plan_model.executable(resp):
            self.exec_btn.setEnabled(True)
            n = len(resp["steps"])
            user_steps = sum(1 for s in resp["steps"] if s["action"] == "user_action")
            extra = tr(" (w tym %d wymagające Twojej akcji)") % user_steps if user_steps else ""
            self.status.setText(tr("Plan gotowy: %d krok(ów)%s. Sprawdź go i kliknij WYKONAJ.") % (n, extra))
        else:
            self.exec_btn.setEnabled(False)
            self.status.setText({"tool": tr("Znalazłem narzędzie."), "answer": tr("Odpowiedź gotowa."),
                                 "clarify": tr("Potrzebuję doprecyzowania.")}.get(resp["type"], ""))

    def _on_llm_error(self, message):
        self._reveal_result()
        purpose, data = self._purpose or ("query", "")
        self._purpose = None
        self._set_busy(False)
        self._log("error", message)
        if purpose == "query":
            self.result.setHtml(render.error(self.pal, message))
            self.status.setText(tr("Nie udało się uzyskać odpowiedzi."))
        elif purpose == "replan":
            self._log("warning", tr("Nie udało się dopasować planu – kontynuuję bez zmian."))
            self.executor.continue_plan()
        else:
            self._show_error_panel(self.executor.idx, tr("Korekta przez AI nie powiodła się: %s") % message)

    # ------------------------------------------------------------------ korekta planu
    def _request_revision(self, purpose, idx):
        if self.external.plan_id:
            self.external.await_revision(purpose, idx)
            return
        cfg = self._config_or_prompt()
        steps = self.executor.steps
        if cfg is None or self.response is None:
            if purpose == "replan":
                self.executor.continue_plan()
            return
        if purpose == "replan":
            from_idx = idx + 1
            if from_idx >= len(steps):
                self.executor.continue_plan()
                return
            step = steps[idx]
            res = self.executor.results.get(step["id"], {})
            lyr = QgsProject.instance().mapLayer(res.get("OUTPUT", "")) if res.get("OUTPUT") else None
            reason = "Użytkownik wykonał krok „%s”%s. Dopasuj dalsze kroki do rzeczywistych danych " \
                     "(nazwy pól, typ geometrii, układ współrzędnych)." % (
                         step["title"], " i wskazał warstwę „%s” (id: %s)" % (lyr.name(), lyr.id()) if lyr else "")
            extra = ""
            if lyr is not None:
                try:
                    extra = "Opis wskazanej warstwy: %s" % context_json(layer_info(
                        lyr, True, self.settings.value("send_samples", False, bool)))
                except ValueError as exc:
                    self._show_error_panel(idx, str(exc))
                    return
            status = tr("Dopasowuję dalsze kroki do wczytanych danych…")
        else:
            from_idx = idx
            step = steps[idx]
            reason = "Krok „%s” (id %s) zakończył się błędem: %s" % (step["title"], step["id"], step.get("_error", ""))
            try:
                extra = "Kod nieudanego kroku: %s" % context_json(
                    {k: v for k, v in step.items() if not k.startswith("_")})
            except ValueError as exc:
                self._show_error_panel(idx, str(exc))
                return
            status = tr("Analizuję błąd i poprawiam plan…")
        try:
            ctx = context_json(build_context(self.iface, self.settings))
        except ValueError as exc:
            self._show_error_panel(idx, str(exc))
            return
        messages = [
            {"role": "user", "content": user_message(self._last_query, self._plan_ctx, language_label())},
            {"role": "assistant", "content": self._last_raw[:8000]},
            {"role": "user", "content": revision_message(reason, self.response, from_idx,
                                                          self.executor.done_summary(), ctx, extra,
                                                          language_label())},
        ]
        self.error_panel.hide()
        self._purpose = (purpose, from_idx)
        self._set_busy(True, status)
        self._log("info", status)
        self.client.request(SYSTEM_PROMPT, messages, cfg)

    def _handle_revision(self, purpose, from_idx, text):
        try:
            rev = extract_json(text)
            new_steps = rev.get("steps") or []
            if new_steps:
                combined = checked_plan({"type": "plan", "steps": self.executor.steps[:from_idx] + new_steps})
                steps = plan_model.validate(combined)["steps"][from_idx:]
            else:
                steps = []
        except Exception as e:  # noqa: BLE001
            self._log("error", tr("Nie udało się odczytać korekty planu: %s") % e)
            if purpose == "replan":
                self.executor.continue_plan()
            else:
                self._show_error_panel(self.executor.idx, tr("Nie udało się odczytać korekty: %s") % e)
            return
        if not steps:
            if purpose == "replan":
                self._log("info", tr("AI potwierdziło, że dalsze kroki nie wymagają zmian."))
                self.executor.continue_plan()
            else:
                self._show_error_panel(self.executor.idx, tr("AI nie zaproponowało poprawionych kroków."))
            return
        self._plan_note = rev.get("note") or (
            tr("Plan dopasowany do danych.") if purpose == "replan" else tr("Plan poprawiony."))
        self._log("info", self._plan_note)
        self.executor.apply_revision(steps, from_idx)

    # ================================================================== wykonanie
    def _execute(self):
        if self.external.approve_revision():
            return
        if not plan_model.executable(self.response) or self.executor.running:
            return
        if any(s.get("_status") not in (None, "pending") for s in self.response["steps"]):
            ans = QMessageBox.question(self, PLUGIN_NAME,
                                       tr("Ten plan był już wykonywany. Wykonać go ponownie od początku?"))
            if ans != QMessageBox.StandardButton.Yes:
                return
        self._set_running(True)
        if self.external.plan_id:
            self.external.started = True
            self.external.cancelled = False
        self.executor.start(self.response)

    def _stop(self):
        self.external.stop()
        self.client.abort()
        self._purpose = None
        self.user_panel.hide()
        self.error_panel.hide()
        self.executor.abort()
        self._set_busy(False)
        self._set_running(False)

    def _set_running(self, running):
        self.exec_btn.setEnabled(not running and plan_model.executable(self.response))
        self.exec_btn.setText(tr("WYKONYWANIE…") if running else tr("WYKONAJ"))
        self.stop_btn.setEnabled(running)
        self.ask_btn.setEnabled(not running)
        if running:
            self.busy.setRange(0, 0)
            self.status.setText(tr("Wykonuję plan…"))
        else:
            self.busy.setRange(0, 1)
            self.busy.setValue(0)

    def _on_progress(self, value):
        if self.busy.maximum() != 100:
            self.busy.setRange(0, 100)
        self.busy.setValue(int(value))

    def _on_plan_finished(self, ok):
        self._set_running(False)
        self.user_panel.hide()
        self.error_panel.hide()
        if ok and any(s.get("_status") != "done" for s in self.response["steps"]):
            self.status.setText("Plan zakończony częściowo — nie wszystkie kroki wykonano.")
        elif ok:
            self.status.setText(tr("✔ Plan wykonany."))
            self.iface.messageBar().pushMessage(PLUGIN_NAME, tr("Plan „%s” został wykonany.") % (
                self.response.get("title") or ""), level=Qgis.MessageLevel.Success, duration=5)
        else:
            self.status.setText(tr("Wykonywanie przerwane."))

    # ------------------------------------------------------------------ krok użytkownika
    def _user_step(self):
        return self.executor.current() or {}

    def _show_user_panel(self, idx):
        self._reveal_result()
        step = self.executor.steps[idx]
        self.error_panel.hide()
        self.user_title.setText(tr("✋  Krok %d wymaga Twojej akcji: %s") % (idx + 1, step["title"]))
        self.user_msg.setText(md_to_html(step.get("message") or step.get("description") or "",
                                         self.pal["primary"], self.pal["code_bg"]))
        exp = step.get("expects_layer")
        if isinstance(exp, dict) or exp:
            exp = exp if isinstance(exp, dict) else {}
            self.layer_combo.setFilters(self._layer_filter(exp.get("geometry")))
            self.layer_label.setText(tr("Warstwa%s:") % (" (%s)" % exp["hint"] if exp.get("hint") else ""))
            self.layer_combo.setLayer(self._guess_layer(exp.get("geometry")))
            self.layer_row.show()
        else:
            self.layer_row.hide()
        self.help_plugin.setVisible(is_web_url(step.get("plugin_url")))
        self.help_dsm.setVisible(bool(step.get("dsm_page")))
        self.help_alg.setVisible(bool(step.get("algorithm_id")))
        self.user_panel.show()
        self._scroll_to(self.user_panel)
        self.busy.setRange(0, 1)
        self.status.setText(tr("Czekam na Twoją akcję…"))
        if not self.isVisible():
            self.show()
        self.raise_()

    @staticmethod
    def _layer_filter(geometry):
        f = Qgis.LayerFilter
        val = {"polygon": f.PolygonLayer, "line": f.LineLayer, "point": f.PointLayer,
               "raster": f.RasterLayer, "vector": f.VectorLayer}.get(str(geometry or "").lower(), f.All)
        try:  # QGIS 3.x (PyQt5) wymaga obiektu flag, w QGIS 4 enum jest flagą
            return Qgis.LayerFilters(val)
        except (AttributeError, TypeError):
            return val

    def _guess_layer(self, geometry):
        active = self.iface.activeLayer()
        if active is None:
            return None
        g = str(geometry or "any").lower()
        if g in ("any", "", "none"):
            return active
        if isinstance(active, QgsVectorLayer):
            gt = QgsWkbTypes.displayString(active.wkbType()).lower()
            if (g == "polygon" and "polygon" in gt) or (g == "line" and "line" in gt) or \
                    (g == "point" and "point" in gt) or g == "vector":
                return active
        return None

    def _on_layers_added(self, layers):
        if self.user_panel.isVisibleTo(self) and self.layer_row.isVisibleTo(self) and layers:
            for lyr in reversed(layers):
                self.layer_combo.setLayer(lyr)
                if self.layer_combo.currentLayer() is lyr:
                    break

    def _user_continue(self):
        layer = self.layer_combo.currentLayer() if self.layer_row.isVisibleTo(self) else None
        if self.layer_row.isVisibleTo(self) and layer is None:
            ans = QMessageBox.question(self, PLUGIN_NAME,
                                       tr("Nie wskazano warstwy. Kolejne kroki mogą jej potrzebować. Kontynuować?"))
            if ans != QMessageBox.StandardButton.Yes:
                return
        self.user_panel.hide()
        self.busy.setRange(0, 0)
        self.executor.user_done(layer)

    def _show_error_panel(self, idx, message):
        self._reveal_result()
        step = self.executor.steps[idx] if 0 <= idx < len(self.executor.steps) else {"title": ""}
        self.user_panel.hide()
        self.error_title.setText(tr("✖  Krok %d nie powiódł się: %s") % (idx + 1, step.get("title", "")))
        self.error_msg.setText(message[:900])
        self.error_panel.show()
        self._scroll_to(self.error_panel)
        self.busy.setRange(0, 1)
        self.status.setText(tr("Wybierz, co zrobić z błędem."))

    def _confirm_python(self, idx):
        step = self.executor.steps[idx]
        if not self.settings.value("allow_python", False, bool):
            self._log("warning", tr("Kod Python jest wyłączony w ustawieniach – krok pominięty."))
            self.executor.run_python(False)
            return
        code = step.get("code") or ""
        dlg = ConfirmDialog(self, tr("kod PyQGIS"),
                            tr("Krok „%s” chce wykonać poniższy kod Python. Sprawdź go przed zatwierdzeniem.")
                            % step["title"], code, python_risks(code), tr("Wykonaj kod"))
        self.executor.run_python(dlg.exec() == QDialog.DialogCode.Accepted)

    def _confirm_load(self, idx, source):
        step = self.executor.steps[idx]
        dlg = ConfirmDialog(self, tr("wczytanie danych"),
                            tr("Krok „%s” chce wczytać do projektu plik lub bazę danych wskazane przez AI. "
                               "Zatwierdź tylko, jeśli znasz to źródło.") % step["title"], source, (),
                            tr("Wczytaj"))
        self.executor.confirm_load(dlg.exec() == QDialog.DialogCode.Accepted)

    # ================================================================== render
    def _render_welcome(self):
        p = self.settings.provider
        self.result.setHtml(render.welcome(self.pal, tr(PROVIDERS[p]["label"]).split(" (")[0], self.settings.model(p),
                                           SecretStore.has(p) or not PROVIDERS[p]["needs_key"]))

    def _render_response(self):
        r = self.response
        if not r:
            return
        bar = self.result.verticalScrollBar()
        pos = bar.value()
        t = r["type"]
        if t == "plan":
            html = render.plan(self.pal, r, self.details_btn.isChecked(), self._plan_note)
        elif t == "tool":
            try:
                from qgis.utils import available_plugins
                installed = set(available_plugins)
            except Exception:  # noqa: BLE001
                installed = set()
            html = render.tools(self.pal, r, installed)
        elif t == "clarify":
            html = render.clarify(self.pal, r)
        else:
            html = render.answer(self.pal, r)
        self.result.setHtml(html)
        bar.setValue(pos)

    def _toggle_details(self, on):
        self.settings.set_value("show_details", on)
        self._render_response()

    # ================================================================== linki / akcje
    def _on_anchor(self, url):
        s = url.toString()
        if not s.startswith("action:"):
            self._open_url(s)
            return
        cmd = unquote(s[len("action:"):])
        if cmd == "settings":
            self._open_settings()
        elif cmd == "retry":
            self._submit(self._pending_query)
        elif cmd == "plugin_manager":
            try:
                self.iface.actionManagePlugins().trigger()
            except AttributeError:
                pass
        elif cmd.startswith("open_alg:"):
            self._open_alg(cmd[len("open_alg:"):])
        elif cmd.startswith("dsm:"):
            self._open_dsm(cmd[len("dsm:"):])
        elif cmd.startswith("answer:"):
            self._submit(cmd[len("answer:"):])

    @staticmethod
    def _open_url(url):
        """Otwiera w przeglądarce wyłącznie adresy http(s) – nigdy plików ani innych schematów (audyt A4)."""
        if not url:
            return
        if not is_web_url(url) or QUrl(url).scheme().lower() not in ("http", "https"):
            QgsMessageLog.logMessage(tr("Zablokowano otwarcie adresu spoza http(s): %s") % str(url)[:200],
                                     LOG_TAG, Qgis.MessageLevel.Warning)
            return
        QDesktopServices.openUrl(QUrl(url))

    def _open_alg(self, alg_id):
        if not alg_id:
            return
        import processing
        try:
            dlg = processing.createAlgorithmDialog(alg_id, {})
            if dlg:
                dlg.show()
                return
        except Exception:  # noqa: BLE001
            QgsMessageLog.logMessage(traceback.format_exc(), LOG_TAG, Qgis.MessageLevel.Info)
        try:
            processing.execAlgorithmDialog(alg_id, {})
        except Exception as e:  # noqa: BLE001
            self.iface.messageBar().pushMessage(PLUGIN_NAME, tr("Nie można otworzyć %s: %s") % (alg_id, e),
                                                level=Qgis.MessageLevel.Warning, duration=5)

    def _open_dsm(self, page):
        try:
            self.iface.openDataSourceManagerPage(page)
        except Exception:  # noqa: BLE001
            try:
                self.iface.actionDataSourceManager().trigger()
            except Exception:  # noqa: BLE001
                pass

    def _open_settings(self):
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec():
            self._configure_bridge()
            where = dlg.property("key_storage_info")
            if where:
                self.iface.messageBar().pushMessage(PLUGIN_NAME, tr("Klucz API zapisany: %s.") % where,
                                                    level=Qgis.MessageLevel.Info, duration=5)
            if not self.response:
                self._render_welcome()

    def _new_conversation(self):
        if self.executor.running:
            return
        self.client.abort()
        self._purpose = None
        self._set_busy(False)
        self.external.clear()
        self.history.clear()
        self.response = None
        self.exec_btn.setEnabled(False)
        self.user_panel.hide()
        self.error_panel.hide()
        self.status.setText(tr("Nowe polecenie."))
        self._render_welcome()
        self.prompt.setFocus()

    # ================================================================== pomocnicze
    def _configure_bridge(self):
        if self.settings.value("mcp_enabled", False, bool):
            try:
                self.bridge.start()
            except OSError:
                self._log("error", "Nie można uruchomić mostu MCP. Sprawdź uprawnienia profilu QGIS.")
        else:
            self.bridge.stop()

    def _set_busy(self, busy, text=""):
        self.busy.setRange(0, 0 if busy else 1)
        if not busy:
            self.busy.setValue(0)
        self.ask_btn.setEnabled(not busy and not self.executor.running)
        self.stop_btn.setEnabled(busy or self.executor.running)
        if text or busy:
            self.status.setText(text)
        self._busy_text = text if busy else ""

    def _on_llm_progress(self, chars):
        if chars and getattr(self, "_busy_text", ""):
            self.status.setText("%s %s" % (self._busy_text, tr("(odebrano znaków: %d)") % chars))

    def _log(self, level, message):
        prefix = {"error": "✖", "warning": "⚠", "success": "✔", "debug": "·"}.get(level, "›")
        self.log_view.appendPlainText("%s %s" % (prefix, message))
        lvl = {"error": Qgis.MessageLevel.Critical, "warning": Qgis.MessageLevel.Warning,
               "success": Qgis.MessageLevel.Success}.get(level, Qgis.MessageLevel.Info)
        QgsMessageLog.logMessage(message, LOG_TAG, lvl)
        if level == "error":
            self.log_section.setExpanded(True)
