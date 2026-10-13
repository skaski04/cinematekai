"""CinematekAI sync — library + per-user watch state."""
import logging
from datetime import datetime
from sqlmodel import select
from datetime import timedelta
from database import (
    Movie, Series, SeriesProgress, Episode, Collection, WatchState, SyncState,
    User, UserSession, get_session,
)
from jellyfin import (
    fetch_all_movies, normalize, extract_user_data,
    fetch_all_collections, fetch_collection_movie_ids,
    fetch_user_items,
    fetch_all_series, normalize_series, fetch_series_episodes,
    fetch_user_series_data, fetch_user_next_up,
    sprachen_aus_streams,
)
import sichtbarkeit

log = logging.getLogger("cinematekai.sync")


def _sichtbarkeit_auffrischen(media_type: str) -> dict:
    """Nach dem Bibliotheks-Sync die sichtbaren Mengen aller Personen
    nachziehen (siehe sichtbarkeit.auffrischen), damit gerade aufgenommene
    Titel nicht bis zum naechsten eigenen Abgleich fuer alle verborgen sind.

    Ein Fehler hier laesst den Bibliotheks-Sync nicht scheitern: dessen Daten
    sind schon geschrieben, und jede Person behaelt ihre bisherige Menge."""
    try:
        return sichtbarkeit.auffrischen(media_type)
    except Exception as e:
        log.warning("Sichtbarkeit (%s) nach dem Bibliotheks-Sync nicht aufgefrischt: %s",
                    media_type, e)
        return {"ok": 0, "fehler": -1}


def sync_library() -> dict:
    """Sync all movies + collections from Jellyfin into SQLite (shared data)."""
    # ---- Collections first ----
    raw_cols = fetch_all_collections()
    col_map: dict[str, str] = {}  # collection_id -> name
    movie_to_cols: dict[str, list[str]] = {}  # movie_id -> [collection_id, ...]

    with get_session() as s:
        for rc in raw_cols:
            cid = rc["Id"]
            cname = rc.get("Name", "")
            col_map[cid] = cname
            col = s.get(Collection, cid)
            if not col:
                col = Collection(id=cid, name=cname, overview=rc.get("Overview") or "")
                s.add(col)
            else:
                col.name = cname
                col.overview = rc.get("Overview") or ""
            # Fetch movies in this collection
            try:
                mids = fetch_collection_movie_ids(cid)
            except Exception as e:
                log.warning("Failed to fetch movies for collection %s: %s", cid, e)
                mids = []
            for mid in mids:
                movie_to_cols.setdefault(mid, []).append(cid)
            col.movie_count = len(mids)
        s.commit()

    log.info("Synced %d collections", len(col_map))

    # ---- Movies ----
    items = fetch_all_movies()
    added = updated = 0
    # Zaehlt, fuer wie viele Filme Jellyfin ueberhaupt eine Tonsprache kennt.
    # Ohne diese Zahl liesse sich nach einem Sync nicht unterscheiden, ob die
    # Spalte leer ist, weil nichts getaggt war, oder weil das Feld fehlte.
    mit_tonsprache = 0
    seen_ids: set[str] = set()

    with get_session() as s:
        for raw in items:
            data = normalize(raw)
            mid = data["id"]
            seen_ids.add(mid)
            if data.get("audio_languages"):
                mit_tonsprache += 1

            # Attach collection info
            cids = movie_to_cols.get(mid, [])
            data["collection_ids"] = ",".join(cids)
            data["collection_names"] = ",".join(col_map.get(c, "") for c in cids)

            existing = s.get(Movie, mid)
            if existing is None:
                m = Movie(**data)
                s.add(m)
                added += 1
            else:
                changed_embed = (
                    existing.name != data["name"]
                    or existing.overview != data["overview"]
                    or existing.genres != data["genres"]
                    or existing.directors != data["directors"]
                    or existing.actors != data["actors"]
                    or existing.characters != data["characters"]
                    or existing.collection_names != data["collection_names"]
                )
                for k, v in data.items():
                    setattr(existing, k, v)
                if changed_embed:
                    existing.embedded = False
                existing.updated_at = datetime.utcnow()
                updated += 1

        # Entfernte Filme loeschen — und die daran haengenden WatchState-Zeilen
        # gleich mit. Ohne diese Kaskade wuchsen die Waisen monoton mit: bei der
        # Bestandsaufnahme zeigten 1392 von 2046 watchstate-Zeilen auf Filme,
        # die es laengst nicht mehr gab.
        removed = 0
        removed_states = 0
        for m in s.exec(select(Movie)).all():
            if m.id not in seen_ids:
                for ws in s.exec(
                    select(WatchState).where(WatchState.movie_id == m.id)
                ).all():
                    s.delete(ws)
                    removed_states += 1
                s.delete(m)
                removed += 1
        if removed_states:
            log.info("Library sync: %d verwaiste WatchState-Zeilen mit entfernt",
                     removed_states)

        state = s.get(SyncState, 1) or SyncState(id=1)
        state.last_sync_at = datetime.utcnow()
        state.last_movie_count = len(seen_ids)
        s.add(state)
        s.commit()

    log.info("Library sync: +%d ~%d -%d = %d (%d mit Tonsprache)",
             added, updated, removed, len(seen_ids), mit_tonsprache)
    sicht = _sichtbarkeit_auffrischen(sichtbarkeit.FILM)
    return {"added": added, "updated": updated, "removed": removed, "total": len(seen_ids),
            "collections": len(col_map), "mit_tonsprache": mit_tonsprache,
            "sichtbarkeit": sicht}


