"""CinematekAI — was jedes Jellyfin-Konto sehen darf.

Der Katalog (Movie, Series, Episode) stammt aus EINEM Konto, dem
Bibliothekskonto aus den Einstellungen (meist ein Admin). Ohne weitere
Pruefung bekaeme deshalb jede Person alles gezeigt, empfohlen und in den
Chat gelegt - auch Titel, die Jellyfin ihrem eigenen Konto sperrt:
Bibliotheken ohne Freigabe, Titel ueber der Altersgrenze (MaxParentalRating),
gesperrte Tags.

Jellyfin wendet genau diese Rechte selbst an, wenn man /Items mit
?userId=<konto> abfragt. Der Sync tut das ohnehin fuer jede Person:
sync_watch_state fragt alle Filme des Kontos ab (gesehene wie ungesehene),
sync_series_progress alle Serien. Die IDs aus diesen Antworten sind die
verlaesslichste Quelle und landen hier als "sichtbare Menge" je Person und
Typ (Tabellen SichtbarerTitel / SichtbarkeitStand).

Regeln:
  * Ohne gespeicherte Menge gilt kein Filter - wie bis 1.0.0. Das betrifft
    nur die Zeit zwischen Update und erstem Sync dieser Person.
  * Filme und Serien getrennt: ist nur eine der beiden Mengen gespeichert
    (etwa weil der Serien-Sync scheiterte), wird nur dieser Typ gefiltert.
  * Folgen gelten als sichtbar, wenn ihre Serie sichtbar ist. Eigene
    Freigaben einzelner Folgen kennt CinematekAI nicht.
  * Was bei gespeicherter Menge NICHT darin steht, ist verborgen - auch ein
    Titel, der erst nach dem letzten Abgleich in den Katalog kam. Lieber ein
    neuer Titel kurz unsichtbar als ein gesperrter kurz sichtbar. Damit neue
    Titel nicht fuer alle bis zum naechsten Login verborgen bleiben, frischt
    der Bibliotheks-Sync die Mengen aller Personen gleich mit auf
    (auffrischen).

Laden: ``laden()`` kostet zwei kleine Abfragen. Damit eine Anfrage, die durch
mehrere Module laeuft (Profil, Kandidaten, Kurator, Chat-Prompt), die Menge
trotzdem nur einmal holt, halten ``fuer_aufruf`` bzw. ``mit_sicht`` die Sicht
fuer die Dauer eines Aufrufs fest; ``laden()`` gibt innerhalb davon dieselbe
zurueck. Ausserhalb eines solchen Rahmens laedt jeder Aufruf frisch - das ist
nur langsamer, nie falsch.
"""
from __future__ import annotations

import functools
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import select, delete

from database import (get_session, Movie, Series, Episode, User, ChatPreload,
                      SichtbarerTitel, SichtbarkeitStand)

log = logging.getLogger("cinematekai.sichtbarkeit")

FILM = "movie"
SERIE = "series"
_TYPEN = (FILM, SERIE)
# Jellyfin-Typnamen fuer die Abfrage beim Auffrischen.
_JELLYFIN_TYP = {FILM: "Movie", SERIE: "Series"}

# SQLite begrenzt die Zahl der Platzhalter je Anweisung (aeltere Versionen auf
# 999). Geloescht wird deshalb in Stuecken, sonst scheitert der Abgleich genau
# dann, wenn einem Konto auf einen Schlag viele Titel entzogen werden.
_STUECK = 500


def _kanonisch(item_id) -> str:
    """Jellyfin schreibt IDs als 32 Hexzeichen klein ohne Bindestriche; der
    Bild-Proxy nimmt auch die GUID-Form mit Bindestrichen an. Fuer den
    Vergleich mit der gespeicherten Menge muessen beide gleich aussehen,
    sonst liesse sich ein gesperrtes Poster ueber die andere Schreibweise
    abrufen (bzw. ein erlaubtes nicht)."""
    return str(item_id or "").replace("-", "").lower()


