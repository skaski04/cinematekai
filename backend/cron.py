"""CinematekAI daily cron — APScheduler.

Laeuft einmal am Tag zu der Uhrzeit, die im Admin-Bereich eingestellt ist
(Abschnitt ``schedule`` in settings.py: Stunde, Minute, an/aus, Zeitzone).
Die Vorgabe 10:30 liegt kurz nach einem morgendlichen Bibliotheks-Scan von
Jellyfin und in einem Zeitraum, in dem Strom tagsueber oft guenstig ist.
Alles Teure laeuft hier und
nicht im Seitenaufruf, damit die Oberflaeche nur Zwischenspeicher-Treffer
oder Token-Stroeme sieht.

Solange CinematekAI nicht eingerichtet ist (kein Jellyfin verbunden), laeuft
die Kette nicht. Einzelne Jobs, denen ihr Modell fehlt (kein Sprach- oder
Einbettungsmodell), melden ``{"skipped": "<Grund>"}`` statt eines Fehlers —
ein frischer Container ohne KI-Anbieter soll im Admin-Bereich nicht lauter
rote Jobs zeigen, und ein Monitoring ueber /metrics soll keinen Ausfall
melden, wo nur etwas noch nicht eingetragen ist.

Jobs:
  1. sync_library()                 — Jellyfin metadata + collections
  2. sync_watch_state(user)         — per-user UserData (played/fav/rating)
  3. embed_all(force=False)         — only new / changed movies
  4. daily_for(user) + explain      — daily pick + LLM reason pre-baked
  5. preload_chat_suggestions(user) — 4 default chips with full streamed answers
  6. embed_user_profile(user)       — user vector for filmdetail match-score
  7. prune_chat_history()           — drop ChatMessage rows older than 24 h

Admin can also trigger any job on demand via ``POST /admin/cron/run?job=<name>``
(asynchron) or ``POST /cron/run-now?job=<name>`` (blockierend, alt).
"""
from __future__ import annotations
import functools, json, logging, threading
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlmodel import select

import anbieter
import llm_client
import settings
import texte
from config import DATA_DIR
from database import get_session, User, ChatMessage, ChatPreload

log = logging.getLogger("cinematekai.cron")

# Rueckfall, falls in der Datenbank eine Zone steht, die das Image nicht
# kennt. settings.py prueft die Zone beim Speichern; das hier greift nur bei
# fehlenden tzdata oder einem von Hand eingetragenen Wert.
_ZONE_RUECKFALL = "Europe/Berlin"

NICHT_EINGERICHTET = "CinematekAI ist noch nicht eingerichtet"

# Texte fuer die Oberflaeche (Admin-Bereich, Einrichtung): Job-Beschreibungen,
# Gruende fuer uebersprungene Jobs und die Zusammenfassung des letzten Laufs.
# Die Zusammenfassung wird NICHT beim Laufende gebaut, sondern beim Ausliefern
# (job_info): der Lauf passiert im Hintergrund ohne Sprache der Anfrage, und
# wer die Liste abruft, soll sie in seiner Sprache sehen. Gespeichert wird das
# rohe Ergebnis des Jobs (Zustandsfeld "ergebnis").
_T = {
    "de": {
        "grund_nicht_eingerichtet": NICHT_EINGERICHTET,
        "grund_kein_einbettungsmodell": "Kein Einbettungsmodell eingerichtet",
        "grund_kein_sprachmodell": "Kein Sprachmodell eingerichtet",

        "uebersprungen": "übersprungen: {grund}",
        "laeuft_bereits": "läuft bereits (eine andere Einbettung ist noch aktiv)",
        "ok_von": "{ok} / {total} ok, {failed} failed",
        "neu_alt_weg": "+{added} neu, ~{updated} aktualisiert, −{removed} weg",
        "synchronisiert": "{synced} synchronisiert",
        "geloescht": "{pruned} Zeilen gelöscht",
        "kette_ok": "{ok} / {n} Jobs ok",
        "kette_uebersprungen": ", davon {u} übersprungen",
        "aktuell": "aktuell",
        "titel_kuratiert": "{n} Titel, kuratiert={kuratiert}",

        "job_all": "Alle Jobs hintereinander (volle tägliche Kette)",
        "job_sync_library": "Jellyfin-Bibliothek synchronisieren (Filme + Sammlungen)",
        "job_sync_series_library": "Jellyfin-Bibliothek synchronisieren (Serien)",
        "job_sync_episodes": "Episoden-Metadaten je Serie (für Folgen-Suche)",
        "job_sync_watch_state": "Watch-State pro User (gespielt, Favorit, Rating)",
        "job_sync_series_progress": "Serien-Fortschritt pro User (Next-Up, Watching-State)",
        "job_embed_missing": "Fehlende Embeddings berechnen (Filme + Serien)",
        "job_embed_user_profiles": "Nutzer-Profilvektoren für Match-Score",
        "job_tmdb_refresh": "TMDB-Listen „Wer das mochte, mochte auch“ nachladen (30 Tage Cache)",
        "job_daily_recommendations": "Tages-Empfehlung, vom KI-Kurator gewählt (2–12 min je Nutzer, abhängig vom Sprachmodell; ohne Sprachmodell mit großem Kontext mechanisch)",
        "job_missing_suggestions": "„Fehlt in deiner Sammlung“ — passende Filme außerhalb von Jellyfin (alle 6 Tage)",
        "job_weekly_series": "Serie der Woche pro User (Cache 7 Tage)",
        "job_preload_chat": "Chat-Vorschläge mit dem Sprachmodell vorberechnen (~4 min mit Cloud-Modell)",
        "job_prune_chat": "Chat-Historie älter als 24h löschen",
        "job_prune_stale": "Karteileichen entfernen (verwaiste Watch-States, Episoden, abgelaufene Sessions, "
                           "TMDB-Daten älter als 170 Tage)",
        "job_warm_similarities": "Passungswerte vorab berechnen, damit die Startseite sofort lädt",
    },
    "en": {
        "grund_nicht_eingerichtet": "CinematekAI is not set up yet",
        "grund_kein_einbettungsmodell": "No embedding model set up",
        "grund_kein_sprachmodell": "No language model set up",

        "uebersprungen": "skipped: {grund}",
        "laeuft_bereits": "already running (another embedding run is still active)",
        "ok_von": "{ok} / {total} ok, {failed} failed",
        "neu_alt_weg": "+{added} new, ~{updated} updated, −{removed} removed",
        "synchronisiert": "{synced} synced",
        "geloescht": "{pruned} rows deleted",
        "kette_ok": "{ok} / {n} jobs ok",
        "kette_uebersprungen": ", {u} of them skipped",
        "aktuell": "up to date",
        "titel_kuratiert": "{n} titles, curated={kuratiert}",

        "job_all": "All jobs in sequence (full daily chain)",
        "job_sync_library": "Sync Jellyfin library (movies + collections)",
        "job_sync_series_library": "Sync Jellyfin library (series)",
        "job_sync_episodes": "Episode metadata per series (for episode search)",
        "job_sync_watch_state": "Watch state per user (played, favorite, rating)",
        "job_sync_series_progress": "Series progress per user (next up, watching state)",
        "job_embed_missing": "Compute missing embeddings (movies + series)",
        "job_embed_user_profiles": "User profile vectors for the match score",
        "job_tmdb_refresh": "Reload TMDB “People who liked this also liked” lists (30-day cache)",
        "job_daily_recommendations": "Daily pick, chosen by the AI curator (2–12 min per user, depending on the language model; picked mechanically without a large-context language model)",
        "job_missing_suggestions": "“Missing from your collection” — matching movies outside Jellyfin (every 6 days)",
        "job_weekly_series": "Series of the week per user (7-day cache)",
        "job_preload_chat": "Precompute chat suggestions with the language model (~4 min with a cloud model)",
        "job_prune_chat": "Delete chat history older than 24h",
        "job_prune_stale": "Remove stale records (orphaned watch states, episodes, expired sessions, "
                           "TMDB data older than 170 days)",
        "job_warm_similarities": "Precompute match values so the home page loads instantly",
    },
}


