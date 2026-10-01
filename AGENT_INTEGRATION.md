# Codex i Claude Code — integracja z QGIS

Dostępne są dwa niezależne warianty: rozmowa w panelu QGIS oraz rozmowa w zewnętrznym agencie, który łączy się z QGIS przez MCP. Można korzystać z obu. MCP nie wymaga ustawienia dostawcy ani klucza API w panelu.

## Rozmowa w panelu QGIS

1. Zainstaluj oficjalny [Codex CLI](https://learn.chatgpt.com/docs/cli) lub [Claude Code](https://code.claude.com/docs/en/setup). Wymagany jest plan/konto z dostępem do wybranego agenta.
2. W zwykłym terminalu zaloguj się kontem, którego planu chcesz używać:

   ```powershell
   codex login
   codex login status
   # lub
   claude auth login
   claude auth status
   ```

3. Otwórz ustawienia wtyczki i wybierz **Połącz z Codex** albo **Połącz z Claude Code**. Wybierz **Domyślny model agenta** lub konkretny model. Dla Codex lista jest pobierana w tle z zalogowanego CLI; przycisk **Odśwież** pobiera ją ponownie. Nadal możesz wpisać identyfikator ręcznie. Link **Dokumentacja** otwiera instrukcję dostawcy.
4. Program jest wykrywany automatycznie. Jeśli QGIS go nie znajduje, wskaż pełną ścieżkę w polu **Program CLI**. Obsługiwane są natywne programy oraz standardowe launchery npm. Dla Codex na Windows launcher npm jest rozwiązywany do natywnego `codex.exe`.
5. Kliknij **Testuj połączenie**, zapisz ustawienia, opisz zadanie. Sprawdź plan i kliknij **WYKONAJ**.

Test oraz zwykłe zapytania zużywają limity konta. To nie jest darmowe API ani sposób obchodzenia limitów. Wtyczka w tych dwóch trybach nie używa klucza API i nie przełącza się na API po wyczerpaniu limitu. Własne opcje rozliczeń konta, w tym wykupione kredyty, pozostają pod kontrolą dostawcy. Dotychczasowe pozycje **Anthropic Claude**, **OpenAI** i **Ollama** nadal działają oddzielnie.

Wtyczka sprawdza tryb logowania przed każdym zapytaniem. Nie kopiuje tokenów agenta, nie otwiera logowania w tle i nie modyfikuje jego globalnej konfiguracji. CLI używa neutralnego katalogu tymczasowego, a polecenie i kontekst dostaje przez stdin. Historia pochodzi z panelu. Polecenie **Zatrzymaj** anuluje oczekiwanie; limit czasu wynosi pięć minut.

Pobranie listy modeli Codex odczytuje metadane przez `app-server` / `model/list`; nie generuje odpowiedzi modelu i nie wysyła danych projektu. Przy braku logowania, błędzie lub timeout pojawia się komunikat, a dotychczasowy wybór pozostaje zachowany. Lista zależy od wersji CLI i konta. Claude Code nadal udostępnia aliasy `sonnet`, `opus`, `haiku` oraz wybór domyślny.

CLI korzysta z własnej konfiguracji sieci/certyfikatów i logowania, a nie z menedżera sieci QGIS. Ograniczenia organizacyjne dostawcy mogą nadal obowiązywać. Tryb panelu wyłącza narzędzia i konfiguracje rozszerzeń dostępne przez obsługiwane opcje CLI; Codex dodatkowo używa sandboxa tylko do odczytu. To nie jest izolacja od wszystkich procesów uruchomionych na koncie systemowym.

## Zewnętrzny Codex / Claude Code przez MCP

1. Włącz w ustawieniach wtyczki **Włącz lokalny most MCP dla tego profilu QGIS** i zapisz.
2. Kliknij **Pokaż konfigurację MCP…**. Okno podaje ścieżki odpowiadające faktycznej instalacji wtyczki i profilowi QGIS.
3. Zarejestruj pomocniczy serwer w swoim agencie. Wymaga on zwykłego Pythona 3.10+; nie wymaga PyQGIS, pip ani uruchamiania drugiej instancji QGIS. Jeśli `python` nie jest dostępny w środowisku agenta, użyj pełnej ścieżki do interpretera.

Przykłady PowerShell — zastąp obie ścieżki wartościami z okna konfiguracji:

```powershell
codex mcp add qgis -- python 'C:\sciezka\gis_assistant_ai\mcp_server.py' --connection-file 'C:\profil-QGIS\gis_assistant_ai\mcp-connection.json'
claude mcp add --transport stdio qgis -- python 'C:\sciezka\gis_assistant_ai\mcp_server.py' --connection-file 'C:\profil-QGIS\gis_assistant_ai\mcp-connection.json'
```

Alternatywnie skopiuj JSON MCP dla Claude Code albo sekcję TOML dla Codex z okna ustawień. Nie zastępuj całej istniejącej konfiguracji — dodaj wpis `qgis`. Zrestartuj sesję agenta, jeśli nie widzi nowych narzędzi. W Codex Desktop użyj konfiguracji MCP właściwej dla lokalnego projektu/hosta.

4. Otwórz panel wtyczki i projekt w QGIS. Agentowi podaj np.:

   > Użyj MCP qgis. Odczytaj projekt i parametry native:buffer. Zaproponuj bufor 100 m dla warstwy punktów w układzie metrycznym. Prześlij plan do zatwierdzenia w QGIS. Nie wykonuj nic poza narzędziami QGIS.

5. Sprawdź otrzymany plan w panelu QGIS i kliknij **WYKONAJ**. Potem poproś agenta o sprawdzenie wyniku, używając zwróconego `plan_id`.

| Narzędzie | Działanie |
|---|---|
| `qgis_get_context` | Warstwy, pola, CRS i dostępni dostawcy Processing; próbki atrybutów tylko po włączeniu opcji w QGIS. |
| `qgis_list_algorithms` | Wyszukiwanie zainstalowanych algorytmów, maks. 50 wyników. |
| `qgis_describe_algorithm` | Parametry, wartości enum, opcjonalność i wyjścia konkretnego algorytmu. |
| `qgis_submit_plan` | Pokazuje plan w panelu; nie wykonuje go. |
| `qgis_get_status` | Stan kroków oraz identyfikatory warstw wynikowych. |
| `qgis_revise_plan` | Wymienia pozostałe kroki wstrzymanego planu; wymaga ponownej zgody w panelu. |

Po błędzie lub kroku wymagającym dopasowania danych poproś tego samego agenta o odczyt statusu i korektę. Ukończone kroki oraz ich wyniki zostają zachowane. Kliknij **ZATWIERDŹ KONTYNUACJĘ**. Zewnętrzny plan nie uruchamia naprawy przez dostawcę wybranego w panelu.

Nie odpytuj statusu w ciasnej pętli. `awaiting_approval`, `waiting_user` i `awaiting_revision_approval` oznaczają oczekiwanie na człowieka. `partial` oznacza, że część kroków pominięto; `completed` oznacza wykonanie wszystkich. Nowe polecenie unieważnia poprzedni identyfikator planu. Zajęty panel odrzuca konkurencyjny plan.

Most nasłuchuje wyłącznie na `127.0.0.1`, na losowym porcie. Każde uruchomienie ma nowy token. Plik połączenia zawiera ten token — nie wysyłaj go agentowi w rozmowie ani nie dodawaj do repozytorium. Pomocnik odczytuje go lokalnie. Jest to integracja dla zaufanego konta systemowego, nie serwer wieloużytkownikowy. Zewnętrzny agent zachowuje własne uprawnienia do narzędzi spoza MCP.

Most działa do wyłączenia opcji MCP, wyłączenia wtyczki lub zakończenia QGIS; samo ukrycie panelu go nie wyłącza. Jeden most może działać w jednym profilu QGIS. Dla kilku jednoczesnych instancji użyj osobnych profili i osobnych wpisów MCP. Po restarcie QGIS otwórz panel wtyczki.

## Diagnostyka

| Objaw | Sprawdzenie |
|---|---|
| Nie znaleziono CLI | Uruchom program w terminalu, sprawdź instalację i pole Program CLI; po instalacji zamknij i uruchom QGIS ponownie. |
| Brak logowania abonamentowego | Sprawdź `codex login status` / `claude auth status` w tym samym koncie systemowym. Zalogowanie do aplikacji Desktop nie zawsze oznacza logowanie CLI. |
| Odpowiedź niekompletna / limit | Sprawdź limity konta i aktualność CLI. Wtyczka odrzuca przerwany strumień i nie wykonuje częściowego JSON-a. |
| MCP niedostępne | Otwórz panel, włącz MCP, sprawdź ścieżkę pliku połączenia i interpretera Python. |
| Panel zajęty | Zakończ działający plan albo wybierz Nowe polecenie w QGIS. |
| Algorytm nie istnieje | Odczytaj `qgis_list_algorithms`; zainstaluj wymaganego dostawcę Processing w QGIS. |
| Plan używa pominiętego wyniku | Wyniku nie ma; potrzebna jest korekta planu. Nie traktuj pominięcia jako sukcesu. |

Sprawdzone lokalnie: Codex CLI 0.153.4 (rzeczywiste zapytanie przez konto ChatGPT), Claude Code 2.1.119 (rzeczywisty proces CLI z lokalnym modelem Ollama `qwen3.5:9b`), QGIS 3.44.13 / Qt5 oraz QGIS 4.2.1 / Qt6. Użytkownik nie ma abonamentu Claude, więc nie przetestowano jego OAuth ani modeli hostowanych przez Anthropic. Szczegóły dowodów i ograniczeń: [PLAN.md](PLAN.md).

## Test Claude Code bez abonamentu

Na potrzeby weryfikacji można uruchomić rzeczywisty Claude Code z lokalnym backendem zgodnym z Anthropic API. W repozytorium jest osobny test:

```powershell
python tests/claude_ollama_smoke.py --model qwen3.5:9b
```

Wymaga działającej Ollamy na `127.0.0.1:11434` i już pobranego modelu. Test nie pobiera modeli, odrzuca modele chmurowe i nie zmienia konfiguracji globalnej. Ustawia adres Ollamy i fikcyjne poświadczenie tylko w środowisku procesu testowego, odbiera strumień z prawdziwego Claude Code i sprawdza go parserem wtyczki. Tryb abonamentowy w panelu nadal wymaga logowania `claude.ai`. Do zwykłej pracy z lokalnym modelem dostępny jest dotychczasowy dostawca **Ollama**.

Sposób połączenia opisuje [oficjalna instrukcja Ollama dla Claude Code](https://docs.ollama.com/integrations/claude-code).

Zasady dostawców: [Codex i plany](https://learn.chatgpt.com/docs/pricing), [Claude Code programowo](https://code.claude.com/docs/en/headless), [Claude — używanie planu przez Agent SDK i narzędzia zewnętrzne](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan). Stan zweryfikowany 2026-09-25; dostępność i zasady mogą się zmieniać.