class Sicht:
    """Die sichtbaren Titel EINER Person, einmal je Anfrage geladen.

    ``filme`` / ``serien``: Menge der erlaubten IDs, oder ``None`` = fuer
    diesen Typ ist (noch) nichts gespeichert, also kein Filter. ``aktiv`` ist
    falsch, wenn gar kein Filter greift - dann geben alle Methoden alles frei
    und ``verborgen()`` ist leer, die Ergebnisse bleiben also Stueck fuer
    Stueck dieselben wie vor 1.0.1.
    """

    __slots__ = ("user_id", "filme", "serien", "_verborgen")

    def __init__(self, user_id: Optional[int],
                 filme: Optional[frozenset] = None,
                 serien: Optional[frozenset] = None):
        self.user_id = user_id
        self.filme = filme
        self.serien = serien
        # (Filme, Serien, Folgen), die verborgen sind - erst bei Bedarf berechnet.
        self._verborgen: Optional[tuple[frozenset, frozenset, frozenset]] = None

    @property
    def aktiv(self) -> bool:
        return self.filme is not None or self.serien is not None

    def film(self, movie_id) -> bool:
        return self.filme is None or movie_id in self.filme

    def serie(self, series_id) -> bool:
        return self.serien is None or series_id in self.serien

    def eintrag(self, it: dict) -> bool:
        """Fuer die angereicherten Treffer aus recommendations._hydrate und
        alles, was daraus im Zwischenspeicher gelandet ist. Ohne
        ``media_type`` (Rangliste des Kurators) ist es ein Film."""
        if not isinstance(it, dict):
            return False
        mid = it.get("movie_id") or it.get("id")
        art = it.get("media_type")
        if art == "episode":
            return self.serie(it.get("series_id"))
        if art == SERIE:
            return self.serie(mid)
        return self.film(mid)

    def filtern(self, items: Iterable[dict]) -> list[dict]:
        items = list(items or [])
        if not self.aktiv:
            return items
        return [it for it in items if self.eintrag(it)]

    def sql(self, spalte, media_type: str):
        """SQL-Bedingung "ID ist fuer diese Person sichtbar" fuer eine
        Abfrage auf Movie bzw. Series - oder ``None``, wenn nichts zu filtern
        ist. Als Unterabfrage statt einer ID-Liste, damit die seitenweise
        Abfrage (limit/offset) und die Trefferzahl stimmen.

        ``None`` auch dann, wenn zwar eine Menge gespeichert, aber kein Titel
        dieses Typs verborgen ist (ein Konto, das alles sieht). Die Abfrage
        bleibt dann Zeichen fuer Zeichen die bisherige - mit der Unterabfrage
        koennte SQLite einen anderen Weg waehlen und gleichrangige Zeilen
        (gleiches Jahr, gleiche Zahl) in anderer Reihenfolge liefern.
        """
        menge = self.filme if media_type == FILM else self.serien
        if menge is None:
            return None
        filme, serien, _ = self._berechnet()
        if not (filme if media_type == FILM else serien):
            return None
        return spalte.in_(self._unterabfrage(media_type))

    def verborgen(self, folgen: bool = False) -> frozenset:
        """IDs aus dem Katalog, die diese Person NICHT sehen darf.

        Fuer die Vektorsuche (embeddings.search_by_text, ``exclude_ids``): sie
        vergroessert ihr Fenster um genau so viele Treffer, wie sie
        ueberspringt. Eine Suche liefert dadurch fuer ein eingeschraenktes
        Konto genauso viele Treffer wie vorher - als gaebe es die gesperrten
        Titel gar nicht. ``folgen=True`` nimmt die Folgen verborgener Serien
        dazu (nur fuer Suchen, die Folgen liefern koennen).

        Einmal je Objekt berechnet; ohne Filter sofort leer, ohne Abfrage.
        """
        if not self.aktiv:
            return frozenset()
        filme, serien, folgen_weg = self._berechnet()
        return filme | serien | folgen_weg if folgen else filme | serien

    def _berechnet(self) -> tuple[frozenset, frozenset, frozenset]:
        if self._verborgen is None:
            filme: set[str] = set()
            serien: set[str] = set()
            eps: set[str] = set()
            with get_session() as s:
                if self.filme is not None:
                    filme = set(s.exec(select(Movie.id).where(
                        Movie.id.not_in(self._unterabfrage(FILM)))).all())
                if self.serien is not None:
                    serien = set(s.exec(select(Series.id).where(
                        Series.id.not_in(self._unterabfrage(SERIE)))).all())
                    eps = set(s.exec(select(Episode.id).where(
                        Episode.series_id.not_in(self._unterabfrage(SERIE)))).all())
            self._verborgen = (frozenset(filme), frozenset(serien), frozenset(eps))
        return self._verborgen

    def _unterabfrage(self, media_type: str):
        return select(SichtbarerTitel.item_id).where(
            SichtbarerTitel.user_id == self.user_id,
            SichtbarerTitel.media_type == media_type)


