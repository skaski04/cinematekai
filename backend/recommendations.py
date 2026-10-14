"""CinematekAI recommendation engine — RAG with diversity guarantee.

Three public entry points:

* daily_for(user_id)        — "Film des Tages" + 3 thematische Vorschläge
* similar_to(user_id, mid)  — Ähnliche Filme (mit sanftem Collection-Bias)
* by_mood(user_id, mood)    — Mood-basierte Vorschläge

Alle Vorschlagslisten mit N>=3 erfüllen die Diversitätsgarantie:
  - mindestens 2 verschiedene Genres
  - mindestens 2 verschiedene Jahrzehnte
  - maximal 1 Film pro Collection
Wenn das nicht erreichbar ist, werden die Bedingungen in dieser Reihenfolge
gelockert: Collection-Limit -> Jahrzehnte -> Genres.
"""
from __future__ import annotations
import json, logging, re, threading
from datetime import datetime, timedelta
from typing import Optional
from sqlmodel import select

import anbieter
import sichtbarkeit
import texte
from database import get_session, Movie, WatchState, RecommendationCache
from embeddings import search_by_text, search_similar, normalize_user_query, skala
from user_profile import build_profile, profile_to_query_text, profile_summary_text

log = logging.getLogger("cinematekai.recs")

COLLECTION_BOOST = 0.15        # 15% bonus for unseen movies in the same collection
CACHE_TTL_HOURS = 24

# Unter dieser Zahl ungesehener Kandidaten wird mit Rewatch-Vorschlaegen
# aufgefuellt. 12 reicht, damit Film des Tages + 3 thematische + Reserve
# tatsaechlich Auswahl haben.
MIN_POOL = 12

# Aus so vielen Spitzenkandidaten wird der Film des Tages gezogen.
MAIN_PICK_WINDOW = 5

# ---------------------------------------------------------------------------
# Stimmungssuche: Schwelle und Prozent-Abbildung
# ---------------------------------------------------------------------------
# Schwelle und Skala haengen am Einbettungsmodell. Gemessen am 29.09. mit
# denselben sechs Filmen und der Frage "lustiger Tierfilm fuer Kinder":
#   nomic-embed-text: 0,471 bis 0,632 — und in der falschen Reihenfolge
#   bge-m3:           0,391 bis 0,536 — richtig sortiert, mit klarem Abstand
# Feste Zahlen hier passten also immer nur zu einem Modell. Seit im
# Admin-Bereich jedes Modell waehlbar ist, kommen die drei Werte zur Laufzeit
# aus embeddings.skala(): je Modell gemessen (embeddings.kalibrieren), fuer
# bge-m3 die bisherigen Handwerte 0,45 / 0,44 / 0,54.
#
# mood_min ist eine harte Untergrenze: darunter ist ein Treffer kein
# Treffer mehr, sondern Rauschen. Bleibt danach nichts uebrig, liefert die
# Suche eine LEERE Liste — lieber "nichts gefunden" als ein Thriller mit
# "100 %" daneben.
#
# Prozentanzeige: absolut, nicht relativ zum besten Treffer der Anfrage. Die
# alte relative Dehnung (Spanne der Anfrage auf 60-100) machte aus drei
# Prozent Rohspanne eine "100 %"-Behauptung und ist deshalb entfallen.
# mood_floor wird als 0 % angezeigt, mood_ceil als 100 %.

# Vielfalt ist in der Stimmungssuche nur noch Nebenbedingung: so viele Titel
# derselben Reihe duerfen hoechstens direkt hintereinander stehen. Die
# Rangfolge nach Passung bleibt sonst unangetastet.
MOOD_MAX_REIHE_AM_STUECK = 2

# ---------------------------------------------------------------------------
# Genre in der Anfrage
# ---------------------------------------------------------------------------
# Gemessen am 11.10.2026 mit bge-m3 an 20 Anfragen: Nennt jemand ein Genre,
# stehen die passenden Titel zwar oben, aber oft UNTER der Schwelle mood_min.
# "Science-Fiction im Weltraum" lieferte "keine guten Treffer", obwohl Matrix,
# Transformers und Interstellar ganz vorn lagen (bester Wert 0,375 bei
# Schwelle 0,45); ebenso "Dokumentation ueber Natur" (0,381) und "Krieg und
# Geschichte" (0,398). Dazwischen drangen Fremde ein: Wickie bei Horror,
# Jumper und Fight Club bei "Animationsfilm mit Tieren", Paddington bei
# Western. Die Schwelle einfach senken ging nicht — Unsinn wie "ungarischer
# Stummfilm ueber Traktoren" erreicht 0,459.
#
# Das Genre-Wort ist dagegen ein Signal, das Unsinn nicht hat. Deshalb:
#   * Reihenfolge: Titel mit dem genannten Genre stehen vor den anderen
#     (starker Bonus nur fuer die Sortierung).
#   * Aufnahme und Prozentanzeige: ein angepasster Bonus, gerade so gross,
#     dass der beste Genre-Treffer bei 60 % der Skala landet, hoechstens eine
#     Skalenbreite. Anfragen, die ohnehin gut trafen, behalten so ihre
#     Prozente (romantische Komoedie weiter 87/76/72 %) — ein fester Bonus
#     haette dort alles auf 100 % gehoben.
# Beides in Vielfachen der Skalenbreite (mood_ceil - mood_floor), damit es
# fuer jedes Einbettungsmodell passt. Ergebnis der Messung: Science-Fiction
# 0 -> 6 Treffer, Dokumentarfilm 0 -> 4, Krieg 0 -> 4, Krimi 0 -> 19; die
# vier Unsinnsanfragen und alle Anfragen ohne Genre-Wort unveraendert.
#
# Jellyfin liefert die Genre-Namen in der Sprache seiner Metadaten; daher je
# Eintrag die deutschen UND englischen Namen.
_GENRE_WOERTER: list[tuple[str, frozenset[str]]] = [
    (r"science[- ]?fiction|sci[- ]?fi", frozenset({"science fiction", "science-fiction", "sci-fi"})),
    (r"doku|dokumentar|dokumentation|documentar", frozenset({"dokumentarfilm", "dokumentation", "documentary"})),
    # "Krieger" ist kein Kriegsfilm, "eine Geschichte ueber ..." keine Historie.
    (r"krieg(?:e|s\w*)?\b|weltkrieg\w*|war\b|kriegsfilm\w*", frozenset({"kriegsfilm", "krieg", "war", "war & politics"})),
    (r"histori\w*|geschichtsfilm\w*|history\b|historical", frozenset({"historie", "history"})),
    (r"horror\w*|grusel\w*", frozenset({"horror"})),
    (r"kom(?:ö|oe)die\w*|comedy|comedies", frozenset({"komödie", "comedy"})),
    (r"liebesfilm\w*|romanti\w*|romanze\w*|romance|romcom", frozenset({"liebesfilm", "romance", "romantik"})),
    (r"thriller\w*", frozenset({"thriller"})),
    (r"krimi\w*|kriminalfilm\w*|crime\b", frozenset({"krimi", "crime"})),
    (r"animation\w*|animiert\w*|zeichentrick\w*|trickfilm\w*|anime\b|cartoon\w*", frozenset({"animation", "zeichentrick"})),
    (r"familienfilm\w*|kinderfilm\w*|familie\b|family\b", frozenset({"familie", "family"})),
    (r"fantasy", frozenset({"fantasy"})),
    (r"action\w*", frozenset({"action"})),
    (r"abenteuer\w*|adventure\w*", frozenset({"abenteuer", "adventure"})),
    (r"drama\b|dramen\b", frozenset({"drama"})),
    (r"western\w*", frozenset({"western"})),
    (r"musikfilm\w*|musical\w*|musik\b|music\b", frozenset({"musik", "music", "musical"})),
    (r"mystery", frozenset({"mystery"})),
]
_GENRE_MUSTER = [(re.compile(r"(?<![\wäöüß])(?:" + m + ")"), namen) for m, namen in _GENRE_WOERTER]
# "kein Horror", "Komoedie ohne Krieg": das Wort davor entscheidet.
_VERNEINUNG = frozenset({"kein", "keine", "keinen", "keiner", "keinem", "ohne", "nicht",
                         "no", "not", "without"})
_WORT = re.compile(r"[\wäöüß]+")
# Anteile der Skalenbreite (Begruendung oben).
MOOD_GENRE_ZIEL = 0.6
MOOD_GENRE_DECKEL = 1.0
MOOD_GENRE_RANG = 1.0


def genres_aus_text(text: Optional[str]) -> set[str]:
    """Genre-Namen (klein geschrieben), die eine Anfrage ausdruecklich nennt.

    Ein verneintes Genre ("kein Horror", "ohne Krieg") zaehlt nicht.
    """
    t = (text or "").lower()
    gefunden: set[str] = set()
    for muster, namen in _GENRE_MUSTER:
        for m in muster.finditer(t):
            davor = _WORT.findall(t[:m.start()])[-2:]
            if any(w in _VERNEINUNG for w in davor):
                continue
            gefunden |= namen
            break
    return gefunden


def _genre_gewichten(items: list[dict], gefragt: set[str]) -> None:
    """Setzt je Treffer ``wert`` (Aufnahme, Prozent) und ``rang`` (Reihenfolge).

    Ohne Genre-Wort sind beide gleich ``score`` — das Verhalten bleibt dann
    genau das bisherige.
    """
    for it in items:
        it["genre_treffer"] = bool(gefragt) and bool(
            {str(g).strip().lower() for g in (it.get("genres") or [])} & gefragt)
    passend = [it.get("score") or 0 for it in items if it["genre_treffer"]]
    s = skala()
    breite = max(0.0, s["mood_ceil"] - s["mood_floor"])
    bonus = 0.0
    if passend:
        ziel = s["mood_floor"] + MOOD_GENRE_ZIEL * breite
        bonus = min(MOOD_GENRE_DECKEL * breite, max(0.0, ziel - max(passend)))
    for it in items:
        roh = it.get("score") or 0
        it["wert"] = roh + (bonus if it["genre_treffer"] else 0.0)
        it["rang"] = it["wert"] + (MOOD_GENRE_RANG * breite if it["genre_treffer"] else 0.0)

# Mindestzahl VERSCHIEDENER Titel, bevor eine Begruendung ein Signal
# behaupten darf ("diese Handschrift kennst du schon"). Eine einzige
# Sternebewertung machte vorher Spielberg zum Lieblingsregisseur.
REASON_MIN_BELEGE = 2


