"""CinematekAI embeddings — eigener Vektorspeicher, Einbettungsmodell aus den Einstellungen.

Die Vektoren liegen seit 1.1.0 im eigenen Vektorspeicher (vektorspeicher.py,
Tabelle ``vektor`` in der Datenbank) statt in ChromaDB; Begruendung dort.

Welches Modell bei welchem Anbieter einbettet, steht seit Oktober 2026 im
Admin-Bereich (Abschnitt ``embedding``, siehe settings.py und anbieter.py) und
kann sich zur Laufzeit aendern. Deshalb haelt dieses Modul das Modell nicht
mehr ab dem Import fest:

  * Je Modell gibt es eine eigene Sammlung (siehe _sammlungsname).
    Aendert sich das Modell, stellt sammlungen_wechseln() auf dessen
    Sammlungen um. Die alten bleiben liegen — zurueckwechseln geht sofort.
  * Es laeuft hoechstens EIN Einbett-Lauf zur Zeit (Cron, Admin-Knopf,
    Modellwechsel). Ein zweiter bekommt ``{"status": "laeuft_bereits"}``.
  * Wie eine Aehnlichkeit in Prozent uebersetzt wird, haengt am Modell. Die
    Skala wird je Modell gemessen (kalibrieren) und in den Einstellungen
    abgelegt; fuer bge-m3 gelten die von Hand gesetzten Werte.

Alle Schreibzugriffe auf die Vektoren passieren in diesem Prozess. Der
Speicher haelt sie im Arbeitsspeicher; was ein zweiter Prozess in die Tabelle
schriebe, saehe das laufende Backend erst nach einem Neustart (bei Chroma war
dasselbe am 29.09. belegt).
"""
from __future__ import annotations

import logging
import math
import re
import threading
import time
from datetime import datetime, timezone
from typing import Iterable

from sqlmodel import func, select

import anbieter
import settings
import texte
import vektorspeicher
from config import CHROMA_DIR, DB_PATH, VEKTOR_EXPORT_DIR
from database import Movie, Series, Episode, User, get_session

log = logging.getLogger("cinematekai.embeddings")


# ── Meldungen fuer die Oberflaeche ─────────────────────────────────────────
# Der Admin-Bereich zeigt den Stand der Einbettung (einbettung_status) und die
# Fehler der Skalen-Messung (kalibrieren). Beides entsteht zum Teil im
# Hintergrund-Faden, der die Sprache der Anfrage nicht kennt — und der Stand
# wird gespeichert, bis jemand ihn abruft, vielleicht in einer anderen Sprache
# als zur Entstehung. Deshalb: _Meldung ist ein Text MIT Schluessel und Werten.
# Als str laesst er sich verketten, ins Log schreiben und als JSON ausgeben;
# _text() baut ihn beim Ausliefern in der gewuenschten Sprache neu.
_T = {
    "de": {
        "modell_gewechselt": "Das Einbettungsmodell wurde gerade gewechselt.",
        "kein_modell": "Kein Einbettungsmodell eingerichtet.",
        "kein_modell_kurz": "Kein Einbettungsmodell eingerichtet",
        "abbruch_modell": "Das Einbettungsmodell wurde während des Laufs gewechselt. "
                          "Dieser Lauf wurde abgebrochen; was dem neuen Modell fehlt, "
                          "bettet ein neuer Lauf ein.",
        "abbruch_serie": "Abgebrochen nach {n} Fehlschlägen in Folge. "
                         "Letzter Fehler: {fehler}",
        "teilweise": "{n} Einträge ließen sich nicht einbetten. Letzter Fehler: {fehler}",
        "lauf_abgebrochen": "Lauf abgebrochen: {fehler}",
        "profile_uebersprungen": "Nutzerprofile übersprungen: {grund}",
        "profile_fehler": "Nutzerprofile: {liste}",
        "skala_nicht_gemessen": "Skala nicht gemessen: {fehler}",
        "skala_fehlgeschlagen": "Skala messen fehlgeschlagen: {fehler}",
        "kein_profil": "Noch kein Nutzerprofil eingebettet. Die Skala lässt sich erst "
                       "messen, wenn für mindestens einen Nutzer ein Profilvektor "
                       "berechnet ist.",
        "keine_probe": "Keine Probe-Anfrage ließ sich einbetten – die Skala wurde "
                       "nicht gemessen.",
        "unplausibel_passung": "Die gemessene Skala für die Passung ist unplausibel "
                               "(0 % bei {unten:.3f}, 100 % bei {oben:.3f}) "
                               "und wird nicht gespeichert.",
        "unplausibel_stimmung": "Die gemessene Skala für die Stimmungssuche ist unplausibel "
                                "(0 % bei {unten:.3f}, 100 % bei {oben:.3f}) "
                                "und wird nicht gespeichert.",
        "sammlung_leer": "Die Sammlung ist noch leer. Erst einbetten, dann messen.",
        "keine_filme": "Es sind noch keine Filme oder Serien eingebettet. Erst "
                       "einbetten, dann messen.",
        "laeuft_gerade": "Gerade läuft eine Einbettung. Bitte warten, bis sie fertig ist.",
        "phase_vorbereitung": "Vorbereitung",
        "phase_filme": "Filme",
        "phase_serien": "Serien",
        "phase_episoden": "Episoden",
        "phase_nutzerprofile": "Nutzerprofile",
        "phase_passung": "Passung vorberechnen",
        "phase_skala": "Skala messen",
    },
    "en": {
        "modell_gewechselt": "The embedding model was just changed.",
        "kein_modell": "No embedding model set up.",
        "kein_modell_kurz": "No embedding model set up",
        "abbruch_modell": "The embedding model was changed during the run. "
                          "This run was aborted; a new run will embed whatever "
                          "the new model is missing.",
        "abbruch_serie": "Aborted after {n} failures in a row. "
                         "Last error: {fehler}",
        "teilweise": "{n} entries could not be embedded. Last error: {fehler}",
        "lauf_abgebrochen": "Run aborted: {fehler}",
        "profile_uebersprungen": "User profiles skipped: {grund}",
        "profile_fehler": "User profiles: {liste}",
        "skala_nicht_gemessen": "Scale not measured: {fehler}",
        "skala_fehlgeschlagen": "Measuring the scale failed: {fehler}",
        "kein_profil": "No user profile embedded yet. The scale can only be measured "
                       "once a profile vector has been computed for at least one user.",
        "keine_probe": "No sample query could be embedded – the scale was not measured.",
        "unplausibel_passung": "The measured scale for the match percentage is implausible "
                               "(0 % at {unten:.3f}, 100 % at {oben:.3f}) "
                               "and will not be saved.",
        "unplausibel_stimmung": "The measured scale for mood search is implausible "
                                "(0 % at {unten:.3f}, 100 % at {oben:.3f}) "
                                "and will not be saved.",
        "sammlung_leer": "The collection is still empty. Embed first, then measure.",
        "keine_filme": "No movies or series are embedded yet. Embed first, "
                       "then measure.",
        "laeuft_gerade": "An embedding run is in progress. Please wait until it is done.",
        "phase_vorbereitung": "Preparing",
        "phase_filme": "Movies",
        "phase_serien": "Series",
        "phase_episoden": "Episodes",
        "phase_nutzerprofile": "User profiles",
        "phase_passung": "Precomputing match values",
        "phase_skala": "Measuring scale",
    },
}


def _wert(wert):
    """Wert einer Meldung festhalten. Eine Ausnahme wird zu ihrer Meldung bzw.
    ihrem Text: so haelt die Meldung keine Rueckverfolgung (und mit ihr halbe
    Programmstaende) am Leben."""
    if isinstance(wert, BaseException):
        erstes = wert.args[0] if wert.args else None
        return erstes if isinstance(erstes, _Meldung) else str(wert)
    return wert


class _Meldung(str):
    """Text einer Meldung, der sich in jeder Sprache neu bauen laesst."""

    def __new__(cls, schluessel: str, **werte):
        werte = {k: _wert(v) for k, v in werte.items()}
        obj = super().__new__(cls, texte.waehle(
            _T, schluessel, None,
            **{k: _text(v) if isinstance(v, _Meldung) else v for k, v in werte.items()}))
        obj.schluessel = schluessel
        obj.werte = werte
        return obj


def _text(meldung, sprache: str | None = None) -> str:
    """Meldung in der Sprache ``sprache`` (ohne Angabe die der Anfrage). Eine
    _Meldung wird neu gebaut, jeder andere Text bleibt, wie er ist."""
    if isinstance(meldung, _Meldung):
        return texte.waehle(
            _T, meldung.schluessel, sprache,
            **{k: _text(v, sprache) if isinstance(v, _Meldung) else v
               for k, v in meldung.werte.items()})
    return str(meldung)


def _t(schluessel: str, sprache: str | None = None, **werte) -> str:
    return texte.waehle(_T, schluessel, sprache, **werte)


# Liest erst beim ersten Zugriff aus der Datenbank (vektorspeicher.py); der
# Import dieses Moduls beruehrt die Datei also nicht. Den Ordner data/chroma
# legt diese Version nicht mehr an: liegt er vor, stammt er von 1.0.x (siehe
# beim_start).
_speicher = vektorspeicher.Vektorspeicher(DB_PATH)


# ── Je Modell eine eigene Sammlung ─────────────────────────────────────────
# bge-m3 liefert 1024 Dimensionen, nomic-embed-text 768. Landen beide in
# derselben Sammlung, weist der Speicher den Vektor mit einer Meldung zur
# Dimension ab — und selbst wenn nicht, waeren Abstaende zwischen Vektoren
# zweier Modelle bedeutungslos. Der Sammlungsname haengt deshalb am Modell.
#
# nomic-embed-text behaelt bewusst die alten Namen ("movies", "user_profiles").
# Dadurch bleiben die bisherigen Vektoren unangetastet liegen: stellt man im
# Admin-Bereich das Modell zurueck, ist die Suche sofort wieder da, ohne
# einen Neu-Einbett-Lauf von ueber einer Stunde.
#
# Die Namen sind dieselben wie zu Chroma-Zeiten (bis 1.0.x): ein Export der
# alten Sammlungen (tools/chroma_export.py) landet so 1:1 dort, wo diese
# Version sucht.
_NAME_UNERLAUBT = re.compile(r"[^a-z0-9]+")


def _sammlungsname(basis: str, modell: str) -> str:
    """'movies' + 'bge-m3' ergibt 'movies__bge_m3'; nomic behaelt 'movies'."""
    kurz = (modell or "").split(":", 1)[0].strip().lower()
    if kurz.startswith("nomic-embed"):
        return basis
    endung = _NAME_UNERLAUBT.sub("_", kurz).strip("_")
    if not endung:
        return basis
    # Hoechstens 63 Zeichen und kein Trenner am Ende - die Regel von Chroma,
    # beibehalten, damit die Namen gleich bleiben.
    return f"{basis}__{endung}"[:63].rstrip("_")


def _sammlung_holen(name: str) -> vektorspeicher.Sammlung:
    # Abstand ist immer Kosinus, wie bei den Chroma-Sammlungen
    # ("hnsw:space": "cosine"), siehe vektorspeicher.py.
    return _speicher.sammlung(name)


# Schuetzt das Umstellen der Sammlungen: _modell und die vier Globalen
# darunter muessen immer zusammenpassen. Wer schreibt, prueft unter dieser
# Sperre, dass die Sammlungen noch zu SEINEM Modell gehoeren — sonst landete
# ein Vektor des alten Modells in der Sammlung des neuen. Wer liest, holt sich
# ueber _stand() alle drei Werte auf einmal, damit Anfragevektor und Sammlung
# nie von zwei verschiedenen Modellen stammen.
_sammlung_lock = threading.RLock()