def ohne_filter() -> Sicht:
    """Sicht ohne Einschraenkung - fuer Aufrufer ohne Person (Entwickler-
    skripte) oder dort, wo bewusst nicht gefiltert wird."""
    return Sicht(None)


# Die im laufenden Aufruf festgehaltene Sicht (siehe fuer_aufruf). Eine
# ContextVar statt eines Parameters, der durch jede Funktion gereicht wird:
# die Sicht wird in user_profile, curator und chat_engine gebraucht, deren
# Signaturen sonst alle mitwachsen muessten. Ein neuer Faden (Kurator im
# Hintergrund, Chat) erbt sie nicht und laedt selbst - so kann eine alte Sicht
# nie in eine spaetere Arbeit hineinwirken.
_im_aufruf: ContextVar[Optional[Sicht]] = ContextVar("cinematekai_sicht", default=None)


def laden(user_id: Optional[int]) -> Sicht:
    """Die Sicht einer Person: zwei kleine Abfragen, danach nur noch Mengen
    im Speicher. Innerhalb von ``fuer_aufruf`` fuer dieselbe Person ohne
    Abfrage die dort geladene."""
    if user_id is None:
        return ohne_filter()
    vorhanden = _im_aufruf.get()
    if vorhanden is not None and vorhanden.user_id == user_id:
        return vorhanden
    return _aus_db(user_id)


@contextmanager
def fuer_aufruf(user_id: Optional[int]):
    """Sicht einer Person fuer die Dauer eines Aufrufs festhalten.

    Nur um lesende Arbeit legen (Empfehlungen, Chat-Prompt, Detailseite):
    ein Sync innerhalb des Rahmens schriebe eine neue Menge, die der Rahmen
    nicht mehr saehe. Verschachtelt fuer dieselbe Person wird die aeussere
    weiterverwendet.
    """
    vorhanden = _im_aufruf.get()
    if user_id is None or (vorhanden is not None and vorhanden.user_id == user_id):
        yield laden(user_id)
        return
    sicht = _aus_db(user_id)
    token = _im_aufruf.set(sicht)
    try:
        yield sicht
    finally:
        _im_aufruf.reset(token)


def mit_sicht(fn):
    """Decorator fuer Einstiegsfunktionen, deren erstes Argument die
    Nutzer-ID ist (daily_for, by_mood, Chat-Prompt ...): alles darunter
    bekommt per ``laden()`` dieselbe Sicht, ohne erneute Abfrage."""
    @functools.wraps(fn)
    def huelle(user_id, *args, **kwargs):
        with fuer_aufruf(user_id):
            return fn(user_id, *args, **kwargs)
    return huelle