def _t(schluessel: str, sprache: str | None = None, **werte) -> str:
    return texte.waehle(_T, schluessel, sprache, **werte)


def grund_text(ergebnis: dict, sprache: str | None = None) -> str:
    """Grund eines uebersprungenen Jobs in der Sprache ``sprache``.

    ``ergebnis`` ist das ``{"skipped": ..., "skipped_key": ...}`` aus _braucht.
    Ohne Schluessel (ein Job, der selbst "skipped" meldet, z. B. tmdb_client)
    bleibt dessen Text, wie er ist."""
    schluessel = "grund_" + str(ergebnis.get("skipped_key") or "")
    if schluessel in _T["de"]:
        return _t(schluessel, sprache)
    return str(ergebnis.get("skipped") or "")


def _zone_laden(name: str):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        pass
    try:
        import pytz      # kommt mit APScheduler; hilft, wenn tzdata fehlt
        return pytz.timezone(name)
    except Exception:
        return None


def _zeitzone(name: str | None):
    """Zeitzone zum Namen aus den Einstellungen — nie ein Absturz."""
    tz = _zone_laden(name or _ZONE_RUECKFALL)
    if tz is None:
        log.warning("cron: Zeitzone %r unbekannt, nehme %s", name, _ZONE_RUECKFALL)
        tz = _zone_laden(_ZONE_RUECKFALL) or timezone.utc
    return tz


def _now_iso() -> str:
    """Zeitstempel fuer die Oberflaeche — mit Zeitzonen-Offset.

    Frueher stand hier `datetime.utcnow().isoformat()`, das eine *naive*
    Zeichenkette liefert. Der Browser liest die als Ortszeit und zeigte
    UTC-Zeiten dadurch zwei Stunden zu frueh an. Mit Offset rechnet der
    Browser korrekt um.
    """
    return datetime.now(timezone.utc).isoformat()

# Der Zeitplan steht seit Oktober 2026 in den Einstellungen (settings.py,
# Abschnitt "schedule"), nicht mehr in data/cron_config.json. Die alte Datei
# hat settings.py beim ersten Start einmal uebernommen. Gespeichert wird ueber
# set_schedule (PUT /admin/cron/schedule) oder die Einstellungsseite; in
# beiden Faellen stellt der Beobachter aus start() den Job sofort um.

# Per-job run tracking so the admin UI can show status + last result without
# blocking on long jobs (mit einem Cloud-Modell wenige Minuten, mit einem
# lokalen Modell auf der CPU deutlich laenger).
_run_state: dict[str, dict] = {}
_state_lock = threading.Lock()


def _set_state(name: str, **kw):
    with _state_lock:
        cur = _run_state.get(name, {})
        cur.update(kw)
        _run_state[name] = cur


def get_run_state(name: str) -> dict:
    with _state_lock:
        return dict(_run_state.get(name, {"status": "idle"}))


def all_run_states() -> dict[str, dict]:
    with _state_lock:
        return {k: dict(v) for k, v in _run_state.items()}

