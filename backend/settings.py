"""Einstellungen von CinematekAI — in der Datenbank, im Admin-Bereich aenderbar.

Bis Oktober 2026 stand alles in einer config.yaml, die beim Start einmal
gelesen wurde. Wer ein Modell wechseln wollte, musste die Datei auf dem Server
bearbeiten und den Container neu bauen; ein frischer Container startete ohne
die Datei gar nicht erst. Jetzt gilt:

  * Jeder Abschnitt (``jellyfin``, ``llm``, ``embedding`` ...) ist eine Zeile
    in der Tabelle ``app_setting``, als JSON. Gelesen wird bei jeder
    Verwendung aus dem Speicher; eine Aenderung wirkt sofort, ohne Neustart.
  * Liegt beim ersten Start noch eine config.yaml da, werden ihre Werte EINMAL
    uebernommen (Marke in ``_meta``). Danach ist die Datei bedeutungslos —
    sonst gaebe es zwei Wahrheiten, und welche gewinnt, waere nicht zu sehen.
  * Geheimnisse (API-Schluessel, Tokens) verlassen das Backend nie. Die
    Oberflaeche sieht nur, OB einer gesetzt ist, und die letzten vier Zeichen.
  * Wechselt die Adresse eines Anbieters, faellt der alte Schluessel weg, wenn
    kein neuer mitkommt. Sonst ginge der ArliAI-Schluessel beim Umstellen auf
    OpenAI an OpenAI.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import text

import texte
from config import DATA_DIR, alte_config_lesen
from database import _engine as engine

log = logging.getLogger("cinematekai.settings")


def _zeitzone_vorgabe() -> str:
    """TZ aus der Umgebung, wenn sie eine gueltige Zone ist — sonst Berlin."""
    tz = (os.environ.get("TZ") or "").strip()
    if tz:
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(tz)
            return tz
        except Exception:
            pass
    return "Europe/Berlin"


# Jeder Abschnitt mit allen Feldern und ihren Vorgaben. Der Typ der Vorgabe
# ist zugleich der erlaubte Typ des Feldes.
STANDARD: dict[str, dict[str, Any]] = {
    "jellyfin": {
        "url": "",           # so erreicht das Backend Jellyfin (LAN/Containernetz)
        "public_url": "",    # so oeffnet der Browser Jellyfin (leer = wie url)
        "api_key": "",
        "user_id": "",       # Konto, dessen Bibliothek synchronisiert wird
    },
    "llm": {
        "provider": "",
        "url": "",
        "api_key": "",
        "model": "",
        "fast_model": "",    # optional: ohne Denkphase, fuer lange Listen
        "parallel": 1,       # Anfragen gleichzeitig (ArliAI Core: 1)
        "context_tokens": 32768,
    },
    "llm_fallback": {
        "provider": "",
        "url": "",
        "api_key": "",
        "model": "",
        "context_tokens": 8192,
    },
    "embedding": {
        "provider": "",
        "url": "",
        "api_key": "",
        "model": "",
    },
    "tmdb": {"bearer": ""},
    "seerr": {"url": ""},
    "schedule": {
        "hour": 10,
        "minute": 30,
        "enabled": True,
        "timezone": _zeitzone_vorgabe(),
    },
    "general": {
        "session_lifetime_days": 30,
        "media_root": "/media",
        # Sprache fuer alle, die selbst keine gewaehlt haben, und fuer die
        # Anmeldeseite (siehe texte.py).
        "language": "de",
    },
}

GEHEIM: dict[str, set[str]] = {
    "jellyfin": {"api_key"},
    "llm": {"api_key"},
    "llm_fallback": {"api_key"},
    "embedding": {"api_key"},
    "tmdb": {"bearer"},
}

# Abschnitte, deren Schluessel an eine Adresse gebunden ist.
_ANBIETER_ABSCHNITTE = ("llm", "llm_fallback", "embedding")

_URL_FELDER = {("jellyfin", "url"), ("jellyfin", "public_url"), ("llm", "url"),
               ("llm_fallback", "url"), ("embedding", "url"), ("seerr", "url")}

_GRENZEN: dict[tuple[str, str], tuple[int, int]] = {
    ("llm", "parallel"): (1, 32),
    ("llm", "context_tokens"): (2048, 2_000_000),
    ("llm_fallback", "context_tokens"): (2048, 2_000_000),
    ("schedule", "hour"): (0, 23),
    ("schedule", "minute"): (0, 59),
    ("general", "session_lifetime_days"): (1, 365),
}


class EinstellungFehler(ValueError):
    """Ungueltiger Wert — die Meldung geht so an die Oberflaeche."""


# Meldungen fuer die Oberflaeche (siehe texte.py). Die Sprache kommt aus der
# laufenden Anfrage — gebaut wird der Text erst beim Ausloesen des Fehlers.
_T = {
    "de": {
        "ja_nein": "{abschnitt}.{feld}: ja oder nein erwartet",
        "zahl": "{abschnitt}.{feld}: Zahl erwartet",
        "grenzen": "{abschnitt}.{feld}: erlaubt sind {unten} bis {oben}",
        "adresse": "{abschnitt}.{feld}: Adresse muss mit http:// oder https:// beginnen",
        "anbieter": "{abschnitt}.provider: unbekannter Anbieter {wert!r}",
        "zeitzone": "Unbekannte Zeitzone {wert!r} (Beispiel: Europe/Berlin)",
        "sprache": "Sprache: erlaubt sind de und en",
        "medienpfad": "Medienpfad muss ein absoluter Pfad im Container sein (z. B. /media)",
        "abschnitt": "Unbekannter Abschnitt {abschnitt!r}",
        "felder": "Unbekannte Felder in {abschnitt}: {felder}",
    },
    "en": {
        "ja_nein": "{abschnitt}.{feld}: expected yes or no",
        "zahl": "{abschnitt}.{feld}: expected a number",
        "grenzen": "{abschnitt}.{feld}: allowed range is {unten} to {oben}",
        "adresse": "{abschnitt}.{feld}: address must start with http:// or https://",
        "anbieter": "{abschnitt}.provider: unknown provider {wert!r}",
        "zeitzone": "Unknown time zone {wert!r} (example: Europe/Berlin)",
        "sprache": "Language: allowed values are de and en",
        "medienpfad": "Media path must be an absolute path inside the container (e.g. /media)",
        "abschnitt": "Unknown section {abschnitt!r}",
        "felder": "Unknown fields in {abschnitt}: {felder}",
    },
}


def _fehler(schluessel: str, **werte: Any) -> EinstellungFehler:
    return EinstellungFehler(texte.waehle(_T, schluessel, **werte))


_lock = threading.RLock()
_speicher: dict[str, dict] | None = None
_beobachter: dict[str, list[Callable[[dict, dict], None]]] = {}


# ---------------------------------------------------------------------------
# Datenbank
# ---------------------------------------------------------------------------

def _tabelle_sicherstellen(conn) -> None:
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS app_setting (
            section    TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """))


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat()


