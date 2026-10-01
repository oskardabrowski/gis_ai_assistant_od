# PLAN — integracja GIS Assistant AI z Codex i Claude Code

Data: 2026-09-25. Projekt: `C:\Users\oskar\Pulpit\gis_ai_assistant_od`.
Status: plan zapisany przed zmianami implementacji; implementacja obu wariantów i weryfikacja zakończone. Użytkownik wybrał OBA warianty.

## Cel i warunki ukończenia

1. W panelu QGIS można wybrać „Połącz z Codex” albo „Połącz z Claude Code”, wysłać polecenie, otrzymać plan i wykonać go istniejącym przyciskiem WYKONAJ.
2. Codex i Claude Code działające poza QGIS mogą odczytać kontekst otwartego projektu, poznać algorytmy, przedstawić plan do zatwierdzenia i odczytać stan jego wykonania przez wspólny lokalny most MCP.
3. Oficjalny agent odpowiada za własne logowanie i limity. Wtyczka nie przechwytuje tokenów OAuth, nie korzysta z nieudokumentowanych endpointów i nie przełącza się automatycznie na płatne API.
4. Obecne integracje Anthropic API, OpenAI API i Ollama pozostają dostępne. Istniejące ustawienia i klucze są zachowane.
5. Brak zamrażania interfejsu podczas oczekiwania na agenta; anulowanie, timeout, zamknięcie i wyłączenie wtyczki sprzątają uruchomione procesy/połączenia.
6. Testy obejmują protokoły, błędy, anulowanie, konfigurację konta oraz MCP. Wyniki i ograniczenia testów z prawdziwym QGIS/agentami zostaną zapisane w tym pliku.

## Stan wejściowy

- Python/PyQGIS/Qt, bez zewnętrznych bibliotek agentowych.
- `LLMClient` ma wspólny interfejs `request`, `abort`, `busy` i sygnały `finished`, `failed`, `progress`.
- `ui/dock.py` zbiera kontekst, odbiera JSON, pokazuje plan i deleguje wykonanie do `PlanExecutor`.
- W repozytorium nie ma MCP ani integracji procesowej z agentami.
- W systemie wykryto Claude Code 2.1.119 oraz Codex CLI 0.153.4. Dostępne opcje sprawdzono przez lokalne `--help`.
- Testy bazowe zakładają nazwę pakietu `gis_assistant_ai`; poprawimy bootstrap testów, żeby działały również w katalogu tej kopii.

## Architektura

### A. Agent w panelu QGIS

- Dwa nowe typy dostawców używające oficjalnych lokalnych CLI w trybie programowym.
- Asynchroniczny `QProcess` i zdarzenia Qt. Bez `shell=True`, poleceń sklejanych z promptem i blokujących oczekiwań w GUI.
- Prompt i oczyszczony kontekst przekazywane przez stdin. Wynik JSON/JSONL parsowany zgodnie z protokołem konkretnego agenta; ukończona odpowiedź trafia do istniejącego planera.
- Claude Code: `claude -p` z JSON/strumieniem, kontem abonamentowym i wyłączonymi narzędziami wykonującymi operacje na komputerze. Nie używamy `--bare`, ponieważ omija logowanie OAuth.
- Codex: oficjalny tryb nieinteraktywny CLI z JSONL, wymuszeniem konta ChatGPT, efemeryczną sesją i sandboxem tylko do odczytu; dostępne opcje potwierdzamy lokalnie. App Server pozostaje alternatywą, jeśli wymagania protokołu uzasadnią jego większy koszt utrzymania.
- Osobny neutralny katalog roboczy procesu; nie uruchamiamy agenta w katalogu danych użytkownika ani z instrukcjami tego repozytorium.
- Usuwamy z otoczenia procesu zmienne wybierające klucze API i alternatywne płatne backendy. Sprawdzamy tryb logowania przed zapytaniem; brak właściwego konta oznacza komunikat, bez cichego fallbacku do API.
- Nie utrzymujemy ukrytej drugiej historii: panel przekazuje swoją bieżącą, ograniczoną historię. Naprawy planu używają wybranego dostawcy.
- Ustawienia: wybór dostawcy, opcjonalny model, ścieżka programu, instrukcja logowania i test połączenia. W tych trybach pola klucza API i adresu API są ukryte.

