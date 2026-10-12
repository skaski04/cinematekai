"""Sprache der Oberflaeche: Deutsch oder Englisch.

Die Oberflaeche uebersetzt ihre eigenen Texte selbst. Hier geht es um alles,
was das Backend an Text liefert und der Nutzer zu sehen bekommt:
Begruendungen ("Komoedie — dieses Genre steht in deinem Profil weit vorn"),
Hinweise der Suche, Fehlermeldungen und die Antworten der Sprachmodelle.

Woher die Sprache kommt:
  * Bei einer Anfrage aus dem Browser schickt das Frontend sie im Kopf
    ``X-Sprache`` mit (siehe frontend/src/lib/i18n.svelte.js). Die
    Middleware in main.py legt sie fuer die Dauer der Anfrage in eine
    ContextVar; ``aktuell()`` liest sie dort.
  * Hintergrundarbeit (Cron, Kurator, Chat-Faden) hat keine Anfrage. Dort
    wird die Sprache des jeweiligen Nutzers ausdruecklich uebergeben:
    ``fuer_nutzer(user)``. threading.Thread erbt die ContextVar NICHT.
  * Fehlt beides: die Vorgabe aus den Einstellungen (Allgemein -> Sprache).

Jedes Modul haelt seine Texte selbst, als Tabelle ``{"de": {...}, "en": {...}}``,
und holt sie mit ``waehle(tabelle, schluessel, **werte)``. So bleiben Text und
Verwendung beieinander, und kein zentrales Woerterbuch wird zum Engpass.
"""
from __future__ import annotations

import contextvars
import logging
from typing import Any, Mapping, Optional

log = logging.getLogger("cinematekai.texte")

SPRACHEN = ("de", "en")
STANDARD = "de"

_aktuell: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "cinematekai_sprache", default=None)


def normalisieren(code: Any) -> Optional[str]:
    """'en-GB', 'EN', 'en_US' -> 'en'; Unbekanntes -> None."""
    roh = str(code or "").strip().lower().replace("_", "-")
    if not roh:
        return None
    kurz = roh.split("-", 1)[0]
    return kurz if kurz in SPRACHEN else None


def vorgabe() -> str:
    """Sprache aus den Einstellungen (Admin -> Allgemein), sonst Deutsch."""
    try:
        import settings
        return normalisieren(settings.get("general").get("language")) or STANDARD
    except Exception:
        return STANDARD


def aktuell() -> str:
    """Sprache der laufenden Anfrage, sonst die Vorgabe."""
    return _aktuell.get() or vorgabe()


def setzen(code: Any) -> contextvars.Token:
    """Fuer die Middleware: Sprache dieser Anfrage festlegen."""
    return _aktuell.set(normalisieren(code))


def zuruecksetzen(token: contextvars.Token) -> None:
    _aktuell.reset(token)


def fuer_nutzer(user: Any) -> str:
    """Sprache eines Nutzers fuer Hintergrundarbeit (gespeicherte Wahl, sonst Vorgabe)."""
    return normalisieren(getattr(user, "language", None)) or vorgabe()


def waehle(tabelle: Mapping[str, Mapping[str, Any]], schluessel: str,
           sprache: Optional[str] = None, **werte: Any) -> str:
    """Text ``schluessel`` aus ``tabelle`` in der gewuenschten Sprache.

    Fehlt er in der Sprache, gilt der deutsche Text; fehlt er ganz, der
    Schluessel selbst (und eine Warnung im Log — so faellt eine Luecke auf,
    ohne dass die Seite bricht). Ein Eintrag darf auch eine Funktion sein,
    die ``werte`` bekommt (z. B. fuer Einzahl/Mehrzahl).
    """
    code = normalisieren(sprache) or aktuell()
    eintrag = (tabelle.get(code) or {}).get(schluessel)
    if eintrag is None:
        eintrag = (tabelle.get(STANDARD) or {}).get(schluessel)
    if eintrag is None:
        log.warning("Text fehlt: %r (%s)", schluessel, code)
        return schluessel
    if callable(eintrag):
        return str(eintrag(**werte))
    return str(eintrag).format(**werte) if werte else str(eintrag)


# Die Prompts der Sprachmodelle sind deutsch geschrieben (und so getestet).
# Fuer eine englische Oberflaeche bleiben sie, wie sie sind; nur die Antwort
# soll englisch sein. Eine angehaengte, unmissverstaendliche Anweisung reicht
# dafuer bei allen gaengigen Modellen — eine zweite Fassung jedes Prompts
# muesste man sonst doppelt pflegen und doppelt abnehmen.
_ANTWORTSPRACHE = {
    "en": ("\n\nWICHTIG: Antworte ausschliesslich auf ENGLISCH (in English), auch wenn "
           "diese Anweisungen und die Daten deutsch sind. Filmtitel bleiben, wie sie "
           "in den Daten stehen."),
}


def antwortsprache(sprache: Optional[str] = None) -> str:
    """Zusatz fuer einen Systemprompt, damit das Modell in der Sprache der
    Oberflaeche antwortet. Fuer Deutsch leer."""
    return _ANTWORTSPRACHE.get(normalisieren(sprache) or aktuell(), "")


# Genre-Namen kommen aus Jellyfin in der Sprache seiner Metadaten — bei dir
# deutsch. Fuer englische Texte des Backends (Begruendungen) werden die
# gaengigen uebersetzt; Unbekanntes bleibt, wie es ist.
_GENRE_EN = {
    "action": "Action", "abenteuer": "Adventure", "animation": "Animation",
    "komödie": "Comedy", "krimi": "Crime", "dokumentarfilm": "Documentary",
    "drama": "Drama", "familie": "Family", "fantasy": "Fantasy", "historie": "History",
    "horror": "Horror", "musik": "Music", "mystery": "Mystery", "liebesfilm": "Romance",
    "science fiction": "Science Fiction", "science-fiction": "Science Fiction",
    "tv-film": "TV Movie", "thriller": "Thriller",
    "kriegsfilm": "War", "western": "Western", "reality": "Reality", "spielshow": "Game Show",
    "miniserie": "Miniseries", "kinder": "Kids", "seifenoper": "Soap", "talkshow": "Talk",
    "news": "News", "sci-fi & fantasy": "Sci-Fi & Fantasy", "action & adventure": "Action & Adventure",
    "war & politics": "War & Politics",
}


def genre(name: str, sprache: Optional[str] = None) -> str:
    """Genre-Namen fuer die Ausgabe in der gewuenschten Sprache."""
    if (normalisieren(sprache) or aktuell()) == "en":
        return _GENRE_EN.get((name or "").strip().lower(), name)
    return name
