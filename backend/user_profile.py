"""CinematekAI user profile aggregation.

Builds a weighted taste profile from a user's WatchState.

Gewichte seit 2026-09-15 (vorher zaehlte jeder gesehene Film voll positiv):

    Herz (Favorit)            = 2.0   "mag ich"
    gesehen ohne Herz          = 0.3   schwach positiv — das Herz wird auch mal
                                       vergessen, ist aber meist "war okay"
    Bewertung >= 7             = +0.5  zusaetzlich
    Bewertung < 4              = -1.0  statt aller anderen Gewichte
    angefangen, >30 Tage liegen = -0.5  (nicht gesehen, Position > 0)

Vorher lag jeder gesehene Film ohne Herz fast gleichauf mit einem Favoriten
(1.0 gegen 2.5) — das Profil spiegelte damit auch, was nicht gefallen hatte.

Fallback: if the user has 0 watch entries, the profile falls back to
library-wide community favourites (highest community_rating) so the
recommendation pipeline still has something to work with.
"""
from collections import Counter
from datetime import datetime
from typing import Optional
from sqlmodel import select
from database import get_session, Movie, Series, SeriesProgress, WatchState
import sichtbarkeit


W_FAVORITE = 2.0
W_PLAYED = 0.3             # nur wenn KEIN Herz gesetzt ist
W_RATED_HIGH_BONUS = 0.5   # zusaetzlich zu Herz bzw. gesehen
W_RATED_LOW = -1.0         # ersetzt alle anderen Gewichte
W_ABANDONED = -0.5
ABANDONED_AFTER_DAYS = 30

# Series-specific weights — same slots as movies, plus the partial "watching"
# state (no movie equivalent) gets half weight as a softer signal.
W_SERIES_COMPLETED = 1.0   # treats like a played movie
W_SERIES_WATCHING  = 0.5   # half-signal: started but not finished
W_SERIES_ABANDONED = 0.0   # ignored — same as user said: "gleich wie Filme"

# Mindestzahl VERSCHIEDENER Titel, bevor der Profiltext eine Vorliebe
# behauptet. Gleiche Strenge wie REASON_MIN_BELEGE in recommendations.py, aber
# als eigene Konstante: recommendations.py importiert dieses Modul, ein Import
# in der Gegenrichtung waere eine Importschleife.
#
# Anlass (Nachtest 01.10.2026): ein Konto mit NULL gesehenen Filmen und einer
# einzigen Sternebewertung auf "Schindlers Liste" bekam den Satz "Der Nutzer
# hat 0 Filme gesehen und 0 als Favorit markiert." und darunter
# "Lieblings-Genres: Drama, Historie, Kriegsfilm, Komoedie / Bevorzugte
# Regisseure: Steven Spielberg". Das Gewicht allein verraet das nicht: ein
# einzelner Titel hebt alle seine Genres und seine Regie gleichzeitig an.
PROFILE_MIN_BELEGE = 2


def _weight(ws: WatchState, now: Optional[datetime] = None) -> float:
    if ws.user_rating is not None and ws.user_rating < 4:
        return W_RATED_LOW
    w = 0.0
    if ws.is_favorite:
        w = W_FAVORITE
    elif ws.played:
        w = W_PLAYED
    elif ws.playback_position_ticks > 0 and ws.last_played is not None:
        now = now or datetime.utcnow()
        if (now - ws.last_played).days > ABANDONED_AFTER_DAYS:
            return W_ABANDONED
    if ws.user_rating is not None and ws.user_rating >= 7:
        w += W_RATED_HIGH_BONUS
    return w


def _series_weight(sp: "SeriesProgress") -> float:
    """Weight for a SeriesProgress row — mirrors `_weight` for movies but
    routes through the watch-state machine. Favorites add 1.5 regardless of
    state (same as movies)."""
    w = 0.0
    if sp.state == "completed":
        w += W_SERIES_COMPLETED
    elif sp.state == "watching":
        w += W_SERIES_WATCHING
    elif sp.state == "abandoned":
        w += W_SERIES_ABANDONED
    if sp.is_favorite:
        w += W_FAVORITE
    return w