def sync_series_library() -> dict:
    """Sync all Series from Jellyfin into SQLite. Same shape as sync_library
    but for the Series-Tabelle. No collections/box-sets — Jellyfin doesn't
    expose series collections in the same way and Phase TV-A doesn't need
    them yet."""
    items = fetch_all_series()
    added = updated = 0
    seen_ids: set[str] = set()

    with get_session() as s:
        for raw in items:
            data = normalize_series(raw)
            sid = data["id"]
            seen_ids.add(sid)
            existing = s.get(Series, sid)
            if existing is None:
                s.add(Series(**data))
                added += 1
            else:
                changed_embed = (
                    existing.name != data["name"]
                    or existing.overview != data["overview"]
                    or existing.genres != data["genres"]
                    or existing.creators != data["creators"]
                    or existing.actors != data["actors"]
                    or existing.characters != data["characters"]
                    or existing.status != data["status"]
                )
                for k, v in data.items():
                    # Staffel- und Folgenzahl stammen aus der eigenen Zaehlung
                    # (recount_series_episodes). Jellyfins Werte sind falsch und
                    # duerfen sie nicht ueberschreiben; sie dienen nur als
                    # Startwert fuer neu aufgenommene Serien.
                    if k in ("episode_count", "season_count") and existing.episode_count:
                        continue
                    setattr(existing, k, v)
                if changed_embed:
                    existing.embedded = False
                existing.updated_at = datetime.utcnow()
                updated += 1

        # Entfernte Serien loeschen — samt Episoden und Fortschritts-Zeilen,
        # die sonst als Waisen zurueckbleiben (zuletzt 878 Episoden und
        # 24 seriesprogress-Eintraege ohne zugehoerige Serie).
        removed = 0
        removed_eps = removed_progress = 0
        for ser in s.exec(select(Series)).all():
            if ser.id not in seen_ids:
                for ep in s.exec(
                    select(Episode).where(Episode.series_id == ser.id)
                ).all():
                    s.delete(ep)
                    removed_eps += 1
                for sp in s.exec(
                    select(SeriesProgress).where(SeriesProgress.series_id == ser.id)
                ).all():
                    s.delete(sp)
                    removed_progress += 1
                s.delete(ser)
                removed += 1
        if removed_eps or removed_progress:
            log.info("Series sync: %d Episoden und %d Fortschritts-Zeilen mit entfernt",
                     removed_eps, removed_progress)
        s.commit()

    log.info("Series sync: +%d ~%d -%d = %d", added, updated, removed, len(seen_ids))
    sicht = _sichtbarkeit_auffrischen(sichtbarkeit.SERIE)
    return {"added": added, "updated": updated, "removed": removed,
            "total": len(seen_ids), "sichtbarkeit": sicht}


