"""Jellyfin-REST-Client fuer CinematekAI.

Adresse, API-Schluessel und Bibliothekskonto stehen in den Einstellungen
(Abschnitt ``jellyfin``) und werden bei JEDEM Aufruf frisch gelesen. Bis
Oktober 2026 waren es Modulkonstanten aus der config.yaml: ein im
Admin-Bereich geaenderter Schluessel haette erst nach einem Neustart gegolten,
und ein frischer Container ohne Datei waere schon beim Import gescheitert.

Fehlt einer der Werte, wirft jeder Aufruf ``NichtEingerichtet``. main.py
macht daraus eine 503 mit Verweis auf /setup — sonst ginge eine Anfrage an
"/Items" ohne Host hinaus und kaeme als unverstaendlicher httpx-Fehler zurueck.
"""
from datetime import datetime
from typing import Iterable, Optional
from urllib.parse import quote, urlparse
import httpx, logging, os, re, uuid

import config
import settings
import texte

log = logging.getLogger("cinematekai.jellyfin")


# Meldungen, die in der Oberflaeche landen (Einrichtung, Verbindungstest,
# Admin-Bereich). Erst beim Ausloesen gebaut, in der Sprache der Anfrage.
_T = {
    "de": {
        "adresse_fehlt": "Jellyfin-Adresse fehlt",
        "schluessel_fehlt": "Jellyfin-API-Schluessel fehlt",
        "konto_fehlt": "Jellyfin-Bibliothekskonto fehlt",
        "adresse_schema": "Die Adresse muss mit http:// oder https:// beginnen, "
                          "z. B. http://192.168.1.10:8096",
        "adresse_web": "Gib nur die Adresse des Servers an, ohne /web und alles danach "
                       "(z. B. http://192.168.1.10:8096).",
        "zertifikat": "Dem Zertifikat von {basis} vertraut CinematekAI nicht. "
                      "Nimm die interne http-Adresse (z. B. http://192.168.1.10:8096).",
        "zeitueberschreitung": "{basis} antwortet nicht (Zeitüberschreitung). "
                               "Stimmen Adresse und Port?",
        "nicht_erreichbar": "{basis} ist nicht erreichbar ({roh}). Stimmen Adresse und Port?",
        "weiterleitung": "{basis} leitet weiter. Trag bitte direkt die Zieladresse ein.",
        "weiterleitung_ziel": "{basis} leitet weiter auf {ziel}. "
                              "Trag bitte direkt die Zieladresse ein.",
        "kein_jellyfin_http": "Unter {basis} antwortet kein Jellyfin (HTTP {status}).",
        "kein_jellyfin": "Unter {basis} antwortet ein Server, aber kein Jellyfin.",
        "anderes_produkt": "Unter {basis} läuft {produkt}, nicht Jellyfin.",
        "assistent_offen": "Jellyfin ist selbst noch nicht eingerichtet. "
                           "Schließ zuerst den Einrichtungsassistenten von Jellyfin ab.",
        "nur_admins": "Jellyfin lässt nur Administratoren API-Schlüssel anlegen.",
        "anlegen_abgelehnt": "Jellyfin hat das Anlegen des API-Schlüssels abgelehnt "
                             "(HTTP {status}).",
        "schluesselliste_fehlt": "Jellyfin hat die Liste der API-Schlüssel nicht geliefert "
                                 "(HTTP {status}).",
        "nicht_in_liste": "Jellyfin hat den API-Schlüssel angelegt, aber nicht in der Liste "
                          "geführt. Leg ihn im Jellyfin-Dashboard an und trag ihn selbst ein.",
        "kein_schluessel": "Für diese Adresse ist kein API-Schlüssel hinterlegt.",
        "schluessel_abgelehnt": "Jellyfin lehnt den API-Schlüssel ab.",
        "benutzerliste_http": "Jellyfin hat die Benutzerliste nicht geliefert (HTTP {status}).",
        "keine_benutzerliste": "Unter {basis} kam keine Benutzerliste zurück — ist das Jellyfin?",
    },
    "en": {
        "adresse_fehlt": "Jellyfin address missing",
        "schluessel_fehlt": "Jellyfin API key missing",
        "konto_fehlt": "Jellyfin library account missing",
        "adresse_schema": "The address must start with http:// or https://, "
                          "e.g. http://192.168.1.10:8096",
        "adresse_web": "Enter only the server address, without /web and anything after it "
                       "(e.g. http://192.168.1.10:8096).",
        "zertifikat": "CinematekAI does not trust the certificate of {basis}. "
                      "Use the internal http address (e.g. http://192.168.1.10:8096).",
        "zeitueberschreitung": "{basis} is not responding (timeout). "
                               "Are the address and port correct?",
        "nicht_erreichbar": "{basis} is not reachable ({roh}). Are the address and port correct?",
        "weiterleitung": "{basis} redirects. Please enter the target address directly.",
        "weiterleitung_ziel": "{basis} redirects to {ziel}. "
                              "Please enter the target address directly.",
        "kein_jellyfin_http": "No Jellyfin is responding at {basis} (HTTP {status}).",
        "kein_jellyfin": "A server is responding at {basis}, but it is not Jellyfin.",
        "anderes_produkt": "{basis} is running {produkt}, not Jellyfin.",
        "assistent_offen": "Jellyfin itself is not set up yet. "
                           "Finish the Jellyfin setup wizard first.",
        "nur_admins": "Jellyfin only lets administrators create API keys.",
        "anlegen_abgelehnt": "Jellyfin refused to create the API key (HTTP {status}).",
        "schluesselliste_fehlt": "Jellyfin did not return the list of API keys "
                                 "(HTTP {status}).",
        "nicht_in_liste": "Jellyfin created the API key but did not list it. "
                          "Create one in the Jellyfin dashboard and enter it yourself.",
        "kein_schluessel": "No API key is stored for this address.",
        "schluessel_abgelehnt": "Jellyfin rejects the API key.",
        "benutzerliste_http": "Jellyfin did not return the user list (HTTP {status}).",
        "keine_benutzerliste": "No user list came back from {basis} — is that Jellyfin?",
    },
}


def _text(schluessel: str, **werte) -> str:
    return texte.waehle(_T, schluessel, **werte)