# Suggestion chips, je Sprache der Oberflaeche. Der Wortlaut muss ZEICHENGENAU
# mit frontend/src/lib/i18n/chat.js (vorschlag*) uebereinstimmen: die Seite
# nimmt die vorbereitete Antwort nur, wenn die gespeicherte Frage gleich der
# angezeigten ist, sonst wird live gefragt. Der Vorlauf stellt die Fragen in
# der Sprache des jeweiligen Nutzers (texte.fuer_nutzer) und legt die Antwort
# in derselben Sprache ab.
CHAT_SUGGESTIONS: dict[str, list[tuple[str, str]]] = {
    "de": [
        ("recent",        "Was habe ich zuletzt geschaut?"),
        ("sunday_calm",   "Empfiehl mir einen Film für einen ruhigen Sonntagabend"),
        ("dark_thriller", "Ich mag düstere Thriller, was passt?"),
        ("mcu_unseen",    "Welche MCU-Filme habe ich noch nicht gesehen?"),
    ],
    "en": [
        ("recent",        "What did I watch last?"),
        ("sunday_calm",   "Recommend a movie for a quiet Sunday evening"),
        ("dark_thriller", "I like dark thrillers, what fits?"),
        ("mcu_unseen",    "Which MCU movies haven’t I seen yet?"),
    ],
}


def chat_suggestions(sprache: str | None = None) -> list[tuple[str, str]]:
    """Die Vorschlagsfragen in der Sprache ``sprache`` (ohne Angabe die der Anfrage)."""
    return CHAT_SUGGESTIONS.get(texte.normalisieren(sprache) or texte.aktuell(),
                                CHAT_SUGGESTIONS[texte.STANDARD])

_scheduler: BackgroundScheduler | None = None
_beobachter_aktiv = False


# ---------------------------------------------------------------------------
# Voraussetzungen der Jobs
# ---------------------------------------------------------------------------
# Jede Pruefung gibt den Schluessel des Grundes zurueck, warum ein Job nicht
# laufen kann (Texte: _T "grund_<schluessel>"), oder None. Gelesen wird bei
# jedem Lauf: die Einstellungen koennen sich zwischen zwei Laeufen geaendert
# haben.

def _ohne_jellyfin() -> str | None:
    return None if settings.ist_eingerichtet() else "nicht_eingerichtet"


def _ohne_einbettung() -> str | None:
    v = anbieter.verbindung(settings.get("embedding"))
    if v and anbieter.einbettung_modell() and (anbieter.preset(v.anbieter) or {}).get("einbettung"):
        return None
    return "kein_einbettungsmodell"


def _ohne_sprachmodell() -> str | None:
    # active_model() nennt Haupt- ODER Rueckfallmodell — genau die beiden,
    # auf die llm_client.stream zurueckgreift. Ist nur der Rueckfall
    # eingetragen, laeuft der Job also trotzdem.
    return None if llm_client.active_model() else "kein_sprachmodell"


def _braucht(*pruefungen: Callable[[], Optional[str]]):
    """Job nur ausfuehren, wenn alle Voraussetzungen erfuellt sind; sonst
    ``{"skipped": grund, "skipped_key": schluessel}`` zurueckgeben. ``grund``
    ist der deutsche Text (Log, Auswertung), der Schluessel erlaubt der
    Oberflaeche, ihn in ihrer Sprache zu bauen (grund_text). Kein Fehler,
    denn es ist nichts kaputt — es ist nur noch nicht eingerichtet."""
    def deco(fn: Callable[[], dict]) -> Callable[[], dict]:
        @functools.wraps(fn)
        def job() -> dict:
            for pruefe in pruefungen:
                schluessel = pruefe()
                if schluessel:
                    grund = _t("grund_" + schluessel, "de")
                    log.info("cron: %s uebersprungen — %s", fn.__name__, grund)
                    return {"skipped": grund, "skipped_key": schluessel}
            return fn()
        return job
    return deco


# ---------------------------------------------------------------------------
# Individual jobs
# ---------------------------------------------------------------------------

@_braucht(_ohne_jellyfin)
def job_sync_library() -> dict:
    from sync import sync_library
    log.info("cron: sync_library ...")
    return sync_library()


@_braucht(_ohne_jellyfin)
def job_sync_series_library() -> dict:
    from sync import sync_series_library
    log.info("cron: sync_series_library ...")
    return sync_series_library()


@_braucht(_ohne_jellyfin)
def job_sync_episodes() -> dict:
    from sync import sync_all_episodes
    log.info("cron: sync_all_episodes ...")
    return sync_all_episodes()


@_braucht(_ohne_jellyfin)
def job_sync_series_progress_all() -> dict:
    from sync import sync_series_progress
    with get_session() as s:
        users = s.exec(select(User)).all()
    out = {}
    for u in users:
        try:
            out[u.jellyfin_username] = sync_series_progress(u.id)
        except Exception as e:
            log.warning("cron: series-progress sync for %s failed: %s",
                        u.jellyfin_username, e)
            out[u.jellyfin_username] = {"error": str(e)}
    return out


@_braucht(_ohne_jellyfin)
def job_sync_watch_state_all() -> dict:
    from sync import sync_watch_state
    with get_session() as s:
        users = s.exec(select(User)).all()
    out = {}
    for u in users:
        try:
            out[u.jellyfin_username] = sync_watch_state(u.id)
        except Exception as e:
            log.warning("cron: watch-state sync for %s failed: %s", u.jellyfin_username, e)
            out[u.jellyfin_username] = {"error": str(e)}
    return out


@_braucht(_ohne_einbettung)
def job_embed_missing() -> dict:
    from embeddings import embed_all
    log.info("cron: embed_all(force=False) ...")
    return embed_all(force=False)