def sync_episodes_for_series(series_id: str) -> dict:
    """Sync episode metadata for a single series. Marks episodes whose
    plot/title changed as un-embedded.

    Traegt nebenbei die Ton- und Untertitelsprachen der Serie zusammen: der
    Serien-Eintrag in Jellyfin hat keine Mediendatei und damit keine
    Sprachangaben, die Folgen haben sie.
    """
    items = fetch_series_episodes(series_id, mit_streams=True)
    added = updated = 0
    seen_ids: set[str] = set()
    with get_session() as s:
        for it in items:
            eid = it.get("Id")
            if not eid:
                continue
            seen_ids.add(eid)
            ticks = it.get("RunTimeTicks") or 0
            data = {
                "id": eid,
                "series_id": series_id,
                "season": it.get("ParentIndexNumber"),
                "episode": it.get("IndexNumber"),
                "name": it.get("Name") or "",
                "overview": it.get("Overview") or "",
                "runtime_min": int(ticks / 10_000_000 / 60) if ticks else None,
                "air_date": _parse_jf_dt(it.get("PremiereDate")),
            }
            existing = s.get(Episode, eid)
            if existing is None:
                s.add(Episode(**data, embedded=False))
                added += 1
            else:
                changed = (
                    existing.name != data["name"]
                    or existing.overview != data["overview"]
                )
                for k, v in data.items():
                    setattr(existing, k, v)
                if changed:
                    existing.embedded = False
                existing.updated_at = datetime.utcnow()
                updated += 1

        # Drop removed episodes (file deleted from Jellyfin)
        removed = 0
        ep_rows = s.exec(
            select(Episode).where(Episode.series_id == series_id)
        ).all()
        for ep in ep_rows:
            if ep.id not in seen_ids:
                s.delete(ep)
                removed += 1

        # ---- Sprachen der Serie aus den Folgen ----
        # Nur schreiben, wenn wenigstens eine Folge ueberhaupt Stream-Angaben
        # hatte. Bonanza etwa listet 461 Folgen, von denen 429 nur als
        # Platzhalter existieren (LocationType "Virtual", keine Datei); waeren
        # alle Folgen einer Serie solche Platzhalter, wuerde eine leere
        # Vereinigung sonst gute Werte ueberschreiben.
        sprachen = _serien_sprachen(items)
        if sprachen is not None:
            ser = s.get(Series, series_id)
            if ser is not None:
                ser.audio_languages, ser.subtitle_languages = sprachen
                ser.updated_at = datetime.utcnow()
        s.commit()
    return {"series_id": series_id, "added": added, "updated": updated,
            "removed": removed, "total": len(seen_ids),
            "sprachen": (sprachen or ("", ""))[0]}


def _serien_sprachen(episoden: list[dict]) -> tuple[str, str] | None:
    """Ton- und Untertitelsprachen ueber alle Folgen vereinigen.

    ``None`` heisst „keine einzige Folge hatte MediaStreams" — dann wurde
    entweder ohne ``mit_streams`` abgefragt oder die Serie liegt gar nicht als
    Datei vor. In beiden Faellen ist Nichtstun richtiger als eine Null-Antwort.

    Reihenfolge: erste Nennung gewinnt, und Jellyfin liefert die Folgen nach
    Staffel und Nummer. Vorn steht also die Sprache der ersten Folge — nicht
    zwingend die haeufigste. Fuer „gibt es die Serie auf Deutsch?" reicht das,
    fuer „ueberwiegend deutsch" nicht; wer das braucht, muesste je Folge zaehlen.
    """
    gesehen = False
    audio: list[str] = []
    subs: list[str] = []
    for ep in episoden:
        streams = ep.get("MediaStreams")
        if not streams:
            continue
        gesehen = True
        a, u = sprachen_aus_streams(streams)
        for k in (x for x in a.split(",") if x):
            if k not in audio:
                audio.append(k)
        for k in (x for x in u.split(",") if x):
            if k not in subs:
                subs.append(k)
    if not gesehen:
        return None
    return ",".join(audio), ",".join(subs)


def sync_all_episodes() -> dict:
    """Run sync_episodes_for_series for every series in the library."""
    with get_session() as s:
        series_ids = [ser.id for ser in s.exec(select(Series)).all()]
    summary = {"series": len(series_ids), "added": 0, "updated": 0,
               "removed": 0, "total": 0}
    for sid in series_ids:
        r = sync_episodes_for_series(sid)
        summary["added"] += r["added"]
        summary["updated"] += r["updated"]
        summary["removed"] += r["removed"]
        summary["total"] += r["total"]
    summary["recount"] = recount_series_episodes()
    log.info("Episode sync: %s", summary)
    return summary


