"""„Fehlt in deiner Sammlung" — passende Filme, die nicht in Jellyfin liegen.

Quelle sind die TMDB-Listen „Wer das mochte, mochte auch" zu den Titeln mit
Signal (tmdb_client.collaborative_scores, Teil ``external``). Das Modell
erfindet also keine Filme; es waehlt und begruendet nur aus echten
TMDB-Treffern. Hinzufuegen macht der Nutzer selbst ueber Seerr — hier wird
nichts angefordert. Die Seerr-Adresse steht in den Einstellungen (Abschnitt
``seerr``); ist keine eingetragen, gibt es keinen Link (``seerr_url: None``).

Zwischengespeichert eine Woche je Nutzer; beim Ausliefern fallen Titel weg,
die inzwischen in der Sammlung sind, und der Seerr-Link wird frisch gebaut.

Kuratiert wird nur mit einem Hauptmodell ab 16K Kontext
(llm_client.grosser_kontext); sonst bleibt die mechanische Reihenfolge der
TMDB-Treffer.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta

from sqlmodel import select

import llm_client
import settings
import texte
from database import get_session, Movie, RecommendationCache
from tmdb_client import collaborative_scores, poster_url

log = logging.getLogger("cinematekai.missing")

CACHE_DAYS = 7
# So viele TMDB-Treffer sieht das Modell. Mit 45 Treffern und dem vollen
# Geschmacksblock (150 Favoriten) dauerte ein Lauf 399 s und lieferte
# vertauschte Begruendungen.
POOL_SIZE = 30
TASTE_FAVORITES = 60
TASTE_PLAYED = 30
RESULT_SIZE = 8
MIN_VOTES = 150         # sehr unbekannte Titel mit Zufallswertung aussortieren

# Texte, die der Nutzer zu sehen bekommt (Rueckfall-Begruendungen, wenn das
# Modell keine geschrieben hat). Die Begruendungen des Modells kommen in der
# Sprache der Anfrage selbst (curator.system_prompt haengt sie an).
_T = {
    "de": {
        "grund_fans": 'Fans von „{titel}" mögen ihn oft auch.',
        "grund_tmdb": "Taucht in den TMDB-Empfehlungen zu deinen Filmen häufig auf.",
    },
    "en": {
        "grund_fans": 'Fans of “{titel}” often like it too.',
        "grund_tmdb": "Shows up often in the TMDB recommendations for your movies.",
    },
}


def _seerr_link(tmdb_id) -> str | None:
    """Link zur Filmseite in Seerr, oder None ohne eingetragene Adresse.

    Bei jedem Aufruf aus den Einstellungen: bis Oktober 2026 stand hier fest
    die Adresse einer einzelnen Installation, eine frische hat aber vielleicht
    gar kein Seerr — dann soll die Oberflaeche den Knopf weglassen statt auf
    eine fremde Adresse zu zeigen."""
    basis = (settings.get("seerr").get("url") or "").strip().rstrip("/")
    return f"{basis}/movie/{tmdb_id}" if basis and tmdb_id else None


def _cache_key(user_id: int, sprache: str) -> str:
    """Die Begruendungen im Zwischenspeicher sind in einer Sprache geschrieben;
    deshalb steht sie im Schluessel (sonst kaeme nach dem Umschalten der alte
    Text). Der alte Schluessel ``u:<id>:missing`` ohne Sprache wird nicht mehr
    gelesen und verfaellt von selbst."""
    return f"u:{user_id}:missing:{sprache}"


def _library_tmdb_ids() -> set[str]:
    with get_session() as s:
        return {m.tmdb_id for m in s.exec(select(Movie)).all() if m.tmdb_id}


def _pool(user_id: int) -> list[dict]:
    this_year = datetime.utcnow().year
    ext = collaborative_scores(user_id)["external"]
    items = [
        e for key, e in ext.items()
        if key.startswith("movie:") and e["score"] > 0
        and (e.get("vote_count") or 0) >= MIN_VOTES
        and e.get("year") and e["year"] <= this_year
    ]
    items.sort(key=lambda e: e["score"], reverse=True)
    return items[:POOL_SIZE]


def _decorate(e: dict, reason: str | None, sprache: str | None = None) -> dict:
    because = e.get("because") or []
    fallback = (texte.waehle(_T, "grund_fans", sprache, titel=because[0]) if because
                else texte.waehle(_T, "grund_tmdb", sprache))
    return {
        "tmdb_id": e["tmdb_id"],
        "title": e["title"],
        "original_title": e.get("original_title"),
        "year": e.get("year"),
        "overview": e.get("overview"),
        "vote_average": e.get("vote_average"),
        "vote_count": e.get("vote_count"),
        "poster_url": poster_url(e.get("poster_path")),
        "because": because[:3],
        "reason": reason or fallback,
        "seerr_url": _seerr_link(e["tmdb_id"]),
    }


def build_messages(user_id: int, pool: list[dict],
                   sprache: str | None = None) -> tuple[list[dict], dict[str, dict]]:
    import curator
    # Neutral statt des Anzeigenamens: der Prompt geht an einen externen
    # Anbieter, und manche Anbieter verbieten personenbezogene Daten oder
    # verwenden Ein-/Ausgaben zum Training. Fuer die Auswahl spielt der Name
    # keine Rolle.
    name = "Nutzer"
    taste = curator._user_taste(user_id)
    taste = {**taste, "favorites": taste["favorites"][:TASTE_FAVORITES],
             "played_no_fav": taste["played_no_fav"][:TASTE_PLAYED]}
    head = curator._taste_block(name, taste, [])

    by_num: dict[str, dict] = {}
    lines = []
    for nr, e in enumerate(pool, start=1):
        by_num[str(nr)] = e
        because = ", ".join(f'"{b}"' for b in (e.get("because") or [])[:2])
        plot = (e.get("overview") or "").replace("\n", " ")[:160]
        lines.append(f'{nr} | {e["title"]} ({e.get("year")}) | TMDB {e.get("vote_average") or 0:.1f} '
                     f'({e.get("vote_count") or 0} Stimmen) | Fans von: {because}\n    {plot}')
    # Der Prompt bleibt deutsch; fuer Englisch faellt nur "deutscher" weg, damit
    # er der angehaengten Antwortsprache nicht widerspricht.
    satz = ("deutscher Satz" if (texte.normalisieren(sprache) or texte.aktuell()) == "de"
            else "Satz")
    user_msg = (
        f"{head}\n\nFILME, DIE ER NOCH NICHT BESITZT (aus TMDB-Empfehlungen zu seinen Filmen; "
        "'Fans von' = Titel aus seiner Historie):\n" + "\n".join(lines) +
        f"\n\nAUFGABE\nWaehle aus DIESER Liste die {RESULT_SIZE} Filme, die er sich am ehesten in "
        "die Sammlung holen sollte — beste Treffer zuerst, mit Abwechslung in Stimmung und Genre. "
        f"Begruendung je ein {satz} ueber genau diesen Film, gern mit Bezug auf einen "
        "seiner Filme. Sprich ihn mit \"du\" an. Gib zu jeder Nummer den exakten Titel aus der "
        "Liste an.\n"
        'JSON-Format: {"vorschlaege": [{"nr": 4, "titel": "...", "grund": "..."}]}'
    )
    messages = [{"role": "system", "content": curator.system_prompt(sprache)},
                {"role": "user", "content": user_msg}]
    return messages, by_num


def _curate(user_id: int, pool: list[dict], sprache: str | None = None) -> list[dict] | None:
    """Modell waehlt RESULT_SIZE Titel aus dem Pool. ``None`` bei Ausfall."""
    import curator

    messages, by_num = build_messages(user_id, pool, sprache)
    t0 = time.time()
    fast = llm_client.fast_model()
    if fast:
        # Schnelles Modell ohne Denkphase. DeepSeek-V4-Flash dachte bei dieser
        # Aufgabe selbst mit reasoning_effort=low 471 s (10K Antwort-Tokens bei
        # 2,8K Prompt); mit voller Denkzeit brach ArliAI nach ~480 s mit 502 ab.
        text = llm_client.complete(messages, temperature=0.7, max_tokens=3000,
                                   timeout=300.0,
                                   allow_fallback=llm_client.rueckfall_grosser_kontext(),
                                   model=fast, aufgabe="fehlt")
    else:
        prompt_est = int(sum(len(m["content"]) for m in messages) / curator.CHARS_PER_TOKEN)
        max_tokens = max(4000, llm_client.kontext_tokens() - prompt_est - 500)
        text = curator.complete_with_retry(messages, max_tokens, first=curator.REASONING_LOW,
                                           aufgabe="fehlt")
    data = curator._parse_json(text) or {}

    out, used, rejected = [], set(), 0
    for entry in data.get("vorschlaege") or []:
        e = curator.resolve_pick(entry, by_num, "title") if isinstance(entry, dict) else None
        if not e or e["tmdb_id"] in used:
            rejected += 1
            continue
        used.add(e["tmdb_id"])
        out.append(_decorate(e, (entry.get("grund") or "").strip() or None, sprache))
        if len(out) >= RESULT_SIZE:
            break
    log.info("Fehlt-in-Sammlung User %s: %d Vorschlaege, %d verworfen, %.0fs (%s)",
             user_id, len(out), rejected, time.time() - t0, fast or llm_client.active_model())
    import ki_protokoll
    ki_protokoll.auswertung("fehlt", bool(out), "" if out else
                            ("kein_json" if not data else "keine_gueltigen_titel"))
    return out or None


def compute(user_id: int, sprache: str | None = None) -> dict:
    """Rechnet die Liste neu und legt sie im Zwischenspeicher ab.

    ``sprache``: Sprache der Begruendungen. Der naechtliche Lauf hat keine
    Anfrage, dort muss der Aufrufer die Sprache des Nutzers angeben
    (texte.fuer_nutzer); ohne Angabe gilt texte.aktuell()."""
    sprache = texte.normalisieren(sprache) or texte.aktuell()
    pool = _pool(user_id)
    items, curated, error = None, False, None
    if pool and llm_client.grosser_kontext():
        try:
            items = _curate(user_id, pool, sprache)
            curated = items is not None
        except Exception as e:
            log.warning("Fehlt-in-Sammlung: Kurator fuer User %s fehlgeschlagen: %s", user_id, e)
            error = str(e)[:200]
    if items is None:
        items = [_decorate(e, None, sprache) for e in pool[:RESULT_SIZE]]
    payload = {"generated_at": datetime.utcnow().isoformat(), "curated": curated,
               "model": (llm_client.fast_model() or llm_client.active_model()) if curated else None,
               "pool": len(pool), "items": items}
    if error:
        payload["error"] = error
    with get_session() as s:
        s.merge(RecommendationCache(key=_cache_key(user_id, sprache),
                                    payload_json=json.dumps(payload, ensure_ascii=False),
                                    created_at=datetime.utcnow()))
        s.commit()
    return payload


def is_stale(user_id: int, max_age: timedelta = timedelta(days=6),
             sprache: str | None = None) -> bool:
    """True, wenn es fuer den Nutzer keinen kuratierten Stand gibt oder er aelter
    als ``max_age`` ist. Der Cron rechnet nur dann neu — die Liste soll ein paar
    Tage stehen bleiben, statt jeden Morgen zu wechseln. Geprueft wird der Stand
    in der Sprache ``sprache`` (ohne Angabe texte.aktuell())."""
    sprache = texte.normalisieren(sprache) or texte.aktuell()
    with get_session() as s:
        row = s.get(RecommendationCache, _cache_key(user_id, sprache))
    if not row or datetime.utcnow() - row.created_at >= max_age:
        return True
    try:
        payload = json.loads(row.payload_json)
    except Exception:
        return True
    # Ohne kuratierfaehiges Modell ist ein mechanischer Stand kein Grund zum
    # Neurechnen — es kaeme nur dasselbe heraus.
    return llm_client.grosser_kontext() and not payload.get("curated")


def missing_for(user_id: int, sprache: str | None = None) -> dict:
    """Aus dem Zwischenspeicher; beim ersten Aufruf ohne Cache mechanisch
    berechnet (schnell) — der naechtliche Lauf kuratiert. Der Zwischenspeicher
    gilt je Sprache: nach dem Umschalten gibt es bis zum naechsten Lauf die
    mechanische Liste in der neuen Sprache."""
    sprache = texte.normalisieren(sprache) or texte.aktuell()
    with get_session() as s:
        row = s.get(RecommendationCache, _cache_key(user_id, sprache))
    payload = None
    if row and datetime.utcnow() - row.created_at < timedelta(days=CACHE_DAYS):
        try:
            payload = json.loads(row.payload_json)
        except Exception:
            payload = None
    if payload is None:
        pool = _pool(user_id)
        payload = {"generated_at": datetime.utcnow().isoformat(), "curated": False,
                   "model": None, "pool": len(pool),
                   "items": [_decorate(e, None, sprache) for e in pool[:RESULT_SIZE]]}
    have = _library_tmdb_ids()
    payload["items"] = [it for it in payload.get("items", []) if it["tmdb_id"] not in have]
    # Link beim Ausliefern neu bauen: der Zwischenspeicher haelt eine Woche,
    # eine geaenderte (oder geloeschte) Seerr-Adresse soll sofort gelten.
    for it in payload["items"]:
        it["seerr_url"] = _seerr_link(it.get("tmdb_id"))
    return payload