# Das Modell, zu dem die Sammlungen gerade gehoeren. Weicht nur fuer den
# Augenblick zwischen dem Speichern einer neuen Einstellung und dem Umstellen
# durch den Beobachter (unten) von den Einstellungen ab.
_modell = anbieter.einbettung_modell()
MOVIE_COLLECTION = _sammlungsname("movies", _modell)
PROFILE_COLLECTION = _sammlungsname("user_profiles", _modell)
_collection = _sammlung_holen(MOVIE_COLLECTION)
# Separate collection for per-user taste profile embeddings. Used to compute
# a "match score" between a user and any movie in <1 ms via cosine similarity.
_user_profiles = _sammlung_holen(PROFILE_COLLECTION)


def _stand():
    """(Modell, Filmsammlung, Profilsammlung) — zusammenpassend."""
    with _sammlung_lock:
        return _modell, _collection, _user_profiles


# ---------------------------------------------------------------------------
# Einbetten einzelner Eintraege
# ---------------------------------------------------------------------------

class _ModellGewechselt(ValueError):
    """Das Einbettungsmodell hat sich mitten in der Arbeit geaendert.

    Ein ValueError, damit die Admin-Endpunkte es als 400 mit lesbarer
    Meldung weitergeben statt als 500."""


def _modell_von(abschnitt: dict) -> str:
    return (abschnitt.get("model") or "").strip()


def _eingerichtet(abschnitt: dict) -> bool:
    """Anbieter, Adresse, ggf. Schluessel und Modell stehen da, und der
    Anbieter bietet ueberhaupt Einbettungen an."""
    v = anbieter.verbindung(abschnitt)
    return bool(v and _modell_von(abschnitt)
                and (anbieter.preset(v.anbieter) or {}).get("einbettung"))


def _einbetten_mit(text: str, modell: str) -> list[float]:
    """Text (schon mit Praefix) einbetten — aber nur mit ``modell``.

    Der Abschnitt wird bei jedem Aufruf frisch gelesen: ein im Admin-Bereich
    korrigierter Schluessel wirkt so schon beim naechsten Eintrag. Steht dort
    inzwischen ein anderes Modell, wird abgebrochen statt eingebettet — der
    Vektor passte nicht mehr zur Sammlung, in die er gehoeren soll.
    """
    abschnitt = settings.get("embedding")
    if _modell_von(abschnitt) != modell:
        raise _ModellGewechselt(_Meldung("modell_gewechselt"))
    return anbieter.einbetten(text, abschnitt)


def _ablegen(modell: str, eintrag_id: str, text: str, vektor: list[float],
             meta: dict, tabelle=None) -> None:
    """Vektor in die Filmsammlung schreiben und, im Lauf, die Marke setzen.

    Beides unter der Sammlungssperre und nur, solange die Sammlungen noch zu
    ``modell`` gehoeren. Ein Wechsel setzt die Marken aus der Mitgliedschaft
    in der NEUEN Sammlung; eine danach vom alten Lauf gesetzte Marke waere
    falsch, und der Titel bekaeme mit dem neuen Modell nie einen Vektor.
    """
    with _sammlung_lock:
        if _modell != modell:
            raise _ModellGewechselt(_Meldung("modell_gewechselt"))
        _collection.schreiben(eintrag_id, vektor, meta, text)
        if tabelle is not None:
            with get_session() as s:
                zeile = s.get(tabelle, eintrag_id)
                if zeile:
                    zeile.embedded = True
                    s.commit()
    _aehnlichkeiten_verwerfen()


def _eintrag_einbetten(modell: str, eintrag_id: str, roh_text: str, meta: dict,
                       tabelle=None) -> None:
    """Praefix setzen, einbetten, ablegen. Wirft bei jedem Fehler."""
    text = anbieter.praefix_dokument(roh_text, modell)
    _ablegen(modell, eintrag_id, text, _einbetten_mit(text, modell), meta, tabelle)


def movie_to_text(m: Movie) -> str:
    """Build the embedding document from a Movie row.

    Die Feldbezeichner sind englisch. Frueher stand hier als Begruendung, dass
    nomic-embed-text mit Englisch besser klarkomme. Fuer das mehrsprachige
    bge-m3 wurde das nachgemessen (10 Filme, Fragen "lustiger Tierfilm fuer
    Kinder", "spannender Thriller", "Film ueber Gangster"): englische und
    deutsche Bezeichner liefern DIESELBE Reihenfolge, die Unterschiede in den
    Werten liegen bei ±0,01 und damit im Rauschen. Umgestellt wird deshalb
    nicht — ein Wechsel haette nur den Dokumenttext veraendert, ohne dass die
    Suche messbar besser wird. Der Inhalt (Titel, Genres, Handlung) ist
    ohnehin deutsch; darauf kommt es an, nicht auf die Etiketten.
    """
    parts = [f"Title: {m.name}"]
    if m.year:
        parts.append(f"Year: {m.year}")
    if m.genres:
        parts.append(f"Genre: {m.genres}")
    if m.directors:
        parts.append(f"Director: {m.directors}")
    if m.actors:
        parts.append(f"Cast: {m.actors}")
    if m.characters:
        parts.append(f"Characters: {m.characters}")
    if m.collection_names:
        parts.append(f"Collection: {m.collection_names}")
    if m.studios:
        parts.append(f"Studio: {m.studios}")
    if m.runtime_min:
        parts.append(f"Runtime: {m.runtime_min} min")
    if m.community_rating:
        parts.append(f"Rating: {m.community_rating:.1f}")
    if m.overview:
        parts.append(f"Plot: {m.overview[:500]}")
    return " | ".join(parts)


def _film_meta(m: Movie) -> dict:
    return {
        "type": "movie",
        "name": m.name,
        "year": m.year or 0,
        "genres": m.genres,
        "collection_names": m.collection_names,
    }


def embed_movie(m: Movie) -> bool:
    """Embed one movie into the vector store. Returns True on success."""
    try:
        _eintrag_einbetten(_stand()[0], m.id, movie_to_text(m), _film_meta(m))
    except Exception as e:
        log.warning("Embedding failed for %s: %s", m.id, e)
        return False
    return True


def series_to_text(s: Series) -> str:
    """Build embedding document for a Series row. Englische Bezeichner aus
    demselben Grund wie bei `movie_to_text` (dort steht die Messung)."""
    year_range = ""
    if s.year and s.end_year and s.end_year != s.year:
        year_range = f"{s.year}–{s.end_year}"
    elif s.year:
        year_range = str(s.year)
    parts = [f"TV Series: {s.name}"]
    if year_range:
        parts.append(f"Years: {year_range}")
    if s.status:
        parts.append(f"Status: {'Ended' if s.status == 'ended' else 'Ongoing'}")
    if s.genres:
        parts.append(f"Genre: {s.genres}")
    if s.creators:
        parts.append(f"Creator: {s.creators}")
    if s.actors:
        parts.append(f"Cast: {s.actors}")
    if s.characters:
        parts.append(f"Characters: {s.characters}")
    if s.studios:
        parts.append(f"Studio: {s.studios}")
    if s.season_count or s.episode_count:
        parts.append(f"Seasons: {s.season_count} ({s.episode_count} episodes)")
    if s.runtime_min_avg:
        parts.append(f"Episode runtime: {s.runtime_min_avg} min")
    if s.community_rating:
        parts.append(f"Rating: {s.community_rating:.1f}")
    if s.overview:
        parts.append(f"Plot: {s.overview[:500]}")
    return " | ".join(parts)


def _serien_meta(ser: Series) -> dict:
    return {
        "type": "series",
        "name": ser.name,
        "year": ser.year or 0,
        "genres": ser.genres,
        "collection_names": "",
    }


def episode_to_text(ep: Episode, series_name: str = "") -> str:
    """Embedding text for one episode. Includes series name + S/E label so
    semantic search like 'die Bienen-Folge von Black Mirror' works.
    """
    label = ""
    if ep.season is not None and ep.episode is not None:
        label = f"S{ep.season:02d}E{ep.episode:02d}"
    parts = [f"TV Episode: {ep.name or label}"]
    if series_name:
        parts.append(f"Series: {series_name}")
    if label:
        parts.append(f"Tag: {label}")
    if ep.runtime_min:
        parts.append(f"Runtime: {ep.runtime_min} min")
    if ep.overview:
        parts.append(f"Plot: {ep.overview[:600]}")
    return " | ".join(parts)


def _episoden_meta(ep: Episode, series_name: str = "") -> dict:
    label = ""
    if ep.season is not None and ep.episode is not None:
        label = f"S{ep.season:02d}E{ep.episode:02d}"
    return {
        "type": "episode",
        "name": ep.name or label,
        "series_id": ep.series_id,
        "series_name": series_name,
        "season": ep.season or 0,
        "episode": ep.episode or 0,
        "label": label,
    }


def embed_episode(ep: Episode, series_name: str = "") -> bool:
    try:
        _eintrag_einbetten(_stand()[0], ep.id, episode_to_text(ep, series_name=series_name),
                           _episoden_meta(ep, series_name))
    except Exception as e:
        log.warning("Episode embedding failed for %s: %s", ep.id, e)
        return False
    return True


def embed_series(ser: Series) -> bool:
    """Embed one series into the vector store. Same collection as movies — `type`
    metadata distinguishes them so cross-format ranking comes for free."""
    try:
        _eintrag_einbetten(_stand()[0], ser.id, series_to_text(ser), _serien_meta(ser))
    except Exception as e:
        log.warning("Series embedding failed for %s: %s", ser.id, e)
        return False
    return True


# ---------------------------------------------------------------------------
# Laufzustand: Sperre, Fortschritt, letzter Lauf
# ---------------------------------------------------------------------------
# EIN Einbett-Lauf zur Zeit. Zwei gleichzeitige Laeufe (Cron und Admin-Knopf,
# oder zwei Klicks) haetten dieselben Titel doppelt eingebettet — mit einem
# lokalen Modell Stunden doppelter Arbeit — und sich gegenseitig die Marken
# 'embedded' ueberschrieben. Die Sperre wird NICHT blockierend genommen: wer
# sie nicht bekommt, erfaehrt sofort "laeuft bereits", statt Stunden zu warten.
# Ein threading.Lock (kein RLock), weil einbettung_starten sie im Anfrage-
# Faden nimmt und der Hintergrund-Faden sie freigibt.
_lauf_lock = threading.Lock()

# Ein Start, der an der Sperre abprallte, weil gerade etwas anderes lief —
# typisch: das Modell wurde gewechselt, waehrend noch der Lauf des alten
# Modells, eine Messung oder das Vorwaermen die Sperre hielt. Wer die Sperre
# freigibt, holt ihn nach (_sperre_freigeben). Sonst bliebe das neue Modell
# bis zum naechsten Cron ohne Vektoren, und Suche und Passung blieben leer.
_nachholen = threading.Event()

# Nach so vielen Fehlschlaegen IN FOLGE bricht ein Lauf ab. anbieter.einbetten
# versucht es bei Netz- und Serverfehlern selbst mehrfach (2 + 5 + 15 s). Ist
# der Dienst ganz weg, braeuchte ein Lauf ueber die ganze Bibliothek sonst
# Stunden, nur um bei jedem Titel denselben Fehler zu sammeln — und hielte so
# lange die Sperre. Einzelne Ausreisser (ein Titel, den der Dienst ablehnt)
# setzen den Zaehler mit dem naechsten Erfolg zurueck.
_ABBRUCH_NACH_FEHLSCHLAEGEN = 10

_zustand_lock = threading.Lock()
_fortschritt_zustand: dict | None = None
_letzter_lauf: dict | None = None


