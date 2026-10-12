"""KI-Protokoll: jeder Aufruf eines Sprachmodells, dauerhaft und auswertbar.

Anlass: Ob ein Sprachmodell auf Dauer traegt - gerade ein Hauptmodell mit
Rueckfall -, soll ein Review an Zahlen zeigen statt am Eindruck: Wie oft ging
etwas schief, wie oft musste der Rueckfall einspringen, wie lange dauerten
Chat und Kurator?

Erfasst wird in llm_client (complete und stream), je Versuch eine Zeile —
ein Kurator-Lauf mit zweitem Versuch und Rueckfall ergibt also drei Zeilen.
Das Erfassen darf eine Anfrage nie stoeren: jeder Fehler hier wird nur
geloggt und verschluckt.
"""
from __future__ import annotations

import logging
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import httpx
from sqlmodel import delete, select

from database import LlmAufruf, get_session

log = logging.getLogger("cinematekai.ki_protokoll")

AUFBEWAHRUNG_TAGE = 180
_TEXT_MAX = 300


def fehlerart(fehler: BaseException | str | None) -> str:
    """Kurze, gruppierbare Art eines Fehlers."""
    if fehler is None:
        return ""
    if isinstance(fehler, str):
        return fehler
    if isinstance(fehler, httpx.TimeoutException) or isinstance(fehler, TimeoutError):
        # TimeoutError kommt auch, wenn kein Anfrageplatz frei wurde.
        text = str(fehler).lower()
        return "kein_anfrageplatz" if "anfrageplatz" in text else "zeitueberschreitung"
    if isinstance(fehler, httpx.HTTPStatusError) and fehler.response is not None:
        return f"http_{fehler.response.status_code}"
    if isinstance(fehler, httpx.TransportError):
        return "verbindung"
    name = type(fehler).__name__
    if name == "KeinModell":
        return "kein_modell"
    if isinstance(fehler, ValueError) and "leer" in str(fehler).lower():
        return "leere_antwort"
    return name.lower()


def erfassen(*, aufgabe: str, rolle: str, anbieter: str, modell: str, ok: bool,
             dauer_s: float, fehler: BaseException | str | None = None,
             erstes_wort_s: float | None = None, tokens_ein: int | None = None,
             tokens_aus: int | None = None, schluessel: str = "") -> None:
    """Eine Zeile schreiben. Wirft nie."""
    try:
        text = "" if fehler is None or isinstance(fehler, str) else str(fehler)
        if schluessel:
            text = text.replace(schluessel, "•••")
        zeile = LlmAufruf(
            aufgabe=aufgabe or "sonstiges", rolle=rolle, anbieter=anbieter or "",
            modell=modell or "", ok=bool(ok), fehlerart=fehlerart(fehler) if not ok else "",
            fehlertext=text.replace("\n", " ")[:_TEXT_MAX],
            dauer_ms=int(max(0.0, dauer_s) * 1000),
            erstes_wort_ms=int(erstes_wort_s * 1000) if erstes_wort_s is not None else None,
            tokens_ein=tokens_ein, tokens_aus=tokens_aus)
        with get_session() as s:
            s.add(zeile)
            s.commit()
    except Exception as e:  # pragma: no cover — darf den Aufruf nie stoeren
        log.warning("KI-Protokoll: Zeile nicht geschrieben: %s", e)


def auswertung(aufgabe: str, brauchbar: bool, grund: str = "") -> None:
    """War die Antwort fuer ihren Zweck brauchbar? Eigene Zeile (Rolle
    "auswertung"), gebucht auf das Modell, das zuletzt in diesem Faden
    geantwortet hat.

    Ein Modell kann technisch antworten und trotzdem nichts Verwertbares
    liefern — kein gueltiges JSON, ein erfundener Film, eine Begruendung, die
    die Pruefung nicht besteht. Fuer ein Review ist das genauso ein Aussetzer
    wie eine Zeitueberschreitung, nur eine andere Sorte.
    """
    try:
        import llm_client
        anbieter, modell = llm_client.zuletzt_genutzt()
    except Exception:
        anbieter, modell = "", ""
    erfassen(aufgabe=aufgabe, rolle="auswertung", anbieter=anbieter, modell=modell,
             ok=brauchbar, dauer_s=0.0, fehler=None if brauchbar else (grund or "unbrauchbar"))