def _csv_tokens(s: str) -> list[str]:
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def build_profile(user_id: int) -> dict:
    """Return a taste profile dict for a user.

    Structure:
        {
          "has_history": bool,
          "has_belege": bool,                 # mind. eine BELEGTE Vorliebe
          "watch_count": int,
          "favorite_count": int,
          "rated_count": int,                 # Titel mit Sternebewertung
          "top_genres":    [(name, score), ...],
          "top_directors": [...],
          "top_actors":    [...],
          "top_collections": [...],
          "avg_runtime":   int | None,
          "avg_year":      int | None,
          "recent_movies": [name, ...],       # last 5 played
          "favorite_movies": [name, ...],     # up to 10
          "signal_belege": {"genre"|"regie"|"besetzung"|"reihe": {name: int}},
          "beleg_titel_gesamt": int,          # Titel mit positivem Gewicht
          "seen_ids":      set[str],
        }
    """
    with get_session() as s:
        ws_rows = s.exec(
            select(WatchState).where(WatchState.user_id == user_id)
        ).all()
        sp_rows = s.exec(
            select(SeriesProgress).where(SeriesProgress.user_id == user_id)
        ).all()
        # Pull all movies + series referenced, plus library for fallback
        all_movies = s.exec(select(Movie)).all()
        all_series = s.exec(select(Series)).all()

    # Nur Titel, die das Konto (noch) sehen darf. Das Profil landet als Text
    # in den Prompts (Chat, Kurator, Begruendung) und nennt Lieblingstitel
    # beim Namen; ein Titel, dessen Freigabe entzogen wurde, wuerde sonst
    # weiter als "dein Lieblingsfilm" auftauchen. Ohne gespeicherte Menge
    # bleibt alles wie bisher.
    sicht = sichtbarkeit.laden(user_id)
    if sicht.aktiv:
        ws_rows = [ws for ws in ws_rows if sicht.film(ws.movie_id)]
        sp_rows = [sp for sp in sp_rows if sicht.serie(sp.series_id)]

    movie_by_id = {m.id: m for m in all_movies}
    series_by_id = {ser.id: ser for ser in all_series}
    seen_ids = {ws.movie_id for ws in ws_rows if ws.played or ws.is_favorite}
    # Also exclude completed/favorite/actively-watched series from "ungesehen"
    # candidate pools — same intent as movies.
    seen_ids |= {sp.series_id for sp in sp_rows
                 if sp.state in ("completed", "watching") or sp.is_favorite}

    genres: Counter = Counter()
    directors: Counter = Counter()
    actors: Counter = Counter()
    collections: Counter = Counter()
    runtimes: list[int] = []
    years: list[int] = []

    favorites: list[tuple[datetime, Movie]] = []
    recent: list[tuple[datetime, Movie]] = []
    fav_count = played_count = 0

    # Belege: auf WIE VIELEN verschiedenen Titeln ein Signal beruht. Nur Titel
    # mit POSITIVEM Gewicht zaehlen — ein abgebrochener oder schlecht
    # bewerteter Film zieht sein Genre im Score nach unten, er belegt keine
    # Vorliebe. Siehe PROFILE_MIN_BELEGE.
    belege: dict[str, dict[str, set[str]]] = {
        "genre": {}, "regie": {}, "besetzung": {}, "reihe": {}}
    beleg_titel: set[str] = set()

    def _beleg(art: str, name: str, titel_id: str) -> None:
        if name:
            belege[art].setdefault(name, set()).add(titel_id)

    # Bewertete Titel zaehlen unabhaengig vom Gewicht: Sterne zwischen 4 und 6
    # ergeben ohne "gesehen" das Gewicht 0 und fallen unten aus der Schleife,
    # fuer den ehrlichen Satz ("1 bewerteter Titel") sind sie aber die Zahl,
    # auf die es ankommt.
    rated_count = sum(1 for ws in ws_rows
                      if ws.user_rating is not None and ws.movie_id in movie_by_id)

    for ws in ws_rows:
        m = movie_by_id.get(ws.movie_id)
        if not m:
            continue
        w = _weight(ws)
        # Frueher wurden negative Gewichte ohne "gesehen" uebersprungen — damit
        # wirkten schlechte Bewertungen und liegen gelassene Filme nie. Jetzt
        # ziehen sie Genre/Regie/Besetzung im Profil nach unten.
        if w == 0:
            continue

        if ws.played:
            played_count += 1
        if ws.is_favorite:
            fav_count += 1
            favorites.append((ws.last_played or ws.updated_at, m))

        if ws.played:
            recent.append((ws.last_played or ws.updated_at, m))

        m_genres = _csv_tokens(m.genres)
        m_dirs = _csv_tokens(m.directors)
        m_actors = _csv_tokens(m.actors)[:5]  # only top-billed, else everyone dominates
        m_cols = _csv_tokens(m.collection_names)
        for g in m_genres:
            genres[g] += w
        for d in m_dirs:
            directors[d] += w
        for a in m_actors:
            actors[a] += w
        for c in m_cols:
            collections[c] += w
        if w > 0:
            beleg_titel.add(m.id)
            for g in m_genres:
                _beleg("genre", g, m.id)
            for d in m_dirs:
                _beleg("regie", d, m.id)
            for a in m_actors:
                _beleg("besetzung", a, m.id)
            for c in m_cols:
                _beleg("reihe", c, m.id)

        if m.runtime_min:
            runtimes.append(m.runtime_min)
        if m.year:
            years.append(m.year)

    # ---- Aggregate Series watch-state into the same counters ----
    series_completed = series_watching = series_fav = 0
    series_recent: list[tuple[datetime, Series]] = []
    series_favorites: list[tuple[datetime, Series]] = []

    for sp in sp_rows:
        ser = series_by_id.get(sp.series_id)
        if not ser:
            continue
        w = _series_weight(sp)
        if w <= 0:
            continue

        if sp.state == "completed":
            series_completed += 1
        elif sp.state == "watching":
            series_watching += 1
        if sp.is_favorite:
            series_fav += 1
            series_favorites.append((sp.last_played or sp.updated_at, ser))
        if sp.state in ("completed", "watching"):
            series_recent.append((sp.last_played or sp.updated_at, ser))

        s_genres = _csv_tokens(ser.genres)
        s_dirs = _csv_tokens(ser.creators)  # showrunners / creators
        s_actors = _csv_tokens(ser.actors)[:5]
        for g in s_genres:
            genres[g] += w
        for d in s_dirs:
            directors[d] += w
        for a in s_actors:
            actors[a] += w
        # w ist hier immer > 0 (w <= 0 wurde oben uebersprungen) — Serien
        # belegen dieselben Signale wie Filme.
        beleg_titel.add(ser.id)
        for g in s_genres:
            _beleg("genre", g, ser.id)
        for d in s_dirs:
            _beleg("regie", d, ser.id)
        for a in s_actors:
            _beleg("besetzung", a, ser.id)

        if ser.runtime_min_avg:
            runtimes.append(ser.runtime_min_avg)
        if ser.year:
            years.append(ser.year)

    # has_history bleibt absichtlich weich: wahr, sobald IRGENDEIN Signal
    # existiert. Es entscheidet nicht nur ueber Text, sondern auch darueber,
    # ob profile_to_query_text die Vektorsuche mit dem Profil statt mit
    # "popular highly rated acclaimed movies" fuettert — und ueber den
    # Einsteiger-Zweig in recommendations._mechanical_reason. Eine einzelne
    # Bewertung ist als *Suchrichtung* ein echtes Signal; falsch war nur, sie
    # als Vorliebe zu BEHAUPTEN. Darum wird der Text gehaertet (siehe
    # profile_summary_text) und has_history nicht verschaerft. Wer die
    # schaerfere Weiche braucht, nimmt "has_belege" (unten).
    has_history = (played_count > 0 or fav_count > 0
                   or series_completed > 0 or series_watching > 0
                   or series_fav > 0)

    # Sort recents / favorites by recency (desc)
    favorites.sort(key=lambda t: t[0] or datetime.min, reverse=True)
    recent.sort(key=lambda t: t[0] or datetime.min, reverse=True)
    series_recent.sort(key=lambda t: t[0] or datetime.min, reverse=True)
    series_favorites.sort(key=lambda t: t[0] or datetime.min, reverse=True)

    profil = {
        "has_history": has_history,
        "watch_count": played_count,
        "favorite_count": fav_count,
        "rated_count": rated_count,
        "series_completed": series_completed,
        "series_watching":  series_watching,
        "series_favorite":  series_fav,
        "top_genres":      [(k, round(v, 2)) for k, v in genres.most_common(5)],
        "top_directors":   [(k, round(v, 2)) for k, v in directors.most_common(5)],
        "top_actors":      [(k, round(v, 2)) for k, v in actors.most_common(8)],
        "top_collections": [(k, round(v, 2)) for k, v in collections.most_common(5)],
        "avg_runtime": int(sum(runtimes) / len(runtimes)) if runtimes else None,
        "avg_year":    int(sum(years) / len(years)) if years else None,
        "recent_movies":   [m.name for _, m in recent[:5]],
        "favorite_movies": [m.name for _, m in favorites[:10]],
        # Mit Genres, damit Empfehlungs-Begruendungen einen *passenden* Anker
        # waehlen koennen statt stumpf den zuletzt gesehenen Film. Vorher
        # begruendete ein und derselbe Titel jede Empfehlung.
        "recent_movies_meta": [
            {"name": m.name, "genres": _csv_tokens(m.genres), "year": m.year}
            for _, m in recent[:8]
        ],
        "favorite_movies_meta": [
            {"name": m.name, "genres": _csv_tokens(m.genres), "year": m.year}
            for _, m in favorites[:12]
        ],
        "recent_series":   [ser.name for _, ser in series_recent[:5]],
        "favorite_series": [ser.name for _, ser in series_favorites[:5]],
        # Zahl der verschiedenen Titel hinter jedem Signal. Als int, nicht als
        # Menge: /recommendations/profile gibt das Profil (ausser seen_ids)
        # direkt als JSON zurueck, Mengen sind dort nicht serialisierbar.
        "signal_belege": {art: {name: len(ids) for name, ids in eintraege.items()}
                          for art, eintraege in belege.items()},
        "beleg_titel_gesamt": len(beleg_titel),
        "seen_ids": seen_ids,
    }
    # Schaerfere Weiche als has_history: wahr nur, wenn mindestens eine
    # Vorliebe auf PROFILE_MIN_BELEGE verschiedenen Titeln beruht. Genau
    # dieselbe Pruefung wie im Profiltext, damit beide nie auseinanderlaufen.
    profil["has_belege"] = bool(
        _belegte_namen(profil, "genre", profil["top_genres"])
        or _belegte_namen(profil, "regie", profil["top_directors"])
        or _belegte_namen(profil, "besetzung", profil["top_actors"]))
    return profil