def _aus_db(user_id: int) -> Sicht:
    with get_session() as s:
        typen = {st.media_type for st in s.exec(
            select(SichtbarkeitStand).where(SichtbarkeitStand.user_id == user_id)).all()}
        if not typen:
            return Sicht(user_id)
        zeilen = s.exec(
            select(SichtbarerTitel.item_id, SichtbarerTitel.media_type)
            .where(SichtbarerTitel.user_id == user_id)).all()
    filme = frozenset(i for i, t in zeilen if t == FILM) if FILM in typen else None
    serien = frozenset(i for i, t in zeilen if t == SERIE) if SERIE in typen else None
    return Sicht(user_id, filme, serien)


def darf_sehen(user_id: int, item_id: str) -> bool:
    """Darf die Person diesen einen Titel sehen? Fuer Detailseiten, Bild-Proxy
    und Trailer.

    Gezielte Abfragen ueber den Primaerschluessel statt laden(): der
    Bild-Proxy laeuft fuer jedes Poster einer Liste einmal, die ganze Menge
    je Bild zu laden waere reine Verschwendung.

    Eine ID, die bei aktivem Filter weder sichtbar noch ein Film, eine Serie
    oder eine Folge des Katalogs ist, wird abgelehnt: worueber nichts bekannt
    ist, gibt es auch keinen Grund, es herauszugeben.
    """
    iid = _kanonisch(item_id)
    with get_session() as s:
        typen = {st.media_type for st in s.exec(
            select(SichtbarkeitStand).where(SichtbarkeitStand.user_id == user_id)).all()}
        if not typen:
            return True

        def sichtbar(i: str) -> bool:
            return s.exec(select(SichtbarerTitel.item_id).where(
                SichtbarerTitel.user_id == user_id,
                SichtbarerTitel.item_id == i)).first() is not None

        if sichtbar(iid):
            return True
        if s.get(Movie, iid) is not None:
            return FILM not in typen
        if s.get(Series, iid) is not None:
            return SERIE not in typen
        ep = s.get(Episode, iid)
        if ep is not None:
            return SERIE not in typen or sichtbar(ep.series_id)
        return False


