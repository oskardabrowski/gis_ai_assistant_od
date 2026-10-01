"""Validate a proposed plan before it can enter the QGIS execution state."""
import json
import re
from .plan_model import ACTIONS, ACTION_ALIASES, normalize
from .utils import find_refs

MAX_PLAN_BYTES = 256 * 1024


def checked_plan(value):
    if not isinstance(value, dict) or value.get("type") != "plan":
        raise ValueError("Oczekiwano obiektu planu z type=plan.")
    if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > MAX_PLAN_BYTES:
        raise ValueError("Plan jest zbyt duży.")
    steps = value.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 60:
        raise ValueError("Plan musi zawierać od 1 do 60 kroków.")
    known = set()
    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            raise ValueError("Każdy krok musi być obiektem JSON.")
        sid = str(step.get("id") or "s%d" % i).strip()
        if not re.fullmatch(r"[\w-]{1,64}", sid) or sid in known:
            raise ValueError("Powtórzony lub pusty identyfikator kroku: %s." % sid)
        action = step.get("action", "processing" if step.get("algorithm") else "note")
        if isinstance(action, str):
            action = action.strip().lower()
        action = ACTION_ALIASES.get(str(action), action)
        if not isinstance(action, str) or action not in ACTIONS:
            raise ValueError("Nieobsługiwana akcja planu: %s." % action)
        missing = find_refs({k: v for k, v in step.items() if not k.startswith("_")}) - known
        if missing:
            raise ValueError("Brak wcześniejszego kroku: %s." % ", ".join(sorted(missing)))
        if action in ("processing", "processing_dialog"):
            algorithm = step.get("algorithm") or step.get("algorithm_id")
            if not isinstance(algorithm, str) or not algorithm.strip():
                raise ValueError("Krok %s nie ma identyfikatora algorytmu." % sid)
            if not isinstance(step.get("params", {}), dict):
                raise ValueError("Parametry algorytmu muszą być obiektem JSON.")
        if action == "load_layer" and (not isinstance(step.get("uri"), str) or not step["uri"]):
            raise ValueError("Krok wczytania warstwy nie ma URI.")
        if action == "python" and not isinstance(step.get("code"), str):
            raise ValueError("Krok Python nie ma kodu.")
        known.add(sid)
    return normalize(json.loads(json.dumps(value)))
