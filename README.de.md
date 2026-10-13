# CinematekAI

[English](README.md) | **Deutsch**

**Was schauen wir heute?** CinematekAI beantwortet die Frage mit den Filmen und Serien, die schon in deiner
Jellyfin-Bibliothek liegen. Es ist eine selbst gehostete App für dich oder deinen ganzen Haushalt: Ein KI-Kurator
wählt einen Film des Tages, du suchst nach Stimmung, zu jedem Film steht, wie gut er zu dir passt, und über deine
Sammlung kannst du dich im Chat unterhalten.

> [!WARNING]
> **Vibe-coded, nicht geprüft.** CinematekAI ist praktisch vollständig mit einem KI-Programmierassistenten
> entstanden („Vibe Coding“). Der Code wurde von keinem erfahrenen Entwickler Zeile für Zeile geprüft. Es gibt keine
> Garantie für Sicherheit oder Fehlerfreiheit - Nutzung auf eigenes Risiko, am besten nur im Heimnetz und nicht
> direkt aus dem Internet erreichbar.

*CinematekAI ist ein unabhängiges Hobbyprojekt und steht in keiner Verbindung zu CINEMATEK, dem Königlichen
Filmarchiv Belgiens.*

## Was CinematekAI anders macht

Viele Empfehlungswerkzeuge für Jellyfin suchen neue Titel, die sich anfragen oder herunterladen lassen. CinematekAI
geht vom anderen Ende aus: von dem, was schon auf deinem Server liegt.

- **Vier Wege zum passenden Film, alle aus deiner eigenen Bibliothek.** Film des Tages, Stimmungssuche, Passung in
  Prozent und Chat arbeiten mit den Titeln, die schon in deiner Jellyfin-Bibliothek liegen. Nichts wird
  automatisch angefragt oder heruntergeladen, kein Film landet ungefragt auf deiner Platte.
- **Eingerichtet im Browser.** Ein Container, Datenbank und Vektorindex sind eingebaut: kein eigener
  Datenbankserver, keine Konfigurationsdatei, keine `.env`. Den Rest erledigt ein Assistent in fünf Schritten, und
  spätere Änderungen gelten ohne Neustart.
- **Für den ganzen Haushalt.** Jede Person meldet sich mit ihrem eigenen Jellyfin-Konto an und wählt Deutsch oder
  Englisch. Alle sehen nur, was ihr Konto sehen darf: Bibliotheksfreigaben, Altersgrenze und gesperrte Tags gelten
  für Listen, Suche, Empfehlungen, Chat, Poster und Trailer.
- **Rückfallmodell und KI-Protokoll.** Fällt das Hauptmodell aus oder hängt es, kann ein optionales Rückfallmodell
  einspringen. Das KI-Protokoll hält jeden Aufruf eines Sprachmodells 180 Tage lang fest und zeigt Admins je
  Aufgabe und Modell die Erfolgsquote, die Antwortzeiten und wie oft der Rückfall nötig war.
- **Datenschutz eingebaut.** Chat und Embeddings werden getrennt eingestellt und können lokal, in der Cloud oder
  gemischt laufen; mit lokalen Modellen geht nichts aus deiner Bibliothek an einen KI-Dienst in der Cloud. Keine
  Telemetrie, die Schriften werden mitgeliefert, und API-Schlüssel landen nie im Browser.
- **Läuft neben Jellyfin, nicht darin.** CinematekAI ist eine eigene App, kein Plugin, und spricht über die API mit
  Jellyfin. Es läuft mit Jellyfin 12, das API-Schlüssel nur noch in der Kopfzeile `Authorization` annimmt.

## Funktionen

- **Film des Tages** - für jede Person wählt ein KI-Kurator einen Film aus deiner Bibliothek und begründet die
  Wahl. Ohne passendes Sprachmodell wird mechanisch ausgewählt.
- **Suche nach Stimmung** - beschreibe, worauf du Lust hast („ein lustiger Tierfilm für Kinder“), und bekomme
  passende Titel. Genres und Anfragen für Kinder werden im Text erkannt; für Kinder fallen Titel mit zu hoher
  Altersfreigabe weg. Dafür reicht das Einbettungsmodell, ein Sprachmodell braucht es nicht.