def _schreiben(conn, abschnitt: str, wert: dict) -> None:
    conn.execute(text(
        "INSERT INTO app_setting (section, value, updated_at) VALUES (:s, :v, :t) "
        "ON CONFLICT(section) DO UPDATE SET value = excluded.value, "
        "updated_at = excluded.updated_at"),
        {"s": abschnitt, "v": json.dumps(wert, ensure_ascii=False), "t": _jetzt()})


def _alles_lesen(conn) -> dict[str, dict]:
    roh: dict[str, dict] = {}
    for abschnitt, wert in conn.execute(text("SELECT section, value FROM app_setting")):
        try:
            daten = json.loads(wert)
        except json.JSONDecodeError:
            log.error("Einstellung %r ist kein gueltiges JSON und wird ignoriert", abschnitt)
            continue
        if isinstance(daten, dict):
            roh[abschnitt] = daten
    return roh


def _laden() -> dict[str, dict]:
    global _speicher
    with _lock:
        if _speicher is not None:
            return _speicher
        with engine.begin() as conn:
            _tabelle_sicherstellen(conn)
            roh = _alles_lesen(conn)
            meta = roh.get("_meta", {})
            if "config_yaml_uebernommen" not in meta:
                uebernommen = _alte_werte_uebernehmen(conn, roh)
                meta["config_yaml_uebernommen"] = _jetzt()
                meta["config_yaml_abschnitte"] = uebernommen
                _schreiben(conn, "_meta", meta)
                roh = _alles_lesen(conn)
        _speicher = roh
        return _speicher