### B. Zewnętrzny agent steruje QGIS przez MCP

- Jedno narzędzie pomocnicze MCP przez stdio, oparte na standardowej bibliotece Python, wspólne dla obu klientów. Nie importuje PyQGIS poza procesem QGIS.
- Wtyczka udostępnia lokalny, uwierzytelniany most na loopback. Kod dotykający projektu działa w głównym wątku Qt.
- Losowy token sesji i port; plik połączenia w profilu QGIS. Brak tokena w logach i poleceniach konfigurujących agenta. Walidacja rozmiaru, metody i argumentów żądania.
- MCP domyślnie wyłączony; użytkownik włącza go w ustawieniach. Instrukcja konfiguracji obu klientów wskazuje konkretny plik połączenia, również dla kilku profili QGIS.
- Narzędzia: opis projektu, wyszukanie algorytmów, pełna specyfikacja algorytmu, zgłoszenie planu, status planu oraz korekta pozostałych kroków. Zgłoszenie planu NIE uruchamia go automatycznie: użytkownik akceptuje go w QGIS. Korekta również wymaga zatwierdzenia i zachowuje ukończone kroki oraz ich wyniki.
- Identyfikator zgłoszonego planu chroni przed odczytaniem lub zastąpieniem innego planu przez pomyłkę. Zajęty panel odrzuca konkurencyjne zgłoszenia.
- Wyniki statusu nie ujawniają pełnych źródeł danych ani sekretów. Pominięcie i wykonanie częściowe muszą być rozróżniane od pełnego sukcesu.
- Integracja MCP działa niezależnie od wybranego dostawcy panelu i nie wymaga w nim klucza API. Nie modyfikujemy automatycznie globalnych konfiguracji agentów.

## Zakres zmian

- `constants.py`, `settings.py`, `ui/settings_dialog.py`: nowe opcje i ustawienia.
- Osobny moduł protokołów/uruchamiania CLI, testowalny bez QGIS, oraz adapter Qt.
- Minimalna delegacja nowych dostawców w `llm_client.py`, zachowująca dotychczasowe wywołania.
- Osobny moduł mostu Qt i samodzielny serwer MCP stdio.
- `ui/dock.py`: cykl życia MCP, przyjmowanie zewnętrznych planów i statusy, zachowanie potwierdzeń wykonania.
- Walidacja wejścia integracji: niepełne JSON-y, brakujące zależności, limity payloadu. Znane wcześniejsze błędy poprawiamy tylko tam, gdzie wpływają na nowy przepływ.
- README/DEVELOPMENT: instalacja CLI, logowanie kontem, opłaty i limity, konfiguracja MCP, test ręczny oraz diagnostyka.

## Etapy

- [x] Rozpoznanie kodu, środowiska, oficjalnych interfejsów i wybór obu wariantów.
- [x] Zapis PLAN.md przed implementacją.
- [x] Implementacja protokołów CLI i testów bez QGIS.
- [x] Integracja procesów z Qt, ustawieniami i panelem.
- [x] Lokalny most QGIS oraz serwer MCP dla obu agentów.
- [x] Walidacja planów i testy regresji związane z integracją.
- [x] Dokumentacja konfiguracji i uruchomienia.
- [x] Testy jednostkowe, test MCP od stdio do mostu, testy Qt/procesów i przegląd diffu.
- [x] Sprawdzenie prawdziwych CLI i dostępnego środowiska QGIS; jawne odnotowanie blokad.

## Scenariusze akceptacyjne