def job_daily_recommendations() -> dict:
    """Pre-compute today's daily rec + LLM justification for every user."""
    from recommendations import daily_for, explain_daily, invalidate_user_cache
    with get_session() as s:
        users = s.exec(select(User)).all()
    out = {}
    for u in users:
        try:
            # Der Lauf hat keine Anfrage: Sprache des Nutzers ausdruecklich
            # angeben, sonst entstuende der Text in der Vorgabesprache.
            sprache = texte.fuer_nutzer(u)
            # Force a fresh compute (ignore yesterday's cache under today's key)
            invalidate_user_cache(u.id)
            # curate=True: KI-Kurator waehlt (Hauptmodell mit grossem Kontext,
            # llm_client.grosser_kontext). Ohne ein solches Modell oder bei
            # unbrauchbarer Antwort bleibt die mechanische Auswahl — deshalb
            # laeuft dieser Job auch ganz ohne Sprachmodell.
            daily = daily_for(u.id, use_cache=False, curate=True, sprache=sprache)
            # Begruendung nur noch nachholen, wenn der Kurator keine lieferte
            # (explain_daily gibt eine vorhandene sofort zurueck).
            llm = explain_daily(u.id, sprache)
            out[u.jellyfin_username] = {
                "film_of_day": (daily.get("film_of_day") or {}).get("name"),
                "curated": bool(daily.get("curated")),
                "curate_error": daily.get("curate_error"),
                "llm_ok": bool(llm.get("llm_reason")),
            }
        except Exception as e:
            log.warning("cron: daily_for(%s) failed: %s", u.jellyfin_username, e)
            out[u.jellyfin_username] = {"error": str(e)}
    return out


@_braucht(_ohne_sprachmodell)
def job_preload_chat_suggestions() -> dict:
    """For each user, run every default chip-question through the LLM and
    store the final answer in ChatPreload. Clicking the chip then renders
    instantly from cache instead of waiting 60-120 s."""
    from chat_engine import stream_chat, looks_like_context_dump
    with get_session() as s:
        users = s.exec(select(User)).all()

    def _drain(user_id: int, prompt: str, sprache: str) -> str:
        chunks: list[str] = []
        for chunk in stream_chat(user_id, prompt, history=None, sprache=sprache):
            chunks.append(chunk)
        return "".join(chunks).strip()

    out: dict[str, dict] = {}
    for u in users:
        per_user: dict[str, str] = {}
        # Frage UND Antwort in der Sprache dieses Nutzers (der Faden kennt
        # keine Anfrage). Wechselt er die Sprache, passt die gespeicherte
        # Frage nicht mehr zu den Knoepfen der Seite; sie fragt dann live,
        # bis der naechste Lauf nachzieht.
        sprache = texte.fuer_nutzer(u)
        for key, prompt in chat_suggestions(sprache):
            try:
                # Drain the generator — we don't need the token stream here,
                # just the final text.
                answer = _drain(u.id, prompt, sprache)
                retried = False
                # stream_chat meldet Fehler als Text ("[Fehler: ...]") statt
                # zu werfen. So eine Antwort gehoert nicht 24 h lang hinter
                # einen Vorschlagsknopf; der Stand von gestern bleibt stehen.
                if "\n[Fehler:" in "\n" + answer:
                    per_user[key] = "error: " + answer.rsplit("[Fehler:", 1)[1].strip(" ]")[:200]
                    continue
                # Sicherheitsnetz: Hermes3 gibt gelegentlich den Bibliotheks-
                # block woertlich zurueck statt zu formulieren. Einmal neu
                # versuchen, statt eine unbrauchbare Antwort 24h lang
                # auszuliefern.
                if answer and looks_like_context_dump(answer):
                    log.warning("cron: preload %s/%s sah aus wie ein Kontext-Dump "
                                "— einmaliger Neuversuch", u.jellyfin_username, key)
                    retry = _drain(u.id, prompt, sprache)
                    retried = True
                    if (retry and not looks_like_context_dump(retry)
                            and "\n[Fehler:" not in "\n" + retry):
                        answer = retry
                    else:
                        per_user[key] = "verworfen (Kontext-Dump)"
                        continue
                if not answer:
                    per_user[key] = "empty"
                    continue
                with get_session() as s2:
                    row = s2.get(ChatPreload, (u.id, key))
                    if row:
                        row.prompt = prompt
                        row.answer = answer
                        row.created_at = datetime.utcnow()
                    else:
                        s2.add(ChatPreload(
                            user_id=u.id, prompt_key=key, prompt=prompt,
                            answer=answer, created_at=datetime.utcnow(),
                        ))
                    s2.commit()
                per_user[key] = (f"ok ({len(answer)} chars"
                                 f"{', 2. Versuch' if retried else ''})")
            except Exception as e:
                log.warning("cron: preload %s/%s failed: %s", u.jellyfin_username, key, e)
                per_user[key] = f"error: {e}"
        out[u.jellyfin_username] = per_user
    return out


@_braucht(_ohne_einbettung)
def job_embed_user_profiles() -> dict:
    """Compute and store per-user taste-profile vectors for filmdetail match-score."""
    from user_profile import build_profile, profile_to_query_text
    from embeddings import embed_user_profile
    with get_session() as s:
        users = s.exec(select(User)).all()
    out = {}
    for u in users:
        try:
            p = build_profile(u.id)
            q = profile_to_query_text(p)
            ok = embed_user_profile(u.id, q)
            out[u.jellyfin_username] = "ok" if ok else "skipped"
        except Exception as e:
            log.warning("cron: embed_user_profile(%s) failed: %s", u.jellyfin_username, e)
            out[u.jellyfin_username] = f"error: {e}"
    return out