def neu_laden() -> None:
    """Speicher verwerfen; der naechste Zugriff liest die Datenbank neu."""
    global _speicher
    with _lock:
        _speicher = None


# ---------------------------------------------------------------------------
# Einmalige Uebernahme der alten config.yaml
# ---------------------------------------------------------------------------

def _anbieter_zur_url(url: str) -> str:
    """Bekannten Anbieter an der Adresse erkennen, sonst 'custom'."""
    from urllib.parse import urlparse
    import anbieter
    host = (urlparse(url).hostname or "").lower()
    for p in anbieter.PRESETS:
        if p["url"] and (urlparse(p["url"]).hostname or "").lower() == host:
            return p["id"]
    return "custom"


def _alte_werte_uebernehmen(conn, vorhanden: dict) -> list[str]:
    """Werte aus config.yaml und cron_config.json in die Tabelle schreiben.

    Abschnitte, die schon in der Tabelle stehen, bleiben unberuehrt.
    """
    neu: dict[str, dict] = {}
    cfg = alte_config_lesen()
    if cfg:
        jf = cfg.get("jellyfin") or {}
        neu["jellyfin"] = {
            "url": str(jf.get("url") or "").strip().rstrip("/"),
            "public_url": str(jf.get("public_url") or "").strip().rstrip("/"),
            "api_key": str(jf.get("api_key") or "").strip(),
            "user_id": str(jf.get("user_id") or "").strip(),
        }
        ol = cfg.get("ollama") or {}
        ollama_url = str(ol.get("url") or "").strip().rstrip("/")
        llm = cfg.get("llm") or {}
        llm_url = str(llm.get("url") or "").strip().rstrip("/")
        cloud = (str(llm.get("provider") or "").lower() in ("openai", "arliai")
                 and llm_url and llm.get("api_key") and llm.get("model"))
        if cloud:
            neu["llm"] = {
                "provider": _anbieter_zur_url(llm_url),
                "url": llm_url,
                "api_key": str(llm.get("api_key")).strip(),
                "model": str(llm.get("model")).strip(),
                "fast_model": str(llm.get("fast_model") or "").strip(),
                "parallel": int(llm.get("parallel") or 1),
                "context_tokens": int(llm.get("context_tokens") or 32768),
            }
            # Bisher war Ollama immer der Rueckfall des Cloud-Modells.
            if ollama_url and ol.get("llm_model"):
                neu["llm_fallback"] = {"provider": "ollama", "url": ollama_url,
                                       "api_key": "", "model": str(ol["llm_model"]),
                                       "context_tokens": 8192}
        elif ollama_url and ol.get("llm_model"):
            neu["llm"] = {"provider": "ollama", "url": ollama_url, "api_key": "",
                          "model": str(ol["llm_model"]), "fast_model": "",
                          "parallel": 1, "context_tokens": 8192}
        if ollama_url and ol.get("embedding_model"):
            neu["embedding"] = {"provider": "ollama", "url": ollama_url, "api_key": "",
                                "model": str(ol["embedding_model"]).strip()}
        bearer = str((cfg.get("tmdb") or {}).get("bearer") or "").strip()
        if bearer and not bearer.startswith("<"):
            neu["tmdb"] = {"bearer": bearer}
        allgemein = {}
        if (cfg.get("media") or {}).get("root"):
            allgemein["media_root"] = str(cfg["media"]["root"])
        if (cfg.get("session") or {}).get("lifetime_days"):
            allgemein["session_lifetime_days"] = int(cfg["session"]["lifetime_days"])
        if allgemein:
            neu["general"] = allgemein
        # Die Seerr-Adresse stand bis hierhin nicht in der config.yaml, sondern
        # fest im Code - als Adresse einer einzelnen Installation. Sie wird
        # deshalb bewusst nicht uebernommen: Seerr bleibt leer (die Oberflaeche
        # zeigt dann keinen Anfrage-Knopf), bis ein Admin unter Einstellungen
        # die eigene Adresse eintraegt.

    cron_datei = DATA_DIR / "cron_config.json"
    if cron_datei.is_file():
        try:
            c = json.loads(cron_datei.read_text())
            neu["schedule"] = {"hour": int(c.get("hour", 10)), "minute": int(c.get("minute", 30)),
                               "enabled": bool(c.get("enabled", True))}
        except Exception as e:
            log.warning("cron_config.json nicht lesbar, Zeitplan bleibt auf Vorgabe: %s", e)

    geschrieben = []
    for abschnitt, werte in neu.items():
        if abschnitt in vorhanden:
            continue
        # Platzhalter aus config.example.yaml ("<JELLYFIN_API_KEY>") nicht uebernehmen.
        werte = {k: v for k, v in werte.items()
                 if not (isinstance(v, str) and v.startswith("<") and v.endswith(">"))}
        _schreiben(conn, abschnitt, werte)
        geschrieben.append(abschnitt)
    if geschrieben:
        log.info("Einstellungen aus config.yaml/cron_config.json uebernommen: %s. "
                 "Die Datei wird ab jetzt nicht mehr gelesen.", ", ".join(geschrieben))
    return geschrieben


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------