def _jetzt_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def einbettung_laeuft() -> bool:
    """Laeuft gerade ein Einbett-Lauf (oder eine Messung der Skala)?"""
    return _lauf_lock.locked()


def _sperre_freigeben() -> None:
    _lauf_lock.release()
    if _nachholen.is_set():
        _nachholen.clear()
        log.info("Einbettungen: aufgeschobenen Lauf nachholen")
        einbettung_starten()


def _fortschritt_beginnen(phase: str) -> None:
    global _fortschritt_zustand
    with _zustand_lock:
        _fortschritt_zustand = {"phase": phase, "fertig": 0, "gesamt": 0,
                                "rest_min": None, "gestartet_am": _jetzt_iso()}


def _phase_setzen(phase: str, fertig: int = 0, gesamt: int = 0,
                  rest_min: float | None = None) -> None:
    """Fortschritt fuer einbettung_status. ``fertig``/``gesamt`` zaehlen
    innerhalb der Phase — eine Restzeit ueber alle Phasen liesse sich nicht
    ehrlich schaetzen (siehe _fortschritt)."""
    global _fortschritt_zustand
    with _zustand_lock:
        gestartet = (_fortschritt_zustand or {}).get("gestartet_am") or _jetzt_iso()
        _fortschritt_zustand = {"phase": phase, "fertig": fertig, "gesamt": gesamt,
                                "rest_min": rest_min, "gestartet_am": gestartet}


def _fortschritt_beenden() -> None:
    global _fortschritt_zustand
    with _zustand_lock:
        _fortschritt_zustand = None


def _letzter_lauf_setzen(ok: int, fehlgeschlagen: int, fehler) -> None:
    """``fehler``: eine Meldung, eine Liste von Meldungen oder None. Sie werden
    als Meldungen gespeichert und erst von einbettung_status() zu Text gebaut,
    in der Sprache dessen, der gerade fragt."""
    global _letzter_lauf
    if not fehler:
        teile = []
    elif isinstance(fehler, (list, tuple)):
        teile = list(fehler)
    else:
        teile = [fehler]
    with _zustand_lock:
        _letzter_lauf = {"beendet_am": _jetzt_iso(), "ok": ok,
                         "fehlgeschlagen": fehlgeschlagen, "fehler_teile": teile}


# Alle wie viele Eintraege eine Fortschrittszeile faellig ist.
_FORTSCHRITT_ALLE = 50


def _fortschritt(phase: str, fertig: int, in_phase: int,
                 gesamt_fertig: int, gesamt: int, t_phase: float) -> None:
    """Regelmaessige Fortschrittsmeldung waehrend eines Neu-Einbett-Laufs.

    Ein vollstaendiger Lauf ueber die ganze Bibliothek dauert auf dieser CPU
    rund zweieinhalb Stunden (bge-m3, gemessen: 6-8 s je Filmdokument, 1,6 s
    je Episode). Ohne regelmaessige Zeile sieht man im Container-Log nicht, ob
    er noch arbeitet oder haengt. Die Restzeit gilt bewusst nur fuer die
    laufende Phase — ein Film kostet das Fuenffache einer Episode, eine
    gemeinsame Schaetzung waere schlicht falsch.

    Der Stand geht zusaetzlich bei JEDEM Eintrag in den Modul-Zustand, den
    einbettung_status() der Oberflaeche zeigt; ins Log nur alle 50.
    """
    dauer = time.time() - t_phase
    je = dauer / fertig if fertig else 0.0
    rest_min = (in_phase - fertig) * je / 60.0
    _phase_setzen(phase, fertig, in_phase, round(rest_min, 1) if fertig else None)
    if fertig % _FORTSCHRITT_ALLE != 0 and fertig != in_phase:
        return
    log.info("  %s %d/%d (gesamt %d/%d) — %.1f s/Eintrag, noch ca. %.0f min",
             _t("phase_" + phase, "de"), fertig, in_phase, gesamt_fertig, gesamt, je, rest_min)


# ---------------------------------------------------------------------------
# Einbett-Lauf
# ---------------------------------------------------------------------------

def _einbett_lauf(force: bool) -> dict:
    """Der eigentliche Lauf. Der Aufrufer haelt ``_lauf_lock``."""
    abschnitt = settings.get("embedding")
    modell = _modell_von(abschnitt)
    if not _eingerichtet(abschnitt):
        log.info("Einbetten uebersprungen: kein Einbettungsmodell eingerichtet")
        return {"total": 0, "ok": 0, "failed": 0, "elapsed_s": 0.0, "model": modell,
                "collection": MOVIE_COLLECTION, "nicht_eingerichtet": True,
                "fehler": _Meldung("kein_modell")}
    if modell != _stand()[0]:
        # Erledigt normalerweise der Beobachter beim Speichern. Hier nur das
        # Sicherheitsnetz, damit ein Lauf nie in die Sammlung eines anderen
        # Modells schreibt.
        sammlungen_wechseln()

    if force:
        # Eigene Sitzung, die VOR dem Laden der Zeilen abgeschlossen wird.
        # Wuerde man im selben Block laden und committen, verwirft SQLAlchemy
        # beim Commit alle Feldwerte der geladenen Objekte; der spaetere
        # Zugriff auf m.name faende dann eine geschlossene Sitzung vor.
        with get_session() as s:
            for tabelle in (Movie, Series, Episode):
                for zeile in s.exec(select(tabelle)).all():
                    zeile.embedded = False
            s.commit()
        log.info("force=True: Marke 'embedded' fuer alle Eintraege zurueckgesetzt")

    with get_session() as s:
        if force:
            movies = s.exec(select(Movie)).all()
            series_rows = s.exec(select(Series)).all()
            episode_rows = s.exec(select(Episode)).all()
        else:
            movies = s.exec(select(Movie).where(Movie.embedded == False)).all()
            series_rows = s.exec(select(Series).where(Series.embedded == False)).all()
            episode_rows = s.exec(select(Episode).where(Episode.embedded == False)).all()
        # Build series-name lookup for episode embeddings
        series_name_by_id = {ser.id: ser.name for ser in s.exec(select(Series)).all()}

    def _episode(ep: Episode) -> tuple[str, str, dict]:
        sname = series_name_by_id.get(ep.series_id, "")
        return ep.id, episode_to_text(ep, series_name=sname), _episoden_meta(ep, sname)

    phasen = (
        ("filme", movies, Movie, lambda m: (m.id, movie_to_text(m), _film_meta(m))),
        ("serien", series_rows, Series, lambda x: (x.id, series_to_text(x), _serien_meta(x))),
        ("episoden", episode_rows, Episode, _episode),
    )

    total = len(movies) + len(series_rows) + len(episode_rows)
    ok = failed = erledigt = in_folge = 0
    letzter_fehler = ""
    abbruch: str | None = None      # eine _Meldung
    gewechselt = False
    t0 = time.time()
    log.info("Einbetten mit %s in Sammlung %r: %d Filme + %d Serien + "
             "%d Episoden (force=%s)",
             modell, MOVIE_COLLECTION,
             len(movies), len(series_rows), len(episode_rows), force)

    for phase, zeilen, tabelle, dokument in phasen:
        if abbruch:
            break
        t_phase = time.time()
        _phase_setzen(phase, 0, len(zeilen))
        for i, zeile in enumerate(zeilen):
            eintrag_id, roh_text, meta = dokument(zeile)
            try:
                _eintrag_einbetten(modell, eintrag_id, roh_text, meta, tabelle)
            except _ModellGewechselt:
                gewechselt = True
                abbruch = _Meldung("abbruch_modell")
                log.warning("Einbetten abgebrochen: Modell %s ist nicht mehr eingestellt", modell)
                break
            except Exception as e:
                failed += 1
                in_folge += 1
                letzter_fehler = str(e)
                log.warning("%s: Einbetten von %s fehlgeschlagen: %s",
                            _t("phase_" + phase, "de"), eintrag_id, e)
            else:
                ok += 1
                in_folge = 0
            erledigt += 1
            _fortschritt(phase, i + 1, len(zeilen), erledigt, total, t_phase)
            if in_folge >= _ABBRUCH_NACH_FEHLSCHLAEGEN:
                abbruch = _Meldung("abbruch_serie", n=in_folge, fehler=letzter_fehler)
                log.warning("Einbetten abgebrochen nach %d Fehlschlaegen in Folge", in_folge)
                break

    elapsed = time.time() - t0
    try:
        anzahl = _stand()[1].anzahl()
    except Exception:
        anzahl = -1
    log.info("Einbetten fertig: %d ok, %d fehlgeschlagen, %.1f s, "
             "Sammlung %r enthaelt jetzt %d Vektoren",
             ok, failed, elapsed, MOVIE_COLLECTION, anzahl)
    erg = {"total": total, "ok": ok, "failed": failed, "elapsed_s": round(elapsed, 1),
           "model": modell, "collection": MOVIE_COLLECTION}
    if abbruch:
        erg["fehler"] = abbruch
        erg["abgebrochen"] = True
    elif failed:
        erg["fehler"] = _Meldung("teilweise", n=failed, fehler=letzter_fehler)
    if gewechselt:
        erg["modell_gewechselt"] = True
    return erg


def embed_all(force: bool = False) -> dict:
    """Bettet Filme, Serien und Episoden ein — mit force=True wirklich ALLE.

    ``force=True`` ist der Weg nach einem Modellwechsel. Die Marke
    ``embedded=True`` in der Datenbank bezieht sich immer auf die Vektoren des
    Modells, mit dem sie gesetzt wurde; nach einem Wechsel ist sie wertlos.
    Sie wird deshalb hier VOR dem Lauf fuer alle Zeilen zurueckgesetzt und
    waehrend des Laufs einzeln wieder gesetzt. Das hat zwei Vorteile: erstens
    stimmt die Anzeige im Admin-Bereich danach wieder, zweitens ist ein
    abgebrochener Lauf (Neustart, Dienst weg) fortsetzbar — ein anschliessendes
    embed_all(force=False) macht genau bei den fehlenden Eintraegen weiter.

    Blockiert, bis der Lauf fertig ist (Cron). Laeuft schon einer, kommt
    sofort ``{"status": "laeuft_bereits"}`` zurueck. Fuer die Oberflaeche ist
    einbettung_starten() da, das im Hintergrund laeuft.
    """
    if not _lauf_lock.acquire(blocking=False):
        log.info("embed_all(force=%s): es laeuft schon ein Einbett-Lauf", force)
        return {"status": "laeuft_bereits"}
    erg: dict = {}
    fehler: str | None = None
    try:
        _fortschritt_beginnen("vorbereitung")
        erg = _einbett_lauf(force)
        fehler = erg.get("fehler")
    except Exception as e:
        fehler = _Meldung("lauf_abgebrochen", fehler=e)
        raise
    finally:
        _letzter_lauf_setzen(erg.get("ok", 0), erg.get("failed", 0), fehler)
        _fortschritt_beenden()
        _sperre_freigeben()
    return erg


def _alle_nutzer_ids() -> list[int]:
    with get_session() as s:
        return [u.id for u in s.exec(select(User)).all()]


def _profile_neu() -> str | None:
    """Profilvektoren aller Nutzer neu berechnen — dieselbe Logik wie der
    Cron-Auftrag. Gibt eine Fehlerzeile (eine _Meldung) zurueck oder None.

    Import erst hier: cron.py holt sich embeddings in seinen Jobs, ein
    Import oben in dieser Datei liefe im Kreis.
    """
    from cron import job_embed_user_profiles
    _phase_setzen("nutzerprofile")
    erg = job_embed_user_profiles() or {}
    if "skipped" in erg:
        grund = (_Meldung("kein_modell_kurz") if erg.get("skipped_key") == "kein_einbettungsmodell"
                 else erg["skipped"])
        return _Meldung("profile_uebersprungen", grund=grund)
    fehler = [f"{name}: {wert}" for name, wert in erg.items()
              if str(wert).startswith("error")]
    return _Meldung("profile_fehler", liste="; ".join(fehler)) if fehler else None


