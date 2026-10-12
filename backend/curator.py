"""KI-Kurator fuer die Tagesempfehlung.

Die mechanische Auswahl (recommendations.daily_candidates) sortiert nach
Profil-Aehnlichkeit und TMDB-Schwarm-Signal. Stimmung, Ton und Tempo versteht
sie nicht. Der Kurator gibt dem Hauptmodell den Geschmack des Nutzers und die
ungesehenen Filme der Sammlung — bei ~440 Filmen passen *alle* in den Prompt —
und laesst es waehlen.

Sicherheitsnetze:
  * Das Modell sieht nur Nummern aus der Liste; jede Antwort wird dagegen
    geprueft. Erfundene oder doppelte Nummern fallen raus.
  * Ist die Antwort unbrauchbar oder der Dienst weg, bleibt die mechanische
    Auswahl stehen — die Startseite zeigt nie "nichts".
  * Das Kontextfenster des Hauptmodells (Einstellung ``llm.context_tokens``,
    z. B. 32K beim ArliAI-Core-Tarif; Denk-Tokens zaehlen mit) wird vorab
    eingehalten: Handlungstexte nur fuer die aussichtsreichsten Kandidaten,
    bei grossen Sammlungen fallen die schwaechsten ganz weg. Gelesen wird es
    bei jedem Lauf, weil sich das Modell in den Einstellungen aendern kann.
  * Der Kurator laeuft nur mit einem Hauptmodell ab 16K Kontext
    (llm_client.grosser_kontext) und ohne Rueckfall: das Rueckfallmodell ist
    meist ein kleines lokales (bisher Hermes3 mit 8K), das den Prompt nicht
    fasst.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta

import httpx
from sqlmodel import select

import llm_client
import texte
from database import (get_session, Movie, Series, WatchState, SeriesProgress,
                      NotInterested)

log = logging.getLogger("cinematekai.curator")

# Platz fuer Nachdenken + Antwort. Denk-Modelle brauchen reichlich: hoechstens
# RESPONSE_BUDGET_MAX, aber nie mehr als das halbe Kontextfenster.
RESPONSE_BUDGET_MAX = 14000


def response_budget(kontext_tokens: int) -> int:
    """Antwortbudget zum Kontextfenster. Eine Funktion statt einer Konstante,
    weil das Fenster jetzt aus den Einstellungen kommt und sich zur Laufzeit
    aendern kann."""
    return min(RESPONSE_BUDGET_MAX, kontext_tokens // 2)


MAX_FAVORITES = 150
MAX_PLAYED_NO_FAV = 100
PLOT_CHARS = 160
# Handlungstext nur fuer die vorderen Kandidaten. Gemessen 2026-09-15 mit
# DeepSeek-V4-Flash: 165 Handlungen (~21K Tokens) -> 342 s. ArliAI bricht
# Anfragen nach ~480 s mit 502 ab (bei GLM-4.7 beobachtet) — das war zu knapp.
MAX_PLOTS = 50
# Hoechstens so viele Kandidaten. Ein Konto mit kaum Seh-Historie hatte 439 im
# Prompt; das Modell hat daraufhin sein ganzes Antwortbudget mit Nachdenken verbraucht
# und nach 398 s nichts geliefert. Die Liste ist nach Passung sortiert — was
# hinten wegfaellt, waere ohnehin kaum gewaehlt worden.
MAX_CANDIDATES = 150
# Gemessen: 3060 Zeichen deutscher Prompt = 1088 Tokens, also ~2,8 Zeichen/Token.
CHARS_PER_TOKEN = 2.7
RANKING_SIZE = 10

# Denklaenge: ArliAI reicht "reasoning_effort" an DeepSeek-V4-Flash durch.
# Gemessen an einem Prompt mit ~8,9K Tokens: Standard 173 s / 8,3K Antwort-
# Tokens, "low" 107 s / 3,4K — mit demselben Film des Tages. Erst volle
# Denkzeit, bei Fehlschlag (leere Antwort, 502, Zeitueberschreitung) einmal kurz.
# Anbieter, die das Feld nicht kennen, antworten mit 400; llm_client merkt
# sich das je Modell und laesst es danach weg. An Ollama und Anthropic geht
# es gar nicht erst (deren Weg in llm_client nimmt kein ``extra``).
REASONING_LOW = {"reasoning_effort": "low"}

_WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]

SYSTEM = (
    "Du bist der Filmkurator von CinematekAI fuer eine private Filmsammlung. "
    "Du kennst Filme gut — Stimmung, Ton, Tempo, Humor, Haerte — und denkst wie "
    "ein Freund mit gutem Geschmack, nicht wie ein Algorithmus. Du waehlst "
    "ausschliesslich aus der nummerierten Kandidatenliste. Antworte nur mit "
    "gueltigem JSON, ohne Text davor oder danach."
)

INSTRUCTIONS = """AUFGABE
1. film_des_tages: der eine Film, der heute am besten passt. Nicht einfach der aehnlichste Titel, sondern einer, auf den sich der Nutzer wirklich freuen wuerde.
2. thematisch: drei weitere Filme, jeweils mit anderer Stimmung als der Film des Tages und untereinander.
3. ausreisser: ein Film ausserhalb seiner ueblichen Genres, der trotzdem zu seinem Geschmack passen koennte.
4. rangliste: zehn weitere Filme in Reihenfolge, fuer den Knopf "Neuer Vorschlag".
Jeder Film hoechstens einmal. Kein Film aus "Film des Tages der letzten Tage".
Gib zu jeder Nummer den exakten Titel aus der Liste an; die Begruendung muss sich auf genau diesen Titel beziehen.
Begruendungen: je ein bis zwei deutsche Saetze, konkret ueber Stimmung und Ton, gern mit Bezug auf einen seiner Lieblingsfilme. Sprich ihn mit "du" an. Keine Floskeln, keine Nummern im Text.
Bei wenig Sehhistorie: waehle breit beliebte, gut bewertete Filme mit Abwechslung.