def job_warm_similarities() -> dict:
    """Passungswerte vorab berechnen, damit der erste Seitenaufruf des Tages
    nicht warten muss. Laeuft zuletzt: nach dem Profil-Job und dem Einbetten
    neuer Filme ist der Speicher verworfen, und danach schreibt kein Job mehr
    Vektoren."""
    from embeddings import aehnlichkeiten_vorwaermen
    with get_session() as s:
        ids = [u.id for u in s.exec(select(User)).all()]
    return {str(uid): n for uid, n in aehnlichkeiten_vorwaermen(ids).items()}


def job_missing_suggestions() -> dict:
    """„Fehlt in deiner Sammlung" je Nutzer — nur wenn der Stand aelter als
    6 Tage oder noch nicht kuratiert ist."""
    from missing import compute, is_stale
    with get_session() as s:
        users = s.exec(select(User)).all()
    out = {}
    for u in users:
        try:
            sprache = texte.fuer_nutzer(u)
            if not is_stale(u.id, sprache=sprache):
                out[u.jellyfin_username] = "aktuell"
                continue
            r = compute(u.id, sprache)
            # Als Zahlen statt Text: _summarise baut die Zeile in der Sprache
            # dessen, der die Jobliste abruft.
            out[u.jellyfin_username] = {"titel": len(r["items"]), "kuratiert": r["curated"]}
        except Exception as e:
            log.warning("cron: missing_suggestions(%s) failed: %s", u.jellyfin_username, e)
            out[u.jellyfin_username] = f"error: {e}"
    return out


def job_tmdb_refresh() -> dict:
    """TMDB-„Wer das mochte, mochte auch"-Listen fuer alle Signal-Titel nachladen."""
    from tmdb_client import refresh_seeds
    return refresh_seeds()


def job_weekly_series() -> dict:
    """Recompute the weekly-series cache for every user. Cheap (single vector
    search each), reuses ISO-week cache key."""
    from recommendations import weekly_series_pick
    with get_session() as s:
        users = s.exec(select(User)).all()
    out = {}
    for u in users:
        try:
            r = weekly_series_pick(u.id, texte.fuer_nutzer(u))
            ser = (r.get("series") or {})
            out[u.jellyfin_username] = ser.get("name") or "—"
        except Exception as e:
            log.warning("cron: weekly-series for %s failed: %s",
                        u.jellyfin_username, e)
            out[u.jellyfin_username] = f"error: {e}"
    return out


def job_prune_chat_history() -> dict:
    """Drop ChatMessage rows older than 24 hours."""
    cutoff = datetime.utcnow() - timedelta(hours=24)
    with get_session() as s:
        rows = s.exec(
            select(ChatMessage).where(ChatMessage.created_at < cutoff)
        ).all()
        for r in rows:
            s.delete(r)
        s.commit()
    return {"pruned": len(rows)}


def _tmdb_aufbewahrung() -> dict:
    """TMDB-Daten ueber der Aufbewahrungsgrenze loeschen. Wirft nie.

    TMDB erlaubt, seine Daten hoechstens 6 Monate zu speichern. Das gilt auch
    fuer eine Installation, deren taegliche Kette im Admin-Bereich
    abgeschaltet ist - deshalb plant start() diese Funktion zusaetzlich als
    eigenen Tagesjob ein, unabhaengig vom Zeitplan der Kette.
    """
    try:
        import tmdb_client
        weg = tmdb_client.aufraeumen()
    except Exception as e:
        log.warning("cron: TMDB-Daten aufraeumen fehlgeschlagen: %s", e)
        return {}
    if any(weg.values()):
        log.info("cron: TMDB-Daten ueber der Aufbewahrungsgrenze geloescht: %s", weg)
    return weg


