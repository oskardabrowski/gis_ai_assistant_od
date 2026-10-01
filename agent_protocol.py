"""Official agent CLI protocols. No Qt, credentials, shell commands or network calls."""
import json
import os
from pathlib import Path
import shutil
import platform

AGENT_PROVIDERS = {"codex", "claude_code"}
MAX_AGENT_OUTPUT = 4 * 1024 * 1024
PLANNER_INSTRUCTION = (
    "You are a QGIS planning assistant. Read the request object from stdin. "
    "Follow its system instructions and messages. Return only the complete response JSON. "
    "Do not use tools, run commands, read files, or perform GIS operations yourself. "
    "The QGIS application will show the plan for human approval and execute it."
)


class AgentError(ValueError):
    pass


def agent_environment(environment):
    """Keep official CLI login stores, never inherit API/provider overrides."""
    blocked = {
        "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID", "CODEX_API_KEY",
        "CODEX_ACCESS_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR",
        "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
        "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SIMPLE",
    }
    return {k: v for k, v in environment.items() if k.upper() not in blocked}


def resolve_command(provider, configured="", environment=None):
    """Resolve native binaries and known npm launchers without cmd.exe/PowerShell."""
    if provider not in AGENT_PROVIDERS:
        raise AgentError("Nieobsługiwany dostawca agenta.")
    env = environment or os.environ
    if os.name == "nt":
        env = {key.upper(): value for key, value in env.items()}
    name = "claude" if provider == "claude_code" else "codex"
    candidate = configured.strip().strip('"')
    if candidate and not Path(candidate).is_file():
        candidate = shutil.which(candidate, path=env.get("PATH", "")) or candidate
    if not candidate:
        candidate = shutil.which(name, path=env.get("PATH", "")) or ""
    if not candidate:
        home = Path(env.get("USERPROFILE") or env.get("HOME") or str(Path.home()))
        candidates = [home / ".local" / "bin" / (name + ".exe")]
        if env.get("APPDATA"):
            candidates.append(Path(env["APPDATA"]) / "npm" / (name + ".cmd"))
        candidate = next((str(p) for p in candidates if p.is_file()), "")
    if not candidate or not Path(candidate).is_file():
        raise AgentError("Nie znaleziono %s. Zainstaluj oficjalny CLI lub wskaż jego plik w ustawieniach." % name)
    path = Path(candidate).resolve()
    if path.suffix.lower() in {".cmd", ".bat", ".ps1"}:
        if provider == "codex":
            arch = "aarch64" if platform.machine().lower() in {"arm64", "aarch64"} else "x86_64"
            package_arch = "arm64" if arch == "aarch64" else "x64"
            vendor = Path("vendor") / (arch + "-pc-windows-msvc")
            roots = [path.parent / "node_modules" / "@openai" / "codex" / "node_modules" / "@openai" /
                     ("codex-win32-" + package_arch), path.parent / "node_modules" / "@openai" / "codex"]
            for root in roots:
                for sub in ("bin", "codex"):
                    native = root / vendor / sub / "codex.exe"
                    if native.is_file():
                        return str(native), []
            raise AgentError("Nie znaleziono natywnego codex.exe obok launchera npm. Zaktualizuj instalację Codex CLI.")
        package = ("@anthropic-ai", "claude-code", "cli.js") if provider == "claude_code" else (
            "@openai", "codex", "bin", "codex.js")
        script = path.parent.joinpath("node_modules", *package)
        node = shutil.which("node", path=env.get("PATH", ""))
        if not node:
            for base in (env.get("ProgramFiles"), env.get("PROGRAMFILES")):
                if base and (Path(base) / "nodejs" / "node.exe").is_file():
                    node = str(Path(base) / "nodejs" / "node.exe")
                    break
        if not script.is_file() or not node:
            raise AgentError("Wskaż natywny plik programu albo standardowy launcher npm z zainstalowanym Node.js.")
        return node, [str(script)]
    return str(path), []


def auth_arguments(provider):
    return ["auth", "status", "--json"] if provider == "claude_code" else ["login", "status"]


