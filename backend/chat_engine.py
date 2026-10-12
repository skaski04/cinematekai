"""CinematekAI chat engine — RAG over ChromaDB + Sprachmodell-Strom (welches
Modell, entscheidet llm_client nach den Einstellungen), with 24-hour
persistence.

The user can close the tab in the middle of a (slow) generation and come back
later; the assistant reply keeps writing to SQLite in a background thread,
and the next /chat/history call returns the completed text.

Flow per user turn:
  1. Persist the user message and a placeholder assistant row (status='streaming').
  2. Spawn a daemon thread that runs the LLM and appends tokens to the
     assistant row's ``content`` column.
  3. The SSE response tails the DB row and emits deltas to the browser.
  4. If the browser disconnects, the worker keeps going; the final text
     stays in the DB for 24 h (cron prunes older rows).
"""
from __future__ import annotations
import logging, threading, time
from datetime import datetime, timedelta
from typing import Iterator, Optional
from sqlmodel import select

import re
from database import (get_session, Movie, Series, Episode, WatchState,
                      SeriesProgress, ChatMessage)
from embeddings import (search_by_text, normalize_user_query,
                        profile_all_similarities, passung_prozent)
from user_profile import build_profile, profile_summary_text
import llm_client
import texte

log = logging.getLogger("cinematekai.chat")

RAG_EPISODE_K = 5
HISTORY_HOURS = 24


def _rag_top_k() -> int:
    """Wie viele Vektortreffer in den Prompt kommen.

    Mit grossem Kontext (Hauptmodell ab 16K, llm_client.grosser_kontext —
    bisher das Cloud-Modell mit 32K) passen deutlich mehr Treffer in den
    Prompt als bei Hermes3 auf der CPU, wo jeder Kandidat Minuten kostete.
    Eine Funktion statt einer Konstante: das Modell laesst sich jetzt zur
    Laufzeit in den Einstellungen wechseln, ein beim Import berechneter Wert
    bliebe bis zum Neustart falsch.
    """
    return 20 if llm_client.grosser_kontext() else 8


# Hard deadline for one chat turn. Hermes3 8b on CPU with a long-context
# prompt can genuinely take up to 10 min, especially when other models share
# RAM. We keep this loose; the inter-chunk watchdog catches actual hangs.
TURN_DEADLINE_S = 900           # 15 min — generous upper bound
FIRST_TOKEN_DEADLINE_S = 480    # 8 min — if no first token by then, abort
INTER_CHUNK_TIMEOUT_S = 90      # 1.5 min between two tokens = stuck

# Freundliche Meldungen statt "[Fehler: ReadTimeout]". Sie nennen keinen
# Anbieter: welches Modell dahintersteht, ist eine Einstellung, und der
# Nutzer kann daran ohnehin nichts aendern — nur ein Admin.
# Sie landen als Text in der Chat-Zeile und sind damit fuer den Nutzer
# sichtbar. Der Chat laeuft in einem eigenen Faden ohne Anfrage-Sprache
# (texte.py): die Sprache wird beim Start uebergeben und hier ausdruecklich
# angegeben. Gebaut wird erst beim Gebrauch, nicht beim Import.
_T = {
    "de": {
        "kein_modell": (
            "⚠ Für den Chat ist noch kein Sprachmodell eingerichtet. "
            "Ein Admin kann das unter Admin → Einstellungen nachholen."
        ),
        "llm_down": (
            "⚠ Das Sprachmodell ist gerade nicht erreichbar. Versuch es in ein, zwei "
            "Minuten noch einmal — bleibt es dabei, prüft ein Admin die Verbindung "
            "unter Admin → Einstellungen."
        ),
        "llm_timeout": (
            "⚠ Die Antwort hat zu lange gedauert. "
            "Bitte den Chat neu öffnen und es nochmal versuchen."
        ),
        "fehlerzeile": "[Fehler: {fehler}]",
        "zeile_weg": "[Fehler: Chat-Zeile verschwunden]",
    },
    "en": {
        "kein_modell": (
            "⚠ No language model is set up for the chat yet. "
            "An admin can do that under Admin → Settings."
        ),
        "llm_down": (
            "⚠ The language model is unreachable right now. Try again in a minute "
            "or two — if it stays that way, an admin should check the connection "
            "under Admin → Settings."
        ),
        "llm_timeout": (
            "⚠ The answer took too long. "
            "Please reopen the chat and try again."
        ),
        "fehlerzeile": "[Error: {fehler}]",
        "zeile_weg": "[Error: chat row disappeared]",
    },
}


def _t(schluessel: str, sprache: Optional[str] = None, **werte) -> str:
    return texte.waehle(_T, schluessel, sprache, **werte)


# Marke, mit der stream_chat einen Fehler mitten im Text meldet, statt zu
# werfen. Interne Verabredung mit cron.py (Vorlauf der Chat-Vorschlaege); die
# Oberflaeche sieht sie nie und sie bleibt deshalb in jeder Sprache gleich.
FEHLER_MARKE = "[Fehler:"

# Heuristic: when does the user's question feel episode-shaped?
# Triggers an extra episode-RAG retrieval that gets injected into the prompt.
_EPISODE_TRIGGER = re.compile(
    r"\b(folge|episode|staffel|season|s\d+\s*e\d+|s\d+e\d+|pilot)\b",
    re.IGNORECASE,
)


def _is_episode_question(query: str) -> bool:
    return bool(_EPISODE_TRIGGER.search(query or ""))
_POLL_INTERVAL_S = 0.15      # how fast the SSE loop re-reads the DB row

