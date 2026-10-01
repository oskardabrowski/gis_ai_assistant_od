"""Ownership and approval lifecycle for plans submitted by external MCP agents."""
import json
import uuid

from . import plan_model
from .integration_validation import checked_plan
from .utils import sanitize_source


class ExternalPlans:
    def __init__(self, dock):
        self.dock = dock
        self.clear()

    def clear(self):
        self.plan_id = None
        self.started = False
        self.cancelled = False
        self.revision_from = None
        self.revision_ready = False

    @staticmethod
    def _validate(value):
        from qgis.core import QgsApplication
        plan = checked_plan(value)
        registry = QgsApplication.processingRegistry()
        for step in plan["steps"]:
            if step["action"] in {"processing", "processing_dialog"} and not registry.algorithmById(step["algorithm"]):
                raise ValueError("Algorytm nie jest zainstalowany: " + step["algorithm"])
        return plan

    def accept(self, value):
        d = self.dock
        if d.client.busy or d.executor.running or (plan_model.executable(d.response) and
                any(s.get("_status") == "pending" for s in d.response["steps"])):
            raise ValueError("Panel jest zajęty. Zakończ plan lub wybierz Nowe polecenie w QGIS.")
        plan = self._validate(value)
        if not plan_model.executable(plan):
            raise ValueError("Plan musi zawierać co najmniej jedną akcję do wykonania.")
        self.clear()
        d._pending_ctx = ""
        d.response = None
        d._handle_query_response("Plan od zewnętrznego agenta MCP", json.dumps(plan, ensure_ascii=False))
        if d.response is None:
            raise ValueError("Panel nie przyjął planu.")
        self.plan_id = uuid.uuid4().hex
        d.show()
        d.raise_()
        return self.status(self.plan_id)

    def status(self, plan_id):
        if not self.plan_id or plan_id != self.plan_id:
            raise ValueError("Nieznany lub nieaktualny plan_id. Plan został zastąpiony albo sesja QGIS zakończona.")
        d = self.dock
        steps = d.response["steps"]
        states = [s.get("_status", "pending") for s in steps]
        if self.cancelled:
            phase = "cancelled"
        elif self.revision_ready:
            phase = "awaiting_revision_approval"
        elif self.revision_from is not None:
            phase = "awaiting_revision"
        elif not self.started:
            phase = "awaiting_approval"
        elif "error" in states:
            phase = "failed"
        elif d.executor.running:
            phase = "waiting_user" if "waiting" in states else "running"
        elif any(s != "done" for s in states):
            phase = "partial"
        else:
            phase = "completed"
        output = []
        for step in steps:
            item = {"id": step["id"], "action": step["action"], "status": step.get("_status", "pending")}
            if step.get("_error"):
                item["error"] = sanitize_source(str(step["_error"])[:1000], "ogr")
            output.append(item)
        layers = []
        if self.started:
            from qgis.core import QgsProject
            for sid, result in d.executor.results.items():
                for key, value in result.items():
                    layer = QgsProject.instance().mapLayer(value) if isinstance(value, str) else None
                    if layer is not None:
                        layers.append({"step_id": sid, "output": key, "layer_id": layer.id(), "name": layer.name()})
        return {"plan_id": plan_id, "status": phase, "steps": output, "output_layers": layers,
                "revision_from": self.revision_from, "approval": "WYKONAJ in QGIS"}

    def await_revision(self, purpose, idx):
        d = self.dock
        start = idx + 1 if purpose == "replan" else idx
        if start >= len(d.executor.steps):
            d.executor.continue_plan()
            return
        self.revision_from = start
        self.revision_ready = False
        d.error_panel.hide()
        d.status.setText("Przekaż zewnętrznemu agentowi: sprawdź status i popraw dalsze kroki przez MCP.")
        d.busy.setRange(0, 1)
        d._log("info", "Plan MCP czeka na korektę od zewnętrznego agenta.")

    def revise(self, plan_id, new_steps):
        phase = self.status(plan_id)["status"]
        if phase not in {"failed", "awaiting_revision"} or not self.dock.executor.running:
            raise ValueError("Korekta jest dozwolona wyłącznie dla wstrzymanego planu z błędem lub prośbą o korektę.")
        executor = self.dock.executor
        start = self.revision_from if self.revision_from is not None else executor.idx
        prefix = executor.steps[:start]
        plan = self._validate({"type": "plan", "steps": prefix + new_steps})
        executor.apply_revision(plan["steps"][start:], start, continue_after=False)
        self.revision_from = start
        self.revision_ready = True
        self.dock.error_panel.hide()
        self.dock.exec_btn.setText("ZATWIERDŹ KONTYNUACJĘ")
        self.dock.exec_btn.setEnabled(True)
        self.dock.status.setText("Agent poprawił dalsze kroki. Sprawdź plan i zatwierdź kontynuację.")
        self.dock.show()
        self.dock.raise_()
        return self.status(plan_id)

    def approve_revision(self):
        if not self.revision_ready:
            return False
        executor = self.dock.executor
        executor.idx = self.revision_from
        self.revision_from = None
        self.revision_ready = False
        self.dock._set_running(True)
        executor._schedule()
        return True

    def stop(self):
        self.cancelled = True
        self.revision_from = None
        self.revision_ready = False