def job_prune_stale() -> dict:
    """Raeumt Karteileichen weg, die der Sync frueher hinterlassen hat.

    Betrifft drei Sorten:
      * WatchState/SeriesProgress ohne zugehoerigen Film bzw. Serie — entstanden,
        weil das Loeschen im Sync nicht kaskadierte (inzwischen behoben, dieser
        Job raeumt den Altbestand und faengt kuenftige Faelle ab).
      * Episoden ohne Serie.
      * Abgelaufene Sitzungen; die wurden bislang nie geloescht (zuletzt 34 von 38).
      * Vektoren ohne Datenbankzeile in der Sammlung des aktuellen
        Einbettungsmodells — der Sync loescht die Zeile, nicht den Vektor
        (siehe verwaiste_vektoren_entfernen in embeddings.py).
    """
    from database import (Movie, Series, Episode, WatchState, SeriesProgress,
                          UserSession, Watchlist, NotInterested)

    with get_session() as s:
        movie_ids = {m.id for m in s.exec(select(Movie)).all()}
        series_ids = {x.id for x in s.exec(select(Series)).all()}
        known_ids = movie_ids | series_ids

        ws_gone = [w for w in s.exec(select(WatchState)).all()
                   if w.movie_id not in movie_ids]
        sp_gone = [p for p in s.exec(select(SeriesProgress)).all()
                   if p.series_id not in series_ids]
        ep_gone = [e for e in s.exec(select(Episode)).all()
                   if e.series_id not in series_ids]
        sess_gone = [x for x in s.exec(select(UserSession)).all()
                     if x.expires_at < datetime.utcnow()]
        # Merkliste zeigt bewusst ohne Fremdschluessel auf Filme *und* Serien —
        # Waisen entstehen, wenn ein Titel aus Jellyfin verschwindet.
        wl_gone = [w for w in s.exec(select(Watchlist)).all()
                   if w.item_id not in known_ids]
        # Abgelaufene "nicht interessiert"-Sperren wirken ohnehin nicht mehr.
        ni_gone = [n for n in s.exec(select(NotInterested)).all()
                   if n.until < datetime.utcnow() or n.item_id not in known_ids]

        for row in (*ws_gone, *sp_gone, *ep_gone, *sess_gone, *wl_gone, *ni_gone):
            s.delete(row)
        s.commit()

    # Die Vektoren erst NACH dem Commit: Episoden, deren Serie gerade in
    # diesem Lauf verschwunden ist, sind dann schon aus der Datenbank weg und
    # verlieren im selben Durchgang auch ihren Vektor.
    # Eigenes try/except, weil der Import von embeddings den Vektorspeicher
    # mitbringt. Faellt der aus, soll das aufgeraeumte Datenbank-Ergebnis
    # trotzdem zurueckkommen statt den ganzen Job als Fehler zu melden.
    try:
        from embeddings import verwaiste_vektoren_entfernen
        vektoren_weg = verwaiste_vektoren_entfernen()
    except Exception as e:
        log.warning("cron: prune_stale — Vektoren aufraeumen fehlgeschlagen: %s", e)
        vektoren_weg = 0

    # TMDB-Daten: aelter als 170 Tage weg (siehe tmdb_client.aufraeumen).
    # Laeuft zusaetzlich als eigener Tagesjob, falls die Kette abgeschaltet ist.
    tmdb_weg = _tmdb_aufbewahrung()

    # KI-Protokoll: aelter als 180 Tage weg (siehe ki_protokoll.py).
    try:
        import ki_protokoll
        protokoll_weg = ki_protokoll.aufraeumen()
    except Exception as e:
        log.warning("cron: prune_stale — KI-Protokoll aufraeumen fehlgeschlagen: %s", e)
        protokoll_weg = 0

    result = {
        "watchstate": len(ws_gone),
        "seriesprogress": len(sp_gone),
        "episodes": len(ep_gone),
        "sessions": len(sess_gone),
        "watchlist": len(wl_gone),
        "not_interested": len(ni_gone),
        "vektoren": vektoren_weg,
        "ki_protokoll": protokoll_weg,
        **tmdb_weg,
    }
    log.info("cron: prune_stale — %s", result)
    return result


JOBS: dict[str, Callable[[], dict]] = {
    "sync_library":            job_sync_library,
    "sync_series_library":     job_sync_series_library,
    "sync_episodes":           job_sync_episodes,
    "sync_watch_state":        job_sync_watch_state_all,
    "sync_series_progress":    job_sync_series_progress_all,
    "embed_missing":           job_embed_missing,
    "embed_user_profiles":     job_embed_user_profiles,
    "tmdb_refresh":            job_tmdb_refresh,
    "daily_recommendations":   job_daily_recommendations,
    "missing_suggestions":     job_missing_suggestions,
    "weekly_series":           job_weekly_series,
    "preload_chat":            job_preload_chat_suggestions,
    "prune_chat":              job_prune_chat_history,
    "prune_stale":             job_prune_stale,
    "warm_similarities":       job_warm_similarities,
}


def run_job(name: str) -> dict:
    job = JOBS.get(name)
    if not job:
        raise KeyError(f"unknown job: {name}")
    log.info("cron: running job '%s' on demand", name)
    _set_state(name, status="running", started_at=_now_iso(),
               finished_at=None, elapsed_s=None, error=None)
    t0 = datetime.utcnow()
    try:
        result = job()
    except Exception as e:
        elapsed = (datetime.utcnow() - t0).total_seconds()
        log.exception("cron: job '%s' crashed", name)
        _set_state(name, status="error", finished_at=_now_iso(),
                   elapsed_s=round(elapsed, 1), error=str(e))
        raise
    elapsed = (datetime.utcnow() - t0).total_seconds()
    log.info("cron: job '%s' finished in %.1fs", name, elapsed)
    # Das rohe Ergebnis, nicht der Text: die Zusammenfassung baut job_info beim
    # Ausliefern in der Sprache des Abrufenden (siehe _summarise).
    _set_state(name, status="ok", finished_at=_now_iso(),
               elapsed_s=round(elapsed, 1), ergebnis=result)
    return {"job": name, "elapsed_s": round(elapsed, 1), "result": result}


def _summarise(result, sprache: str | None = None) -> str:
    """Short human-readable summary of a job result for the admin UI, in der
    Sprache ``sprache`` (ohne Angabe die der laufenden Anfrage)."""
    try:
        if isinstance(result, dict):
            if "_kette" in result:
                # Zusammenfassung der taeglichen Kette (run_nightly)
                k = result["_kette"]
                text = _t("kette_ok", sprache, ok=k["ok"], n=k["n"])
                if k.get("uebersprungen"):
                    text += _t("kette_uebersprungen", sprache, u=k["uebersprungen"])
                return text
            if "skipped" in result:
                return _t("uebersprungen", sprache, grund=grund_text(result, sprache))
            if result.get("status") == "laeuft_bereits":
                # embed_all laeuft nur einmal gleichzeitig (Sperre in
                # embeddings.py) — z. B. wenn im Admin-Bereich gerade neu
                # eingebettet wird.
                return _t("laeuft_bereits", sprache)
            if "ok" in result and "total" in result:
                return _t("ok_von", sprache, ok=result.get("ok"), total=result.get("total"),
                          failed=result.get("failed", 0))
            if "added" in result:
                return _t("neu_alt_weg", sprache, added=result.get("added", 0),
                          updated=result.get("updated", 0), removed=result.get("removed", 0))
            if "synced" in result:
                return _t("synchronisiert", sprache, synced=result["synced"])
            if "pruned" in result:
                return _t("geloescht", sprache, pruned=result["pruned"])
            # Per-user result
            parts = []
            for k, v in list(result.items())[:4]:
                if isinstance(v, dict):
                    if "error" in v:
                        parts.append(f"{k}: error")
                    elif "titel" in v:
                        parts.append(f"{k}: " + _t("titel_kuratiert", sprache, n=v["titel"],
                                                   kuratiert=v.get("kuratiert")))
                    else:
                        parts.append(f"{k}: ok")
                elif v == "aktuell":
                    parts.append(f"{k}: " + _t("aktuell", sprache))
                else:
                    parts.append(f"{k}: {v}")
            return ", ".join(parts) if parts else "ok"
        return str(result)[:120]
    except Exception:
        return "ok"


