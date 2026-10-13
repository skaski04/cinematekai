"""CinematekAI database models — Phase 2 schema with multi-user support."""
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field, create_engine, Session
from config import DB_PATH


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    jellyfin_user_id: str = Field(index=True, unique=True)
    jellyfin_username: str
    display_name: str = ""
    is_admin: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_login_at: Optional[datetime] = None
    # "de", "en" oder leer (= Vorgabe aus den Einstellungen), siehe texte.py
    language: str = ""


class UserSession(SQLModel, table=True):
    token: str = Field(primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    jf_access_token: str = ""
    expires_at: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Movie(SQLModel, table=True):
    id: str = Field(primary_key=True)
    name: str
    year: Optional[int] = None
    runtime_min: Optional[int] = None
    community_rating: Optional[float] = None
    official_rating: Optional[str] = None
    genres: str = ""
    studios: str = ""
    directors: str = ""
    actors: str = ""
    characters: str = ""          # csv of character/role names
    overview: str = ""
    tagline: str = ""
    collection_ids: str = ""      # csv of Jellyfin BoxSet IDs
    collection_names: str = ""    # csv, denormalized for search
    # TMDB-ID aus Jellyfins ProviderIds — Schluessel fuer die "Wer das mochte,
    # mochte auch"-Listen und fuer den Abgleich mit Filmen ausserhalb der Sammlung.
    tmdb_id: Optional[str] = None
    # Ton- und Untertitelsprachen als kommagetrennte ISO-639-1-Kuerzel, in der
    # Reihenfolge der Spuren in der Datei ("de,en"). Leerer String heisst
    # "keine Angabe" — NICHT "kein Ton": 32 Filme haben Tonspuren ohne
    # Sprachkennung. Wer danach filtert, muss den Unterschied beachten.
    audio_languages: str = ""
    subtitle_languages: str = ""
    date_added: Optional[datetime] = None
    embedded: bool = False
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Series(SQLModel, table=True):
    id: str = Field(primary_key=True)              # Jellyfin Series-ID
    name: str
    year: Optional[int] = None
    end_year: Optional[int] = None                 # None = laufend
    runtime_min_avg: Optional[int] = None
    community_rating: Optional[float] = None
    official_rating: Optional[str] = None
    genres: str = ""
    studios: str = ""
    creators: str = ""                             # csv (People.Type=='Creator')
    actors: str = ""
    characters: str = ""                           # csv (People.Role)
    overview: str = ""
    tagline: str = ""
    # Gezaehlt aus der Episode-Tabelle (sync.recount_series_episodes), nicht aus
    # Jellyfins RecursiveItemCount — das lieferte durchweg zu kleine Werte
    # (Bonanza 32 statt 431 Folgen) und verfaelschte auch den Fortschritt.
    season_count: int = 0                          # ohne Staffel 0
    episode_count: int = 0                         # regulaere Folgen, ohne Specials
    special_count: int = 0                         # Staffel 0: Making-of, Recaps
    status: str = "continuing"                     # 'continuing' | 'ended'
    tmdb_id: Optional[str] = None                  # siehe Movie.tmdb_id
    # Wie Movie.audio_languages — aber nicht aus dem Serien-Eintrag selbst:
    # eine Serie ist in Jellyfin ein Ordner ohne Mediendatei und liefert gar
    # keine MediaStreams. Die Werte entstehen im Episoden-Sync als Vereinigung
    # ueber alle vorhandenen Folgen (siehe sync.sync_episodes_for_series).
    audio_languages: str = ""
    subtitle_languages: str = ""
    date_added: Optional[datetime] = None
    embedded: bool = False
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Collection(SQLModel, table=True):
    id: str = Field(primary_key=True)
    name: str
    overview: str = ""
    movie_count: int = 0


class Episode(SQLModel, table=True):
    id: str = Field(primary_key=True)              # Jellyfin Episode-ID
    series_id: str = Field(foreign_key="series.id", index=True)
    season: Optional[int] = None
    episode: Optional[int] = None
    name: str = ""
    overview: str = ""
    runtime_min: Optional[int] = None
    air_date: Optional[datetime] = None
    embedded: bool = False
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class SeriesProgress(SQLModel, table=True):
    user_id: int = Field(foreign_key="user.id", primary_key=True)
    series_id: str = Field(foreign_key="series.id", primary_key=True)
    episodes_watched: int = 0
    total_episodes: int = 0
    next_up_id: Optional[str] = None       # Jellyfin Episode-ID
    next_up_label: str = ""                # "S2E5"
    last_played: Optional[datetime] = None
    is_favorite: bool = False
    state: str = "not_started"             # not_started|watching|completed|abandoned
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class WatchState(SQLModel, table=True):
    user_id: int = Field(foreign_key="user.id", primary_key=True)
    movie_id: str = Field(foreign_key="movie.id", primary_key=True)
    played: bool = False
    play_count: int = 0
    last_played: Optional[datetime] = None
    user_rating: Optional[float] = None
    is_favorite: bool = False
    # Resume position — Jellyfin tracks PlaybackPositionTicks for partially
    # watched movies. > 0 AND played==False = "continue watching" candidate.
    playback_position_ticks: int = 0
    runtime_ticks: int = 0   # mirror of Movie.RunTimeTicks for % calc
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class SichtbarerTitel(SQLModel, table=True):
    """Ein Film oder eine Serie, den bzw. die ein Konto laut Jellyfin sehen
    darf (Bibliotheksfreigaben, Altersgrenze, gesperrte Tags; siehe
    sichtbarkeit.py).

    Eine Zeile je Person und Titel statt einer JSON-Liste: so lassen sich die
    seitenweise abgerufenen Listen (limit/offset) direkt im SQL filtern, und
    der Bild-Proxy prueft einen einzelnen Titel ueber den Primaerschluessel,
    statt fuer jedes Poster die ganze Menge zu laden. ``item_id`` steht vor
    ``media_type``, damit die Abfrage "Person + Titel" den Index nutzt.
    """
    user_id: int = Field(foreign_key="user.id", primary_key=True)
    item_id: str = Field(primary_key=True)
    media_type: str = Field(primary_key=True)        # "movie" | "series"


class SichtbarkeitStand(SQLModel, table=True):
    """Ob und wann die sichtbare Menge einer Person je Typ gespeichert wurde.

    Trennt "noch nie abgeglichen" (keine Zeile: kein Filter, Verhalten wie bis
    1.0.0) von "abgeglichen, sieht nichts" (Zeile mit ``anzahl`` 0). Ohne diese
    Unterscheidung saehe ein frisch aktualisiertes System bis zum ersten Sync
    fuer alle eine leere Bibliothek.
    """
    user_id: int = Field(foreign_key="user.id", primary_key=True)
    media_type: str = Field(primary_key=True)        # "movie" | "series"
    anzahl: int = 0
    synced_at: datetime = Field(default_factory=datetime.utcnow)


class SyncState(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    last_sync_at: Optional[datetime] = None
    last_movie_count: int = 0


class RecommendationCache(SQLModel, table=True):
    key: str = Field(primary_key=True)
    payload_json: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ChatMessage(SQLModel, table=True):
    """Persisted chat history. Kept 24h so the user can close the tab
    during a slow LLM generation and come back later to read the reply."""
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    role: str                               # 'user' | 'assistant'
    content: str = ""
    status: str = "complete"                # 'streaming' | 'complete' | 'error'
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class ChatPreload(SQLModel, table=True):
    """Pre-computed answers for the default chat suggestion chips.
    Generated nightly so clicking a chip feels instant instead of waiting
    60-120 s for Hermes3."""
    user_id: int = Field(foreign_key="user.id", primary_key=True)
    prompt_key: str = Field(primary_key=True)       # stable slug for the chip
    prompt: str                                     # the actual question shown
    answer: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Watchlist(SQLModel, table=True):
    """Merkliste — rein lokal.

    Jellyfin kennt keine Watchlist, deshalb gibt es hier nichts nach draussen
    zu spiegeln (anders als bei Favoriten und Bewertungen). `item_id` zeigt je
    nach `media_type` auf einen Film oder eine Serie; bewusst ohne
    Fremdschluessel, damit ein geloeschter Titel den Eintrag nicht blockiert —
    der Cron-Job `prune_stale` raeumt Waisen weg.
    """
    user_id: int = Field(foreign_key="user.id", primary_key=True)
    item_id: str = Field(primary_key=True)
    media_type: str = "movie"            # "movie" | "series"
    added_at: datetime = Field(default_factory=datetime.utcnow)


class NotInterested(SQLModel, table=True):
    """„Nicht interessiert" — blendet einen Titel zeitlich begrenzt aus.

    Bewusst mit Ablaufdatum statt dauerhaft: Geschmack aendert sich, und eine
    unwiderrufliche Sperre waere fuer einen Haushaltskatalog zu grob.
    """
    user_id: int = Field(foreign_key="user.id", primary_key=True)
    item_id: str = Field(primary_key=True)
    until: datetime                      # bis wann ausgeblendet
    created_at: datetime = Field(default_factory=datetime.utcnow)


class TmdbRecommendation(SQLModel, table=True):
    """TMDB-Liste „Wer das mochte, mochte auch" je Quelltitel, zwischengespeichert.

    TMDB leitet diese Listen aus dem Verhalten seiner Nutzer ab. Das ist die
    Schwarm-Information, die eine reine Inhaltsaehnlichkeit ueber Embeddings
    nicht hat — "Blade Runner" und "Drive" liegen im Vektorraum weit
    auseinander, bei TMDB aber nah beieinander.

    ``payload_json`` enthaelt die Treffer als Liste von Dicts
    (tmdb_id, title, year, poster_path, overview, vote_average, vote_count).

    TMDB erlaubt hoechstens 6 Monate Zwischenspeicherung; Zeilen aelter als
    170 Tage loescht tmdb_client.aufraeumen() taeglich.
    """
    source_tmdb_id: str = Field(primary_key=True)
    media_type: str = Field(primary_key=True)      # "movie" | "tv"
    payload_json: str = "[]"
    fetched_at: datetime = Field(default_factory=datetime.utcnow)


class LlmAufruf(SQLModel, table=True):
    """Ein Aufruf eines Sprachmodells — fuer das KI-Protokoll (ki_protokoll.py).

    Ob ein Hauptmodell samt Rueckfall auf Dauer traegt, soll sich an echten
    Zahlen zeigen statt am Eindruck:
    jede Anfrage, ob gelungen, gescheitert oder auf den Rueckfall ausgewichen,
    steht hier. Das Container-Log taugt dafuer nicht, es wird nach wenigen Tagen
    rotiert. Aufbewahrt wird 180 Tage (Aufraeumen im naechtlichen prune_stale).
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    zeit: datetime = Field(default_factory=datetime.utcnow, index=True)   # UTC
    aufgabe: str = Field(default="sonstiges", index=True)  # chat, kurator, begruendung, ...
    rolle: str = "haupt"             # haupt | rueckfall
    anbieter: str = ""
    modell: str = ""
    ok: bool = True
    fehlerart: str = ""              # zeitueberschreitung, http_504, leer, abgebrochen, ...
    fehlertext: str = ""             # gekuerzt, ohne Schluessel
    dauer_ms: int = 0
    erstes_wort_ms: Optional[int] = None   # nur bei gestreamten Antworten
    tokens_ein: Optional[int] = None
    tokens_aus: Optional[int] = None


_engine = create_engine(
    f"sqlite:///{DB_PATH}", echo=False,
    connect_args={"check_same_thread": False},
)


def init_db():
    SQLModel.metadata.create_all(_engine)


def get_session() -> Session:
    return Session(_engine)