# ---------------------------------------------------------------------------
# Texte fuer die Oberflaeche (Deutsch / Englisch)
# ---------------------------------------------------------------------------
# Alles, was als Begruendung, Hinweis oder Etikett in den Antworten steht. Die
# Auswahl der Formulierung je Film ist an movie_id gebunden (siehe
# _mechanical_reason): Tupel stehen fuer gleichwertige Varianten, und
# Deutsch und Englisch haben je Schluessel gleich viele — so bleibt die
# Wahl eines Films in beiden Sprachen dieselbe. Die deutschen Texte stehen
# woertlich so da, wie sie schon vorher ausgeliefert wurden.
_T = {
    "de": {
        # Substantive je Medientyp
        "n_pl_film": "Filme", "n_pl_serie": "Serien",
        "n_sg_film": "dieser Film", "n_sg_serie": "diese Serie",
        "n_acc_film": "diesen Film", "n_acc_serie": "diese Serie",

        # Einsteiger ohne Historie
        "start_stark": "Von der Community stark empfohlen ({rating:.1f} ★) — guter Einstieg in deine Bibliothek.",
        "start": "Beliebter Titel aus deiner Bibliothek — perfekt zum Reinkommen.",

        # Reihe
        "reihe_gesehen_mehrere": (
            'Gehört zu „{col}“, aus der du bereits mehrere Filme gesehen hast.',
            'Setzt die „{col}“-Reihe fort, von der du schon mehrere Teile kennst.',
        ),
        "reihe_gesehen_eine": (
            'Teil der Reihe „{col}“ — einen Film daraus hast du schon gesehen.',
        ),
        "reihe_bewertet_mehrere": (
            'Gehört zu „{col}“ — aus der Reihe hast du mehrere gut bewertet.',
        ),
        "reihe_bewertet_eine": (
            'Teil der Reihe „{col}“ — einen Film daraus hast du gut bewertet.',
        ),
        "reihe_merkliste_mehrere": (
            'Gehört zu „{col}“ — die Reihe steht mehrfach auf deiner Merkliste.',
        ),
        "reihe_merkliste_eine": (
            'Teil der Reihe „{col}“ — ein Film daraus steht auf deiner Merkliste.',
        ),

        # TMDB-Schwarm
        "tmdb_zwei": (
            'Wer „{a}“ und „{b}“ mochte, mochte oft auch {noun_acc}.',
            'Taucht bei TMDB immer wieder neben „{a}“ und „{b}“ auf.',
        ),
        "tmdb_eins": (
            'Wer „{a}“ mochte, mochte oft auch {noun_acc}.',
            'Taucht bei TMDB immer wieder neben „{a}“ auf.',
        ),

        # Regie
        "wer_regie": "Regie von {d}",
        "wer_serie": "Showrunner {d}",
        "regie_gesehen": (
            "{wer} — diese Handschrift war in deiner Sehhistorie schon öfter dabei.",
            "{wer} — von {d} hast du dir schon mehrere {noun_pl} angeschaut.",
        ),
        "regie_bewertet": (
            "{wer} — mehrere Titel dieser Handschrift hast du gut bewertet.",
            "{wer} — davon steht schon mehreres mit guter Note in deinem Profil.",
        ),
        "regie_merkliste": "{wer} — diese Handschrift steht mehrfach auf deiner Merkliste.",

        # Besetzung
        "und_name": " und {name}",
        "besetzung_gesehen": (
            "{a}{others_str} spielt mit — ein Gesicht, das in deiner Sehhistorie oft auftaucht.",
            "Mit {a}{others_str} — in mehreren Titeln zu sehen, die du geschaut hast.",
        ),
        "besetzung_bewertet": "Mit {a}{others_str} — in mehreren Titeln zu sehen, die du gut bewertet hast.",
        "besetzung_merkliste": "Mit {a}{others_str} — taucht mehrfach auf deiner Merkliste auf.",

        # Genre
        "genre2_gesehen": (
            "{g} trifft {secondary} — genau die Mischung, die du oft anschaust.",
            "Eine {g}/{secondary}-Kombi, wie sie in deiner Historie häufig vorkommt.",
        ),
        "genre2_bewertet": "{g} trifft {secondary} — beide Genres hast du mehrfach gut bewertet.",
        "genre2_merkliste": "{g} trifft {secondary} — beide Genres stehen mehrfach auf deiner Merkliste.",
        "genre1_gesehen": (
            "{g} — dein meistgesehenes Genre, und {noun_sg} passt ins Schema.",
            "{g} in klassischer Form — bedient dein Profil, ohne sich zu wiederholen.",
        ),
        "genre1_bewertet": "{g} — dieses Genre hast du mehrfach gut bewertet.",
        "genre1_merkliste": "{g} — dieses Genre steht mehrfach auf deiner Merkliste.",

        # Anker aus der Historie
        "anker_gesehen": (
            'Wenn dir „{anchor}“ gefallen hat, dürfte dieser Ton dich abholen.',
            'Schlägt in dieselbe Kerbe wie „{anchor}“ aus deiner Sehhistorie.',
            'Ähnliche Richtung wie „{anchor}“, das du schon gesehen hast.',
        ),
        "anker_bewertet": (
            'Schlägt in dieselbe Kerbe wie „{anchor}“, das du gut bewertet hast.',
            'Ähnliche Richtung wie „{anchor}“ aus deinen Favoriten.',
        ),
        "anker_schwach": 'Liegt auf der Linie deiner Favoriten — erinnert an „{anchor}“.',

        # Zeit
        "zeit_gesehen": (
            "Aus den {decade}ern — du schaust oft {noun_pl} aus dieser Zeit.",
            "{film_year} passt genau in dein bevorzugtes Zeitfenster.",
        ),
        "zeit": "{film_year} passt genau in dein bevorzugtes Zeitfenster.",

        # Wertung und Laufzeit
        "wertung_hoch": (
            "Community-Wertung {rating:.1f} ★ — ein echter Hochkaräter.",
            "Mit {rating:.1f} ★ unter den am besten bewerteten Titeln der Bibliothek.",
        ),
        "wertung_solide": "Solide {rating:.1f} ★ in der Community.",
        "laufzeit_kurz": (
            "Dazu kompakt mit {runtime} Min — passt auch an einen kurzen Abend.",
            "Kurz und knackig ({runtime} Min).",
        ),
        "laufzeit_lang": "Plane am besten den Abend komplett ein — {runtime} Min Laufzeit.",

        # Notnagel
        "notnagel_genre": "{genre} — dieses Genre steht in deinem Profil weit vorn.",
        "notnagel_wertung": "Community-Wertung {rating:.1f} ★ — {noun_sg} gehört zu den besser bewerteten Titeln deiner Bibliothek.",
        "notnagel_reihe": "Gehört zur Reihe „{reihe}“ in deiner Bibliothek.",
        "notnagel_jahr": "Aus dem Jahr {film_year} — noch nicht in deiner Historie.",
        "notnagel_gesehen": "Ausgewählt nach deinem bisherigen Sehverhalten.",
        "notnagel_markiert": "Ausgewählt nach dem, was du bisher markiert und bewertet hast.",

        # Raus aus der Blase
        "explore_genre": "{genre} — steht sonst selten in deiner Liste.",
        "explore_dekade": "Aus den {decade}ern — andere Epoche als sonst.",
        "explore_sonst": "Mal etwas ganz anderes als üblich.",

        # Hinweise
        "keine_kandidaten": "Keine ungesehenen Kandidaten gefunden.",
        "keine_serie": "Keine Serien-Empfehlung gefunden.",
        "hinweis_alter": "Dazu gibt es in deiner Bibliothek nichts mit passender Altersfreigabe.",
        "hinweis_treffer": "Dazu findet die KI in deiner Bibliothek keine guten Treffer.",
    },
    "en": {
        "n_pl_film": "movies", "n_pl_serie": "series",
        "n_sg_film": "this movie", "n_sg_serie": "this series",
        "n_acc_film": "this movie", "n_acc_serie": "this series",

        "start_stark": "Highly recommended by the community ({rating:.1f} ★) — a good way into your library.",
        "start": "A popular pick from your library — a great place to start.",

        "reihe_gesehen_mehrere": (
            "Part of “{col}”, and you've already watched several of its movies.",
            "Continues the “{col}” collection — you already know several parts of it.",
        ),
        "reihe_gesehen_eine": (
            "Part of the “{col}” collection — you've already watched one movie from it.",
        ),
        "reihe_bewertet_mehrere": (
            "Part of “{col}” — you've rated several from this collection highly.",
        ),
        "reihe_bewertet_eine": (
            "Part of the “{col}” collection — you rated one movie from it highly.",
        ),
        "reihe_merkliste_mehrere": (
            "Part of “{col}” — several of its movies are on your watchlist.",
        ),
        "reihe_merkliste_eine": (
            "Part of the “{col}” collection — one movie from it is on your watchlist.",
        ),

        "tmdb_zwei": (
            "People who liked “{a}” and “{b}” often liked {noun_acc} too.",
            "Keeps showing up on TMDB next to “{a}” and “{b}”.",
        ),
        "tmdb_eins": (
            "People who liked “{a}” often liked {noun_acc} too.",
            "Keeps showing up on TMDB next to “{a}”.",
        ),

        "wer_regie": "Directed by {d}",
        "wer_serie": "Showrunner {d}",
        "regie_gesehen": (
            "{wer} — this style has shown up in your watch history more than once.",
            "{wer} — you've already watched several of their {noun_pl}.",
        ),
        "regie_bewertet": (
            "{wer} — you've rated several titles in this style highly.",
            "{wer} — your profile already has several of their titles with good ratings.",
        ),
        "regie_merkliste": "{wer} — several titles in this style are on your watchlist.",

        "und_name": " and {name}",
        "besetzung_gesehen": (
            "{a}{others_str} in the cast — a familiar presence in your watch history.",
            "With {a}{others_str} — seen in several titles you've watched.",
        ),
        "besetzung_bewertet": "With {a}{others_str} — seen in several titles you rated highly.",
        "besetzung_merkliste": "With {a}{others_str} — a recurring name on your watchlist.",

        "genre2_gesehen": (
            "{g} meets {secondary} — exactly the mix you often watch.",
            "{g}/{secondary} combo — the kind that shows up often in your history.",
        ),
        "genre2_bewertet": "{g} meets {secondary} — you've rated several titles in both genres highly.",
        "genre2_merkliste": "{g} meets {secondary} — your watchlist has several titles in both genres.",
        "genre1_gesehen": (
            "{g} — your most-watched genre, and {noun_sg} fits the pattern.",
            "{g} in its classic form — suits your profile without repeating itself.",
        ),
        "genre1_bewertet": "{g} — you've rated several titles in this genre highly.",
        "genre1_merkliste": "{g} — your watchlist has several titles in this genre.",

        "anker_gesehen": (
            "If you enjoyed “{anchor}”, this tone should speak to you.",
            "In the same vein as “{anchor}” from your watch history.",
            "Similar in spirit to “{anchor}”, which you've already watched.",
        ),
        "anker_bewertet": (
            "In the same vein as “{anchor}”, which you rated highly.",
            "Similar in spirit to “{anchor}” from your favorites.",
        ),
        "anker_schwach": "In line with your favorites — reminiscent of “{anchor}”.",

        "zeit_gesehen": (
            "From the {decade}s — you often watch {noun_pl} from this era.",
            "{film_year} fits right into your preferred time window.",
        ),
        "zeit": "{film_year} fits right into your preferred time window.",

        "wertung_hoch": (
            "Community rating {rating:.1f} ★ — a real standout.",
            "At {rating:.1f} ★, among the best-rated titles in your library.",
        ),
        "wertung_solide": "A solid {rating:.1f} ★ from the community.",
        "laufzeit_kurz": (
            "Compact at {runtime} min — fits a short evening too.",
            "Short and punchy ({runtime} min).",
        ),
        "laufzeit_lang": "Best to clear your whole evening — {runtime} min runtime.",

        "notnagel_genre": "{genre} — this genre ranks near the top of your profile.",
        "notnagel_wertung": "Community rating {rating:.1f} ★ — {noun_sg} is among the better-rated titles in your library.",
        "notnagel_reihe": "Part of the “{reihe}” collection in your library.",
        "notnagel_jahr": "From {film_year} — not in your history yet.",
        "notnagel_gesehen": "Picked based on what you've watched so far.",
        "notnagel_markiert": "Picked based on what you've marked and rated so far.",

        "explore_genre": "{genre} — rarely on your list otherwise.",
        "explore_dekade": "From the {decade}s — a different era than usual.",
        "explore_sonst": "Something completely different for a change.",

        "keine_kandidaten": "No unwatched candidates found.",
        "keine_serie": "No series recommendation found.",
        "hinweis_alter": "Nothing in your library has a matching age rating.",
        "hinweis_treffer": "The AI finds no good matches for this in your library.",
    },
}


def _sp(sprache: Optional[str]) -> str:
    """Sprachcode: der uebergebene, sonst der der laufenden Anfrage (texte.aktuell).
    Hintergrundarbeit muss die Sprache des Nutzers selbst uebergeben, eine
    ContextVar erbt kein Thread."""
    return texte.normalisieren(sprache) or texte.aktuell()


def _t(schluessel: str, sprache: Optional[str] = None, **werte) -> str:
    return texte.waehle(_T, schluessel, sprache, **werte)