SYSTEM_TEMPLATE = """Du bist CinematekAI, ein persönlicher Film- und Serien-Berater für die Jellyfin-Bibliothek des Nutzers.

{sprachzeile}

AUSGABEFORMAT — DAS WICHTIGSTE:
Du antwortest in ganzen deutschen Sätzen, so wie ein Mensch reden würde.
Der Block zwischen <bibliothek> und </bibliothek> ist reines Nachschlagematerial.
Gib daraus NIEMALS Zeilen wörtlich zurück. Keine Tabellenzeilen, keine Pipe-Zeichen (|),
keine Aufzählung im Format "Art | Titel | Jahr | ...". Formuliere immer selbst.

So sieht eine gute Antwort aus:
"Für einen ruhigen Abend passt „Forrest Gump" (1994) — warmherzig und ohne Hektik.
Falls es kürzer sein soll, nimm „Der Club der toten Dichter" (1989)."

So sieht eine FALSCHE Antwort aus (niemals so):
"Film | Forrest Gump | 1994 | Drama | ungesehen | Die Geschichte von ..."

REGELN:
- Kurz und direkt (max. 4-5 Sätze). Kein Schleim, keine Floskeln.
  Bei Bestandsfragen darf es eine Aufzählung mit Spiegelstrichen sein — ohne Pipe-Zeichen.
- Empfiehl ausschließlich Titel, die unten in <bibliothek> oder <katalog> stehen. Erfinde nichts.
- Fragen nach dem Bestand („welche … habe ich", „noch nicht gesehen", „alle Filme von …")
  beantwortest du mit dem <katalog>, falls er vorhanden ist: Er listet die KOMPLETTE Sammlung
  mit Status. Behaupte nie, ein Titel fehle oder sei gesehen, ohne dort nachzusehen.
- Wenn etwas nicht in der Bibliothek ist, sag das ehrlich.
- Die Spalte "Status" sagt, ob der Nutzer den Titel schon kennt. Fragt er nach etwas
  Ungesehenem, nenne ausschließlich Titel mit Status "ungesehen".
- Bei "was habe ich zuletzt geschaut" → konkret aus ZULETZT GESEHEN aufzählen.
- Bei Empfehlungen: Titel in Anführungszeichen + 1 Satz Begründung pro Eintrag.
- Bei Episoden-Fragen: Staffel/Folge-Nummer immer mitnennen ("S2E4").
- Wird eine Laufzeit verlangt („unter 90 Minuten"), pruefe die Laufzeit-Spalte und
  halte die Grenze strikt ein. Nenne die Laufzeit in der Antwort mit.

EHRLICHE EINSCHÄTZUNG:
- Bei „Würdest du mir X empfehlen?" steht am Anfang deiner Antwort ein klares
  Urteil: Ja / Eher nicht / Nein. Stütze dich dabei auf die Zeile des Titels in
  <bibliothek> — auf das, was dort steht, nicht auf Höflichkeit.
- Ein Nein ist ausdrücklich erwünscht, wenn die Angaben dagegen sprechen: schwache
  Wertung, Genre passt nicht zum Profil, ähnliche Titel hat der Nutzer schlecht
  bewertet, oder bei einer Reihe fehlen die Vorgänger.
- Auch bei einem Ja nennst du den stärksten Einwand — ein Satz.
- Fragt der Nutzer nach einer Sprache („auf Deutsch", „deutscher Ton"), zählt die
  Spalte Tonspuren: nur Titel mit „de" darin kommen infrage. Von 450 Filmen haben
  nur 65 deutschen Ton — findest du nichts Passendes, sag das klar, statt einen
  Film ohne deutsche Tonspur zu empfehlen. „?" heißt: keine Angabe, also nicht sicher.
- Die Spalten sind deine Grundlage, aber sie gehören NICHT in die Antwort. Du redest
  wie ein Mensch, nicht wie ein Datenblatt. Verboten sind deshalb:
  Prozentzahlen jeder Art, Wertungszahlen („6,8", „7,0 ★"), und die internen
  Begriffe „Passung", „Community-Wertung", „Status", „Favorit", „gesehen",
  „ungesehen" — auch in Abwandlungen wie „noch ungesehen", „als Favorit markiert"
  oder „steht bei dir als Favorit".
  Sag stattdessen in normalen Worten, was gemeint ist: „liegt neben deinem
  Geschmack", „schwächer als der erste Teil", „einer deiner Lieblingsfilme",
  „hat dir gefallen", „kennst du noch nicht", „gilt allgemein als schwach".
  Einzige Ausnahme: Der Nutzer fragt ausdrücklich nach einer Zahl (Wertung,
  Laufzeit, Jahr) — dann nenne genau diese.
- Beziehe dich nur auf die Zeile des Titels, nach dem gefragt wurde — bei Reihen
  stehen die Nachbarteile direkt daneben. Fehlt der Titel im Block, sag das offen,
  statt über den nächstbesten Eintrag zu urteilen.
- Keine Werbesprache: kein „unbedingt anschauen", kein „wirst du bestens
  unterhalten", kein „ein absolutes Muss".
- Nicht schmeicheln. Der Nutzer will eine Einschätzung, keine Bestätigung.

NUTZERPROFIL:
{profile}

ZULETZT GESEHEN:
{recent}

<bibliothek>
Treffer zur Frage '{query}' — Spalten: Art | Titel | Jahr | Genres | Status | Community-Wertung (0-10) | Wertung des Nutzers (0-10) | Passung zum Profil | Tonspuren | Kurzinhalt
{candidates}{episodes_block}
</bibliothek>{catalog_block}"""