def aufraeumen(tage: int = AUFBEWAHRUNG_TAGE) -> int:
    """Zeilen aelter als ``tage`` loeschen; gibt die Anzahl zurueck."""
    grenze = datetime.utcnow() - timedelta(days=tage)
    with get_session() as s:
        alt = s.exec(select(LlmAufruf.id).where(LlmAufruf.zeit < grenze)).all()
        if alt:
            s.exec(delete(LlmAufruf).where(LlmAufruf.zeit < grenze))
            s.commit()
    return len(alt)


def _sekunden(ms_werte: list[int], anteil: float) -> float | None:
    if not ms_werte:
        return None
    werte = sorted(ms_werte)
    if anteil == 0.5:
        return round(statistics.median(werte) / 1000, 1)
    i = min(len(werte) - 1, max(0, int(round(anteil * (len(werte) - 1)))))
    return round(werte[i] / 1000, 1)


def zusammenfassung(tage: int = 30) -> dict:
    """Auswertung der letzten ``tage`` Tage fuer Oberflaeche und Review."""
    tage = max(1, min(int(tage), AUFBEWAHRUNG_TAGE))
    seit = datetime.utcnow() - timedelta(days=tage)
    with get_session() as s:
        zeilen = s.exec(select(LlmAufruf).where(LlmAufruf.zeit >= seit)
                        .order_by(LlmAufruf.zeit)).all()

    gruppen: dict[tuple, list[LlmAufruf]] = defaultdict(list)
    for z in zeilen:
        gruppen[(z.aufgabe, z.rolle, z.modell)].append(z)
    je_aufgabe = []
    # Je Aufgabe: erst die Anfragen (Haupt, Rueckfall), dann die Bewertung der
    # Antwort, zuletzt Tests — so steht in der Tabelle zusammen, was zusammengehoert.
    reihe = {"haupt": 0, "rueckfall": 1, "auswertung": 2, "test": 3}
    for (aufgabe, rolle, modell), liste in sorted(
            gruppen.items(), key=lambda kv: (kv[0][0], reihe.get(kv[0][1], 9), kv[0][2])):
        gut = [z for z in liste if z.ok]
        je_aufgabe.append({
            "aufgabe": aufgabe, "rolle": rolle, "modell": modell,
            "anzahl": len(liste), "ok": len(gut), "fehler": len(liste) - len(gut),
            "quote": round(100 * len(gut) / len(liste)) if liste else None,
            "dauer_median_s": _sekunden([z.dauer_ms for z in gut], 0.5),
            "dauer_p90_s": _sekunden([z.dauer_ms for z in gut], 0.9),
            "dauer_max_s": _sekunden([z.dauer_ms for z in gut], 1.0),
            "erstes_wort_median_s": _sekunden(
                [z.erstes_wort_ms for z in gut if z.erstes_wort_ms is not None], 0.5),
        })

    # Aufrufe = Anfragen an ein Modell; die Bewertungen der Antworten
    # (Rolle "auswertung") zaehlen getrennt als "unbrauchbar".
    aufrufe = [z for z in zeilen if z.rolle != "auswertung"]
    bewertet = [z for z in zeilen if z.rolle == "auswertung"]

    je_tag: dict[str, dict] = {}
    for z in zeilen:
        tag = z.zeit.date().isoformat()
        e = je_tag.setdefault(tag, {"datum": tag, "anzahl": 0, "fehler": 0, "rueckfall": 0,
                                    "unbrauchbar": 0})
        if z.rolle == "auswertung":
            e["unbrauchbar"] += 0 if z.ok else 1
            continue
        e["anzahl"] += 1
        e["fehler"] += 0 if z.ok else 1
        e["rueckfall"] += 1 if z.rolle == "rueckfall" else 0

    fehler = [z for z in zeilen if not z.ok]
    arten = Counter((z.modell, z.fehlerart) for z in fehler)
    return {
        "tage": tage,
        "seit": seit.isoformat() + "Z",
        "aufrufe": len(aufrufe),
        "fehler": sum(1 for z in aufrufe if not z.ok),
        "rueckfall": sum(1 for z in aufrufe if z.rolle == "rueckfall"),
        "unbrauchbar": sum(1 for z in bewertet if not z.ok),
        "je_aufgabe": je_aufgabe,
        "je_tag": list(je_tag.values()),
        "fehlerarten": [{"modell": m, "fehlerart": a, "anzahl": n}
                        for (m, a), n in arten.most_common()],
        "letzte_fehler": [{
            "zeit": z.zeit.isoformat() + "Z", "aufgabe": z.aufgabe, "rolle": z.rolle,
            "modell": z.modell, "fehlerart": z.fehlerart, "fehlertext": z.fehlertext,
            "dauer_s": round(z.dauer_ms / 1000, 1),
        } for z in reversed(fehler[-25:])],
    }