def _kette(force: bool) -> None:
    """Hintergrund-Faden von einbettung_starten. Haelt ``_lauf_lock`` bis zum
    Ende — auch waehrend Profile, Messung und Vorwaermen, damit niemand
    dazwischen das Modell wechselt (die Oberflaeche fragt einbettung_laeuft)
    und keine Messung auf einer halb gefuellten Sammlung landet."""
    fehler: list[str] = []
    erg: dict = {}
    try:
        erg = _einbett_lauf(force)
        if erg.get("fehler"):
            fehler.append(erg["fehler"])
        # Ohne Modell, nach einem Modellwechsel oder wenn der Dienst nicht
        # antwortet, haetten die naechsten Schritte nichts Sinnvolles zu tun.
        if not erg.get("nicht_eingerichtet") and not erg.get("abgebrochen"):
            hinweis = _profile_neu()
            if hinweis:
                fehler.append(hinweis)
            hinweis = _kalibrieren_falls_noetig()
            if hinweis:
                fehler.append(hinweis)
        if not erg.get("modell_gewechselt"):
            _phase_setzen("passung")
            aehnlichkeiten_vorwaermen(_alle_nutzer_ids())
    except Exception as e:
        log.exception("Einbettung im Hintergrund fehlgeschlagen")
        fehler.append(_Meldung("lauf_abgebrochen", fehler=e))
    finally:
        _letzter_lauf_setzen(erg.get("ok", 0), erg.get("failed", 0), fehler or None)
        _fortschritt_beenden()
        _sperre_freigeben()


def einbettung_starten(force: bool = False) -> dict:
    """Einbett-Lauf im Hintergrund: fehlende (oder mit ``force`` alle)
    Eintraege einbetten, danach die Profilvektoren aller Nutzer, danach die
    Skala messen, falls fuer das Modell noch keine gespeichert ist, zuletzt
    die Passungswerte vorrechnen.

    Kehrt sofort zurueck. Mit einem lokalen Modell dauert ein voller Lauf
    Stunden; eine Anfrage, die so lange offen bliebe, liefe in jeden
    Zeitablauf (Browser, Reverse-Proxy). Den Stand liefert einbettung_status().
    """
    if not _lauf_lock.acquire(blocking=False):
        return {"status": "laeuft_bereits"}
    try:
        _fortschritt_beginnen("vorbereitung")
        threading.Thread(target=_kette, args=(force,), name="einbettung",
                         daemon=True).start()
    except Exception:
        _fortschritt_beenden()
        _lauf_lock.release()
        raise
    return {"status": "gestartet"}


# ---------------------------------------------------------------------------
# Modellwechsel
# ---------------------------------------------------------------------------

def _marken_aus_sammlung(filme) -> int:
    """Marke 'embedded' aller Zeilen aus der Mitgliedschaft in ``filme``
    setzen. Gibt die Zahl der geaenderten Zeilen zurueck."""
    ids = set(filme.ids())
    geaendert = 0
    with get_session() as s:
        for tabelle in (Movie, Series, Episode):
            for zeile in s.exec(select(tabelle)).all():
                soll = zeile.id in ids
                if bool(zeile.embedded) != soll:
                    zeile.embedded = soll
                    geaendert += 1
        s.commit()
    return geaendert


def sammlungen_wechseln() -> dict:
    """Auf die Sammlungen des eingestellten Modells umstellen.

    Holt sie (oder legt sie an), verwirft die zwischengespeicherten
    Passungswerte und setzt die Marke 'embedded' danach, ob ein Titel in der
    neuen Sammlung liegt. Die Marke bezieht sich immer auf das Modell, mit
    dem sie gesetzt wurde: ohne den Abgleich hielte ein Wechsel zu einem
    neuen Modell die Bibliothek fuer fertig eingebettet, und ein Wechsel
    zurueck zu einem alten liesse alles noch einmal einbetten, obwohl die
    Vektoren laengst da sind.

    Alles unter der Sammlungssperre: ein laufender Lauf des alten Modells
    sieht danach den Wechsel und bricht ab, statt weiter Marken zu setzen.
    """
    global _modell, _collection, _user_profiles, MOVIE_COLLECTION, PROFILE_COLLECTION
    with _sammlung_lock:
        modell = anbieter.einbettung_modell()
        filme_name = _sammlungsname("movies", modell)
        profile_name = _sammlungsname("user_profiles", modell)
        filme = _sammlung_holen(filme_name)
        profile = _sammlung_holen(profile_name)
        alte = (MOVIE_COLLECTION, PROFILE_COLLECTION)
        _modell, _collection, _user_profiles = modell, filme, profile
        MOVIE_COLLECTION, PROFILE_COLLECTION = filme_name, profile_name
        # Die Matrizen der bisherigen Sammlungen aus dem Arbeitsspeicher nehmen
        # (in der Datenbank bleiben sie liegen, zurueckwechseln laedt sie neu).
        # Sonst hielte der Prozess bis zum Neustart die Vektoren beider Modelle
        # im RAM - bei nomic und bge-m3 zusammen rund 30 MB, ohne dass jemand
        # die alten noch liest.
        for name in alte:
            if name not in (filme_name, profile_name):
                _speicher.vergessen(name)
        _aehnlichkeiten_verwerfen()
        _skala_verwerfen()
        try:
            geaendert = _marken_aus_sammlung(filme)
        except Exception as e:
            log.warning("Modellwechsel: Marken nicht abgeglichen: %s", e)
            geaendert = -1
    log.info("Einbettungen: umgestellt auf Modell %s, Sammlung %r, Profile %r "
             "(%d Marken angepasst)", modell or "(keins)", filme_name, profile_name, geaendert)
    return einbettung_status()


def _einstellung_geaendert(alt: dict, neu: dict) -> None:
    """Beobachter fuer den Abschnitt ``embedding``.

    Nur ein neues Modell braucht neue Sammlungen. Aendern sich nur Anbieter,
    Adresse oder Schluessel, bleibt alles, wie es ist — anbieter.py liest die
    Verbindung bei jedem Aufruf neu, und seine Verfuegbarkeitsantwort gilt
    ohnehin nur eine Minute.
    """
    if _modell_von(alt) == _modell_von(neu):
        return
    status = sammlungen_wechseln()
    if status["eingerichtet"] and status["fehlend"] > 0:
        log.info("Einbettungen: %d Eintraege fehlen fuer %s — Lauf startet im Hintergrund",
                 status["fehlend"], status["modell"])
        if einbettung_starten()["status"] == "laeuft_bereits":
            # Ein Lauf des alten Modells bricht am naechsten Eintrag ab (er
            # prueft das Modell), eine Messung oder das Vorwaermen endet von
            # selbst — wer die Sperre freigibt, startet dann nach.
            _nachholen.set()
            # Wurde die Sperre genau zwischen den beiden Zeilen frei, kaeme
            # niemand mehr vorbei, der nachholt. Deshalb noch ein Versuch.
            if einbettung_starten()["status"] == "gestartet":
                _nachholen.clear()


settings.beobachten("embedding", _einstellung_geaendert)


# ---------------------------------------------------------------------------
# Start: Uebernahme aus Chroma (1.0.x), Marken, Stand im Log
# ---------------------------------------------------------------------------

def _chroma_altbestand() -> bool:
    """Liegt unter data/chroma noch der Index einer 1.0.x-Version?"""
    return (CHROMA_DIR / "chroma.sqlite3").is_file()


def _marken_nach_start(filme, dazu: set[str], zuruecksetzen: bool = True) -> tuple[int, int]:
    """Marken 'embedded' beim Start an den Vektorspeicher angleichen - aber nur
    in den zwei Richtungen, die sicher stimmen:

      * Marke ohne Vektor -> False. So sieht es nach einem Update von 1.0.x
        ohne Export aus: die Datenbank haelt alle Titel fuer eingebettet, der
        neue Speicher ist leer. Ohne den Abgleich bettete embed_missing nie
        wieder etwas ein, und Suche und Passung blieben fuer immer leer.
      * gerade aus einem Export uebernommen -> True. Lief diese Version schon
        einmal ohne Export (Marken zurueckgesetzt) und kommt der Export erst
        danach, bettete der naechste Lauf sonst alles neu ein, obwohl die
        Vektoren eben uebernommen wurden.

    Bewusst NICHT allgemein "Vektor da -> True": nach "Alle neu einbetten"
    (force) stehen die Marken absichtlich auf False, waehrend die alten
    Vektoren noch da sind; ein Neustart mittendrin soll den Lauf nicht fuer
    erledigt erklaeren.

    ``zuruecksetzen=False`` laesst Marken ohne Vektor stehen und zaehlt sie
    nur - fuer einen Export, der da ist, aber nicht uebernommen werden konnte
    (siehe beim_start). Gibt (ohne Vektor, gesetzt) zurueck; der erste Wert
    ist mit ``zuruecksetzen`` die Zahl der zurueckgesetzten Marken.
    """
    ids = set(filme.ids())
    runter = hoch = 0
    with get_session() as s:
        for tabelle in (Movie, Series, Episode):
            for zeile in s.exec(select(tabelle)).all():
                if zeile.embedded and zeile.id not in ids:
                    runter += 1
                    if zuruecksetzen:
                        zeile.embedded = False
                elif not zeile.embedded and zeile.id in dazu:
                    zeile.embedded = True
                    hoch += 1
        if (runter and zuruecksetzen) or hoch:
            s.commit()
    return runter, hoch


