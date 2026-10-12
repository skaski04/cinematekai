"""TMDB-Anbindung — „Wer das mochte, mochte auch" als Schwarm-Signal.

Die Embedding-Suche vergleicht nur Inhalte: Genres, Besetzung, Handlungstext.
Sie weiss nicht, dass Menschen, die "Blade Runner" moegen, oft auch "Drive"
moegen — das steht in keinem Handlungstext. TMDB leitet seine
Empfehlungslisten aus dem Verhalten seiner Nutzer ab und liefert genau diese
Information.

Aufbau:
  * ``refresh_seeds()`` laeuft im naechtlichen Cron und holt fuer jeden
    Titel, der fuer irgendeinen Nutzer ein Signal traegt, die TMDB-Liste.
    Zwischengespeichert 30 Tage in ``TmdbRecommendation``.
  * ``collaborative_scores(user_id)`` liest nur den Zwischenspeicher — kein
    Netzwerk im Request-Pfad — und zaehlt, wie oft ein Titel in den Listen
    der Nutzer-Titel auftaucht, gewichtet nach Signalstaerke.

Ohne TMDB-Token (Einstellungen, Abschnitt ``tmdb``, Feld ``bearer``) liefert
alles leere Ergebnisse, der Rest der Empfehlungen laeuft unveraendert weiter.
Der Token wird bei jedem Aufruf frisch gelesen, damit ein im Admin-Bereich
eingetragener oder entfernter Token ohne Neustart gilt.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta

import httpx
from sqlmodel import delete, select

import settings
import texte
from database import (get_session, Movie, Series, WatchState, SeriesProgress,
                      RecommendationCache, TmdbRecommendation, User)

log = logging.getLogger("cinematekai.tmdb")

# Meldungen fuer die Oberflaeche (Verbindungstest, Ergebnis des naechtlichen
# Laufs). Erst beim Ausloesen gebaut, in der Sprache der Anfrage.
_T = {
    "de": {
        "kurzer_schluessel": "Das ist der kurze API-Schlüssel. Gebraucht wird das lange "
                             "„Lese-Zugriffstoken“ (API Read Access Token) aus den "
                             "TMDB-Einstellungen.",
        "nicht_erreichbar": "TMDB ist nicht erreichbar ({fehler}).",
        "http_status": "TMDB antwortet mit HTTP {status}. Versuch es später noch einmal.",
        "kein_token": "kein TMDB-Token eingerichtet (Admin → Einstellungen)",
    },
    "en": {
        "kurzer_schluessel": "This is the short API key. You need the long "
                             "\"API Read Access Token\" from the TMDB settings.",
        "nicht_erreichbar": "TMDB is not reachable ({fehler}).",
        "http_status": "TMDB responded with HTTP {status}. Try again later.",
        "kein_token": "no TMDB token set up (Admin → Settings)",
    },
}


def _text(schluessel: str, **werte) -> str:
    return texte.waehle(_T, schluessel, **werte)

API_BASE = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p"
CACHE_DAYS = 30
# TMDB erlaubt in seinen API-Nutzungsbedingungen (Abschnitt 1.C), Daten
# hoechstens 6 Monate zwischenzuspeichern. 6 Monate sind mindestens 181 Tage;
# 170 lassen Puffer fuer ein paar ausgefallene Laeufe (Container gestoppt,
# Server neu gestartet). Siehe aufraeumen().
AUFBEWAHRUNG_TAGE = 170

# Signalstaerke je Titel (seit 2026-09-15, gleiche Lesart wie user_profile.py):
# Herz = "mag ich"; ohne Herz gesehen = eher okay, aber das Herz wird auch mal
# vergessen — deshalb schwach positiv statt neutral oder negativ.
SEED_FAVORITE = 1.0
SEED_PLAYED = 0.3
SEED_LOW_RATING = -1.0
SEED_ABANDONED = -0.5
ABANDONED_AFTER_DAYS = 30

# Position in der TMDB-Liste zaehlt: Platz 1 voll, Platz 20 etwa halb.
_POSITION_DECAY = 40.0


def _bearer() -> str:
    return (settings.get("tmdb")["bearer"] or "").strip()


def enabled() -> bool:
    return bool(_bearer())


def poster_url(poster_path: str | None, size: str = "w342") -> str | None:
    return f"{IMAGE_BASE}/{size}{poster_path}" if poster_path else None


# ---------------------------------------------------------------------------
# HTTP + Zwischenspeicher
# ---------------------------------------------------------------------------

def _get(path: str, params: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {_bearer()}", "Accept": "application/json"}
    query = {"language": "de-DE", **(params or {})}
    with httpx.Client(timeout=20.0, headers=headers) as c:
        r = c.get(f"{API_BASE}{path}", params=query)
        if r.status_code == 429:          # Rate-Limit: kurz warten, einmal wiederholen
            time.sleep(2.0)
            r = c.get(f"{API_BASE}{path}", params=query)
        r.raise_for_status()
        return r.json()


def token_pruefen(bearer: str) -> bool:
    """``True``, wenn TMDB den Token annimmt (GET /configuration mit 200).

    ``False`` nur, wenn TMDB ihn ausdruecklich ablehnt (401/403) oder keiner
    da ist. Ist TMDB nicht erreichbar oder antwortet anders, kommt ein
    ``ValueError`` mit deutscher Meldung — sonst stuende in der Oberflaeche
    "Token ungueltig", obwohl nur die Verbindung fehlt.
    """
    bearer = (bearer or "").strip()
    if not bearer:
        return False
    # TMDB zeigt in den Kontoeinstellungen zwei Werte: den kurzen API-Schluessel
    # (32 Hex-Zeichen, fuer ?api_key=) und das lange Lese-Zugriffstoken. Als
    # Bearer taugt nur das Token; der Schluessel endet in 401, und "ungueltig"
    # allein hilft beim Verwechseln nicht weiter.
    if len(bearer) == 32 and all(z in "0123456789abcdefABCDEF" for z in bearer):
        raise ValueError(_text("kurzer_schluessel"))
    try:
        with httpx.Client(timeout=10.0) as c:
            r = c.get(f"{API_BASE}/configuration",
                      headers={"Authorization": f"Bearer {bearer}",
                               "Accept": "application/json"})
    except httpx.HTTPError as e:
        raise ValueError(_text("nicht_erreichbar", fehler=str(e) or type(e).__name__)) from e
    if r.status_code == 200:
        return True
    if r.status_code in (401, 403):
        return False
    raise ValueError(_text("http_status", status=r.status_code))


def _compact(x: dict, media_type: str) -> dict:
    date = x.get("release_date") or x.get("first_air_date") or ""
    return {
        "tmdb_id": str(x["id"]),
        "media_type": media_type,
        "title": x.get("title") or x.get("name") or "",
        "original_title": x.get("original_title") or x.get("original_name") or "",
        "year": int(date[:4]) if date[:4].isdigit() else None,
        "poster_path": x.get("poster_path"),
        "overview": (x.get("overview") or "")[:400],
        "vote_average": x.get("vote_average"),
        "vote_count": x.get("vote_count"),
    }


def _cached(tmdb_id: str, media_type: str) -> tuple[list[dict] | None, bool]:
    """(Liste oder None, ist_frisch)"""
    with get_session() as s:
        row = s.get(TmdbRecommendation, (tmdb_id, media_type))
    if not row:
        return None, False
    try:
        items = json.loads(row.payload_json)
    except Exception:
        return None, False
    alter = datetime.utcnow() - row.fetched_at
    # Was die Aufbewahrungsgrenze ueberschritten hat, gilt als nicht vorhanden,
    # auch wenn das taegliche Aufraeumen es noch nicht geloescht hat.
    if alter >= timedelta(days=AUFBEWAHRUNG_TAGE):
        return None, False
    return items, alter < timedelta(days=CACHE_DAYS)


def aufraeumen(tage: int = AUFBEWAHRUNG_TAGE) -> dict:
    """Loescht alles, was aus TMDB stammt oder davon abgeleitet ist und
    aelter als ``tage`` ist (TMDB erlaubt hoechstens 6 Monate).

    Betroffen sind zwei Tabellen:
      * TmdbRecommendation - die TMDB-Listen selbst. Der naechtliche Refresh
        erneuert nur Listen zu aktuellen Signal-Titeln; die uebrigen blieben
        sonst fuer immer liegen.
      * RecommendationCache - die Zwischenspeicher der Empfehlungen. "Fehlt in
        deiner Sammlung" enthaelt Titel, Poster und Kurzinhalte von TMDB, Film
        des Tages und Wochenserie das TMDB-Signal ("Fans von ..."). Gelesen
        werden sie nur wenige Tage, geloescht wurden sie bisher nie. Jede
        Zeile dort ist ein abgeleiteter Zwischenstand und wird beim naechsten
        Lauf neu berechnet; nach 170 Tagen ist keine mehr in Gebrauch.

    Bewertungen, Favoriten, Gesehen-Status und tmdb_id-Verweise (die kommen
    aus Jellyfin, nicht von TMDB) bleiben unberuehrt.
    """
    grenze = datetime.utcnow() - timedelta(days=tage)
    with get_session() as s:
        listen = len(s.exec(select(TmdbRecommendation.source_tmdb_id)
                            .where(TmdbRecommendation.fetched_at < grenze)).all())
        caches = len(s.exec(select(RecommendationCache.key)
                            .where(RecommendationCache.created_at < grenze)).all())
        if listen:
            s.exec(delete(TmdbRecommendation).where(TmdbRecommendation.fetched_at < grenze))
        if caches:
            s.exec(delete(RecommendationCache).where(RecommendationCache.created_at < grenze))
        s.commit()
    return {"tmdb_listen": listen, "empfehlungs_zwischenspeicher": caches}


def fetch_recommendations(tmdb_id: str, media_type: str) -> list[dict] | None:
    """Holt die TMDB-Liste und speichert sie. ``None`` bei Fehler."""
    try:
        data = _get(f"/{media_type}/{tmdb_id}/recommendations")
    except Exception as e:
        log.warning("TMDB %s/%s fehlgeschlagen: %s", media_type, tmdb_id, e)
        return None
    items = [_compact(x, media_type) for x in data.get("results", [])]
    with get_session() as s:
        row = s.get(TmdbRecommendation, (tmdb_id, media_type))
        payload = json.dumps(items, ensure_ascii=False)
        if row:
            row.payload_json = payload
            row.fetched_at = datetime.utcnow()
        else:
            s.add(TmdbRecommendation(source_tmdb_id=tmdb_id, media_type=media_type,
                                     payload_json=payload))
        s.commit()
    return items


# ---------------------------------------------------------------------------
# Signale je Nutzer
# ---------------------------------------------------------------------------

def movie_seed_weight(ws: WatchState, now: datetime | None = None) -> float:
    """Signalstaerke eines Films fuer den Nutzer (0 = kein Signal)."""
    now = now or datetime.utcnow()
    if ws.user_rating is not None and ws.user_rating < 4:
        return SEED_LOW_RATING
    if ws.is_favorite or (ws.user_rating is not None and ws.user_rating >= 7):
        return SEED_FAVORITE
    if ws.played:
        return SEED_PLAYED
    if (ws.playback_position_ticks > 0 and ws.last_played is not None
            and ws.last_played < now - timedelta(days=ABANDONED_AFTER_DAYS)):
        return SEED_ABANDONED
    return 0.0


def _series_seed_weight(sp: SeriesProgress) -> float:
    if sp.is_favorite:
        return SEED_FAVORITE
    if sp.state == "completed":
        return SEED_PLAYED
    return 0.0


def user_seeds(user_id: int) -> list[tuple[str, str, float, str]]:
    """Alle Titel mit Signal: ``(media_type, tmdb_id, gewicht, name)``,
    staerkste zuerst — damit Begruendungen bevorzugt einen Favoriten nennen."""
    now = datetime.utcnow()
    with get_session() as s:
        ws_rows = s.exec(select(WatchState).where(WatchState.user_id == user_id)).all()
        sp_rows = s.exec(select(SeriesProgress).where(SeriesProgress.user_id == user_id)).all()
        movies = {m.id: m for m in s.exec(select(Movie)).all()}
        series = {x.id: x for x in s.exec(select(Series)).all()}

    out: list[tuple[str, str, float, str]] = []
    for ws in ws_rows:
        m = movies.get(ws.movie_id)
        if not m or not m.tmdb_id:
            continue
        w = movie_seed_weight(ws, now)
        if w:
            out.append(("movie", m.tmdb_id, w, m.name))
    for sp in sp_rows:
        ser = series.get(sp.series_id)
        if not ser or not ser.tmdb_id:
            continue
        w = _series_seed_weight(sp)
        if w:
            out.append(("tv", ser.tmdb_id, w, ser.name))
    out.sort(key=lambda t: t[2], reverse=True)
    return out


def collaborative_scores(user_id: int) -> dict:
    """Zaehlt TMDB-Treffer ueber alle Signal-Titel des Nutzers.

    Rueckgabe::

        {"library":  {item_id: {"score": float, "because": [namen]}},
         "external": {"movie:<tmdb_id>": {...TMDB-Daten, "score", "because"}}}

    ``library`` enthaelt Titel, die in Jellyfin vorhanden sind, ``external``
    solche, die fehlen. Gesehene oder ausgeblendete Titel filtert der Aufrufer.
    Liest nur den Zwischenspeicher.
    """
    with get_session() as s:
        lib = {
            "movie": {m.tmdb_id: m.id for m in s.exec(select(Movie)).all() if m.tmdb_id},
            "tv": {x.tmdb_id: x.id for x in s.exec(select(Series)).all() if x.tmdb_id},
        }

    library: dict[str, dict] = {}
    external: dict[str, dict] = {}
    for media_type, tmdb_id, weight, name in user_seeds(user_id):
        recs, _fresh = _cached(tmdb_id, media_type)
        for idx, rec in enumerate(recs or []):
            contrib = weight * (1.0 - idx / _POSITION_DECAY)
            item_id = lib[media_type].get(rec["tmdb_id"])
            if item_id:
                entry = library.setdefault(item_id, {"score": 0.0, "because": []})
            else:
                key = f"{media_type}:{rec['tmdb_id']}"
                entry = external.setdefault(key, {**rec, "score": 0.0, "because": []})
            entry["score"] += contrib
            if weight > 0 and name not in entry["because"]:
                entry["because"].append(name)
    return {"library": library, "external": external}


# ---------------------------------------------------------------------------
# Cron
# ---------------------------------------------------------------------------

def refresh_seeds() -> dict:
    """Fehlende oder abgelaufene TMDB-Listen fuer alle Nutzer nachladen."""
    if not enabled():
        return {"skipped": _text("kein_token")}
    with get_session() as s:
        user_ids = [u.id for u in s.exec(select(User)).all()]

    wanted: set[tuple[str, str]] = set()
    for uid in user_ids:
        wanted |= {(mt, tid) for mt, tid, _w, _n in user_seeds(uid)}

    fetched = fresh = failed = 0
    for media_type, tmdb_id in sorted(wanted):
        _items, is_fresh = _cached(tmdb_id, media_type)
        if is_fresh:
            fresh += 1
            continue
        if fetch_recommendations(tmdb_id, media_type) is None:
            failed += 1
        else:
            fetched += 1
        time.sleep(0.05)      # TMDB erlaubt ~50 Anfragen/s; wir bleiben weit darunter

    result = {"seeds": len(wanted), "fetched": fetched, "fresh": fresh, "failed": failed}
    log.info("TMDB refresh: %s", result)
    return result
