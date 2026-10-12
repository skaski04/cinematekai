"""Ein Zugang fuer alle Textaufgaben: Hauptmodell, schnelles Modell, Rueckfall.

Welche Modelle das sind, steht in den Einstellungen (Abschnitte ``llm`` und
``llm_fallback``) und kann sich zur Laufzeit aendern — gelesen wird deshalb
bei jedem Aufruf, nichts wird beim Import festgehalten.

Drei Wege zum Modell (siehe anbieter.py): das OpenAI-Format, das fast alle
Anbieter sprechen, Anthropics eigenes Format und Ollamas eigene API.

Was hier sonst noch passiert und warum:
  * Manche Tarife erlauben nur **eine Anfrage gleichzeitig** (ArliAI Core) —
    und reihen nicht ein, sondern antworten sofort mit 403 ("exceeded the
    maximum number of parallel requests"). Innerhalb des Backends ordnet ein
    Semaphor je Adresse die Aufrufe; laeuft trotzdem eine fremde Anfrage
    (derselbe Schluessel in einer anderen App), wird gewartet und erneut
    versucht.
  * Anbieter unterscheiden sich in Kleinigkeiten, die mit 400 enden: neuere
    OpenAI-Modelle wollen ``max_completion_tokens`` statt ``max_tokens`` und
    lassen keine Temperatur zu, nicht jedes Modell kennt ``response_format``,
    ``reasoning_effort`` versteht kaum jemand ausser OpenAI und DeepSeek. Statt
    eine Tabelle zu pflegen, die nie vollstaendig ist, wird die Fehlermeldung
    gelesen, die Anfrage angepasst und das Ergebnis je Modell gemerkt.
  * Denk-Modelle liefern Ueberlegungen in ``reasoning_content`` oder als
    ``<think>…</think>`` im Text. Nach aussen geht nur die Antwort.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Iterator

import httpx

import anbieter
import ollama_client
import settings
import texte
from anbieter import AnbieterFehler, Verbindung

log = logging.getLogger("cinematekai.llm")

# Meldungen, die ueber Fehlertexte bis in die Oberflaeche gelangen koennen
# (Verbindungstest im Admin-Bereich, "[Fehler: ...]" im Chat). Erst beim
# Ausloesen gebaut, in der Sprache der Anfrage bzw. des Fadens (texte.py).
_T = {
    "de": {
        "kein_hauptmodell": "Kein Hauptmodell eingerichtet",
        "kein_sprachmodell": "Kein Sprachmodell eingerichtet",
        "kein_platz": "kein freier Anfrageplatz (eine andere Anfrage laeuft noch)",
        "anbieter_belegt": "Anbieter blieb belegt",
        "leer_detail": "leere Antwort (Ende={ende}, {tokens} Antwort-Tokens, "
                       "Denkanteil {denk} Zeichen)",
        "leer_ende": "leere Antwort (Ende={ende})",
        "leer": "leere Antwort",
        "verbindung_fehlt": "Anbieter, Adresse oder Schluessel fehlt",
        "modell_fehlt": "Kein Modell angegeben",
        "test_belegt": "{name} ist gerade mit einer anderen Anfrage belegt (der Tarif erlaubt "
                       "nur eine zur Zeit, z. B. läuft der Kurator). Die Einstellung kann "
                       "trotzdem stimmen. In ein paar Minuten noch einmal testen.",
        "test_kein_platz": "{name}: kein freier Anfrageplatz, gerade läuft eine "
                           "andere Anfrage (z. B. der Kurator). Später noch einmal "
                           "testen.",
    },
    "en": {
        "kein_hauptmodell": "No main model set up",
        "kein_sprachmodell": "No language model set up",
        "kein_platz": "no free request slot (another request is still running)",
        "anbieter_belegt": "The provider stayed busy",
        "leer_detail": "empty response (finish={ende}, {tokens} response tokens, "
                       "{denk} characters of reasoning)",
        "leer_ende": "empty response (finish={ende})",
        "leer": "empty response",
        "verbindung_fehlt": "Provider, address or key is missing",
        "modell_fehlt": "No model given",
        "test_belegt": "{name} is busy with another request right now (the plan allows "
                       "only one at a time, e.g. the curator is running). The setting may "
                       "still be correct. Try again in a few minutes.",
        "test_kein_platz": "{name}: no free request slot, another request is running "
                           "right now (e.g. the curator). Try again later.",
    },
}


def _t(schluessel: str, **werte) -> str:
    return texte.waehle(_T, schluessel, **werte)

# So lange wartet eine Anfrage hoechstens auf einen freien Platz (intern wie
# beim Anbieter). Grosszuegig: im Hintergrund spielt Zeit keine Rolle.
SLOT_WAIT_S = 900
PARALLEL_RETRY_S = 20
# 429 ohne Parallel-Hinweis = Ratenbegrenzung. Kurz warten, dann aufgeben.
RATE_RETRIES = 2
RATE_WAIT_MAX_S = 30.0

# Ab dieser Fenstergroesse gelten die Aufgaben, die viel Kontext brauchen
# (Kurator, Katalog im Chat), als machbar. Hermes3 mit 8K schafft sie nicht.
GROSSER_KONTEXT_AB = 16000

# Hoechstens so lange darf das Hauptmodell im Chat-Strom gar nichts schicken,
# wenn ein Rueckfall bereitsteht. Gemessen am 12.10.2026: Bei einem
# Cloud-Anbieter lieferten zwei Modelle 150 s lang kein einziges Byte. Mit der
# alten Grenze (480 s) haette der Chat acht Minuten gewartet, bevor der
# Rueckfall einspringt. Denk-Modelle stoert das nicht, solange sie ihre
# Denkphase mitstreamen (die Grenze gilt fuer Stille, nicht fuer die
# Gesamtdauer).
HAUPT_STILLE_S = 150.0

# Token-Zahlen der letzten Antwort in diesem Faden — fuer das KI-Protokoll.
# Faden-lokal, weil Kurator, Chat und Cron gleichzeitig laufen koennen.
_nutzung = threading.local()
# Welches Modell in diesem Faden zuletzt erfolgreich geantwortet hat — damit
# die Bewertung der Antwort (ki_protokoll.auswertung) beim richtigen Modell
# landet, auch wenn der Rueckfall eingesprungen ist.
_zuletzt = threading.local()


def zuletzt_genutzt() -> tuple[str, str]:
    """(Anbieter, Modell) des letzten erfolgreichen Aufrufs in diesem Faden."""
    return getattr(_zuletzt, "anbieter", ""), getattr(_zuletzt, "modell", "")


def _protokoll(aufgabe: str, rolle: str, v: Verbindung, modell: str, t0: float, *,
               fehler: BaseException | str | None = None,
               erstes_wort: float | None = None) -> None:
    """Einen Versuch ins KI-Protokoll schreiben (ki_protokoll.py). Wirft nie."""
    if fehler is None:
        _zuletzt.anbieter, _zuletzt.modell = v.anbieter, modell
    try:
        import ki_protokoll
        nutzung = getattr(_nutzung, "wert", None) or {}
        ki_protokoll.erfassen(
            aufgabe=aufgabe, rolle=rolle, anbieter=v.anbieter, modell=modell,
            ok=fehler is None, dauer_s=time.monotonic() - t0, fehler=fehler,
            erstes_wort_s=(erstes_wort - t0) if erstes_wort is not None else None,
            tokens_ein=nutzung.get("ein"), tokens_aus=nutzung.get("aus"),
            schluessel=v.schluessel)
    except Exception as e:
        log.warning("KI-Protokoll: %s", e)
    finally:
        _nutzung.wert = None

_THINK_BLOCK = re.compile(r"(?s)<think>.*?</think>")


class KeinModell(RuntimeError):
    """Kein Sprachmodell eingerichtet."""


# ---------------------------------------------------------------------------
# Welche Verbindung gilt
# ---------------------------------------------------------------------------

def _aus(abschnitt_name: str) -> tuple[Verbindung, dict] | None:
    a = settings.get(abschnitt_name)
    v = anbieter.verbindung(a)
    if not v or not (a.get("model") or "").strip():
        return None
    if not (anbieter.preset(v.anbieter) or {}).get("chat"):
        return None
    return v, a


def _haupt() -> tuple[Verbindung, dict] | None:
    return _aus("llm")


def _rueckfall() -> tuple[Verbindung, dict] | None:
    return _aus("llm_fallback")


def eingerichtet() -> bool:
    """Ein Hauptmodell ist eingerichtet."""
    return _haupt() is not None


def grosser_kontext() -> bool:
    """Hauptmodell mit genug Kontext fuer Kurator und Katalog im Chat."""
    h = _haupt()
    return bool(h) and int(h[1].get("context_tokens") or 0) >= GROSSER_KONTEXT_AB


def rueckfall_grosser_kontext() -> bool:
    """Rueckfallmodell mit genug Kontext fuer den Kurator.

    Bis Oktober 2026 war der Rueckfall immer das kleine lokale Hermes3 (8K),
    deshalb lief der Kurator ohne Rueckfall. Hat der Rueckfall genug Kontext
    (z. B. ein Cloud-Modell mit 32K), kann der Kurator bei einem Ausfall
    dorthin ausweichen statt auf die mechanische Auswahl zurueckzufallen.
    """
    r = _rueckfall()
    return bool(r) and int(r[1].get("context_tokens") or 0) >= GROSSER_KONTEXT_AB


def kontext_tokens() -> int:
    h = _haupt()
    return int(h[1].get("context_tokens") or 8192) if h else 8192


def active_model() -> str:
    h = _haupt() or _rueckfall()
    return h[1]["model"] if h else ""


def fast_model() -> str:
    """Schnelles Modell ohne Denkphase, falls eingetragen — sonst leer."""
    h = _haupt()
    return (h[1].get("fast_model") or "").strip() if h else ""


def _erreichbar(v: Verbindung) -> bool:
    # Cloud gilt als erreichbar, sobald eingerichtet — ein Ping pro Anfrage
    # wuerde bei nur einem Anfrageplatz selbst einen Platz belegen. Fehler
    # faengt der Aufruf ab und faellt auf den Rueckfall zurueck.
    return anbieter.ollama_erreichbar(v.url) if v.api == "ollama" else True


def is_available() -> bool:
    for wahl in (_haupt(), _rueckfall()):
        if wahl and _erreichbar(wahl[0]):
            return True
    return False


def vorwaermen() -> None:
    """Ein lokales Hauptmodell vorladen. Cloud-Modelle und ein Ollama, das
    nur Rueckfall ist, bleiben kalt — sonst laege es dauerhaft im RAM."""
    h = _haupt()
    if h and h[0].api == "ollama":
        ollama_client.warm(h[0].url, h[1]["model"], anbieter.kopfzeilen(h[0]))


def strip_reasoning(text: str) -> str:
    text = _THINK_BLOCK.sub("", text or "")
    # Ein nicht geschlossener Denkblock (Antwort abgeschnitten) zaehlt nicht als Antwort.
    if "<think>" in text:
        text = text.split("<think>", 1)[0]
    return text.strip()


# ---------------------------------------------------------------------------
# Anfrageplaetze und Eigenheiten je Modell
# ---------------------------------------------------------------------------

_slots: dict[tuple[str, int], threading.BoundedSemaphore] = {}
_slots_lock = threading.Lock()


def _slot_groesse(v: Verbindung) -> int:
    """Die Grenze gilt je Anbieterkonto. Teilt sich der Rueckfall die Adresse
    mit dem Hauptmodell, teilt er sich auch dessen Plaetze."""
    h = settings.get("llm")
    hv = anbieter.verbindung(h)
    if hv and hv.url == v.url:
        return max(1, int(h.get("parallel") or 1))
    return 4


def _slot(v: Verbindung) -> threading.BoundedSemaphore:
    schluessel = (v.url, _slot_groesse(v))
    with _slots_lock:
        if schluessel not in _slots:
            _slots[schluessel] = threading.BoundedSemaphore(schluessel[1])
        return _slots[schluessel]


# (Adresse, Modell) -> gelernte Anpassungen. Nur im Speicher: nach einem
# Neustart kostet das Wiederlernen je Modell eine zusaetzliche Anfrage.
_eigen: dict[tuple[str, str], dict] = {}

_KONTEXT_ZU_LANG = ("context length", "context_length", "maximum context", "context window",
                    "too many tokens", "prompt is too long", "reduce the length",
                    "input is too long", "tokens exceed", "token limit")
_NICHT_UNTERSTUETZT = ("unsupported", "not supported", "does not support", "only the default",
                       "only supports", "not allowed", "deprecated", "unrecognized",
                       "unknown", "not permitted", "extra inputs", "invalid")


def _ist_parallel_grenze(status: int, text: str) -> bool:
    return status in (403, 429) and "parallel" in (text or "").lower()


def _kontext_zu_lang(text: str) -> bool:
    t = (text or "").lower()
    return any(w in t for w in _KONTEXT_ZU_LANG)


def _aus_fehler_lernen(text: str, body: dict, eigen: dict, extra: dict | None) -> bool:
    """400-Meldung lesen und die naechste Anfrage anpassen. True, wenn sich
    etwas geaendert hat und ein neuer Versuch lohnt."""
    t = (text or "").lower()
    if _kontext_zu_lang(t):
        return False      # das regelt der Aufrufer (Prompt kuerzen)
    neu = False
    token_feld = "max_completion_tokens" if "max_completion_tokens" in body else "max_tokens"
    if "max_tokens" in t and "max_completion_tokens" in t and token_feld == "max_tokens":
        eigen["max_completion_tokens"] = True
        neu = True
    elif any(f in t for f in ("max_tokens", "max_completion_tokens", "max_output_tokens")):
        m = re.search(r"(?:at most|maximum(?: value)?(?: is)?|up to|<=|>)\s*(\d{3,7})", t)
        if m and int(m.group(1)) < int(body.get(token_feld) or 0):
            eigen["max_tokens_cap"] = int(m.group(1))
            neu = True
    if ("temperature" in t and "temperature" in body
            and any(w in t for w in _NICHT_UNTERSTUETZT)):
        eigen["ohne_temperature"] = True
        neu = True
    if "response_format" in body and any(w in t for w in ("response_format", "json_object",
                                                          "json mode")):
        eigen["ohne_response_format"] = True
        neu = True
    if (extra and not eigen.get("ohne_extra")
            and (any(k.lower() in t for k in extra)
                 or any(w in t for w in ("extra inputs", "unrecognized", "unknown parameter",
                                         "unexpected keyword", "extra_forbidden")))):
        eigen["ohne_extra"] = True
        neu = True
    return neu


def _ratenwartezeit(r: httpx.Response) -> float:
    nach = r.headers.get("retry-after")
    try:
        return min(RATE_WAIT_MAX_S, max(1.0, float(nach))) if nach else 5.0
    except ValueError:
        return 5.0


def _timeout(read: float) -> httpx.Timeout:
    return httpx.Timeout(connect=15.0, read=read, write=30.0, pool=15.0)


def _senden(v: Verbindung, url: str, body: dict, timeout: float,
            slot_warten: float = SLOT_WAIT_S) -> httpx.Response:
    """POST mit Anfrageplatz, Warten auf belegte Plaetze beim Anbieter und
    kurzem Warten bei Ratenbegrenzung."""
    slot = _slot(v)
    if not slot.acquire(timeout=slot_warten):
        raise TimeoutError(_t("kein_platz"))
    try:
        frist = time.monotonic() + slot_warten
        raten = 0
        while True:
            r = httpx.post(url, headers=anbieter.kopfzeilen(v), json=body,
                           timeout=_timeout(timeout))
            if _ist_parallel_grenze(r.status_code, r.text) and time.monotonic() < frist:
                log.info("%s: Anfrageplatz beim Anbieter belegt — neuer Versuch in %ss",
                         v.name, PARALLEL_RETRY_S)
                time.sleep(PARALLEL_RETRY_S)
                continue
            if r.status_code == 429 and raten < RATE_RETRIES:
                raten += 1
                warten = _ratenwartezeit(r)
                log.info("%s: Ratenbegrenzung — neuer Versuch in %.0f s", v.name, warten)
                time.sleep(warten)
                continue
            return r
    finally:
        slot.release()


def _fehler(v: Verbindung, r: httpx.Response) -> httpx.HTTPStatusError:
    return httpx.HTTPStatusError(anbieter.fehlertext(v, r.status_code, r.text),
                                 request=r.request, response=r)


# ---------------------------------------------------------------------------
# OpenAI-Format
# ---------------------------------------------------------------------------

def _openai_body(model, messages, temperature, max_tokens, stream, json_mode, extra, eigen):
    body = {"model": model, "messages": messages, "stream": stream}
    if not eigen.get("ohne_temperature"):
        body["temperature"] = temperature
    n = min(int(max_tokens), int(eigen.get("max_tokens_cap") or max_tokens))
    body["max_completion_tokens" if eigen.get("max_completion_tokens") else "max_tokens"] = n
    if json_mode and not eigen.get("ohne_response_format"):
        body["response_format"] = {"type": "json_object"}
    if extra and not eigen.get("ohne_extra"):
        body.update(extra)
    return body


def _openai_complete(v, model, messages, temperature, max_tokens, timeout, json_mode,
                     extra, slot_warten) -> str:
    eigen = _eigen.setdefault((v.url, model), {})
    t0 = time.monotonic()
    for _ in range(5):
        body = _openai_body(model, messages, temperature, max_tokens, False, json_mode,
                            extra, eigen)
        r = _senden(v, f"{v.url}/chat/completions", body, timeout, slot_warten)
        if r.status_code == 400:
            if _aus_fehler_lernen(r.text, body, eigen, extra):
                log.info("%s/%s: Anfrage angepasst nach 400 (%s)", v.name, model,
                         ", ".join(sorted(eigen)))
                continue
            if "response_format" in body and not _kontext_zu_lang(r.text):
                # Nicht jedes Modell kennt response_format — dann ohne erneut.
                json_mode = False
                continue
        break
    if r.status_code >= 400:
        raise _fehler(v, r)
    data = r.json()
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    usage = data.get("usage") or {}
    finish = choice.get("finish_reason")
    reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
    _nutzung.wert = {"ein": usage.get("prompt_tokens"), "aus": usage.get("completion_tokens")}
    log.info("%s %s: %s Prompt-, %s Antwort-Tokens (Denkanteil %d Zeichen), Ende=%s, %.0fs",
             v.name, model, usage.get("prompt_tokens"), usage.get("completion_tokens"),
             len(reasoning), finish, time.monotonic() - t0)
    text = strip_reasoning(msg.get("content") or "")
    if not text:
        raise ValueError(_t("leer_detail", ende=finish, tokens=usage.get("completion_tokens"),
                            denk=len(reasoning)))
    return text


def _openai_deltas(r: httpx.Response) -> Iterator[str]:
    for line in r.iter_lines():
        if not line or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            delta = (json.loads(payload).get("choices") or [{}])[0].get("delta") or {}
        except (json.JSONDecodeError, AttributeError, IndexError):
            continue
        # reasoning_content bleibt bewusst draussen — nur die Antwort zaehlt.
        if delta.get("content"):
            yield delta["content"]


def _openai_stream(v, model, messages, temperature, max_tokens, read_timeout) -> Iterator[str]:
    eigen = _eigen.setdefault((v.url, model), {})
    slot = _slot(v)
    if not slot.acquire(timeout=SLOT_WAIT_S):
        raise TimeoutError(_t("kein_platz"))
    try:
        frist = time.monotonic() + SLOT_WAIT_S
        raten = 0
        with httpx.Client(timeout=_timeout(read_timeout)) as c:
            for _ in range(20):
                body = _openai_body(model, messages, temperature, max_tokens, True, False,
                                    None, eigen)
                with c.stream("POST", f"{v.url}/chat/completions",
                              headers=anbieter.kopfzeilen(v), json=body) as r:
                    if r.status_code >= 400:
                        text = r.read().decode(errors="replace")
                        if _ist_parallel_grenze(r.status_code, text) and time.monotonic() < frist:
                            log.info("%s: Anfrageplatz belegt — neuer Versuch in %ss",
                                     v.name, PARALLEL_RETRY_S)
                            time.sleep(PARALLEL_RETRY_S)
                            continue
                        if r.status_code == 429 and raten < RATE_RETRIES:
                            raten += 1
                            time.sleep(_ratenwartezeit(r))
                            continue
                        if r.status_code == 400 and _aus_fehler_lernen(text, body, eigen, None):
                            continue
                        raise httpx.HTTPStatusError(anbieter.fehlertext(v, r.status_code, text),
                                                    request=r.request, response=r)
                    yield from _ohne_denken(_openai_deltas(r))
                    return
            raise TimeoutError(_t("anbieter_belegt"))
    finally:
        slot.release()


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

def _anthropic_body(model, messages, temperature, max_tokens, stream, json_mode, eigen):
    system = [m["content"] for m in messages if m.get("role") == "system"]
    verlauf: list[dict] = []
    for m in messages:
        if m.get("role") == "system":
            continue
        rolle = "assistant" if m.get("role") == "assistant" else "user"
        # Anthropic verlangt abwechselnde Rollen; zwei Nutzernachrichten in
        # Folge (Verlauf + neue Frage) werden zusammengelegt.
        if verlauf and verlauf[-1]["role"] == rolle:
            verlauf[-1]["content"] += "\n\n" + (m.get("content") or "")
        else:
            verlauf.append({"role": rolle, "content": m.get("content") or ""})
    if not verlauf or verlauf[0]["role"] != "user":
        verlauf.insert(0, {"role": "user", "content": "(Beginn des Gespraechs)"})
    text = "\n\n".join(s for s in system if s)
    if json_mode:
        text += ("\n\n" if text else "") + ("Antworte ausschliesslich mit einem gueltigen "
                                            "JSON-Objekt, ohne Text davor oder danach.")
    n = min(int(max_tokens), int(eigen.get("max_tokens_cap") or max_tokens))
    body = {"model": model, "max_tokens": n, "messages": verlauf, "stream": stream}
    if text:
        body["system"] = text
    if not eigen.get("ohne_temperature"):
        body["temperature"] = max(0.0, min(1.0, float(temperature)))
    return body


def _anthropic_complete(v, model, messages, temperature, max_tokens, timeout, json_mode,
                        slot_warten) -> str:
    eigen = _eigen.setdefault((v.url, model), {})
    t0 = time.monotonic()
    for _ in range(4):
        body = _anthropic_body(model, messages, temperature, max_tokens, False, json_mode, eigen)
        r = _senden(v, f"{v.url}/messages", body, timeout, slot_warten)
        if r.status_code == 400 and _aus_fehler_lernen(r.text, body, eigen, None):
            continue
        break
    if r.status_code >= 400:
        raise _fehler(v, r)
    data = r.json()
    teile = [b.get("text") or "" for b in data.get("content") or [] if b.get("type") == "text"]
    usage = data.get("usage") or {}
    _nutzung.wert = {"ein": usage.get("input_tokens"), "aus": usage.get("output_tokens")}
    log.info("%s %s: %s Prompt-, %s Antwort-Tokens, Ende=%s, %.0fs", v.name, model,
             usage.get("input_tokens"), usage.get("output_tokens"), data.get("stop_reason"),
             time.monotonic() - t0)
    text = strip_reasoning("".join(teile))
    if not text:
        raise ValueError(_t("leer_ende", ende=data.get("stop_reason")))
    return text


def _anthropic_deltas(r: httpx.Response) -> Iterator[str]:
    for line in r.iter_lines():
        if not line or not line.startswith("data:"):
            continue
        try:
            ereignis = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        art = ereignis.get("type")
        if art == "content_block_delta":
            delta = ereignis.get("delta") or {}
            # thinking_delta (erweitertes Denken) bleibt draussen.
            if delta.get("type") == "text_delta" and delta.get("text"):
                yield delta["text"]
        elif art == "error":
            fehler = ereignis.get("error") or {}
            raise AnbieterFehler(f"Anthropic: {fehler.get('type')}: {fehler.get('message')}")
        elif art == "message_stop":
            break


def _anthropic_stream(v, model, messages, temperature, max_tokens, read_timeout) -> Iterator[str]:
    eigen = _eigen.setdefault((v.url, model), {})
    slot = _slot(v)
    if not slot.acquire(timeout=SLOT_WAIT_S):
        raise TimeoutError(_t("kein_platz"))
    try:
        raten = 0
        with httpx.Client(timeout=_timeout(read_timeout)) as c:
            for _ in range(6):
                body = _anthropic_body(model, messages, temperature, max_tokens, True, False, eigen)
                with c.stream("POST", f"{v.url}/messages", headers=anbieter.kopfzeilen(v),
                              json=body) as r:
                    if r.status_code >= 400:
                        text = r.read().decode(errors="replace")
                        if r.status_code == 429 and raten < RATE_RETRIES:
                            raten += 1
                            time.sleep(_ratenwartezeit(r))
                            continue
                        if r.status_code == 400 and _aus_fehler_lernen(text, body, eigen, None):
                            continue
                        raise httpx.HTTPStatusError(anbieter.fehlertext(v, r.status_code, text),
                                                    request=r.request, response=r)
                    yield from _ohne_denken(_anthropic_deltas(r))
                    return
            raise TimeoutError(_t("anbieter_belegt"))
    finally:
        slot.release()


# ---------------------------------------------------------------------------
# Gemeinsam
# ---------------------------------------------------------------------------

def _ohne_denken(stuecke: Iterator[str]) -> Iterator[str]:
    """``<think>``-Bloecke aus einem Textstrom entfernen, auch wenn die Marken
    ueber mehrere Stuecke verteilt ankommen."""
    in_think = False
    pending = ""
    for text in stuecke:
        pending += text
        out = ""
        while pending:
            if in_think:
                end = pending.find("</think>")
                if end < 0:
                    pending = pending[-8:]
                    break
                pending = pending[end + len("</think>"):]
                in_think = False
            else:
                start = pending.find("<think>")
                if start < 0:
                    keep = 0
                    for k in range(1, 7):      # evtl. angeschnittene Marke am Ende
                        if pending.endswith("<think>"[:k]):
                            keep = k
                    out += pending[:len(pending) - keep]
                    pending = pending[len(pending) - keep:]
                    break
                out += pending[:start]
                pending = pending[start + len("<think>"):]
                in_think = True
        if out:
            yield out
    if pending and not in_think:
        yield pending


def _complete_mit(v: Verbindung, a: dict, model: str, messages: list[dict], *,
                  temperature: float, max_tokens: int, timeout: float, json_mode: bool,
                  extra: dict | None, slot_warten: float = SLOT_WAIT_S) -> str:
    if v.api == "ollama":
        return strip_reasoning(ollama_client.chat(
            v.url, model, messages, headers=anbieter.kopfzeilen(v),
            num_ctx=int(a.get("context_tokens") or 8192), temperature=temperature,
            timeout=timeout))
    if v.api == "anthropic":
        return _anthropic_complete(v, model, messages, temperature, max_tokens, timeout,
                                   json_mode, slot_warten)
    return _openai_complete(v, model, messages, temperature, max_tokens, timeout, json_mode,
                            extra, slot_warten)


def _stream_mit(v: Verbindung, a: dict, model: str, messages: list[dict], *,
                temperature: float, max_tokens: int, read_timeout: float) -> Iterator[str]:
    if v.api == "ollama":
        return _ohne_denken(ollama_client.chat_stream(
            v.url, model, messages, headers=anbieter.kopfzeilen(v),
            num_ctx=int(a.get("context_tokens") or 8192), temperature=temperature,
            read_timeout=read_timeout))
    if v.api == "anthropic":
        return _anthropic_stream(v, model, messages, temperature, max_tokens, read_timeout)
    return _openai_stream(v, model, messages, temperature, max_tokens, read_timeout)


# ---------------------------------------------------------------------------
# Oeffentliche Aufrufe
# ---------------------------------------------------------------------------

def complete(messages: list[dict], *, temperature: float = 0.6,
             max_tokens: int = 8000, timeout: float = 600.0,
             allow_fallback: bool = True, json_mode: bool = False,
             extra: dict | None = None, model: str | None = None,
             aufgabe: str = "sonstiges") -> str:
    """Komplette Antwort als Text (Denkanteil entfernt).

    ``extra`` geht unveraendert in den Anfrage-Body (z. B. ``reasoning_effort``);
    Anbieter, die es nicht kennen, bekommen es nach dem ersten 400 nicht mehr.
    ``model`` ersetzt das Hauptmodell (fuer das schnelle Modell).
    ``aufgabe`` benennt den Aufruf im KI-Protokoll (kurator, begruendung, ...)."""
    haupt = _haupt()
    fehler: Exception | None = None
    if haupt:
        v, a = haupt
        modell = model or a["model"]
        t0 = time.monotonic()
        _nutzung.wert = None
        try:
            text = _complete_mit(v, a, modell, messages, temperature=temperature,
                                 max_tokens=max_tokens, timeout=timeout,
                                 json_mode=json_mode, extra=extra)
            _protokoll(aufgabe, "haupt", v, modell, t0)
            return text
        except Exception as e:
            _protokoll(aufgabe, "haupt", v, modell, t0, fehler=e)
            log.warning("Sprachmodell %s (%s) fehlgeschlagen: %s", modell, v.name, e)
            if not allow_fallback:
                raise
            fehler = e
    elif not allow_fallback:
        raise KeinModell(_t("kein_hauptmodell"))

    rueck = _rueckfall()
    if rueck:
        v, a = rueck
        log.info("weiche auf das Rueckfallmodell %s (%s) aus", a["model"], v.name)
        # Das Antwortbudget hat der Aufrufer fuer das Hauptmodell berechnet
        # (Kurator: Kontext minus Prompt). Hat der Rueckfall ein kleineres
        # Fenster, wuerde er die Anfrage mit 400 ablehnen. Die Haelfte seines
        # Fensters laesst dem Prompt die andere Haelfte.
        fenster = int(a.get("context_tokens") or 0)
        budget = min(max_tokens, max(1000, fenster // 2)) if fenster else max_tokens
        t0 = time.monotonic()
        _nutzung.wert = None
        try:
            text = _complete_mit(v, a, a["model"], messages, temperature=temperature,
                                 max_tokens=budget, timeout=timeout,
                                 json_mode=json_mode, extra=None)
        except Exception as e:
            _protokoll(aufgabe, "rueckfall", v, a["model"], t0, fehler=e)
            raise
        _protokoll(aufgabe, "rueckfall", v, a["model"], t0)
        return text
    if fehler:
        raise fehler
    raise KeinModell(_t("kein_sprachmodell"))


def stream(messages: list[dict], *, temperature: float = 0.7,
           max_tokens: int = 8000, read_timeout: float = 600.0,
           allow_fallback: bool = True, aufgabe: str = "chat") -> Iterator[str]:
    """Liefert nur sichtbaren Antworttext. Faellt auf das Rueckfallmodell
    zurueck, solange noch kein Text ausgegeben wurde."""
    haupt = _haupt()
    fehler: Exception | None = None
    if haupt:
        v, a = haupt
        emitted = False
        erstes: float | None = None
        t0 = time.monotonic()
        # Nur mit Rueckfall verkuerzen — ohne ihn ist Warten besser als nichts.
        stille = min(read_timeout, HAUPT_STILLE_S) if (allow_fallback and _rueckfall()) \
            else read_timeout
        try:
            for chunk in _stream_mit(v, a, a["model"], messages, temperature=temperature,
                                     max_tokens=max_tokens, read_timeout=stille):
                if erstes is None:
                    erstes = time.monotonic()
                emitted = True
                yield chunk
            if not emitted:
                raise ValueError(_t("leer"))
            _protokoll(aufgabe, "haupt", v, a["model"], t0, erstes_wort=erstes)
            return
        except GeneratorExit:
            # Der Aufrufer hat den Strom beendet (Frist des Chats ueberschritten,
            # Faden abgebrochen) — fuer das Protokoll ein Aussetzer.
            _protokoll(aufgabe, "haupt", v, a["model"], t0, fehler="abgebrochen",
                       erstes_wort=erstes)
            raise
        except Exception as e:
            _protokoll(aufgabe, "haupt", v, a["model"], t0, fehler=e, erstes_wort=erstes)
            log.warning("Sprachmodell-Strom %s (%s) fehlgeschlagen: %s", a["model"], v.name, e)
            if emitted or not allow_fallback:
                raise
            fehler = e

    rueck = _rueckfall()
    if rueck:
        v, a = rueck
        log.info("weiche fuer den Chat auf das Rueckfallmodell %s (%s) aus", a["model"], v.name)
        erstes = None
        t0 = time.monotonic()
        try:
            for chunk in _stream_mit(v, a, a["model"], messages, temperature=temperature,
                                     max_tokens=max_tokens, read_timeout=read_timeout):
                if erstes is None:
                    erstes = time.monotonic()
                yield chunk
        except GeneratorExit:
            _protokoll(aufgabe, "rueckfall", v, a["model"], t0, fehler="abgebrochen",
                       erstes_wort=erstes)
            raise
        except Exception as e:
            _protokoll(aufgabe, "rueckfall", v, a["model"], t0, fehler=e, erstes_wort=erstes)
            raise
        _protokoll(aufgabe, "rueckfall", v, a["model"], t0,
                   fehler=None if erstes is not None else "leere_antwort", erstes_wort=erstes)
        return
    if fehler:
        raise fehler
    raise KeinModell(_t("kein_sprachmodell"))


def verbindung_testen(abschnitt: dict, modell: str | None = None) -> dict:
    """Eine kurze Frage an genau diese Verbindung, ohne Rueckfall.

    Wartet hoechstens 30 s auf einen freien Platz: laeuft gerade der Kurator
    ueber denselben Anbieter, soll der Test das sagen und nicht 15 Minuten
    haengen."""
    v = anbieter.verbindung(abschnitt)
    modell = (modell or abschnitt.get("model") or "").strip()
    if not v:
        raise AnbieterFehler(_t("verbindung_fehlt"))
    if not modell:
        raise AnbieterFehler(_t("modell_fehlt"))
    t0 = time.monotonic()
    _nutzung.wert = None
    # Denk-Modelle brauchen Spielraum, bevor sichtbarer Text kommt — mit
    # wenigen Tokens kaeme eine leere Antwort zurueck.
    try:
        antwort = _complete_mit(v, abschnitt, modell,
                                [{"role": "user", "content": "Antworte nur mit dem Wort OK."}],
                                temperature=0.0, max_tokens=2000, timeout=180.0,
                                json_mode=False, extra=None, slot_warten=30.0)
    except Exception as e:
        _protokoll("test", "test", v, modell, t0, fehler=e)
        # Am 11.10.2026 beim Test gesehen: der Kurator hielt den einzigen
        # Platz des ArliAI-Tarifs, und der Knopf zeigte die rohe 403 mit
        # "exceeded the maximum number of parallel requests". Das ist kein
        # Fehler der Einstellung — das soll die Meldung auch sagen.
        if (isinstance(e, httpx.HTTPStatusError) and e.response is not None
                and _ist_parallel_grenze(e.response.status_code, e.response.text)):
            raise AnbieterFehler(_t("test_belegt", name=v.name)) from e
        if isinstance(e, TimeoutError):
            raise AnbieterFehler(_t("test_kein_platz", name=v.name)) from e
        raise
    _protokoll("test", "test", v, modell, t0)
    return {"ok": True, "ms": int((time.monotonic() - t0) * 1000), "antwort": antwort[:200]}