def beim_start() -> dict:
    """Startschritt, aufgerufen von main._startup nach den Migrationen und vor
    dem Zeitplan:

      1. einen Export der Chroma-Vektoren von 1.0.x uebernehmen, falls unter
         data/vektor_export einer liegt (vektorspeicher.export_uebernehmen);
      2. die Marken 'embedded' angleichen (_marken_nach_start) - vor dem
         Zeitplan, weil ein naechtlicher Lauf sonst nach den alten Marken
         entschiede, was fehlt;
      3. Modell, Sammlungen und Zahl der Vektoren ins Log, mit Hinweis, was zu
         tun ist, wenn der Speicher leer ist.

    Synchron statt im eigenen Faden: Uebernahme und Abgleich muessen fertig
    sein, bevor der erste Lauf oder das Vorwaermen die Sammlung liest. Die
    Uebernahme von rund 9000 Vektoren dauert wenige Sekunden und kommt nur
    einmal vor; sonst kostet der Schritt das Zaehlen und einen Durchgang
    durch die Marken.
    """
    erg: dict = {"uebernahme": None, "marken_zurueck": 0, "marken_gesetzt": 0,
                 "marken_behalten": 0}
    try:
        erg["uebernahme"] = vektorspeicher.export_uebernehmen(_speicher, VEKTOR_EXPORT_DIR)
    except Exception as e:
        log.exception("Vektor-Export: Uebernahme fehlgeschlagen")
        erg["uebernahme"] = {"sammlungen": {}, "fehler": [str(e)], "umbenannt_in": None}
    uebernahme = erg["uebernahme"] or {}
    uebernommen = {name: s["uebernommen"] for name, s in
                   (uebernahme.get("sammlungen") or {}).items() if s.get("uebernommen")}
    fehler_export = [f for f in (uebernahme.get("fehler") or ())
                     if not str(f).startswith("umbenennen:")]

    with _sammlung_lock:
        modell, filme, profile = _modell, _collection, _user_profiles
        filme_name, profile_name = MOVIE_COLLECTION, PROFILE_COLLECTION
        # Ein Export, der da ist, dessen Filmsammlung fuer das eingestellte
        # Modell aber nicht uebernommen werden konnte (Pruefsumme, Platte
        # voll, Datenbank gesperrt, export.json fehlt ...), bleibt liegen und
        # wird beim naechsten Start erneut gelesen. Bis dahin die Marken NICHT
        # zuruecksetzen: sonst bettete schon der naechste taegliche Lauf die
        # ganze Bibliothek neu ein (mit einem lokalen Modell auf der CPU
        # Stunden), obwohl die Vektoren im Export liegen. Scheiterte nur eine
        # andere Sammlung (etwa die eines alten Modells) oder nur das
        # Umbenennen, ist die eigene da, und es gilt der normale Abgleich.
        export_haengt = bool(fehler_export) and filme_name not in (uebernahme.get("sammlungen") or {})
        erg["export_haengt"] = export_haengt
        if modell:
            dazu = set(((uebernahme.get("sammlungen") or {}).get(filme_name) or {}).get("ids") or ())
            try:
                ohne_vektor, erg["marken_gesetzt"] = _marken_nach_start(
                    filme, dazu, zuruecksetzen=not export_haengt)
                erg["marken_behalten" if export_haengt else "marken_zurueck"] = ohne_vektor
            except Exception as e:
                log.warning("Einbettungen: Marken beim Start nicht abgeglichen: %s", e)

    if uebernommen:
        log.info("Einbettungen: %d Vektoren aus dem Chroma-Export uebernommen (%s) - "
                 "dafuer muss nichts neu eingebettet werden.", sum(uebernommen.values()),
                 ", ".join(f"{n}: {k}" for n, k in sorted(uebernommen.items())))
    if not modell:
        log.info("Einbettungen: kein Einbettungsmodell eingerichtet — Suche "
                 "und Passung bleiben leer, bis eines eingetragen ist "
                 "(Admin -> Einstellungen -> Embeddings).")
        return erg
    try:
        anzahl_filme, anzahl_profile = filme.anzahl(), profile.anzahl()
    except Exception as e:
        log.warning("Einbettungen: Sammlung nicht lesbar: %s", e)
        return erg
    erg["vektoren"], erg["profile"] = anzahl_filme, anzahl_profile
    log.info("Einbettungen: Modell %s, Sammlung %r mit %d Vektoren, "
             "Profile %r mit %d Vektoren",
             modell, filme_name, anzahl_filme, profile_name, anzahl_profile)
    if erg["marken_zurueck"]:
        log.info("Einbettungen: %d Eintraege ohne Vektor als fehlend markiert - der "
                 "naechste Lauf (taeglich oder 'Fehlende einbetten') bettet sie ein",
                 erg["marken_zurueck"])
    if erg["marken_gesetzt"]:
        log.info("Einbettungen: %d uebernommene Eintraege als eingebettet markiert",
                 erg["marken_gesetzt"])
    if export_haengt:
        log.error("Einbettungen: der Vektor-Export unter %s konnte fuer %r nicht "
                  "uebernommen werden (Fehler siehe oben). Marken 'eingebettet' ohne "
                  "Vektor bleiben deshalb stehen (%d Eintraege), damit nicht stundenlang "
                  "neu eingebettet wird; bis zur Uebernahme fehlen diese Titel in Suche "
                  "und Passung. Der naechste Start versucht es erneut. Ist der Export "
                  "nicht zu retten: ihn mit 1.0.x neu erstellen - oder den Ordner "
                  "umbenennen und neu starten, dann wird neu eingebettet.",
                  VEKTOR_EXPORT_DIR, filme_name, erg["marken_behalten"])
    elif anzahl_filme == 0 and _chroma_altbestand():
        log.warning("Einbettungen: Sammlung %r ist LEER. Unter %s liegt noch der "
                    "Chroma-Index einer Version 1.0.x; diese Version liest ihn nicht "
                    "mehr. Ohne Export wird die Bibliothek neu eingebettet - im "
                    "taeglichen Lauf oder sofort mit Admin -> Einstellungen -> "
                    "Embeddings: 'Fehlende einbetten'; mit einem lokalen Modell auf "
                    "der CPU dauert das Stunden, bis dahin liefern Suche und Passung "
                    "nichts. Um das zu vermeiden: 1.0.x noch einmal starten, im "
                    "Repo-Ordner 'docker exec -i cinematekai python - < "
                    "tools/chroma_export.py' ausfuehren und danach wieder diese "
                    "Version starten - sie uebernimmt den Export beim Start.",
                    filme_name, CHROMA_DIR)
    elif anzahl_filme == 0:
        log.warning("Einbettungen: Sammlung %r ist LEER — die Vektorsuche "
                    "liefert nichts, bis eingebettet wurde (Admin -> "
                    "Einstellungen -> Embeddings: 'Fehlende einbetten').",
                    filme_name)
    elif _chroma_altbestand():
        log.info("Einbettungen: %s (Chroma-Index von 1.0.x) wird nicht mehr benutzt. "
                 "Er kann geloescht werden, sobald kein Rueckweg auf 1.0.x mehr "
                 "noetig ist.", CHROMA_DIR)
    if anzahl_profile == 0:
        log.warning("Einbettungen: keine Profilvektoren in %r — die "
                    "Passungswerte bleiben leer, bis der Cron-Auftrag "
                    "'embed_user_profiles' gelaufen ist.", profile_name)
    return erg


def _db_zaehlen() -> tuple[int, int]:
    """(Eintraege gesamt, davon eingebettet) ueber Filme, Serien, Episoden."""
    gesamt = eingebettet = 0
    with get_session() as s:
        for tabelle in (Movie, Series, Episode):
            gesamt += int(s.exec(select(func.count(tabelle.id))).one() or 0)
            eingebettet += int(s.exec(select(func.count(tabelle.id))
                                      .where(tabelle.embedded == True)).one() or 0)
    return gesamt, eingebettet


def einbettung_status() -> dict:
    """Stand der Einbettungen fuer den Admin-Bereich."""
    abschnitt = settings.get("embedding")
    p = anbieter.preset(abschnitt.get("provider", ""))
    _, filme, _ = _stand()
    try:
        vektoren = filme.anzahl()
    except Exception as e:
        log.warning("Einbettungen: Sammlung nicht lesbar: %s", e)
        vektoren = 0
    gesamt, eingebettet = _db_zaehlen()
    laeuft = einbettung_laeuft()
    with _zustand_lock:
        fortschritt = dict(_fortschritt_zustand) if _fortschritt_zustand else None
        letzter = dict(_letzter_lauf) if _letzter_lauf else None
    # Texte erst hier bauen, in der Sprache der Anfrage (siehe _Meldung).
    if fortschritt:
        fortschritt["phase"] = _t("phase_" + str(fortschritt.get("phase")))
    if letzter:
        teile = letzter.pop("fehler_teile", None) or []
        letzter["fehler"] = " · ".join(_text(t) for t in teile) or None
    return {
        "modell": _modell_von(abschnitt),
        "anbieter": anbieter.anzeigename(p["id"]) if p else "",
        "eingerichtet": _eingerichtet(abschnitt),
        "sammlung": MOVIE_COLLECTION,
        "vektoren": vektoren,
        "eintraege_gesamt": gesamt,
        "eingebettet": eingebettet,
        "fehlend": gesamt - eingebettet,
        "laeuft": laeuft,
        "fortschritt": fortschritt if laeuft else None,
        "letzter_lauf": letzter,
        "kalibrierung": skala(),
    }


def backfill_type_metadata() -> dict:
    """One-shot helper: tag every existing item in the movie collection with
    `type='movie'` if no `type` is set yet. Called from /admin/backfill-types
    after upgrading to Phase TV-A so we don't have to re-embed 393 films."""
    _, filme, _ = _stand()
    try:
        alle = filme.metadaten()
    except Exception as e:
        log.warning("backfill_type_metadata: get failed: %s", e)
        return {"error": str(e)}
    neu: dict[str, dict] = {}
    for mid, alt in alle.items():
        meta = dict(alt or {})
        if meta.get("type"):
            continue
        # Heuristic: if a row exists in the Series table → series, else movie.
        with get_session() as s:
            is_series = s.get(Series, mid) is not None
        meta["type"] = "series" if is_series else "movie"
        neu[mid] = meta
    if neu:
        # Uebergeben werden die vollstaendigen Metadaten (alte + type), das
        # Ersetzen im Speicher wirkt also wie das Ergaenzen bei Chroma.
        filme.metadaten_setzen(neu)
    return {"tagged": len(neu), "total": len(alle)}


def search_similar(movie_id: str, k: int = 15,
                   exclude_ids: Iterable[str] | None = None) -> list[dict]:
    """Find k movies most similar to the given movie by embedding.

    ``exclude_ids``: weitere IDs, die nicht vorkommen duerfen - die fuer den
    Nutzer verborgenen Titel (sichtbarkeit.Sicht.verborgen). Uebergeben statt
    hinterher gefiltert, damit die Suche ihr Fenster entsprechend vergroessert
    und trotzdem k Treffer liefert."""
    with get_session() as s:
        m = s.get(Movie, movie_id)
    if not m:
        return []
    text = anbieter.praefix_anfrage(movie_to_text(m), _stand()[0])
    ohne = {movie_id} | set(exclude_ids or ())
    return search_by_text(text, k=k + 1, exclude_ids=ohne, _raw_query=True)[:k]


# Deutsche Frage- und Fuellwoerter. nomic-embed-text ist englisch-optimiert;
# eine deutsche Frage wie "Welche MCU-Filme habe ich noch nicht gesehen?" wird
# von den Funktionswoertern dominiert — im Test lieferte sie "Ich - Einfach
# unverbesserlich", weil das Fragewort "Ich" auf den Filmtitel passte.
# Gemessen ueber 10 Anfragen: Precision@5 steigt von 55 % auf 74 %.
# Bewusst OHNE "Film"/"Serie" — die mitzuentfernen war messbar schlechter (72 %).
_DE_STOPWORDS = frozenset("""
welche welcher welches was wer wie wo wann warum wieso
habe hab hast hat haben ich du er sie es wir ihr mir mich dir dich
noch nicht kein keine mal etwas einen einem eine einer ein eines
der die das den dem des und oder aber fuer für von mit im in am an auf zu zum zur
mag möchte moechte will willst wollen soll sollte gib gibt
zeig zeige zeigen empfiehl empfehle empfehlen suche such finde finden
bitte danke bin ist sind war waren wurde werden
viel sehr gern gerne über ueber
""".split())

# Dasselbe fuer englische Fragen ("Which MCU movies haven't I seen yet?"),
# damit die Funktionswoerter auch dort nicht den Ausschlag geben. Gilt nur bei
# englischer Oberflaeche und kommt zur deutschen Liste DAZU: wer Deutsch tippt,
# obwohl die Oberflaeche englisch ist, wird weiter gefiltert wie bisher. Nicht
# gemessen wie die deutsche Liste — deshalb bewusst kurz und ohne Woerter, die
# oft zu Titeln gehoeren ("Her", "Them", "Up" faellt ohnehin an der
# Laengenpruefung durch). Ohne "movie"/"film"/"like": die tragen die Frage.
_EN_STOPWORDS = frozenset("""
the what which who how where when why have has had does did are was were been
you your they our and but for with from into about this that these those
there any some something anything more most very really just also not than then
can could should would will may might must want recommend suggest give tell please
haven hasn hadn don doesn didn isn aren wasn weren won couldn wouldn shouldn
""".split())
_STOPWOERTER_EN = _DE_STOPWORDS | _EN_STOPWORDS