- **Passung in Prozent** - zu jedem Film steht, wie gut er zu dir passt, abgeleitet aus dem, was du gesehen und
  bewertet hast.
- **Chat über deine Sammlung** - bitte um Vorschläge; die Antwort kommt aus den Filmen und Serien, die du wirklich
  hast. Der Chat kennt Laufzeiten und Tonspuren („unter 90 Minuten, auf Deutsch“) und antwortet auf „Würdest du mir
  … empfehlen?“ mit einem klaren Ja, Eher nicht oder Nein.
- **Mehr auf der Startseite** - Weiterschauen, Serie der Woche, „Wie viel Zeit hast du?“ (ungesehene Filme, die in
  deinen Abend passen), „Raus aus der Blase“ (Genres, die sonst selten bei dir vorkommen), „Weiter oder weg?“ für
  Serien, bei denen du hängen geblieben bist, und eine Merkliste.
- **Filme und Serien** mit Favoriten und Bewertungen, die auch in Jellyfin landen.

### Optional: TMDB

Mit einem kostenlosen [TMDB](https://www.themoviedb.org)-Token zeigt die Startseite außerdem **Fehlt in deiner
Sammlung**: gute Filme, die du noch nicht hast, aus den TMDB-Listen „Wer das mochte, mochte auch“ zu den Titeln, die
du gesehen hast. Ist eine Adresse von [Seerr](https://github.com/seerr-team/seerr) (früher Jellyseerr) eingetragen,
führt jeder Titel direkt zu seiner Seite in Seerr, wo du ihn selbst anfragen kannst - automatisch angefragt wird
nichts.

## Voraussetzungen

- Ein **Jellyfin**-Server und ein Konto mit Administratorrechten (wird einmal für die Einrichtung gebraucht).
- Ein **Sprachmodell-Anbieter** für die KI-Funktionen (Chat, Kurator, Begründungen). Ohne ihn bleibt die
  Tagesauswahl rein mechanisch.
- Ein **Einbettungsmodell** (Embeddings) für Suche und Passung, zum Beispiel lokal mit
  [Ollama](https://ollama.com) und `bge-m3`:

```bash
ollama pull bge-m3
```

Optional: ein kostenloser [TMDB](https://www.themoviedb.org)-Zugriffstoken (API Read Access Token; nötig für „Fehlt in
deiner Sammlung“) und eine Seerr-Adresse.

## Schnellstart

Mit `docker run`:

```bash
docker run -d --name cinematekai --restart unless-stopped -p 8095:8095 -v ./data:/app/data ghcr.io/skaski04/cinematekai:latest
```

Oder mit Compose - dieses Repository klonen oder herunterladen und im Ordner mit der `docker-compose.yml`:

```bash
docker compose up -d
```

Danach `http://<dein-server>:8095` öffnen. Beim ersten Aufruf startet der Einrichtungsassistent.

- Die Zeitzone der täglichen Läufe legst du mit `-e TZ=Europe/Berlin` fest (bei Compose: `TZ` in der Datei).
  Ohne Angabe gilt `Europe/Berlin`; der Assistent schlägt außerdem die Zone deines Browsers vor.
- **Lokale Trailer (optional):** den Medienordner nur lesend unter dem Pfad einhängen, unter dem Jellyfin ihn sieht,
  zum Beispiel `-v /pfad/zu/medien:/media:ro`, und, falls dieser Pfad nicht `/media` ist, unter *Admin ->
  Einstellungen* als „Medienpfad im Container“ eintragen.

## Einrichtung im Browser

Der Assistent hat fünf Schritte. Alles lässt sich später unter **Admin -> Einstellungen** ändern, ohne Neustart.

1. **Jellyfin** - Adresse eintragen (so, wie der Container sie erreicht, siehe unten) und mit einem
   Jellyfin-Administrator anmelden. CinematekAI legt in Jellyfin selbst einen API-Schlüssel an; dein Passwort wird
   nicht gespeichert. Wer einrichtet, wird der erste Admin von CinematekAI; weitere Jellyfin-Nutzer setzt ein Admin
   auf eine Zulassungsliste.
2. **Sprachmodell** - für Chat, Kurator und Begründungen. Optional.
3. **Embeddings** - für Suche und Passung. Ohne sie gibt es keine Suche nach Stimmung.
4. **Extras** - alles freiwillig: Uhrzeit und Zeitzone des nächtlichen Laufs und ein Rückfallmodell, das
   einspringt, wenn das Hauptmodell ausfällt.
5. **Bibliothek einlesen** - lässt die ganze Kette einmal laufen (abgleichen, einbetten, Tagesauswahl). Mit einem
   lokalen Einbettungsmodell auf der CPU kann das bei einer großen Bibliothek Stunden dauern; der Fortschritt
   wird angezeigt.

Ebenfalls in Schritt 4 trägst du den TMDB-Token und die Seerr-Adresse ein (siehe [Optional: TMDB](#optional-tmdb)).

## Unterstützte Anbieter

Chat und Embeddings werden getrennt eingestellt und lassen sich daher mischen (zum Beispiel ein Chatmodell aus der
Cloud mit lokalen Embeddings).

| Anbieter | Chat | Embeddings | Hinweis |
|---|:-:|:-:|---|
| OpenAI | ja | ja | z. B. `text-embedding-3-small` |
| Anthropic (Claude) | ja | - | kein eigener Einbettungsdienst; dafür z. B. Voyage AI nehmen |
| Google Gemini | ja | ja | Schlüssel aus Google AI Studio; z. B. `gemini-embedding-001` |
| Mistral AI | ja | ja | `mistral-embed` |
| DeepSeek | ja | - | |
| OpenRouter | ja | - | viele Modelle unter einem Schlüssel |
| Groq | ja | - | |
| Together AI | ja | ja | |
| Fireworks AI | ja | ja | |
| xAI (Grok) | ja | - | |
| Cerebras | ja | - | |
| Cohere | ja | ja | z. B. `embed-multilingual-v3.0` |
| ArliAI | ja | - | der Core-Tarif erlaubt nur eine Anfrage gleichzeitig |
| NVIDIA | ja | - | |
| Voyage AI | - | ja | z. B. `voyage-3.5` |
| Jina AI | - | ja | z. B. `jina-embeddings-v3` |
| Azure OpenAI | ja | ja | Adresse `https://<ressource>.openai.azure.com/openai/v1`, Modell = Name der Bereitstellung |
| Ollama | ja | ja | wird über die eigene API angesprochen, damit sich das Kontextfenster setzen lässt |
| LM Studio | ja | ja | |
| Andere (OpenAI-kompatibel) | ja | ja | vLLM, LocalAI, llama.cpp, LiteLLM und alles, was `/chat/completions` spricht |

**Kontextfenster.** Der KI-Kurator und der vollständige Bibliothekskatalog im Chat brauchen ein Hauptmodell mit
einem Kontextfenster von mindestens 16.000 Tokens (Feld „Kontextfenster (Tokens)“ im Anbieterformular). Bei einem
kleineren Fenster wird der Kurator übersprungen, die Tagesauswahl bleibt mechanisch, und der Chat arbeitet ohne den
Katalog.

## Gut zu wissen

- **`localhost` im Container ist der Container selbst.** Laufen Jellyfin, Ollama oder LM Studio auf dem Rechner
  daneben oder auf einem anderen, die Netzwerkadresse dieses Rechners eintragen (zum Beispiel
  `http://<host>:11434`), nicht `localhost`.
- **Cloud-Embeddings schicken Daten an den Anbieter.** Titel, Handlung, Besetzung und Genres jedes Films deiner
  Bibliothek gehen dorthin. Cloud-Sprachmodelle sehen, was Chat oder Kurator in einen Prompt packen, etwa Titel.
  Ist das nicht akzeptabel, lokale Modelle verwenden.
- **Ein neues Einbettungsmodell bedeutet, dass die ganze Bibliothek neu eingebettet wird.** Die Vektoren des alten
  Modells bleiben liegen, ein Zurückwechseln geht sofort. Die Prozentskala der Passung misst CinematekAI für das
  neue Modell selbst nach (Admin -> Einstellungen -> Embeddings -> „Skala neu messen“).
- **Schlüssel verlassen das Backend nie.** Die Oberfläche zeigt nur, ob ein Schlüssel gesetzt ist, und seine letzten vier
  Zeichen. Wird Anbieter oder Adresse geändert, ohne einen neuen Schlüssel einzutragen, verwirft CinematekAI den
  alten, damit er nicht beim falschen Anbieter landet.
- **Jede Person sieht nur, was ihr Jellyfin-Konto sehen darf.** Listen, Suche, Empfehlungen, Chat, Poster und
  Trailer richten sich nach den Bibliotheksfreigaben, der Altersgrenze (Jugendschutz) und den gesperrten Tags des
  Kontos in Jellyfin. Diese Rechte liest CinematekAI bei jedem Abgleich (Anmeldung, täglicher Lauf, „Sync“);
  direkt nach dem Update von 1.0.0 gilt bis zum ersten Abgleich dieser Person noch kein Filter.
- **Jellyfin wird gesammelt gelesen, nicht ständig abgefragt.** Bibliothek und Sehstand aller Personen liest
  CinematekAI im täglichen Lauf; ein Admin kann mit „Sync“ einen Abgleich der Bibliothek starten, und den Sehstand
  einer Person liest es zusätzlich bei ihrer Anmeldung. Dazwischen arbeiten Listen, Suche, Empfehlungen und Chat mit
  der eigenen Kopie - was du gerade in Jellyfin geschaut hast, erscheint also nach dem nächsten Abgleich. Poster,
  Folgenlisten und Trailer holt CinematekAI beim Öffnen aus Jellyfin, und Favoriten und Bewertungen, die du setzt,
  schreibt es dorthin zurück.

## Daten und Sicherung

Alles, was die App behält - Datenbank, Vektorindex, Einstellungen samt API-Schlüsseln -, liegt in `/app/data`
(in den Beispielen oben `./data`). **Dieser Ordner ist alles, was gesichert werden muss.** Den Container beim
Kopieren anhalten, sonst kann die Kopie inkonsistent sein. Den Ordner vertraulich behandeln: Er enthält deine
API-Schlüssel.

Von TMDB geladene Listen und alle zwischengespeicherten Vorschläge werden spätestens nach 170 Tagen automatisch
gelöscht, denn die Nutzungsbedingungen von TMDB erlauben, seine Daten höchstens sechs Monate zwischenzuspeichern.

## Aktualisieren

Mit Compose:

```bash
docker compose pull && docker compose up -d
```

Mit `docker run`: neues Image holen, alten Container entfernen und mit demselben Befehl wie im Schnellstart neu
starten (deine Daten in `./data` bleiben):

```bash
docker pull ghcr.io/skaski04/cinematekai:latest
```

```bash
docker rm -f cinematekai
```

Deine Daten bleiben erhalten; Änderungen an der Datenbank werden beim Start automatisch eingespielt. Neben `latest`
gibt es feste Tags wie `1`, `1.0` und `1.0.0`, falls du lieber bewusst aktualisierst.

## Selbst bauen

```bash
docker build -t cinematekai .
```

Mit Compose bauen: in der `docker-compose.yml` die Zeile `build: .` einkommentieren und
`docker compose up -d --build` ausführen. Eigene Ergänzungen zur Compose-Datei (Pfade, Ports, Grenzen) kommen in
eine `docker-compose.override.yml` daneben, die Compose automatisch mitlädt.

## Entwickeln

Backend (Python 3.12), im Hauptordner des Repositorys:

```bash
pip install -r backend/requirements.txt
```

```bash
cd backend && CINEMATEKAI_DATA=../data CINEMATEKAI_STATIC=../frontend/build uvicorn server:app --port 8095
```

Frontend (Node 22 oder neuer, SvelteKit):

```bash
cd frontend && npm install && npm run dev
```

Der Vite-Entwicklungsserver (Port 5173) leitet `/api` an `http://localhost:8095` weiter; ein anderes Ziel
steht in `BACKEND_URL`. `npm run build` erzeugt die statische Oberfläche in `frontend/build`, die das Backend
ausliefert, wenn `CINEMATEKAI_STATIC` darauf zeigt.

| Umgebungsvariable | Bedeutung | Standard |
|---|---|---|
| `CINEMATEKAI_DATA` | Ordner für Datenbank, Vektoren und Einstellungen | `/app/data` |
| `CINEMATEKAI_STATIC` | gebaute Oberfläche, die das Backend ausliefert | `/app/static` |
| `CINEMATEKAI_CORS_REGEX` | Muster für erlaubte fremde Ursprünge; nur nötig, wenn die Oberfläche unter einer anderen Adresse läuft als die API | aus (nicht nötig: Oberfläche und API haben dieselbe Adresse) |
| `CINEMATEKAI_API_DOCS` | `1` schaltet die automatische API-Dokumentation unter `/api/docs`, `/api/redoc` und `/api/openapi.json` ein | aus |
| `TZ` | Standard-Zeitzone der täglichen Läufe | `Europe/Berlin` |

## Ports und Endpunkte

| Was | Wo |
|---|---|
| Oberfläche und API | Port `8095` (die API unter `/api/...`) |
| Gesundheitsprüfung | `/health` liefert `{"ok": true}` und die laufende `version` (die Prüfung des Containers selbst nutzt `/api/health`) |
| Metriken | `/metrics` im Prometheus-Textformat: nur Zähler und Zustände, keine Titel oder Namen |

`/health` und `/metrics` brauchen keine Anmeldung, damit Überwachungswerkzeuge sie erreichen. Den Port nicht ohne
einen Reverse-Proxy, der den Zugriff beschränkt, ins Internet stellen.

## Drittinhalte

- **Schriften** - *Cormorant Garamond* und *DM Sans* liegen unter `frontend/static/fonts`, damit die Oberfläche keine
  Anfragen an externe Schriftserver stellt. Beide stehen unter der
  [SIL Open Font License 1.1](https://openfontlicense.org); Urheberhinweise und Lizenztext stehen in
  `frontend/static/fonts/OFL-CormorantGaramond.txt` und `frontend/static/fonts/OFL-DMSans.txt`.
- **Filmdaten** - Titel und Poster der Liste „Fehlt in deiner Sammlung“ und die Listen „Wer das mochte, mochte
  auch“, die in die Empfehlungen einfließen, stammen von [TMDB](https://www.themoviedb.org). Dieses Produkt nutzt
  TMDB und die TMDB-APIs, wird aber von TMDB nicht befürwortet, zertifiziert oder anderweitig genehmigt (im
  Original: *This product uses TMDB and the TMDB APIs but is not endorsed, certified, or otherwise approved by
  TMDB.*).
- **TMDB-Logo** - `frontend/static/tmdb/tmdb-logo.svg` ist das unveränderte Logo „Alt short (blue)“ von der TMDB-Seite
  [Logos & Attribution](https://www.themoviedb.org/about/logos-attribution). Das Logo ist eine Marke von TMDB und wird
  nach deren Attributionsregeln verwendet: zusammen mit dem Hinweis oben im Abspann am Seitenende und als
  Quellenangabe neben „Fehlt in deiner Sammlung“, stets kleiner als das CinematekAI-Logo. Es fällt nicht unter die
  Lizenz dieses Projekts.
- **Software** - FastAPI, SQLModel, ChromaDB, APScheduler, SvelteKit und weitere Open-Source-Pakete; die vollständige
  Liste steht in `backend/requirements.txt` und `frontend/package.json`.

## Lizenz

CinematekAI steht unter der [PolyForm Noncommercial License 1.0.0](LICENSE). Rechtlich gilt der englische
Lizenztext in der Datei `LICENSE`; die folgenden Sätze fassen ihn nur zusammen.

Frei nutzbar für den privaten Gebrauch, für Bildung, Forschung und gemeinnützige oder andere nichtkommerzielle
Organisationen. **Kommerzielle Nutzung ist nicht erlaubt.** Wer CinematekAI kommerziell einsetzen möchte, meldet
sich über GitHub.