- Każdy z dwóch dostawców: poprawna odpowiedź, brak CLI, brak logowania, konto API zamiast abonamentu, błąd limitu, niepoprawny JSON, timeout, Stop, ponowne zapytanie po Stop.
- Windows: ścieżki ze spacjami, wykrycie plików exe/cmd/npm, brak widocznych dodatkowych konsol.
- MCP: inicjalizacja, lista narzędzi, odczyt projektu, parametry algorytmu, plan oczekujący na akceptację, status po wykonaniu; błędny token, nieznane narzędzie, nadmierny payload, konkurencyjny plan, rozłączenie i wyłączenie wtyczki.
- Regresja: dotychczasowe testy utils/plan_model oraz tryby API/Ollama zachowują format konfiguracji i żądań.
- Test ręczny GIS: tymczasowa warstwa punktowa w układzie metrycznym, bufor 100 m, rezultat dopiero po WYKONAJ. Test poza QGIS wysyła ten sam plan przez MCP.

## Poza zakresem

Pełny autonomiczny GIS, nieograniczone wykonywanie kodu, serwer dostępny z internetu, wspólne konto abonamentowe dla wielu użytkowników, obchodzenie limitów, automatyczne kupowanie kredytów, pełne przepisanie UI i wszystkich wcześniejszych integracji.

## Źródła zweryfikowane 2026-09-25

- https://learn.chatgpt.com/docs/app-server
- https://learn.chatgpt.com/docs/extend/mcp
- https://learn.chatgpt.com/docs/pricing
- https://code.claude.com/docs/en/headless
- https://code.claude.com/docs/en/cli-reference
- https://code.claude.com/docs/en/mcp
- https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan
- https://docs.ollama.com/integrations/claude-code
- https://docs.ollama.com/api/anthropic-compatibility

Abonament nie jest API bez limitu. Oficjalny komunikat Anthropic wstrzymuje zapowiedziane zmiany rozliczeń SDK; nie zakładamy, że warunki pozostaną niezmienne. Wtyczka nie przechowuje haseł ani tokenów logowania agentów.

## Wyniki wykonania

### Wdrożone

- Dostawcy `codex` i `claude_code`, kontrola logowania kontem, strumienie JSONL, asynchroniczny QProcess, timeout i Stop. Wyłączenie kluczy/backendów API w trybach konta; brak fallbacku.
- Ustawienia programu/modelu, test połączenia, niezależne włączenie MCP i okno generujące konfigurację obu klientów. Dotychczasowe ustawienia API/Ollama są zachowane.
- Sześć narzędzi MCP, losowy token i port loopback, ograniczenia payloadu, blokada drugiego mostu w tym samym profilu, sprzątanie przy wyłączeniu.
- Osobna własność i status planu, zgoda przed wykonaniem i korektą, zachowanie poprzednich wyników, odrzucenie konkurencyjnego planu.
- Odrzucenie uciętego JSON-a, duplikatów ID i odwołań do nieistniejących wcześniejszych kroków przed normalizacją. Kontekst przekraczający limit jest odrzucany z komunikatem zamiast obcinania JSON-a. Panel odróżnia częściowe wykonanie od pełnego sukcesu.
- Instrukcja użytkownika w `AGENT_INTEGRATION.md`; procedury testowe w `DEVELOPMENT.md`.

### Dowody

| Weryfikacja | Wynik |
|---|---|
| `python -m pytest -q -p no:cacheprovider` | **83 passed**: stare testy oraz protokoły agentów, autoryzacja, brak fallbacku, stdin, błędy/limity, walidacja planu, MCP i pomocnik stdio. |
| `ruff check .` | **All checks passed**, Ruff 0.16.9, konfiguracja repozytorium. |
| `tests/qgis_smoke.py`, QGIS 3.44.13 / Qt5 | **PASS**: prawdziwy Qt i PyQGIS, procesy testowe obu agentów, anulowanie/restart, timeout, ustawienia, MCP, bufor 100 m, korekta i sprzątanie. |
| `tests/qgis_smoke.py`, QGIS 4.2.1 / Qt6 | **PASS**, ten sam zestaw. |
| Codex CLI 0.153.4 | Potwierdzony tryb konta ChatGPT. Rzeczywiste zapytanie ze skonfigurowanymi argumentami zwróciło `{"type":"answer","text":"OK"}` i przeszło parser produkcyjny. |
| Claude Code 2.1.119 + Ollama 0.34.4, lokalny `qwen3.5:9b` | **PASS**: rzeczywisty Claude Code → lokalna Ollama → stream-json → parser produkcyjny; dokładna odpowiedź `{"type":"answer","text":"LOCAL_OK"}`. |