_WORD_RE = re.compile(r"[\wäöüßÄÖÜ\-]+", re.UNICODE)


def normalize_user_query(query: str, sprache: str | None = None) -> str:
    """Entfernt Funktionswoerter aus einer *Nutzereingabe*: deutsche immer,
    bei englischer Oberflaeche zusaetzlich englische.

    ``sprache`` ("de"/"en"): Sprache der Oberflaeche; ohne Angabe die der
    laufenden Anfrage (texte.aktuell). Der Chat-Faden gibt sie ausdruecklich an.

    Nur fuer Freitext vom Nutzer gedacht (Suchfeld, Stimmungssuche, Chat).
    Nicht auf profilgenerierte Anfragen anwenden: dort stecken Filmtitel drin
    ("Der Pate"), die nicht zerpflueckt werden sollen.

    Bleibt nach dem Filtern nichts uebrig, wird die Eingabe unveraendert
    zurueckgegeben — lieber eine schlechte Suche als gar keine.
    """
    if not query or not query.strip():
        return query
    stoppwoerter = _DE_STOPWORDS
    if (texte.normalisieren(sprache) or texte.aktuell()) == "en":
        stoppwoerter = _STOPWOERTER_EN
    words = _WORD_RE.findall(query)
    kept = [w for w in words if w.lower() not in stoppwoerter and len(w) > 2]
    return " ".join(kept) if kept else query


def search_by_text(query: str, k: int = 15, exclude_ids: list[str] | None = None,
                   _raw_query: bool = False,
                   media_type: str | None = None) -> list[dict]:
    """Search the vector store by arbitrary text query.

    ``media_type`` filters the collection: 'movie', 'series', 'episode',
    'any' (everything), or None (movies + series, no episodes).

    ``distance`` ist der Kosinus-Abstand wie zu Chroma-Zeiten (1 - Kosinus,
    float32), ``score`` = round(1 - distance, 3).
    """
    modell, filme, _ = _stand()
    try:
        q = query if _raw_query else anbieter.praefix_anfrage(query, modell)
        vec = _einbetten_mit(q, modell)
    except Exception as e:
        log.warning("Query embedding failed: %s", e)
        return []
    # Default behaviour: only movies + series (episodes opt-in).
    if media_type == "episode":
        where = {"type": "episode"}
    elif media_type in ("movie", "series"):
        where = {"type": media_type}
    elif media_type == "any":
        # All types incl. episodes
        where = None
    else:
        # Default = movies + series, NO episodes (avoid granularity-mix
        # in mood searches).
        where = {"type": {"$in": ["movie", "series"]}}
    # Frueher stand hier ein harter Deckel von 50. Aufrufer mit groesserem k
    # (daily_for k=60, by_mood bis k*3) wurden dadurch still abgeschnitten —
    # nach dem "ungesehen"-Filter blieben teils nur 15 Kandidaten uebrig,
    # obwohl die Bibliothek 179 ungesehene Filme enthielt. Jetzt nur noch
    # gegen die tatsaechliche Sammlungsgroesse begrenzt (so stammt es aus der
    # Chroma-Zeit, die bei n_results > Anzahl Dokumente warnte; das Fenster
    # bleibt gleich, damit die Treffer dieselben bleiben).
    n_wanted = k + len(exclude_ids or [])
    try:
        n_available = filme.anzahl()
    except Exception:
        n_available = n_wanted
    try:
        treffer = filme.suchen(vec, max(1, min(n_wanted, n_available)), where)
    except Exception as e:
        # Frueher kam ein Fehler der Abfrage als 500 bei der Oberflaeche an.
        # Weniger Treffer (keine) sind fuer Startseite und Chat besser als
        # eine Fehlerseite; der Grund steht im Log.
        log.warning("Vektorsuche in %r fehlgeschlagen: %s", getattr(filme, "name", "?"), e)
        return []
    items = []
    for mid, abstand, meta in treffer:
        if exclude_ids and mid in exclude_ids:
            continue
        items.append({
            "movie_id": mid,
            "name": (meta or {}).get("name", ""),
            "distance": abstand,
            "score": round(1.0 - abstand, 3),
        })
        if len(items) >= k:
            break
    return items


def collection_count() -> int:
    """Number of items in the movie collection of the current model."""
    return _stand()[1].anzahl()


# ── Aufraeumen: Vektoren ohne Datenbankzeile ───────────────────────────────
# Verschwindet ein Titel aus Jellyfin, loescht sync.py die Zeile in
# movie/series/episode — den Vektor aber nicht. Die Karteileichen
# sammeln sich dadurch mit jedem Sync weiter an.
# Nachgemessen am 01.10.2026: die alte nomic-Sammlung "movies" trug 5767
# Vektoren bei 3187 Datenbank-IDs, also 2580 Karteileichen (44,7 %). Die am
# 30.09. neu aufgebaute Sammlung movies__bge_m3 war zu diesem Zeitpunkt noch
# sauber (3187 Vektoren, 0 verwaist) — dieser Schritt haelt sie es.
# Falsche Treffer entstehen dadurch nicht, weil jeder Treffer gegen die
# Datenbank aufgeloest wird. Es kostet aber Platz und Suchzeit, und
# _berechne_alle_aehnlichkeiten rechnet bei jedem kalten Aufruf gegen ALLE Vektoren.
#
# Groesse der Loeschhaeppchen: der Speicher nimmt auch lange ID-Listen an,
# aber haeppchenweise bleibt jede Transaktion kurz, und ein Fehler mittendrin
# laesst den bereits geschafften Teil geloescht.
_LOESCH_HAEPPCHEN = 500

# Anteil, ab dem das Aufraeumen verweigert wird (mehr als die Haelfte).
_WAISEN_OBERGRENZE = 0.5


def verwaiste_vektoren_entfernen() -> int:
    """Entfernt Vektoren, zu denen keine Zeile in movie/series/episode mehr
    gehoert. Gibt die Zahl der entfernten Eintraege zurueck und schreibt sie
    ins Log.

    Angefasst wird AUSSCHLIESSLICH die Sammlung des eingestellten Modells.
    Die Sammlungen anderer Modelle (etwa die alte nomic-Sammlung "movies")
    bleiben bewusst unberuehrt: sie sind der Rueckfall und sollen genau so
    liegen bleiben, wie sie beim Wechsel waren. Stellt man das Modell im
    Admin-Bereich zurueck, ist die Suche sofort wieder da (siehe
    _sammlungsname oben); sammlungen_wechseln() setzt dann die Marken aus der
    Mitgliedschaft, und nur was seither dazugekommen ist, bettet der naechste
    Lauf nach.

    Sicherheitsnetz: Sind mehr als die Haelfte der Vektoren betroffen, wird
    nichts geloescht, sondern nur gewarnt. Ein leerer oder halb gefuellter
    Datenbankstand (abgebrochener Sync, verlorene Datei, querliegende
    Migration) saehe sonst aus wie "fast alles geloescht" und wuerde die
    ganze Sammlung ausradieren — deren Wiederaufbau kostet zweieinhalb
    Stunden.

    Laeuft im Backend-Prozess (Cron), nicht in einem zweiten Prozess: am
    29.09. belegt, dass Schreibzugriffe von aussen nicht im Suchindex
    ankommen.
    """
    _, filme, _ = _stand()
    name = MOVIE_COLLECTION
    try:
        # Nur die IDs - die Vektoren braucht der Vergleich nicht.
        ids = filme.ids()
    except Exception as e:
        log.warning("Aufraeumen: Sammlung %r nicht lesbar: %s", name, e)
        return 0
    if not ids:
        log.info("Aufraeumen: Sammlung %r ist leer — nichts zu tun", name)
        return 0

    # Eigene Abfrage, damit die Funktion auch allein aufrufbar bleibt (Admin
    # kann prune_stale einzeln anstossen) und nicht davon abhaengt, dass der
    # Aufrufer die IDs schon geladen hat.
    with get_session() as s:
        bekannt = {m.id for m in s.exec(select(Movie)).all()}
        bekannt |= {x.id for x in s.exec(select(Series)).all()}
        bekannt |= {e.id for e in s.exec(select(Episode)).all()}

    waisen = [i for i in ids if i not in bekannt]
    if not waisen:
        log.info("Aufraeumen: keine verwaisten Vektoren in %r "
                 "(%d Vektoren, %d Datenbank-IDs)",
                 name, len(ids), len(bekannt))
        return 0

    anteil = len(waisen) / len(ids)
    if anteil > _WAISEN_OBERGRENZE:
        log.warning("Aufraeumen ABGEBROCHEN: %d von %d Vektoren in %r haben "
                    "keine Datenbankzeile (%.1f %%) — mehr als die Haelfte. Es "
                    "wurde NICHTS geloescht. Das deutet auf einen "
                    "unvollstaendigen Datenbankstand hin, nicht auf geloeschte "
                    "Titel: in movie/series/episode stehen nur %d IDs. Bitte "
                    "die Datenbank pruefen und den Job danach erneut anstossen.",
                    len(waisen), len(ids), name, anteil * 100,
                    len(bekannt))
        return 0

    entfernt = 0
    for start in range(0, len(waisen), _LOESCH_HAEPPCHEN):
        haeppchen = waisen[start:start + _LOESCH_HAEPPCHEN]
        try:
            filme.loeschen(haeppchen)
        except Exception as e:
            log.warning("Aufraeumen: Loeschen von %d Vektoren fehlgeschlagen, "
                        "Abbruch nach %d entfernten: %s",
                        len(haeppchen), entfernt, e)
            break
        entfernt += len(haeppchen)

    if entfernt:
        # Die zwischengespeicherten Passungswerte halten die geloeschten IDs
        # noch fest (Frist 26 Stunden). Ohne Verwerfen lieferte die Startseite
        # bis zum naechsten Schreibzugriff Werte zu Titeln, die es nicht mehr
        # gibt.
        _aehnlichkeiten_verwerfen()
    log.info("Aufraeumen: %d verwaiste Vektoren aus %r entfernt "
             "(%.1f %% von %d), Sammlung enthaelt jetzt %d",
             entfernt, name, anteil * 100, len(ids), filme.anzahl())
    return entfernt


# ---------------------------------------------------------------------------
# User profile embeddings (for match-score in Filmdetail-Seite)
# ---------------------------------------------------------------------------

def embed_user_profile(user_id: int, query_text: str) -> bool:
    """Embed a user's taste profile text and store it in the user_profiles
    collection. Called from the nightly cron. Returns True on success."""
    if not query_text.strip():
        return False
    modell = _stand()[0]
    text = anbieter.praefix_anfrage(query_text, modell)
    try:
        vec = _einbetten_mit(text, modell)
    except Exception as e:
        log.warning("User profile embedding failed for user %s: %s", user_id, e)
        return False
    with _sammlung_lock:
        # Dieselbe Pruefung wie in _ablegen: der Vektor gehoert nur in die
        # Profilsammlung SEINES Modells.
        if _modell != modell:
            log.warning("User profile embedding for user %s verworfen: "
                        "Modell wurde gewechselt", user_id)
            return False
        _user_profiles.schreiben(str(user_id), vec, None, text)
    _aehnlichkeiten_verwerfen()
    return True