def profile_to_query_text(profile: dict) -> str:
    """Build an English query text from a profile for embedding lookup."""
    if not profile["has_history"]:
        return "popular highly rated acclaimed movies"

    parts = []
    if profile["top_genres"]:
        gs = ", ".join(g for g, _ in profile["top_genres"][:3])
        parts.append(f"Genre: {gs}")
    if profile["top_directors"]:
        ds = ", ".join(d for d, _ in profile["top_directors"][:2])
        parts.append(f"Director: {ds}")
    if profile["top_actors"]:
        acs = ", ".join(a for a, _ in profile["top_actors"][:4])
        parts.append(f"Cast: {acs}")
    if profile["top_collections"]:
        cs = ", ".join(c for c, _ in profile["top_collections"][:2])
        parts.append(f"Collection: {cs}")
    if profile["favorite_movies"]:
        parts.append("Liked: " + ", ".join(profile["favorite_movies"][:5]))
    elif profile["recent_movies"]:
        parts.append("Recently watched: " + ", ".join(profile["recent_movies"][:5]))
    if profile.get("favorite_series"):
        parts.append("Liked TV: " + ", ".join(profile["favorite_series"][:4]))
    elif profile.get("recent_series"):
        parts.append("Recently watched TV: " + ", ".join(profile["recent_series"][:4]))
    return " | ".join(parts)