# Die Sprachzeile des Systemprompts. Der Prompt selbst bleibt deutsch (so ist
# er getestet); fuer eine englische Oberflaeche ersetzt eine zweite Zeile die
# erste, damit der Prompt sich nicht selbst widerspricht — zusaetzlich haengt
# _build_system_prompt am Ende texte.antwortsprache() an. Die verbotenen
# Begriffe sind auf Deutsch aufgezaehlt; das Modell soll sie auch in der
# englischen Antwort meiden, sonst plaudert es "match score" und "unwatched"
# aus dem Datenblatt.
_SPRACHZEILE = {
    "de": "SPRACHE: Antworte IMMER auf Deutsch. Niemals Englisch. Auch wenn Titel englisch "
          "sind — dein Fließtext ist deutsch.",
    "en": "SPRACHE: Antworte IMMER auf Englisch (English). Niemals Deutsch. Auch wenn Titel "
          "deutsch sind — dein Fließtext ist englisch. Die Beispiele unten sind deutsch; "
          "antworte trotzdem englisch. Die verbotenen Begriffe gelten sinngemäß auch auf "
          "Englisch (z. B. „match“, „community rating“, „status“, „favorite“, „watched“, "
          "„unwatched“), ebenso Prozentzahlen und Wertungszahlen.",
}

# Kompletter Bestand fuer Bestandsfragen. Nur mit Cloud-Modell: bei ~465 Titeln
# rund 9K Tokens — fuer Hermes3 (8K Kontext) unmoeglich, fuer 32K kein Problem.
# Anlass: "Welche MCU-Filme habe ich noch nicht gesehen?" beantwortete das
# Cloud-Modell mit "keinen", weil es nur 20 Vektortreffer sah — obwohl
# "Thunderbolts*" ungesehen in der Sammlung liegt.
# 468 Titel mit allen Spalten sind rund 12K Tokens. Mit 11000 fiel der Katalog
# nach dem Ergaenzen der Laufzeit-Spalte auf die Kurzform ohne Genres und Reihe
# zurueck. Selbst 14000 lassen im 32K-Fenster genug Luft fuer Treffer, Profil
# und Antwort.
CATALOG_MAX_TOKENS = 14000
_CATALOG_HEADER = """

<katalog>
KOMPLETTE Sammlung — Spalten: Art | Titel | Jahr | Laufzeit in Minuten | Genres | Status
(Bei Serien ist die Laufzeit die durchschnittliche Folgenlaenge.)
{rows}
</katalog>"""


_EPISODES_HEADER = "\n\nEinzelne Folgen — Spalten: Serie | Folge | Titel | Kurzinhalt\n{episodes}"


# ---------------------------------------------------------------------------
# RAG helpers
# ---------------------------------------------------------------------------

def _status_label(it: dict) -> str:
    """Kurzes Status-Wort fuer die Bibliotheks-Tabelle. Ohne diese Spalte kann
    das Modell Fragen nach 'noch nicht gesehen' nicht korrekt beantworten."""
    if it.get("favorite") and it.get("played"):
        return "gesehen, Favorit"
    if it.get("favorite"):
        return "Favorit"
    if it.get("played"):
        return "gesehen"
    if it.get("partial"):
        return "angefangen"
    return "ungesehen"


def _wertung(v: Optional[float]) -> str:
    """Wertung mit einer Nachkommastelle und deutschem Komma. Mit Punkt
    uebernahm das Modell die Schreibweise in den deutschen Fliesstext
    ('Community-Wertung 5.4')."""
    if v is None:
        return "?"
    return f"{v:.1f}".replace(".", ",")


def _passung_prozent(sim: Optional[float]) -> str:
    """Passung in Prozent — exakt dieselbe Umrechnung wie der match_score in
    main.py (cosine mit Kontrastspreizung). Muss identisch bleiben, sonst nennt
    der Chat andere Prozentwerte, als die Karten in der Oberflaeche anzeigen."""
    if sim is None:
        return "?"
    return f"{passung_prozent(sim)} %"


def _format_candidates(items: list[dict]) -> str:
    """Belegzeilen fuer die ~20 RAG-Treffer.

    Wertungen und Passung stehen bewusst NUR hier und nicht im <katalog>:
    dort kosteten drei weitere Spalten ueber alle ~470 Titel mehrere tausend
    Tokens und wuerden das Budget (CATALOG_MAX_TOKENS) auf die Kurzform
    zurueckwerfen. Ohne diese Spalten hatte das Modell keine Grundlage fuer
    ein Urteil und empfahl praktisch jeden erfragten Titel.
    """
    if not items:
        return "(keine Treffer)"
    lines = []
    for it in items:
        genres = ", ".join(it.get("genres") or [])[:60]
        year = it.get("year") or "?"
        plot = (it.get("overview") or "")[:180].replace("\n", " ")
        kind = it.get("kind", "Film")
        lines.append(
            f"{kind} | {it['name']} | {year} | {genres} | "
            f"{_status_label(it)} | {_wertung(it.get('community_rating'))} | "
            f"{_wertung(it.get('user_rating'))} | "
            f"{_passung_prozent(it.get('match'))} | "
            f"{it.get('sprachen') or '?'} | {plot}"
        )
    return "\n".join(lines)