def run_job_async(name: str) -> dict:
    """Spawn a job in a daemon thread so the HTTP call returns immediately."""
    if name not in JOBS and name != "all":
        raise KeyError(f"unknown job: {name}")
    current = get_run_state(name)
    if current.get("status") == "running":
        return {"job": name, "status": "already_running",
                "started_at": current.get("started_at")}

    def _runner():
        try:
            if name == "all":
                run_nightly()
            else:
                run_job(name)
        except Exception:
            log.exception("cron: async job %s failed", name)

    # Status VOR dem Start setzen: ein uebersprungener Job ist in
    # Millisekunden fertig. Kaeme "running" erst nach t.start(), koennte es
    # das "ok" des Threads ueberschreiben, und der Job stuende fuer immer
    # auf "laeuft".
    _set_state(name, status="running",
               started_at=_now_iso(),
               finished_at=None, elapsed_s=None, error=None)
    t = threading.Thread(target=_runner, daemon=True, name=f"cron-{name}")
    t.start()
    return {"job": name, "status": "started"}


def run_nightly() -> dict:
    """The full daily chain, in dependency order. Errors in one job don't
    abort the rest — each job is wrapped in try/except.

    Ohne Jellyfin-Verbindung laeuft gar nichts: die Kette beginnt mit dem
    Sync, und alles danach arbeitet mit dessen Ergebnis."""
    if not settings.ist_eingerichtet():
        log.info("cron: taegliche Kette uebersprungen — %s", NICHT_EINGERICHTET)
        jetzt = _now_iso()
        _set_state("all", status="ok", started_at=jetzt, finished_at=jetzt,
                   elapsed_s=0.0, error=None,
                   ergebnis={"skipped": NICHT_EINGERICHTET,
                             "skipped_key": "nicht_eingerichtet"})
        return {"skipped": NICHT_EINGERICHTET}
    log.info("cron: starting daily chain")
    _set_state("all", status="running", started_at=_now_iso(),
               finished_at=None)
    t0 = datetime.utcnow()
    order = [
        "sync_library",
        "sync_series_library",
        "sync_episodes",
        "sync_watch_state",
        "sync_series_progress",
        "embed_missing",
        "embed_user_profiles",
        "tmdb_refresh",
        "daily_recommendations",
        "missing_suggestions",
        "weekly_series",
        "preload_chat",
        "prune_chat",
        "prune_stale",
        "warm_similarities",
    ]
    summary = {}
    for name in order:
        try:
            summary[name] = run_job(name)
        except Exception as e:
            log.exception("cron: job %s crashed", name)
            summary[name] = {"error": str(e)}
    elapsed = (datetime.utcnow() - t0).total_seconds()
    log.info("cron: daily chain done (%.1fs)", elapsed)
    ok = sum(1 for v in summary.values() if "error" not in v)
    # Uebersprungene Jobs zaehlen als ok (nichts ist kaputt), werden aber
    # genannt — sonst saehe "15 / 15 Jobs ok" nach einem vollen Lauf aus,
    # obwohl ohne Sprachmodell gar keine Chat-Vorschlaege entstanden sind.
    uebersprungen = sum(1 for v in summary.values()
                        if isinstance(v.get("result"), dict) and "skipped" in v["result"])
    _set_state("all", status="ok", finished_at=_now_iso(),
               elapsed_s=round(elapsed, 1),
               ergebnis={"_kette": {"ok": ok, "n": len(order), "uebersprungen": uebersprungen}})
    # Persist last-run timestamp for UI display
    try:
        (DATA_DIR / "last_cron_summary.json").write_text(
            json.dumps({"finished_at": _now_iso(),
                        "summary": summary}, default=str, indent=2))
    except Exception:
        pass
    return summary


# ---------------------------------------------------------------------------
# Scheduler lifecycle
# ---------------------------------------------------------------------------

