"""KI-Anbieter: welche es gibt und wie man sie anspricht.

Fast alle Anbieter sprechen heute das Format der OpenAI-API
(``/chat/completions``, ``/embeddings``, ``/models``) — auch Google, Mistral,
Groq, OpenRouter, DeepSeek, ArliAI und die lokalen Server (LM Studio, vLLM,
llama.cpp). Fuer sie reicht deshalb eine Adresse und ein Schluessel. Eigene
Wege brauchen nur zwei:

  * **Anthropic** hat ein eigenes Format (``/messages``, Systemtext getrennt,
    Schluessel im Kopf ``x-api-key``). Anthropic bietet zwar auch eine
    OpenAI-Schicht an, nennt sie aber ausdruecklich nicht fuer den Dauerbetrieb
    gedacht.
  * **Ollama** wird ueber seine eigene API angesprochen, weil nur die das
    Kontextfenster (``num_ctx``) setzen laesst. Ohne das faellt Ollama auf
    4096 Tokens zurueck und vergisst die Formatvorgaben im Prompt.

Diese Datei kennt die Anbieter und erledigt alles, was nicht Chat ist:
Anmeldung, Modellliste, Einbettungen. Der Chat selbst liegt in llm_client.py.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import httpx

import settings
import texte

log = logging.getLogger("cinematekai.anbieter")


# Reihenfolge = Reihenfolge in der Auswahlliste. ``schluessel``: pflicht,
# optional oder nein. ``chat``/``einbettung``: wofuer der Anbieter taugt.
# ``kontext`` ist nur der Vorschlag fuer das Feld im Formular.
# ``hinweis_en``/``name_en``: englische Fassung fuer die Oberflaeche; welche
# ausgeliefert wird, entscheidet ``fuer_oberflaeche`` (fehlt sie, gilt die
# deutsche).
PRESETS: list[dict] = [
    {"id": "openai", "name": "OpenAI", "api": "openai",
     "url": "https://api.openai.com/v1", "schluessel": "pflicht",
     "chat": True, "einbettung": True, "kontext": 128000,
     "hinweis": "Einbettung z. B. text-embedding-3-small",
     "hinweis_en": "Embedding e.g. text-embedding-3-small"},
    {"id": "anthropic", "name": "Anthropic (Claude)", "api": "anthropic",
     "url": "https://api.anthropic.com/v1", "schluessel": "pflicht",
     "chat": True, "einbettung": False, "kontext": 200000,
     "hinweis": "Kein eigener Einbettungsdienst; dafuer z. B. Voyage AI nehmen",
     "hinweis_en": "No embedding service of its own; use e.g. Voyage AI for that"},
    {"id": "google", "name": "Google Gemini", "api": "openai",
     "url": "https://generativelanguage.googleapis.com/v1beta/openai", "schluessel": "pflicht",
     "chat": True, "einbettung": True, "kontext": 1000000,
     "hinweis": "Schluessel aus Google AI Studio; Einbettung z. B. gemini-embedding-001",
     "hinweis_en": "Key from Google AI Studio; embedding e.g. gemini-embedding-001"},
    {"id": "mistral", "name": "Mistral AI", "api": "openai",
     "url": "https://api.mistral.ai/v1", "schluessel": "pflicht",
     "chat": True, "einbettung": True, "kontext": 128000,
     "hinweis": "Einbettung: mistral-embed",
     "hinweis_en": "Embedding: mistral-embed"},
    {"id": "deepseek", "name": "DeepSeek", "api": "openai",
     "url": "https://api.deepseek.com/v1", "schluessel": "pflicht",
     "chat": True, "einbettung": False, "kontext": 64000, "hinweis": ""},
    {"id": "openrouter", "name": "OpenRouter", "api": "openai",
     "url": "https://openrouter.ai/api/v1", "schluessel": "pflicht",
     "chat": True, "einbettung": False, "kontext": 32768,
     "hinweis": "Viele Modelle unter einem Schluessel",
     "hinweis_en": "Many models under one key"},
    {"id": "groq", "name": "Groq", "api": "openai",
     "url": "https://api.groq.com/openai/v1", "schluessel": "pflicht",
     "chat": True, "einbettung": False, "kontext": 32768, "hinweis": ""},
    {"id": "together", "name": "Together AI", "api": "openai",
     "url": "https://api.together.xyz/v1", "schluessel": "pflicht",
     "chat": True, "einbettung": True, "kontext": 32768, "hinweis": ""},
    {"id": "fireworks", "name": "Fireworks AI", "api": "openai",
     "url": "https://api.fireworks.ai/inference/v1", "schluessel": "pflicht",
     "chat": True, "einbettung": True, "kontext": 32768, "hinweis": ""},
    {"id": "xai", "name": "xAI (Grok)", "api": "openai",
     "url": "https://api.x.ai/v1", "schluessel": "pflicht",
     "chat": True, "einbettung": False, "kontext": 128000, "hinweis": ""},
    {"id": "cerebras", "name": "Cerebras", "api": "openai",
     "url": "https://api.cerebras.ai/v1", "schluessel": "pflicht",
     "chat": True, "einbettung": False, "kontext": 32768, "hinweis": ""},
    {"id": "cohere", "name": "Cohere", "api": "openai",
     "url": "https://api.cohere.ai/compatibility/v1", "schluessel": "pflicht",
     "chat": True, "einbettung": True, "kontext": 128000,
     "hinweis": "Einbettung z. B. embed-multilingual-v3.0",
     "hinweis_en": "Embedding e.g. embed-multilingual-v3.0"},
    {"id": "arliai", "name": "ArliAI", "api": "openai",
     "url": "https://api.arliai.com/v1", "schluessel": "pflicht",
     "chat": True, "einbettung": False, "kontext": 32768,
     "hinweis": "Core-Tarif: nur 1 Anfrage gleichzeitig",
     "hinweis_en": "Core plan: only 1 request at a time"},
    # NVIDIA-API (OpenAI-kompatibel). Kontext-Vorgabe 65536: Modelle mit langer
    # Denkphase liefen beim Kurator mit 32768 einmal ins Leere.
    {"id": "nvidia", "name": "NVIDIA (build.nvidia.com)", "api": "openai",
     "url": "https://integrate.api.nvidia.com/v1", "schluessel": "pflicht",
     "chat": True, "einbettung": False, "kontext": 65536,
     "hinweis": "OpenAI-kompatible API von NVIDIA; Modellnamen siehe Katalog auf build.nvidia.com",
     "hinweis_en": "OpenAI-compatible API from NVIDIA; for model names see the catalog on "
                   "build.nvidia.com"},
    {"id": "voyage", "name": "Voyage AI", "api": "openai",
     "url": "https://api.voyageai.com/v1", "schluessel": "pflicht",
     "chat": False, "einbettung": True, "kontext": 0,
     "hinweis": "Nur Einbettung, z. B. voyage-3.5",
     "hinweis_en": "Embedding only, e.g. voyage-3.5"},
    {"id": "jina", "name": "Jina AI", "api": "openai",
     "url": "https://api.jina.ai/v1", "schluessel": "pflicht",
     "chat": False, "einbettung": True, "kontext": 0,
     "hinweis": "Nur Einbettung, z. B. jina-embeddings-v3",
     "hinweis_en": "Embedding only, e.g. jina-embeddings-v3"},
    {"id": "azure", "name": "Azure OpenAI", "api": "openai", "auth": "api-key",
     "url": "", "schluessel": "pflicht",
     "chat": True, "einbettung": True, "kontext": 128000,
     "hinweis": "Adresse https://<ressource>.openai.azure.com/openai/v1, "
                "Modell = Name der Bereitstellung",
     "hinweis_en": "Address https://<resource>.openai.azure.com/openai/v1, "
                   "model = name of the deployment"},
    {"id": "ollama", "name": "Ollama", "api": "ollama",
     "url": "http://localhost:11434", "schluessel": "optional",
     "chat": True, "einbettung": True, "kontext": 8192,
     "hinweis": "Im Container ist localhost der Container selbst — "
                "die Adresse des Rechners angeben, auf dem Ollama laeuft",
     "hinweis_en": "Inside the container, localhost is the container itself — "
                   "enter the address of the machine running Ollama"},
    {"id": "lmstudio", "name": "LM Studio", "api": "openai",
     "url": "http://localhost:1234/v1", "schluessel": "nein",
     "chat": True, "einbettung": True, "kontext": 8192,
     "hinweis": "Wie bei Ollama: die Adresse des Rechners angeben",
     "hinweis_en": "As with Ollama: enter the address of the machine"},
    {"id": "custom", "name": "Andere (OpenAI-kompatibel)", "api": "openai",
     "name_en": "Other (OpenAI-compatible)",
     "url": "", "schluessel": "optional",
     "chat": True, "einbettung": True, "kontext": 32768,
     "hinweis": "vLLM, LocalAI, llama.cpp, LiteLLM und alles, was /chat/completions spricht",
     "hinweis_en": "vLLM, LocalAI, llama.cpp, LiteLLM and anything that speaks /chat/completions"},
]

_NACH_ID = {p["id"]: p for p in PRESETS}

ANTHROPIC_VERSION = "2023-06-01"

# Felder, die es je Sprache gibt (``<feld>_<sprache>`` neben dem deutschen).
_SPRACHFELDER = ("name", "hinweis")


def _uebersetzt(p: dict, feld: str, sprache: str | None = None) -> str:
    """``name``/``hinweis`` eines Anbieters in der gewuenschten Sprache."""
    code = texte.normalisieren(sprache) or texte.aktuell()
    if code != texte.STANDARD:
        wert = p.get(f"{feld}_{code}")
        if wert:
            return wert
    return p.get(feld, "")


def anzeigename(anbieter_id: str, sprache: str | None = None) -> str:
    """Name eines Anbieters fuer Meldungen, in der Sprache der Anfrage."""
    p = _NACH_ID.get(anbieter_id or "")
    return _uebersetzt(p, "name", sprache) if p else (anbieter_id or "")


def fuer_oberflaeche(sprache: str | None = None) -> list[dict]:
    """PRESETS fuer den Admin-Bereich: ``name`` und ``hinweis`` in der Sprache
    der Anfrage, ohne die Fassungen der anderen Sprachen."""
    aus = []
    for p in PRESETS:
        eintrag = {k: v for k, v in p.items()
                   if not any(k.startswith(f"{f}_") for f in _SPRACHFELDER)}
        for feld in _SPRACHFELDER:
            eintrag[feld] = _uebersetzt(p, feld, sprache)
        aus.append(eintrag)
    return aus


# Meldungen fuer die Oberflaeche (Modellliste, Verbindungstest, Einbettung).
_T = {
    "de": {
        "nicht_erreichbar_adresse": "{name} nicht erreichbar ({url}): {fehler}",
        "kein_json": "{name}: Antwort ist kein JSON — stimmt die Adresse?",
        "kein_modell": "Kein Einbettungsmodell eingerichtet",
        "keine_einbettung": "{name} bietet keine Einbettungen an",
        "nicht_erreichbar": "{name} nicht erreichbar: {fehler}",
        "ohne_vektor": "{name}: Antwort ohne Vektor",
        "keine_antwort": "{name}: keine Antwort nach mehreren Versuchen",
    },
    "en": {
        "nicht_erreichbar_adresse": "{name} not reachable ({url}): {fehler}",
        "kein_json": "{name}: response is not JSON — is the address correct?",
        "kein_modell": "No embedding model set up",
        "keine_einbettung": "{name} does not offer embeddings",
        "nicht_erreichbar": "{name} not reachable: {fehler}",
        "ohne_vektor": "{name}: response without a vector",
        "keine_antwort": "{name}: no response after several attempts",
    },
}


def _text(schluessel: str, **werte) -> str:
    return texte.waehle(_T, schluessel, **werte)


class AnbieterFehler(RuntimeError):
    """Fehler eines Anbieters, aufbereitet fuer die Oberflaeche."""


@dataclass(frozen=True)
class Verbindung:
    anbieter: str
    api: str          # openai | anthropic | ollama
    url: str
    schluessel: str
    auth: str         # bearer | api-key

    @property
    def name(self) -> str:
        # Anzeigename in der Sprache der Anfrage ("Andere ..." / "Other ...").
        return anzeigename(self.anbieter)


def preset(anbieter_id: str) -> dict | None:
    return _NACH_ID.get(anbieter_id or "")


def verbindung(abschnitt: dict) -> Verbindung | None:
    """Verbindung aus einem Einstellungsabschnitt, oder ``None``, wenn er
    nicht vollstaendig ist (kein Anbieter, keine Adresse, fehlender Pflicht-
    schluessel)."""
    p = preset(abschnitt.get("provider", ""))
    if not p:
        return None
    url = (abschnitt.get("url") or p["url"] or "").strip().rstrip("/")
    schluessel = (abschnitt.get("api_key") or "").strip()
    if not url or (p["schluessel"] == "pflicht" and not schluessel):
        return None
    return Verbindung(anbieter=p["id"], api=p["api"], url=url,
                      schluessel=schluessel, auth=p.get("auth", "bearer"))


def kopfzeilen(v: Verbindung) -> dict:
    h = {"Content-Type": "application/json"}
    if v.api == "anthropic":
        h["x-api-key"] = v.schluessel
        h["anthropic-version"] = ANTHROPIC_VERSION
    elif v.schluessel:
        if v.auth == "api-key":
            h["api-key"] = v.schluessel
        else:
            h["Authorization"] = f"Bearer {v.schluessel}"
    if v.anbieter == "openrouter":
        h["X-Title"] = "CinematekAI"
    return h


def fehlertext(v: Verbindung, status: int, text: str) -> str:
    """Antwort eines Anbieters kuerzen und den Schluessel sicher entfernen."""
    text = (text or "").strip().replace("\n", " ")
    if v.schluessel:
        text = text.replace(v.schluessel, "•••")
    return f"HTTP {status}: {text[:300]}"


# ---------------------------------------------------------------------------
# Modellliste
# ---------------------------------------------------------------------------

def modelle(v: Verbindung, timeout: float = 20.0) -> list[str]:
    """Namen der Modelle, die der Anbieter anbietet. Fuer die Vorschlagsliste
    im Formular — eingeben laesst sich trotzdem jeder Name."""
    try:
        with httpx.Client(timeout=timeout) as c:
            if v.api == "ollama":
                r = c.get(f"{v.url}/api/tags", headers=kopfzeilen(v))
            elif v.api == "anthropic":
                r = c.get(f"{v.url}/models", headers=kopfzeilen(v), params={"limit": 1000})
            else:
                r = c.get(f"{v.url}/models", headers=kopfzeilen(v))
    except httpx.HTTPError as e:
        raise AnbieterFehler(_text("nicht_erreichbar_adresse", name=v.name, url=v.url,
                                  fehler=e)) from e
    if r.status_code >= 400:
        raise AnbieterFehler(fehlertext(v, r.status_code, r.text))
    try:
        daten = r.json()
    except ValueError:
        raise AnbieterFehler(_text("kein_json", name=v.name))
    if v.api == "ollama":
        namen = [m.get("name") or m.get("model") for m in daten.get("models") or []]
    else:
        eintraege = daten.get("data") if isinstance(daten, dict) else daten
        namen = [m.get("id") for m in eintraege or [] if isinstance(m, dict)]
    # Google liefert "models/gemini-2.5-flash", erwartet im Chat aber den Namen ohne.
    namen = [n.removeprefix("models/") for n in namen if n]
    return sorted(set(namen), key=str.lower)


# ---------------------------------------------------------------------------
# Einbettungen
# ---------------------------------------------------------------------------

# bge-m3 braucht auf einer sparsamen Server-CPU ohne GPU 6-8 s fuer ein
# Filmdokument (gemessen, warmes Modell). Dazu kommt beim allerersten Aufruf
# das Laden des Modells. Ein Zeitablauf mittendrin liesse den
# Neu-Einbett-Lauf mit lauter Fehlern durchlaufen; deshalb grosszuegig.
_EINBETTEN_TIMEOUT_S = 120.0
# Wartezeiten bei 429 und 5xx. Cloud-Dienste drosseln beim Neu-Einbetten der
# ganzen Bibliothek gern; ein einzelner Fehlschlag soll den Titel nicht kosten.
_WIEDERHOLEN_NACH_S = (2.0, 5.0, 15.0)
_VORUEBERGEHEND = {408, 429, 500, 502, 503, 504, 529}


def _einbettung_abschnitt() -> dict:
    return settings.get("embedding")


def einbettung_modell() -> str:
    return (_einbettung_abschnitt().get("model") or "").strip()


def einbetten(text: str, abschnitt: dict | None = None) -> list[float]:
    """Einbettungsvektor fuer einen Text, mit dem eingestellten Modell.

    Nimmt den Text unveraendert; die Praefixe setzen die Aufrufer ueber
    ``praefix_dokument`` / ``praefix_anfrage``, weil der eingebettete Text im
    Vektorspeicher mitgespeichert wird und dort zum Vektor passen muss.
    """
    abschnitt = abschnitt or _einbettung_abschnitt()
    v = verbindung(abschnitt)
    modell = (abschnitt.get("model") or "").strip()
    if not v or not modell:
        raise AnbieterFehler(_text("kein_modell"))
    if not (preset(v.anbieter) or {}).get("einbettung"):
        raise AnbieterFehler(_text("keine_einbettung", name=v.name))

    for versuch, warten in enumerate((*_WIEDERHOLEN_NACH_S, None)):
        try:
            with httpx.Client(timeout=_EINBETTEN_TIMEOUT_S) as c:
                if v.api == "ollama":
                    r = c.post(f"{v.url}/api/embeddings", headers=kopfzeilen(v),
                               json={"model": modell, "prompt": text})
                else:
                    r = c.post(f"{v.url}/embeddings", headers=kopfzeilen(v),
                               json={"model": modell, "input": [text]})
        except httpx.TransportError as e:
            if warten is None:
                raise AnbieterFehler(_text("nicht_erreichbar", name=v.name, fehler=e)) from e
            time.sleep(warten)
            continue
        if r.status_code in _VORUEBERGEHEND and warten is not None:
            nach = r.headers.get("retry-after")
            try:
                warten = min(60.0, float(nach)) if nach else warten
            except ValueError:
                pass
            log.info("Einbettung: %s antwortet %s — neuer Versuch in %.0f s",
                     v.name, r.status_code, warten)
            time.sleep(warten)
            continue
        if r.status_code >= 400:
            raise AnbieterFehler(fehlertext(v, r.status_code, r.text))
        daten = r.json()
        if v.api == "ollama":
            vektor = daten.get("embedding")
        else:
            vektor = ((daten.get("data") or [{}])[0]).get("embedding")
        if not vektor:
            raise AnbieterFehler(_text("ohne_vektor", name=v.name))
        return vektor
    raise AnbieterFehler(_text("keine_antwort", name=v.name))


def einbettung_testen(abschnitt: dict) -> dict:
    """Einen kurzen Text einbetten; meldet Dauer und Dimension."""
    t0 = time.monotonic()
    vektor = einbetten(praefix_anfrage("lustiger Tierfilm fuer Kinder", abschnitt.get("model")),
                       abschnitt)
    return {"ok": True, "ms": int((time.monotonic() - t0) * 1000), "dimension": len(vektor)}


_verfuegbar_bis = 0.0
_verfuegbar_wert = False
_verfuegbar_lock = threading.Lock()


def einbettung_verfuegbar() -> bool:
    """Eingerichtet und (bei Ollama) erreichbar. Eine Minute gemerkt, weil
    die Startseite das bei jedem Aufruf fragt."""
    global _verfuegbar_bis, _verfuegbar_wert
    with _verfuegbar_lock:
        if time.monotonic() < _verfuegbar_bis:
            return _verfuegbar_wert
    abschnitt = _einbettung_abschnitt()
    v = verbindung(abschnitt)
    if not v or not abschnitt.get("model"):
        wert = False
    elif v.api == "ollama":
        try:
            with httpx.Client(timeout=5.0) as c:
                wert = c.get(f"{v.url}/api/tags", headers=kopfzeilen(v)).status_code == 200
        except httpx.HTTPError:
            wert = False
    else:
        # Bei Cloud-Diensten kostet jede Probe eine Anfrage; ein Fehler zeigt
        # sich ohnehin beim ersten echten Aufruf.
        wert = True
    with _verfuegbar_lock:
        _verfuegbar_wert, _verfuegbar_bis = wert, time.monotonic() + 60.0
    return wert


def ollama_erreichbar(url: str) -> bool:
    try:
        with httpx.Client(timeout=5.0) as c:
            return c.get(f"{url.rstrip('/')}/api/tags").status_code == 200
    except httpx.HTTPError:
        return False


# ---------------------------------------------------------------------------
# Praefixe je Einbettungsmodell
# ---------------------------------------------------------------------------
# nomic-embed-text ist darauf trainiert, dass vor dem Text steht, wozu er
# dient: "search_document: " fuer einen Bibliothekseintrag, "search_query: "
# fuer eine Frage. Die e5-Familie erwartet dasselbe als "passage: " und
# "query: ". bge-m3, OpenAI, Gemini, Mistral und Voyage kennen keine Praefixe —
# bekommen sie trotzdem welche, verwaessern sie den Vektor.
#
# Nachgemessen an der Bibliothek mit der Frage "lustiger Tierfilm fuer
# Kinder": bge-m3 OHNE Praefix sortiert richtig — Pinguine 0,539 >
# Bambi 0,527 > Shrek 0,509 > Ice Age 0,492, dahinter mit deutlichem
# Abstand Casino 0,393 und Nicht auflegen! 0,389.

def _modellbasis(modell: str) -> str:
    """'nomic-embed-text:latest' -> 'nomic-embed-text', 'intfloat/e5-large' -> 'e5-large'."""
    basis = (modell or "").split(":", 1)[0].strip().lower()
    return basis.rsplit("/", 1)[-1]


def _praefixe(modell: str | None) -> tuple[str, str]:
    basis = _modellbasis(modell if modell is not None else einbettung_modell())
    if basis.startswith("nomic-embed"):
        return "search_document: ", "search_query: "
    if basis.startswith(("e5-", "multilingual-e5")):
        return "passage: ", "query: "
    return "", ""


def praefix_dokument(text: str, modell: str | None = None) -> str:
    """Text so vorbereiten, wie er als Bibliothekseintrag eingebettet wird."""
    return _praefixe(modell)[0] + text


def praefix_anfrage(text: str, modell: str | None = None) -> str:
    """Text so vorbereiten, wie er als Suchanfrage eingebettet wird."""
    return _praefixe(modell)[1] + text