class NichtEingerichtet(RuntimeError):
    """Jellyfin-Adresse, API-Schluessel oder Bibliothekskonto fehlen.

    Eigene Klasse, damit main.py sie gezielt in eine 503 mit Hinweis auf die
    Einrichtung uebersetzen kann. Ein fehlender Wert ist kein Ausfall von
    Jellyfin und soll auch nicht so aussehen.
    """


def _url() -> str:
    """Adresse, unter der das BACKEND Jellyfin erreicht (ohne / am Ende)."""
    url = (settings.get("jellyfin")["url"] or "").strip().rstrip("/")
    if not url:
        raise NichtEingerichtet(_text("adresse_fehlt"))
    return url


# Jellyfin 12.0 wertet X-Emby-Token, X-MediaBrowser-Token und ?api_key= nicht
# mehr aus — jede Anfrage damit endet in 401. Seit dem Update am 13.09.2026
# lief dadurch kein Sync mehr. Akzeptiert wird nur noch der Authorization-Header.
def _token_kopf(schluessel: str) -> dict:
    return {"Authorization": f'MediaBrowser Token="{schluessel}"'}


def _headers() -> dict:
    """Kopfzeile mit dem gespeicherten API-Schluessel."""
    schluessel = (settings.get("jellyfin")["api_key"] or "").strip()
    if not schluessel:
        raise NichtEingerichtet(_text("schluessel_fehlt"))
    return _token_kopf(schluessel)


def _bib_nutzer() -> str:
    """Jellyfin-ID des Kontos, dessen Bibliothek synchronisiert wird.

    Die Bibliotheksabfragen laufen mit ?userId= dieses Kontos. Welches Konto
    im Admin-Bereich gewaehlt ist, bestimmt also, welche Bibliotheken
    CinematekAI ueberhaupt sieht.
    """
    uid = (settings.get("jellyfin")["user_id"] or "").strip()
    if not uid:
        raise NichtEingerichtet(_text("konto_fehlt"))
    return uid