# ── Zwischenspeicher fuer profile_all_similarities ─────────────────────────
# Die Berechnung rechnet bei jedem Aufruf gegen SAEMTLICHE Vektoren. Die
# Startseite loest sie viermal gleichzeitig aus (Filme, Serien, Weiterschauen,
# Serienliste); gemessen mit Chroma, das die Vektoren dafuer jedes Mal aus der
# Datenbank las, brauchte ein Aufruf allein 1,2 s, alle vier zusammen aber
# 9,7 s, weil sie sich um denselben Prozessor stritten. Der Vektorspeicher
# haelt die Matrix im Arbeitsspeicher, die Rechnung selbst ist dadurch billig;
# der Zwischenspeicher bleibt trotzdem, weil er nichts kostet und das Bauen
# der Ergebnis-Tabelle je Aufruf spart. Das Ergebnis aendert sich nur, wenn
# der naechtliche Cron das Profil neu schreibt oder ein Sync neue Vektoren
# anlegt — beides verwirft den Speicher.
#
# Die Frist ist nur ein Sicherheitsnetz, denn echte Aenderungen verwerfen den
# Speicher selbst. Anfangs stand sie auf 15 Minuten: bei der ueblichen Nutzung
# (die App einmal am Abend oeffnen) war er dann fast immer abgelaufen, und der
# erste Aufruf brauchte 2,75 s. 26 Stunden decken einen Tag ab; weil der
# naechtliche Cron ohnehin neu vorwaermt, ueberlappen sich die Zyklen.
#
# Setzt EINEN Backend-Prozess voraus (so laeuft uvicorn hier). Mit mehreren
# Workern haette jeder seinen eigenen Speicher, und das Verwerfen nach dem
# Cron erreichte nur einen davon — dann muesste die Frist wieder kurz sein.
#
# Zum Modellwechsel: hier kann nichts modelluebergreifend haengenbleiben.
# sammlungen_wechseln() verwirft den Speicher, und eine Rechnung, die noch
# mit den alten Sammlungen lief, wird wegen der neuen Generation nicht mehr
# gespeichert. Nichts davon wird auf die Platte geschrieben.
_AEHNL_FRIST_S = 26 * 3600.0
_aehnl_speicher: dict[int, tuple[int, float, dict[str, float]]] = {}
_aehnl_generation = 0
_aehnl_zugriff = threading.Lock()   # schuetzt Speicher und Generation
_aehnl_rechnen = threading.Lock()   # nur eine Berechnung zur Zeit


def _aehnlichkeiten_verwerfen() -> None:
    """Nach jedem Schreiben von Film- oder Profilvektoren aufrufen."""
    global _aehnl_generation
    with _aehnl_zugriff:
        _aehnl_generation += 1
        _aehnl_speicher.clear()


def _aus_speicher(user_id: int) -> dict[str, float] | None:
    with _aehnl_zugriff:
        e = _aehnl_speicher.get(user_id)
        if e and e[0] == _aehnl_generation and time.monotonic() - e[1] < _AEHNL_FRIST_S:
            return e[2]
    return None


def profile_all_similarities(user_id: int) -> dict[str, float]:
    """Passung jedes Titels zum Nutzerprofil, zwischengespeichert.

    Gleichzeitige Aufrufe rechnen nicht parallel: der erste rechnet, die
    uebrigen warten auf ihn und uebernehmen sein Ergebnis — schneller als
    vier Rechnungen, die sich den Prozessor teilen. Die Generation wird vor
    der Berechnung gemerkt; wurde waehrenddessen geschrieben, geht das
    Ergebnis zurueck, wird aber nicht gespeichert. Zurueck kommt eine Kopie,
    damit kein Aufrufer den Speicher veraendern kann.
    """
    treffer = _aus_speicher(user_id)
    if treffer is not None:
        return dict(treffer)
    with _aehnl_rechnen:
        treffer = _aus_speicher(user_id)
        if treffer is not None:
            return dict(treffer)
        with _aehnl_zugriff:
            generation = _aehnl_generation
        ergebnis = _berechne_alle_aehnlichkeiten(user_id)
        # Leer heisst: Profil oder Vektoren fehlen noch. Nicht festhalten,
        # damit der naechste Aufruf es erneut versucht.
        if ergebnis:
            with _aehnl_zugriff:
                if generation == _aehnl_generation:
                    _aehnl_speicher[user_id] = (generation, time.monotonic(), ergebnis)
        return dict(ergebnis)


def aehnlichkeiten_vorwaermen(user_ids: list[int]) -> dict[int, int]:
    """Rechnet die Passungswerte vorab, damit schon der erste Seitenaufruf
    aus dem Speicher bedient wird. Gibt je Nutzer die Zahl der Titel zurueck
    (0 heisst: noch kein Profil oder keine Vektoren).

    Misst vorher die Skala, falls sie fuer das Modell noch vorlaeufig ist
    (siehe _nachkalibrieren). Das ist der Weg fuer eine frische Installation:
    dort bettet der Cron die Bibliothek ein und rechnet die Profile, ohne
    dass je einbettung_starten liefe — und dieser Schritt kommt in der
    taeglichen Kette nach beidem.
    """
    _nachkalibrieren()
    return {uid: len(profile_all_similarities(uid)) for uid in user_ids}


def _berechne_alle_aehnlichkeiten(user_id: int) -> dict[str, float]:
    """Cosine similarity between the user's profile vector and *every*
    vector in the movie collection (movies, series, episodes), clipped to
    [0, 1]. Returns ``{movie_id: sim}``. One matrix-vector product over the
    normalised vectors the store keeps in memory — cheap even for 10 k titles."""
    import numpy as np
    _, filme, profile = _stand()
    try:
        uv = profile.vektor(str(user_id))
    except Exception as e:
        log.warning("profile_all_similarities user fetch failed: %s", e)
        return {}
    # Kein Profil oder ein Nullvektor: wie bisher keine Werte.
    if uv is None or not np.any(uv):
        return {}
    try:
        ids, sims = filme.aehnlichkeiten(uv)
    except Exception as e:
        log.warning("profile_all_similarities movies fetch failed: %s", e)
        return {}
    if not ids:
        return {}
    sims = np.clip(sims, 0.0, 1.0)
    return {ids[i]: float(sims[i]) for i in range(len(ids))}


# ---------------------------------------------------------------------------
# Skala: Aehnlichkeit -> Prozent, je Modell
# ---------------------------------------------------------------------------
# Zwei Prozentanzeigen haengen an absoluten Aehnlichkeiten, und deren Lage
# verschiebt sich mit dem Modell (am 29.09. mit derselben Frage und denselben
# sechs Filmen: nomic-embed-text 0,471 bis 0,632, bge-m3 0,391 bis 0,536):
#   * Passung (Profil -> Titel): match_floor = 0 %, match_ceil = 100 %.
#   * Stimmungssuche (Anfrage -> Titel, recommendations._scale_mood_scores):
#     mood_min ist eine harte Untergrenze — darunter ist ein Treffer kein
#     Treffer mehr, sondern Rauschen; mood_floor = 0 %, mood_ceil = 100 %.
#
# Voreinstellung bge-m3 — die von Hand gesetzten Werte, unveraendert:
# Passung kalibriert am 30.09.2026, gemessen ueber alle 3187 Vektoren: Median
# der Bibliothek 0,39-0,42 je Nutzer, 95. Perzentil 0,56-0,61, Bestwert
# 0,72-0,76. Mit der alten nomic-Abbildung ((sim-0,3)/0,5) haette derselbe Film
# 58 statt 69 Prozent bekommen — die Zahlen haetten also nur anders ausgesehen,
# ohne dass sich etwas verbessert haette. Untergrenze und Obergrenze sind
# deshalb an die neue Verteilung gelegt: die Haelfte der Bibliothek landet
# unter 15 %, und die 100 bleibt den echten Lieblingen vorbehalten.
# Erster Versuch mit 0,33/0,62 war zu grosszuegig: Bambi, Casino,
# Interstellar und Schindlers Liste lagen alle zwischen 80 und 100 %.
# Die Messregel unten (kalibrieren) ergab am 10.10.2026 fuer bge-m3
# 0,377/0,697 und 0,449/0,439/0,540 — sie bestaetigt die Handwerte. Diese
# bleiben trotzdem stehen, damit sich fuer die bestehende Installation keine
# Prozentzahl verschiebt.
_BGE_M3 = {"match_floor": 0.38, "match_ceil": 0.70,
           "mood_min": 0.45, "mood_floor": 0.44, "mood_ceil": 0.54}

_SKALA_FELDER = ("match_floor", "match_ceil", "mood_min", "mood_floor", "mood_ceil")

# Die zwoelf Probe-Anfragen der Messung — dieselben, mit denen die Regel an
# der Bibliothek gegen die Handwerte von bge-m3 geprueft wurde.
_PROBEN = (
    "lustiger Tierfilm für Kinder", "spannender Thriller", "romantische Komödie",
    "Film über Gangster", "Science-Fiction im Weltraum", "gruseliger Horrorfilm",
    "Dokumentation über Natur", "Animationsfilm mit Tieren", "Krieg und Geschichte",
    "etwas Leichtes zum Abschalten", "Superhelden Action", "Weihnachtsfilm für die Familie",
)

# skala() steckt in jeder Prozentzahl (passung_prozent wird je Titel einer
# Liste aufgerufen); die Einstellungen jedes Mal zu kopieren waere unnoetig.
# kalibrieren() und der Modellwechsel verwerfen den Speicher selbst; die
# kurze Frist faengt Aenderungen ab, die an diesem Modul vorbei gehen
# (settings.neu_laden, ein spaeteres Formular fuer Handwerte).
_SKALA_FRIST_S = 30.0
_skala_speicher: tuple[str, float, dict] | None = None


def _modell_schluessel(modell: str) -> str:
    """Schluessel der Kalibrierung: ohne Ollama-Tag, klein
    ('bge-m3:latest' -> 'bge-m3')."""
    return (modell or "").split(":", 1)[0].strip().lower()


def _skala_verwerfen() -> None:
    global _skala_speicher
    _skala_speicher = None


def _skala_lesen(schluessel: str) -> dict:
    gespeichert = settings.get("kalibrierung").get(schluessel)
    if isinstance(gespeichert, dict):
        try:
            werte = {f: float(gespeichert[f]) for f in _SKALA_FELDER}
            return {**werte, "quelle": str(gespeichert.get("quelle") or "gemessen"),
                    "gemessen_am": gespeichert.get("gemessen_am")}
        except (KeyError, TypeError, ValueError):
            log.warning("Kalibrierung fuer %r unvollstaendig, wird ignoriert", schluessel)
    # Ohne Messung: fuer bge-m3 die Handwerte, fuer jedes andere Modell
    # dieselben Zahlen als Notbehelf ("vorlaeufig") bis zur ersten Messung.
    quelle = "voreingestellt" if schluessel == "bge-m3" else "vorlaeufig"
    return {**_BGE_M3, "quelle": quelle, "gemessen_am": None}


def _skala_intern(modell: str | None = None) -> dict:
    """Wie skala(), aber ohne Kopie — nur fuer Leser in diesem Modul."""
    global _skala_speicher
    # _modell ohne Sperre lesen: passung_prozent laeuft je Titel einer Liste
    # und soll nicht warten, solange sammlungen_wechseln die Marken abgleicht.
    # Ein einzelner Lesezugriff auf eine Modul-Globale ist dafuer sicher genug.
    schluessel = _modell_schluessel(_modell if modell is None else modell)
    sp = _skala_speicher
    jetzt = time.monotonic()
    if sp is not None and sp[0] == schluessel and jetzt - sp[1] < _SKALA_FRIST_S:
        return sp[2]
    werte = _skala_lesen(schluessel)
    _skala_speicher = (schluessel, jetzt, werte)
    return werte


def skala(modell: str | None = None) -> dict:
    """Skala des Modells (Vorgabe: das, zu dem die Sammlungen gehoeren):
    ``{"match_floor", "match_ceil", "mood_min", "mood_floor", "mood_ceil",
    "quelle", "gemessen_am"}``. Quelle ist "gemessen" (gespeichert),
    "voreingestellt" (bge-m3) oder "vorlaeufig"."""
    return dict(_skala_intern(modell))