def recount_series_episodes() -> dict:
    """Staffel- und Folgenzahl aus der Episode-Tabelle neu bestimmen.

    Jellyfins ``RecursiveItemCount`` zaehlte offenbar nur eine Staffel: Bonanza
    stand mit 32 statt 431 Folgen da, Peaky Blinders mit 6 statt 36. Wir haben
    ohnehin jede Folge lokal — also zaehlen wir selbst. Specials (Staffel 0)
    werden getrennt gefuehrt, damit „6 Staffeln · 36 Folgen · 18 Extras"
    moeglich ist statt zweier Zahlen, die Verschiedenes meinen.
    """
    changed = 0
    with get_session() as s:
        per: dict[str, list[Episode]] = {}
        for e in s.exec(select(Episode)).all():
            per.setdefault(e.series_id, []).append(e)
        rows = s.exec(select(Series)).all()
        for ser in rows:
            eps = per.get(ser.id) or []
            if not eps:
                continue
            specials = [e for e in eps if e.season == 0]
            regular = [e for e in eps if e.season != 0]
            seasons = len({e.season for e in regular if e.season is not None})
            new = (len(regular), len(specials), seasons or ser.season_count)
            if (ser.episode_count, ser.special_count, ser.season_count) != new:
                ser.episode_count, ser.special_count, ser.season_count = new
                changed += 1
        s.commit()
    result = {"series": len(rows), "geaendert": changed}
    log.info("Folgenzahlen neu gezaehlt: %s", result)
    return result


def _parse_jf_dt(s):
    if not s:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def sync_series_progress(user_id: int) -> dict:
    """Pull per-user Series UserData + Next-Up info from Jellyfin and write
    SeriesProgress rows. State heuristic:

      not_started   episodes_watched == 0
      completed     episodes_watched == total_episodes  (and total > 0)
      watching      0 < episodes_watched < total AND last_played > 90 days ago
      abandoned     same as watching but last_played > 90 days ago
    """
    from datetime import datetime
    with get_session() as s:
        user = s.get(User, user_id)
        if not user:
            return {"error": "user not found"}
        jf_user_id = user.jellyfin_user_id

    raw_series = fetch_user_series_data(jf_user_id)
    # Dieselbe Antwort ist die Liste ALLER Serien, die Jellyfin diesem Konto
    # zeigt (mit ?userId= wendet es Bibliotheksfreigaben, Altersgrenze und
    # gesperrte Tags selbst an) - also genau die sichtbare Menge. Gespeichert
    # wird sie vor dem Rest, damit ein spaeterer Fehler (NextUp, Folgen) die
    # Rechte nicht auf dem alten Stand laesst.
    sichtbarkeit.speichern(user_id, sichtbarkeit.SERIE, [it.get("Id") for it in raw_series])
    next_up = fetch_user_next_up(jf_user_id)

    with get_session() as s:
        known_series = {x.id for x in s.exec(select(Series)).all()}

    # Folgen je Nutzer selbst zaehlen. Jellyfins RecursiveItemCount und
    # UnplayedItemCount lieferten zu kleine Zahlen (7 vs. Wild: 49 statt 88
    # regulaerer Folgen), wodurch „2 / 49 Folgen" falsch war und die
    # „Weiter oder weg?"-Schwelle (<= 3 gesehene Folgen) auf falscher Basis
    # entschied. Kostet rund 25 Abrufe je Nutzer und Nacht.
    counts: dict[str, tuple[int, int]] = {}
    for it in raw_series:
        sid = it.get("Id")
        if not sid or sid not in known_series:
            continue
        try:
            eps = fetch_series_episodes(sid, jf_user_id=jf_user_id)
        except Exception as e:
            log.warning("Episodenabruf fuer Serie %s fehlgeschlagen: %s", sid, e)
            continue
        regular = [e for e in eps if (e.get("ParentIndexNumber") or 0) != 0]
        watched = sum(1 for e in regular if (e.get("UserData") or {}).get("Played"))
        counts[sid] = (len(regular), watched)

    cutoff = datetime.utcnow() - timedelta(days=90)
    upserted = 0
    state_counts = {"not_started": 0, "watching": 0,
                    "completed": 0, "abandoned": 0}

    with get_session() as s:
        for it in raw_series:
            sid = it.get("Id")
            if not sid or not s.get(Series, sid):
                continue  # series not in our library cache
            ud = it.get("UserData") or {}
            total_eps, watched_eps = counts.get(sid, (0, 0))
            if not total_eps:
                # Rueckfall, wenn der Episodenabruf scheiterte.
                total_eps = int(it.get("RecursiveItemCount") or 0)
                unwatched = int(ud.get("UnplayedItemCount") or total_eps)
                watched_eps = max(0, total_eps - unwatched)
            last_played = _parse_jf_dt(ud.get("LastPlayedDate"))
            is_fav = bool(ud.get("IsFavorite"))

            if total_eps == 0:
                state = "not_started"
            elif watched_eps == 0:
                state = "not_started"
            elif watched_eps >= total_eps:
                state = "completed"
            else:
                if last_played and last_played < cutoff:
                    state = "abandoned"
                else:
                    state = "watching"

            nu = next_up.get(sid) or {}
            nu_id = nu.get("episode_id")
            nu_label = ""
            if nu.get("season") is not None and nu.get("episode") is not None:
                nu_label = f"S{nu['season']}E{nu['episode']}"

            row = s.get(SeriesProgress, (user_id, sid))
            if row is None:
                row = SeriesProgress(user_id=user_id, series_id=sid)
                s.add(row)
            row.episodes_watched = watched_eps
            row.total_episodes = total_eps
            row.next_up_id = nu_id
            row.next_up_label = nu_label
            row.last_played = last_played
            row.is_favorite = is_fav
            row.state = state
            row.updated_at = datetime.utcnow()
            upserted += 1
            state_counts[state] = state_counts.get(state, 0) + 1
        s.commit()

    log.info("Series-progress sync user=%d: %d entries, states=%s",
             user_id, upserted, state_counts)
    return {"user_id": user_id, "synced": upserted, "states": state_counts}