def _schlecht_bewertete_titel(user_id: int, limit: int = 5) -> list[str]:
    """Filme, die der Nutzer selbst schwach bewertet hat (<= 4 von 10).

    Das Profil nennt nur Vorlieben. Ohne die Gegenprobe fehlt dem Modell jeder
    Anhaltspunkt fuer ein Nein — und es bestaetigt jeden Vorschlag.
    """
    with get_session() as s:
        rows = s.exec(
            select(WatchState).where(WatchState.user_id == user_id,
                                     WatchState.user_rating != None,   # noqa: E711
                                     WatchState.user_rating <= 4)
        ).all()
        if not rows:
            return []
        # Die schlechtesten zuerst: sie sagen am meisten ueber Abneigungen aus.
        rows = sorted(rows, key=lambda w: w.user_rating)[:limit]
        movies = s.exec(
            select(Movie).where(Movie.id.in_([w.movie_id for w in rows]))
        ).all()
    name_by_id = {m.id: m.name for m in movies}
    return [f"{name_by_id[w.movie_id]} ({_wertung(w.user_rating)})"
            for w in rows if w.movie_id in name_by_id]


# Erkennt Antworten, in denen das Modell den Bibliotheksblock nachplappert
# statt zu formulieren. Wird fuer den einmaligen Retry im Preload-Cron benutzt.
#   altes Format:  "- [Film] Iron Man (2008) [Action]: Plot..."
#   neues Format:  "Film | Iron Man | 2008 | Action | gesehen | Plot..."
_DUMP_LINE = re.compile(
    r"^\s*[-*]?\s*(?:"
    r"\[(?:Film|Serie|Episode|TV Episode)\]"      # [Film] ...
    r"|(?:Film|Serie|Episode)\s*\|"               # Film | ...
    r")",
    re.IGNORECASE,
)