def _wahl(rng, schluessel: str, sprache: str, **werte) -> str:
    """Eine von mehreren gleichwertigen Formulierungen, per ``rng`` gewaehlt.

    Ruft ``rng.choice`` auch bei nur EINER Variante auf — so verbraucht jeder
    Film in beiden Sprachen dieselbe Zufallsfolge."""
    varianten = ((_T.get(sprache) or {}).get(schluessel)
                 or _T[texte.STANDARD][schluessel])
    return rng.choice(varianten).format(**werte)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decade(year: Optional[int]) -> Optional[int]:
    return (year // 10) * 10 if year else None


def _load_movie(mid: str) -> Optional[Movie]:
    with get_session() as s:
        return s.get(Movie, mid)


def _hydrate(items: list[dict]) -> list[dict]:
    """Attach full Movie / Series / Episode data to score items. Each result
    gains a ``media_type`` key so downstream UIs can route detail-page
    links."""
    if not items:
        return []
    ids = [it["movie_id"] for it in items]
    from database import Series, Episode
    with get_session() as s:
        movies = s.exec(select(Movie).where(Movie.id.in_(ids))).all()
        series = s.exec(select(Series).where(Series.id.in_(ids))).all()
        episodes = s.exec(select(Episode).where(Episode.id.in_(ids))).all()
        # Look up series names for episode hits
        ep_series_ids = {e.series_id for e in episodes}
        ep_series_rows = s.exec(
            select(Series).where(Series.id.in_(ep_series_ids))
        ).all() if ep_series_ids else []
    movie_by_id = {m.id: m for m in movies}
    series_by_id = {ser.id: ser for ser in series}
    episode_by_id = {e.id: e for e in episodes}
    ep_series_by_id = {ser.id: ser for ser in ep_series_rows}
    out = []
    for it in items:
        mid = it["movie_id"]
        if mid in movie_by_id:
            m = movie_by_id[mid]
            out.append({
                **it,
                "media_type": "movie",
                "name": m.name,
                "year": m.year,
                "genres": [g for g in m.genres.split(",") if g],
                "collections": [c for c in m.collection_names.split(",") if c],
                "collection_ids": [c for c in m.collection_ids.split(",") if c],
                "directors": [d for d in m.directors.split(",") if d],
                "actors": [a for a in m.actors.split(",") if a],
                "overview": m.overview,
                "rating": m.community_rating,
                # Altersfreigabe (FSK-0/6/12/16/18, G, PG, PG-13, R, NC-17,
                # NR). Stand hier frueher nicht drin — deshalb konnte auf
                # "fuer Kinder" ein FSK-16-Thriller ganz vorne landen.
                "official_rating": m.official_rating,
                "runtime_min": m.runtime_min,
                "poster_url": f"/image/{m.id}",
            })
        elif mid in series_by_id:
            ser = series_by_id[mid]
            out.append({
                **it,
                "media_type": "series",
                "name": ser.name,
                "year": ser.year,
                "genres": [g for g in ser.genres.split(",") if g],
                "collections": [],
                "collection_ids": [],
                "directors": [c for c in ser.creators.split(",") if c],
                "actors": [a for a in ser.actors.split(",") if a],
                "overview": ser.overview,
                "rating": ser.community_rating,
                # Serien tragen andere Etiketten als Filme (TV-14, TV-MA,
                # "12+"), siehe `mindestalter`.
                "official_rating": ser.official_rating,
                # Use average episode length so runtime filters do something
                # sensible against series too.
                "runtime_min": ser.runtime_min_avg,
                "season_count": ser.season_count,
                "episode_count": ser.episode_count,
                "status": ser.status,
                "poster_url": f"/image/{ser.id}",
            })
        elif mid in episode_by_id:
            ep = episode_by_id[mid]
            sname = ep_series_by_id.get(ep.series_id)
            label = ""
            if ep.season is not None and ep.episode is not None:
                label = f"S{ep.season}E{ep.episode}"
            out.append({
                **it,
                "media_type": "episode",
                "name": f"{sname.name if sname else 'Episode'} · {label}: {ep.name}".strip(),
                "year": ep.air_date.year if ep.air_date else None,
                "genres": [g for g in (sname.genres.split(",") if sname else []) if g],
                "collections": [],
                "collection_ids": [],
                "directors": [],
                "actors": [],
                "overview": ep.overview,
                "rating": None,
                # Folgen haben in Jellyfin selten eine eigene Freigabe. Ohne
                # das Erben von der Serie waere jede Folge "unbekannt" und
                # fiele bei gesetztem Altersfilter durch das Raster.
                "official_rating": sname.official_rating if sname else None,
                "runtime_min": ep.runtime_min,
                "series_id": ep.series_id,
                "season": ep.season,
                "episode": ep.episode,
                "label": label,
                "poster_url": f"/image/{ep.id}",
            })
    return out


# ---------------------------------------------------------------------------
# Altersfreigabe
# ---------------------------------------------------------------------------
# Die Freigabe steht in Movie.official_rating / Series.official_rating und war
# bis jetzt tote Last: `_hydrate` warf sie weg. Gezaehlt am 29.09.2026 stehen
# 441 von 451 Filmen mit einer Angabe in der Datenbank, Serien tragen daneben
# TV-Etiketten (TV-G/TV-PG/TV-14/TV-MA) und Zahlformen wie "12+".

# Feste Zuordnung Etikett -> Mindestalter. Wird VOR der Zahlenerkennung
# geprueft, sonst wuerde "PG-13" als 13 und "NC-17" als 17 gelesen.
_FREIGABE_MINDESTALTER: dict[str, Optional[int]] = {
    # deutsche Freigaben
    "FSK-0": 0, "FSK 0": 0, "FSK-6": 6, "FSK 6": 6,
    "FSK-12": 12, "FSK 12": 12, "FSK-16": 16, "FSK 16": 16,
    "FSK-18": 18, "FSK 18": 18,
    # US-Kinofreigaben
    "G": 0, "PG": 6, "PG-13": 12, "R": 16, "NC-17": 18,
    # US-Fernsehfreigaben (kommen bei Serien vor)
    "TV-Y": 0, "TV-G": 0, "TV-Y7": 7, "TV-PG": 6, "TV-14": 14, "TV-MA": 16,
    # ausdruecklich OHNE Angabe — nicht mit "0" verwechseln
    "NR": None, "UNRATED": None, "NOT RATED": None, "UR": None,
}

_ZAHL_IN_FREIGABE = re.compile(r"(\d{1,2})")


def mindestalter(freigabe: Optional[str]) -> Optional[int]:
    """Altersfreigabe -> Mindestalter in Jahren.

    ``None`` heisst ausdruecklich "unbekannt" (leer, NR, unbekanntes Etikett)
    und NICHT "ab 0" — der Aufrufer muss selbst entscheiden, was er damit
    macht. Reihenfolge: feste Zuordnung zuerst, danach die Zahl im Etikett
    ("12+", "DE-16"), damit "PG-13" nicht als 13 durchrutscht.
    """
    if not freigabe:
        return None
    schluessel = freigabe.strip().upper()
    if schluessel in _FREIGABE_MINDESTALTER:
        return _FREIGABE_MINDESTALTER[schluessel]
    treffer = _ZAHL_IN_FREIGABE.search(schluessel)
    if treffer:
        wert = int(treffer.group(1))
        return wert if 0 <= wert <= 21 else None
    return None


def _erlaubt_bis_alter(item: dict, max_age: Optional[int]) -> bool:
    """Harter Altersfilter fuer einen hydrierten Treffer.

    Ohne gesetztes ``max_age`` bleibt alles drin. Ist es gesetzt, fliegen auch
    Titel OHNE Angabe raus: sonst rutschen genau die zehn ungeprueften Filme
    (und jede Serie mit exotischem Etikett) durch einen Filter, den jemand
    gerade fuer seine Kinder gesetzt hat. Keine stillschweigende Lockerung —
    lieber eine kurze Liste als ein falscher Treffer.
    """
    if max_age is None:
        return True
    alter = mindestalter(item.get("official_rating"))
    if alter is None:
        return False
    return alter <= max_age


# Woerter, die eine Anfrage als Kinder-Anfrage ausweisen. Bewusst eine feste
# Liste und kein Modellaufruf: die Erkennung muss nachvollziehbar und einzeln
# testbar bleiben. Verglichen wird auf ganze Woerter — sonst schluege
# "Kindheit" an, und "Film ueber eine Kindheit in den 70ern" waere ploetzlich
# ein Kinderfilm.
KINDER_WOERTER: frozenset[str] = frozenset({
    "kind", "kinds", "kindes", "kinder", "kindern",
    "kids", "kiddies", "nachwuchs",
})

# Zusammensetzungen und gebeugte Formen ueber den Wortstamm. "Kindgerechtes",
# "kindgerechten", "Familienfilme" sind dieselbe Aussage; sie alle einzeln
# aufzuzaehlen waere eine Liste, die beim naechsten Kasus wieder loechrig ist.
# Bewusst OHNE den Stamm "kinder": sonst faengt "Kinderei" mit, und vor allem
# waere "Kindheit" nur eine Buchstabenfolge entfernt.
KINDER_STAEMME: tuple[str, ...] = (
    "kindgerecht", "kindertauglich", "kinderfreundlich",
    "kinderfilm", "kinderserie", "kinderprogramm", "kinderkino",
    "familienfilm", "familienserie", "familientauglich",
    "familienabend", "familienkino",
    "kleinkind", "grundschulkind", "vorschulkind",
)

# Wendungen, die nur als ganze Phrase eindeutig sind. "kleinen" allein waere
# zu gierig ("die kleinen Dinge des Lebens").
KINDER_PHRASEN: tuple[str, ...] = (
    "fuer die kleinen", "für die kleinen",
    "fuer die kleinsten", "für die kleinsten",
    "mit den kindern", "mit den kleinen",
)

# Ausdrueckliche Altersangaben: "8 Jahre", "8-jaehrige", "ab 8".
_ALTERSANGABE = re.compile(
    # "ab 16 Jahren" ist keine Kinder-Angabe, sondern eine Untergrenze — der
    # Rueckblick schliesst sie hier aus; sie faellt unten unter _AB_ALTER.
    r"(?<!ab )\b(\d{1,2})\s*(?:-\s*)?(?:jahre|jahren|jahr|jährig\w*|jaehrig\w*)\b",
    re.IGNORECASE,
)
# "ab 16" ist im Deutschen eine MINDEST-Angabe: "Filme ab 16" meint FSK-16,
# nicht "hoechstens 16". Als Hoechstalter gelesen drehte es die Anfrage um und
# warf genau die Titel raus, die gemeint waren. Deshalb zaehlt "ab N" nur,
# wenn ohnehin von Kindern die Rede ist ("fuer Kinder ab 8").
_AB_ALTER = re.compile(r"\bab\s+(\d{1,2})\b", re.IGNORECASE)
# Zwei Alter nebeneinander ("Kinder, 8 und 11"). Eine einzelne blosse Zahl
# zaehlt NICHT mehr: "Film ueber 2 Kinder im Krieg" ergab sonst Hoechstalter 2,
# und davon ueberleben nur FSK-0-Titel.
_ALTERSPAAR = re.compile(r"\b(\d{1,2})\s*(?:und|,|&|\+)\s*(\d{1,2})\b")
_WORT = re.compile(r"[\wäöüß]+", re.UNICODE)

# Ohne genannte Zahl steht "Kinder" fuer diese Altersgrenze.
KINDER_STANDARD_ALTER = 6
# Auch eine genannte 16 fuehrt nicht ueber diese Grenze hinaus — wer nach
# einem Film "fuer die Kinder" fragt, meint keinen FSK-18-Titel.
KINDER_MAX_ALTER = 12


def altersgrenze_aus_text(text: Optional[str]) -> Optional[int]:
    """Erkennt Kinder-Anfragen im Freitext und liefert das Hoechstalter.

    Rueckgabe ``None`` = keine Kinder-Anfrage, also kein Filter.

    Regeln:
      * ein Wort aus ``KINDER_WOERTER``, ein Wort mit einem Stamm aus
        ``KINDER_STAEMME`` oder eine Wendung aus ``KINDER_PHRASEN`` loest aus,
      * eine ausdrueckliche Altersangabe ("ab 8", "8 Jahre", "8-jaehrige")
        loest ebenfalls aus,
      * "ab N" und Alterspaare ("8 und 11") zaehlen NUR, wenn ohnehin schon
        ein Kinderwort in der Anfrage steht. Sonst waere "Ocean's 11" eine
        Anfrage fuer Elfjaehrige und "Filme ab 16" ein Kinderfilter.
      * eine einzelne blosse Zahl zaehlt nie ("Film ueber 2 Kinder im Krieg").
      * genannt wird die kleinste Zahl, gedeckelt auf KINDER_MAX_ALTER.
    """
    if not text or not text.strip():
        return None
    klein = text.lower()
    woerter = {w for w in _WORT.findall(klein)}

    hat_kinderwort = (
        bool(woerter & KINDER_WOERTER)
        or any(w.startswith(st) for w in woerter for st in KINDER_STAEMME)
        or any(ph in klein for ph in KINDER_PHRASEN)
    )

    zahlen: list[int] = []
    for m in _ALTERSANGABE.finditer(klein):
        zahlen.append(int(m.group(1)))
    if hat_kinderwort:
        for m in _AB_ALTER.finditer(klein):
            zahlen.append(int(m.group(1)))
        for m in _ALTERSPAAR.finditer(klein):
            zahlen += [int(m.group(1)), int(m.group(2))]

    # Nur plausible Kinderalter. 0 ist erlaubt, ab 18 ist es keine
    # Kinder-Angabe mehr, sondern eher eine Jahreszahl oder Laufzeit.
    zahlen = [z for z in zahlen if 0 <= z <= 17]

    if not hat_kinderwort and not zahlen:
        return None
    if zahlen:
        return min(min(zahlen), KINDER_MAX_ALTER)
    return KINDER_STANDARD_ALTER


# ---------------------------------------------------------------------------
# Diversity filter
# ---------------------------------------------------------------------------

def _pick_diverse(candidates: list[dict], n: int,
                  require_collections: bool = True,
                  require_decades: bool = True,
                  require_genres: bool = True) -> list[dict]:
    """Greedy select N items und halte dabei die Diversitaetsgarantie ein.

    Die alte Fassung filterte in der Auswahlschleife *nur* nach Collection und
    pruefte Genres/Dekaden erst hinterher. Fiel diese Pruefung durch, wurde als
    "Relaxation" ausgerechnet die Collection-Bedingung fallengelassen — im
    Stresstest landeten so zwei Filme derselben Reihe in einer 4er-Auswahl.
    Die Genre- und Dekaden-Garantie war damit faktisch nie wirksam.

    Jetzt wird waehrend der Auswahl gesteuert:

    * Durchgang 1 achtet auf die Collection-Regel und nimmt einen Kandidaten
      nur dann, wenn dadurch die Genre-/Dekaden-Quote noch erreichbar bleibt.
      Sobald nur noch so viele Plaetze frei sind, wie zum Erfuellen der Quote
      gebraucht werden, kommen ausschliesslich Kandidaten zum Zug, die
      tatsaechlich etwas Neues beitragen.
    * Durchgang 2 fuellt Restplaetze auf, die Collection-Regel gilt weiter.
    * Durchgang 3 gibt die Collection-Regel nur auf, wenn die Liste sonst
      kuerzer als N bliebe — eine zu kurze Reihe ist schlechter als eine
      wiederholte Reihe.
    """
    if n <= 0 or not candidates:
        return []

    min_genres = 2 if require_genres else 0
    min_decades = 2 if require_decades else 0

    def _genres(c) -> set:
        return set(c.get("genres") or [])

    def _cols(c) -> set:
        return set(c.get("collection_ids") or [])

    picked: list[dict] = []
    taken: set[int] = set()          # Indizes, nicht IDs — Duplikate moeglich
    used_genres: set[str] = set()
    used_decades: set[int] = set()
    used_collections: set[str] = set()

    def _take(i: int, c: dict) -> None:
        picked.append(c)
        taken.add(i)
        used_genres.update(_genres(c))
        d = _decade(c.get("year"))
        if d is not None:
            used_decades.add(d)
        used_collections.update(_cols(c))

    # --- Durchgang 1: Collection-Regel + aktive Genre-/Dekaden-Steuerung ---
    for i, c in enumerate(candidates):
        if len(picked) >= n:
            break
        if require_collections and _cols(c) & used_collections:
            continue

        slots_left = n - len(picked)
        need_genres = max(0, min_genres - len(used_genres))
        need_decades = max(0, min_decades - len(used_decades))

        # Wird es knapp, nur noch Kandidaten nehmen, die eine offene Quote bedienen.
        if slots_left <= max(need_genres, need_decades):
            d = _decade(c.get("year"))
            helps_genre = bool(_genres(c) - used_genres)
            helps_decade = d is not None and d not in used_decades
            wanted = ((need_genres and helps_genre) or
                      (need_decades and helps_decade))
            if not wanted:
                continue

        _take(i, c)

    # --- Durchgang 2: auffuellen, Collection-Regel gilt weiter ---
    if len(picked) < n:
        for i, c in enumerate(candidates):
            if len(picked) >= n:
                break
            if i in taken:
                continue
            if require_collections and _cols(c) & used_collections:
                continue
            _take(i, c)

    # --- Durchgang 3: letzte Instanz, Collection-Regel faellt ---
    if len(picked) < n and require_collections:
        for i, c in enumerate(candidates):
            if len(picked) >= n:
                break
            if i in taken:
                continue
            _take(i, c)

    return picked


def dismissed_ids(user_id: int) -> set[str]:
    """IDs, die der Nutzer als "nicht interessiert" markiert hat und deren
    Sperre noch laeuft. Abgelaufene Eintraege werden ignoriert (und vom
    Cron-Job `prune_stale` spaeter entfernt)."""
    from database import NotInterested
    now = datetime.utcnow()
    with get_session() as s:
        rows = s.exec(
            select(NotInterested).where(NotInterested.user_id == user_id)
        ).all()
    return {r.item_id for r in rows if r.until > now}


def _rewatch_candidates(user_id: int, exclude_ids: set[str], n: int) -> list[dict]:
    """Laengst nicht mehr gesehene Filme als Auffuellung fuer den Kandidatenpool.

    Sortiert nach `last_played` aufsteigend (am laengsten her zuerst), Filme
    ohne Datum zuletzt. Jeder Treffer bekommt ``rewatch=True``, damit die
    Oberflaeche ihn als "Nochmal schauen" kennzeichnen kann statt ihn
    faelschlich als Neuentdeckung zu verkaufen.
    """
    if n <= 0:
        return []
    with get_session() as s:
        rows = s.exec(
            select(WatchState).where(WatchState.user_id == user_id,
                                     WatchState.played == True)  # noqa: E712
        ).all()
    # Gesehen heisst nicht sichtbar: wurde die Freigabe spaeter entzogen,
    # steht die WatchState-Zeile noch da.
    sicht = sichtbarkeit.laden(user_id)
    rows = [w for w in rows if w.movie_id not in exclude_ids and sicht.film(w.movie_id)]
    if not rows:
        return []
    rows.sort(key=lambda w: (w.last_played is not None,
                             w.last_played or datetime.min))
    picked = _hydrate([{"movie_id": w.movie_id, "score": 0.0} for w in rows[:n * 3]])
    last_by_id = {w.movie_id: w.last_played for w in rows}
    out = []
    for c in picked:
        c["rewatch"] = True
        c["last_played"] = last_by_id.get(c["movie_id"])
        out.append(c)
        if len(out) >= n:
            break
    return out


def _pick_main(candidates: list[dict], day_key: str, forced: bool = False) -> dict:
    """Waehlt den Film des Tages aus dem vorderen Feld.

    Ohne Zufall lieferte "Neuer Vorschlag" immer denselben Titel bzw. pendelte
    zwischen zweien. Der Tag dient als Seed, damit ein normaler Seitenaufruf
    stabil bleibt; bei erzwungener Neuberechnung (`forced`) wird frei gewuerfelt.
    """
    import random
    if not candidates:
        return {}
    window = candidates[:MAIN_PICK_WINDOW]
    rng = random.Random() if forced else random.Random(day_key)
    # Diversitaetsfilter zuerst, damit das Fenster nicht aus einer Reihe stammt
    diverse = _pick_diverse(window, n=len(window)) or window
    return rng.choice(diverse)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _cache_get(key: str, allow_stale: bool = False) -> Optional[dict]:
    """Return the cached payload, or None if missing.

    If ``allow_stale`` is True, an expired entry is still returned but with
    ``stale=True`` added. This lets the API fall back to yesterday's daily
    recommendation when the embedding service / vector store are down.
    """
    with get_session() as s:
        row = s.get(RecommendationCache, key)
        if not row:
            return None
        expired = datetime.utcnow() - row.created_at > timedelta(hours=CACHE_TTL_HOURS)
        if expired and not allow_stale:
            return None
        try:
            payload = json.loads(row.payload_json)
        except Exception:
            return None
        if expired:
            payload["stale"] = True
        return payload


def _cache_put(key: str, payload: dict):
    with get_session() as s:
        row = s.get(RecommendationCache, key)
        pj = json.dumps(payload, default=str)
        if row:
            row.payload_json = pj
            row.created_at = datetime.utcnow()
        else:
            s.add(RecommendationCache(
                key=key, payload_json=pj, created_at=datetime.utcnow()))
        s.commit()


def _daily_key(user_id: int, tag: str, sprache: str) -> str:
    """Schluessel der Tagesempfehlung im Zwischenspeicher. Die Sprache steht
    drin: Begruendungen (mechanische wie die des Kurators) sind in EINER
    Sprache gespeichert, sonst kaeme nach dem Umschalten der alte Text."""
    return f"u:{user_id}:daily:{tag}:{sprache}"


def _daily_key_teile(key: str) -> tuple[str, str]:
    """``u:7:daily:2026-10-11:en`` -> ``("2026-10-11", "en")``. Alte Schluessel
    ohne Sprache stammen aus der Zeit vor der Umschaltung: deutsch."""
    teile = key.split(":")
    return (teile[3] if len(teile) > 3 else "",
            teile[4] if len(teile) > 4 else texte.STANDARD)


def _latest_stale_daily(user_id: int, sprache: Optional[str] = None) -> Optional[dict]:
    """Find the most recent daily-cache row for this user regardless of age.

    Ein Stand in der gewuenschten Sprache hat Vorrang; gibt es nur einen in der
    anderen, kommt der (besser ein Text in der falschen Sprache als keiner —
    es ist ohnehin der Notfall bei ausgefallenem Einbettungsdienst)."""
    sprache = _sp(sprache)
    with get_session() as s:
        rows = s.exec(
            select(RecommendationCache).where(
                RecommendationCache.key.like(f"u:{user_id}:daily:%")
            )
        ).all()
    if not rows:
        return None
    rows.sort(key=lambda r: r.created_at, reverse=True)
    rows = [r for r in rows if _daily_key_teile(r.key)[1] == sprache] or rows
    try:
        payload = json.loads(rows[0].payload_json)
    except Exception:
        return None
    payload["stale"] = True
    return payload


def _recent_film_of_day_ids(user_id: int, days: int = 3,
                            sprache: Optional[str] = None) -> set[str]:
    """Return the set of movie IDs that were 'film of the day' for this user
    in any of the last ``days`` days. Used to avoid repeating a main pick.

    Je Tag gibt es jetzt eine Zeile pro Sprache. Gezaehlt werden die letzten
    ``days`` TAGE; die heutige Auswahl in der ANDEREN Sprache bleibt aussen
    vor, sonst waere nach dem Umschalten genau der gerade gezeigte Film
    gesperrt und der Film des Tages wechselte mit der Sprache."""
    sprache = _sp(sprache)
    heute = datetime.utcnow().strftime("%Y-%m-%d")
    with get_session() as s:
        rows = s.exec(
            select(RecommendationCache).where(
                RecommendationCache.key.like(f"u:{user_id}:daily:%"))
        ).all()
    rows.sort(key=lambda r: r.created_at, reverse=True)
    tage: dict[str, set[str]] = {}
    for r in rows:
        tag, lang = _daily_key_teile(r.key)
        if tag == heute and lang != sprache:
            continue
        ids = tage.setdefault(tag, set())
        try:
            p = json.loads(r.payload_json)
        except Exception:
            continue
        fod = (p.get("film_of_day") or {}).get("movie_id")
        if fod:
            ids.add(fod)
    out: set[str] = set()
    for tag in sorted(tage, reverse=True)[:days]:
        out |= tage[tag]
    return out


def _auswahl_sichtbar(payload: dict, sicht) -> bool:
    """Eine gespeicherte Tagesauswahl auf das zuschneiden, was die Person
    JETZT sehen darf. Die Auswahl kann aelter sein als der letzte Abgleich
    ihrer Rechte (Zwischenspeicher, Stand von gestern bei ausgefallener
    Einbettung). Weitere Vorschlaege, Ausreisser und die Rangliste fuer
    "Neuer Vorschlag" werden gefiltert.

    Rueckgabe: ob der Film des Tages selbst sichtbar ist. Ist er es nicht,
    verwirft der Aufrufer die Auswahl und rechnet neu - ein anderer Titel an
    seiner Stelle passte nicht zu Begruendung und Kurator-Text daneben.
    """
    if not sicht.aktiv or not isinstance(payload, dict):
        return True
    for feld in ("thematic", "exploration"):
        if isinstance(payload.get(feld), list):
            payload[feld] = sicht.filtern(payload[feld])
    if isinstance(payload.get("ranking"), list):
        # Die Rangliste des Kurators enthaelt nur Filme (siehe curator).
        payload["ranking"] = [e for e in payload["ranking"]
                              if isinstance(e, dict) and sicht.film(e.get("movie_id"))]
    fod = payload.get("film_of_day")
    return not fod or sicht.eintrag(fod)


def invalidate_user_cache(user_id: int):
    with get_session() as s:
        rows = s.exec(
            select(RecommendationCache).where(
                RecommendationCache.key.like(f"u:{user_id}:%"))
        ).all()
        for r in rows:
            s.delete(r)
        s.commit()


# ---------------------------------------------------------------------------
# Daily recommendations (RAG)
# ---------------------------------------------------------------------------

# Anteil des TMDB-Schwarm-Signals am Kandidaten-Score (Rest: Profil-Aehnlichkeit).
COLLAB_WEIGHT = 0.5
# So viele TMDB-Treffer aus der Bibliothek kommen zusaetzlich in den Pool.
COLLAB_POOL = 40


def daily_candidates(user_id: int, profile: dict, hidden: set[str]) -> list[dict]:
    """Ungesehene Kandidaten fuer die Tagesempfehlung, bester zuerst.

    Frueher: die 60 naechsten Vektortreffer, danach "ungesehen" gefiltert. Bei
    254 gesehenen Filmen blieben davon im Test weniger als zwoelf uebrig; der
    Film des Tages rotierte ueber zehn Tage zwischen drei Titeln. Jetzt waechst
    die Abfrage mit der Zahl gesehener Titel, und TMDB-Treffer kommen hinzu.
    """
    seen = profile["seen_ids"]
    # Fuer das Konto verborgene Titel gar nicht erst holen: als exclude_ids
    # vergroessert die Suche ihr Fenster um genau diese Treffer, der Pool
    # bleibt also so gross wie bei einem Konto ohne Einschraenkung. Als Teil
    # von ``hidden`` fallen sie auch aus dem TMDB-Anteil (_blend_candidates).
    verborgen = sichtbarkeit.laden(user_id).verborgen()
    hidden = set(hidden) | verborgen
    raw = search_by_text(profile_to_query_text(profile), k=60 + len(seen),
                         exclude_ids=verborgen)
    unseen = [r for r in raw if r["movie_id"] not in seen and r["movie_id"] not in hidden]
    return _blend_candidates(user_id, profile, unseen, hidden)


def _blend_candidates(user_id: int, profile: dict, vector_hits: list[dict],
                      hidden: set[str]) -> list[dict]:
    """Mischt Profil-Aehnlichkeit mit dem TMDB-Schwarm-Signal.

    Beide Anteile werden auf 0..1 normiert und je zur Haelfte gewichtet.
    Treffer mit TMDB-Signal bekommen ``tmdb_because`` (Namen der Titel, ueber
    die sie gefunden wurden) fuer die Begruendung. Ohne TMDB-Daten bleibt es
    beim reinen Vektor-Ranking von vorher.
    """
    try:
        from tmdb_client import collaborative_scores
        collab = collaborative_scores(user_id)["library"]
    except Exception as e:
        log.warning("TMDB-Signal nicht verfuegbar: %s", e)
        collab = {}
    seen = profile["seen_ids"]
    collab = {k: v for k, v in collab.items()
              if v["score"] > 0 and k not in seen and k not in hidden}
    if not collab:
        return _hydrate(vector_hits)

    ids = [h["movie_id"] for h in vector_hits]
    for k in sorted(collab, key=lambda k: collab[k]["score"], reverse=True)[:COLLAB_POOL]:
        if k not in ids:
            ids.append(k)

    # Einheitliche Aehnlichkeit fuer alle Kandidaten — auch fuer TMDB-Treffer,
    # die nicht aus der Vektorsuche kamen.
    try:
        from embeddings import profile_all_similarities
        sims = profile_all_similarities(user_id)
    except Exception:
        sims = {}
    vec_by_id = {h["movie_id"]: h["score"] for h in vector_hits}

    def _vec(i: str) -> float:
        return sims.get(i, vec_by_id.get(i, 0.0))

    vals = [_vec(i) for i in ids]
    lo, hi = min(vals), max(vals)
    cmax = max(v["score"] for v in collab.values())
    scored = []
    for i in ids:
        v = (_vec(i) - lo) / (hi - lo) if hi > lo else 0.5
        c = collab[i]["score"] / cmax if i in collab else 0.0
        scored.append({"movie_id": i,
                       "score": round((1 - COLLAB_WEIGHT) * v + COLLAB_WEIGHT * c, 3)})
    scored.sort(key=lambda x: x["score"], reverse=True)

    # "Fans von ..." nennt Titel aus der eigenen Historie. Ist einer davon fuer
    # das Konto inzwischen verborgen, faellt sein Name hier weg: er stuende
    # sonst in der Begruendung und im Kurator-Prompt. Sein Beitrag zur
    # Wertung bleibt - der nennt nichts.
    verborgen = sichtbarkeit.laden(user_id).verborgen()
    verborgene_namen: set[str] = set()
    if verborgen:
        with get_session() as s:
            from database import Series
            verborgene_namen = (
                {n for i, n in s.exec(select(Movie.id, Movie.name)).all() if i in verborgen}
                | {n for i, n in s.exec(select(Series.id, Series.name)).all() if i in verborgen})

    hydrated = _hydrate(scored)
    for h in hydrated:
        if h["movie_id"] in collab:
            h["tmdb_because"] = [b for b in collab[h["movie_id"]]["because"]
                                 if b not in verborgene_namen][:3]
    return hydrated


@sichtbarkeit.mit_sicht
def daily_for(user_id: int, use_cache: bool = True, curate: bool = False,
              sprache: Optional[str] = None) -> dict:
    """Film des Tages + 3 thematische Vorschlaege + Ausreisser.

    ``sprache`` ("de"/"en"): Sprache aller Texte der Antwort. Der Zwischen-
    speicher gilt je Sprache. Hintergrundarbeit (Cron, Kurator-Thread) muss
    sie angeben, ohne Angabe gilt die der laufenden Anfrage (texte.aktuell).

    ``curate=True`` (naechtlicher Cron, Hintergrund-Thread) laesst zusaetzlich
    den KI-Kurator auswaehlen — das dauert Minuten. Ohne ``curate`` bleibt der
    Aufruf schnell: Cache, bei "Neuer Vorschlag" die Rangliste des Kurators,
    sonst mechanisch — und der Kurator wird im Hintergrund angestossen.
    """
    sprache = _sp(sprache)
    today = datetime.utcnow().strftime("%Y-%m-%d")
    key = _daily_key(user_id, today, sprache)
    sicht = sichtbarkeit.laden(user_id)
    if use_cache:
        cached = _cache_get(key)
        # Ist der Film des Tages fuer die Person inzwischen verborgen, gilt
        # der Zwischenspeicher als leer und es wird neu gerechnet.
        if cached and _auswahl_sichtbar(cached, sicht):
            _curate_in_background(user_id, cached, sprache)
            return cached
    elif not curate:
        rotated = _rotate_curated(user_id, key, sprache)
        if rotated:
            return rotated

    # If upstream services are down, fall back to the most recent cached
    # payload (any day) rather than crashing — UI shows a "stale" banner.
    # Massgeblich ist der Einbettungsdienst: ohne ihn liefert die Vektorsuche
    # keine Kandidaten. Das Sprachmodell braucht die mechanische Auswahl hier
    # nicht; ob es da ist, prueft _curate_in_background bzw. der Kurator.
    if not anbieter.einbettung_verfuegbar():
        stale = _latest_stale_daily(user_id, sprache)
        if stale and _auswahl_sichtbar(stale, sicht):
            return stale

    profile = build_profile(user_id)
    recent_fod = _recent_film_of_day_ids(user_id, days=3, sprache=sprache)
    # Verborgene Titel zaehlen fuer alles Weitere wie ausgeblendete: so
    # erreichen sie weder die mechanische Auswahl noch den Kurator
    # (curator._candidate_pool filtert ebenfalls nach ``hidden``).
    _hidden = dismissed_ids(user_id) | sicht.verborgen()
    candidates = daily_candidates(user_id, profile, _hidden)

    # Wer den Katalog weitgehend durchgeschaut hat, laeuft sonst leer: bei
    # 226 von 405 gesehenen Filmen bleiben nach dem Filter kaum Kandidaten.
    # Dann mit lange nicht gesehenen Titeln auffuellen, klar als "Nochmal
    # schauen" gekennzeichnet, damit die Reihe nicht auf zwei Titel schrumpft.
    if len(candidates) < MIN_POOL:
        candidates += _rewatch_candidates(
            user_id,
            exclude_ids={c["movie_id"] for c in candidates},
            n=MIN_POOL - len(candidates),
        )
    # Letzte Sicherung vor der Auswahl; beide Quellen filtern schon selbst.
    candidates = sicht.filtern(candidates)

    # Avoid repeating a main pick from the last 3 days for the film-of-day slot
    main_candidates = [c for c in candidates if c["movie_id"] not in recent_fod]
    if not main_candidates:
        main_candidates = candidates  # degrade gracefully

    if not candidates:
        payload = {
            "generated_at": datetime.utcnow().isoformat(),
            "profile_summary": profile_summary_text(profile),
            "film_of_day": None,
            "thematic": [],
            "exploration": [],
            "reason": _t("keine_kandidaten", sprache),
            "kuratiert": False,
        }
        _cache_put(key, payload)
        return payload

    # Pick 4 diverse films (1 of day + 3 thematic). Film-of-day comes from
    # main_candidates (no-repeat); thematic may draw from the full pool.
    #
    # Frueher wurde hier immer der bestbewertete Kandidat genommen. Da
    # `_recent_film_of_day_ids` nur den zuletzt gecachten Titel ausschliesst,
    # pendelte "Neuer Vorschlag" dadurch zwischen genau zwei Filmen hin und
    # her. Jetzt wird aus dem vorderen Feld gewuerfelt — mit dem Tag als Seed,
    # damit derselbe Tag ohne Force denselben Film zeigt.
    film_of_day = _pick_main(main_candidates, today, forced=not use_cache)
    remaining = [c for c in candidates if c["movie_id"] != film_of_day["movie_id"]]
    thematic = _pick_diverse(remaining, n=3) or remaining[:3]

    # Exploration picks — 1-2 films *outside* the user's usual bubble.
    exploration = sicht.filtern(_exploration_picks(
        user_id, profile,
        exclude_ids={film_of_day["movie_id"]} | {t["movie_id"] for t in thematic},
        n=2, sprache=sprache))

    # Build mechanical reason immediately (fast, always present)
    reason = _mechanical_reason(profile, film_of_day, user_id, sprache)

    payload = {
        "generated_at": datetime.utcnow().isoformat(),
        "profile_summary": profile_summary_text(profile),
        "film_of_day": film_of_day,
        "thematic": thematic,
        "exploration": exploration,
        "reason": reason,
        "llm_reason": None,  # optionally filled by /recommendations/daily/explain
        # Faellt der Kurator aus, stand das bisher nur als `curate_error` im
        # JSON — die Oberflaeche sah davon nichts und zeigte stillschweigend
        # die mechanische Auswahl als waere sie kuratiert. `kuratiert` ist
        # deshalb immer gesetzt (true/false), nicht nur im Erfolgsfall.
        "kuratiert": False,
    }
    if curate:
        payload = _apply_curator(user_id, profile, _hidden, recent_fod, payload,
                                 sprache)
    _cache_put(key, payload)
    if not curate:
        _curate_in_background(user_id, payload, sprache)
    return payload


# ---------------------------------------------------------------------------
# KI-Kurator (siehe curator.py)
# ---------------------------------------------------------------------------

# Hoechstens so oft pro Nutzer im Hintergrund kuratieren. Bewertungen und
# "nicht interessiert" leeren den Cache; ohne diese Sperre loeste jede
# Swipe-Bewertung einen neuen Modelllauf aus.
BG_CURATE_MIN_INTERVAL = timedelta(hours=2)
_bg_lock = threading.Lock()
# Schluessel (Nutzer, Sprache): wer die Sprache umschaltet, soll nicht bis zum
# Ablauf der Sperre auf die mechanische Auswahl in der neuen Sprache warten.
_bg_running: set[tuple[int, str]] = set()
_bg_last: dict[tuple[int, str], datetime] = {}


def _apply_curator(user_id: int, profile: dict, hidden: set[str],
                   recent_fod: set[str], payload: dict,
                   sprache: Optional[str] = None) -> dict:
    """Ersetzt die mechanische Auswahl durch die des Kurators, wenn der eine
    gueltige liefert. Sonst bleibt das mechanische Ergebnis unveraendert."""
    import curator
    sprache = _sp(sprache)
    with _bg_lock:
        _bg_last[(user_id, sprache)] = datetime.utcnow()
    payload["curate_attempted_at"] = datetime.utcnow().isoformat()
    try:
        cur = curator.curate(user_id, profile, hidden, recent_fod, sprache)
    except Exception as e:
        log.warning("Kurator fuer User %s fehlgeschlagen: %s", user_id, e)
        payload["curate_error"] = str(e)[:200]
        payload["kuratiert"] = False
        return payload
    if not cur:
        payload["curate_error"] = "keine gueltige Auswahl"
        payload["kuratiert"] = False
        return payload

    fod = cur["film_of_day"]
    fid = fod["movie_id"]
    payload.update({
        "film_of_day": fod,
        "reason": _mechanical_reason(profile, fod, user_id, sprache),
        "llm_reason": fod.get("curator_reason"),
        "thematic": cur["thematic"] or [t for t in payload.get("thematic", [])
                                        if t["movie_id"] != fid],
        "exploration": cur["exploration"] or [e for e in payload.get("exploration", [])
                                              if e["movie_id"] != fid],
        "ranking": cur["ranking"],
        "shown_ids": [fid],
        "curated": True,
        "kuratiert": True,
        "curator_model": cur["model"],
        "curator_stats": cur["stats"],
    })
    payload.pop("curate_error", None)
    return payload


def _rotate_curated(user_id: int, key: str,
                    sprache: Optional[str] = None) -> Optional[dict]:
    """"Neuer Vorschlag" bei kuratiertem Tag: naechster Titel der Rangliste,
    ohne neuen Modelllauf. ``None``, wenn nichts (mehr) da ist."""
    sprache = _sp(sprache)
    cached = _cache_get(key)
    # Verborgene Titel fallen hier aus Rangliste und Nebenreihen; der Film des
    # Tages wird ohnehin ersetzt.
    _auswahl_sichtbar(cached, sichtbarkeit.laden(user_id))
    if not cached or not cached.get("ranking"):
        return None
    shown = set(cached.get("shown_ids") or [])
    profile = build_profile(user_id)
    hidden = dismissed_ids(user_id)
    for entry in cached["ranking"]:
        mid = entry.get("movie_id")
        if not mid or mid in shown or mid in profile["seen_ids"] or mid in hidden:
            continue
        film = next(iter(_hydrate([{"movie_id": mid, "score": 0.0}])), None)
        if not film:
            continue
        cached["film_of_day"] = film
        cached["reason"] = _mechanical_reason(profile, film, user_id, sprache)
        cached["llm_reason"] = entry.get("grund")
        cached["shown_ids"] = sorted(shown | {mid})
        cached["thematic"] = [t for t in cached.get("thematic", []) if t["movie_id"] != mid]
        cached["exploration"] = [e for e in cached.get("exploration", []) if e["movie_id"] != mid]
        cached.pop("stale", None)
        _cache_put(key, cached)
        return cached
    return None


def _curate_in_background(user_id: int, payload: dict,
                          sprache: Optional[str] = None) -> None:
    """Kurator fuer diesen Nutzer anstossen, wenn der heutige Stand noch nicht
    kuratiert ist — z. B. fuer neue Nutzer oder nach einer neuen Bewertung.
    Das naechste Laden der Startseite zeigt dann die KI-Auswahl.

    Der Thread erbt die Sprache der Anfrage NICHT (ContextVar), deshalb wird
    sie hier ausdruecklich weitergegeben."""
    sprache = _sp(sprache)
    try:
        import llm_client
        if payload.get("curated") or not llm_client.grosser_kontext():
            return
    except Exception:
        return
    now = datetime.utcnow()
    bg_key = (user_id, sprache)
    with _bg_lock:
        last = _bg_last.get(bg_key)
        if bg_key in _bg_running or (last and now - last < BG_CURATE_MIN_INTERVAL):
            return
        _bg_running.add(bg_key)
        _bg_last[bg_key] = now

    def _run():
        try:
            daily_for(user_id, use_cache=False, curate=True, sprache=sprache)
        except Exception as e:
            log.warning("Hintergrund-Kurator fuer User %s fehlgeschlagen: %s", user_id, e)
        finally:
            with _bg_lock:
                _bg_running.discard(bg_key)

    threading.Thread(target=_run, daemon=True, name=f"curate-{user_id}").start()


def _exploration_picks(user_id: int, profile: dict, exclude_ids: set[str],
                       n: int = 2, sprache: Optional[str] = None) -> list[dict]:
    """Pick 1-2 films explicitly *outside* the user's current bubble.

    Strategy: find genres / decades the user has under-explored (not in their
    top-5), then query the embedding store with those terms. Require:
      - ungesehen
      - nicht in ``exclude_ids``
      - kein Überlapp mit top-3 Genres
      - andere Dekade als die dominanteste
      - community_rating >= 6.5 (damit es kein Schrott ist)
    """
    top_genres = {g for g, _ in (profile.get("top_genres") or [])[:3]}
    top_decades = {d for d, _ in (profile.get("top_decades") or [])[:1]}
    seen = profile.get("seen_ids") or set()

    # Build an "anti-profile" query: genres the user rarely watches.
    # We ask the embedding store for a few broad, varied terms.
    ALL_GENRES = ["documentary", "animation", "musical", "western", "war",
                  "historical drama", "foreign language", "arthouse",
                  "film noir", "biographical", "sports", "family adventure"]
    anti = [g for g in ALL_GENRES if g.lower() not in {tg.lower() for tg in top_genres}]
    query = "underrated gem " + " ".join(anti[:6])

    raw = search_by_text(query, k=40,
                         exclude_ids=sichtbarkeit.laden(user_id).verborgen())
    hydrated = _hydrate(raw)

    picked: list[dict] = []
    used_genres: set[str] = set()
    for c in hydrated:
        if len(picked) >= n:
            break
        mid = c["movie_id"]
        if mid in seen or mid in exclude_ids:
            continue
        c_genres = set(c.get("genres") or [])
        # Must have at least one genre that isn't in the user's top-3
        if c_genres and not (c_genres - top_genres):
            continue
        # Prefer a different decade than the user's dominant one
        c_dec = _decade(c.get("year"))
        if top_decades and c_dec in top_decades:
            continue
        # Quality floor
        if (c.get("rating") or 0) < 6.5:
            continue
        # Don't double up on the same fresh genre inside the exploration row
        if c_genres & used_genres:
            continue
        used_genres |= c_genres
        c = {**c, "exploration_reason": _exploration_reason(profile, c, sprache)}
        picked.append(c)

    # If strict filter returned nothing useful, relax the quality + decade bar
    if not picked:
        for c in hydrated:
            if len(picked) >= n:
                break
            mid = c["movie_id"]
            if mid in seen or mid in exclude_ids:
                continue
            c_genres = set(c.get("genres") or [])
            if c_genres and not (c_genres - top_genres):
                continue
            c = {**c, "exploration_reason": _exploration_reason(profile, c, sprache)}
            picked.append(c)
    return picked


def _exploration_reason(profile: dict, film: dict,
                        sprache: Optional[str] = None) -> str:
    """Short hint why this film is 'out of bubble' for the user."""
    sprache = _sp(sprache)
    # Ohne den Praefix "Raus aus der Blase": der steht schon als Reihentitel
    # darueber, und in der schmalen Karte frass die Wiederholung genau den
    # Platz, der dann fuer die eigentliche Begruendung fehlte.
    top_genres = {g for g, _ in (profile.get("top_genres") or [])[:3]}
    film_genres = [g for g in (film.get("genres") or []) if g not in top_genres]
    if film_genres:
        return _t("explore_genre", sprache, genre=texte.genre(film_genres[0], sprache))
    decade = _decade(film.get("year"))
    if decade:
        return _t("explore_dekade", sprache, decade=decade)
    return _t("explore_sonst", sprache)


@sichtbarkeit.mit_sicht
def explain_daily(user_id: int, sprache: Optional[str] = None) -> dict:
    """Enhance today's daily recommendation with an LLM-written justification.

    Blocks for 60-150s on CPU — intended to be called asynchronously from the UI
    (or skipped entirely). Updates the cached payload so subsequent calls return
    the LLM reason too. ``sprache``: Sprache der Begruendung (siehe daily_for).
    """
    sprache = _sp(sprache)
    today = datetime.utcnow().strftime("%Y-%m-%d")
    key = _daily_key(user_id, today, sprache)
    cached = _cache_get(key)
    # Die Nebenreihe geht in den Prompt ("Ausserdem im Angebot"): nur, was die
    # Person sehen darf. Ist der Film des Tages selbst verborgen, gibt es
    # nichts zu begruenden - daily_for rechnet beim naechsten Laden neu.
    if (not cached or not cached.get("film_of_day")
            or not _auswahl_sichtbar(cached, sichtbarkeit.laden(user_id))):
        return {"ok": False, "error": "no_daily_recommendation"}
    if cached.get("llm_reason"):
        return {"ok": True, "llm_reason": cached["llm_reason"], "cached": True}
    # Ist die Kuratierung ausgefallen, ist auch das Cloud-Modell weg — dann
    # antwortet hier Hermes3 auf der CPU, und dessen Antwort war am 30.09. die
    # wiederholte Prompt-Gliederung. In diesem Fall gar nicht erst fragen: die
    # mechanische Begruendung in `reason` ist richtig und bleibt stehen.
    if cached.get("curate_error"):
        return {"ok": False, "error": "nicht_kuratiert",
                "curate_error": cached["curate_error"], "kuratiert": False}

    profile = build_profile(user_id)
    llm_text = _llm_justify(profile, cached["film_of_day"], cached.get("thematic", []),
                            sprache)
    if llm_text:
        cached["llm_reason"] = llm_text
        _cache_put(key, cached)
    return {"ok": bool(llm_text), "llm_reason": llm_text}


# ---------------------------------------------------------------------------
# Belege fuer Begruendungen
# ---------------------------------------------------------------------------
# Das Profil (user_profile.build_profile) liefert nur GEWICHTE, keine Zahl der
# Titel: ein einziger Favorit mit guter Bewertung kommt auf 2,5, zwei bloss
# gesehene Filme zusammen nur auf 0,6. Ueber das Gewicht laesst sich also nicht
# entscheiden, ob hinter einem Signal ein Titel steht oder zwei — gemessen am
# 29.09.: ein Testkonto mit NULL gesehenen Filmen und einer einzigen
# Sternebewertung auf "Schindlers Liste" bekam "Regie von Steven Spielberg,
# diese Handschrift war in deiner Sehhistorie schon oefter dabei".
# Deshalb werden die Belege hier eigens gezaehlt, nach Titeln getrennt und
# nach Quelle (gesehen / bewertet / Merkliste) sortiert.


def _belege(user_id: int) -> dict:
    """Zaehlt je Signal, auf welchen VERSCHIEDENEN Titeln es beruht.

    Rueckgabe::

        {"regie":     {name: {"gesehen": {ids}, "bewertet": {ids}, "merkliste": {ids}}},
         "besetzung": {...}, "genre": {...}, "reihe": {...},
         "titel":     {titelname: "gesehen"|"bewertet"|"merkliste"},
         "gesehen_gesamt": int}

    Quellen:
      * ``gesehen``   — Film abgespielt, Serie laufend oder durchgeschaut.
      * ``bewertet``  — Herz gesetzt oder Sterne >= 7, aber NICHT abgespielt.
      * ``merkliste`` — steht nur auf der Merkliste.

    Titel mit Sternen < 4 zaehlen gar nicht: der Nutzer hat sie zwar gesehen,
    aber ausdruecklich nicht gemocht — als Beleg fuer "das magst du" waeren
    sie falsch herum.
    """
    from database import Watchlist
    leer = {"regie": {}, "besetzung": {}, "genre": {}, "reihe": {},
            "titel": {}, "gesehen_gesamt": 0}
    if user_id is None:
        return leer

    with get_session() as s:
        ws_rows = s.exec(
            select(WatchState).where(WatchState.user_id == user_id)).all()
        from database import Series, SeriesProgress
        sp_rows = s.exec(
            select(SeriesProgress).where(SeriesProgress.user_id == user_id)).all()
        wl_rows = s.exec(
            select(Watchlist).where(Watchlist.user_id == user_id)).all()

        ids = ({w.movie_id for w in ws_rows} | {x.series_id for x in sp_rows}
               | {r.item_id for r in wl_rows})
        if not ids:
            return leer
        movies = {m.id: m for m in
                  s.exec(select(Movie).where(Movie.id.in_(ids))).all()}
        series = {x.id: x for x in
                  s.exec(select(Series).where(Series.id.in_(ids))).all()}

    def _csv(text: str) -> list[str]:
        return [t.strip() for t in (text or "").split(",") if t.strip()]

    ergebnis = {"regie": {}, "besetzung": {}, "genre": {}, "reihe": {},
                "titel": {}, "gesehen_gesamt": 0}

    def _eintragen(art: str, name: str, quelle: str, titel_id: str) -> None:
        if not name:
            return
        eintrag = ergebnis[art].setdefault(
            name, {"gesehen": set(), "bewertet": set(), "merkliste": set()})
        eintrag[quelle].add(titel_id)

    def _titel_verbuchen(item_id: str, quelle: str) -> None:
        m = movies.get(item_id)
        ser = series.get(item_id)
        if m is None and ser is None:
            return
        name = m.name if m else ser.name
        # Staerkere Quelle gewinnt, falls ein Titel mehrfach auftaucht.
        rang = {"gesehen": 3, "bewertet": 2, "merkliste": 1}
        vorher = ergebnis["titel"].get(name)
        if vorher is None or rang[quelle] > rang[vorher]:
            ergebnis["titel"][name] = quelle
        if quelle == "gesehen":
            ergebnis["gesehen_gesamt"] += 1
        regie = _csv(m.directors if m else ser.creators)
        # Nur die ersten fuenf Namen der Besetzung — wie im Profil, sonst
        # zaehlt jeder Statist als Signal.
        besetzung = _csv(m.actors if m else ser.actors)[:5]
        genres = _csv(m.genres if m else ser.genres)
        reihen = _csv(m.collection_names) if m else []
        for name_ in regie:
            _eintragen("regie", name_, quelle, item_id)
        for name_ in besetzung:
            _eintragen("besetzung", name_, quelle, item_id)
        for name_ in genres:
            _eintragen("genre", name_, quelle, item_id)
        for name_ in reihen:
            _eintragen("reihe", name_, quelle, item_id)

    for ws in ws_rows:
        if ws.user_rating is not None and ws.user_rating < 4:
            continue
        if ws.played:
            quelle = "gesehen"
        elif ws.is_favorite or (ws.user_rating is not None and ws.user_rating >= 7):
            quelle = "bewertet"
        else:
            continue
        _titel_verbuchen(ws.movie_id, quelle)

    for sp in sp_rows:
        if sp.state in ("completed", "watching"):
            quelle = "gesehen"
        elif sp.is_favorite:
            quelle = "bewertet"
        else:
            continue
        _titel_verbuchen(sp.series_id, quelle)

    for r in wl_rows:
        _titel_verbuchen(r.item_id, "merkliste")

    return ergebnis


def _beleg_quelle(belege: dict, art: str, name: str,
                  mindestens: int = REASON_MIN_BELEGE) -> tuple[Optional[str], int]:
    """``(Quelle, Zahl der Titel DIESER Quelle)`` fuer ein Signal, sonst
    ``(None, 0)``.

    Die Quelle bestimmt spaeter die Formulierung: "hast du gesehen" nur bei
    tatsaechlich gesehenen Titeln, sonst "gut bewertet" bzw. "Merkliste".

    Verlangt wird, dass EINE Quelle allein auf ``mindestens`` verschiedene
    Titel kommt. Ein gesehener plus ein gemerkter Titel ergeben zusammen
    keinen Satz: er muesste entweder beide Quellen nennen (unlesbar) oder
    eine davon unterschlagen (unehrlich). Staerkste Quelle zuerst — wer
    zwei Titel gesehen UND zwei gemerkt hat, bekommt den Satz ueber das
    Gesehene.
    """
    eintrag = (belege.get(art) or {}).get(name)
    if not eintrag:
        return None, 0
    for quelle in ("gesehen", "bewertet", "merkliste"):
        if len(eintrag[quelle]) >= mindestens:
            return quelle, len(eintrag[quelle])
    return None, 0


def _best_anchor(profile: dict, film_genres: set[str],
                 belege: Optional[dict] = None) -> tuple[Optional[str], bool, str]:
    """Waehlt aus Favoriten und zuletzt Gesehenem den Titel, der dem
    empfohlenen Film inhaltlich am naechsten liegt.

    Rueckgabe: ``(Titelname, hat_gemeinsames_genre, quelle)`` mit ``quelle``
    aus ``"gesehen"`` / ``"bewertet"``. Gibt es weder Genre-Ueberlapp noch
    Favoriten, ``(None, False, "")`` — dann laesst der Aufrufer den Satz weg,
    statt einen unpassenden Bezug zu erzwingen.

    Favoriten sind NICHT automatisch gesehen (das Herz laesst sich auch ohne
    Abspielen setzen). Ob ein Titel wirklich abgespielt wurde, steht in
    ``belege["titel"]``; ohne Belege dient ``recent_movies_meta`` als
    Rueckfall — das sind die letzten acht ABGESPIELTEN Titel, also eher zu
    wenige als zu viele. Untertreiben ist hier der richtige Fehler.
    """
    favs_meta = profile.get("favorite_movies_meta") or []
    recent_meta = profile.get("recent_movies_meta") or []
    aus_belegen = {n for n, q in ((belege or {}).get("titel") or {}).items()
                   if q == "gesehen"}
    gesehene_namen = aus_belegen or {m.get("name") for m in recent_meta}

    def _quelle(name: Optional[str]) -> str:
        return "gesehen" if name in gesehene_namen else "bewertet"

    best_name, best_overlap = None, 0
    # Favoriten zaehlen staerker als blosses "zuletzt gesehen".
    for weight, pool in ((2, favs_meta), (1, recent_meta)):
        for m in pool:
            overlap = len(film_genres & set(m.get("genres") or [])) * weight
            if overlap > best_overlap:
                best_overlap, best_name = overlap, m.get("name")

    if best_name:
        return best_name, True, _quelle(best_name)

    # Kein Genre-Treffer: Favorit als schwacher Anker, sonst gar keiner.
    if favs_meta:
        name = favs_meta[0].get("name")
        return name, False, _quelle(name)
    fallback = profile.get("favorite_movies") or []
    if fallback:
        return fallback[0], False, _quelle(fallback[0])
    return None, False, ""


def _mechanical_reason(profile: dict, film: dict,
                       user_id: Optional[int] = None,
                       sprache: Optional[str] = None) -> str:
    """Varied, individual reason derived from overlap between profile and film.

    Strategy:
      1. Collect all matching signals (genre, director, actor, character,
         collection, decade, quality, runtime).
      2. Pick the strongest distinguishing signal as the headline.
      3. Deterministically vary the phrasing per film (seeded by movie_id) so
         the same film always shows the same text, but adjacent films don't
         all start with the same words.

    ``user_id`` wird fuer die Belege gebraucht (siehe `_belege`): Regie,
    Besetzung, Genre und Reihe duerfen erst behauptet werden, wenn das Signal
    auf mindestens REASON_MIN_BELEGE verschiedenen Titeln beruht, und die
    Formulierung richtet sich nach der Quelle. Ohne ``user_id`` (nur noch das
    Entwickler-Skript dev/_smoke_detail.py) entfallen diese Saetze ganz —
    lieber eine allgemeine Begruendung als eine erfundene.

    ``sprache``: Sprache des Textes ("de"/"en"); ohne Angabe die der
    laufenden Anfrage (texte.aktuell). Die Wahl der Formulierung bleibt je
    Film in beiden Sprachen dieselbe (siehe _wahl).
    """
    import random

    sprache = _sp(sprache)
    belege = _belege(user_id)
    # has_history (user_profile.py) ist schon wahr, sobald IRGENDEIN Signal
    # existiert — eine einzige Sternebewertung reicht. Fuer Saetze ueber die
    # Sehhistorie braucht es tatsaechlich abgespielte Titel.
    gesehen_gesamt = belege.get("gesehen_gesamt", 0)

    if not profile.get("has_history"):
        rating = film.get("rating")
        if rating and rating >= 7.5:
            return _t("start_stark", sprache, rating=rating)
        return _t("start", sprache)

    # Deterministic per film: movie_id seeds the phrasing picks.
    rng = random.Random(film.get("movie_id", film.get("name", "")))

    # Die Begruendung wird auch fuer die "Serie der Woche" gebaut. Vorher stand
    # dort "einer der am besten bewerteten Filme der Bibliothek" ueber einer
    # Serie. Substantive richten sich jetzt nach dem Medientyp.
    is_series = (film.get("media_type") == "series")
    art = "serie" if is_series else "film"
    noun_pl = _t("n_pl_" + art, sprache)
    noun_sg = _t("n_sg_" + art, sprache)
    noun_acc = _t("n_acc_" + art, sprache)

    user_genres = {g for g, _ in profile.get("top_genres") or []}
    film_genres = [g for g in (film.get("genres") or [])]
    genre_overlap = [g for g in film_genres if g in user_genres]

    user_dirs = {d for d, _ in profile.get("top_directors") or []}
    film_dirs = [d for d in (film.get("directors") or []) if d in user_dirs]

    # Actors live in the hydrated film dict as well (via recommendations._hydrate
    # normally drops them, so fall back to what we have)
    film_actors = film.get("actors") or []
    user_actors = {a for a, _ in profile.get("top_actors") or []}
    actor_overlap = [a for a in film_actors if a in user_actors]

    user_cols = {c for c, _ in profile.get("top_collections") or []}
    film_cols = [c for c in (film.get("collections") or []) if c in user_cols]

    # Ab hier zaehlt nicht mehr, ob der Name im Profil OBEN steht, sondern auf
    # wie vielen verschiedenen Titeln er beruht. Die Quelle bestimmt den
    # Wortlaut.
    regie_quelle = besetzung_quelle = genre_quelle = None
    if film_dirs:
        regie_quelle, _ = _beleg_quelle(belege, "regie", film_dirs[0])
        if regie_quelle is None:
            film_dirs = []
    if actor_overlap:
        # Ersten Namen suchen, der wirklich belegt ist — sonst faellt der
        # ganze Satz weg, obwohl ein anderer Name getragen haette.
        belegt = [(a, _beleg_quelle(belege, "besetzung", a)[0])
                  for a in actor_overlap]
        belegt = [(a, q) for a, q in belegt if q is not None]
        actor_overlap = [a for a, _ in belegt]
        besetzung_quelle = belegt[0][1] if belegt else None
    if genre_overlap:
        belegt_g = [(g, _beleg_quelle(belege, "genre", g)[0])
                    for g in genre_overlap]
        belegt_g = [(g, q) for g, q in belegt_g if q is not None]
        genre_overlap = [g for g, _ in belegt_g]
        genre_quelle = belegt_g[0][1] if belegt_g else None
        # Je Genre die eigene Quelle: der Satz ueber ZWEI Genres darf nur dann
        # von Gesehenem sprechen, wenn beide auch gesehen sind.
        genre_quellen = dict(belegt_g)
    # Eine Reihe darf schon ab EINEM Titel genannt werden — "einen Teil
    # kennst du" ist ein konkretes, nachpruefbares Signal. Der Satz wird dann
    # aber im Singular formuliert.
    reihe_quelle, reihe_anzahl = (None, 0)
    if film_cols:
        reihe_quelle, reihe_anzahl = _beleg_quelle(belege, "reihe", film_cols[0],
                                                   mindestens=1)
        if reihe_quelle is None:
            film_cols = []

    avg_year = profile.get("avg_year")
    film_year = film.get("year")
    rating = film.get("rating") or 0
    runtime = film.get("runtime_min")

    # Gather candidate sentences (a mix of specific + phrasing-varied templates)
    candidates: list[str] = []

    # 1) Collection / Reihe — strongest signal, very specific
    if film_cols:
        col = film_cols[0]
        mehrere = reihe_anzahl >= REASON_MIN_BELEGE
        quelle_reihe = (reihe_quelle if reihe_quelle in ("gesehen", "bewertet")
                        else "merkliste")
        candidates.append(_wahl(
            rng, f"reihe_{quelle_reihe}_{'mehrere' if mehrere else 'eine'}",
            sprache, col=col))

    # 1b) TMDB-Schwarm — nennt echte Titel aus der eigenen Historie, ueber die
    #     der Film gefunden wurde. Staerkstes Signal zuerst (Favoriten vor
    #     "nur gesehen"), siehe tmdb_client.user_seeds.
    because = film.get("tmdb_because") or []
    if len(because) >= 2:
        candidates.append(_wahl(rng, "tmdb_zwei", sprache, a=because[0],
                                b=because[1], noun_acc=noun_acc))
    elif because:
        candidates.append(_wahl(rng, "tmdb_eins", sprache, a=because[0],
                                noun_acc=noun_acc))

    # 2) Director overlap — sehr spezifisch (für Serien: Showrunner-Tonfall)
    #    Nur noch mit Belegen auf mindestens zwei Titeln, und der Satz nennt
    #    die richtige Quelle: eine Sternebewertung ist keine Sehhistorie.
    if film_dirs:
        d = film_dirs[0]
        wer = _t("wer_serie" if is_series else "wer_regie", sprache, d=d)
        if regie_quelle == "gesehen":
            candidates.append(_wahl(rng, "regie_gesehen", sprache, wer=wer, d=d,
                                    noun_pl=noun_pl))
        elif regie_quelle == "bewertet":
            # Kein "… gesehen hast du noch nichts davon": ob daneben doch ein
            # Titel abgespielt wurde, sagt diese Quelle nicht aus.
            candidates.append(_wahl(rng, "regie_bewertet", sprache, wer=wer))
        else:
            candidates.append(_t("regie_merkliste", sprache, wer=wer))

    # 3) Actor overlap — konkrete Namen
    if actor_overlap:
        a = actor_overlap[0]
        others = actor_overlap[1:2]
        others_str = _t("und_name", sprache, name=others[0]) if others else ""
        if besetzung_quelle == "gesehen":
            candidates.append(_wahl(rng, "besetzung_gesehen", sprache, a=a,
                                    others_str=others_str))
        elif besetzung_quelle == "bewertet":
            candidates.append(_t("besetzung_bewertet", sprache, a=a,
                                 others_str=others_str))
        else:
            candidates.append(_t("besetzung_merkliste", sprache, a=a,
                                 others_str=others_str))

    # 4) Genre match — spezifisch genug, wenn wir den genauen Genre nennen
    if genre_overlap:
        # Genre-Namen kommen aus Jellyfin (deutsch); fuer englische Texte
        # werden die gaengigen uebersetzt. Die Quellen laufen ueber die
        # Rohnamen (genre_quellen).
        g_roh = genre_overlap[0]
        sec_roh = genre_overlap[1] if len(genre_overlap) > 1 else None
        g = texte.genre(g_roh, sprache)
        secondary = texte.genre(sec_roh, sprache) if sec_roh else None
        if secondary and genre_quellen.get(sec_roh) == genre_quelle:
            # Nur wenn beide Genres aus DERSELBEN Quelle belegt sind, darf der
            # Satz ueber beide reden. Vorher behauptete dieser Zweig immer
            # Sehhistorie — auch bei einem Konto ohne einen einzigen
            # abgespielten Film.
            if genre_quelle == "gesehen":
                candidates.append(_wahl(rng, "genre2_gesehen", sprache, g=g,
                                        secondary=secondary))
            elif genre_quelle == "bewertet":
                candidates.append(_t("genre2_bewertet", sprache, g=g,
                                     secondary=secondary))
            else:
                candidates.append(_t("genre2_merkliste", sprache, g=g,
                                     secondary=secondary))
        else:
            # Die Genrenamen haben jedes Geschlecht — Abenteuer saechlich,
            # Komoedie weiblich, Krimi maennlich. Vorlagen mit Artikel oder
            # gebeugtem Adjektiv davor ergaben deshalb Saetze wie
            # "Klassischer Abenteuer": der Name steht jetzt immer ohne
            # Begleiter, dann stimmt jede Kombination.
            if genre_quelle == "gesehen":
                candidates.append(_wahl(rng, "genre1_gesehen", sprache, g=g,
                                        noun_sg=noun_sg))
            elif genre_quelle == "bewertet":
                candidates.append(_t("genre1_bewertet", sprache, g=g))
            else:
                candidates.append(_t("genre1_merkliste", sprache, g=g))

    # 5) Anker aus der Historie: "Passt nach deinem X"
    #
    # Frueher stand hier stumpf `recent[0]` — der zuletzt gesehene Film. Der
    # Zweig greift, sobald weder Regisseur noch Reihe ueberlappen, also bei den
    # meisten Titeln. Ergebnis: ein und derselbe Film begruendete jede
    # Empfehlung, quer durch alle Genres. Jetzt wird der Anker gewaehlt, der
    # inhaltlich am naechsten liegt; findet sich keiner, entfaellt der Satz.
    if not film_dirs and not film_cols and not because:
        anchor, shared, anchor_quelle = _best_anchor(profile, set(film_genres),
                                                    belege)
        if anchor:
            # "das du schon kennst" nur, wenn der Anker wirklich abgespielt
            # wurde — ein Herz ohne Abspielen ist kein Gesehen.
            if shared and anchor_quelle == "gesehen":
                candidates.append(_wahl(rng, "anker_gesehen", sprache, anchor=anchor))
            elif shared:
                candidates.append(_wahl(rng, "anker_bewertet", sprache, anchor=anchor))
            else:
                candidates.append(_t("anker_schwach", sprache, anchor=anchor))

    # 7) Decade / era
    #    "du schaust oft" setzt gesehene Titel voraus; avg_year steht im
    #    Profil auch dann, wenn nur bewertet wurde.
    if film_year and avg_year and abs(film_year - avg_year) <= 5:
        decade = (film_year // 10) * 10
        if gesehen_gesamt >= REASON_MIN_BELEGE:
            candidates.append(_wahl(rng, "zeit_gesehen", sprache, decade=decade,
                                    noun_pl=noun_pl, film_year=film_year))
        else:
            candidates.append(_t("zeit", sprache, film_year=film_year))

    # 8) Quality flag as a flavour add-on (not as the only reason)
    flavour: list[str] = []
    if rating >= 8:
        flavour.append(_wahl(rng, "wertung_hoch", sprache, rating=rating))
    elif rating >= 7 and len(candidates) < 2:
        flavour.append(_t("wertung_solide", sprache, rating=rating))

    if runtime:
        if runtime < 95:
            flavour.append(_wahl(rng, "laufzeit_kurz", sprache, runtime=runtime))
        elif runtime >= 150:
            flavour.append(_t("laufzeit_lang", sprache, runtime=runtime))

    # Assemble: pick 1 (or sometimes 2) headline reasons + at most 1 flavour.
    if not candidates:
        # Notnagel. Hier stand bis zum 01.10.:
        #     f"Trifft das Genre {top_genre}, das dein Profil dominiert."
        # `top_genre` ist das Lieblingsgenre des NUTZERS, ohne jeden Abgleich
        # mit den Genres des Titels. Live gesehen: "Jeffrey Epstein:
        # Stinkreich" (Dokumentarfilm, Krimi) bekam "Trifft das Genre Drama" —
        # eine Behauptung ueber den Titel, die nur ueber den Nutzer bekannt
        # war. Jetzt wird nur ein Genre genannt, das der Titel wirklich hat.
        gemeinsam = next((g for g in film_genres if g in user_genres), None)
        if gemeinsam:
            # Ohne Belege (sonst waere der Satz oben schon entstanden) nur die
            # schwache, aber nachpruefbare Aussage: das Genre steht im Profil
            # vorn, und der Titel hat es tatsaechlich.
            return _t("notnagel_genre", sprache,
                      genre=texte.genre(gemeinsam, sprache))
        # Keine Ueberschneidung: auf etwas ausweichen, das sich am Titel selbst
        # belegen laesst, statt ueber das Profil zu spekulieren.
        if rating >= 7:
            return _t("notnagel_wertung", sprache, rating=rating, noun_sg=noun_sg)
        # `film_cols` ist hier zwangslaeufig leer (sonst gaebe es oben einen
        # Kandidaten), deshalb die rohen Reihen des Titels — eine Reihe ist
        # eine Tatsache ueber den Titel, keine ueber den Nutzer.
        reihen_roh = film.get("collections") or []
        if reihen_roh:
            return _t("notnagel_reihe", sprache, reihe=reihen_roh[0])
        if film_year:
            return _t("notnagel_jahr", sprache, film_year=film_year)
        if gesehen_gesamt:
            return _t("notnagel_gesehen", sprache)
        return _t("notnagel_markiert", sprache)

    # Prefer the first (most specific) candidate, but variably append a second
    headline = candidates[0]
    extras: list[str] = []
    if len(candidates) >= 2 and rng.random() < 0.4:
        extras.append(candidates[1])
    if flavour and rng.random() < 0.7:
        extras.append(flavour[0])

    return " ".join([headline, *extras]).strip()


# ---------------------------------------------------------------------------
# Pruefung der Modellantwort fuer llm_reason
# ---------------------------------------------------------------------------
# Am 30.09. stand die ungefilterte Modellantwort im Hero der Startseite: 1880
# Zeichen statt der ueblichen 200, beginnend mit "FILM DES TAGES: Nuernberg
# (2025) … WEITERE VORSCHLÄGE: - Schindlers Liste (1993): …". Ursache: faellt
# ArliAI mit 503 aus, antwortet Hermes3 auf der CPU — und das kleine Modell
# wiederholt die Gliederung der Anfrage, statt zwei Saetze zu schreiben.
# Deshalb wird die Antwort BEIM BEFUELLEN geprueft, nicht erst beim Anzeigen:
# was durch `pruefe_llm_begruendung` faellt, wird gar nicht gespeichert, und
# die mechanische Begruendung bleibt stehen — die ist richtig.

# Ueberschriften, mit denen die Anfrage gegliedert ist. Alt (bis 30.09.) und
# neu, damit die Pruefung beide Echos erkennt — im Cache koennen noch alte
# Antworten liegen, und Modelle erinnern sich an keines von beiden Formaten.
_PROMPT_UEBERSCHRIFTEN = (
    "film des tages", "nutzerprofil", "profil des nutzers",
    "der titel:", "weitere vorschläge", "weitere vorschlaege",
    "außerdem im angebot", "ausserdem im angebot",
)

# Ab hier zaehlt das Modell die anderen Vorschlaege auf: alles davor ist
# womoeglich noch brauchbar, alles danach gehoert nicht in den Hero.
_WEITERE_MARKER = (
    "weitere vorschläge", "weitere vorschlaege", "weitere empfehlungen",
    "außerdem im angebot", "ausserdem im angebot",
)

# Zwei bis drei Saetze sind rund 200 Zeichen (die echte gute Antwort vom
# 30.09. hatte 203). 400 laesst Luft fuer einen langen dritten Satz und
# schneidet alles ab, was in Wahrheit eine Liste ist.
LLM_BEGRUENDUNG_MAX_ZEICHEN = 400


def pruefe_llm_begruendung(text: Optional[str]) -> str:
    """Macht aus einer rohen Modellantwort eine anzeigbare Begruendung — oder "".

    Reine Funktion, absichtlich ohne Seiteneffekte, damit sie einzeln testbar
    ist (siehe Tests unten im Bericht). Reihenfolge der Schritte:

    1. Alles ab der Ueberschrift fuer die weiteren Vorschlaege abschneiden.
    2. Verwerfen, wenn der Rest als Listeneintrag anfaengt.
    3. Verwerfen, wenn der Rest mit der Prompt-Gliederung anfaengt (oder sie
       nach einer Vorrede nachschiebt).
    4. Verwerfen, wenn der Rest laenger als LLM_BEGRUENDUNG_MAX_ZEICHEN ist.
    5. Verwerfen, wenn der Rest wie eine Aufzaehlung mehrerer Titel aussieht
       (mehrere Listenzeilen oder mehrere Jahreszahlen in Klammern).

    Verworfen heisst: leerer String. Der Aufrufer speichert dann nichts.
    """
    if not isinstance(text, str) or not text.strip():
        return ""
    rest = text.strip()

    # 1) Abschneiden am Marker fuer die weiteren Vorschlaege.
    klein = rest.lower()
    treffer = [p for p in (klein.find(m) for m in _WEITERE_MARKER) if p >= 0]
    if treffer:
        rest = rest[:min(treffer)].strip()
    if not rest:
        return ""

    # 2) Faengt die Antwort als Listeneintrag an, ist sie eine Aufzaehlung und
    #    keine Begruendung — auch dann, wenn nur noch ein Eintrag uebrig ist.
    if re.match(r'^\s*(?:[-*•–—]|\d+[.)])\s', rest):
        return ""

    # 3) Wiederholte Prompt-Gliederung. Fuehrende Deko (Sternchen, Raute,
    #    Anfuehrungszeichen) zaehlt nicht mit, "- " bleibt absichtlich stehen,
    #    das hat Schritt 2 schon erledigt.
    ohne_deko = re.sub(r'^[\s*_#`>"„»\[(]+', "", rest).lower()
    if ohne_deko.startswith(_PROMPT_UEBERSCHRIFTEN):
        return ""
    # Die Gliederung kann auch nach einer Vorrede kommen ("Gerne! FILM DES
    # TAGES: …"). Nur mit Doppelpunkt suchen: "der Film des Tages passt zu
    # dir" ist ein voellig normaler Satz und darf nicht daran scheitern.
    if any(f"{u}:" in ohne_deko for u in ("film des tages", "nutzerprofil",
                                          "profil des nutzers")):
        return ""

    # 4) Laenge.
    if len(rest) > LLM_BEGRUENDUNG_MAX_ZEICHEN:
        return ""

    # 5) Aufzaehlung mehrerer Titel.
    if len(re.findall(r'(?m)^\s*(?:[-*•–—]|\d+[.)])\s+', rest)) >= 2:
        return ""
    if len(re.findall(r'\(\s*(?:18|19|20)\d{2}\s*\)', rest)) >= 2:
        return ""
    return rest


def _llm_justify(profile: dict, film_of_day: dict, thematic: list[dict],
                 sprache: Optional[str] = None) -> str:
    """Kurze LLM-Begruendung (Cloud-Modell, sonst Hermes3). Still leer bei Ausfall.

    Leer auch dann, wenn die Antwort die Pruefung nicht besteht — lieber keine
    Begruendung als die wiederholte Prompt-Gliederung im Hero.

    Der Prompt bleibt deutsch; die Antwortsprache haengt texte.antwortsprache an.
    """
    import llm_client
    if not llm_client.is_available():
        return ""
    sprache = _sp(sprache)
    # Fuer Englisch faellt "auf Deutsch" weg, damit der Prompt der angehaengten
    # Antwortsprache nicht widerspricht.
    auf_deutsch = ", auf Deutsch" if sprache == "de" else ""
    system = (
        "Du bist ein freundlicher Film-Kurator. Du bekommst ein Nutzerprofil und "
        "eine Liste von Filmempfehlungen und schreibst eine kurze, persönliche "
        f"Begründung (2-3 Sätze{auf_deutsch}), warum genau der erste Titel zum "
        "Nutzer passt. Erwähne konkret wenigstens eine Eigenschaft (Genre, Regie, "
        "Schauspieler, Sammlung oder Thema), die du in der Sehhistorie findest. "
        "Sei ehrlich und nicht schleimig. Antworte in reinem Fließtext: keine "
        "Überschriften, keine Aufzählung, keine Liste von Titeln."
    ) + texte.antwortsprache(sprache)
    # Neutrale Ueberschriften: die alten ("FILM DES TAGES:", "WEITERE
    # VORSCHLÄGE:") lasen sich wie eine Antwortvorlage, und genau die hat das
    # kleine Modell zurueckgespiegelt. So sieht ein Echo nicht mehr wie eine
    # gueltige Antwort aus — und `pruefe_llm_begruendung` erkennt beides.
    user_msg = (
        f"Profil des Nutzers:\n{profile_summary_text(profile)}\n\n"
        f"Der Titel:\n- {film_of_day['name']} ({film_of_day.get('year','?')}) "
        f"— Genre: {', '.join(film_of_day.get('genres') or [])}\n"
        f"  Plot: {(film_of_day.get('overview') or '')[:400]}\n\n"
        f"Außerdem im Angebot:\n" +
        "\n".join(f"- {t['name']} ({t.get('year','?')})" for t in thematic)
    )
    try:
        resp = llm_client.complete(
            [{"role": "system", "content": system},
             {"role": "user", "content": user_msg}],
            aufgabe="begruendung",
        )
    except Exception as e:
        log.warning("LLM justify failed: %s", e)
        return ""
    geprueft = pruefe_llm_begruendung(resp if isinstance(resp, str) else "")
    import ki_protokoll
    ki_protokoll.auswertung("begruendung", bool(geprueft), "verworfen")
    if not geprueft and isinstance(resp, str) and resp.strip():
        log.warning("LLM-Begruendung verworfen (%s Zeichen): %.80s",
                    len(resp.strip()), resp.strip().replace("\n", " "))
    return geprueft


# ---------------------------------------------------------------------------
# Similar-to (with collection bias)
# ---------------------------------------------------------------------------

@sichtbarkeit.mit_sicht
def similar_to(user_id: int, movie_id: str, k: int = 12) -> dict:
    """Vector search with collection boost. Prefers unseen movies."""
    profile = build_profile(user_id)
    seen = profile["seen_ids"]
    sicht = sichtbarkeit.laden(user_id)

    base = _load_movie(movie_id)
    # Ein verborgener Ausgangsfilm verhaelt sich wie ein unbekannter: auch
    # seine Nachbarn verrieten, worum es in ihm geht.
    if not base or not sicht.film(movie_id):
        return {"movie_id": movie_id, "similar": [], "unseen_only": []}

    base_collections = set(
        c for c in (base.collection_ids or "").split(",") if c)

    # Pull generous candidate set, then re-rank with collection bonus
    raw = search_similar(movie_id, k=max(k * 3, 30), exclude_ids=sicht.verborgen())
    hydrated = sicht.filtern(_hydrate(raw))

    # Apply collection boost for unseen movies
    for it in hydrated:
        cols = set(it.get("collection_ids") or [])
        if cols & base_collections and it["movie_id"] not in seen:
            it["score"] = round(min(1.0, it["score"] * (1 + COLLECTION_BOOST)), 3)
            it["collection_boost"] = True

    hydrated.sort(key=lambda x: x["score"], reverse=True)

    _hidden = dismissed_ids(user_id)
    unseen = [it for it in hydrated
              if it["movie_id"] not in seen and it["movie_id"] not in _hidden]
    diverse = _pick_diverse(unseen, n=k) if len(unseen) >= k else unseen

    return {
        "movie_id": movie_id,
        "base_name": base.name,
        "similar": hydrated[:k],
        "unseen_only": diverse,
    }


# ---------------------------------------------------------------------------
# Mood search
# ---------------------------------------------------------------------------
# Schwelle und Prozent-Abbildung kommen je Einbettungsmodell aus
# embeddings.skala() (mood_min, mood_floor, mood_ceil; Begruendung oben in
# dieser Datei), die Reihen-Regel aus MOOD_MAX_REIHE_AM_STUECK.


def _mood_reihenfolge(items: list[dict], n: int) -> list[dict]:
    """Strikt nach Aehnlichkeit absteigend — Vielfalt nur als Nebenbedingung.

    Vorher lieferte `_pick_diverse` die Reihenfolge, und die richtet sich nach
    Genre-/Dekaden-Quoten statt nach der Frage. Im Test kam dabei "… 61, 60,
    84 …" heraus: der beste Treffer stand an dritter Stelle. Die Passung zur
    SUCHE entscheidet jetzt die Rangfolge.

    Einzige Nebenbedingung: hoechstens ``MOOD_MAX_REIHE_AM_STUECK`` Titel
    derselben Reihe direkt hintereinander. Gibt es keinen Titel aus einer
    anderen Reihe, bleibt es bei der Rangfolge — Passung schlaegt Vielfalt,
    und kein Treffer wird deswegen ganz nach hinten geschoben.
    """
    if n <= 0 or not items:
        return []
    # ``rang`` setzt _genre_gewichten; ohne ihn gilt der Rohwert.
    pool = sorted(items, key=lambda it: it.get("rang", it.get("score") or 0),
                  reverse=True)
    out: list[dict] = []
    letzte_reihe: set[str] = set()   # Collection-IDs des zuletzt genommenen Titels
    am_stueck = 0

    while pool and len(out) < n:
        wahl = 0
        if am_stueck >= MOOD_MAX_REIHE_AM_STUECK and letzte_reihe:
            # Naechstbesten Titel suchen, der NICHT aus derselben Reihe kommt.
            for i, c in enumerate(pool):
                if not (set(c.get("collection_ids") or []) & letzte_reihe):
                    wahl = i
                    break
        c = pool.pop(wahl)
        reihen = set(c.get("collection_ids") or [])
        am_stueck = am_stueck + 1 if (reihen and reihen & letzte_reihe) else 1
        letzte_reihe = reihen
        out.append(c)
    return out


@sichtbarkeit.mit_sicht
def by_mood(user_id: int, mood: str, k: int = 12,
            media_type: str | None = None,
            max_age: Optional[int] = None,
            sprache: Optional[str] = None) -> dict:
    """Freitext-Suche nach Stimmung.

    ``max_age`` ist ein HARTER Altersfilter (Hoechstalter in Jahren). Wird er
    nicht uebergeben, erkennt `altersgrenze_aus_text` Kinder-Anfragen selbst —
    ohne Modellaufruf und ohne zusaetzliche Frage in der Oberflaeche.

    ``results`` kann leer sein: taucht kein Treffer ueber ``mood_min`` der
    Skala (embeddings.skala) auf, wird lieber nichts gezeigt als Rauschen mit
    Prozentzahl. ``hinweis`` traegt dann den Grund fuer die Oberflaeche
    (in der Sprache ``sprache``, ohne Angabe die der Anfrage).
    """
    if max_age is None:
        max_age = altersgrenze_aus_text(mood)

    profile = build_profile(user_id)
    seen = profile["seen_ids"]
    # Mit Altersfilter muss das Fenster groesser sein, sonst siebt er die
    # Trefferliste leer: bei Hoechstalter 6 sind nur rund ein Viertel der
    # Bibliothek zulaessig, und davon geht das Gesehene noch ab — aus 36
    # geholten Kandidaten blieben gemessen drei Vorschlaege.
    gefragt = genres_aus_text(mood)
    # Auch bei einem Genre-Wort braucht es das grosse Fenster: die passenden
    # Titel liegen dann oft weiter hinten (Interstellar erst auf Platz 7).
    fenster = (max(k * 3, 30) if max_age is None and not gefragt
               else max(k * 12, 120))
    # Verborgene Titel (und bei Suchen mit Folgen deren Folgen) schon in der
    # Suche ueberspringen, damit das Fenster gleich gross bleibt.
    sicht = sichtbarkeit.laden(user_id)
    raw = search_by_text(normalize_user_query(mood), k=fenster,
                         media_type=media_type,
                         exclude_ids=sicht.verborgen(folgen=media_type in ("any", "episode")))
    hydrated = sicht.filtern(_hydrate(raw))

    # Altersfilter zuerst, damit auch der Rueckfall auf gesehene Titel weiter
    # unten nichts wieder hereinlaesst, was gerade aussortiert wurde.
    if max_age is not None:
        hydrated = [it for it in hydrated if _erlaubt_bis_alter(it, max_age)]

    _hidden = dismissed_ids(user_id)
    unseen = [it for it in hydrated
              if it["movie_id"] not in seen and it["movie_id"] not in _hidden]
    # Hat der Nutzer alles Passende schon gesehen, sind gesehene Titel immer
    # noch eine bessere Antwort als eine leere Liste.
    kandidaten = unseen or hydrated
    _genre_gewichten(kandidaten, gefragt)
    # Erst die Schwelle, dann Reihenfolge und Anzahl: sonst belegten
    # Genre-Treffer unter der Schwelle Plaetze und fielen danach heraus.
    ergebnisse = _mood_reihenfolge(_scale_mood_scores(kandidaten), k)

    hinweis = ""
    if not ergebnisse:
        if max_age is not None and not hydrated:
            hinweis = _t("hinweis_alter", sprache)
        else:
            hinweis = _t("hinweis_treffer", sprache)

    return {
        "mood": mood,
        "max_age": max_age,
        "genres": sorted(gefragt),
        "results": ergebnisse,
        "hinweis": hinweis,
    }


def _scale_mood_scores(items: list[dict]) -> list[dict]:
    """Schwache Treffer wegwerfen und den Rest ABSOLUT in Prozent umrechnen.

    Vorher wurde die Spanne der jeweiligen Anfrage auf 60-100 gedehnt. Lagen
    alle Treffer innerhalb von drei Prozent Rohabstand — der Normalfall bei
    deutschen Anfragen an nomic-embed-text —, wurde daraus "100 %" fuer den
    besten Zufallstreffer. Die Prozentzahl behauptete einen Unterschied, den
    es nicht gab.

    Jetzt: alles unter ``mood_min`` fliegt raus, der Rest wird gegen die feste
    Skala ``mood_floor``..``mood_ceil`` gerechnet. Bleibt nichts uebrig, ist
    das Ergebnis eine LEERE Liste — der Aufrufer meldet "keine guten Treffer".
    ``score`` bleibt in jedem Fall der Rohwert.

    Die drei Werte kommen bei jedem Aufruf aus embeddings.skala(), weil sie
    am Einbettungsmodell haengen und das im Admin-Bereich wechseln kann.
    """
    if not items:
        return []
    s = skala()
    # ``wert`` = Rohwert plus Genre-Bonus (siehe _genre_gewichten).
    stark = [it for it in items if it.get("wert", it.get("score") or 0) >= s["mood_min"]]
    if not stark:
        return []
    spanne = s["mood_ceil"] - s["mood_floor"]
    for it in stark:
        roh = it.get("wert", it.get("score") or 0)
        anteil = 1.0 if spanne <= 0 else (roh - s["mood_floor"]) / spanne
        it["relevanz"] = int(round(max(0.0, min(1.0, anteil)) * 100))
    return stark


# ---------------------------------------------------------------------------
# Quiz / Discover (Phase 9) — questionnaire → recommendation without typing
# ---------------------------------------------------------------------------

# Map discrete answers → vector-query fragments (English, for nomic). Profile
# preferences still mix in via the final query string.
_MOOD_MAP = {
    "relaxed":   "calm gentle heartwarming feel-good ruhig gemuetlich warmherzig leise wohlfuehlen",
    "exciting":  "thrilling action-packed adrenaline suspenseful spannend rasant packend nervenaufreibend",
    "thoughtful":"thought-provoking profound contemplative drama nachdenklich tiefgruendig ruhig erzaehlt",
    "funny":     "hilarious lighthearted comedy witty amusing lustig komisch albern humorvoll",
}
_FAMILIARITY_MAP = {
    "classic":    "classic timeless acclaimed",
    "unseen":     "",  # no query bias; enforced via filter below
    "surprise":   "unexpected unusual hidden gem",
}
_COMPANY_MAP = {
    "alone":   "introspective immersive",
    "partner": "romantic relationship warm",
    "friends": "crowd-pleaser group-friendly entertaining",
}


@sichtbarkeit.mit_sicht
def quiz_recommend(user_id: int, answers: dict, n: int = 3,
                   max_age: Optional[int] = None,
                   sprache: Optional[str] = None) -> dict:
    """Answer dict keys: mood, time_bucket, familiarity, company, genre.

    - ``time_bucket``: "short" (<90), "medium" (~120), "any"
    - ``familiarity``: "classic" (seen before OK), "unseen" (only new), "surprise"
    - ``genre``: optional; "any" or omitted for no filter
    - ``max_age``: HARTER Altersfilter (Hoechstalter in Jahren), optional.

    Der Assistent selbst stellt bewusst KEINE Alters-Frage — ``max_age`` kommt
    von aussen (z. B. aus dem Chat) oder bleibt leer.
    """
    mood = answers.get("mood") or "exciting"
    time_bucket = answers.get("time_bucket") or "any"
    familiarity = answers.get("familiarity") or "surprise"
    company = answers.get("company") or "alone"
    genre = (answers.get("genre") or "").strip()
    media_type = answers.get("media_type") or "any"

    # Die Stimmung wiegt schwerer als Begleitung und Bekanntheitsgrad: im Test
    # kamen auf "entspannt, wohlfuehlig" Jumper (Action) und Euphoria (Sucht,
    # Trauma) heraus, weil der Stimmungsbegriff im Abfragetext unterging.
    query_parts = [
        _MOOD_MAP.get(mood, ""),
        _MOOD_MAP.get(mood, ""),
        _FAMILIARITY_MAP.get(familiarity, ""),
        _COMPANY_MAP.get(company, ""),
    ]
    if genre and genre.lower() != "any":
        query_parts.append(genre.lower())
    query = " ".join(p for p in query_parts if p).strip() or "great movie"

    profile = build_profile(user_id)
    seen = profile["seen_ids"]

    # Pull a wide candidate pool — the vector search filters on type
    typ_filter = media_type if media_type in ("movie", "series") else None
    sicht = sichtbarkeit.laden(user_id)
    raw = search_by_text(query, k=60, media_type=typ_filter,
                         exclude_ids=sicht.verborgen())
    hydrated = sicht.filtern(_hydrate(raw))

    # Der Altersfilter greift VOR allen anderen Filtern und wird unten auch
    # nicht gelockert: die Lockerungsstufen arbeiten alle auf `hydrated`, das
    # hier schon bereinigt ist. Titel ohne Freigabe fliegen dabei mit raus
    # (siehe `_erlaubt_bis_alter`) — sonst rutschten die zehn ungeprueften
    # Filme durch einen Filter, der gerade fuer Kinder gesetzt wurde.
    if max_age is not None:
        hydrated = [m for m in hydrated if _erlaubt_bis_alter(m, max_age)]

    def matches_runtime(m: dict) -> bool:
        rt = m.get("runtime_min")
        if time_bucket == "short":
            return rt is not None and rt < 90
        if time_bucket == "medium":
            return rt is not None and rt <= 130
        return True

    def matches_familiarity(m: dict) -> bool:
        is_seen = m["movie_id"] in seen
        if familiarity == "unseen":
            return not is_seen
        if familiarity == "classic":
            return is_seen
        return True  # surprise: either is fine

    def matches_genre(m: dict) -> bool:
        if not genre or genre.lower() == "any":
            return True
        # Gespeichert sind deutsche Genrenamen, das Quiz schickt sie seit der
        # Umstellung ebenfalls deutsch. Beide Seiten laufen trotzdem durch die
        # Uebersetzung: so trifft auch ein englischer Wert (alter Client,
        # Direktaufruf der API, englisch gebliebene Rohdaten) noch.
        from jellyfin import translate_genre
        gesucht = translate_genre(genre).lower()
        g_lower = [translate_genre(g).lower() for g in (m.get("genres") or [])]
        return gesucht in g_lower

    # Apply hard filters, then diversity
    filtered = [m for m in hydrated
                if matches_runtime(m) and matches_familiarity(m)
                and matches_genre(m)]

    # Bei zu wenigen Treffern werden Genre und Bekanntheitsgrad gelockert —
    # die Laufzeit NICHT. Vorher fiel sie als Erstes weg, und auf "unter
    # 90 min" kam "Armageddon" mit 151 Minuten.
    if len(filtered) < n and genre and genre.lower() != "any":
        filtered = [m for m in hydrated
                    if matches_runtime(m) and matches_familiarity(m)]
    if len(filtered) < n:
        filtered = [m for m in hydrated if matches_runtime(m)]

    picked = _pick_diverse(filtered, n=n) if len(filtered) >= n else filtered[:n]

    # Bewertung aus zwei Teilen: wie gut der Treffer zur *Stimmung* passt
    # (Aehnlichkeit zur Abfrage) und wie gut zum Profil. Nur nach Profil zu
    # sortieren hat die Stimmungsauswahl wirkungslos gemacht — auf "entspannt"
    # stand "Armageddon" oben, weil es zum Actionprofil passt.
    from embeddings import profile_movie_similarity
    roh = [p.get("score") or 0 for p in picked] or [0]
    lo, hi = min(roh), max(roh)
    results = []
    for p in picked:
        sim = profile_movie_similarity(user_id, p["movie_id"])
        profil = 50 if sim is None else int(round(max(0.0, min(1.0, (sim - 0.3) / 0.5)) * 100))
        stimmung = 100 if hi <= lo else int(round(((p.get("score") or 0) - lo) / (hi - lo) * 100))
        results.append({
            **p,
            "match_score": int(round(0.6 * stimmung + 0.4 * profil)),
            "stimmung_score": stimmung,
            "profil_score": profil,
            "match_reason": _mechanical_reason(profile, p, user_id, sprache),
        })

    # Bester Treffer zuerst — bei drei Ergebnissen faellt eine Reihenfolge wie
    # 77, 67, 69 sofort auf.
    results.sort(key=lambda r: r["match_score"], reverse=True)

    return {
        "query": query,
        "answers": answers,
        "max_age": max_age,
        "filtered_count": len(filtered),
        "results": results,
    }


# ---------------------------------------------------------------------------
# Weekly series pick (TV-C)
# ---------------------------------------------------------------------------

def _weekly_in_sprache(user_id: int, cache_key: str, payload: dict,
                       sprache: str) -> dict:
    """Die Begruendung der Serie der Woche steht in der Sprache, in der sie
    gebaut wurde (``payload["sprache"]``, alte Eintraege: deutsch). Hat der
    Nutzer inzwischen umgeschaltet, wird NUR der Text neu gebaut — die Serie
    bleibt dieselbe, sonst wechselte mit der Sprache auch die Empfehlung."""
    if payload.get("sprache", texte.STANDARD) == sprache or not payload.get("series"):
        return payload
    try:
        profile = build_profile(user_id)
        payload["reason"] = _mechanical_reason(
            profile, {**payload["series"], "media_type": "series"}, user_id, sprache)
        payload["sprache"] = sprache
        with get_session() as s:
            row = s.get(RecommendationCache, cache_key)
            if row:
                row.payload_json = json.dumps(payload, ensure_ascii=False)
                s.add(row)
                s.commit()
    except Exception as e:
        log.warning("Serie der Woche: Begruendung in %s nicht gebaut: %s", sprache, e)
    return payload


@sichtbarkeit.mit_sicht
def weekly_series_pick(user_id: int, sprache: Optional[str] = None) -> dict:
    """One curated series suggestion for the current ISO calendar week.

    Strategy:
      1. Build user profile, embed query.
      2. Pull top-K series from the vector store filtered to type='series'.
      3. Drop those the user is already engaged with (watching/completed/
         favorite) — this is the "discover something new" slot.
      4. Diversity: prefer a different genre than last week's pick.
      5. Cache under key `u:{id}:weekly:{YYYY-WW}` for 7 days.
    """
    from datetime import datetime
    sprache = _sp(sprache)
    iso_year, iso_week, _ = datetime.utcnow().isocalendar()
    cache_key = f"u:{user_id}:weekly:{iso_year}-{iso_week:02d}"

    sicht = sichtbarkeit.laden(user_id)
    with get_session() as s:
        cached = s.get(RecommendationCache, cache_key)
    if cached:
        try:
            fertig = json.loads(cached.payload_json)
        except Exception:
            fertig = None
        if fertig is not None:
            serie = fertig.get("series") or {}
            # Eine inzwischen verborgene Serie der Woche wird nicht
            # ausgeliefert, sondern ersetzt (der Eintrag unten ueberschreibt
            # den alten).
            if not serie or sicht.serie(serie.get("movie_id")):
                return _weekly_in_sprache(user_id, cache_key, fertig, sprache)

    profile = build_profile(user_id)
    seen = profile["seen_ids"]
    query = profile_to_query_text(profile) or "great acclaimed tv series"

    raw = search_by_text(query, k=40, media_type="series",
                         exclude_ids=sicht.verborgen())
    hydrated = sicht.filtern(_hydrate(raw))
    candidates = [it for it in hydrated
                  if it["movie_id"] not in seen
                  and it["movie_id"] not in dismissed_ids(user_id)]
    if not candidates and hydrated:
        candidates = hydrated  # fall back if user has watched everything

    pick = None
    last_week_genres: set[str] = set()
    # Look at last week's pick for genre-diversity
    with get_session() as s:
        rows = s.exec(
            select(RecommendationCache)
            .where(RecommendationCache.key.like(f"u:{user_id}:weekly:%"))
            .order_by(RecommendationCache.created_at.desc())
        ).all()
    for row in rows[:1]:
        try:
            prev = json.loads(row.payload_json)
            for g in (prev.get("series") or {}).get("genres", []):
                last_week_genres.add(g)
        except Exception:
            pass

    # First pass: pick something with no genre overlap with last week
    for c in candidates:
        if not (set(c.get("genres") or []) & last_week_genres):
            pick = c
            break
    if pick is None and candidates:
        pick = candidates[0]

    if not pick:
        return {"week": f"{iso_year}-W{iso_week:02d}", "series": None,
                "reason": _t("keine_serie", sprache)}

    payload = {
        "week": f"{iso_year}-W{iso_week:02d}",
        "series": pick,
        "match_score": int(round(max(0.0, min(1.0, (pick.get("score") or 0)
                                              if False else 0.5)) * 100)),
        "reason": _mechanical_reason(profile, {**pick, "media_type": "series"},
                                     user_id, sprache),
        "sprache": sprache,
    }

    with get_session() as s:
        s.merge(RecommendationCache(
            key=cache_key,
            payload_json=json.dumps(payload, ensure_ascii=False),
            created_at=datetime.utcnow(),
        ))
        s.commit()
    return payload