def sync_watch_state(user_id: int) -> dict:
    """Sync watch state for one CinematekAI user from Jellyfin."""
    with get_session() as s:
        user = s.get(User, user_id)
        if not user:
            return {"error": "user not found"}
        jf_user_id = user.jellyfin_user_id

    # Frueher wurde hier eine nicht abgelaufene UserSession verlangt und deren
    # jf_access_token weitergereicht. Das war eine Scheinhuerde: der Token wird
    # in `fetch_user_items` gar nicht benutzt (Parameter heisst dort
    # `_jf_access_token`), die Abfrage laeuft ueber den geteilten Admin-Key.
    # Praktische Folge war, dass der Sync fuer jeden Nutzer ohne frischen Login
    # dauerhaft fehlschlug — ein Konto ohne regelmaessigen Login behielt so
    # ein leeres Profil, und alle seine Empfehlungen basierten auf nichts.
    if not jf_user_id:
        return {"error": "user hat keine jellyfin_user_id"}

    items = fetch_user_items(jf_user_id)
    # fetch_user_items liefert ALLE Filme, die Jellyfin diesem Konto zeigt -
    # gesehene wie ungesehene, mit den Rechten des Kontos (Bibliotheks-
    # freigaben, Altersgrenze, gesperrte Tags). Das ist die sichtbare Menge.
    sichtbarkeit.speichern(user_id, sichtbarkeit.FILM, [raw.get("Id") for raw in items])
    upserted = 0

    with get_session() as s:
        for raw in items:
            movie_id = raw["Id"]
            # Only if movie exists in our cache
            if not s.get(Movie, movie_id):
                continue
            ud = extract_user_data(raw)
            ws = s.get(WatchState, (user_id, movie_id))
            if ws is None:
                ws = WatchState(user_id=user_id, movie_id=movie_id)
                s.add(ws)
            ws.played = ud["played"]
            ws.play_count = ud["play_count"]
            ws.last_played = ud["last_played"]
            ws.user_rating = ud["user_rating"]
            ws.is_favorite = ud["is_favorite"]
            ws.playback_position_ticks = ud.get("playback_position_ticks") or 0
            ws.runtime_ticks = ud.get("runtime_ticks") or 0
            ws.updated_at = datetime.utcnow()
            upserted += 1
        s.commit()

    log.info("Watch-state sync for user %d: %d entries", user_id, upserted)
    return {"user_id": user_id, "synced": upserted}