def looks_like_context_dump(text: str) -> bool:
    """True, wenn die Antwort (auch nur teilweise) im Kandidatenformat vorliegt.

    Zwei Kriterien, weil beide Fehlerbilder real vorkamen:
      1. Die Antwort *beginnt* mit einer Datenzeile — dann ist sie schon kaputt,
         egal wie lang sie ist (eine einzeilige Antwort erreicht die
         Mehrheitsregel unten sonst nie).
      2. Mindestens die Haelfte der Zeilen sieht nach Tabelle aus.
    """
    lines = [l for l in (text or "").splitlines() if l.strip()]
    if not lines:
        return False
    if _DUMP_LINE.match(lines[0]):
        return True
    hits = sum(1 for l in lines if _DUMP_LINE.match(l) or l.count("|") >= 3)
    return hits >= max(2, len(lines) // 2)


def _retrieve_context(query: str, user_id: int,
                      sprache: Optional[str] = None) -> list[dict]:
    """Films + series. Episodes are NOT included by default — they go through
    `_retrieve_episodes` which fires only on episode-shaped questions."""
    raw = search_by_text(normalize_user_query(query, sprache), k=_rag_top_k())
    if not raw:
        return []
    ids = [r["movie_id"] for r in raw]
    with get_session() as s:
        movies = s.exec(select(Movie).where(Movie.id.in_(ids))).all()
        series_rows = s.exec(select(Series).where(Series.id.in_(ids))).all()
        ws_rows = s.exec(
            select(WatchState).where(WatchState.user_id == user_id,
                                     WatchState.movie_id.in_(ids))
        ).all()
        sp_rows = s.exec(
            select(SeriesProgress).where(SeriesProgress.user_id == user_id,
                                         SeriesProgress.series_id.in_(ids))
        ).all()
    ws_by_id = {w.movie_id: w for w in ws_rows}
    sp_by_id = {p.series_id: p for p in sp_rows}
    movie_by_id = {m.id: m for m in movies}
    series_by_id = {ser.id: ser for ser in series_rows}
    # Einmal pro Aufruf, nicht je Titel: die Funktion ist zwischengespeichert,
    # der erste Aufruf nach dem Sync rechnet aber ueber alle Vektoren.
    # Faellt Chroma aus, bleibt der Chat nutzbar — dann eben ohne Passung.
    try:
        sims = profile_all_similarities(user_id)
    except Exception as e:
        log.warning("Passungswerte fuer den Chat nicht verfuegbar: %s", e)
        sims = {}
    out = []
    for r in raw:
        mid = r["movie_id"]
        if mid in movie_by_id:
            out.append(_film_zeile(movie_by_id[mid], ws_by_id.get(mid), sims))
        elif mid in series_by_id:
            out.append(_serie_zeile(series_by_id[mid], sp_by_id.get(mid), sims))
    return out


def _film_zeile(m: Movie, ws: Optional[WatchState], sims: dict) -> dict:
    return {
        "kind": "Film", "id": m.id, "name": m.name, "year": m.year,
        "genres": [g for g in m.genres.split(",") if g],
        "overview": m.overview,
        "played": bool(ws and ws.played),
        "favorite": bool(ws and ws.is_favorite),
        "community_rating": m.community_rating,
        "user_rating": ws.user_rating if ws else None,
        "match": sims.get(m.id),
        # Nur der Ton: Untertitel beantworten die Frage "auf Deutsch?" nicht,
        # und jede weitere Spalte kostet Platz im Fenster.
        "sprachen": m.audio_languages or "",
    }


def _serie_zeile(ser: Series, sp: Optional[SeriesProgress], sims: dict) -> dict:
    return {
        "kind": "Serie", "id": ser.id, "name": ser.name, "year": ser.year,
        "genres": [g for g in ser.genres.split(",") if g],
        "overview": ser.overview,
        # Serien haben keine WatchState-Zeile — der Status kommt aus
        # SeriesProgress, sonst stuenden alle Serien als "ungesehen" da.
        "played": bool(sp and sp.state == "completed"),
        "partial": bool(sp and sp.state == "watching"),
        "favorite": bool(sp and sp.is_favorite),
        "community_rating": ser.community_rating,
        # SeriesProgress hat kein user_rating — nur Filme werden bewertet.
        "user_rating": None,
        "match": sims.get(ser.id),
        "sprachen": ser.audio_languages or "",
    }


def _vergleichbar(text: str) -> str:
    """Titelvergleich ohne Zierrat: Anfuehrungszeichen, Binde- und Gedankenstriche
    und Mehrfachleerzeichen fallen weg, damit „Gott ist nicht tot 4 – Wir sind
    das Volk" und die Schreibweise mit Bindestrich als dasselbe gelten."""
    t = (text or "").lower()
    for zeichen in "„“”\"'`":
        t = t.replace(zeichen, " ")
    for zeichen in "-–—:":
        t = t.replace(zeichen, " ")
    return " ".join(t.split())


def _titel_aus_frage(message: str, user_id: int, limit: int = 3) -> list[dict]:
    """Belegzeilen fuer Titel, die woertlich in der Frage stehen.

    Die Vektorsuche lieferte zu „Gott ist nicht tot 4 — Wir sind das Volk" die
    Teile 1, 2 und 5, aber nicht den gefragten: die Titel einer Reihe liegen im
    Vektorraum zu dicht beieinander. Das Modell nahm daraufhin die Wertung und
    die Passung der Nachbarzeile und urteilte ueber den falschen Film. Wer nach
    einem Titel fragt, bekommt dessen eigene Zeile jetzt garantiert — der
    laengste Treffer zuerst, damit Teil 4 gewinnt und nicht der Reihenname.
    """
    frage = _vergleichbar(message)
    if not frage:
        return []
    with get_session() as s:
        movies = s.exec(select(Movie)).all()
        series = s.exec(select(Series)).all()
        treffer = []
        for m in movies:
            n = _vergleichbar(m.name)
            if len(n) >= 4 and n in frage:
                treffer.append((len(n), "movie", m))
        for x in series:
            n = _vergleichbar(x.name)
            if len(n) >= 4 and n in frage:
                treffer.append((len(n), "series", x))
        if not treffer:
            return []
        treffer.sort(key=lambda t: t[0], reverse=True)
        treffer = treffer[:limit]
        film_ids = [row.id for _, art, row in treffer if art == "movie"]
        serien_ids = [row.id for _, art, row in treffer if art == "series"]
        ws_by_id = {w.movie_id: w for w in s.exec(
            select(WatchState).where(WatchState.user_id == user_id,
                                     WatchState.movie_id.in_(film_ids))).all()} if film_ids else {}
        sp_by_id = {p.series_id: p for p in s.exec(
            select(SeriesProgress).where(SeriesProgress.user_id == user_id,
                                         SeriesProgress.series_id.in_(serien_ids))).all()} if serien_ids else {}
    try:
        sims = profile_all_similarities(user_id)
    except Exception:
        sims = {}
    return [_film_zeile(row, ws_by_id.get(row.id), sims) if art == "movie"
            else _serie_zeile(row, sp_by_id.get(row.id), sims)
            for _, art, row in treffer]


def _retrieve_episodes(query: str, sprache: Optional[str] = None) -> list[dict]:
    """Episode RAG — only called when the question looks episode-shaped."""
    raw = search_by_text(normalize_user_query(query, sprache), k=RAG_EPISODE_K,
                         media_type="episode")
    if not raw:
        return []
    ids = [r["movie_id"] for r in raw]
    with get_session() as s:
        eps = s.exec(select(Episode).where(Episode.id.in_(ids))).all()
        sids = {e.series_id for e in eps}
        sers = s.exec(select(Series).where(Series.id.in_(sids))).all()
    ser_by_id = {ser.id: ser for ser in sers}
    out = []
    for r in raw:
        ep_obj = next((e for e in eps if e.id == r["movie_id"]), None)
        if not ep_obj:
            continue
        ser = ser_by_id.get(ep_obj.series_id)
        label = ""
        if ep_obj.season is not None and ep_obj.episode is not None:
            label = f"S{ep_obj.season}E{ep_obj.episode}"
        out.append({
            "series": ser.name if ser else "?",
            "label": label,
            "name": ep_obj.name,
            "overview": ep_obj.overview,
        })
    return out


def _format_episodes(eps: list[dict]) -> str:
    if not eps:
        return ""
    lines = []
    for e in eps:
        plot = (e.get("overview") or "")[:200].replace("\n", " ")
        lines.append(f'{e["series"]} | {e["label"]} | {e["name"]} | {plot}')
    return "\n".join(lines)


def _catalog_block(user_id: int) -> str:
    """Alle Filme und Serien mit Status des Nutzers — nur mit grossem Kontext
    (llm_client.grosser_kontext); einem 8K-Modell wuerde der Katalog den
    Prompt sprengen."""
    if not llm_client.grosser_kontext():
        return ""
    with get_session() as s:
        movies = s.exec(select(Movie)).all()
        series = s.exec(select(Series)).all()
        ws = {w.movie_id: w for w in s.exec(
            select(WatchState).where(WatchState.user_id == user_id)).all()}
        sp = {p.series_id: p for p in s.exec(
            select(SeriesProgress).where(SeriesProgress.user_id == user_id)).all()}

    full, short = [], []
    for m in sorted(movies, key=lambda x: (x.name or "").lower()):
        w = ws.get(m.id)
        status = _status_label({
            "played": bool(w and w.played), "favorite": bool(w and w.is_favorite),
            "partial": bool(w and not w.played and w.playback_position_ticks > 0)})
        genres = ", ".join([g for g in (m.genres or "").split(",") if g][:2])
        # Laufzeit gehoert dazu: ohne sie konnte das Modell "nenne mir einen
        # Film unter 90 Minuten" nicht beantworten und schlug 96-Minuten-Filme vor.
        # Die Reihe steht bewusst NICHT hier: mit ihr waere der Katalog rund
        # 17K Tokens gross und fiele auf die Kurzform ohne Genres zurueck —
        # Reihen erkennt das Modell ohnehin am Titel.
        full.append(f"Film | {m.name} | {m.year or '?'} | {m.runtime_min or '?'} | "
                    f"{genres} | {status}")
        short.append(f"Film | {m.name} | {m.year or '?'} | {m.runtime_min or '?'} | {status}")
    for x in sorted(series, key=lambda x: (x.name or "").lower()):
        p = sp.get(x.id)
        status = _status_label({
            "played": bool(p and p.state == "completed"),
            "partial": bool(p and p.state == "watching"),
            "favorite": bool(p and p.is_favorite)})
        genres = ", ".join([g for g in (x.genres or "").split(",") if g][:2])
        full.append(f"Serie | {x.name} | {x.year or '?'} | {x.runtime_min_avg or '?'} | "
                    f"{genres} | {status}")
        short.append(f"Serie | {x.name} | {x.year or '?'} | {x.runtime_min_avg or '?'} | {status}")

    rows = "\n".join(full)
    if len(rows) / 2.7 > CATALOG_MAX_TOKENS:
        rows = "\n".join(short)          # grosse Sammlung: nur Titel + Status
    if len(rows) / 2.7 > CATALOG_MAX_TOKENS:
        return ""                        # noch groesser: lieber keinen halben Katalog
    return _CATALOG_HEADER.format(rows=rows)


def _build_system_prompt(user_id: int, message: str,
                         sprache: Optional[str] = None) -> str:
    """Systemprompt fuer eine Frage. ``sprache``: Sprache der Antwort ("de"/"en");
    ohne Angabe die der laufenden Anfrage (texte.aktuell). Der Chat-Faden und
    der naechtliche Vorlauf haben keine Anfrage und muessen sie angeben."""
    sprache = texte.normalisieren(sprache) or texte.aktuell()
    profile = build_profile(user_id)
    candidates = _retrieve_context(message, user_id, sprache)
    # Woertlich genannte Titel stehen vorn und verdraengen ihre eigene
    # Fundstelle aus der Vektorsuche (siehe _titel_aus_frage).
    genannt = _titel_aus_frage(message, user_id)
    if genannt:
        bekannt = {c["id"] for c in genannt}
        candidates = genannt + [c for c in candidates if c["id"] not in bekannt]
    recent = ", ".join(profile.get("recent_movies") or []) or "(noch nichts gesehen)"
    profil_text = profile_summary_text(profile)
    schlecht = _schlecht_bewertete_titel(user_id)
    if schlecht:
        profil_text += "\nSchlecht bewertet: " + ", ".join(schlecht)
    episodes_block = ""
    if _is_episode_question(message):
        eps = _retrieve_episodes(message, sprache)
        if eps:
            episodes_block = _EPISODES_HEADER.format(episodes=_format_episodes(eps))
    prompt = SYSTEM_TEMPLATE.format(
        sprachzeile=_SPRACHZEILE.get(sprache) or _SPRACHZEILE["de"],
        profile=profil_text,
        recent=recent,
        query=message,
        candidates=_format_candidates(candidates),
        episodes_block=episodes_block,
        catalog_block=_catalog_block(user_id),
    )
    return prompt + texte.antwortsprache(sprache)


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _insert_turn(user_id: int, user_text: str) -> int:
    """Insert the user message and an empty streaming assistant row.
    Returns the assistant row id."""
    now = datetime.utcnow()
    with get_session() as s:
        s.add(ChatMessage(user_id=user_id, role="user", content=user_text,
                          status="complete", created_at=now))
        assistant = ChatMessage(user_id=user_id, role="assistant", content="",
                                status="streaming",
                                created_at=now + timedelta(microseconds=1))
        s.add(assistant)
        s.commit()
        s.refresh(assistant)
        return assistant.id


def _append_chunk(row_id: int, chunk: str) -> None:
    with get_session() as s:
        row = s.get(ChatMessage, row_id)
        if not row:
            return
        row.content = (row.content or "") + chunk
        s.add(row)
        s.commit()


def _finalize(row_id: int, status: str, error: Optional[str] = None,
              sprache: Optional[str] = None) -> None:
    with get_session() as s:
        row = s.get(ChatMessage, row_id)
        if not row:
            return
        row.status = status
        if error:
            row.content = (row.content or "") + "\n" + _t("fehlerzeile", sprache, fehler=error)
        s.add(row)
        s.commit()


def _read_row(row_id: int) -> Optional[ChatMessage]:
    with get_session() as s:
        return s.get(ChatMessage, row_id)


# ---------------------------------------------------------------------------
# LLM worker (runs on a daemon thread, independent of the HTTP request)
# ---------------------------------------------------------------------------

def _recent_history_for_prompt(user_id: int, exclude_row_id: int,
                               max_turns: int = 6) -> list[dict]:
    """Pull the last N complete messages before this turn to give the LLM
    conversational context."""
    with get_session() as s:
        rows = s.exec(
            select(ChatMessage).where(ChatMessage.user_id == user_id)
        ).all()
    rows = [r for r in rows
            if r.id != exclude_row_id
            and r.status == "complete"
            and r.content]
    rows.sort(key=lambda r: r.created_at)
    # take the tail — exclude the just-inserted user message too by id match
    tail = rows[-max_turns:]
    return [{"role": r.role, "content": r.content} for r in tail]


def _run_llm(row_id: int, user_id: int, message: str,
             preroll_history: Optional[list[dict]] = None,
             sprache: Optional[str] = None) -> None:
    """Worker — runs the full LLM stream and writes into the assistant row.

    ``sprache`` ("de"/"en"): Sprache der Meldungen und der Antwort. Der Faden
    erbt die Sprache der Anfrage nicht (texte.py), start_turn gibt sie mit.

    Hardening:
      1. Pre-flight: kein Modell eingerichtet oder keins erreichbar —
         sofort eine verstaendliche Meldung statt eines Fehlers.
      2. Per-chunk read-timeout (90s) so a hung
         model raises ReadTimeout instead of blocking forever.
      3. Total turn deadline (TURN_DEADLINE_S) — if exceeded, abort.
      4. Catches httpx.ReadTimeout and an unreachable model separately so the UI
         gets a friendly German message instead of a stack-trace string.
    """
    import time
    import httpx

    sprache = texte.normalisieren(sprache) or texte.aktuell()

    # 1) Pre-flight. Zwei Faelle getrennt: "nicht eingerichtet" kann nur ein
    # Admin beheben, "nicht erreichbar" erledigt sich oft von selbst.
    if not llm_client.active_model():
        log.info("Chat row %s: kein Sprachmodell eingerichtet", row_id)
        _append_chunk(row_id, _t("kein_modell", sprache))
        _finalize(row_id, "error", sprache=sprache)
        return
    if not llm_client.is_available():
        log.warning("Chat row %s: kein Sprachmodell erreichbar — failing fast", row_id)
        _append_chunk(row_id, _t("llm_down", sprache))
        _finalize(row_id, "error", sprache=sprache)
        return

    try:
        system = _build_system_prompt(user_id, message, sprache)
        history = preroll_history if preroll_history is not None \
                  else _recent_history_for_prompt(user_id, row_id)
        if not history or history[-1].get("content") != message:
            history.append({"role": "user", "content": message})

        messages = [{"role": "system", "content": system}] + history
        # Cloud-Modell mit Ollama als Rueckfall. Der Lesetimeout bleibt locker:
        # Hermes3 braucht kalt 2–5 min bis zum ersten Wort, und ein Denk-Modell
        # schreibt erst nach dem (herausgefilterten) Nachdenken sichtbaren Text.
        # Niedrige Temperatur, weil die Antwort ein Urteil ist: mit der Vorgabe
        # 0.7 beantwortete das Modell dieselbe Frage zweimal hintereinander
        # einmal mit Ja und einmal mit Nein.
        stream = llm_client.stream(messages, temperature=0.35, read_timeout=480.0,
                                   aufgabe="chat")

        start = time.monotonic()
        deadline = start + TURN_DEADLINE_S
        buf = ""
        any_chunk = False
        last_chunk_at = start

        for chunk in stream:
            now = time.monotonic()
            # Hard turn-deadline (15 min)
            if now > deadline:
                log.warning("Chat row %s: hit turn deadline (%ds)",
                            row_id, TURN_DEADLINE_S)
                if buf:
                    _append_chunk(row_id, buf)
                _append_chunk(row_id, "\n\n" + _t("llm_timeout", sprache))
                _finalize(row_id, "error", sprache=sprache)
                return
            # Inter-chunk watchdog: if tokens were flowing and stop, abort.
            if any_chunk and (now - last_chunk_at) > INTER_CHUNK_TIMEOUT_S:
                log.warning("Chat row %s: inter-chunk timeout (%ds)",
                            row_id, INTER_CHUNK_TIMEOUT_S)
                if buf:
                    _append_chunk(row_id, buf)
                _append_chunk(row_id, "\n\n" + _t("llm_timeout", sprache))
                _finalize(row_id, "error", sprache=sprache)
                return
            # First-token deadline: if no tokens at all yet and we've waited
            # too long, abort (Ollama is stuck, not just slow).
            if not any_chunk and (now - start) > FIRST_TOKEN_DEADLINE_S:
                log.warning("Chat row %s: first-token deadline (%ds)",
                            row_id, FIRST_TOKEN_DEADLINE_S)
                _append_chunk(row_id, _t("llm_timeout", sprache))
                _finalize(row_id, "error", sprache=sprache)
                return
            if chunk:
                any_chunk = True
                last_chunk_at = now
                buf += chunk
                if len(buf) >= 24 or "\n" in buf:
                    _append_chunk(row_id, buf)
                    buf = ""

        if buf:
            _append_chunk(row_id, buf)
        if not any_chunk:
            _append_chunk(row_id, _t("llm_timeout", sprache))
            _finalize(row_id, "error", sprache=sprache)
            return
        _finalize(row_id, "complete")

    except httpx.ReadTimeout:
        log.warning("Chat row %s: ReadTimeout (Modell haengt mitten im Strom)", row_id)
        _append_chunk(row_id, "\n\n" + _t("llm_timeout", sprache))
        _finalize(row_id, "error", sprache=sprache)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        log.warning("Chat row %s: Sprachmodell mitten im Aufruf nicht erreichbar", row_id)
        _append_chunk(row_id, _t("llm_down", sprache))
        _finalize(row_id, "error", sprache=sprache)
    except llm_client.KeinModell:
        # Zwischen Pre-flight und Aufruf umgestellt — selten, aber moeglich.
        _append_chunk(row_id, _t("kein_modell", sprache))
        _finalize(row_id, "error", sprache=sprache)
    except Exception as e:
        log.warning("LLM worker failed for row %s: %s", row_id, e)
        _finalize(row_id, "error", error=str(e), sprache=sprache)


def _faden(row_id: int, user_id: int, message: str, sprache: str) -> None:
    """Einstieg des Chat-Fadens. threading.Thread erbt die ContextVar mit der
    Sprache der Anfrage NICHT; hier wird sie fuer diesen Faden gesetzt, damit
    auch Texte tief darunter (Fehlermeldungen aus llm_client, Stoppwoerter der
    Suche) in der Sprache des Nutzers kommen."""
    texte.setzen(sprache)
    _run_llm(row_id, user_id, message, sprache=sprache)


def start_turn(user_id: int, message: str, sprache: Optional[str] = None) -> int:
    """Create the DB rows + kick off the worker. Returns the assistant row id.

    Die Sprache wird HIER geholt (der Aufruf kommt aus der Anfrage, ``texte.aktuell()``
    kennt dort die Sprache des Nutzers) und dem Faden uebergeben."""
    sprache = texte.normalisieren(sprache) or texte.aktuell()
    row_id = _insert_turn(user_id, message)
    t = threading.Thread(
        target=_faden, args=(row_id, user_id, message, sprache), daemon=True,
    )
    t.start()
    return row_id


# ---------------------------------------------------------------------------
# SSE stream — tails the DB row and emits deltas
# ---------------------------------------------------------------------------

HEARTBEAT_S = 10.0   # siehe stream_assistant


def stream_assistant(row_id: int, timeout_s: float = 600.0,
                     sprache: Optional[str] = None) -> Iterator[str]:
    """Yield text chunks for SSE. Stops when the row reaches complete/error
    or the timeout elapses.

    Ein Denk-Modell schreibt minutenlang nichts Sichtbares (DeepSeek: gemessen
    3–5 min bis zum ersten Wort). Ueber die Strecke floss kein Byte, und der
    Reverse-Proxy schloss die Verbindung — im Browser stand "Keine Verbindung
    zum Server", obwohl die Antwort im Hintergrund fertig wurde. Alle
    ``HEARTBEAT_S`` Sekunden geht deshalb ein leerer Textbrocken raus; der
    Aufrufer macht daraus ein Lebenszeichen, das der Client ignoriert.
    """
    last_len = 0
    letztes_lebenszeichen = time.monotonic()
    deadline = time.monotonic() + timeout_s
    while True:
        row = _read_row(row_id)
        if not row:
            yield _t("zeile_weg", sprache)
            return
        if row.content and len(row.content) > last_len:
            delta = row.content[last_len:]
            last_len = len(row.content)
            letztes_lebenszeichen = time.monotonic()
            yield delta
        if row.status == "complete":
            return
        if row.status == "error":
            return
        jetzt = time.monotonic()
        if jetzt > deadline:
            return
        if jetzt - letztes_lebenszeichen >= HEARTBEAT_S:
            letztes_lebenszeichen = jetzt
            yield ""
        time.sleep(_POLL_INTERVAL_S)


# ---------------------------------------------------------------------------
# Legacy / debug helpers (keep callable from /chat/context endpoint)
# ---------------------------------------------------------------------------

def chat_context(user_id: int, message: str) -> dict:
    profile = build_profile(user_id)
    candidates = _retrieve_context(message, user_id)
    return {
        "profile_summary": profile_summary_text(profile),
        "recent": profile.get("recent_movies", []),
        "candidates": candidates,
    }


def get_history(user_id: int, hours: int = HISTORY_HOURS) -> list[dict]:
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    with get_session() as s:
        rows = s.exec(
            select(ChatMessage).where(ChatMessage.user_id == user_id)
        ).all()
    rows = [r for r in rows if r.created_at >= cutoff]
    rows.sort(key=lambda r: r.created_at)
    return [
        {
            "id": r.id,
            "role": r.role,
            "content": r.content,
            "status": r.status,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


def clear_history(user_id: int) -> int:
    with get_session() as s:
        rows = s.exec(
            select(ChatMessage).where(ChatMessage.user_id == user_id)
        ).all()
        for r in rows:
            s.delete(r)
        s.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# Compat for the preload cron: drain a turn synchronously.
# ---------------------------------------------------------------------------

def stream_chat(user_id: int, message: str,
                history: list[dict] | None = None,
                sprache: Optional[str] = None) -> Iterator[str]:
    """Synchronous generator used by the preload cron. Does NOT persist —
    the caller (cron.py) stores the final text in ChatPreload instead.

    ``sprache``: Sprache der Antwort. Der Vorlauf hat keine Anfrage und gibt
    die des jeweiligen Nutzers an (texte.fuer_nutzer)."""
    try:
        system = _build_system_prompt(user_id, message, sprache)
        messages: list[dict] = [{"role": "system", "content": system}]
        for turn in (history or [])[-6:]:
            if turn.get("role") in ("user", "assistant") and turn.get("content"):
                messages.append({"role": turn["role"], "content": turn["content"]})
        messages.append({"role": "user", "content": message})
        # Dieselbe niedrige Temperatur wie im echten Chat: die vorberechneten
        # Antworten sollen dieselben Urteile liefern wie eine Live-Frage.
        stream = llm_client.stream(messages, temperature=0.35, read_timeout=600.0,
                                   aufgabe="chat_vorschlaege")
        for chunk in stream:
            if chunk:
                yield chunk
    except Exception as e:
        log.warning("stream_chat failed: %s", e)
        yield f"\n{FEHLER_MARKE} {e}]"