Test QGIS uruchamia osobny profil bez widocznego okna i używa tymczasowej warstwy punktowej EPSG:2180. Potwierdza brak nowej warstwy przed akceptacją oraz bufor o szerokości obwiedni 200 m po akceptacji planu 100 m. Test stdio uruchamia osobny proces pomocnika MCP, który odpytuje rzeczywistą instancję PyQGIS przez most Qt. Nie zastępuje otwarcia sesji MCP w interaktywnym kliencie przez użytkownika.

Test anulowania wykrył błąd przy Stop w stanie QProcess.Starting: proces nie miał jeszcze PID. Adapter teraz anuluje go także po sygnale started, ignoruje spóźnione sygnały poprzedniej generacji i usuwa katalog po zakończeniu procesu.

### Zakres potwierdzenia i ograniczenia

- Użytkownik potwierdził brak abonamentu Claude. Na jego życzenie zastosowano lokalną Ollamę jako backend testowy. To potwierdza działanie rzeczywistego CLI i formatu odpowiedzi, **nie** logowanie OAuth Claude ani usługę modeli Anthropic.
- Test z Ollamą jest odseparowany w `tests/claude_ollama_smoke.py`. Nie zmienia konfiguracji globalnej, nie pobiera modeli i nie osłabia sprawdzania logowania w dostawcy abonamentowym panelu.
- Testy Qt używają kontrolowanych procesów agentów; realne zapytania CLI sprawdzono osobno. Nie mierzono jakości złożonych analiz GIS ani wydajności dla dużych projektów.
- Nie wykonano rzeczywistych żądań do płatnych API Anthropic/OpenAI w starych trybach. Ich formaty żądań pozostają niezmienione, a ustawienia są objęte testem Qt.
- Nie zmieniono globalnych konfiguracji Codex/Claude ani nie podmieniono wtyczki w profilu użytkownika. Użytkownik instaluje tę kopię i rejestruje MCP według instrukcji; nie wymaga to przekazywania nam poświadczeń.
- Zmiany warunków kont, wersji CLI i protokołów mogą wymagać aktualizacji adapterów. Zweryfikowane wersje podano powyżej.

## Poprawka listy modeli — 2026-09-25

- Zgłoszenie: lista Codex miała jeden pusty wpis, a etykieta „Lista modeli” otwierała tylko dokumentację. Test Qt odtworzył błąd przed zmianą kodu.
- Poprawka: automatyczny odczyt `model/list` przez oficjalny Codex app-server, bez generowania odpowiedzi modelu. Wspólny adapter Qt zachowuje kontrolę logowania, timeout i anulowanie. Uwzględniono stronicowanie i ukryte modele.
- UI: jawny wybór „Domyślny model agenta”, przycisk Odśwież, stan pobierania/błędu, link Dokumentacja. Ręczny model i zapisany wybór nie giną podczas odświeżania; do CLI trafia identyfikator, nie etykieta.
- Weryfikacja: 93 testy jednostkowe; test selektora na Qt5 i Qt6 obejmuje zapis, ponowne otwarcie, ręczne wpisywanie, zmianę dostawcy, brak logowania, timeout i zamknięcie okna. Rzeczywisty Codex CLI zwrócił sześć modeli konta przez ten sam adapter Qt.