JSON-Format:
{"film_des_tages": {"nr": 12, "titel": "...", "grund": "..."},
 "thematisch": [{"nr": 3, "titel": "...", "grund": "..."}, {"nr": 7, "titel": "...", "grund": "..."}, {"nr": 21, "titel": "...", "grund": "..."}],
 "ausreisser": {"nr": 40, "titel": "...", "grund": "..."},
 "rangliste": [{"nr": 5, "titel": "...", "grund": "..."}, {"nr": 9, "titel": "...", "grund": "..."}]}"""


def system_prompt(sprache: str | None = None) -> str:
    """Systemprompt des Kurators samt Hinweis auf die Antwortsprache.

    Der Prompt bleibt deutsch (so getestet); fuer eine englische Oberflaeche
    haengt texte.antwortsprache() die Anweisung an, englisch zu antworten.
    Auch missing.py nimmt diesen Prompt."""
    return SYSTEM + texte.antwortsprache(sprache)


def instructions(sprache: str | None = None) -> str:
    """Aufgabentext. Fuer Englisch faellt das Wort "deutsche" weg, damit die
    Aufgabe der angehaengten Antwortsprache nicht widerspricht."""
    if (texte.normalisieren(sprache) or texte.aktuell()) == "de":
        return INSTRUCTIONS
    return INSTRUCTIONS.replace("deutsche Saetze", "Saetze")


def _tok(text: str, chars_per_token: float) -> int:
    return int(len(text) / chars_per_token) + 1


def _label(m) -> str:
    return f"{m.name} ({m.year})" if m.year else m.name


# ---------------------------------------------------------------------------
# Geschmack
# ---------------------------------------------------------------------------

def _user_taste(user_id: int) -> dict:
    now = datetime.utcnow()
    with get_session() as s:
        ws_rows = s.exec(select(WatchState).where(WatchState.user_id == user_id)).all()
        sp_rows = s.exec(select(SeriesProgress).where(SeriesProgress.user_id == user_id)).all()
        movies = {m.id: m for m in s.exec(select(Movie)).all()}
        series = {x.id: x for x in s.exec(select(Series)).all()}
        ni_rows = s.exec(select(NotInterested).where(NotInterested.user_id == user_id)).all()

    fav, played, abandoned, low, recent = [], [], [], [], []
    for ws in ws_rows:
        m = movies.get(ws.movie_id)
        if not m:
            continue
        label = _label(m)
        if ws.user_rating is not None:
            label += f" ★{ws.user_rating / 2:.0f}/5"
        ts = ws.last_played or ws.updated_at or datetime.min
        if ws.user_rating is not None and ws.user_rating < 4:
            low.append((ts, label))
        elif ws.is_favorite:
            fav.append((ts, label))
        elif ws.played:
            played.append((ts, label))
        elif (ws.playback_position_ticks > 0 and ws.last_played is not None
              and (now - ws.last_played).days > 30):
            abandoned.append((ts, label))
        if ws.played and ws.last_played:
            recent.append((ws.last_played, _label(m)))

    ser_fav, ser_done, ser_watching = [], [], []
    for sp in sp_rows:
        ser = series.get(sp.series_id)
        if not ser:
            continue
        ts = sp.last_played or sp.updated_at or datetime.min
        if sp.is_favorite:
            ser_fav.append((ts, ser.name))
        elif sp.state == "completed":
            ser_done.append((ts, ser.name))
        elif sp.state == "watching":
            ser_watching.append((ts, ser.name))

    dismissed = [_label(movies[n.item_id]) for n in ni_rows
                 if n.until > now and n.item_id in movies]

    def _newest(rows, limit):
        return [label for _, label in sorted(rows, key=lambda t: t[0], reverse=True)[:limit]]

    return {
        "favorites": _newest(fav, MAX_FAVORITES),
        "favorite_total": len(fav),
        "played_no_fav": _newest(played, MAX_PLAYED_NO_FAV),
        "abandoned": _newest(abandoned, 30),
        "low_rated": _newest(low, 50),
        "recent": _newest(recent, 10),
        "series_fav": _newest(ser_fav, 20),
        "series_done": _newest(ser_done, 20),
        "series_watching": _newest(ser_watching, 10),
        "dismissed": dismissed[:30],
    }


def _taste_block(name: str, taste: dict, recent_fod: list[str]) -> str:
    today = datetime.now()
    parts = [f"GESCHMACK VON {name.upper()}"]

    def add(title: str, items: list[str], note: str = ""):
        if items:
            parts.append(f"{title}{note}: " + "; ".join(items))

    add("Lieblingsfilme (Herz = mag ich, neueste zuerst)", taste["favorites"],
        f" [{taste['favorite_total']} insgesamt]" if taste["favorite_total"] > len(taste["favorites"]) else "")
    add("Gesehen ohne Herz (meist 'war okay'; das Herz wird aber auch mal vergessen)",
        taste["played_no_fav"])
    add("Angefangen und liegen gelassen", taste["abandoned"])
    add("Schlecht bewertet", taste["low_rated"])
    add("Lieblingsserien", taste["series_fav"])
    add("Komplett gesehene Serien", taste["series_done"])
    add("Serien, die er gerade schaut", taste["series_watching"])
    add("Zuletzt gesehene Filme (neueste zuerst)", taste["recent"])
    add("Bewusst ausgeblendet ('nicht interessiert')", taste["dismissed"])
    if len(parts) == 1:
        parts.append("Noch keine Sehhistorie.")
    add("Film des Tages der letzten Tage (nicht wiederholen)", recent_fod)
    parts.append(f"Heute: {_WEEKDAYS[today.weekday()]}, {today:%d.%m.%Y}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Kandidaten
# ---------------------------------------------------------------------------

def _candidate_pool(user_id: int, profile: dict, hidden: set[str]) -> list[dict]:
    """Alle ungesehenen Filme: zuerst in der Reihenfolge der mechanischen
    Auswahl (Profil + TMDB), danach der Rest nach Community-Wertung."""
    import recommendations as R
    ranked = [c for c in R.daily_candidates(user_id, profile, hidden)
              if c.get("media_type") == "movie"]
    have = {c["movie_id"] for c in ranked}
    seen = profile["seen_ids"]
    with get_session() as s:
        rest = [m for m in s.exec(select(Movie)).all()
                if m.id not in seen and m.id not in hidden and m.id not in have]
    rest.sort(key=lambda m: -(m.community_rating or 0))
    return ranked + R._hydrate([{"movie_id": m.id, "score": 0.0} for m in rest])


def _cand_line(nr: int, c: dict) -> str:
    bits = [f"{nr} | {c['name']}" + (f" ({c['year']})" if c.get("year") else "")]
    genres = ", ".join((c.get("genres") or [])[:3])
    if genres:
        bits.append(genres)
    if c.get("directors"):
        bits.append("Regie: " + c["directors"][0])
    if c.get("runtime_min"):
        bits.append(f"{c['runtime_min']} min")
    if c.get("rating"):
        bits.append(f"TMDB {c['rating']:.1f}")
    if c.get("tmdb_because"):
        bits.append("Fans von: " + ", ".join(f'"{b}"' for b in c["tmdb_because"][:2]))
    return " | ".join(bits)


def _build_messages(name: str, taste: dict, pool: list[dict], recent_fod: list[str],
                    chars_per_token: float,
                    kontext_tokens: int | None = None,
                    sprache: str | None = None) -> tuple[list[dict], dict, dict]:
    if kontext_tokens is None:
        kontext_tokens = llm_client.kontext_tokens()
    system = system_prompt(sprache)
    aufgabe = instructions(sprache)
    head = _taste_block(name, taste, recent_fod)
    legend = ("UNGESEHENE FILME IN DER SAMMLUNG (Nr | Titel | Genres | Regie | Laenge | "
              "TMDB-Wertung | 'Fans von' = Titel aus seiner Historie, deren Fans "
              "diesen Film auf TMDB oft auch moegen):")
    fixed = sum(_tok(t, chars_per_token) for t in (system, head, legend, aufgabe)) + 200
    budget = kontext_tokens - response_budget(kontext_tokens) - fixed

    chosen: list[tuple[int, dict, str]] = []
    used = 0
    for nr, c in enumerate(pool, start=1):
        if nr > MAX_CANDIDATES:
            break
        line = _cand_line(nr, c)
        t = _tok(line, chars_per_token) + 1
        if used + t > budget:
            break
        chosen.append((nr, c, line))
        used += t

    lines, plots = [], 0
    for nr, c, line in chosen:
        plot = (c.get("overview") or "").replace("\n", " ").strip()[:PLOT_CHARS]
        if plot and plots < MAX_PLOTS:
            extra = f"\n    {plot}"
            t = _tok(extra, chars_per_token)
            if used + t <= budget:
                line += extra
                used += t
                plots += 1
        lines.append(line)

    user = f"{head}\n\n{legend}\n" + "\n".join(lines) + f"\n\n{aufgabe}"
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    by_num = {str(nr): c for nr, c, _ in chosen}
    prompt_est = fixed + used
    stats = {"pool": len(pool), "in_prompt": len(chosen), "with_plot": plots,
             "prompt_tokens_est": prompt_est}
    return messages, by_num, stats


def complete_with_retry(messages: list[dict], max_tokens: int,
                        first: dict | None = None, aufgabe: str = "kurator") -> str:
    """Anfrage ans Hauptmodell (standardmaessig mit voller Denkzeit), bei
    Fehlschlag einmal mit kurzer. ``first`` setzt Parameter fuer den ersten Versuch. Ein 400
    (Prompt zu lang) wird weitergereicht — das regelt der Aufrufer."""
    try:
        return llm_client.complete(messages, temperature=0.7, max_tokens=max_tokens,
                                   timeout=900.0,
                                   allow_fallback=llm_client.rueckfall_grosser_kontext(),
                                   extra=first, aufgabe=aufgabe)
    except httpx.HTTPStatusError as e:
        if e.response is not None and e.response.status_code == 400:
            raise
        log.warning("Kurator: %s — zweiter Versuch mit kurzer Denkphase", e)
    except (ValueError, httpx.TimeoutException) as e:
        log.warning("Kurator: %s — zweiter Versuch mit kurzer Denkphase", e)
    return llm_client.complete(messages, temperature=0.7, max_tokens=max_tokens,
                               timeout=900.0,
                               allow_fallback=llm_client.rueckfall_grosser_kontext(),
                               extra=REASONING_LOW, aufgabe=aufgabe)


def _title_key(s: str) -> str:
    s = re.sub(r"\(\d{4}\)", " ", (s or "").lower())
    return " ".join(re.sub(r"[^\w]+", " ", s).split())


def _same_title(given: str, actual: str) -> bool:
    g, a = _title_key(given), _title_key(actual)
    return bool(g and a) and (g == a or g in a or a in g)


def resolve_pick(entry: dict, by_num: dict[str, dict], name_key: str) -> dict | None:
    """Nummer UND Titel muessen zusammenpassen.

    Anlass: Bei "Fehlt in deiner Sammlung" lieferte das Modell Nummern, deren
    Begruendungen zu ganz anderen Filmen gehoerten ("Justice League" mit einem
    Text ueber "Der Pate"). Passt die Nummer nicht zum Titel, wird der Titel in
    der Liste gesucht; ohne Titel oder ohne Treffer zaehlt der Eintrag nicht.
    """
    title = str(entry.get("titel") or "").strip()
    if not title:
        return None
    c = by_num.get(str(entry.get("nr")).strip())
    if c and _same_title(title, c.get(name_key, "")):
        return c
    matches = [x for x in by_num.values() if _same_title(title, x.get(name_key, ""))]
    return matches[0] if len(matches) == 1 else None


def _parse_json(text: str) -> dict | None:
    body = re.sub(r"^```(?:json)?|```$", "", (text or "").strip()).strip()
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(body[start:end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# Einstieg
# ---------------------------------------------------------------------------

def curate(user_id: int, profile: dict, hidden: set[str],
           recent_fod_ids: set[str], sprache: str | None = None) -> dict | None:
    """Kuratierte Tagesauswahl oder ``None``, wenn nichts Brauchbares kam.

    ``sprache``: Sprache der Begruendungen ("de"/"en"). Laeuft der Kurator im
    Hintergrund, MUSS der Aufrufer sie angeben (eine ContextVar erbt kein
    Thread); ohne Angabe gilt texte.aktuell()."""
    if not llm_client.grosser_kontext():
        return None
    sprache = texte.normalisieren(sprache) or texte.aktuell()
    # Einmal je Lauf lesen: Prompt-Budget und Antwortlaenge muessen zum
    # selben Fenster passen, auch wenn jemand waehrenddessen umstellt.
    kontext = llm_client.kontext_tokens()
    from database import User
    with get_session() as s:
        user = s.get(User, user_id)
        recent_fod = [_label(m) for mid in recent_fod_ids if (m := s.get(Movie, mid))]
    # Neutral statt des Anzeigenamens: der Prompt geht an einen externen
    # Anbieter, und manche Anbieter verbieten personenbezogene Daten oder
    # verwenden Ein-/Ausgaben zum Training. Fuer die Auswahl spielt der Name
    # keine Rolle.
    name = "Nutzer"

    taste = _user_taste(user_id)
    pool = _candidate_pool(user_id, profile, hidden)
    if not pool:
        return None

    text, by_num, stats = None, {}, {}
    # Zweiter Versuch mit vorsichtigerer Token-Schaetzung, falls der Anbieter
    # den Prompt als zu lang ablehnt.
    for chars_per_token in (CHARS_PER_TOKEN, 2.0):
        messages, by_num, stats = _build_messages(name, taste, pool, recent_fod, chars_per_token,
                                                  kontext, sprache)
        max_tokens = max(2000, kontext - stats["prompt_tokens_est"] - 500)
        t0 = time.time()
        try:
            text = complete_with_retry(messages, max_tokens)
            stats["seconds"] = round(time.time() - t0, 1)
            break
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400 and chars_per_token > 2.0:
                log.warning("Kurator: Anfrage abgelehnt (%s) — versuche kuerzeren Prompt",
                            e.response.text[:200])
                continue
            raise
    if not text:
        return None

    import ki_protokoll
    data = _parse_json(text)
    if not data:
        log.warning("Kurator: keine lesbare JSON-Antwort: %s", text[:300])
        ki_protokoll.auswertung("kurator", False, "kein_json")
        return None

    used: set[str] = set()
    invalid = 0

    def pick(entry) -> dict | None:
        nonlocal invalid
        if not isinstance(entry, dict):
            invalid += 1
            return None
        c = resolve_pick(entry, by_num, "name")
        if not c or c["movie_id"] in used or c["movie_id"] in recent_fod_ids:
            invalid += 1
            return None
        used.add(c["movie_id"])
        return {**c, "curator_reason": (entry.get("grund") or "").strip() or None}

    fod = pick(data.get("film_des_tages"))
    if not fod:
        log.warning("Kurator: Film des Tages ungueltig: %s", data.get("film_des_tages"))
        ki_protokoll.auswertung("kurator", False, "film_des_tages_ungueltig")
        return None
    thematic = [x for x in (pick(e) for e in (data.get("thematisch") or [])[:3]) if x]
    aus = data.get("ausreisser")
    exploration = []
    for e in (aus if isinstance(aus, list) else [aus])[:2]:
        x = pick(e) if e else None
        if x:
            x["exploration_reason"] = x.get("curator_reason")
            exploration.append(x)
    ranking = []
    for e in (data.get("rangliste") or [])[:RANKING_SIZE + 2]:
        x = pick(e)
        if x:
            ranking.append({"movie_id": x["movie_id"], "grund": x.get("curator_reason")})

    stats.update({"invalid_picks": invalid, "ranking": len(ranking)})
    log.info("Kurator User %s: %s (%s)", user_id, fod["name"], stats)
    ki_protokoll.auswertung("kurator", True)
    return {"film_of_day": fod, "thematic": thematic, "exploration": exploration,
            "ranking": ranking, "model": llm_client.active_model(), "stats": stats}