# Jellyfin-IDs sind GUIDs. In seinen Antworten (und damit in der Datenbank)
# stehen sie als 32 Hexzeichen ohne Bindestriche; die Form mit Bindestrichen
# nimmt Jellyfin in Pfaden ebenso an und ist deshalb auch erlaubt.
_ITEM_ID = re.compile(
    r"[0-9a-fA-F]{32}"
    r"|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def ist_item_id(wert) -> bool:
    """Sieht ``wert`` aus wie eine Jellyfin-ID?

    Fuer Endpunkte, die eine ID aus der Anfrage ungeprueft an Jellyfin
    weiterreichen (Bild-Proxy, Trailer). Ohne die Pruefung baute eine ID wie
    "x?foo=1" oder ".." eine andere Jellyfin-Adresse - abgerufen mit dem
    Admin-Schluessel des Backends. fullmatch statt match mit "$": "$" laesst
    einen Zeilenumbruch am Ende durchgehen.
    """
    return isinstance(wert, str) and _ITEM_ID.fullmatch(wert) is not None


def _id_pfad(item_id: str) -> str:
    """ID fuer den Einbau in einen URL-Pfad, vollstaendig kodiert.

    Fuer echte IDs (Hexzeichen) aendert sich nichts. Es ist die zweite
    Sicherung neben ``ist_item_id``: rutscht doch einmal etwas anderes
    durch, wird daraus ein einzelnes, harmloses Pfadstueck statt eines
    weiteren Pfadabschnitts oder einer Abfrage.
    """
    return quote(str(item_id), safe="")


def _basis(url: Optional[str]) -> str:
    """Von aussen uebergebene Adresse (Einrichtung, Verbindungstest) pruefen.

    Die Meldungen gehen so an die Oberflaeche. Abgefangen wird vor allem die
    aus der Adresszeile kopierte Web-Oberflaeche (".../web/#/home"): damit
    antwortet Jellyfin auf keine einzige API-Route.
    """
    url = (url or "").strip().rstrip("/")
    if not re.match(r"^https?://[^\s/]+", url):
        raise ValueError(_text("adresse_schema"))
    teile = urlparse(url)
    if teile.fragment or teile.query or "/web/" in teile.path + "/":
        raise ValueError(_text("adresse_web"))
    return url


_FIELDS = ",".join([
    "Genres", "ProductionYear", "RunTimeTicks", "CommunityRating",
    "OfficialRating", "Overview", "Taglines", "Studios", "People",
    # MediaStreams traegt Ton- und Untertitelsprachen. Gemessen am 29.09.2026
    # an der ganzen Bibliothek: 450 Filme, 5,57 MB ohne / 9,28 MB mit dem Feld,
    # 9,3 s gegen 10,7 s. Fuer einen naechtlichen Lauf ist das vertretbar.
    "MediaStreams",
    "DateCreated", "UserData", "ProviderIds",
])


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _ticks_to_min(ticks: Optional[int]) -> Optional[int]:
    if not ticks:
        return None
    return int(ticks / 10_000_000 / 60)


# ---------------------------------------------------------------------------
# Ton- und Untertitelsprachen
# ---------------------------------------------------------------------------

# Jellyfin liefert in ``MediaStreams[].Language`` dreistellige ISO-639-2-Codes,
# je nach Datei in der bibliografischen oder der terminologischen Variante
# ("ger" neben "deu", "fre" neben "fra"). ``DisplayLanguage`` ist an der
# getesteten Instanz durchgehend null, taugt also nicht. Gespeichert wird das zweistellige
# ISO-639-1-Kuerzel, weil Oberflaeche und Chat damit arbeiten ("auf Deutsch"
# -> "de") und weil sonst zwei Schreibweisen derselben Sprache nebeneinander
# in der Spalte staenden.
_ISO2 = {
    "deu": "de", "ger": "de", "eng": "en", "fra": "fr", "fre": "fr",
    "spa": "es", "ita": "it", "nld": "nl", "dut": "nl", "por": "pt",
    "pol": "pl", "rus": "ru", "ukr": "uk", "ces": "cs", "cze": "cs",
    "slk": "sk", "slo": "sk", "slv": "sl", "hrv": "hr", "srp": "sr",
    "bos": "bs", "mkd": "mk", "mac": "mk", "bul": "bg", "ron": "ro",
    "rum": "ro", "hun": "hu", "ell": "el", "gre": "el", "tur": "tr",
    "swe": "sv", "dan": "da", "nor": "no", "nob": "nb", "nno": "nn",
    "fin": "fi", "isl": "is", "ice": "is", "est": "et", "lav": "lv",
    "lit": "lt", "cat": "ca", "eus": "eu", "baq": "eu", "glg": "gl",
    "gle": "ga", "cym": "cy", "wel": "cy", "lat": "la", "epo": "eo",
    "zho": "zh", "chi": "zh", "jpn": "ja", "kor": "ko", "tha": "th",
    "vie": "vi", "ind": "id", "msa": "ms", "may": "ms", "hin": "hi",
    "ben": "bn", "tam": "ta", "tel": "te", "mal": "ml", "mar": "mr",
    "guj": "gu", "kan": "kn", "pan": "pa", "urd": "ur", "ara": "ar",
    "heb": "he", "fas": "fa", "per": "fa", "hye": "hy", "arm": "hy",
    "kat": "ka", "geo": "ka", "aze": "az", "kaz": "kk", "uzb": "uz",
    "swa": "sw", "zul": "zu", "afr": "af", "amh": "am", "sqi": "sq",
    "alb": "sq", "yid": "yi", "tgl": "tl",
}

# Diese Codes sagen nichts ueber die Sprache aus: "und" = nicht bestimmt,
# "zxx" = kein sprachlicher Inhalt, "mis"/"mul" = mehrsprachig bzw. sonstiges.
# Sie zu speichern hiesse, eine Angabe vorzutaeuschen, die es nicht gibt.
_KEINE_SPRACHE = {"und", "zxx", "mis", "mul", "unknown", "undetermined"}


def _sprachkuerzel(roh: Optional[str]) -> Optional[str]:
    """ISO-Code aus Jellyfin auf ein kleingeschriebenes Kuerzel bringen.

    Unbekannte Codes bleiben unveraendert stehen (nur kleingeschrieben) —
    lieber ein ungewohntes Kuerzel in der Spalte als eine verworfene Angabe.
    """
    code = (roh or "").strip().lower().replace("_", "-")
    if not code:
        return None
    code = code.split("-", 1)[0]          # "de-DE" -> "de"
    if code in _KEINE_SPRACHE:
        return None
    return _ISO2.get(code, code)


def sprachen_aus_streams(streams) -> Optional[tuple[str, str]]:
    """``(Tonsprachen, Untertitelsprachen)`` als kommagetrennte Kuerzel.

    Gibt ``None`` zurueck, wenn Jellyfin ueberhaupt kein ``MediaStreams``
    mitgeschickt hat. Das ist bewusst von ``("", "")`` unterschieden:
    "nicht abgefragt" darf im Sync vorhandene Werte nicht ueberschreiben,
    "abgefragt, aber nichts getaggt" schon.

    Die Reihenfolge folgt den Spuren in der Datei. Jellyfin stellt die
    Standardspur voran, damit steht auch hier vorn, was der Zuschauer ohne
    Zutun hoert.
    """
    if streams is None:
        return None
    audio: list[str] = []
    subs: list[str] = []
    for st in streams:
        typ = st.get("Type")
        if typ == "Audio":
            ziel = audio
        elif typ == "Subtitle":
            ziel = subs
        else:
            continue
        kuerzel = _sprachkuerzel(st.get("Language"))
        if kuerzel and kuerzel not in ziel:
            ziel.append(kuerzel)
    return ",".join(audio), ",".join(subs)


# ---------------------------------------------------------------------------
# Library fetching (uses shared API key)
# ---------------------------------------------------------------------------

# Die Routen /Users/{userId}/Items und /Users/{userId}/Items/{itemId} hat
# Jellyfin als veraltet markiert: es fuehrt sie nur noch zur
# Rueckwaertskompatibilitaet und nimmt sie nicht mehr in seine
# OpenAPI-Beschreibung auf (in der von 12.1 nachgesehen). Entfernt sind sie
# nicht, sie antworten weiterhin - aber als veraltete Route kann jede kommende
# Version sie streichen, und dann fiele der Sync still aus. Deshalb die
# dokumentierte Form: derselbe Endpunkt ohne Nutzer im Pfad, der Nutzer als
# ?userId=. (Zu Favoriten und Bewertungen siehe set_favorite und set_rating.)
def _items_url() -> str:
    return f"{_url()}/Items"


def fetch_all_movies() -> list[dict]:
    url = _items_url()
    params = {
        "userId": _bib_nutzer(),
        "IncludeItemTypes": "Movie",
        "Recursive": "true",
        "Fields": _FIELDS,
        "EnableImages": "false",
        "Limit": 10000,
    }
    with httpx.Client(timeout=60.0, headers=_headers()) as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        return r.json().get("Items", [])


def fetch_all_collections() -> list[dict]:
    """Fetch all BoxSet items (movie collections / franchises)."""
    url = _items_url()
    params = {
        "userId": _bib_nutzer(),
        "IncludeItemTypes": "BoxSet",
        "Recursive": "true",
        "Fields": "Overview",
        "Limit": 10000,
    }
    with httpx.Client(timeout=60.0, headers=_headers()) as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        return r.json().get("Items", [])


def fetch_collection_movie_ids(collection_id: str) -> list[str]:
    """Return movie IDs that belong to a BoxSet."""
    url = _items_url()
    params = {
        "userId": _bib_nutzer(),
        "ParentId": collection_id,
        "Limit": 10000,
    }
    with httpx.Client(timeout=30.0, headers=_headers()) as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        return [it["Id"] for it in r.json().get("Items", []) if it.get("Type") == "Movie"]


def normalize(item: dict) -> dict:
    """Transform a Jellyfin item into a flat Movie-row dict (no user fields)."""
    people = item.get("People") or []
    directors = [p["Name"] for p in people if p.get("Type") == "Director"]
    actors = [p["Name"] for p in people if p.get("Type") == "Actor"][:10]
    # Characters: role names from all people entries
    characters = []
    for p in people:
        role = (p.get("Role") or "").strip()
        if role:
            characters.append(role)
    daten = {
        "id": item["Id"],
        "name": item.get("Name") or "Unknown",
        "year": item.get("ProductionYear"),
        "runtime_min": _ticks_to_min(item.get("RunTimeTicks")),
        "community_rating": item.get("CommunityRating"),
        "official_rating": item.get("OfficialRating"),
        "genres": ",".join(item.get("Genres") or []),
        "studios": ",".join([s["Name"] for s in (item.get("Studios") or [])]),
        "directors": ",".join(directors),
        "actors": ",".join(actors),
        "characters": ",".join(characters),
        "overview": item.get("Overview") or "",
        "tagline": ((item.get("Taglines") or [""])[:1] or [""])[0],
        "tmdb_id": (item.get("ProviderIds") or {}).get("Tmdb") or None,
        "date_added": _parse_dt(item.get("DateCreated")),
    }
    # Die Sprachschluessel werden nur gesetzt, wenn Jellyfin MediaStreams
    # mitgeschickt hat. Sonst fehlen sie im Dict — und der Sync, der stur
    # ueber die Schluessel iteriert, laesst die gespeicherten Werte in Ruhe,
    # statt sie bei einer Abfrage ohne das Feld auf "" zurueckzusetzen.
    sprachen = sprachen_aus_streams(item.get("MediaStreams"))
    if sprachen is not None:
        daten["audio_languages"], daten["subtitle_languages"] = sprachen
    return daten


def extract_user_data(item: dict) -> dict:
    """Extract user-specific fields from a Jellyfin item response."""
    ud = item.get("UserData") or {}
    return {
        "played": bool(ud.get("Played")),
        "play_count": int(ud.get("PlayCount") or 0),
        "last_played": _parse_dt(ud.get("LastPlayedDate")),
        "user_rating": ud.get("Rating"),
        "is_favorite": bool(ud.get("IsFavorite")),
        "playback_position_ticks": int(ud.get("PlaybackPositionTicks") or 0),
        "runtime_ticks": int(item.get("RunTimeTicks") or 0),
    }


# ---------------------------------------------------------------------------
# Per-user watch state
# ---------------------------------------------------------------------------

_SERIES_FIELDS = ",".join([
    "Genres", "ProductionYear", "EndDate", "Status", "RunTimeTicks",
    "CommunityRating", "OfficialRating", "Overview", "Taglines", "Studios",
    "People", "DateCreated", "ChildCount", "RecursiveItemCount", "ProviderIds",
])


def fetch_all_series() -> list[dict]:
    """Fetch all Series items from any Jellyfin library."""
    url = _items_url()
    params = {
        "userId": _bib_nutzer(),
        "IncludeItemTypes": "Series",
        "Recursive": "true",
        "Fields": _SERIES_FIELDS,
        "EnableImages": "false",
        "Limit": 5000,
    }
    with httpx.Client(timeout=60.0, headers=_headers()) as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        return r.json().get("Items", [])


# Serien-Genres kommen aus Jellyfin auf Englisch, Film-Genres auf Deutsch.
# Damit waren "Komoedie" und "Comedy" getrennte Werte: ein gemeinsamer Filter
# ueber Filme und Serien konnte nicht funktionieren, und im Geschmacksprofil
# zaehlte dieselbe Vorliebe zweimal getrennt.
_GENRE_DE = {
    "adventure": "Abenteuer", "animation": "Animation", "comedy": "Komödie",
    "crime": "Krimi", "documentary": "Dokumentarfilm", "drama": "Drama",
    "family": "Familie", "fantasy": "Fantasy", "game show": "Spielshow",
    "history": "Historie", "horror": "Horror", "kids": "Kinder",
    "mini-series": "Miniserie", "music": "Musik", "musical": "Musical",
    "mystery": "Mystery", "news": "Nachrichten", "politics": "Politik",
    "reality": "Reality", "romance": "Liebesfilm", "science fiction": "Science Fiction",
    "soap": "Seifenoper", "sport": "Sport", "suspense": "Thriller",
    "talk show": "Talkshow", "thriller": "Thriller", "travel": "Reisen",
    "tv movie": "TV-Film", "war": "Kriegsfilm", "western": "Western",
    "action": "Action", "biography": "Biografie", "food": "Essen",
    "home and garden": "Haus und Garten", "special interest": "Spezialinteresse",
}


def translate_genre(name: str) -> str:
    return _GENRE_DE.get((name or "").strip().lower(), (name or "").strip())


def normalize_series(item: dict) -> dict:
    """Transform a Jellyfin Series item into a flat Series-row dict."""
    people = item.get("People") or []
    creators = [p["Name"] for p in people
                if p.get("Type") in ("Creator", "Writer", "Director")]
    # de-duplicate while preserving order
    seen = set(); creators_dedup = []
    for c in creators:
        if c not in seen:
            seen.add(c); creators_dedup.append(c)
    creators = creators_dedup[:4]
    actors = [p["Name"] for p in people if p.get("Type") == "Actor"][:10]
    characters = [
        (p.get("Role") or "").strip()
        for p in people if (p.get("Role") or "").strip()
    ]

    end_dt = _parse_dt(item.get("EndDate"))
    end_year = end_dt.year if end_dt else None
    status = (item.get("Status") or "Continuing").lower()
    if status not in ("continuing", "ended"):
        status = "continuing"

    daten = {
        "id": item["Id"],
        "name": item.get("Name") or "Unknown",
        "year": item.get("ProductionYear"),
        "end_year": end_year,
        "runtime_min_avg": _ticks_to_min(item.get("RunTimeTicks")),
        "community_rating": item.get("CommunityRating"),
        "official_rating": item.get("OfficialRating"),
        "genres": ",".join(translate_genre(g) for g in (item.get("Genres") or [])),
        "studios": ",".join([s["Name"] for s in (item.get("Studios") or [])]),
        "creators": ",".join(creators),
        "actors": ",".join(actors),
        "characters": ",".join(characters),
        "overview": item.get("Overview") or "",
        "tagline": ((item.get("Taglines") or [""])[:1] or [""])[0],
        # ChildCount = direct children (seasons), RecursiveItemCount = total episodes.
        "season_count": int(item.get("ChildCount") or 0),
        "episode_count": int(item.get("RecursiveItemCount") or 0),
        "status": status,
        "tmdb_id": (item.get("ProviderIds") or {}).get("Tmdb") or None,
        "date_added": _parse_dt(item.get("DateCreated")),
    }
    # Eine Serie ist in Jellyfin ein Ordner ohne eigene Mediendatei und liefert
    # deshalb kein MediaStreams (an der laufenden Instanz fuer alle 26 Serien
    # geprueft). Die Sprachen der Serie entstehen im Episoden-Sync als
    # Vereinigung ueber ihre Folgen. Der Zweig bleibt trotzdem stehen: liefert
    # eine kuenftige Jellyfin-Version doch etwas, wird es uebernommen — und bis
    # dahin fehlen die Schluessel, sodass der Sync die aus den Folgen
    # zusammengetragenen Werte nicht ueberschreibt.
    sprachen = sprachen_aus_streams(item.get("MediaStreams"))
    if sprachen is not None:
        daten["audio_languages"], daten["subtitle_languages"] = sprachen
    return daten


def fetch_user_series_data(jf_user_id: str) -> list[dict]:
    """Per-user Series payload — UserData (Played, last played, favorite,
    watched-children-count) for every series in the library. Uses shared API
    key (admin can read any user's UserData)."""
    url = _items_url()
    params = {
        "userId": jf_user_id,
        "IncludeItemTypes": "Series",
        "Recursive": "true",
        "Fields": "UserData,RecursiveItemCount",
        "EnableImages": "false",
        "Limit": 5000,
    }
    with httpx.Client(timeout=60.0, headers=_headers()) as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        return r.json().get("Items", [])


def fetch_user_next_up(jf_user_id: str) -> dict[str, dict]:
    """Map Series-ID → next-up Episode info. One Jellyfin call.

    Returns ``{series_id: {episode_id, season, episode, name}}``.
    Used to fill ``SeriesProgress.next_up_id`` / ``next_up_label``.
    """
    url = f"{_url()}/Shows/NextUp"
    params = {
        "UserId": jf_user_id,
        "Limit": 500,
        "Fields": "SeriesId",
    }
    with httpx.Client(timeout=30.0, headers=_headers()) as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        items = r.json().get("Items", [])
    out: dict[str, dict] = {}
    for it in items:
        sid = it.get("SeriesId")
        if not sid:
            continue
        out[sid] = {
            "episode_id": it.get("Id"),
            "season": it.get("ParentIndexNumber"),
            "episode": it.get("IndexNumber"),
            "name": it.get("Name") or "",
        }
    return out


def fetch_series_episodes(series_id: str, jf_user_id: Optional[str] = None,
                          mit_streams: bool = False) -> list[dict]:
    """Fetch all episodes of a series. UserId param attaches UserData
    (Played, LastPlayedDate) when available.

    ``mit_streams`` holt zusaetzlich ``MediaStreams`` — noetig, um die
    Tonsprachen einer Serie aus ihren Folgen zusammenzutragen. Standardmaessig
    aus, weil dieselbe Funktion im naechtlichen Fortschritts-Sync je Nutzer
    einmal pro Serie laeuft; dort waere die groessere Antwort reiner Ballast
    (gemessen: Black Mirror 90 kB -> 237 kB, Bonanza 1,16 MB -> 1,23 MB).
    """
    user = jf_user_id or _bib_nutzer()
    url = f"{_url()}/Shows/{_id_pfad(series_id)}/Episodes"
    felder = "Overview,RunTimeTicks,UserData"
    if mit_streams:
        felder += ",MediaStreams"
    params = {
        "UserId": user,
        "Fields": felder,
        "Limit": 5000,
    }
    with httpx.Client(timeout=30.0, headers=_headers()) as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        return r.json().get("Items", [])


def set_favorite(jf_user_id: str, item_id: str, favorite: bool) -> None:
    """Mark/unmark a movie as favorite in Jellyfin (mirrors the heart icon
    in Jellyfin Web). Uses the shared admin API key.

    Endpoint: POST/DELETE /UserFavoriteItems/{itemId}?userId=...
    (bis 10.11 /Users/{userId}/FavoriteItems/{itemId}, in 12.0 entfernt)
    """
    url = f"{_url()}/UserFavoriteItems/{_id_pfad(item_id)}"
    params = {"userId": jf_user_id}
    with httpx.Client(timeout=15.0, headers=_headers()) as c:
        if favorite:
            r = c.post(url, params=params)
        else:
            r = c.delete(url, params=params)
        r.raise_for_status()


def set_rating(jf_user_id: str, item_id: str, rating: Optional[float]) -> None:
    """Setzt die Nutzerbewertung in Jellyfin — oder loescht sie bei ``None``.

    Endpunkt: ``POST /UserItems/{itemId}/UserData?userId=...`` mit
    ``{"Rating": <0-10 oder null>}``. An der laufenden Instanz (Jellyfin
    10.11.11, in 12.0 unveraendert vorhanden) gegen zwei Alternativen
    geprueft; dieser ist der einzige, mit dem
    sich ein *exakter* Wert setzen laesst — ``/Items/{id}/Rating?likes=true``
    schreibt stur eine 10.

    Nebenwirkung von Jellyfin: eine gesetzte Bewertung setzt zugleich
    ``Likes=true``. Das laesst sich nicht abschalten und ist hinnehmbar.
    """
    with httpx.Client(timeout=15.0, headers=_headers()) as c:
        if rating is None:
            # Wichtig: ein POST mit "Rating": null loescht NICHTS — Jellyfin
            # wertet null als "unveraendert". Nur der DELETE-Endpunkt raeumt
            # Rating und Likes wirklich weg. Im Test faellt das sonst nicht
            # auf, weil lokal korrekt geloescht wird und nur Jellyfin zurueckbleibt.
            # 12.0: /Users/{userId}/Items/{itemId}/Rating gibt es nicht mehr.
            r = c.delete(f"{_url()}/UserItems/{_id_pfad(item_id)}/Rating",
                         params={"userId": jf_user_id})
        else:
            r = c.post(f"{_url()}/UserItems/{_id_pfad(item_id)}/UserData",
                       params={"userId": jf_user_id},
                       json={"Rating": rating, "Likes": rating >= 5})
        r.raise_for_status()


def fetch_user_items(jf_user_id: str, _jf_access_token: str = "") -> list[dict]:
    """Fetch all movies with UserData for a specific user.

    Uses shared admin API key (can read any user's data in same library).
    """
    url = _items_url()
    params = {
        "userId": jf_user_id,
        "IncludeItemTypes": "Movie",
        "Recursive": "true",
        "Fields": "UserData,RunTimeTicks",
        "EnableImages": "false",
        "Limit": 10000,
    }
    with httpx.Client(timeout=60.0, headers=_headers()) as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        return r.json().get("Items", [])


def fetch_sichtbare_ids(jf_user_id: str, typ: str) -> list[str]:
    """IDs aller Filme (``typ="Movie"``) bzw. Serien (``"Series"``), die das
    Konto ``jf_user_id`` sehen darf.

    Mit ?userId= wendet Jellyfin die Rechte dieses Kontos selbst an:
    freigegebene Bibliotheken, Altersgrenze und gesperrte Tags. Dieselbe Form
    wie fetch_user_items, aber ohne Nutzerdaten und Felder - gebraucht nach
    dem Bibliotheks-Sync, wenn nur die Menge aufgefrischt werden soll (siehe
    sichtbarkeit.auffrischen). Die Obergrenzen entsprechen denen des
    Bibliotheks-Syncs, damit beide Seiten gleich weit reichen.
    """
    params = {
        "userId": jf_user_id,
        "IncludeItemTypes": typ,
        "Recursive": "true",
        "EnableImages": "false",
        "EnableUserData": "false",
        "Limit": 10000 if typ == "Movie" else 5000,
    }
    with httpx.Client(timeout=60.0, headers=_headers()) as c:
        r = c.get(_items_url(), params=params)
        r.raise_for_status()
        return [it["Id"] for it in r.json().get("Items", []) if it.get("Id")]


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

_DEVICE_ID = str(uuid.uuid4())

def authenticate(username: str, password: str, url: Optional[str] = None) -> dict:
    """Authenticate against Jellyfin. Returns dict with user info + token.

    ``url`` nur fuer die Einrichtung: dort ist noch keine Adresse gespeichert,
    und gespeichert wird sie erst, wenn die Anmeldung geklappt hat. Sonst
    stuende nach einem Tippfehler im Passwort eine halbe Einrichtung in der
    Datenbank.

    Raises httpx.HTTPStatusError on auth failure (401).
    """
    basis = _basis(url) if url else _url()
    ziel = f"{basis}/Users/AuthenticateByName"
    headers = {
        "Authorization": (
            f'MediaBrowser Client="CinematekAI", Device="Web", '
            f'DeviceId="{_DEVICE_ID}", Version="{config.VERSION}"'
        ),
        "Content-Type": "application/json",
    }
    body = {"Username": username, "Pw": password}
    with httpx.Client(timeout=15.0) as c:
        r = c.post(ziel, headers=headers, json=body)
        r.raise_for_status()
        data = r.json()
    user = data.get("User") or {}
    policy = user.get("Policy") or {}
    return {
        "jellyfin_user_id": user.get("Id", ""),
        "access_token": data.get("AccessToken", ""),
        "is_jf_admin": bool(policy.get("IsAdministrator")),
        "display_name": user.get("Name") or username,
    }


def lookup_jellyfin_user(username: str) -> Optional[dict]:
    """Look up a Jellyfin user by name using the shared API key.

    Returns {"id": ..., "name": ...} or None.
    """
    url = f"{_url()}/Users"
    with httpx.Client(timeout=15.0, headers=_headers()) as c:
        r = c.get(url)
        r.raise_for_status()
        users = r.json()
    for u in users:
        if (u.get("Name") or "").lower() == username.lower():
            return {"id": u["Id"], "name": u["Name"]}
    return None


# ---------------------------------------------------------------------------
# Einrichtung und Verbindungstest
# ---------------------------------------------------------------------------
# Alles hier wird von der Oberflaeche aus aufgerufen, oft mit einer Adresse,
# die noch nirgends gespeichert ist. Fehler kommen deshalb als ValueError mit
# einer deutschen Meldung, die der Admin direkt lesen kann — nicht als
# httpx-Fehler mit Stacktrace-Englisch.

def _netzfehler(basis: str, e: Exception) -> ValueError:
    """Transportfehler in eine lesbare Meldung uebersetzen."""
    roh = str(e) or type(e).__name__
    if "CERTIFICATE_VERIFY_FAILED" in roh:
        # Typisch im Heimnetz: ein Zertifikat aus einer eigenen Zertifizierungs-
        # stelle (mkcert, Router), der der Container nicht vertraut. Die
        # interne http-Adresse umgeht das, ohne die Pruefung abzuschalten.
        return ValueError(_text("zertifikat", basis=basis))
    if isinstance(e, httpx.TimeoutException):
        return ValueError(_text("zeitueberschreitung", basis=basis))
    return ValueError(_text("nicht_erreichbar", basis=basis, roh=roh[:200]))


_INFO_PFAD = "/System/Info/Public"


def oeffentliche_info(url: str) -> dict:
    """``{"server_name", "version", "id"}`` eines Jellyfin-Servers, ohne Anmeldung.

    Fuer den ersten Schritt der Einrichtung: bevor jemand sein Passwort
    eintippt, soll feststehen, dass unter der Adresse wirklich ein Jellyfin
    antwortet. Wirft ``ValueError`` mit deutscher Meldung.
    """
    basis = _basis(url)
    try:
        with httpx.Client(timeout=10.0) as c:
            r = c.get(f"{basis}{_INFO_PFAD}", headers={"Accept": "application/json"})
    except httpx.HTTPError as e:
        raise _netzfehler(basis, e) from e

    # Weiterleitungen bewusst nicht folgen: alle anderen Aufrufe folgen ihnen
    # auch nicht. Die Pruefung bestuende sonst, und danach scheiterte jede
    # echte Anfrage an derselben Adresse. Besser gleich die Zieladresse nennen.
    if r.is_redirect:
        ziel = r.headers.get("location", "")
        if ziel.endswith(_INFO_PFAD):
            ziel = ziel[: -len(_INFO_PFAD)]
        if ziel:
            raise ValueError(_text("weiterleitung_ziel", basis=basis, ziel=ziel))
        raise ValueError(_text("weiterleitung", basis=basis))
    if r.status_code != 200:
        raise ValueError(_text("kein_jellyfin_http", basis=basis, status=r.status_code))
    try:
        daten = r.json()
    except ValueError:
        daten = None
    if not isinstance(daten, dict) or not daten.get("Id") or not daten.get("Version"):
        raise ValueError(_text("kein_jellyfin", basis=basis))
    # Emby antwortet auf dieselbe Route (Jellyfin ist daraus hervorgegangen).
    # CinematekAI ist aber nur gegen Jellyfin gebaut und geprueft — besser
    # hier abweisen als spaeter an einer Route scheitern, die es dort nicht gibt.
    produkt = str(daten.get("ProductName") or "")
    if produkt and "jellyfin" not in produkt.lower():
        raise ValueError(_text("anderes_produkt", basis=basis, produkt=produkt))
    if daten.get("StartupWizardCompleted") is False:
        raise ValueError(_text("assistent_offen"))
    return {
        "server_name": str(daten.get("ServerName") or ""),
        "version": str(daten["Version"]),
        "id": str(daten["Id"]),
    }


def _erstellt_am(eintrag: dict) -> tuple[float, str]:
    """Sortierschluessel fuer ``DateCreated``. Laesst sich der Wert nicht
    lesen, entscheidet der Text — Jellyfin schreibt alle Eintraege im selben
    ISO-Format, dann sortiert auch der Text richtig."""
    roh = str(eintrag.get("DateCreated") or "")
    dt = _parse_dt(roh)
    try:
        return (dt.timestamp() if dt else 0.0, roh)
    except (OverflowError, OSError, ValueError):
        return (0.0, roh)


def api_schluessel_anlegen(url: str, access_token: str, app: str = "CinematekAI") -> str:
    """In Jellyfin einen API-Schluessel fuer ``app`` anlegen und zurueckgeben.

    Damit muss bei der Einrichtung niemand im Jellyfin-Dashboard einen
    Schluessel erzeugen und abtippen — das Access-Token aus der Anmeldung des
    Admins reicht. Die Antwort auf das Anlegen enthaelt den Schluessel nicht;
    er steht erst in der Liste. Genommen wird der juengste Eintrag mit diesem
    App-Namen. Aeltere aus frueheren Einrichtungen bleiben stehen: sie zu
    loeschen koennte einer zweiten Installation den Zugang nehmen.

    Beide Routen verlangen Administratorrechte. Wirft ``ValueError`` mit
    deutscher Meldung; Token und Schluessel stehen nie darin.
    """
    basis = _basis(url)
    kopf = {"Authorization": (
        f'MediaBrowser Client="CinematekAI", Device="Server", '
        f'DeviceId="{_DEVICE_ID}", Version="{config.VERSION}", Token="{access_token}"'
    )}
    try:
        with httpx.Client(timeout=15.0, headers=kopf) as c:
            r = c.post(f"{basis}/Auth/Keys", params={"app": app})
            if r.status_code in (401, 403):
                raise ValueError(_text("nur_admins"))
            if r.status_code >= 300:
                raise ValueError(_text("anlegen_abgelehnt", status=r.status_code))
            r = c.get(f"{basis}/Auth/Keys")
            if r.status_code != 200:
                raise ValueError(_text("schluesselliste_fehlt", status=r.status_code))
            try:
                daten = r.json()
            except ValueError:
                daten = None
    except httpx.HTTPError as e:
        raise _netzfehler(basis, e) from e

    eintraege = (daten or {}).get("Items") if isinstance(daten, dict) else None
    passend = [e for e in eintraege or []
               if isinstance(e, dict) and e.get("AppName") == app and e.get("AccessToken")]
    if not passend:
        raise ValueError(_text("nicht_in_liste"))
    neuester = max(passend, key=_erstellt_am)
    log.info("Jellyfin: API-Schluessel fuer %r angelegt", app)
    return str(neuester["AccessToken"])


def nutzer_liste(url: Optional[str] = None, api_key: Optional[str] = None) -> list[dict]:
    """Alle Jellyfin-Konten als ``[{"id", "name", "is_admin"}]``, nach Namen.

    Ohne Argumente mit Adresse und Schluessel aus den Einstellungen (wirft
    dann ``NichtEingerichtet``, wenn sie fehlen). Mit Argumenten fuer
    Einrichtung und Verbindungstest. Der gespeicherte Schluessel geht dabei
    nur an die gespeicherte Adresse, nie an eine andere — sonst landete er
    nach einem Tippfehler in der Adresse bei einem fremden Server.

    Fehler der Abfrage als ``ValueError`` mit deutscher Meldung.
    """
    if url is None and api_key is None:
        basis, kopf = _url(), _headers()
    else:
        basis = _basis(url) if url else _url()
        schluessel = (api_key or "").strip()
        if not schluessel:
            jf = settings.get("jellyfin")
            gespeichert = (jf["url"] or "").strip().rstrip("/")
            if not jf["api_key"] or basis != gespeichert:
                raise ValueError(_text("kein_schluessel"))
            schluessel = jf["api_key"]
        kopf = _token_kopf(schluessel)

    try:
        with httpx.Client(timeout=15.0, headers=kopf) as c:
            r = c.get(f"{basis}/Users")
    except httpx.HTTPError as e:
        raise _netzfehler(basis, e) from e
    if r.status_code in (401, 403):
        raise ValueError(_text("schluessel_abgelehnt"))
    if r.status_code != 200:
        raise ValueError(_text("benutzerliste_http", status=r.status_code))
    try:
        konten = r.json()
    except ValueError:
        konten = None
    if not isinstance(konten, list):
        raise ValueError(_text("keine_benutzerliste", basis=basis))

    aus = [{
        "id": str(u["Id"]),
        "name": str(u.get("Name") or ""),
        "is_admin": bool((u.get("Policy") or {}).get("IsAdministrator")),
    } for u in konten if isinstance(u, dict) and u.get("Id")]
    aus.sort(key=lambda u: u["name"].lower())
    return aus


# ---------------------------------------------------------------------------
# Image proxying + deep links
# ---------------------------------------------------------------------------

def image_url(item_id: str, image_type: str = "Primary") -> str:
    return f"{_url()}/Items/{_id_pfad(item_id)}/Images/{_id_pfad(image_type)}"


def stream_image(item_id: str, image_type: str = "Primary",
                 max_width: Optional[int] = None) -> Iterable[bytes]:
    """Bild aus Jellyfin durchreichen, optional auf ``max_width`` begrenzt.

    Jellyfin skaliert selbst, wenn ``maxWidth`` mitgeschickt wird — es braucht
    also keine eigene Skalierung im Backend. Ohne Grenze kamen Poster in
    Originalgroesse (bis 3840 px breit, 1,9 MB) fuer 168 px breite Kacheln.
    """
    url = image_url(item_id, image_type)
    params = {"maxWidth": max_width, "quality": 90} if max_width else None
    with httpx.stream("GET", url, headers=_headers(), params=params, timeout=30.0) as r:
        r.raise_for_status()
        yield from r.iter_bytes()


class ImageNotFound(Exception):
    """Jellyfin kennt das Bild nicht. Vom Transportfehler zu trennen, damit
    der Proxy 404 statt 502 melden kann — sonst sieht ein fehlendes Poster
    wie ein Serverausfall aus und das Frontend kann nicht auf seinen
    eigenen Platzhalter umschalten."""


class KeinBild(Exception):
    """Jellyfin hat geantwortet, aber nicht mit einem Rasterbild.

    Eigene Klasse, damit der Proxy das als 502 meldet und im Protokoll
    erkennbar bleibt, warum. Durchgereicht wuerde sonst eine Antwort mit
    fremdem Inhaltstyp (HTML-Fehlerseite eines Proxys, JSON) unter der
    eigenen Adresse der App ausgeliefert.
    """


# Nur diese Typen reicht der Proxy durch. Bewusst eine Liste statt "image/*":
# image/svg+xml kann Skript enthalten und liefe dann unter dem Ursprung der
# App - mit Zugriff auf deren Sitzung. Jellyfin liefert Poster, Hintergruende
# und Logos als JPEG, PNG oder WebP; "image/jpg" ist die verbreitete
# Falschschreibung, die manche Server senden.
_BILDTYPEN = ("image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif",
              "image/avif", "image/bmp")


def _ist_rasterbild(content_type: str) -> bool:
    return content_type.split(";", 1)[0].strip().lower() in _BILDTYPEN


def open_image(item_id: str, image_type: str = "Primary",
               max_width: Optional[int] = None) -> tuple[str, Iterable[bytes]]:
    """Wie ``stream_image``, gibt aber den Inhaltstyp von Jellyfin mit zurueck.

    Der Proxy hat bisher jede Antwort als ``image/jpeg`` deklariert. Fuer
    Poster stimmt das, fuer Logos nicht: die liefert Jellyfin als PNG mit
    Transparenz, und als JPEG ausgezeichnet verliert der Hero die
    Freistellung. Rueckgabe ist ``(content_type, iterator)``; der Aufrufer
    muss den Iterator auslaufen lassen, damit die Verbindung schliesst.
    """
    url = image_url(item_id, image_type)
    params = {"maxWidth": max_width, "quality": 90} if max_width else None
    client = httpx.Client(timeout=30.0)
    try:
        req = client.build_request("GET", url, headers=_headers(), params=params)
        r = client.send(req, stream=True)
    except Exception:
        client.close()
        raise
    if r.status_code == 404:
        r.close(); client.close()
        raise ImageNotFound(f"{image_type} for {item_id}")
    try:
        r.raise_for_status()
    except Exception:
        r.close(); client.close()
        raise

    # Ohne Inhaltstyp wurde bisher JPEG angenommen. Jellyfin schickt bei
    # Bildern immer einen mit; fehlt er, ist die Antwort nicht von dort
    # oder nicht in Ordnung - also wie jeder andere fremde Typ abweisen.
    # Geprueft wird hier und nicht erst im Aufrufer: der Generator unten
    # schliesst die Verbindung nur, wenn er auch gestartet wird.
    ctype = r.headers.get("content-type", "")
    if not _ist_rasterbild(ctype):
        r.close(); client.close()
        raise KeinBild(f"{image_type} for {item_id}: content-type {ctype!r}")

    def body():
        try:
            yield from r.iter_bytes()
        finally:
            r.close()
            client.close()

    return ctype, body()


# Die Bibliothek legt Trailer als "<Filmordner>/backdrops/trailer.mp4" ab.
# Jellyfin indexiert sie nicht — "backdrops" ist dort fuer Bilder reserviert,
# deshalb steht LocalTrailerCount ueberall auf 0 und die Dateien tauchen in
# keiner Item-Abfrage auf. Gefunden werden sie darum ueber den Dateipfad des
# Films, den Jellyfin mitliefert.
TRAILER_UNTERORDNER = "backdrops"
TRAILER_DATEI = "trailer.mp4"


def fetch_item_path(item_id: str) -> Optional[str]:
    """Dateipfad des Films, so wie Jellyfin ihn sieht (z. B. /media/filme/...).

    ``GET /Items/{itemId}?userId=`` statt des veralteten
    ``/Users/{userId}/Items/{itemId}`` (siehe Kommentar ueber _items_url).
    Die alte Route ist in Jellyfin nur noch ein Verweis auf diese; die
    Antwort ist dieselbe, ``Path`` steht also weiter darin.
    """
    url = f"{_url()}/Items/{_id_pfad(item_id)}"
    with httpx.Client(timeout=20.0, headers=_headers()) as c:
        r = c.get(url, params={"userId": _bib_nutzer()})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json().get("Path")


def trailer_pfad(item_id: str, medien_wurzel: str = "/media") -> Optional[str]:
    """Pfad zur lokalen Trailer-Datei, oder ``None``.

    ``medien_wurzel`` begrenzt, was ausgeliefert werden darf: Jellyfin liefert
    den Pfad, und ohne diese Schranke wuerde ein manipulierter Eintrag das
    Backend dazu bringen, beliebige Dateien zu streamen.
    """
    pfad = fetch_item_path(item_id)
    if not pfad:
        return None

    wurzel = os.path.realpath(medien_wurzel)
    kandidat = os.path.realpath(
        os.path.join(os.path.dirname(pfad), TRAILER_UNTERORDNER, TRAILER_DATEI)
    )
    if os.path.commonpath([wurzel, kandidat]) != wurzel:
        log.warning("Trailer-Pfad ausserhalb der Medienwurzel: %s", kandidat)
        return None
    return kandidat if os.path.isfile(kandidat) else None


def web_deep_link(item_id: str) -> str:
    return f"{_url()}/web/#/details?id={item_id}"