def require_subscription(provider, stdout, stderr, exit_code):
    if provider == "claude_code":
        try:
            status = json.loads(stdout)
        except (ValueError, TypeError):
            status = {}
        valid = isinstance(status, dict) and status.get("loggedIn") is True and (
            status.get("authMethod") == "claude.ai" and not status.get("apiKeySource"))
    else:
        valid = "logged in using chatgpt" in (stdout + "\n" + stderr).lower()
    if exit_code != 0 or not valid:
        command = "claude auth login" if provider == "claude_code" else "codex login"
        raise AgentError(
            "Brak potwierdzonego logowania abonamentowego. Uruchom %s w terminalu i zaloguj się kontem "
            "z dostępem do agenta. Klucze API nie są używane w tym trybie. "
            "Jeśli logowanie już działa, zaktualizuj CLI i sprawdź jego polecenie status." % command)


def request_arguments(provider, model=""):
    if provider == "claude_code":
        args = ["-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
                "--no-session-persistence", "--tools", "", "--permission-mode", "dontAsk",
                "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--setting-sources", "",
                "--settings", '{"disableAllHooks":true,"enabledPlugins":{},"forceLoginMethod":"claudeai"}',
                "--system-prompt", PLANNER_INSTRUCTION]
    else:
        args = ["exec", "--ignore-user-config", "--ignore-rules", "--ephemeral", "--skip-git-repo-check",
                "--sandbox", "read-only", "--color", "never", "--json"]
        for setting in ('forced_login_method="chatgpt"', 'web_search="disabled"', 'approval_policy="never"',
                        'features.shell_tool=false', 'features.apps=false', 'features.hooks=false',
                        'features.multi_agent=false', 'features.remote_plugin=false', 'features.memories=false',
                        'features.goals=false', 'features.code_mode.enabled=false'):
            args.extend(["-c", setting])
    if model.strip():
        args.extend(["--model", model.strip()])
    if provider == "codex":
        args.append("-")
    return args


def request_payload(system, messages):
    return (PLANNER_INSTRUCTION + "\n" + json.dumps(
        {"system": system, "messages": messages}, ensure_ascii=False) + "\n").encode("utf-8")


class AgentStream:
    """Incremental JSONL parser; never deliver partial or failed agent responses."""
    def __init__(self, provider):
        self.provider = provider
        self.pending = b""
        self.total = 0
        self.chars = 0
        self.text = ""
        self.complete = False
        self.error = None

    def feed(self, chunk):
        self.total += len(chunk)
        if self.total > MAX_AGENT_OUTPUT:
            raise AgentError("Odpowiedź agenta przekroczyła dozwolony rozmiar.")
        lines = (self.pending + chunk).split(b"\n")
        self.pending = lines.pop()
        for line in lines:
            self._line(line)

    def _line(self, line):
        if not line.strip():
            return
        try:
            event = json.loads(line)
        except (ValueError, UnicodeError) as exc:
            raise AgentError("Agent zwrócił niepoprawny strumień JSON. Zaktualizuj CLI.") from exc
        if not isinstance(event, dict):
            raise AgentError("Niepoprawny format zdarzenia agenta.")
        kind = event.get("type")
        if self.provider == "claude_code":
            if kind == "stream_event":
                inner = event.get("event")
                delta = inner.get("delta") if isinstance(inner, dict) else None
                fragment = delta.get("text") if isinstance(delta, dict) else None
                if isinstance(fragment, str):
                    self.chars += len(fragment)
            elif kind == "result":
                self.complete = not event.get("is_error") and event.get("subtype") == "success"
                self.text = event.get("result") or ""
                if not self.complete:
                    self.error = "Claude Code nie ukończył odpowiedzi (%s). Sprawdź logowanie i limity konta." % (
                        event.get("subtype") or "błąd")
        else:
            item = event.get("item")
            if kind == "item.completed" and isinstance(item, dict) and item.get("type") == "agent_message":
                self.text = item.get("text") or ""
            elif kind == "turn.completed":
                self.complete = True
            elif kind == "turn.failed":
                self.error = "Codex nie ukończył odpowiedzi. Sprawdź logowanie, limity konta i aktualność CLI."
        if not isinstance(self.text, str):
            raise AgentError("Niepoprawny format tekstu odpowiedzi agenta.")
        self.chars = max(self.chars, len(self.text))

    def finish(self, exit_code):
        if self.pending.strip():
            self._line(self.pending)
            self.pending = b""
        if exit_code or self.error or not self.complete or not isinstance(self.text, str) or not self.text.strip():
            raise AgentError(self.error or "Agent zakończył pracę bez kompletnej odpowiedzi. Sprawdź CLI i konto.")
        return self.text