def passung_prozent(sim: float) -> int:
    """Kosinus-Aehnlichkeit -> Prozentzahl fuer die Oberflaeche."""
    s = _skala_intern()
    spanne = s["match_ceil"] - s["match_floor"]
    if spanne <= 0:
        return 100 if sim >= s["match_ceil"] else 0
    return int(round(max(0.0, min(1.0, (sim - s["match_floor"]) / spanne)) * 100))


# ── Messregel ─────────────────────────────────────────────────────────────
# Am 10.10.2026 an der Bibliothek gemessen und so gewaehlt, dass sie fuer
# bge-m3 die Handwerte reproduziert:
#   Passung:  match_floor = kleinster Median je Nutzer - 0,01,
#             match_ceil  = kleinster Bestwert je Nutzer - 0,02
#             (ueber ALLE Vektoren der Sammlung, also auch Episoden — genau
#             die Menge, gegen die _berechne_alle_aehnlichkeiten rechnet).
#   Stimmung: mood_min   = Mittelwert der 99. Perzentile der Probe-Anfragen,
#             mood_floor = mood_min - 0,01,
#             mood_ceil  = 90. Perzentil der Bestwerte der Probe-Anfragen
#             (nur Filme und Serien — wie die Stimmungssuche selbst).
#
# Gerundet wird auf drei Stellen, und zwar NACH AUSSEN: Untergrenzen ab,
# Obergrenzen auf. So umschliesst die gerundete Skala die gemessene, und kein
# Titel faellt allein durch die Rundung unter die Schwelle. Kaufmaennisch
# gerundet kaemen aus den bge-m3-Probezahlen 0,450 und 0,539 heraus statt
# der 0,449 und 0,540, mit denen die Regel abgeglichen ist.
_PLAUSIBEL_ABSTAND = 0.03


def _abrunden(x: float) -> float:
    # round(..., 6) zuerst: 0.387 - 0.01 ist im Rechner 0.37699999..., und
    # math.floor machte daraus sonst 0.376.
    return math.floor(round(x * 1000, 6)) / 1000


def _aufrunden(x: float) -> float:
    return math.ceil(round(x * 1000, 6)) / 1000


def skala_berechnen(mediane: list[float], bestwerte: list[float],
                    proben_p99: list[float], proben_top1: list[float]) -> dict:
    """Skala aus den Rohzahlen einer Messung — reine Rechnung, ohne Vektorspeicher.

    ``mediane``/``bestwerte``: je Nutzerprofil Median und Hoechstwert der
    Aehnlichkeit zu allen Vektoren der Sammlung. ``proben_p99``/
    ``proben_top1``: je Probe-Anfrage das 99. Perzentil und der Bestwert der
    Aehnlichkeit zu allen Filmen und Serien. Wirft ValueError (Meldung fuer
    die Oberflaeche), wenn Zahlen fehlen oder die Skala unplausibel ist.
    """
    import numpy as np
    if not mediane or not bestwerte:
        raise ValueError(_Meldung("kein_profil"))
    if not proben_p99 or not proben_top1:
        raise ValueError(_Meldung("keine_probe"))
    match_floor = _abrunden(min(mediane) - 0.01)
    match_ceil = _aufrunden(min(bestwerte) - 0.02)
    mood_min = _abrunden(float(np.mean(proben_p99)))
    mood_floor = round(mood_min - 0.01, 3)
    mood_ceil = _aufrunden(float(np.percentile(proben_top1, 90)))
    if not match_ceil > match_floor + _PLAUSIBEL_ABSTAND:
        raise ValueError(_Meldung("unplausibel_passung", unten=match_floor, oben=match_ceil))
    if not mood_ceil > mood_floor + _PLAUSIBEL_ABSTAND:
        raise ValueError(_Meldung("unplausibel_stimmung", unten=mood_floor, oben=mood_ceil))
    return {"match_floor": match_floor, "match_ceil": match_ceil,
            "mood_min": mood_min, "mood_floor": mood_floor, "mood_ceil": mood_ceil}


def _einheitsvektor(vec):
    import numpy as np
    v = np.asarray(vec, dtype=np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else None


def _messen(modell: str) -> dict:
    """Rohzahlen fuer die Kalibrierung. Liest den Vektorspeicher, bettet die
    zwoelf Proben ein, schreibt nichts."""
    import numpy as np
    with _sammlung_lock:
        if _modell != modell:
            raise _ModellGewechselt(_Meldung("modell_gewechselt"))
        filme, profile = _collection, _user_profiles

    p_ids, p_vek, _ = profile.alle()
    if not p_ids:
        raise ValueError(_Meldung("kein_profil"))

    # Die Matrix kommt schon normiert aus dem Speicher (Nullvektoren bleiben
    # Null) - dasselbe, was hier frueher von Hand geschah.
    ids, M, metas = filme.alle()
    if not ids:
        raise ValueError(_Meldung("sammlung_leer"))
    typen = np.array([(m or {}).get("type", "") for m in metas])
    film_serie = np.isin(typen, ["movie", "series"])
    if not film_serie.any():
        raise ValueError(_Meldung("keine_filme"))
    MF = M[film_serie]

    profile_zahlen: dict[str, dict] = {}
    for uid, vec in zip(p_ids, p_vek):
        u = _einheitsvektor(vec)
        if u is None:
            continue
        sims = np.clip(M @ u, 0.0, 1.0)
        profile_zahlen[str(uid)] = {"median": float(np.median(sims)), "max": float(sims.max())}

    proben: dict[str, dict] = {}
    for frage in _PROBEN:
        text = anbieter.praefix_anfrage(normalize_user_query(frage), modell)
        v = _einheitsvektor(_einbetten_mit(text, modell))
        if v is None:
            log.warning("Kalibrierung: Probe %r lieferte einen Nullvektor", frage)
            continue
        sims = np.clip(MF @ v, 0.0, 1.0)
        proben[frage] = {"p99": float(np.percentile(sims, 99)), "top1": float(sims.max())}

    return {"vektoren": int(M.shape[0]), "filme_serien": int(MF.shape[0]),
            "profile": profile_zahlen, "proben": proben}


def _gerundet(x):
    """Rohzahlen fuer die Ablage kuerzen — vier Stellen reichen zum Nachvollziehen."""
    if isinstance(x, float):
        return round(x, 4)
    if isinstance(x, dict):
        return {k: _gerundet(v) for k, v in x.items()}
    return x


def _kalibrieren_gesperrt() -> dict:
    """Messen und speichern. Der Aufrufer haelt ``_lauf_lock``."""
    abschnitt = settings.get("embedding")
    modell = _modell_von(abschnitt)
    if not _eingerichtet(abschnitt):
        raise ValueError(_Meldung("kein_modell"))
    _phase_setzen("skala")
    t0 = time.monotonic()
    roh = _messen(modell)
    werte = skala_berechnen(
        [z["median"] for z in roh["profile"].values()],
        [z["max"] for z in roh["profile"].values()],
        [z["p99"] for z in roh["proben"].values()],
        [z["top1"] for z in roh["proben"].values()],
    )
    eintrag = {**werte, "quelle": "gemessen", "gemessen_am": _jetzt_iso(),
               "messung": _gerundet(roh)}
    settings.kalibrierung_setzen(_modell_schluessel(modell), eintrag)
    _skala_verwerfen()
    log.info("Skala fuer %s gemessen (%.1f s, %d Vektoren, %d Profile): Passung "
             "%.3f-%.3f, Stimmung ab %.3f, %.3f-%.3f", modell,
             time.monotonic() - t0, roh["vektoren"], len(roh["profile"]),
             werte["match_floor"], werte["match_ceil"], werte["mood_min"],
             werte["mood_floor"], werte["mood_ceil"])
    return eintrag


def kalibrieren() -> dict:
    """Skala fuer das eingestellte Modell messen und speichern (quelle
    "gemessen", mit Rohzahlen unter "messung"). Ueberschreibt auch eine
    fruehere Messung — das ist der Knopf "Skala neu messen".

    Wirft ValueError mit einer Meldung fuer die Oberflaeche, wenn es nicht
    geht. Waehrend eines Einbett-Laufs wird nicht gemessen: auf einer halb
    gefuellten Sammlung kaeme eine schiefe Skala heraus.
    """
    if not _lauf_lock.acquire(blocking=False):
        raise ValueError(_Meldung("laeuft_gerade"))
    try:
        _fortschritt_beginnen("skala")
        return _kalibrieren_gesperrt()
    finally:
        _fortschritt_beenden()
        _sperre_freigeben()


def _kalibrieren_falls_noetig() -> str | None:
    """Skala messen, wenn fuer das Modell keine gespeichert ist (Quelle
    "vorlaeufig"). bge-m3 behaelt seine Handwerte. Der Aufrufer haelt
    ``_lauf_lock``. Gibt einen Hinweis fuer den Laufstatus zurueck oder None.

    Gemessen wird erst, wenn mindestens neun Zehntel der Bibliothek
    eingebettet sind. Eine Skala aus einer halb gefuellten Sammlung waere
    schief und bliebe als "gemessen" stehen — automatisch ersetzt wird sie
    danach nicht mehr.
    """
    if _skala_intern()["quelle"] != "vorlaeufig":
        return None
    if not _eingerichtet(settings.get("embedding")):
        return None
    gesamt, eingebettet = _db_zaehlen()
    if gesamt == 0 or (gesamt - eingebettet) * 10 > gesamt:
        log.info("Skala noch nicht gemessen: erst %d von %d Eintraegen eingebettet",
                 eingebettet, gesamt)
        return None
    try:
        _kalibrieren_gesperrt()
    except ValueError as e:
        log.info("Skala nicht gemessen: %s", e)
        return _Meldung("skala_nicht_gemessen", fehler=e)
    except Exception as e:
        log.warning("Skala messen fehlgeschlagen: %s", e)
        return _Meldung("skala_fehlgeschlagen", fehler=e)
    return None


def _nachkalibrieren() -> None:
    """Fuer aehnlichkeiten_vorwaermen: messen, falls noetig und gerade kein
    Lauf aktiv ist. Wartet nie — laeuft einer, misst dessen Kette selbst."""
    try:
        if _skala_intern()["quelle"] != "vorlaeufig":
            return
    except Exception:
        return
    if not _lauf_lock.acquire(blocking=False):
        return
    try:
        _fortschritt_beginnen("skala")
        _kalibrieren_falls_noetig()
    except Exception as e:
        log.warning("Skala messen fehlgeschlagen: %s", e)
    finally:
        _fortschritt_beenden()
        _sperre_freigeben()


def profile_movie_similarity(user_id: int, movie_id: str) -> float | None:
    """Cosine similarity in [0, 1] between a user's profile vector and a
    movie's vector. Returns None if either vector is missing."""
    import numpy as np
    _, filme, profile = _stand()
    try:
        uv = profile.vektor(str(user_id))
        mv = filme.vektor(movie_id)
    except Exception as e:
        log.warning("profile_movie_similarity lookup failed: %s", e)
        return None
    if uv is None or mv is None or uv.size != mv.size:
        return None
    # Kosinus-Aehnlichkeit. Der Speicher liefert beide Vektoren normiert -
    # nomic-embed-text liefert sie so, andere Modelle muessen das nicht tun.
    # Ein Nullvektor bleibt Null und hat keine Richtung: dann wie bisher None.
    if not np.any(uv) or not np.any(mv):
        return None
    return max(0.0, min(1.0, float(np.dot(uv, mv))))
