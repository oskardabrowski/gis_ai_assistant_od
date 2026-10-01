# -*- coding: utf-8 -*-
import os

from .i18n import N_

PLUGIN_NAME = "GIS Assistant AI"
PLUGIN_SLUG = "gis_assistant_ai"
PLUGIN_DIR = os.path.dirname(__file__)
IMG_DIR = os.path.join(PLUGIN_DIR, "img")
ICON_PATH = os.path.join(IMG_DIR, "icon.png")
CHAT_ICON_PATH = os.path.join(IMG_DIR, "chat.png")
# Logo EnviroSolutions w środku stopki (prowadzi do strony firmy).
FOOTER_LOGO_PATH = os.path.join(IMG_DIR, "es_logo.png")

# Konwencja EnviroSolutions: wspólny pasek narzędzi i menu
ENV_MENU_NAME = "EnviroSolutions"

# Prefiks ustawień pozostaje z wersji 0.x – dzięki temu po zmianie nazwy katalogu zachowane są
# ustawienia użytkownika i zapisany klucz API.
SETTINGS_PREFIX = "asystent_ai/"
LOG_TAG = "GIS Assistant AI"

_UTM = "?utm_source=stopka&utm_medium=wtyczka&utm_campaign=organic&utm_term=gis_assistant_ai"
FOOTER_LEFT = (N_("SYSTEMY I WSPARCIE GIS"), "https://envirosolutions.pl/systemy-opensource-gis/" + _UTM)
FOOTER_RIGHT = (N_("SZKOLENIA GIS"), "https://envirosolutions.pl/oferta-szkoleniowa/" + _UTM)
FOOTER_LOGO_URL = "https://envirosolutions.pl/"

# Dostawcy modeli językowych. Adresy i modele można zmienić w ustawieniach.
PROVIDERS = {
    "codex": {
        "label": N_("Połącz z Codex"),
        "url": "", "models": [""], "needs_key": False,
        "models_url": "https://learn.chatgpt.com/docs/models",
    },
    "claude_code": {
        "label": N_("Połącz z Claude Code"),
        "url": "", "models": ["", "sonnet", "opus", "haiku"], "needs_key": False,
        "models_url": "https://code.claude.com/docs/en/model-config",
    },
    "anthropic": {
        "label": N_("Anthropic Claude"),
        "url": "https://api.anthropic.com/v1/messages",
        "models": ["claude-sonnet-5", "claude-opus-5-5", "claude-haiku-4-5-20251001"],
        "models_url": "https://docs.claude.com/en/docs/about-claude/models/overview",
        "needs_key": True,
    },
    "openai": {
        "label": N_("OpenAI / API zgodne z OpenAI"),
        "url": "https://api.openai.com/v1/chat/completions",
        "models": ["gpt-4.1", "gpt-4.1-mini", "gpt-4o"],
        "models_url": "https://platform.openai.com/docs/models",
        "needs_key": True,
    },
    "ollama": {
        "label": N_("Ollama (lokalnie – dane nie opuszczają komputera)"),
        "url": "http://localhost:11434/api/chat",
        "models": ["qwen2.5:14b", "llama3.1:8b", "mistral-nemo"],
        "models_url": "https://ollama.com/library",
        "needs_key": False,
    },
}
DEFAULT_PROVIDER = "anthropic"

ULDK_URL = "https://uldk.gugik.gov.pl/"
ULDK_LEVELS = {
    "wojewodztwo": ("GetVoivodeshipById", "geom_wkt,teryt,voivodeship"),
    "powiat": ("GetCountyById", "geom_wkt,teryt,county,voivodeship"),
    "gmina": ("GetCommuneById", "geom_wkt,teryt,commune,county,voivodeship"),
    "obreb": ("GetRegionById", "geom_wkt,teryt,region,commune,county,voivodeship"),
    "dzialka": ("GetParcelByIdOrNr", "geom_wkt,teryt,parcel,region,commune,county,voivodeship"),
}

HISTORY_EXCHANGES = 3          # ile par pytanie/odpowiedź pamiętać w rozmowie
MAX_CONTEXT_LAYERS = 60
MAX_CONTEXT_FIELDS = 40
MAX_CONTEXT_CHARS = 16000

EXAMPLE_PROMPTS = [
    N_("Stwórz bufor o promieniu 100 metrów od szkół w powiecie piaseczyńskim"),
    N_("Pobierz granicę gminy Lesznowola i policz jej powierzchnię w hektarach"),
    N_("Dodaj ortofotomapę z Geoportalu jako podkład mapowy"),
    N_("Jakim narzędziem połączę kilka warstw wektorowych w jedną?"),
    N_("Gdzie znajdę narzędzie do agregacji (rozpuszczania) poligonów?"),
    N_("Jak wyszukać działkę ewidencyjną po numerze?"),
]

# Parametry zapytań – dobrane na stałe, bez konfiguracji po stronie użytkownika.
LLM_MAX_TOKENS = 8192          # wystarcza na rozbudowany plan; odpowiedź ucięta jest wykrywana
LLM_TIMEOUT_S = {"anthropic": 180, "openai": 180, "ollama": 300, "codex": 300, "claude_code": 300}
OLLAMA_TEMPERATURE = 0.2       # modele lokalne trzymają format JSON stabilniej przy niskiej temperaturze