def _belegte_namen(profile: dict, art: str, paare: list,
                   limit: Optional[int] = None) -> list[str]:
    """Namen aus ``paare``, die auf mindestens PROFILE_MIN_BELEGE
    verschiedenen Titeln beruhen — und deren Punktestand noch positiv ist.

    Die zweite Bedingung, weil ``top_*`` nur nach Punkten sortiert ist: bei
    duenner Datenlage rutschen auch negative Eintraege nach oben. In einem
    Konto mit wenig Historie stand so ein Genre mit -0.5 (nur ein
    abgebrochener Film) zwischen den "Lieblings-Genres".
    """
    belege = (profile.get("signal_belege") or {}).get(art) or {}
    namen = [name for name, score in (paare or [])
             if score > 0 and belege.get(name, 0) >= PROFILE_MIN_BELEGE]
    return namen if limit is None else namen[:limit]


def _anzahl(n: int, singular: str, plural: str) -> str:
    """"1 Film" statt "1 Filme" — der Satz landet auch beim Nutzer."""
    return f"{n} {singular if n == 1 else plural}"


def _duenne_datenlage_text(profile: dict) -> str:
    """Ehrlicher Satz statt erfundener Vorliebe.

    Greift, wenn kein einziges Signal auf PROFILE_MIN_BELEGE verschiedenen
    Titeln beruht. Nennt die Zahlen — behauptet aber keine Vorliebe.
    """
    teile = []
    if profile.get("rated_count"):
        teile.append(_anzahl(profile["rated_count"],
                             "bewerteter Titel", "bewertete Titel"))
    if profile.get("favorite_count"):
        teile.append(_anzahl(profile["favorite_count"], "Favorit", "Favoriten"))
    if profile.get("series_favorite"):
        teile.append(_anzahl(profile["series_favorite"],
                             "Serie als Favorit", "Serien als Favorit"))
    if profile.get("series_completed"):
        teile.append(_anzahl(profile["series_completed"],
                             "durchgeschaute Serie", "durchgeschaute Serien"))
    if profile.get("series_watching"):
        teile.append(_anzahl(profile["series_watching"],
                             "angefangene Serie", "angefangene Serien"))
    gesehen = profile.get("watch_count") or 0
    teile.append("nichts gesehen" if not gesehen
                 else _anzahl(gesehen, "gesehener Film", "gesehene Filme"))
    return "Noch zu wenig Verlauf für ein Profil: " + ", ".join(teile) + "."


