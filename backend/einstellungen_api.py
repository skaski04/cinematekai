"""Einrichtung und Einstellungen ueber die Oberflaeche.

Seit Oktober 2026 startet ein frischer Container ohne config.yaml. Alles, was
dort stand, wird im Browser eingetragen: zuerst im Einrichtungsassistenten
(/setup), danach im Admin-Bereich (/admin/einstellungen). Dieses Modul ist die
API dahinter. Die Pfade stehen hier ohne Praefix; server.py nimmt das ``/api``
der Oberflaeche ab, bevor die Anfrage bei FastAPI ankommt.

Drei Grundsaetze, die sich durch alles ziehen:

  * **Einrichtung nur, solange es niemanden gibt.** Die beiden /setup-Routen
    sind ohne Anmeldung erreichbar — anders bekaeme ein frischer Container nie
    einen ersten Admin. Sobald ein Nutzer existiert, sind sie zu. Sonst koennte
    jeder im Netz Jellyfin auf einen eigenen Server umbiegen.
  * **Passwoerter bleiben fluechtig.** Das Jellyfin-Passwort des Admins wird
    genau einmal an Jellyfin weitergereicht und weder gespeichert noch
    geloggt. Gespeichert wird nur der API-Schluessel, den Jellyfin dafuer
    ausstellt.
  * **Ein gespeicherter Schluessel geht nur dorthin, wofuer er eingetragen
    wurde** (siehe ``_schluessel_nach_regel``). Wer im Formular den Anbieter
    oder die Adresse aendert und "Modelle laden" klickt, hat noch nichts
    gespeichert — der alte ArliAI-Schluessel darf dabei nicht an OpenAI oder
    an einen Tippfehler-Host gehen.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlmodel import select

import anbieter
import jellyfin
import llm_client
import settings
import texte
import tmdb_client
from auth import COOKIE_NAME, require_admin, sitzung_anlegen, sitzungsdauer_tage
from database import User, get_session

log = logging.getLogger("cinematekai.einstellungen")

router = APIRouter()

# Abschnitte, deren Verbindung aus Anbieter + Adresse + Schluessel besteht.
_ANBIETER_ABSCHNITTE = ("llm", "llm_fallback", "embedding")

# Meldungen fuer die Oberflaeche. Erst beim Ausloesen gebaut (texte.waehle),
# in der Sprache der Anfrage — nie als Konstante beim Import.
_T = {
    "de": {
        "bereits_eingerichtet": "CinematekAI ist bereits eingerichtet.",
        "einbettung_laeuft": "Gerade läuft eine Einbettung. Bitte warten, bis sie fertig ist.",
        "zeitueberschreitung": "Zeitüberschreitung — der Dienst hat nicht rechtzeitig "
                               "geantwortet ({text}).",
        "einrichtung_laeuft": "Die Einrichtung läuft gerade schon. Warte einen Moment "
                              "und lade die Seite dann neu.",
        "zugangsdaten": "Bitte Benutzername und Passwort eines Jellyfin-Admins angeben.",
        "login_falsch": "Benutzername oder Passwort stimmt nicht.",
        "login_abgelehnt": "Jellyfin hat die Anmeldung abgelehnt (HTTP {status}).",
        "login_fehlgeschlagen": "Anmeldung bei Jellyfin fehlgeschlagen: {fehler}",
        "kein_jf_admin": "Für die Einrichtung brauchst du ein Jellyfin-Konto mit "
                         "Administratorrechten.",
        "keine_benutzer_id": "Jellyfin hat bei der Anmeldung keine Benutzer-ID geliefert.",
        "schluessel_defekt": "Der API-Schlüssel funktioniert nicht: {fehler}",
        "abschnitt": "Unbekannter Abschnitt {abschnitt!r}",
        "anbieter": "Unbekannter Anbieter {anbieter!r}.",
        "adresse_noetig": "Für {name} brauchst du eine Adresse.",
        "schluessel_noetig": "{name} braucht einen API-Schlüssel.",
        "keine_modellliste": "Für {abschnitt!r} gibt es keine Modellliste.",
        "keine_adresse": "Keine Adresse angegeben.",
        "kein_token": "Kein Token angegeben.",
        "tmdb_abgelehnt": "TMDB lehnt den Token ab. Gebraucht wird das lange "
                          "„Lese-Zugriffstoken“ (API Read Access Token).",
        "kein_test": "Für {abschnitt!r} gibt es keinen Test.",
    },
    "en": {
        "bereits_eingerichtet": "CinematekAI is already set up.",
        "einbettung_laeuft": "An embedding run is in progress. Please wait until it has finished.",
        "zeitueberschreitung": "Timeout — the service did not respond in time ({text}).",
        "einrichtung_laeuft": "Setup is already running. Wait a moment, "
                              "then reload the page.",
        "zugangsdaten": "Please enter the username and password of a Jellyfin admin.",
        "login_falsch": "Username or password is wrong.",
        "login_abgelehnt": "Jellyfin rejected the sign-in (HTTP {status}).",
        "login_fehlgeschlagen": "Signing in to Jellyfin failed: {fehler}",
        "kein_jf_admin": "Setup requires a Jellyfin account with administrator rights.",
        "keine_benutzer_id": "Jellyfin did not return a user ID on sign-in.",
        "schluessel_defekt": "The API key doesn't work: {fehler}",
        "abschnitt": "Unknown section {abschnitt!r}",
        "anbieter": "Unknown provider {anbieter!r}.",
        "adresse_noetig": "You need an address for {name}.",
        "schluessel_noetig": "{name} needs an API key.",
        "keine_modellliste": "There is no model list for {abschnitt!r}.",
        "keine_adresse": "No address given.",
        "kein_token": "No token given.",
        "tmdb_abgelehnt": "TMDB rejects the token. You need the long "
                          "\"API Read Access Token\".",
        "kein_test": "There is no test for {abschnitt!r}.",
    },
}


def _text(schluessel: str, **werte) -> str:
    return texte.waehle(_T, schluessel, **werte)


# ---------------------------------------------------------------------------
# Gemeinsame Helfer (auch von main.py genutzt)
# ---------------------------------------------------------------------------

def sitzungs_cookie_setzen(response: Response, token: str) -> None:
    """Sitzungs-Cookie setzen — fuer /auth/login und die Einrichtung gleich.

    An einer Stelle, damit beide Wege dieselben Parameter haben. Die
    Lebensdauer folgt der Sitzungsdauer aus den Einstellungen; vorher stand
    hier fest 30 Tage, und ein im Admin-Bereich verkuerzter Wert haette den
    Cookie laenger leben lassen als die Sitzung dahinter.
    """
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=sitzungsdauer_tage() * 86400,
        path="/",
    )


def _nutzer_vorhanden() -> bool:
    with get_session() as s:
        return s.exec(select(User.id).limit(1)).first() is not None


def _einbettung_eingerichtet() -> bool:
    """Einbettung vollstaendig eingetragen (ohne Erreichbarkeitsprobe).

    Bewusst nicht ``anbieter.einbettung_verfuegbar()``: /setup/status ist
    oeffentlich und wird bei jedem Seitenaufbau gefragt. Eine Probe an Ollama
    pro Aufruf waere unnoetiger Verkehr, und die Frage hier ist ohnehin "ist
    etwas eingetragen", nicht "laeuft es gerade".
    """
    abschnitt = settings.get("embedding")
    return bool(anbieter.verbindung(abschnitt) and (abschnitt.get("model") or "").strip())


def _fehlertext(e: Exception) -> str:
    """Lesbare Meldung fuer die Oberflaeche. Manche Ausnahmen (z. B.
    httpx.ReadTimeout) haben keinen Text — dann wenigstens die Art."""
    text = str(e).strip()
    if isinstance(e, httpx.HTTPStatusError) and text.startswith(("Client error", "Server error")):
        # Standardtext von raise_for_status (z. B. aus ollama_client): nennt
        # nur Status und Adresse plus einen Verweis auf MDN. Was wirklich
        # schiefging ("model 'x' not found"), steht in der Antwort.
        try:
            inhalt = (e.response.text or "").strip().replace("\n", " ")[:300]
        except Exception:
            inhalt = ""
        return f"HTTP {e.response.status_code}" + (f": {inhalt}" if inhalt else "")
    if isinstance(e, httpx.TimeoutException):
        return _text("zeitueberschreitung", text=text or type(e).__name__)
    return text or type(e).__name__


# ---------------------------------------------------------------------------
# Einrichtung (ohne Anmeldung, nur solange es keinen Nutzer gibt)
# ---------------------------------------------------------------------------

@router.get("/setup/status")
def setup_status():
    """Was ist eingerichtet? Oeffentlich: die Oberflaeche entscheidet damit
    vor der Anmeldung, ob sie zum Assistenten leitet."""
    nutzer = _nutzer_vorhanden()
    jf = settings.ist_eingerichtet()
    return {
        "eingerichtet": bool(jf and nutzer),
        "jellyfin": jf,
        "nutzer": nutzer,
        # Nur "eingetragen", keine Probe — siehe _einbettung_eingerichtet.
        "llm": llm_client.eingerichtet(),
        "einbettung": _einbettung_eingerichtet(),
        # Vorgabe-Sprache: fuer Anmelde- und Einrichtungsseite, solange der
        # Browser noch keine eigene Wahl gespeichert hat.
        "sprache": texte.vorgabe(),
    }


def _nur_vor_der_einrichtung() -> None:
    """Waechter fuer die /setup-Routen.

    Massstab ist, ob es schon einen CinematekAI-Nutzer gibt — nicht, ob
    Jellyfin eingetragen ist. Eine alte Installation, deren config.yaml
    uebernommen wurde, hat Jellyfin schon, aber vielleicht noch keinen Nutzer;
    eine eingerichtete hat immer einen Admin. Und solange keiner existiert,
    kann sich ohnehin niemand anmelden, um es im Admin-Bereich zu tun.
    """
    if _nutzer_vorhanden():
        raise HTTPException(403, _text("bereits_eingerichtet"))


# Nur eine Einrichtung zur Zeit. Ohne Sperre koennten zwei Anfragen (Doppel-
# klick, zweiter Tab, oder jemand anderes im Netz) beide den Waechter
# passieren, bevor eine den Nutzer angelegt hat — dann gaebe es zwei Admins
# und die Jellyfin-Einstellungen der zweiten ueberschrieben die der ersten.
_einrichtung_sperre = threading.Lock()


class JellyfinPruefenRequest(BaseModel):
    url: str


@router.post("/setup/jellyfin/pruefen")
def setup_jellyfin_pruefen(body: JellyfinPruefenRequest):
    """Antwortet unter der Adresse ein Jellyfin? Ohne Anmeldung bei Jellyfin."""
    _nur_vor_der_einrichtung()
    try:
        info = jellyfin.oeffentliche_info(body.url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "server_name": info["server_name"], "version": info["version"]}


class JellyfinEinrichtenRequest(BaseModel):
    url: str
    username: str
    password: str
    public_url: Optional[str] = None
    api_key: Optional[str] = None

    def __repr_args__(self):
        # Landet das Objekt je in einer Log-Zeile oder einem Traceback, sollen
        # Passwort und ein eingetragener Schluessel nicht mit darin stehen.
        # (Field(repr=False) waere der uebliche Weg, loest mit FastAPI 0.115
        # und neuem pydantic aber bei jeder Anfrage eine Warnung aus.)
        for name, wert in super().__repr_args__():
            yield name, ("•••" if name in ("password", "api_key") and wert else wert)


@router.post("/setup/jellyfin")
def setup_jellyfin(body: JellyfinEinrichtenRequest, response: Response):
    """Jellyfin verbinden und den ersten Admin anlegen und anmelden.

    Gespeichert wird erst, wenn alles geklappt hat: Erreichbarkeit, Anmeldung,
    Adminrecht, API-Schluessel. Sonst stuende nach einem Tippfehler im
    Passwort eine halbe Einrichtung in der Datenbank, und /setup/status
    meldete Jellyfin als verbunden.
    """
    if not _einrichtung_sperre.acquire(blocking=False):
        raise HTTPException(409, _text("einrichtung_laeuft"))
    try:
        _nur_vor_der_einrichtung()
        url = (body.url or "").strip().rstrip("/")
        username = (body.username or "").strip()
        if not username or not body.password:
            raise HTTPException(400, _text("zugangsdaten"))

        # 1. Antwortet dort ueberhaupt ein Jellyfin?
        try:
            info = jellyfin.oeffentliche_info(url)
        except ValueError as e:
            raise HTTPException(400, str(e))

        # 2. Anmelden. Das Passwort geht nur hier an Jellyfin und sonst nirgends
        #    hin — keine Log-Zeile, kein Speichern.
        try:
            jf = jellyfin.authenticate(username, body.password, url)
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (400, 401, 403):
                raise HTTPException(401, _text("login_falsch"))
            raise HTTPException(400, _text("login_abgelehnt", status=e.response.status_code))
        except ValueError as e:
            raise HTTPException(400, str(e))
        except httpx.HTTPError as e:
            raise HTTPException(400, _text("login_fehlgeschlagen", fehler=_fehlertext(e)))

        # 3. Nur ein Jellyfin-Admin darf CinematekAI einrichten — dieselbe Regel
        #    wie beim Bootstrap in auth.login. Er bekommt die Adminrechte hier
        #    und muss einen API-Schluessel anlegen duerfen.
        if not jf["is_jf_admin"]:
            raise HTTPException(403, _text("kein_jf_admin"))
        if not jf["jellyfin_user_id"]:
            raise HTTPException(400, _text("keine_benutzer_id"))

        # 4. API-Schluessel: selbst eingetragen pruefen oder von Jellyfin anlegen lassen.
        eigener = (body.api_key or "").strip()
        try:
            if eigener:
                jellyfin.nutzer_liste(url, eigener)
                api_key = eigener
            else:
                api_key = jellyfin.api_schluessel_anlegen(url, jf["access_token"])
        except ValueError as e:
            if eigener:
                raise HTTPException(400, _text("schluessel_defekt", fehler=e))
            raise HTTPException(400, str(e))

        # Kurz vor dem Schreiben noch einmal: die Sperre schuetzt nur gegen
        # eine zweite Einrichtung, nicht gegen den Bootstrap in /auth/login
        # (moeglich, wenn eine alte config.yaml Jellyfin schon eingetragen hat).
        _nur_vor_der_einrichtung()

        # 5. Einstellungen. Die Einrichtung legt den ganzen Abschnitt fest;
        #    die Bibliothek wird zunaechst ueber das Konto des Admins gelesen
        #    (im Admin-Bereich spaeter aenderbar).
        try:
            settings.speichern("jellyfin", {
                "url": url,
                "public_url": (body.public_url or "").strip(),
                "api_key": api_key,
                "user_id": jf["jellyfin_user_id"],
            })
        except settings.EinstellungFehler as e:
            raise HTTPException(400, str(e))

        # 6. Erster Admin, angemeldet. Eigene Datenbanksitzung erst nach dem
        #    Speichern oben: SQLite kennt nur einen Schreiber, und eine offene
        #    Schreibtransaktion hier liesse settings.speichern warten.
        with get_session() as s:
            user = User(
                jellyfin_user_id=jf["jellyfin_user_id"],
                jellyfin_username=username,
                display_name=jf["display_name"],
                is_admin=True,
                last_login_at=datetime.utcnow(),
            )
            s.add(user)
            s.commit()
            s.refresh(user)
            user_id, display_name = user.id, user.display_name
        token = sitzung_anlegen(user_id)
        sitzungs_cookie_setzen(response, token)
        log.info("Einrichtung: Jellyfin %r (%s) verbunden, Admin %r angelegt",
                 info["server_name"], info["version"], username)
        return {"ok": True, "server_name": info["server_name"],
                "display_name": display_name, "is_admin": True}
    finally:
        _einrichtung_sperre.release()


# ---------------------------------------------------------------------------
# Einstellungen (nur Admins)
# ---------------------------------------------------------------------------

@router.get("/admin/settings")
def admin_settings(admin: User = Depends(require_admin)):
    # Name und Hinweis der Anbieter in der Sprache der Anfrage.
    return {"einstellungen": settings.fuer_oberflaeche(),
            "anbieter": anbieter.fuer_oberflaeche()}


@router.put("/admin/settings/{abschnitt}")
def admin_settings_speichern(abschnitt: str, werte: dict = Body(...),
                             admin: User = Depends(require_admin)):
    """Felder eines Abschnitts aendern. Geheimfelder: leer = behalten,
    ``null`` = loeschen (siehe settings.speichern)."""
    if abschnitt not in settings.STANDARD:
        raise HTTPException(400, _text("abschnitt", abschnitt=abschnitt))
    einbettung = None
    if abschnitt == "embedding":
        # Import erst hier: embeddings zieht numpy und den Vektorspeicher
        # nach, und die uebrigen Abschnitte brauchen es nicht.
        import embeddings
        alt = (settings.get("embedding")["model"] or "").strip()
        neu = str(werte.get("model") or "").strip() if "model" in werte else alt
        # Ein Modellwechsel mitten im Lauf schaltete die Sammlung unter dem
        # laufenden Einbetten um — die halbe Bibliothek landete im falschen
        # Vektorraum.
        if neu != alt and embeddings.einbettung_laeuft():
            raise HTTPException(409, _text("einbettung_laeuft"))
    try:
        settings.speichern(abschnitt, werte)
    except settings.EinstellungFehler as e:
        raise HTTPException(400, str(e))
    if abschnitt == "embedding":
        # Nach dem Speichern: der Beobachter in embeddings hat die Sammlung
        # dann schon gewechselt und ggf. das Einbetten gestartet.
        einbettung = embeddings.einbettung_status()
    return {"abschnitt": settings.fuer_oberflaeche()[abschnitt], "einbettung": einbettung}


def _effektive_url(provider: str, url: Optional[str]) -> str:
    """Adresse, an die tatsaechlich gesendet wird: leer = die des Anbieters."""
    p = anbieter.preset(provider) or {}
    return ((url or "").strip() or p.get("url") or "").strip().rstrip("/")


def _schluessel_nach_regel(abschnitt: str, provider: str, url: Optional[str],
                           eingegeben: Optional[str]) -> str:
    """Welcher Schluessel fuer einen Test oder die Modellliste gilt.

    Eingegeben gewinnt. Sonst der gespeicherte, aber NUR, wenn Anbieter und
    effektive Adresse genau die gespeicherten sind — sonst ohne Schluessel.
    Ein Abgleich nur der Adresse reichte nicht: "custom" und "openai" koennen
    dieselbe haben, verlangen aber unterschiedliche Kopfzeilen (Azure).
    """
    if eingegeben and eingegeben.strip():
        return eingegeben.strip()
    gespeichert = settings.get(abschnitt)
    if not gespeichert.get("api_key"):
        return ""
    if (provider or "") != (gespeichert.get("provider") or ""):
        return ""
    ziel = _effektive_url(provider, url)
    if not ziel or ziel != _effektive_url(gespeichert["provider"], gespeichert.get("url")):
        return ""
    return gespeichert["api_key"]


class ModelleRequest(BaseModel):
    abschnitt: str
    provider: str
    url: Optional[str] = None
    api_key: Optional[str] = None


def _verbindung_aus(abschnitt: str, provider: str, url: Optional[str],
                    api_key: Optional[str]) -> anbieter.Verbindung:
    """Verbindung aus Formularwerten bauen; ``ValueError`` mit Meldung, wenn
    etwas fehlt."""
    p = anbieter.preset(provider)
    if not p:
        raise ValueError(_text("anbieter", anbieter=provider))
    schluessel = _schluessel_nach_regel(abschnitt, provider, url, api_key)
    v = anbieter.verbindung({"provider": provider, "url": (url or "").strip(),
                             "api_key": schluessel})
    if v:
        return v
    if not _effektive_url(provider, url):
        raise ValueError(_text("adresse_noetig", name=anbieter.anzeigename(provider)))
    raise ValueError(_text("schluessel_noetig", name=anbieter.anzeigename(provider)))


@router.post("/admin/settings/modelle")
def admin_settings_modelle(body: ModelleRequest, admin: User = Depends(require_admin)):
    """Modellliste des Anbieters — fuer die Vorschlaege im Formular."""
    if body.abschnitt not in _ANBIETER_ABSCHNITTE:
        raise HTTPException(400, _text("keine_modellliste", abschnitt=body.abschnitt))
    try:
        v = _verbindung_aus(body.abschnitt, body.provider, body.url, body.api_key)
        namen = anbieter.modelle(v)
    except (ValueError, anbieter.AnbieterFehler) as e:
        raise HTTPException(400, _fehlertext(e))
    return {"modelle": namen}


class TestenRequest(BaseModel):
    abschnitt: str
    provider: Optional[str] = None
    url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    fast_model: Optional[str] = None
    bearer: Optional[str] = None


def _formular_abschnitt(body: TestenRequest) -> dict:
    """Formularwerte ueber den gespeicherten Abschnitt legen.

    Was im Formular fehlt (``None``), kommt aus dem Gespeicherten — aber nur
    beim selben Anbieter. Nach einem Anbieterwechsel gehoeren Adresse und
    Modell des alten nicht zum neuen.
    """
    gespeichert = settings.get(body.abschnitt)
    provider = body.provider if body.provider is not None else gespeichert["provider"]
    gleich = provider == gespeichert["provider"]

    def wert(eingabe: Optional[str], feld: str) -> str:
        if eingabe is not None:
            return eingabe.strip()
        return (gespeichert.get(feld) or "") if gleich else ""

    a = dict(gespeichert)
    a["provider"] = provider
    a["url"] = wert(body.url, "url")
    a["model"] = wert(body.model, "model")
    if "fast_model" in gespeichert:
        a["fast_model"] = wert(body.fast_model, "fast_model")
    a["api_key"] = _schluessel_nach_regel(body.abschnitt, provider, a["url"], body.api_key)
    return a


def _llm_testen(a: dict, modell: Optional[str] = None) -> dict:
    try:
        return llm_client.verbindung_testen(a, modell)
    except Exception as e:
        return {"ok": False, "fehler": _fehlertext(e)}


def _jellyfin_testen(body: TestenRequest) -> dict:
    gespeichert = settings.get("jellyfin")
    url = (body.url or "").strip().rstrip("/") or gespeichert["url"]
    if not url:
        return {"ok": False, "fehler": _text("keine_adresse")}
    t0 = time.monotonic()
    info = jellyfin.oeffentliche_info(url)
    # Ohne eingegebenen Schluessel nimmt nutzer_liste den gespeicherten —
    # und zwar nur fuer die gespeicherte Adresse (Regel dort).
    konten = jellyfin.nutzer_liste(url, (body.api_key or "").strip() or None)
    return {"ok": True, "ms": int((time.monotonic() - t0) * 1000),
            "server_name": info["server_name"], "version": info["version"],
            "nutzer_anzahl": len(konten)}


def _tmdb_testen(body: TestenRequest) -> dict:
    # TMDB hat nur eine Adresse; der gespeicherte Token kann also nicht an
    # einen falschen Ort gehen.
    bearer = (body.bearer or "").strip() or settings.get("tmdb")["bearer"]
    if not bearer:
        return {"ok": False, "fehler": _text("kein_token")}
    t0 = time.monotonic()
    if tmdb_client.token_pruefen(bearer):
        return {"ok": True, "ms": int((time.monotonic() - t0) * 1000)}
    return {"ok": False, "fehler": _text("tmdb_abgelehnt")}


@router.post("/admin/settings/testen")
def admin_settings_testen(body: TestenRequest, admin: User = Depends(require_admin)):
    """Verbindung mit den Formularwerten pruefen, ohne zu speichern.

    Antwortet IMMER mit 200 und ``ok`` true/false: ein fehlgeschlagener Test
    ist ein Ergebnis, kein Fehler der Anfrage. So kann die Oberflaeche die
    Meldung des Anbieters direkt neben den Knopf schreiben.
    """
    try:
        if body.abschnitt in ("llm", "llm_fallback"):
            a = _formular_abschnitt(body)
            ergebnis = _llm_testen(a)
            schnell = (a.get("fast_model") or "").strip() if body.abschnitt == "llm" else ""
            if schnell:
                ergebnis["schnell"] = _llm_testen(a, schnell)
            return ergebnis
        if body.abschnitt == "embedding":
            return anbieter.einbettung_testen(_formular_abschnitt(body))
        if body.abschnitt == "jellyfin":
            return _jellyfin_testen(body)
        if body.abschnitt == "tmdb":
            return _tmdb_testen(body)
        return {"ok": False, "fehler": _text("kein_test", abschnitt=body.abschnitt)}
    except Exception as e:
        return {"ok": False, "fehler": _fehlertext(e)}


@router.get("/admin/jellyfin/nutzer")
def admin_jellyfin_nutzer(admin: User = Depends(require_admin)):
    """Jellyfin-Konten — fuer die Auswahl des Bibliothekskontos."""
    try:
        return jellyfin.nutzer_liste()
    except ValueError as e:
        raise HTTPException(502, str(e))


# ---------------------------------------------------------------------------
# Einbettung (nur Admins)
# ---------------------------------------------------------------------------
# Importe innerhalb der Funktionen: embeddings zieht numpy und den
# Vektorspeicher nach, und dieses Modul soll auch ohne (etwa in Tests)
# importierbar bleiben.

@router.get("/admin/ki-protokoll")
def admin_ki_protokoll(tage: int = Query(30, ge=1, le=180), admin: User = Depends(require_admin)):
    """Auswertung des KI-Protokolls: Erfolgsquote, Dauer, Rueckfall, letzte Fehler
    (siehe ki_protokoll.py). Fuer die Admin-Seite und fuer Reviews."""
    import ki_protokoll
    return ki_protokoll.zusammenfassung(tage)


@router.get("/admin/einbettung")
def admin_einbettung(admin: User = Depends(require_admin)):
    import embeddings
    return embeddings.einbettung_status()


@router.post("/admin/einbettung/starten")
def admin_einbettung_starten(force: bool = Query(False), admin: User = Depends(require_admin)):
    """Einbetten im Hintergrund starten; der Fortschritt steht in /admin/einbettung."""
    import embeddings
    return embeddings.einbettung_starten(force)


@router.post("/admin/einbettung/kalibrieren")
def admin_einbettung_kalibrieren(admin: User = Depends(require_admin)):
    """Passungs- und Stimmungsskala fuer das aktuelle Modell neu messen."""
    import embeddings
    # Waehrend eines Laufs fehlt einem Teil der Bibliothek noch der Vektor;
    # die Messung bekaeme eine schiefe Verteilung und speicherte sie als
    # "gemessen".
    if embeddings.einbettung_laeuft():
        raise HTTPException(409, _text("einbettung_laeuft"))
    try:
        return {"kalibrierung": embeddings.kalibrieren()}
    except ValueError as e:
        raise HTTPException(400, str(e))