def get(abschnitt: str) -> dict:
    """Abschnitt mit allen Feldern (fehlende mit Vorgabe). Eine Kopie."""
    if abschnitt not in STANDARD and abschnitt != "kalibrierung":
        raise KeyError(abschnitt)
    gespeichert = _laden().get(abschnitt, {})
    if abschnitt == "kalibrierung":
        return copy.deepcopy(gespeichert)
    return {**copy.deepcopy(STANDARD[abschnitt]),
            **{k: v for k, v in gespeichert.items() if k in STANDARD[abschnitt]}}


def ist_eingerichtet() -> bool:
    """Jellyfin ist verbunden — ohne das laeuft nichts (Bibliothek, Anmeldung)."""
    jf = get("jellyfin")
    return bool(jf["url"] and jf["api_key"] and jf["user_id"])


def _maskieren(abschnitt: str, werte: dict) -> dict:
    aus = dict(werte)
    for feld in GEHEIM.get(abschnitt, ()):
        wert = str(aus.get(feld) or "")
        aus[feld] = ""
        aus[f"{feld}_gesetzt"] = bool(wert)
        # Die letzten vier Zeichen helfen beim Wiedererkennen. Bei kurzen
        # Schluesseln waere das ein zu grosser Teil des Geheimnisses.
        aus[f"{feld}_ende"] = wert[-4:] if len(wert) >= 16 else ""
    return aus


def fuer_oberflaeche() -> dict[str, dict]:
    """Alle Abschnitte, Geheimnisse ersetzt."""
    return {a: _maskieren(a, get(a)) for a in STANDARD}


# ---------------------------------------------------------------------------
# Schreiben
# ---------------------------------------------------------------------------

def _pruefen(abschnitt: str, feld: str, wert: Any) -> Any:
    vorgabe = STANDARD[abschnitt][feld]
    if isinstance(vorgabe, bool):
        if not isinstance(wert, bool):
            raise _fehler("ja_nein", abschnitt=abschnitt, feld=feld)
        return wert
    if isinstance(vorgabe, int):
        try:
            zahl = int(wert)
        except (TypeError, ValueError):
            raise _fehler("zahl", abschnitt=abschnitt, feld=feld)
        unten, oben = _GRENZEN.get((abschnitt, feld), (-(2**31), 2**31))
        if not unten <= zahl <= oben:
            raise _fehler("grenzen", abschnitt=abschnitt, feld=feld, unten=unten, oben=oben)
        return zahl
    wert = "" if wert is None else str(wert).strip()
    if (abschnitt, feld) in _URL_FELDER and wert:
        if not re.match(r"^https?://[^\s/]+", wert):
            raise _fehler("adresse", abschnitt=abschnitt, feld=feld)
        wert = wert.rstrip("/")
    if feld == "provider" and wert:
        import anbieter
        if wert not in {p["id"] for p in anbieter.PRESETS}:
            raise _fehler("anbieter", abschnitt=abschnitt, wert=wert)
    if (abschnitt, feld) == ("schedule", "timezone"):
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(wert)
        except Exception:
            raise _fehler("zeitzone", wert=wert)
    if (abschnitt, feld) == ("general", "language") and wert not in ("de", "en"):
        raise _fehler("sprache")
    if (abschnitt, feld) == ("general", "media_root") and not wert.startswith("/"):
        raise _fehler("medienpfad")
    return wert