def profile_summary_text(profile: dict) -> str:
    """Human-readable German summary, used as LLM prompt context.

    Der Text geht in zwei Richtungen: in den Chat-Prompt (chat_engine) und als
    ``profile_summary`` nach aussen (/recommendations/daily). Vorlieben werden
    deshalb erst behauptet, wenn sie belegt sind — siehe PROFILE_MIN_BELEGE.
    Vorher stand unter "Der Nutzer hat 0 Filme gesehen und 0 als Favorit
    markiert." trotzdem "Lieblings-Genres: Drama, Historie, Kriegsfilm,
    Komoedie" aus einer einzigen Sternebewertung.

    Bleibt deutsch, auch bei englischer Oberflaeche: Die Oberflaeche zeigt den
    Text nicht an, er ist Kontext fuer die (deutschen) Prompts. Die Antwort-
    sprache der Modelle regelt texte.antwortsprache().
    """
    if not profile["has_history"]:
        return "Der Nutzer hat noch keine Sehhistorie."

    genres = _belegte_namen(profile, "genre", profile["top_genres"], 5)
    regie = _belegte_namen(profile, "regie", profile["top_directors"], 3)
    besetzung = _belegte_namen(profile, "besetzung", profile["top_actors"], 5)

    # Konkrete Titel sind keine Behauptung, sondern Tatsache — die Zeile bleibt
    # auch bei duenner Datenlage stehen, damit der Chat einen Anknuepfpunkt hat.
    if profile["favorite_movies"]:
        titel_zeile = "Favoriten: " + ", ".join(profile["favorite_movies"][:5])
    elif profile["recent_movies"]:
        titel_zeile = "Zuletzt gesehen: " + ", ".join(profile["recent_movies"][:5])
    else:
        titel_zeile = ""

    if not (genres or regie or besetzung):
        lines = [_duenne_datenlage_text(profile)]
        if titel_zeile:
            lines.append(titel_zeile)
        return "\n".join(lines)

    kopf = (f"Der Nutzer hat {_anzahl(profile['watch_count'], 'Film', 'Filme')} "
            f"gesehen und {profile['favorite_count']} als Favorit markiert.")
    # Serien zaehlen in die Vorlieben mit ein, standen in diesem Satz aber
    # nirgends. Bei einem Konto mit Serien, aber ohne gesehene Filme las sich
    # "0 Filme gesehen und 0 als Favorit markiert" wie "keine Daten" — und die
    # Genres darunter wie geraten.
    serien = []
    if profile.get("series_completed"):
        serien.append(_anzahl(profile["series_completed"],
                              "durchgeschaut", "durchgeschaut"))
    if profile.get("series_watching"):
        serien.append(_anzahl(profile["series_watching"], "laufend", "laufend"))
    if profile.get("series_favorite"):
        serien.append(_anzahl(profile["series_favorite"],
                              "als Favorit", "als Favorit"))
    if serien:
        kopf += " Bei Serien: " + ", ".join(serien) + "."

    lines = [kopf]
    if genres:
        lines.append("Lieblings-Genres: " + ", ".join(genres))
    if regie:
        lines.append("Bevorzugte Regisseure: " + ", ".join(regie))
    if besetzung:
        lines.append("Häufige Schauspieler: " + ", ".join(besetzung))
    if titel_zeile:
        lines.append(titel_zeile)
    return "\n".join(lines)