def speichern(user_id: int, media_type: str, ids: Iterable[str]) -> dict:
    """Sichtbare Menge einer Person fuer einen Typ ersetzen.

    ``ids`` sind ALLE IDs, die Jellyfin fuer das Konto geliefert hat - auch
    solche, die (noch) nicht im Katalog stehen: kommt ein Titel spaeter
    hinzu, ist er dann schon richtig eingeordnet. Geschrieben wird nur der
    Unterschied, damit ein unveraenderter Abgleich (der Normalfall jede
    Nacht) die Tabelle nicht umschichtet. Was nicht mehr geliefert wird,
    faellt heraus - so verschwindet ein entzogener Titel beim naechsten Sync.

    Wird dabei ein Titel des Katalogs neu verborgen (Freigabe entzogen, oder
    der erste Abgleich nach dem Update findet gesperrte Titel), fallen die
    vorberechneten Chat-Antworten dieser Person weg: es ist freier Text, der
    sich nicht nachtraeglich filtern laesst, und er koennte genau diesen
    Titel nennen. Die Knoepfe fragen dann live, bis der naechste Vorlauf
    nachzieht. Empfehlungen im Zwischenspeicher bleiben stehen; die filtert
    das Ausliefern (siehe recommendations).
    """
    if media_type not in _TYPEN:
        raise ValueError(f"unbekannter Typ {media_type!r}")
    neu = {_kanonisch(i) for i in ids if i}
    neu.discard("")
    katalog_tabelle = Movie if media_type == FILM else Series
    with get_session() as s:
        alt = set(s.exec(select(SichtbarerTitel.item_id).where(
            SichtbarerTitel.user_id == user_id,
            SichtbarerTitel.media_type == media_type)).all())
        stand = s.get(SichtbarkeitStand, (user_id, media_type))
        katalog = set(s.exec(select(katalog_tabelle.id)).all())
        # Vorher galt ohne Stand gar kein Filter, also war nichts verborgen.
        vorher_verborgen = (katalog - alt) if stand is not None else set()
        neu_verborgen = (katalog - neu) - vorher_verborgen
        weg = sorted(alt - neu)
        dazu = sorted(neu - alt)
        for start in range(0, len(weg), _STUECK):
            s.exec(delete(SichtbarerTitel).where(
                SichtbarerTitel.user_id == user_id,
                SichtbarerTitel.media_type == media_type,
                SichtbarerTitel.item_id.in_(weg[start:start + _STUECK])))
        # "ON CONFLICT DO NOTHING" statt s.add: laufen zwei Abgleiche derselben
        # Person gleichzeitig (Anmeldung waehrend der naechtlichen Kette),
        # schreiben beide dieselben neuen Zeilen. Mit s.add scheiterte der
        # zweite am Primaerschluessel, obwohl das Ergebnis dasselbe ist.
        for start in range(0, len(dazu), _STUECK):
            s.exec(sqlite_insert(SichtbarerTitel).on_conflict_do_nothing(),
                   params=[{"user_id": user_id, "item_id": i, "media_type": media_type}
                           for i in dazu[start:start + _STUECK]])
        jetzt = datetime.utcnow()
        s.exec(sqlite_insert(SichtbarkeitStand)
               .values(user_id=user_id, media_type=media_type, anzahl=len(neu), synced_at=jetzt)
               .on_conflict_do_update(index_elements=["user_id", "media_type"],
                                      set_={"anzahl": len(neu), "synced_at": jetzt}))
        if neu_verborgen:
            s.exec(delete(ChatPreload).where(ChatPreload.user_id == user_id))
        s.commit()
    if weg or dazu:
        log.info("Sichtbarkeit user=%s %s: %d sichtbar (+%d -%d)",
                 user_id, media_type, len(neu), len(dazu), len(weg))
    if neu_verborgen:
        log.info("Sichtbarkeit user=%s %s: %d Titel des Katalogs neu verborgen, "
                 "vorberechnete Chat-Antworten verworfen",
                 user_id, media_type, len(neu_verborgen))
    return {"sichtbar": len(neu), "neu": len(dazu), "weg": len(weg),
            "neu_verborgen": len(neu_verborgen)}


def auffrischen(media_type: str) -> dict:
    """Mengen aller Personen fuer einen Typ neu von Jellyfin holen.

    Laeuft am Ende des Bibliotheks-Syncs. Ohne diesen Schritt waere ein Titel,
    den der Bibliotheks-Sync gerade aufgenommen hat, fuer alle unsichtbar, bis
    ihr eigener Abgleich laeuft (Anmeldung oder naechste Nacht) - nach dem
    Knopf "Bibliothek abgleichen" saehe also niemand den neuen Film. Fehler
    einzelner Konten werden nur protokolliert: die Person behaelt ihre
    bisherige Menge, und der Bibliotheks-Sync selbst ist gelungen.
    """
    from jellyfin import fetch_sichtbare_ids
    with get_session() as s:
        nutzer = [(u.id, u.jellyfin_user_id) for u in s.exec(select(User)).all()]
    ok = fehler = 0
    for uid, jf_id in nutzer:
        if not jf_id:
            continue
        try:
            speichern(uid, media_type, fetch_sichtbare_ids(jf_id, _JELLYFIN_TYP[media_type]))
            ok += 1
        except Exception as e:
            log.warning("Sichtbarkeit fuer user=%s (%s) nicht aufgefrischt: %s",
                        uid, media_type, e)
            fehler += 1
    return {"ok": ok, "fehler": fehler}


def entfernen(s, user_id: int) -> None:
    """Alle Sichtbarkeits-Zeilen einer Person loeschen, in der Sitzung ``s``
    des Aufrufers (Nutzer entfernen). Sonst erbte ein spaeter neu angelegtes
    Konto mit derselben internen ID eine fremde Menge."""
    s.exec(delete(SichtbarerTitel).where(SichtbarerTitel.user_id == user_id))
    s.exec(delete(SichtbarkeitStand).where(SichtbarkeitStand.user_id == user_id))