def _ensure_job(cfg: dict) -> None:
    """(Re)install the APScheduler job based on the ``schedule`` settings."""
    # Kein assert mehr: der Beobachter aus start() kann auch nach stop()
    # noch feuern (Einstellung gespeichert, Scheduler schon beendet).
    if _scheduler is None:
        return
    # Remove the existing job (if any) and re-add with fresh trigger
    try:
        _scheduler.remove_job("daily")
    except Exception:
        pass
    if not cfg.get("enabled", True):
        log.info("cron: scheduler disabled by config")
        return
    zone_name = cfg.get("timezone") or _ZONE_RUECKFALL
    # Die Zeitzone MUSS explizit an den Trigger. Ein ohne `timezone` gebauter
    # CronTrigger nimmt nicht die des Schedulers, sondern die lokale
    # Systemzeit — und der Container laeuft auf UTC. Dadurch lief die
    # naechtliche Kette bislang um 10:30 UTC, also 12:30 Ortszeit, obwohl in
    # der Oberflaeche "10:30 Europe/Berlin" stand. Die Zone kommt jetzt aus
    # den Einstellungen und wird bei jedem Neuanlegen frisch gelesen.
    _scheduler.add_job(
        run_nightly,
        trigger=CronTrigger(hour=int(cfg["hour"]), minute=int(cfg["minute"]),
                            timezone=_zeitzone(zone_name)),
        id="daily",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    log.info("cron: daily chain scheduled for %02d:%02d %s",
             int(cfg["hour"]), int(cfg["minute"]), zone_name)


def _zeitplan_geaendert(alt: dict, neu: dict) -> None:
    """Beobachter fuer settings "schedule": Job sofort umstellen."""
    _ensure_job(neu)


def start() -> None:
    global _scheduler, _beobachter_aktiv
    if _scheduler is not None:
        return
    cfg = settings.get("schedule")
    # Die Zone des Schedulers gilt nur fuer Jobs ohne eigene Zone; der
    # Tagesjob bekommt sie trotzdem am Trigger (siehe _ensure_job).
    _scheduler = BackgroundScheduler(timezone=_zeitzone(cfg.get("timezone")))
    _ensure_job(cfg)
    # Eigener Tagesjob fuer die TMDB-Aufbewahrung: laeuft auch, wenn die Kette
    # abgeschaltet ist (siehe _tmdb_aufbewahrung). Erster Lauf wenige Minuten
    # nach dem Start, damit ein lange gestoppter Container Altes sofort los wird.
    _scheduler.add_job(
        _tmdb_aufbewahrung,
        trigger=IntervalTrigger(hours=24),
        id="tmdb_aufbewahrung",
        next_run_time=datetime.now(_scheduler.timezone) + timedelta(minutes=5),
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    _scheduler.start()
    if not _beobachter_aktiv:
        # Einmal registrieren, auch wenn start() nach stop() erneut laeuft —
        # sonst stellte jede Aenderung den Job mehrfach um. Der Beobachter
        # greift egal, wo gespeichert wurde: /admin/cron/schedule oder die
        # Einstellungsseite (PUT /admin/settings/schedule).
        settings.beobachten("schedule", _zeitplan_geaendert)
        _beobachter_aktiv = True


def stop() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


# ---------------------------------------------------------------------------
# Admin API helpers
# ---------------------------------------------------------------------------

def get_schedule() -> dict:
    cfg = settings.get("schedule")
    next_run: Optional[str] = None
    if _scheduler is not None:
        try:
            job = _scheduler.get_job("daily")
            if job and job.next_run_time:
                next_run = job.next_run_time.isoformat()
        except Exception:
            pass
    return {"hour": cfg["hour"], "minute": cfg["minute"], "enabled": cfg["enabled"],
            "timezone": cfg["timezone"], "next_run_at": next_run}


def set_schedule(hour: int, minute: int, enabled: bool,
                 timezone: Optional[str] = None) -> dict:
    """Zeitplan speichern; ``timezone=None`` (oder leer) behaelt die Zone.

    Bereiche und Zone prueft settings.speichern — ein ungueltiger Wert kommt
    als settings.EinstellungFehler (ein ValueError) mit deutscher Meldung
    zurueck, main.py macht daraus 400. Den Job stellt der Beobachter um,
    den start() registriert hat."""
    werte: dict = {"hour": hour, "minute": minute, "enabled": bool(enabled)}
    if timezone and str(timezone).strip():
        werte["timezone"] = str(timezone).strip()
    settings.speichern("schedule", werte)
    return get_schedule()


def _letztes_ergebnis(state: dict, sprache: str) -> Optional[str]:
    """Zusammenfassung des letzten Laufs, in der Sprache des Abrufenden gebaut."""
    if "ergebnis" in state:
        return _summarise(state["ergebnis"], sprache)
    return state.get("last_result")


def _ist_uebersprungen(state: dict) -> bool:
    ergebnis = state.get("ergebnis")
    return isinstance(ergebnis, dict) and ("skipped" in ergebnis)


def job_info() -> list[dict]:
    """Combine job names, last run state, and human description for the UI.

    Texte in der Sprache der Anfrage (texte.aktuell). ``skipped``: der letzte
    Lauf wurde uebersprungen (fehlendes Modell) — fuer Anzeigen, die das nicht
    am Wortlaut von ``last_result`` erkennen sollen, der je Sprache anders ist.
    """
    sprache = texte.aktuell()

    def beschreibung(name: str) -> str:
        schluessel = "job_" + name
        return _t(schluessel, sprache) if schluessel in _T["de"] else ""

    out = []
    for name in JOBS.keys():
        state = get_run_state(name)
        out.append({
            "name": name,
            "description": beschreibung(name),
            "status": state.get("status", "idle"),
            "started_at": state.get("started_at"),
            "finished_at": state.get("finished_at"),
            "elapsed_s": state.get("elapsed_s"),
            "last_result": _letztes_ergebnis(state, sprache),
            "skipped": _ist_uebersprungen(state),
            "error": state.get("error"),
        })
    # Also surface the "run all" meta-entry
    all_state = get_run_state("all")
    out.insert(0, {
        "name": "all",
        "description": beschreibung("all"),
        "status": all_state.get("status", "idle"),
        "started_at": all_state.get("started_at"),
        "finished_at": all_state.get("finished_at"),
        "elapsed_s": all_state.get("elapsed_s"),
        "last_result": _letztes_ergebnis(all_state, sprache),
        "skipped": _ist_uebersprungen(all_state),
        "error": all_state.get("error"),
    })
    return out
