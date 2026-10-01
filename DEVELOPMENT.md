# GIS Assistant AI – dokumentacja techniczna

Panel z linią poleceń, w której opisujesz językiem naturalnym, co chcesz zrobić w QGIS.
Wtyczka odpowiada na dwa sposoby:

* **Narzędzie** – przy prostych pytaniach („jak połączyć obiekty w jeden?”) wskazuje algorytm
  lub narzędzie wraz ze ścieżką w menu (np. *Wektor › Narzędzia geoprocesingu › Agreguj*),
  przyciskiem „Otwórz narzędzie” albo linkiem do wtyczki w repozytorium
  (np. <https://plugins.qgis.org/plugins/pobieracz_danych_gugik/>).
* **Plan działań** – przy zadaniach („Stwórz bufor 100 m od szkół w powiecie piaseczyńskim”)
  przygotowuje ponumerowany plan krok po kroku. Po jego wyświetleniu odblokowuje się przycisk
  **WYKONAJ**, który realizuje plan automatycznie.

Jeżeli krok wymaga Twojej akcji (podłączenie usługi WMS, pobranie paczki BDOT10k, wybór pliku),
plan zatrzymuje się i wyświetla komunikat. Po kliknięciu **„Wykonałem – kontynuuj”** (i ewentualnym
wskazaniu wczytanej warstwy) AI dopasowuje dalsze kroki do rzeczywistej struktury danych
i wykonanie jest kontynuowane. Gdy krok się nie powiedzie, możesz poprosić AI o naprawę,
ponowić, pominąć krok lub przerwać plan.

## Instalacja

1. QGIS: *Wtyczki › Zarządzaj wtyczkami › Zainstaluj z pliku ZIP* → wskaż `GIS_Assistant_AI.zip`.
   Jeśli masz starszą wersję (katalog `asystent_ai` lub `gis_assistant`), najpierw ją odinstaluj – ustawienia i klucz API zostaną zachowane.
2. Ponieważ wtyczka jest oznaczona jako eksperymentalna, w zakładce *Ustawienia* menedżera wtyczek
   można włączyć „Pokaż także wtyczki eksperymentalne” (niepotrzebne przy instalacji z ZIP).
3. Panel otworzysz z paska/menu **EnviroSolutions › GIS Assistant AI** lub skrótem **Ctrl+Alt+A**.

Wymagania: QGIS 3.34 – 4.x (Qt5 i Qt6, `supportsQt6=True`), dostęp do internetu
(lub lokalny serwer Ollama).

## Język

Wtyczka przejmuje język z ustawień QGIS (*Ustawienia › Opcje › Ogólne › Język*); nie korzysta z adresu IP
ani żadnej usługi sieciowej. Dostępne są: polski (tekst źródłowy) i angielski. Dla pozostałych języków
interfejs wtyczki jest po angielsku, ale asystent AI i tak odpowiada w języku QGIS, a ścieżki menu
podaje tak, jak wyglądają w tej wersji językowej. Zmiana języka wymaga ponownego uruchomienia QGIS.

Nowe tłumaczenie: skopiuj `i18n/gis_assistant_ai_en.ts` jako `gis_assistant_ai_<kod>.ts` (np. `de`),
ustaw atrybut `language`, przetłumacz w Qt Linguist i skompiluj poleceniem
`lrelease i18n/gis_assistant_ai_<kod>.ts` – wtyczka sama wczyta plik `.qm`.

## Konfiguracja modelu

Przycisk ustawień (ikona klucza) w nagłówku panelu:

| Dostawca | Uwagi |
|---|---|
| Anthropic Claude | domyślny; klucz API z console.anthropic.com |
| OpenAI / API zgodne z OpenAI | możliwa zmiana adresu (np. Azure, serwer firmowy) |
| Ollama | model lokalny, **żadne dane nie opuszczają komputera**, brak klucza |

Odpowiedzi modelu są odbierane strumieniowo – w linii statusu widać postęp, a długie odpowiedzi nie są
przerywane przez limit czasu sieci QGIS. Jeśli mimo to połączenie zostanie przerwane, zwiększ
*Ustawienia › Opcje › Sieć › Limit czasu*.

Jeśli klucz Anthropic nie jest przypisany do workspace (błąd HTTP 400 „not scoped to a workspace”),
wpisz w polu **ID workspace** identyfikator w formacie `wrkspc_…` z Claude Console albo utwórz
klucz w konkretnym workspace. ID workspace nie jest sekretem – zapisywane jest w ustawieniach QGIS.

Parametry zapytań (długość odpowiedzi, limit czasu) są dobrane automatycznie. Przycisk „Testuj połączenie” sprawdza konfigurację.

## Bezpieczeństwo i prywatność

* **Klucz API** jest zapisywany wyłącznie w zaszyfrowanej bazie menedżera uwierzytelniania QGIS
  (hasło główne) albo – jeśli odznaczysz zapamiętywanie – trzymany tylko w pamięci do zamknięcia
  QGIS (lub wyłączenia wtyczki). Nigdy nie trafia do pliku ustawień ani do dziennika (w komunikatach jest maskowany).
  Gdy adres API używa nieszyfrowanego `http://` (poza `localhost`), okno ustawień ostrzega i prosi o potwierdzenie.
* **Do modelu nie są wysyłane sekrety**: ze źródeł warstw usuwane są hasła, loginy, tokeny,
  klucze API, identyfikatory `authcfg` i dane logowania w adresach URL, a pełne ścieżki dysków
  są skracane do nazwy pliku. Warstwy niedostępne (np. baza bez połączenia) nie są odpytywane.
* Wysyłany jest tylko opis projektu: nazwy i typy warstw, pola, układy współrzędnych, zasięg mapy,
  lista dostawców Processing i zainstalowanych wtyczek. **Wartości atrybutów** są wysyłane
  tylko po włączeniu opcji „Wysyłaj przykładowe wartości atrybutów”.
* **Treść od modelu traktowana jest jako niezaufana.** Nazwy warstw, pól czy wartości atrybutów w cudzym
  projekcie mogą zawierać ukryte polecenia dla AI (tzw. prompt injection). Dlatego każde działanie
  o skutkach poza projektem QGIS wymaga Twojej zgody albo jest zablokowane:
  * kod PyQGIS jest **domyślnie wyłączony**; po włączeniu każdy kod jest pokazywany w całości przed
    uruchomieniem, z ostrzeżeniem o konstrukcjach wysokiego ryzyka (pliki, procesy, sieć, ustawienia
    uwierzytelniania), a przyciskiem domyślnym jest „Pomiń”;
  * wczytanie pliku z dysku lub bazy danych wskazanych przez model (a także ścieżek GDAL `/vsi…`) wymaga
    potwierdzenia; usługi sieciowe (WMS/WMTS/WFS/XYZ…) wczytywane są bez pytania;
  * otwierane są wyłącznie adresy `http(s)://` – linki `file:`, `smb:`, `ms-settings:` itp. są blokowane;
  * pola stanu kroków (np. „wykonany”) nie mogą zostać ustawione przez model.
* Wyniki algorytmów trafiają domyślnie do warstw tymczasowych – zapisz je, jeśli chcesz je zachować.

## Typy kroków planu

| Akcja | Działanie |
|---|---|
| `processing` | uruchomienie algorytmu Processing (w tle), wynik dodawany do projektu |
| `load_layer` | wczytanie warstwy WMS/WMTS, WFS, XYZ lub pliku |
| `uldk_boundary` | granica gminy/powiatu/województwa z usługi ULDK GUGiK (EPSG:2180) |
| `user_action` | komunikat dla użytkownika i oczekiwanie na potwierdzenie (opcjonalnie wskazanie warstwy) |
| `processing_dialog` | otwarcie okna algorytmu z wypełnionymi parametrami |
| `zoom` | przybliżenie mapy do warstwy |
| `python` | krótki skrypt PyQGIS (po potwierdzeniu) |
| `note` | informacja bez działania |

Kroki odwołują się do wyników poprzednich zapisem `{{s1}}` / `{{s1.OUTPUT}}`. Identyfikatory
algorytmów są sprawdzane w rejestrze Processing i automatycznie poprawiane (np. `qgis:buffer` →
`native:buffer`).

## Budowa

```
gis_assistant_ai/
├── plugin.py            – rejestracja w pasku i menu „EnviroSolutions”, panel
├── ui/dock.py           – panel: polecenie, wynik, WYKONAJ, akcje użytkownika, błędy, dziennik
├── ui/render.py         – widok HTML planu/narzędzi
├── ui/styles.py         – paleta z księgi znaku, jasny i ciemny motyw
├── ui/widgets.py        – zwijane sekcje (Polecenie, Wynik, Dziennik), ui/icons.py – ikony SVG
├── ui/settings_dialog.py
├── ui/confirm_dialog.py – zatwierdzanie kodu PyQGIS i wczytania lokalnych danych
├── llm_client.py        – asynchroniczne zapytania (QgsNetworkAccessManager, obsługa proxy QGIS)
├── prompts.py           – instrukcja systemowa i format odpowiedzi JSON
├── context_builder.py   – opis projektu (z usuwaniem sekretów)
├── plan_model.py        – parsowanie i walidacja odpowiedzi
├── executor.py          – wykonywanie planu krok po kroku
├── settings.py          – ustawienia i bezpieczne przechowywanie klucza
├── i18n.py, i18n/       – wybór języka wg QGIS i pliki tłumaczeń (.ts/.qm)
├── tests/               – testy jednostkowe (pytest), ruff.toml – standard kodu
└── utils.py
```

## Rozwój

### Integracja agentów i MCP

- `agent_protocol.py`: rozwiązywanie CLI bez powłoki, środowisko konta, kontrola logowania, protokoły JSONL.
- `agent_client.py`: procesy Qt, stdin, limity odpowiedzi, timeout, anulowanie także w stanie Starting.
- `mcp_server.py`: samodzielny serwer stdio i klient loopback (standardowa biblioteka Pythona).
- `qgis_bridge.py`: uwierzytelniany transport Qt; dostęp do projektu wyłącznie z głównego wątku.
- `external_plans.py`: własność planu, status, zatwierdzanie i korekty z zachowaniem ukończonych kroków.
- `integration_validation.py`: walidacja przed normalizacją, aby nie naprawiać po cichu duplikatów ID.

Testy bez QGIS, niezależne od nazwy folderu tej kopii:

```powershell
python -m pytest -q -p no:cacheprovider
```

Testy rzeczywistego PyQGIS/Qt (osobny profil, offscreen, dane tymczasowe; bez zapytań do modeli):

```powershell
& 'C:\Program Files\QGIS 3.44.13\bin\python-qgis-ltr.bat' -u tests/qgis_smoke.py 'C:\Program Files\QGIS 3.44.13'
& 'C:\Program Files\QGIS 4.2.1\bin\python-qgis.bat' -u tests/qgis_smoke.py 'C:\Program Files\QGIS 4.2.1'
```

Test sprawdza oba formaty agentów przez prawdziwy QProcess, anulowanie/restart, timeout, ustawienia, pełną ścieżkę stdio→MCP→Qt→QGIS, bufor 100 m, zgodę przed wykonaniem i korektą, zachowanie wyników, błędny token, blokadę drugiej instancji oraz sprzątanie mostu. Procesy agentów są kontrolowanymi atrapami (`tests/fake_agent.py`); nie jest to dowód poprawnego logowania do usługi. Test online uruchamia się osobno po potwierdzeniu logowania kontem.

Opcjonalny test prawdziwego Claude Code bez abonamentu: `python tests/claude_ollama_smoke.py --model qwen3.5:9b`. Używa lokalnej Ollamy i istniejącego modelu; zastępuje backend wyłącznie w procesie testowym. Nie jest dowodem działania OAuth ani modeli Anthropic.

Nie uruchamiaj agenta w katalogu danych, nie kopiuj credential store do profilu QGIS, nie dodawaj fallbacku do API i nie dopisuj narzędzia MCP wykonującego plan bez zgody. Serwer stdio nie powinien importować PyQGIS ani wypisywać diagnostyki na stdout.

* Standard kodu: `ruff check .` (konfiguracja w `ruff.toml`, długość linii 120).
* Testy jednostkowe modułów niezależnych od QGIS (`utils`, `plan_model`): w katalogu nadrzędnym
  wtyczki uruchom `pytest gis_assistant_ai/tests`.
* Tłumaczenia: `i18n/gis_assistant_ai_en.ts` (Qt Linguist) → `lrelease`.

## Do uzupełnienia przed publikacją

* `metadata.txt` – adresy `tracker` i `repository` są tymczasowe (repozytorium jeszcze nie istnieje).
* Logo EnviroSolutions w środku stopki: `img/es_logo.png` (ścieżka `FOOTER_LOGO_PATH` w `constants.py`).
* Lista modeli w `constants.py` – zaktualizuj według aktualnej oferty dostawców.

## Licencja

GNU GPL v3 – © 2026 EnviroSolutions Sp. z o.o.

---
Instrukcja dla użytkowników: [README.md](README.md).