def _ziel_url(abschnitt: dict) -> str:
    """Adresse, an die ein Anbieter-Abschnitt sendet: eingetragen oder die des Anbieters."""
    import anbieter
    p = anbieter.preset(abschnitt.get("provider", "")) or {}
    return ((abschnitt.get("url") or "").strip() or p.get("url") or "").rstrip("/").lower()


def speichern(abschnitt: str, werte: dict) -> dict:
    """Felder eines Abschnitts aendern; gibt den neuen Stand zurueck (unmaskiert).

    Geheime Felder: fehlt das Feld oder ist es leer, bleibt der alte Wert;
    ``None`` loescht ihn. Aendert sich Anbieter oder Adresse und kommt kein
    neuer Schluessel mit, wird der alte verworfen.
    """
    if abschnitt not in STANDARD:
        raise _fehler("abschnitt", abschnitt=abschnitt)
    unbekannt = [k for k in werte if k not in STANDARD[abschnitt]]
    if unbekannt:
        raise _fehler("felder", abschnitt=abschnitt, felder=", ".join(unbekannt))

    geheim = GEHEIM.get(abschnitt, set())
    with _lock:
        alt = get(abschnitt)
        neu = dict(alt)
        for feld, wert in werte.items():
            if feld in geheim:
                if wert is None:
                    neu[feld] = ""
                elif str(wert).strip():
                    neu[feld] = str(wert).strip()
                continue
            neu[feld] = _pruefen(abschnitt, feld, wert)

        if abschnitt in _ANBIETER_ABSCHNITTE:
            # Massgeblich ist die Adresse, an die tatsaechlich gesendet wird
            # (leer = die des Anbieters). Wechselt nur der Eintrag in der
            # Liste, die Adresse aber nicht ("Andere" -> "NVIDIA" mit derselben
            # URL), geht der Schluessel an denselben Server — kein Grund, ihn
            # zu verwerfen.
            umgezogen = _ziel_url(neu) != _ziel_url(alt)
            neuer_schluessel = bool(str(werte.get("api_key") or "").strip())
            if umgezogen and not neuer_schluessel and alt["api_key"]:
                neu["api_key"] = ""
                log.info("%s: Anbieter/Adresse geaendert, alter Schluessel verworfen", abschnitt)

        with engine.begin() as conn:
            _tabelle_sicherstellen(conn)
            _schreiben(conn, abschnitt, neu)
        _laden()[abschnitt] = copy.deepcopy(neu)

    geaendert = sorted(k for k in neu if neu[k] != alt[k])
    if geaendert:
        log.info("Einstellung %s geaendert: %s", abschnitt,
                 ", ".join(k if k in geheim else f"{k}={neu[k]!r}" for k in geaendert))
        for fn in list(_beobachter.get(abschnitt, [])):
            try:
                fn(alt, copy.deepcopy(neu))
            except Exception as e:
                log.warning("Beobachter fuer %s fehlgeschlagen: %s", abschnitt, e)
    return neu


def kalibrierung_setzen(modell: str, werte: dict) -> None:
    """Skala der Passungswerte fuer ein Einbettungsmodell ablegen."""
    with _lock:
        alles = get("kalibrierung")
        alles[modell] = werte
        with engine.begin() as conn:
            _tabelle_sicherstellen(conn)
            _schreiben(conn, "kalibrierung", alles)
        _laden()["kalibrierung"] = copy.deepcopy(alles)


def beobachten(abschnitt: str, fn: Callable[[dict, dict], None]) -> None:
    """``fn(alt, neu)`` wird nach jeder Aenderung des Abschnitts aufgerufen."""
    _beobachter.setdefault(abschnitt, []).append(fn)
