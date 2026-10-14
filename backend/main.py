"""CinematekAI Backend — Phase 2 with multi-user, characters, collections."""
import functools, json, logging, os, threading
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import FastAPI, HTTPException, Query, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, Response, FileResponse
from sqlmodel import select, or_
from sqlalchemy import literal
from pydantic import BaseModel

# Logging VOR den eigenen Modulen einrichten: schon beim Import lesen einige
# von ihnen die Einstellungen (embeddings z. B. das Einbettungsmodell), und
# der allererste Lesezugriff uebernimmt einmalig die alte config.yaml. Ohne
# Empfaenger ginge genau diese Meldung verloren — man saehe im Log nie, ob
# und was uebernommen wurde.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("cinematekai")

import anbieter
import config
import einstellungen_api
import texte
import jellyfin
import llm_client
import settings
import sichtbarkeit
from database import (init_db, get_session, Movie, Series, SeriesProgress, Episode,
                      Collection, WatchState, SyncState, User, UserSession,
                      Watchlist, NotInterested)
from embeddings import normalize_user_query, passung_prozent
from jellyfin import (open_image, ImageNotFound, KeinBild, ist_item_id,
                      trailer_pfad, lookup_jellyfin_user,
                      set_favorite as jf_set_favorite, set_rating as jf_set_rating,
                      fetch_series_episodes)
from sync import sync_library, sync_watch_state, sync_series_progress
from auth import (
    login as auth_login, logout as auth_logout, COOKIE_NAME,
    require_user, require_admin, get_current_user_or_none,
)
from einstellungen_api import sitzungs_cookie_setzen

# Meldungen fuer die Oberflaeche (Fehlerantworten, Hinweise im Admin-Bereich).
# Erst beim Ausloesen gebaut (texte.waehle), in der Sprache der Anfrage.
# Die knappen Technikmeldungen, die schon vorher englisch waren
# ("movie not found"), stehen in der deutschen Tabelle unveraendert.
_T = {
    "de": {
        "nicht_eingerichtet": "CinematekAI ist noch nicht eingerichtet. Ein Admin richtet es "
                              "unter /setup ein.",
        "kein_modell": "Kein Sprachmodell eingerichtet (Admin → Einstellungen).",
        "jf_benutzer_fehlt": "Jellyfin-Benutzer '{name}' nicht gefunden",
        "schon_allowlist": "Benutzer bereits auf der Allowlist",
        "selbst_entfernen": "Du kannst dich nicht selbst entfernen",
        "einziger_admin": "Das ist der einzige Admin — erst einen anderen User zum Admin "
                          "machen, dann diesen entfernen.",
        "letzter_admin": "Das ist der letzte Admin — dieses Recht kann nicht entzogen "
                         "werden, solange kein anderer Admin existiert.",
        "sync": "sync failed: {fehler}",
        "sync_watch": "watch-state sync failed: {fehler}",
        "sync_serien": "series-progress sync failed: {fehler}",
        "film_fehlt": "movie not found",
        "serie_fehlt": "series not found",
        "fortschritt_fehlt": "series progress not found",
        "bewertung_bereich": "rating muss zwischen 0 und 10 liegen",
        "medientyp": "media_type muss 'movie' oder 'series' sein",
        "entscheidung": "decision must be 'keep' or 'drop'",
        "jellyfin_abruf": "jellyfin fetch failed: {fehler}",
        "trailer_suche": "trailer lookup failed",
        "kein_trailer": "no trailer",
        "bild_fehlt": "image not available",
        "bild_fehler": "image fetch failed",
    },
    "en": {
        "nicht_eingerichtet": "CinematekAI is not set up yet. An admin sets it up at /setup.",
        "kein_modell": "No language model set up (Admin → Settings).",
        "jf_benutzer_fehlt": "Jellyfin user '{name}' not found",
        "schon_allowlist": "User is already on the allowlist",
        "selbst_entfernen": "You can't remove yourself",
        "einziger_admin": "This is the only admin — make another user an admin first, "
                          "then remove this one.",
        "letzter_admin": "This is the last admin — the admin role can't be removed "
                         "until another admin exists.",
        "sync": "Sync failed: {fehler}",
        "sync_watch": "Watch-state sync failed: {fehler}",
        "sync_serien": "Series-progress sync failed: {fehler}",
        "film_fehlt": "Movie not found",
        "serie_fehlt": "Series not found",
        "fortschritt_fehlt": "Series progress not found",
        "bewertung_bereich": "Rating must be between 0 and 10",
        "medientyp": "media_type must be 'movie' or 'series'",
        "entscheidung": "Decision must be 'keep' or 'drop'",
        "jellyfin_abruf": "Jellyfin fetch failed: {fehler}",
        "trailer_suche": "Trailer lookup failed",
        "kein_trailer": "No trailer",
        "bild_fehlt": "Image not available",
        "bild_fehler": "Image fetch failed",
    },
}


def _text(schluessel: str, **werte) -> str:
    return texte.waehle(_T, schluessel, **werte)


def _mit_sicht(endpunkt):
    """Fuer Endpunkte, die neben dem eigenen Filter noch Module aufrufen, die
    die Sichtbarkeit selbst laden (Profil, Empfehlungen): die Menge des
    angemeldeten Nutzers wird so fuer die ganze Anfrage einmal geholt (siehe
    sichtbarkeit.fuer_aufruf). Steht unter ``@app.get``; FastAPI liest die
    Parameter ueber functools.wraps vom eigentlichen Endpunkt."""
    @functools.wraps(endpunkt)
    def huelle(*args, **kwargs):
        user = kwargs.get("user")
        with sichtbarkeit.fuer_aufruf(getattr(user, "id", None)):
            return endpunkt(*args, **kwargs)
    return huelle


def _jellyfin_browser_url() -> str:
    """Adresse, unter der der BROWSER Jellyfin erreicht (leer = wie die
    Backend-Adresse). Bei jedem Aufruf gelesen, damit eine Aenderung im
    Admin-Bereich sofort in allen Links steckt."""
    jf = settings.get("jellyfin")
    return (jf["public_url"] or jf["url"]).rstrip("/")


def oeffentlicher_link(item_id: str) -> str:
    """Jellyfin-Deeplink fuer den BROWSER des Nutzers.

    `jellyfin.web_deep_link` baut den Link aus der Backend-Adresse
    (``jellyfin.url``) — und unter der erreicht nur das Backend Jellyfin
    (Containernetz bzw. LAN-IP). Im Browser des Nutzers fuehrte der Knopf damit
    ins Leere, sobald er nicht zufaellig im selben Netz sass. API-Aufrufe an
    Jellyfin laufen unveraendert ueber ``url``; nur was der Nutzer anklickt,
    bekommt ``public_url``.

    Die Pfadform steht bewusst hier und nicht in jellyfin.py — dort werden
    auch die API-Adressen gebaut, und die duerfen sich nicht mitverschieben.
    """
    return f"{_jellyfin_browser_url()}/web/#/details?id={item_id}"

# Die automatische API-Doku (/api/docs, /api/redoc, /api/openapi.json) ist
# standardmaessig aus. Sie listet jede Route samt Parametern fuer jeden, der
# die Seite erreicht - ohne Anmeldung. Fuer die Oberflaeche wird sie nicht
# gebraucht; wer an der API arbeitet, schaltet sie mit CINEMATEKAI_API_DOCS=1
# ein. Gelesen beim Start, weil FastAPI die Routen dafuer beim Anlegen der
# App einrichtet.
_API_DOKU = (os.environ.get("CINEMATEKAI_API_DOCS") or "").strip().lower() in (
    "1", "true", "yes", "on", "ja")

app = FastAPI(
    title="CinematekAI Backend",
    version=config.VERSION,
    docs_url="/docs" if _API_DOKU else None,
    redoc_url="/redoc" if _API_DOKU else None,
    openapi_url="/openapi.json" if _API_DOKU else None,
)

# CORS nur, wenn ausdruecklich verlangt. Oberflaeche und API kommen seit
# Oktober 2026 aus demselben Prozess und von derselben Adresse (server.py),
# und beim Entwickeln reicht der Vite-Server /api per Proxy weiter - der
# Browser fragt also immer nur den eigenen Ursprung, fuer den CORS gar nicht
# greift. Bis 1.0.0 war trotzdem ein Muster fuer alle privaten Netze und
# lokalen Namen voreingestellt: damit durfte jede Seite aus dem Heimnetz
# (etwa ein anderer selbst gehosteter Dienst mit einer Luecke) Anfragen mit
# dem Sitzungs-Cookie stellen und die Antworten lesen. Wer die Oberflaeche
# wirklich unter einer anderen Adresse betreibt als die API, setzt
# CINEMATEKAI_CORS_REGEX auf genau diese Adresse.
_CORS_REGEX = (os.environ.get("CINEMATEKAI_CORS_REGEX") or "").strip()
if _CORS_REGEX:
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=_CORS_REGEX,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


class _SpracheJeAnfrage:
    """Sprache der Anfrage aus dem Kopf ``X-Sprache`` in texte.py ablegen.

    Reine ASGI-Middleware statt BaseHTTPMiddleware: die vertraegt sich nicht
    gut mit gestreamten Antworten (Chat per SSE). Die ContextVar gilt fuer
    alles, was waehrend dieser Anfrage laeuft — auch fuer synchrone Endpunkte
    im Thread-Pool und fuer das Ausliefern eines Stroms, weil Starlette den
    Kontext dorthin mitnimmt. Fehlt der Kopf (curl, Monitoring), gilt die
    Vorgabe aus den Einstellungen.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        code = None
        for name, wert in scope.get("headers") or []:
            if name == b"x-sprache":
                code = wert.decode("latin-1")
                break
        token = texte.setzen(code)
        try:
            await self.app(scope, receive, send)
        finally:
            texte.zuruecksetzen(token)


app.add_middleware(_SpracheJeAnfrage)


# Fehlende Einrichtung ist kein Absturz. Ohne diese Handler kaeme aus jedem
# Endpunkt, der Jellyfin oder ein Sprachmodell braucht, eine nackte 500 —
# und die Oberflaeche koennte nicht sagen, was zu tun ist. 503, weil der
# Dienst grundsaetzlich da ist, nur noch nicht benutzbar.
@app.exception_handler(jellyfin.NichtEingerichtet)
async def _nicht_eingerichtet(request: Request, exc: jellyfin.NichtEingerichtet):
    return JSONResponse(status_code=503, content={"detail": _text("nicht_eingerichtet")})


@app.exception_handler(llm_client.KeinModell)
async def _kein_modell(request: Request, exc: llm_client.KeinModell):
    return JSONResponse(status_code=503, content={"detail": _text("kein_modell")})


@app.exception_handler(settings.EinstellungFehler)
async def _einstellung_fehler(request: Request, exc: settings.EinstellungFehler):
    # Die Meldungen aus settings.py sind fuer die Oberflaeche geschrieben.
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# Einrichtung (/setup/...) und Einstellungen (/admin/settings/...,
# /admin/einbettung/...) liegen in einem eigenen Modul — main.py ist lang genug.
app.include_router(einstellungen_api.router)


@app.on_event("startup")
def _startup():
    init_db()
    # Muss nach init_db laufen: create_all legt neue Tabellen an, aendert aber
    # nie bestehende Daten oder Spalten. Genau das machen die Migrationen.
    try:
        from migrations import run_migrations
        res = run_migrations()
        if res["count"]:
            for line in res["applied"]:
                log.info("Migration angewandt: %s", line)
    except Exception as e:
        log.error("Migrationen fehlgeschlagen: %s", e)
    # Vektorspeicher: einen Export der Chroma-Vektoren von 1.0.x uebernehmen
    # und die Marken 'embedded' angleichen (embeddings.beim_start). Nach den
    # Migrationen (Tabelle aus 007) und vor Vorwaermen und Zeitplan, die sonst
    # einen noch nicht uebernommenen Bestand laesen. Ein Fehler hier haelt den
    # Start nicht auf: schlimmstenfalls wird neu eingebettet.
    try:
        import embeddings
        embeddings.beim_start()
    except Exception as e:
        log.error("Vektorspeicher: Startschritt fehlgeschlagen: %s", e)
    log.info("CinematekAI backend started")
    # Einstellungen einmal ausdruecklich lesen. Der erste Zugriff uebernimmt
    # bei einer alten Installation die config.yaml; meist ist das beim Import
    # schon passiert, aber so steht es in jedem Fall frueh im Log und nicht
    # erst beim ersten Seitenaufruf.
    jf = settings.get("jellyfin")
    eingerichtet = settings.ist_eingerichtet()
    if eingerichtet:
        log.info("Jellyfin: %s", jf["url"])
    else:
        log.info("CinematekAI ist noch nicht eingerichtet — ein Admin richtet es "
                 "im Browser unter /setup ein")
    if llm_client.eingerichtet():
        log.info("Sprachmodell: %s (%s)", llm_client.active_model(),
                 settings.get("llm")["provider"])
    elif llm_client.active_model():
        log.info("Sprachmodell: nur Rueckfallmodell %s eingerichtet", llm_client.active_model())
    else:
        log.info("Sprachmodell: kein Sprachmodell eingerichtet")
    # Ein lokales Hauptmodell vorladen (Cloud und reiner Rueckfall bleiben
    # kalt, das entscheidet llm_client). Im eigenen Faden: das Laden kann
    # Minuten dauern und soll den Start nicht aufhalten.
    threading.Thread(target=llm_client.vorwaermen, daemon=True,
                     name="sprachmodell-vorwaermen").start()
    # Passungswerte vorwaermen: nach einem Neustart ist der Speicher leer, und
    # der erste Seitenaufruf brauchte sonst 2,75 s. Im eigenen Faden, weil das
    # Laden der Vektoren den Start sonst genauso lange aufhielte. Kommt ein
    # Aufruf dazwischen, wartet er auf diese Rechnung statt eine zweite zu
    # beginnen (siehe embeddings.profile_all_similarities). Ohne Einrichtung
    # gibt es weder Bibliothek noch Nutzer — dann nichts zu rechnen.
    def _vorwaermen():
        try:
            from embeddings import aehnlichkeiten_vorwaermen
            with get_session() as s:
                ids = [u.id for u in s.exec(select(User)).all()]
            erg = aehnlichkeiten_vorwaermen(ids)
            log.info("Passungswerte vorgewaermt: %s", erg)
        except Exception as e:
            log.warning("Vorwaermen der Passungswerte fehlgeschlagen: %s", e)
    if eingerichtet:
        threading.Thread(target=_vorwaermen, daemon=True).start()
    # APScheduler — Tageslauf zur Uhrzeit und in der Zeitzone aus den
    # Einstellungen (Abschnitt "schedule"). Auch ohne Einrichtung starten:
    # die Jobs ueberspringen sich dann selbst, und nach dem Assistenten laeuft
    # der Zeitplan ohne Neustart los.
    try:
        import cron as cron_mod
        cron_mod.start()
    except Exception as e:
        log.warning("cron scheduler failed to start: %s", e)


@app.on_event("shutdown")
def _shutdown():
    try:
        import cron as cron_mod
        cron_mod.stop()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Health / Status
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    # Bewusst ohne Anmeldung (Monitoring, HEALTHCHECK des Containers). Die
    # Version steht dabei, damit nach einem Update von aussen zu sehen ist,
    # welcher Stand wirklich laeuft.
    return {"ok": True, "version": config.VERSION}


@app.get("/status")
def status(user: User = Depends(require_user)):
    # Nur angemeldet: die Antwort nennt die interne Jellyfin-Adresse und
    # Zahlen zur Bibliothek. Die Oberflaeche fragt hier nur auf der
    # Startseite nach dem letzten Abgleich, also immer mit Sitzung.
    with get_session() as s:
        state = s.get(SyncState, 1)
        total = len(s.exec(select(Movie)).all())
        embedded = len(s.exec(select(Movie).where(Movie.embedded == True)).all())
        users = len(s.exec(select(User)).all())
        cols = len(s.exec(select(Collection)).all())
    return {
        "app": "CinematekAI",
        "movies_in_cache": total,
        "movies_embedded": embedded,
        "collections": cols,
        "users": users,
        "last_sync_at": state.last_sync_at.isoformat() if state and state.last_sync_at else None,
        "jellyfin_url": settings.get("jellyfin")["url"],
    }


# ---------------------------------------------------------------------------
# Auth Endpoints
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/auth/login")
def do_login(body: LoginRequest, response: Response):
    result = auth_login(body.username, body.password)
    # Lebensdauer = Sitzungsdauer aus den Einstellungen (vorher fest 30 Tage);
    # dieselben Parameter wie bei der Einrichtung, deshalb ein gemeinsamer Helfer.
    sitzungs_cookie_setzen(response, result["token"])
    # After login, sync watch state + series progress for this user
    try:
        sync_watch_state(result["user_id"])
    except Exception as e:
        log.warning("Watch-state sync after login failed: %s", e)
    try:
        sync_series_progress(result["user_id"])
    except Exception as e:
        log.warning("Series-progress sync after login failed: %s", e)
    return {
        "user_id": result["user_id"],
        "display_name": result["display_name"],
        "is_admin": result["is_admin"],
    }


@app.post("/auth/logout")
def do_logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        auth_logout(token)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/auth/me")
def me(user: Optional[User] = Depends(get_current_user_or_none)):
    if not user:
        return {"logged_in": False}
    return {
        "logged_in": True,
        "user_id": user.id,
        "display_name": user.display_name,
        "is_admin": user.is_admin,
        "jellyfin_username": user.jellyfin_username,
        # Eigene Wahl oder Vorgabe; "sprache_gewaehlt" sagt, ob es eine eigene ist.
        "sprache": texte.fuer_nutzer(user),
        "sprache_gewaehlt": bool(texte.normalisieren(getattr(user, "language", ""))),
        # Fuer den Abspann der Oberflaeche. Hier und nicht in /app-info: diese
        # Abfrage stellt die Oberflaeche ohnehin bei jedem Laden und nach jeder
        # Anmeldung, /app-info dagegen prueft dabei die KI-Dienste.
        "version": config.VERSION,
    }


class SpracheRequest(BaseModel):
    sprache: str


@app.put("/auth/sprache")
def sprache_setzen(body: SpracheRequest, user: User = Depends(require_user)):
    """Sprache der Oberflaeche fuer diesen Nutzer merken (auch fuer Hintergrund-
    arbeit wie die Begruendung des Films des Tages)."""
    code = texte.normalisieren(body.sprache)
    if not code:
        raise HTTPException(400, "de / en")
    with get_session() as s:
        u = s.get(User, user.id)
        u.language = code
        s.add(u)
        s.commit()
    return {"sprache": code}


# ---------------------------------------------------------------------------
# Admin Endpoints
# ---------------------------------------------------------------------------

class AddUserRequest(BaseModel):
    jellyfin_username: str


@app.get("/admin/users")
def list_users(admin: User = Depends(require_admin)):
    with get_session() as s:
        users = s.exec(select(User)).all()
    return [
        {
            "id": u.id,
            "jellyfin_username": u.jellyfin_username,
            "display_name": u.display_name,
            "is_admin": u.is_admin,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
        }
        for u in users
    ]


@app.post("/admin/users")
def add_user(body: AddUserRequest, admin: User = Depends(require_admin)):
    # Look up in Jellyfin
    jf = lookup_jellyfin_user(body.jellyfin_username)
    if not jf:
        raise HTTPException(404, _text("jf_benutzer_fehlt", name=body.jellyfin_username))
    with get_session() as s:
        existing = s.exec(
            select(User).where(User.jellyfin_user_id == jf["id"])
        ).first()
        if existing:
            raise HTTPException(409, _text("schon_allowlist"))
        user = User(
            jellyfin_user_id=jf["id"],
            jellyfin_username=jf["name"],
            display_name=jf["name"],
            is_admin=False,
        )
        s.add(user)
        s.commit()
        s.refresh(user)
    return {"id": user.id, "jellyfin_username": user.jellyfin_username}


def _remaining_admin_count_excluding(s, excluded_user_id: int) -> int:
    """Count how many admins would still exist if the given user were removed
    / demoted. Used to prevent an 'orphan' state with zero admins."""
    admins = s.exec(select(User).where(User.is_admin == True)).all()
    return len([u for u in admins if u.id != excluded_user_id])


@app.delete("/admin/users/{user_id}")
def remove_user(user_id: int, admin: User = Depends(require_admin)):
    with get_session() as s:
        user = s.get(User, user_id)
        if not user:
            raise HTTPException(404)
        if user.id == admin.id:
            raise HTTPException(400, _text("selbst_entfernen"))
        # Deleting the last remaining admin would lock everyone out of admin ops
        if user.is_admin and _remaining_admin_count_excluding(s, user.id) == 0:
            raise HTTPException(400, _text("einziger_admin"))
        # Delete sessions
        for sess in s.exec(select(UserSession).where(UserSession.user_id == user_id)).all():
            s.delete(sess)
        # Delete watch states
        for ws in s.exec(select(WatchState).where(WatchState.user_id == user_id)).all():
            s.delete(ws)
        # Sichtbare Titel ebenso: sonst erbte ein spaeter angelegtes Konto mit
        # derselben internen ID die Rechte des entfernten.
        sichtbarkeit.entfernen(s, user_id)
        s.delete(user)
        s.commit()
    return {"ok": True}


class SetAdminRequest(BaseModel):
    is_admin: bool


@app.put("/admin/users/{user_id}/admin")
def toggle_admin(user_id: int, body: SetAdminRequest, admin: User = Depends(require_admin)):
    with get_session() as s:
        user = s.get(User, user_id)
        if not user:
            raise HTTPException(404)
        # Prevent demoting the last remaining admin (including the caller
        # demoting themselves when they're the sole admin).
        if user.is_admin and not body.is_admin:
            if _remaining_admin_count_excluding(s, user.id) == 0:
                raise HTTPException(400, _text("letzter_admin"))
        user.is_admin = body.is_admin
        s.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

@app.post("/sync")
def trigger_sync(user: User = Depends(require_admin)):
    try:
        result = sync_library()
    except Exception as e:
        raise HTTPException(502, _text("sync", fehler=e))
    return result


@app.post("/sync/watch")
def trigger_watch_sync(user: User = Depends(require_user)):
    """Sync watch state for the current user."""
    try:
        result = sync_watch_state(user.id)
    except Exception as e:
        raise HTTPException(502, _text("sync_watch", fehler=e))
    return result


@app.post("/sync/series-progress")
def trigger_series_progress_sync(user: User = Depends(require_user)):
    """Sync per-user series progress (next-up, watched-count) for the
    current user. Runs automatically at login + nightly cron — this endpoint
    is for manually re-pulling after watching something."""
    try:
        result = sync_series_progress(user.id)
    except Exception as e:
        raise HTTPException(502, _text("sync_serien", fehler=e))
    return result


# ---------------------------------------------------------------------------
# Movies
# ---------------------------------------------------------------------------

def _hat_sprache(spalte, kuerzel: str):
    """SQL-Bedingung: steht ``kuerzel`` als eigenes Kuerzel in der Spalte?

    Die Spalten ``audio_languages``/``subtitle_languages`` sind kommagetrennte
    Listen ("de,en,fr"). Ein schlichtes ``LIKE '%de%'`` wuerde auch mitten in
    einem anderen Kuerzel treffen — in der Bibliothek stehen tatsaechlich
    Codes, die der Normalisierer nicht kannte und darum dreistellig
    durchgelassen hat ("fil", "tat", "mdr"). Darum erst Kommas um den ganzen
    Wert legen und dann nach ",de," suchen: so trifft nur ein vollstaendiges
    Kuerzel.

    Ein leerer Wert heisst "keine Angabe", nicht "kein deutscher Ton" — er
    faellt hier also absichtlich heraus, wie jeder andere Nicht-Treffer.
    """
    return (literal(",") + spalte + literal(",")).like(f"%,{kuerzel},%")


@app.get("/movies")
def list_movies(
    user: User = Depends(require_user),
    q: Optional[str] = None,
    genre: Optional[str] = None,
    year: Optional[int] = None,
    played: Optional[bool] = None,
    favorites_only: bool = False,
    # Tonsprache als ISO-Kuerzel ("de"). Der Filter gehoert ins SQL und nicht
    # in die Oberflaeche: die Liste kommt seitenweise (limit/offset), ein
    # Nachfiltern im Browser wuerde also nur die gerade geladene Seite
    # ausduennen und die Trefferzahl verfaelschen.
    audio_lang: Optional[str] = Query(None, pattern="^[a-z]{2,3}$"),
    sort: str = Query("name", pattern="^(name|year|rating|added|last_played|ai_match)$"),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    # Nur, was das Jellyfin-Konto sehen darf - im SQL und nicht hinterher,
    # damit limit/offset und die Gesamtzahl zur gefilterten Liste passen.
    sichtbar = sichtbarkeit.laden(user.id).sql(Movie.id, sichtbarkeit.FILM)
    with get_session() as s:
        stmt = select(Movie)
        if sichtbar is not None:
            stmt = stmt.where(sichtbar)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(or_(
                Movie.name.ilike(like),
                Movie.overview.ilike(like),
                Movie.directors.ilike(like),
                Movie.actors.ilike(like),
                Movie.characters.ilike(like),
                Movie.collection_names.ilike(like),
            ))
        if genre:
            stmt = stmt.where(Movie.genres.ilike(f"%{genre}%"))
        if year is not None:
            stmt = stmt.where(Movie.year == year)
        if audio_lang:
            stmt = stmt.where(_hat_sprache(Movie.audio_languages, audio_lang))

        if sort == "ai_match":
            # Ordering happens in-python after scoring against the user profile.
            # Fetch unsorted; we re-order below.
            movies = s.exec(stmt).all()
        else:
            col = {
                "name": Movie.name,
                "year": Movie.year,
                "rating": Movie.community_rating,
                "added": Movie.date_added,
                "last_played": Movie.date_added,  # placeholder, real sort by ws below
            }[sort]
            stmt = stmt.order_by(col.desc() if order == "desc" else col.asc())
            movies = s.exec(stmt.offset(offset).limit(limit)).all()
        alle = select(Movie.id)
        if sichtbar is not None:
            alle = alle.where(sichtbar)
        total = len(s.exec(alle).all())

        # Build watch state lookup for this user
        ws_all = s.exec(
            select(WatchState).where(WatchState.user_id == user.id)
        ).all()
        ws_map = {ws.movie_id: ws for ws in ws_all}

    items = []
    for m in movies:
        ws = ws_map.get(m.id)
        is_played = ws.played if ws else False
        is_fav = ws.is_favorite if ws else False

        if played is not None and is_played != played:
            continue
        if favorites_only and not is_fav:
            continue

        items.append({
            "id": m.id,
            "name": m.name,
            "year": m.year,
            "runtime_min": m.runtime_min,
            "rating": m.community_rating,
            "genres": [g for g in m.genres.split(",") if g],
            "directors": [d for d in m.directors.split(",") if d],
            "actors": [a for a in m.actors.split(",") if a],
            "characters": [c for c in m.characters.split(",") if c],
            "collections": [c for c in m.collection_names.split(",") if c],
            # Kommagetrennte ISO-Kuerzel, unveraendert wie in der Datenbank.
            # Leerer String heisst "keine Angabe" — die Oberflaeche laesst die
            # Zeile dann weg, statt "unbekannt" zu behaupten.
            "audio_languages": m.audio_languages or "",
            "subtitle_languages": m.subtitle_languages or "",
            "overview": m.overview,
            "played": is_played,
            "favorite": is_fav,
            # Wird fuer den Bewerten-Modus im Swipe-Stapel gebraucht.
            "user_rating": ws.user_rating if ws else None,
            "last_played": ws.last_played.isoformat() if ws and ws.last_played else None,
            "poster_url": f"/image/{m.id}",
            "jellyfin_url": oeffentlicher_link(m.id),
        })

    # Always compute match-score so the badge can render outside ai_match
    # sort, too. Single batch op — cheap.
    try:
        from embeddings import profile_all_similarities
        sims = profile_all_similarities(user.id)
    except Exception as e:
        log.warning("profile sims for movies failed: %s", e)
        sims = {}

    def _score(mid: str) -> int:
        sim = sims.get(mid)
        if sim is None:
            return -1
        return passung_prozent(sim)

    for it in items:
        it["match_score"] = _score(it["id"])

    if sort == "ai_match":
        reverse = (order != "asc")   # "desc" = best first (default UX)
        items.sort(key=lambda it: it["match_score"], reverse=reverse)
        items = items[offset:offset + limit]

    return {"total": total, "count": len(items), "items": items}


@app.get("/movies/{movie_id}")
def get_movie(movie_id: str, user: User = Depends(require_user)):
    with get_session() as s:
        m = s.get(Movie, movie_id)
        # Fuer das Konto verborgen = nicht vorhanden. Dieselbe 404 wie fuer
        # eine unbekannte ID, damit sich gesperrte Titel nicht erraten lassen.
        if not m or not sichtbarkeit.laden(user.id).film(movie_id):
            raise HTTPException(404, _text("film_fehlt"))
        ws = s.get(WatchState, (user.id, movie_id))
    return {
        "id": m.id, "name": m.name, "year": m.year, "runtime_min": m.runtime_min,
        "rating": m.community_rating, "official_rating": m.official_rating,
        "genres": [g for g in m.genres.split(",") if g],
        "studios": [x for x in m.studios.split(",") if x],
        "directors": [d for d in m.directors.split(",") if d],
        "actors": [a for a in m.actors.split(",") if a],
        "characters": [c for c in m.characters.split(",") if c],
        "collections": [c for c in m.collection_names.split(",") if c],
        # siehe /movies: Kuerzel wie gespeichert, "" = keine Angabe
        "audio_languages": m.audio_languages or "",
        "subtitle_languages": m.subtitle_languages or "",
        "overview": m.overview, "tagline": m.tagline,
        "played": ws.played if ws else False,
        "play_count": ws.play_count if ws else 0,
        "last_played": ws.last_played.isoformat() if ws and ws.last_played else None,
        "user_rating": ws.user_rating if ws else None,
        "favorite": ws.is_favorite if ws else False,
        "poster_url": f"/image/{m.id}",
        "backdrop_url": f"/image/{m.id}?type=Backdrop",
        "jellyfin_url": oeffentlicher_link(m.id),
    }


@app.get("/movies/{movie_id}/detail")
@_mit_sicht
def get_movie_detail(movie_id: str, user: User = Depends(require_user)):
    """Rich detail payload for the dedicated film page.

    Includes: movie core, the user's watch-state, a 0-100 match-score derived
    from cosine similarity between the user's taste-profile vector and the
    movie's vector, a mechanical match-reason, tagline, collection info and
    the Jellyfin play URL. No LLM call — response stays snappy.
    """
    from embeddings import profile_movie_similarity
    from recommendations import _mechanical_reason
    from user_profile import build_profile

    sicht = sichtbarkeit.laden(user.id)
    with get_session() as s:
        m = s.get(Movie, movie_id)
        if not m or not sicht.film(movie_id):      # verborgen = 404, siehe get_movie
            raise HTTPException(404, _text("film_fehlt"))
        ws = s.get(WatchState, (user.id, movie_id))
        collection = None
        first_cid = next((c for c in (m.collection_ids or "").split(",") if c), None)
        if first_cid:
            c = s.get(Collection, first_cid)
            if c:
                collection = {
                    "id": c.id, "name": c.name, "movie_count": c.movie_count,
                }
                # Phase 6: Fortschritt in der Reihe. Die Startseite zeigt das
                # in "Weiterschauen" laengst — auf der Detailseite stand
                # bislang nur der nackte Reihen-Name.
                collection.update(_collection_progress(s, user.id, c.id, movie_id, sicht))
                # Die Zahl der Teile aus der Tabelle zaehlt auch verborgene
                # mit. Nur ersetzen, wenn wirklich etwas verborgen ist: ohne
                # Einschraenkung bleibt der bisherige Wert aus Jellyfin stehen.
                if sicht.verborgen():
                    collection["movie_count"] = collection["total_count"]

    # Match score — profile vector may be missing before first cron run
    sim = profile_movie_similarity(user.id, movie_id)
    if sim is None:
        match_score = 50
    else:
        # Map cosine [0,1] → [0,100] with a mild contrast boost so mediocre
        # matches don't all cluster near 50.
        match_score = passung_prozent(sim)

    # Match reason — re-use the recommendations helper
    profile = build_profile(user.id)
    film_dict = {
        "name": m.name, "year": m.year,
        "genres": [g for g in m.genres.split(",") if g],
        "directors": [d for d in m.directors.split(",") if d],
        "collections": [c for c in m.collection_names.split(",") if c],
        "rating": m.community_rating,
    }
    match_reason = _mechanical_reason(profile, film_dict, user.id)

    # Spoiler-free blurb: tagline first, else first sentence of overview
    tagline = (m.tagline or "").strip()
    if not tagline and m.overview:
        first = m.overview.split(".")[0].strip()
        tagline = first + ("." if first and not first.endswith(".") else "")

    return {
        "movie": {
            "id": m.id, "name": m.name, "year": m.year,
            "runtime_min": m.runtime_min,
            "rating": m.community_rating,
            "official_rating": m.official_rating,
            "genres": [g for g in m.genres.split(",") if g],
            "studios": [x for x in m.studios.split(",") if x],
            "directors": [d for d in m.directors.split(",") if d],
            "actors": [a for a in m.actors.split(",") if a],
            "characters": [c for c in m.characters.split(",") if c],
            # siehe /movies: Kuerzel wie gespeichert, "" = keine Angabe.
            # Die Detailseite macht daraus die Zeile "Ton: … · Untertitel: …".
            "audio_languages": m.audio_languages or "",
            "subtitle_languages": m.subtitle_languages or "",
            "overview": m.overview,
            "poster_url": f"/image/{m.id}",
            "backdrop_url": f"/image/{m.id}?type=Backdrop",
        },
        "watch_state": {
            "played": ws.played if ws else False,
            "favorite": ws.is_favorite if ws else False,
            "last_played": ws.last_played.isoformat() if ws and ws.last_played else None,
            "user_rating": ws.user_rating if ws else None,
            # Phase 5: die Oberflaeche braucht den Ausgangszustand der neuen
            # Schalter, sonst zeigt sie beim Laden immer "nicht gesetzt".
            "watchlist": _on_watchlist(user.id, movie_id),
            "dismissed_until": _dismissed_until(user.id, movie_id),
        },
        "match_score": match_score,
        "match_score_raw": round(sim, 3) if sim is not None else None,
        "match_reason": match_reason,
        "tagline": tagline,
        "collection": collection,
        "play_url": oeffentlicher_link(m.id),
    }


def _collection_progress(s, user_id: int, collection_id: str,
                         current_id: str, sicht=None) -> dict:
    """Fortschritt des Nutzers innerhalb einer Filmreihe.

    Liefert alle Teile nach Jahr sortiert mit Gesehen-Kennzeichen, den Zaehler
    und den naechsten ungesehenen Teil. Damit beantwortet die Detailseite
    „du hast 4 von 7" und „dir fehlt noch Teil 3", ohne dass der Nutzer die
    Reihe im Kopf haben muss.

    Teile, die das Konto nicht sehen darf, zaehlen nicht mit: sonst stuende
    dort „dir fehlt noch Teil 3" samt Poster eines gesperrten Films.
    """
    sicht = sicht or sichtbarkeit.laden(user_id)
    members = [
        mv for mv in s.exec(select(Movie)).all()
        if collection_id in [x.strip() for x in (mv.collection_ids or "").split(",") if x.strip()]
        and sicht.film(mv.id)
    ]
    if len(members) < 2:
        return {"parts": [], "watched_count": 0, "total_count": len(members),
                "next_unwatched": None}

    members.sort(key=lambda mv: (mv.year or 9999, mv.name))
    ws_rows = {
        w.movie_id: w for w in s.exec(
            select(WatchState).where(WatchState.user_id == user_id)
        ).all()
    }

    parts, watched = [], 0
    for idx, mv in enumerate(members, start=1):
        w = ws_rows.get(mv.id)
        seen = bool(w and w.played)
        if seen:
            watched += 1
        parts.append({
            "position": idx,
            "id": mv.id,
            "name": mv.name,
            "year": mv.year,
            "played": seen,
            "favorite": bool(w and w.is_favorite),
            "is_current": mv.id == current_id,
            "poster_url": f"/image/{mv.id}",
        })

    next_unwatched = next((p for p in parts if not p["played"]), None)
    return {
        "parts": parts,
        "watched_count": watched,
        "total_count": len(parts),
        "next_unwatched": next_unwatched,
    }


def _on_watchlist(user_id: int, item_id: str) -> bool:
    with get_session() as s:
        return s.get(Watchlist, (user_id, item_id)) is not None


def _dismissed_until(user_id: int, item_id: str) -> Optional[str]:
    with get_session() as s:
        row = s.get(NotInterested, (user_id, item_id))
    if row and row.until > datetime.utcnow():
        return row.until.isoformat()
    return None


class FavoriteRequest(BaseModel):
    favorite: bool


@app.post("/movies/{movie_id}/favorite")
def toggle_favorite(
    movie_id: str,
    body: FavoriteRequest,
    user: User = Depends(require_user),
):
    """Toggle a film's favorite-flag for the logged-in user.

    Persists locally **and** mirrors the change to Jellyfin (POST/DELETE
    /Users/{userId}/FavoriteItems/{itemId}) so the heart shows up in the
    Jellyfin Web UI as well.

    Behaviour: if the Jellyfin call fails (e.g. server down) we still apply
    the change locally and surface a warning — the next nightly watch-state
    sync will reconcile.

    Fuer das Konto verborgene Titel: 404 wie auf der Detailseite. Sonst
    setzte das Backend mit seinem Admin-Schluessel in Jellyfin ein Herz an
    einem Titel, den das Konto dort gar nicht sehen darf.
    """
    with get_session() as s:
        m = s.get(Movie, movie_id)
        if not m or not sichtbarkeit.laden(user.id).film(movie_id):
            raise HTTPException(404, _text("film_fehlt"))
        ws = s.get(WatchState, (user.id, movie_id))
        if ws is None:
            ws = WatchState(
                user_id=user.id, movie_id=movie_id,
                played=False, play_count=0,
                is_favorite=body.favorite,
                updated_at=datetime.utcnow(),
            )
            s.add(ws)
        else:
            ws.is_favorite = body.favorite
            ws.updated_at = datetime.utcnow()
        s.commit()

    jf_warning = None
    try:
        jf_set_favorite(user.jellyfin_user_id, movie_id, body.favorite)
    except Exception as e:
        log.warning("Jellyfin favorite sync failed for %s/%s: %s",
                    user.jellyfin_username, movie_id, e)
        jf_warning = str(e)

    return {
        "movie_id": movie_id,
        "favorite": body.favorite,
        "jellyfin_synced": jf_warning is None,
        "jellyfin_warning": jf_warning,
    }


# ---------------------------------------------------------------------------
# Nutzersignale — Phase 5 (Bewertung, Merkliste, "nicht interessiert")
# ---------------------------------------------------------------------------

class RatingRequest(BaseModel):
    # None loescht die Bewertung wieder.
    rating: Optional[float] = None


@app.post("/movies/{movie_id}/rating")
def set_movie_rating(movie_id: str, body: RatingRequest,
                     user: User = Depends(require_user)):
    """Setzt die Sterne-Bewertung und spiegelt sie nach Jellyfin.

    `user_rating` war im Empfehlungsprofil laengst verdrahtet (>=7 Bonus,
    <4 negatives Signal), nur gab es keine Oberflaeche dazu — beide Nutzer
    standen deshalb auf null Bewertungen. Wie beim Favoriten gilt: schlaegt
    Jellyfin fehl, wird lokal trotzdem gespeichert und gewarnt; der naechtliche
    Sync gleicht ab.
    """
    if body.rating is not None and not (0 <= body.rating <= 10):
        raise HTTPException(400, _text("bewertung_bereich"))

    with get_session() as s:
        # Verborgen = 404, aus demselben Grund wie beim Favoriten.
        if not s.get(Movie, movie_id) or not sichtbarkeit.laden(user.id).film(movie_id):
            raise HTTPException(404, _text("film_fehlt"))
        ws = s.get(WatchState, (user.id, movie_id))
        if ws is None:
            ws = WatchState(user_id=user.id, movie_id=movie_id)
            s.add(ws)
        ws.user_rating = body.rating
        ws.updated_at = datetime.utcnow()
        s.commit()

    jf_warning = None
    try:
        jf_set_rating(user.jellyfin_user_id, movie_id, body.rating)
    except Exception as e:
        log.warning("Jellyfin rating sync failed for %s/%s: %s",
                    user.jellyfin_username, movie_id, e)
        jf_warning = str(e)

    # Eine neue Bewertung aendert das Profil — alte Empfehlungen verwerfen.
    try:
        from recommendations import invalidate_user_cache
        invalidate_user_cache(user.id)
    except Exception:
        pass

    return {"movie_id": movie_id, "rating": body.rating,
            "jellyfin_synced": jf_warning is None, "jellyfin_warning": jf_warning}


@app.get("/recommendations/by-time")
@_mit_sicht
def recommendations_by_time(
    minutes: int = Query(..., ge=20, le=400),
    k: int = Query(6, ge=1, le=20),
    user: User = Depends(require_user),
):
    """„Ich habe X Minuten" — ungesehene Filme, die in das Zeitfenster passen.

    Das Budget gilt hart: „90 min" heisst hoechstens 90 Minuten. Vorher wurde um
    10 Minuten aufgeweicht und zugleich absteigend nach Laufzeit sortiert — im
    Test lagen dadurch alle 20 sichtbaren Treffer ueber dem gewaehlten Budget,
    obwohl der Topf 53 Filme enthielt. Sortiert wird jetzt nach Passung zum
    Profil; bei gleicher Passung kommt der laengste noch passende Film zuerst.
    """
    from recommendations import _hydrate, _pick_diverse, dismissed_ids
    from user_profile import build_profile

    profile = build_profile(user.id)
    hidden = dismissed_ids(user.id)
    sicht = sichtbarkeit.laden(user.id)

    with get_session() as s:
        rows = s.exec(
            select(Movie)
            .where(Movie.runtime_min != None)          # noqa: E711
            .where(Movie.runtime_min <= minutes)
        ).all()

    # pool_size zaehlt damit auch nur sichtbare Filme.
    candidates = [
        mv for mv in rows
        if mv.id not in profile["seen_ids"] and mv.id not in hidden and sicht.film(mv.id)
    ]

    try:
        from embeddings import profile_all_similarities
        sims = profile_all_similarities(user.id)
    except Exception as e:
        log.warning("by-time: Profil-Aehnlichkeiten nicht verfuegbar: %s", e)
        sims = {}

    def _match(mid: str) -> int:
        sim = sims.get(mid)
        if sim is None:
            return -1
        return passung_prozent(sim)

    candidates.sort(key=lambda mv: (-_match(mv.id), -(mv.runtime_min or 0)))
    hydrated = _hydrate([
        {"movie_id": mv.id, "score": round(max(_match(mv.id), 0) / 100, 3)}
        for mv in candidates[:60]
    ])
    for it in hydrated:
        it["match_score"] = _match(it["movie_id"])
    picked = _pick_diverse(hydrated, n=k)

    return {
        "minutes": minutes,
        "limit_min": minutes,          # Feldname bleibt, Toleranz entfaellt
        "max_runtime_min": minutes,
        "pool_size": len(candidates),
        "results": picked,
    }


class WatchlistRequest(BaseModel):
    on: bool
    media_type: str = "movie"


@app.post("/watchlist/{item_id}")
def toggle_watchlist(item_id: str, body: WatchlistRequest,
                     user: User = Depends(require_user)):
    """Merkliste an/aus. Rein lokal — Jellyfin kennt keine Watchlist."""
    if body.media_type not in ("movie", "series"):
        raise HTTPException(400, _text("medientyp"))
    with get_session() as s:
        row = s.get(Watchlist, (user.id, item_id))
        if body.on and row is None:
            s.add(Watchlist(user_id=user.id, item_id=item_id,
                            media_type=body.media_type))
        elif not body.on and row is not None:
            s.delete(row)
        s.commit()
    return {"item_id": item_id, "on": body.on}


@app.get("/watchlist")
def get_watchlist(user: User = Depends(require_user)):
    """Merkliste des Nutzers, neueste zuerst, mit angereicherten Titeldaten."""
    with get_session() as s:
        rows = s.exec(
            select(Watchlist).where(Watchlist.user_id == user.id)
        ).all()
    rows.sort(key=lambda r: r.added_at, reverse=True)
    from recommendations import _hydrate
    # Gemerkt ist nicht gleich sichtbar: wurde die Freigabe spaeter entzogen,
    # bleibt der Eintrag stehen (er kommt mit der Freigabe zurueck), wird
    # aber nicht gezeigt.
    items = sichtbarkeit.laden(user.id).filtern(
        _hydrate([{"movie_id": r.item_id, "score": 0.0} for r in rows]))
    added = {r.item_id: r.added_at for r in rows}
    for it in items:
        it["added_at"] = added.get(it["movie_id"])
    return {"count": len(items), "items": items}


class DismissRequest(BaseModel):
    days: int = 60


@app.post("/recommendations/dismiss/{item_id}")
def dismiss_item(item_id: str, body: DismissRequest,
                 user: User = Depends(require_user)):
    """„Nicht interessiert" — blendet einen Titel zeitlich begrenzt aus.

    Bewusst befristet statt dauerhaft: Geschmack aendert sich, und eine
    unwiderrufliche Sperre waere fuer einen Haushaltskatalog zu grob.
    """
    days = max(1, min(int(body.days or 60), 3650))
    until = datetime.utcnow() + timedelta(days=days)
    with get_session() as s:
        row = s.get(NotInterested, (user.id, item_id))
        if row is None:
            s.add(NotInterested(user_id=user.id, item_id=item_id, until=until))
        else:
            row.until = until
        s.commit()
    try:
        from recommendations import invalidate_user_cache
        invalidate_user_cache(user.id)
    except Exception:
        pass
    return {"item_id": item_id, "hidden_until": until.isoformat(), "days": days}


@app.delete("/recommendations/dismiss/{item_id}")
def undismiss_item(item_id: str, user: User = Depends(require_user)):
    with get_session() as s:
        row = s.get(NotInterested, (user.id, item_id))
        if row:
            s.delete(row)
            s.commit()
    try:
        from recommendations import invalidate_user_cache
        invalidate_user_cache(user.id)
    except Exception:
        pass
    return {"item_id": item_id, "dismissed": False}


# ---------------------------------------------------------------------------
# Series — Phase TV-A
# ---------------------------------------------------------------------------

@app.get("/series")
def list_series(
    user: User = Depends(require_user),
    q: Optional[str] = None,
    genre: Optional[str] = None,
    sort: str = Query("name", pattern="^(name|year|rating|added|ai_match)$"),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    # Wie bei /movies: Sichtbarkeit im SQL, damit Seiten und total stimmen.
    sichtbar = sichtbarkeit.laden(user.id).sql(Series.id, sichtbarkeit.SERIE)
    with get_session() as s:
        stmt = select(Series)
        if sichtbar is not None:
            stmt = stmt.where(sichtbar)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(or_(
                Series.name.ilike(like),
                Series.overview.ilike(like),
                Series.creators.ilike(like),
                Series.actors.ilike(like),
                Series.characters.ilike(like),
            ))
        if genre:
            stmt = stmt.where(Series.genres.ilike(f"%{genre}%"))

        if sort == "ai_match":
            rows = s.exec(stmt).all()
        else:
            col = {
                "name": Series.name, "year": Series.year,
                "rating": Series.community_rating, "added": Series.date_added,
            }[sort]
            stmt = stmt.order_by(col.desc() if order == "desc" else col.asc())
            rows = s.exec(stmt.offset(offset).limit(limit)).all()
        alle = select(Series.id)
        if sichtbar is not None:
            alle = alle.where(sichtbar)
        total = len(s.exec(alle).all())

    # Pull match-scores once for every visible series — single batch op.
    sims: dict[str, float] = {}
    try:
        from embeddings import profile_all_similarities
        sims = profile_all_similarities(user.id)
    except Exception as e:
        log.warning("profile sims for series failed: %s", e)

    # Pull all SeriesProgress rows for this user once
    with get_session() as s2:
        sp_rows = s2.exec(
            select(SeriesProgress).where(SeriesProgress.user_id == user.id)
        ).all()
    progress_map = {sp.series_id: sp for sp in sp_rows}

    def score_for(sid: str) -> int:
        sim = sims.get(sid)
        if sim is None:
            return -1
        return passung_prozent(sim)

    def progress_for(sid: str) -> dict:
        sp = progress_map.get(sid)
        if not sp:
            return {"state": "not_started", "episodes_watched": 0,
                    "is_favorite": False, "next_up_label": ""}
        return {
            "state": sp.state,
            "episodes_watched": sp.episodes_watched,
            "total_episodes": sp.total_episodes,
            "next_up_label": sp.next_up_label or "",
            "next_up_id": sp.next_up_id,
            "last_played": sp.last_played.isoformat() if sp.last_played else None,
            "is_favorite": sp.is_favorite,
        }

    items = []
    for ser in rows:
        items.append({
            "id": ser.id, "name": ser.name,
            "year": ser.year, "end_year": ser.end_year,
            "rating": ser.community_rating,
            "official_rating": ser.official_rating,
            "genres": [g for g in ser.genres.split(",") if g],
            "creators": [c for c in ser.creators.split(",") if c],
            "actors": [a for a in ser.actors.split(",") if a],
            "season_count": ser.season_count,
            "episode_count": ser.episode_count,
            "special_count": ser.special_count,
            "status": ser.status,
            "runtime_min_avg": ser.runtime_min_avg,
            "tagline": ser.tagline,
            # Bei Serien die Vereinigung ueber alle Folgen (siehe
            # sync._serien_sprachen) — sonst wie bei Filmen.
            "audio_languages": ser.audio_languages or "",
            "subtitle_languages": ser.subtitle_languages or "",
            "poster_url": f"/image/{ser.id}",
            "match_score": score_for(ser.id),
            "progress": progress_for(ser.id),
        })

    if sort == "ai_match":
        items.sort(
            key=lambda it: (it.get("match_score") or -1),
            reverse=(order != "asc"),
        )
        items = items[offset:offset + limit]

    return {"total": total, "limit": limit, "offset": offset, "items": items}


@app.get("/series/{series_id}")
def get_series(series_id: str, user: User = Depends(require_user)):
    with get_session() as s:
        ser = s.get(Series, series_id)
        if not ser or not sichtbarkeit.laden(user.id).serie(series_id):   # siehe get_movie
            raise HTTPException(404, _text("serie_fehlt"))
    return {
        "id": ser.id, "name": ser.name,
        "year": ser.year, "end_year": ser.end_year,
        "rating": ser.community_rating,
        "official_rating": ser.official_rating,
        "genres": [g for g in ser.genres.split(",") if g],
        "studios": [x for x in ser.studios.split(",") if x],
        "creators": [c for c in ser.creators.split(",") if c],
        "actors": [a for a in ser.actors.split(",") if a],
        "characters": [c for c in ser.characters.split(",") if c],
        # siehe /series: Vereinigung ueber alle Folgen, "" = keine Angabe
        "audio_languages": ser.audio_languages or "",
        "subtitle_languages": ser.subtitle_languages or "",
        "overview": ser.overview, "tagline": ser.tagline,
        "season_count": ser.season_count,
        "episode_count": ser.episode_count,
        "special_count": ser.special_count,
        "status": ser.status,
        "runtime_min_avg": ser.runtime_min_avg,
        "poster_url": f"/image/{ser.id}",
        "backdrop_url": f"/image/{ser.id}?type=Backdrop",
        "jellyfin_url": oeffentlicher_link(ser.id),
    }


@app.get("/series/{series_id}/detail")
@_mit_sicht
def get_series_detail(series_id: str, user: User = Depends(require_user)):
    """Rich detail payload for the dedicated series page — match-score +
    mechanical match-reason + tagline + Jellyfin play-URL."""
    from embeddings import profile_movie_similarity  # works for any media id
    from recommendations import _mechanical_reason
    from user_profile import build_profile

    with get_session() as s:
        ser = s.get(Series, series_id)
        if not ser or not sichtbarkeit.laden(user.id).serie(series_id):   # siehe get_movie
            raise HTTPException(404, _text("serie_fehlt"))
        sp = s.get(SeriesProgress, (user.id, series_id))

    sim = profile_movie_similarity(user.id, series_id)
    if sim is None:
        match_score = 50
    else:
        match_score = passung_prozent(sim)

    profile = build_profile(user.id)
    series_dict = {
        "name": ser.name, "year": ser.year,
        "genres": [g for g in ser.genres.split(",") if g],
        # creators routed into "directors" so _mechanical_reason picks them up
        "directors": [c for c in ser.creators.split(",") if c],
        "collections": [],
        "rating": ser.community_rating,
        "media_type": "series",
    }
    match_reason = _mechanical_reason(profile, series_dict, user.id)

    tagline = (ser.tagline or "").strip()
    if not tagline and ser.overview:
        first = ser.overview.split(".")[0].strip()
        tagline = first + ("." if first and not first.endswith(".") else "")

    return {
        "series": {
            "id": ser.id, "name": ser.name,
            "year": ser.year, "end_year": ser.end_year,
            "rating": ser.community_rating,
            "official_rating": ser.official_rating,
            "genres": [g for g in ser.genres.split(",") if g],
            "studios": [x for x in ser.studios.split(",") if x],
            "creators": [c for c in ser.creators.split(",") if c],
            "actors": [a for a in ser.actors.split(",") if a],
            "characters": [c for c in ser.characters.split(",") if c],
            # siehe /series: Vereinigung ueber alle Folgen, "" = keine Angabe
            "audio_languages": ser.audio_languages or "",
            "subtitle_languages": ser.subtitle_languages or "",
            "overview": ser.overview,
            "season_count": ser.season_count,
            "episode_count": ser.episode_count,
            "special_count": ser.special_count,
            "status": ser.status,
            "runtime_min_avg": ser.runtime_min_avg,
            "poster_url": f"/image/{ser.id}",
            "backdrop_url": f"/image/{ser.id}?type=Backdrop",
        },
        "match_score": match_score,
        "match_score_raw": round(sim, 3) if sim is not None else None,
        "match_reason": match_reason,
        "tagline": tagline,
        "play_url": oeffentlicher_link(ser.id),
        "progress": {
            "state": sp.state if sp else "not_started",
            "episodes_watched": sp.episodes_watched if sp else 0,
            "total_episodes": sp.total_episodes if sp else 0,
            "next_up_id": sp.next_up_id if sp else None,
            "next_up_label": sp.next_up_label if sp else "",
            "next_up_play_url": oeffentlicher_link(sp.next_up_id) if sp and sp.next_up_id else None,
            "last_played": sp.last_played.isoformat() if sp and sp.last_played else None,
            "is_favorite": sp.is_favorite if sp else False,
        },
    }


@app.get("/home/continue-or-quit")
def continue_or_quit(user: User = Depends(require_user)):
    """Series the user got stuck in: started but stalled, ≤ 3 episodes in.
    Returns ``slots`` so frontend can render a 'weiter oder weg?' prompt.

    Heuristic:
      state ∈ {abandoned, watching} AND episodes_watched ≤ 3
      AND (state == 'abandoned' OR last_played > 30 days ago)
    """
    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(days=30)
    items = []
    sicht = sichtbarkeit.laden(user.id)
    with get_session() as s:
        sp_rows = s.exec(
            select(SeriesProgress)
            .where(SeriesProgress.user_id == user.id)
            .where(SeriesProgress.state.in_(["watching", "abandoned"]))
            .where(SeriesProgress.episodes_watched > 0)
            .where(SeriesProgress.episodes_watched <= 3)
        ).all()
        for sp in sp_rows:
            # Der Fortschritt bleibt nach entzogener Freigabe stehen; gezeigt
            # wird die Serie trotzdem nicht.
            if not sicht.serie(sp.series_id):
                continue
            stalled = (
                sp.state == "abandoned"
                or (sp.last_played is not None and sp.last_played < cutoff)
                or sp.last_played is None  # no last-played stamp = stalled
            )
            if not stalled:
                continue
            ser = s.get(Series, sp.series_id)
            if not ser:
                continue
            items.append({
                "id": ser.id, "name": ser.name,
                "year": ser.year, "poster_url": f"/image/{ser.id}",
                "episodes_watched": sp.episodes_watched,
                "total_episodes": sp.total_episodes,
                "next_up_label": sp.next_up_label,
                "next_up_play_url": (oeffentlicher_link(sp.next_up_id)
                                    if sp.next_up_id else None),
                "state": sp.state,
                "last_played": sp.last_played.isoformat() if sp.last_played else None,
                "detail_url": f"/series/{ser.id}",
            })
    return {"items": items}


class CoQDecisionRequest(BaseModel):
    decision: str   # 'keep' | 'drop'


@app.post("/series/{series_id}/coq-decision")
def coq_decision(
    series_id: str,
    body: CoQDecisionRequest,
    user: User = Depends(require_user),
):
    """User's verdict on a 'continue or quit' card:
       keep → stay in 'watching' state (no-op, but refreshes updated_at);
       drop → mark as 'abandoned' locally (does NOT touch Jellyfin)."""
    if body.decision not in ("keep", "drop"):
        raise HTTPException(400, _text("entscheidung"))
    with get_session() as s:
        sp = s.get(SeriesProgress, (user.id, series_id))
        if not sp:
            raise HTTPException(404, _text("fortschritt_fehlt"))
        if body.decision == "drop":
            sp.state = "abandoned"
        else:
            # keep — bump updated_at so the row drops out of CoQ for a while
            sp.state = "watching"
        sp.updated_at = datetime.utcnow()
        s.commit()
    return {"series_id": series_id, "decision": body.decision, "state": sp.state}


@app.get("/recommendations/weekly-series")
def weekly_series(user: User = Depends(require_user)):
    """One curated series of the week. Cached by ISO calendar week so the
    pick stays stable until the user has had time to actually start it."""
    from recommendations import weekly_series_pick
    return weekly_series_pick(user.id)


@app.get("/home/continue-watching")
def continue_watching(user: User = Depends(require_user)):
    """Films + series the user is mid-way through, sorted by recency.

    - Movies: WatchState.playback_position_ticks > 0 AND not played
    - Series: SeriesProgress.state == 'watching'

    Alles nur, soweit das Jellyfin-Konto es sehen darf: Fortschritt und
    Merker bleiben nach entzogener Freigabe in der Datenbank, und eine Reihe
    zaehlt nur ihre sichtbaren Teile.
    """
    items: list[dict] = []
    sicht = sichtbarkeit.laden(user.id)
    with get_session() as s:
        # Series in watching state
        sp_rows = s.exec(
            select(SeriesProgress)
            .where(SeriesProgress.user_id == user.id)
            .where(SeriesProgress.state == "watching")
        ).all()
        for sp in sp_rows:
            ser = s.get(Series, sp.series_id)
            if not ser or not sicht.serie(ser.id):
                continue
            items.append({
                "type": "series",
                "id": ser.id, "name": ser.name,
                "year": ser.year, "poster_url": f"/image/{ser.id}",
                "next_up_label": sp.next_up_label,
                "next_up_id": sp.next_up_id,
                "next_up_play_url": oeffentlicher_link(sp.next_up_id) if sp.next_up_id else None,
                "episodes_watched": sp.episodes_watched,
                "total_episodes": sp.total_episodes,
                "progress_pct": (
                    int(round(sp.episodes_watched / sp.total_episodes * 100))
                    if sp.total_episodes else 0
                ),
                "last_played": sp.last_played.isoformat() if sp.last_played else None,
                "status": ser.status,
                "detail_url": f"/series/{ser.id}",
            })

        # Movies with resume-position
        ws_rows = s.exec(
            select(WatchState)
            .where(WatchState.user_id == user.id)
            .where(WatchState.played == False)        # noqa: E712
            .where(WatchState.playback_position_ticks > 0)
        ).all()
        for ws in ws_rows:
            m = s.get(Movie, ws.movie_id)
            if not m or not sicht.film(m.id):
                continue
            pct = 0
            if ws.runtime_ticks > 0:
                pct = int(round(ws.playback_position_ticks
                                / ws.runtime_ticks * 100))
            # Ab 90 % gilt ein Film als durch. "Die Peanuts" stand mit 94 %
            # (24 von 25 min) in der Reihe — 90 Sekunden Rest sind kein
            # Weiterschauen mehr.
            if pct >= 90:
                continue
            remaining_ticks = max(0, ws.runtime_ticks - ws.playback_position_ticks)
            items.append({
                "type": "movie",
                "id": m.id, "name": m.name,
                "year": m.year, "poster_url": f"/image/{m.id}",
                "runtime_min": m.runtime_min,
                "progress_pct": pct,
                "position_min": (
                    int(ws.playback_position_ticks / 10_000_000 / 60)
                    if ws.playback_position_ticks else 0
                ),
                # Die Kachel zeigt die *verbleibende* Zeit. Vorher stand dort
                # die bereits gesehene ("Film · 201m"), was in einer Reihe
                # namens "Weiterschauen" genau andersherum gelesen wird.
                "remaining_min": int(remaining_ticks / 10_000_000 / 60),
                "last_played": ws.last_played.isoformat() if ws.last_played else None,
                "play_url": oeffentlicher_link(m.id),
                "detail_url": f"/movie/{m.id}",
            })

        # Movie collections (franchises) — user has watched ≥1 film and
        # there's at least 1 unwatched. Suggest the next-by-year-asc.
        # Suppress collections suggested as a movie-resume-row item.
        all_ws = s.exec(
            select(WatchState).where(WatchState.user_id == user.id)
        ).all()
        ws_by_movie = {ws.movie_id: ws for ws in all_ws}
        all_collections = s.exec(select(Collection)).all()
        # Index movie → collection_ids quickly
        movies_by_collection: dict[str, list[Movie]] = {}
        all_movies_in_db = [m for m in s.exec(select(Movie)).all() if sicht.film(m.id)]
        for m in all_movies_in_db:
            for cid in (m.collection_ids or "").split(","):
                cid = cid.strip()
                if cid:
                    movies_by_collection.setdefault(cid, []).append(m)

        for col in all_collections:
            members = movies_by_collection.get(col.id, [])
            if len(members) < 2:
                continue   # not a real "Reihe" with just 1 film
            played_members, unplayed_members = [], []
            latest_played_ts = None
            for m in members:
                ws = ws_by_movie.get(m.id)
                if ws and ws.played:
                    played_members.append((m, ws))
                    if ws.last_played and (latest_played_ts is None
                                           or ws.last_played > latest_played_ts):
                        latest_played_ts = ws.last_played
                else:
                    unplayed_members.append(m)
            if not played_members or not unplayed_members:
                continue

            # Pick "next" — earliest year among unplayed; tie-breaker by name
            next_movie = min(
                unplayed_members,
                key=lambda x: (x.year if x.year is not None else 9999, x.name),
            )
            items.append({
                "type": "collection",
                "id": next_movie.id,                # poster + click target
                "collection_id": col.id,
                "collection_name": col.name,
                "name": next_movie.name,
                "year": next_movie.year,
                "poster_url": f"/image/{next_movie.id}",
                "runtime_min": next_movie.runtime_min,
                "watched_count": len(played_members),
                "total_count": len(members),
                "progress_pct": int(round(len(played_members) / len(members) * 100)),
                "last_played": (latest_played_ts.isoformat()
                                if latest_played_ts else None),
                "play_url": oeffentlicher_link(next_movie.id),
                "detail_url": f"/movie/{next_movie.id}",
            })

    # Sortierung:
    #   Position 1: das Item mit dem jüngsten last_played (egal ob Film,
    #               Sammlung oder Serie) — was du gerade dran bist gehört
    #               an erste Stelle.
    #   Position 2..N: alle anderen, stumpf nach AI-Match absteigend.
    try:
        from embeddings import profile_all_similarities
        sims = profile_all_similarities(user.id)
    except Exception as e:
        log.warning("continue-watching: sims lookup failed: %s", e)
        sims = {}

    def _match(it: dict) -> int:
        sim = sims.get(it["id"])
        if sim is None:
            return -1
        return passung_prozent(sim)

    for it in items:
        it["match_score"] = _match(it)

    if not items:
        return {"items": []}

    # Pick the single most-recent item; everything else sorted by AI-Match.
    dated = [it for it in items if it.get("last_played")]
    if dated:
        head = max(dated, key=lambda x: x["last_played"])
        rest = [it for it in items if it is not head]
    else:
        head = None
        rest = items
    rest.sort(key=lambda x: x.get("match_score", -1), reverse=True)
    return {"items": ([head] if head else []) + rest}


@app.get("/series/{series_id}/episodes")
def get_series_episodes(series_id: str, user: User = Depends(require_user)):
    """On-demand episode list (not cached in DB). Returns flat list with
    season/episode numbers + UserData where available."""
    with get_session() as s:
        ser = s.get(Series, series_id)
        # Jellyfin filtert die Folgen selbst (Abfrage mit dem Konto der
        # Person); die Serie muss trotzdem vorher sichtbar sein, sonst
        # verriete schon die Antwort, dass es sie gibt.
        if not ser or not sichtbarkeit.laden(user.id).serie(series_id):
            raise HTTPException(404, _text("serie_fehlt"))
    try:
        raw = fetch_series_episodes(series_id, jf_user_id=user.jellyfin_user_id)
    except Exception as e:
        raise HTTPException(502, _text("jellyfin_abruf", fehler=e))
    eps = []
    for it in raw:
        ud = it.get("UserData") or {}
        ticks = it.get("RunTimeTicks") or 0
        eps.append({
            "id": it.get("Id"),
            "name": it.get("Name") or "",
            "season": it.get("ParentIndexNumber"),
            "episode": it.get("IndexNumber"),
            "overview": it.get("Overview") or "",
            "runtime_min": int(ticks / 10_000_000 / 60) if ticks else None,
            "played": bool(ud.get("Played")),
            "play_count": int(ud.get("PlayCount") or 0),
            "poster_url": f"/image/{it.get('Id')}",
        })
    # Staffel 0 (Specials, Making-of, Recaps) ans Ende: wer die Serie schauen
    # will, soll nicht erst an Bonusmaterial vorbeiscrollen.
    eps.sort(key=lambda e: (e["season"] == 0,
                            99 if e["season"] is None else e["season"],
                            e["episode"] or 0))
    return {"series_id": series_id, "items": eps}


@app.post("/series/{series_id}/favorite")
def toggle_series_favorite(
    series_id: str,
    body: FavoriteRequest,
    user: User = Depends(require_user),
):
    """Mark/unmark a series as favorite. Mirrors to Jellyfin AND writes
    SeriesProgress.is_favorite locally so it shows up in continue-watching
    and counts toward the user profile."""
    with get_session() as s:
        ser = s.get(Series, series_id)
        # Verborgen = 404, wie beim Film-Favoriten.
        if not ser or not sichtbarkeit.laden(user.id).serie(series_id):
            raise HTTPException(404, _text("serie_fehlt"))
        sp = s.get(SeriesProgress, (user.id, series_id))
        if sp is None:
            sp = SeriesProgress(
                user_id=user.id, series_id=series_id,
                is_favorite=body.favorite,
                state="not_started",
                updated_at=datetime.utcnow(),
            )
            s.add(sp)
        else:
            sp.is_favorite = body.favorite
            sp.updated_at = datetime.utcnow()
        s.commit()

    jf_warning = None
    try:
        jf_set_favorite(user.jellyfin_user_id, series_id, body.favorite)
    except Exception as e:
        log.warning("Jellyfin series-favorite sync failed for %s/%s: %s",
                    user.jellyfin_username, series_id, e)
        jf_warning = str(e)

    return {
        "series_id": series_id,
        "favorite": body.favorite,
        "jellyfin_synced": jf_warning is None,
        "jellyfin_warning": jf_warning,
    }


@app.post("/admin/backfill-types")
def admin_backfill_types(admin: User = Depends(require_admin)):
    """One-shot: tag existing vector store items with type='movie' so series
    filtering works without a full re-embed."""
    from embeddings import backfill_type_metadata
    return backfill_type_metadata()


# Gross genug, dass das Lesen nicht zum Flaschenhals wird, klein genug, dass
# ein Abbruch des Browsers (Spulen, Fenster zu) schnell auffaellt.
_TRAILER_BLOCKGROESSE = 64 * 1024


def _trailer_bereich(range_header: Optional[str], groesse: int):
    """Wertet den ``Range``-Kopf einer Trailer-Anfrage aus.

    Rueckgabe: ``None`` — ganze Datei schicken (kein oder kein verwertbarer
    Bereich), ``False`` — Bereich nicht erfuellbar (416), sonst ``(start,
    ende)`` mit beiden Grenzen einschliesslich.
    """
    if not range_header:
        return None
    wert = range_header.strip()
    # Eine fremde Einheit muss laut Standard ignoriert werden, nicht abgelehnt.
    if not wert.lower().startswith("bytes="):
        return None
    spez = wert[len("bytes="):].strip()
    # Mehrere Bereiche muessten als multipart/byteranges beantwortet werden.
    # Fuer Video fordert das kein Browser an; die ganze Datei ist die vom
    # Standard erlaubte Ausweichantwort.
    if "," in spez:
        return None
    # Unleserliches wird ignoriert, nicht abgelehnt: 416 ist laut Standard fuer
    # Bereiche reserviert, die man versteht, aber nicht liefern kann. Ein
    # kaputter Kopf bekommt die ganze Datei.
    if "-" not in spez:
        return None
    anfang_s, _, ende_s = spez.partition("-")
    anfang_s, ende_s = anfang_s.strip(), ende_s.strip()
    try:
        if not anfang_s:
            # Suffix-Form "bytes=-<n>": die letzten n Bytes. Safari benutzt sie,
            # um den moov-Atom am Dateiende zu holen.
            laenge = int(ende_s)
            # Null Bytes vom Ende sind verstanden, aber nicht lieferbar: 416.
            if laenge <= 0 or groesse == 0:
                return False
            return (max(0, groesse - laenge), groesse - 1)
        start = int(anfang_s)
        ende = int(ende_s) if ende_s else None
    except ValueError:
        return None
    # Ende vor Anfang ist ein ungueltiger Bereich — ignorieren.
    if start < 0 or (ende is not None and ende < start):
        return None
    # Anfang hinter dem Dateiende ist verstanden, aber nicht lieferbar: 416.
    if start >= groesse:
        return False
    return (start, min(groesse - 1 if ende is None else ende, groesse - 1))


def _datei_bloecke(pfad: str, start: int, laenge: int):
    """Liest ab ``start`` genau ``laenge`` Bytes blockweise.

    Das ``with`` schliesst die Datei auch dann, wenn der Browser mitten im
    Spulen abbricht — dann wirft Starlette den Generator weg, und der
    Kontextmanager laeuft beim Aufraeumen ab.
    """
    with open(pfad, "rb") as f:
        f.seek(start)
        rest = laenge
        while rest > 0:
            block = f.read(min(_TRAILER_BLOCKGROESSE, rest))
            if not block:
                break
            rest -= len(block)
            yield block


@app.get("/items/{item_id}/trailer")
def get_trailer(request: Request, item_id: str, user: User = Depends(require_user)):
    """Liefert den lokalen Trailer des Titels.

    Die Bibliothek legt Trailer als ``<Filmordner>/backdrops/trailer.mp4`` ab
    — 318 der 444 Filme haben einen. Jellyfin indexiert sie nicht, weil der
    Ordner dort fuer Bilder steht; gefunden werden sie ueber den Dateipfad,
    den Jellyfin zum Film meldet. Kein Trailer ist ein normaler Zustand.

    Bereichsanfragen beantwortet der Endpunkt selbst. Die im Image
    installierte Starlette 0.38.6 kann das mit ``FileResponse`` noch nicht:
    sie schickt immer die ganze Datei mit Status 200. Dass Spulen trotzdem
    klappt, macht der Reverse-Proxy davor — direkt ueber Port 8095 fehlt er,
    und Safari spielt ein Video ohne 206-Antwort gar nicht erst ab.
    """
    # Die ID geht in eine Jellyfin-Abfrage mit dem Admin-Schluessel. Was keine
    # Jellyfin-ID ist, kann auch keinen Trailer haben - also gleich 404.
    if not ist_item_id(item_id):
        raise HTTPException(404, _text("kein_trailer"))
    # Der Trailer eines Titels, den das Konto nicht sehen darf, gibt es fuer
    # diese Person genauso wenig wie den Titel selbst.
    if not sichtbarkeit.darf_sehen(user.id, item_id):
        raise HTTPException(404, _text("kein_trailer"))
    try:
        # Medienwurzel bei jedem Aufruf aus den Einstellungen: im Admin-Bereich
        # geaendert, gilt sie sofort.
        pfad = trailer_pfad(item_id, medien_wurzel=settings.get("general")["media_root"])
    except jellyfin.NichtEingerichtet:
        raise
    except Exception as e:
        log.warning("trailer lookup for %s failed: %s", item_id, e)
        raise HTTPException(502, _text("trailer_suche"))
    if not pfad:
        raise HTTPException(404, _text("kein_trailer"))
    try:
        groesse = os.path.getsize(pfad)
    except OSError as e:
        # Der Pfad kam aus Jellyfin, die Datei liegt aber im Container-Mount:
        # verschwindet sie dazwischen, ist das dasselbe wie "kein Trailer".
        log.warning("trailer file %s unreadable: %s", pfad, e)
        raise HTTPException(404, _text("kein_trailer"))

    # "private" statt "public", aus demselben Grund wie beim Bild-Proxy: die
    # Antwort setzt eine Anmeldung voraus und haengt seit 1.0.1 auch davon
    # ab, was das Konto sehen darf. Ein geteilter Zwischenspeicher davor
    # (Reverse-Proxy) duerfte sie sonst an andere Personen weitergeben; der
    # Browser behaelt sie weiterhin einen Tag.
    kopf = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=86400"}
    bereich = _trailer_bereich(request.headers.get("range"), groesse)
    if bereich is False:
        return Response(status_code=416,
                        headers={**kopf, "Content-Range": f"bytes */{groesse}"})
    if bereich is None:
        return FileResponse(pfad, media_type="video/mp4", headers=kopf)

    start, ende = bereich
    laenge = ende - start + 1
    return StreamingResponse(
        _datei_bloecke(pfad, start, laenge),
        status_code=206,
        media_type="video/mp4",
        headers={**kopf,
                 "Content-Range": f"bytes {start}-{ende}/{groesse}",
                 "Content-Length": str(laenge)},
    )


@app.get("/image/{movie_id}")
def proxy_image(movie_id: str,
                type: str = Query("Primary", pattern="^(Primary|Backdrop|Thumb|Logo)$"),
                w: Optional[int] = Query(None, ge=32, le=3840),
                user: User = Depends(require_user)):
    """Bild-Proxy mit Breitenbegrenzung.

    Ohne ``w`` greift eine Voreinstellung je Bildtyp. Vorher lieferte der Proxy
    stets das Original: auf der Favoritenseite 30,7 MB fuer 55 Poster, im Mittel
    achtfach groesser als dargestellt. Poster werden nirgends breiter als ~300 px
    gezeigt, 500 px deckt auch hochaufloesende Bildschirme ab.

    Seit 1.0.1 nur mit Anmeldung. Bis dahin konnte jeder, der den Port
    erreichte, ueber den Admin-Schluessel des Backends beliebige Bilder aus
    Jellyfin abrufen. Die Oberflaeche merkt davon nichts: Bilder laedt sie
    nur auf Seiten hinter der Anmeldung, und ein <img> auf demselben
    Ursprung schickt das Sitzungs-Cookie mit. Anmelde- und Einrichtungsseite
    zeigen nur mitgelieferte Dateien (Banner, Logo).
    """
    # Nur echte Jellyfin-IDs weiterreichen; sonst baute eine ID wie
    # "x?api_key=..." oder ".." eine andere Jellyfin-Adresse. 404 statt 422,
    # damit die Oberflaeche wie bei jedem fehlenden Bild ihren Platzhalter
    # zeigt (z. B. fuer einen TMDB-Vorschlag ohne Poster).
    if not ist_item_id(movie_id):
        raise HTTPException(404, _text("bild_fehlt"))
    # Bilder nur zu Titeln, die das Jellyfin-Konto der Person sehen darf; ein
    # Poster verraet einen gesperrten Film genauso wie seine Detailseite.
    # Dieselbe 404 wie fuer ein fehlendes Bild, damit die Oberflaeche ihren
    # Platzhalter zeigt und sich gesperrte IDs nicht von unbekannten
    # unterscheiden lassen. darf_sehen fragt gezielt nach diesem einen Titel,
    # weil eine Liste den Proxy fuer jedes Poster einzeln aufruft.
    if not sichtbarkeit.darf_sehen(user.id, movie_id):
        raise HTTPException(404, _text("bild_fehlt"))
    max_width = w or (1920 if type == "Backdrop" else 500)
    try:
        ctype, body = open_image(movie_id, type, max_width=max_width)
    except ImageNotFound:
        # Vorher lief auch das fehlende Bild in den 502-Zweig. Ein Poster, das
        # Jellyfin nicht hat, ist aber kein Serverausfall — und nur bei 404
        # kann das Frontend guten Gewissens seinen Platzhalter zeigen.
        raise HTTPException(404, _text("bild_fehlt"))
    except KeinBild as e:
        # Antwort ohne Rasterbild-Typ (siehe jellyfin._BILDTYPEN): nicht unter
        # der eigenen Adresse ausliefern, sondern wie einen Abruffehler melden.
        log.warning("image fetch for %s (%s) returned no image: %s", movie_id, type, e)
        raise HTTPException(502, _text("bild_fehler"))
    except Exception as e:
        # Die httpx-Meldung trug bis 1.0.0 die interne Jellyfin-Adresse samt
        # Host und Port in die Antwort. Details bleiben im Protokoll.
        log.warning("image fetch failed for %s (%s): %s", movie_id, type, e)
        raise HTTPException(502, _text("bild_fehler"))

    # Inhaltstyp von Jellyfin uebernehmen statt pauschal image/jpeg: Logos
    # kommen als PNG, und nur so bleibt ihre Transparenz erhalten.
    # "private" statt "public": die Antwort setzt jetzt eine Anmeldung
    # voraus, und ein geteilter Zwischenspeicher (Reverse-Proxy) duerfte sie
    # sonst an Anfragen ohne Sitzung weitergeben.
    return StreamingResponse(
        body, media_type=ctype,
        headers={"Cache-Control": "private, max-age=86400"},
    )


def _ollama_adressen() -> list[str]:
    """Adressen aller Ollama-Server, die ein Abschnitt (Haupt-, Rueckfall-,
    Einbettungsmodell) benutzt — ohne doppelte. Leere Adresse = Vorgabe des
    Anbieters, genau wie beim echten Aufruf."""
    adressen: list[str] = []
    for name in ("llm", "llm_fallback", "embedding"):
        a = settings.get(name)
        p = anbieter.preset(a.get("provider", ""))
        if not p or p["api"] != "ollama":
            continue
        url = ((a.get("url") or p["url"] or "").strip()).rstrip("/")
        if url and url not in adressen:
            adressen.append(url)
    return adressen


@app.get("/metrics")
def metrics():
    """Prometheus-Metriken im Textformat.

    Bewusst ohne Anmeldung: Prometheus scrapt ohne Sitzung, und der Dienst ist
    nur im LAN erreichbar. Es werden ausschliesslich Zaehler und Zustaende
    ausgegeben — keine Titel, keine Namen, nichts Personenbezogenes ausser der
    Anzahl der Nutzer.

    Bewusst ohne prometheus_client: eine Handvoll Zahlen rechtfertigt keine
    zusaetzliche Abhaengigkeit im Image.
    """
    lines: list[str] = []

    def add(name: str, value, help_text: str, mtype: str = "gauge", labels: str = ""):
        if not any(l.startswith(f"# HELP {name} ") for l in lines):
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {mtype}")
        lines.append(f"{name}{labels} {value}")

    try:
        with get_session() as s:
            movies = s.exec(select(Movie)).all()
            series_rows = s.exec(select(Series)).all()
            episodes = s.exec(select(Episode)).all()
            users = s.exec(select(User)).all()
            collections = s.exec(select(Collection)).all()
            watchstates = s.exec(select(WatchState)).all()
            sync_state = s.get(SyncState, 1)

        add("cinematekai_library_items", len(movies),
            "Anzahl Titel in der lokalen Bibliothek", labels='{kind="movie"}')
        add("cinematekai_library_items", len(series_rows), "", labels='{kind="series"}')
        add("cinematekai_library_items", len(episodes), "", labels='{kind="episode"}')
        add("cinematekai_collections", len(collections), "Anzahl Filmreihen")
        add("cinematekai_users", len(users), "Angelegte Nutzer")

        # Embedding-Rueckstand — waechst, wenn der Einbettungsdienst laenger weg ist.
        add("cinematekai_unembedded_items",
            sum(1 for m in movies if not m.embedded),
            "Titel ohne Embedding (Rueckstand)", labels='{kind="movie"}')
        add("cinematekai_unembedded_items",
            sum(1 for x in series_rows if not x.embedded), "", labels='{kind="series"}')
        add("cinematekai_unembedded_items",
            sum(1 for e in episodes if not e.embedded), "", labels='{kind="episode"}')

        # Karteileichen — sollten nach prune_stale bei 0 liegen.
        movie_ids = {m.id for m in movies}
        add("cinematekai_orphan_rows",
            sum(1 for w in watchstates if w.movie_id not in movie_ids),
            "Verwaiste Zeilen ohne zugehoerigen Titel", labels='{table="watchstate"}')

        if sync_state and sync_state.last_sync_at:
            add("cinematekai_last_sync_timestamp_seconds",
                int(sync_state.last_sync_at.replace(tzinfo=timezone.utc).timestamp()),
                "Zeitpunkt des letzten Bibliotheks-Syncs")

        # Nutzersignale — zeigt, ob die Bewertungen ueberhaupt benutzt werden.
        add("cinematekai_user_ratings",
            sum(1 for w in watchstates if w.user_rating is not None),
            "Abgegebene Sterne-Bewertungen")
        add("cinematekai_favorites",
            sum(1 for w in watchstates if w.is_favorite),
            "Als Favorit markierte Titel")
    except Exception as e:
        log.warning("metrics: DB-Teil fehlgeschlagen: %s", e)
        add("cinematekai_up", 0, "1 wenn die Metriken vollstaendig erhoben wurden")
        return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    # Abhaengigkeiten. "embedding" und "llm" gibt es immer — 0 heisst dann
    # auch "nicht eingerichtet". "ollama" nur, wenn ein Abschnitt Ollama
    # benutzt: bisher stand die Reihe immer da, weil Ollama fest gesetzt war.
    # Mit einem Cloud-Anbieter waere eine dauerhafte 0 dort ein Fehlalarm.
    def abhaengigkeit(name: str, pruefen) -> None:
        try:
            wert = 1 if pruefen() else 0
        except Exception as e:
            log.warning("metrics: Pruefung %s fehlgeschlagen: %s", name, e)
            wert = 0
        add("cinematekai_dependency_up", wert,
            "1 wenn die Abhaengigkeit erreichbar ist", labels=f'{{name="{name}"}}')

    abhaengigkeit("embedding", anbieter.einbettung_verfuegbar)
    abhaengigkeit("llm", llm_client.is_available)
    ollama_urls = _ollama_adressen()
    if ollama_urls:
        # Mehrere Ollama-Adressen (selten) zaehlen nur als "da", wenn alle antworten.
        abhaengigkeit("ollama", lambda: all(anbieter.ollama_erreichbar(u) for u in ollama_urls))

    # Cron-Zustand je Job
    try:
        import cron as cron_mod
        for name, state in cron_mod.all_run_states().items():
            safe = name.replace('"', "")
            ok = 1 if state.get("status") == "ok" else 0
            add("cinematekai_cron_last_ok", ok,
                "1 wenn der letzte Lauf des Jobs erfolgreich war",
                labels=f'{{job="{safe}"}}')
            if state.get("elapsed_s") is not None:
                add("cinematekai_cron_last_duration_seconds", state["elapsed_s"],
                    "Dauer des letzten Laufs", labels=f'{{job="{safe}"}}')
    except Exception as e:
        log.warning("metrics: Cron-Teil fehlgeschlagen: %s", e)

    add("cinematekai_up", 1, "1 wenn die Metriken vollstaendig erhoben wurden")
    return Response("\n".join(lines) + "\n",
                    media_type="text/plain; version=0.0.4")


@app.get("/facets")
def facets(user: User = Depends(require_user)):
    # Die Zahlen gehoeren zur gefilterten Liste (/movies): nur sichtbare Filme.
    sichtbar = sichtbarkeit.laden(user.id).sql(Movie.id, sichtbarkeit.FILM)
    with get_session() as s:
        stmt = select(Movie)
        if sichtbar is not None:
            stmt = stmt.where(sichtbar)
        movies = s.exec(stmt).all()
    genres: dict[str, int] = {}
    years: dict[int, int] = {}
    # Wie viele Filme haben eine Tonspur in welcher Sprache? Die Oberflaeche
    # braucht die Zahl, um den Tonfilter wie die Genres mit Trefferzahl zu
    # beschriften ("Deutscher Ton (65)"). Dieselbe Schleife liest die Filme
    # ohnehin schon — eine zweite Abfrage waere Verschwendung.
    tonsprachen: dict[str, int] = {}
    for m in movies:
        for g in m.genres.split(","):
            g = g.strip()
            if g:
                genres[g] = genres.get(g, 0) + 1
        if m.year:
            years[m.year] = years.get(m.year, 0) + 1
        # set(), damit ein Film mit zwei deutschen Spuren nicht doppelt zaehlt
        for code in {c.strip() for c in m.audio_languages.split(",") if c.strip()}:
            tonsprachen[code] = tonsprachen.get(code, 0) + 1
    return {
        "genres": sorted([{"name": k, "count": v} for k, v in genres.items()], key=lambda x: -x["count"]),
        "years": sorted([{"year": k, "count": v} for k, v in years.items()], key=lambda x: -x["year"]),
        "audio_languages": sorted([{"code": k, "count": v} for k, v in tonsprachen.items()],
                                  key=lambda x: -x["count"]),
    }


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------

@app.get("/collections")
def list_collections(user: User = Depends(require_user)):
    sicht = sichtbarkeit.laden(user.id)
    with get_session() as s:
        cols = s.exec(select(Collection)).all()
        # Nur wenn fuer die Person wirklich etwas verborgen ist: dann zaehlen
        # nur ihre sichtbaren Teile, und eine Reihe ganz ohne sichtbaren Teil
        # faellt weg (schon ihr Name kann verraten, was gesperrt ist). Ohne
        # Einschraenkung bleibt die Zahl aus Jellyfin, wie bisher.
        if sicht.verborgen():
            je_reihe: dict[str, int] = {}
            for mv in s.exec(select(Movie)).all():
                if not sicht.film(mv.id):
                    continue
                for cid in {x.strip() for x in (mv.collection_ids or "").split(",") if x.strip()}:
                    je_reihe[cid] = je_reihe.get(cid, 0) + 1
            cols = [c for c in cols if je_reihe.get(c.id)]
            zahl = {c.id: je_reihe[c.id] for c in cols}
        else:
            zahl = {c.id: c.movie_count for c in cols}
    return [
        {"id": c.id, "name": c.name, "movie_count": zahl[c.id], "overview": c.overview}
        for c in sorted(cols, key=lambda c: c.name)
    ]


# ---------------------------------------------------------------------------
# Embeddings (Phase 3)
# ---------------------------------------------------------------------------

@app.post("/embed-all")
def trigger_embed_all(force: bool = False, admin: User = Depends(require_admin)):
    """Einbetten im Hintergrund starten (Admin). force=true bettet alles neu ein.

    Frueher blockierte der Aufruf, bis alles fertig war — mit einem lokalen
    Modell und der ganzen Bibliothek weit laenger, als ein Proxy eine Anfrage
    offen haelt. Jetzt kommt sofort ``{"status": "gestartet"|"laeuft_bereits"}``
    zurueck; den Fortschritt zeigt /embed-status bzw. /admin/einbettung. Alle
    Wege gehen ueber dieselbe Sperre in embeddings, laufen also nie doppelt.
    """
    from embeddings import einbettung_starten
    return einbettung_starten(force)


@app.get("/embed-status")
def embed_status(admin: User = Depends(require_admin)):
    """Stand der Einbettung (siehe embeddings.einbettung_status).

    Die alten Schluessel (nur Filme) stehen zusaetzlich drin, damit Skripte
    und Abfragen, die sie lesen, weiterlaufen.
    """
    from embeddings import einbettung_status
    stand = einbettung_status()
    with get_session() as s:
        total = len(s.exec(select(Movie)).all())
        embedded = len(s.exec(select(Movie).where(Movie.embedded == True)).all())
    return {
        **stand,
        "total_movies": total,
        "embedded_in_db": embedded,
        # Name aus der Chroma-Zeit, bleibt fuer bestehende Skripte.
        "chroma_count": stand.get("vektoren"),
        "not_embedded": total - embedded,
    }


@app.get("/movies/{movie_id}/similar")
def similar_movies(movie_id: str, k: int = Query(15, ge=1, le=50), user: User = Depends(require_user)):
    """Find movies similar to the given movie via vector search."""
    from embeddings import search_similar
    sicht = sichtbarkeit.laden(user.id)
    # Ausgangsfilm verborgen: keine Nachbarn (die verrieten, worum es geht).
    if not sicht.film(movie_id):
        return {"movie_id": movie_id, "similar": []}
    results = search_similar(movie_id, k=k, exclude_ids=sicht.verborgen())
    return {"movie_id": movie_id, "similar": results}


@app.get("/search/semantic")
def semantic_search(q: str, k: int = Query(15, ge=1, le=50), user: User = Depends(require_user)):
    """Semantic search across all movies by text query."""
    from embeddings import search_by_text
    # Nutzereingabe: deutsche Funktionswoerter raus, sonst dominieren sie
    # den Vektor (siehe embeddings.normalize_user_query). Verborgene Titel
    # ueberspringt die Suche, damit trotzdem k Treffer kommen.
    results = search_by_text(normalize_user_query(q), k=k,
                             exclude_ids=sichtbarkeit.laden(user.id).verborgen())
    return {"query": q, "results": results}


@app.get("/ollama/status")
def ollama_status(user: User = Depends(require_user)):
    """Sind Sprachmodell und Einbettung benutzbar?

    Der Pfad heisst aus Gewohnheit noch "ollama". Gemeint ist inzwischen der
    eingestellte Anbieter, egal welcher: ``available`` = ein Sprachmodell
    (Haupt oder Rueckfall) ist da, ``einbettung`` = Suche und Passung
    funktionieren.

    Seit 1.0.1 nur mit Anmeldung: bei einem Ollama-Modell fragt der Aufruf
    den Ollama-Server an, und ohne Anmeldung liess sich das von aussen
    beliebig oft ausloesen. Vor der Anmeldung braucht ihn niemand: die
    Oberflaeche ruft ihn gar nicht mehr auf (sie nimmt /setup/status und
    /app-info), und ein Monitoring hat /metrics (cinematekai_dependency_up).
    /metrics macht dieselben Proben weiterhin ohne Anmeldung, weil Prometheus
    keine Sitzung hat; weggefallen ist nur der zweite, ungenutzte offene Weg.
    """
    return {"available": llm_client.is_available(),
            "einbettung": anbieter.einbettung_verfuegbar()}


@app.get("/app-info")
def app_info(user: User = Depends(require_user)):
    """Was die Oberflaeche ueber die Einrichtung wissen muss, um Knoepfe und
    Links nur dann zu zeigen, wenn sie auch funktionieren."""
    return {
        "seerr_url": settings.get("seerr")["url"],
        "jellyfin_url": _jellyfin_browser_url(),
        "ki": {"chat": llm_client.is_available(), "kurator": llm_client.grosser_kontext()},
        "einbettung": anbieter.einbettung_verfuegbar(),
    }


# ---------------------------------------------------------------------------
# Recommendations (Phase 4)
# ---------------------------------------------------------------------------

@app.get("/recommendations/daily")
def rec_daily(force: bool = False, user: User = Depends(require_user)):
    """Get today's personalised recommendation (Film des Tages + 3 thematic).

    Returns instantly with a mechanical reason. Call /explain to get an
    optional LLM-written justification (60-150s on CPU).
    """
    from recommendations import daily_for
    return daily_for(user.id, use_cache=not force)


@app.post("/recommendations/daily/explain")
def rec_daily_explain(user: User = Depends(require_user)):
    """Enhance today's recommendation with an LLM-written reason. Slow (~2min)."""
    from recommendations import explain_daily
    return explain_daily(user.id)


@app.get("/recommendations/missing")
def rec_missing(user: User = Depends(require_user)):
    """„Fehlt in deiner Sammlung": passende Filme, die nicht in Jellyfin liegen.

    Quelle sind die TMDB-Empfehlungslisten zu den eigenen Titeln; der naechtliche
    Lauf laesst das Sprachmodell daraus waehlen. Hinzufuegen macht der Nutzer
    selbst ueber den Seerr-Link — hier wird nichts angefordert.
    """
    from missing import missing_for
    return missing_for(user.id)


@app.get("/recommendations/similar/{movie_id}")
def rec_similar(movie_id: str, k: int = Query(12, ge=1, le=30),
                user: User = Depends(require_user)):
    """Similar movies to the given one, with collection bias and diversity."""
    from recommendations import similar_to
    return similar_to(user.id, movie_id, k=k)


class QuizRequest(BaseModel):
    mood: Optional[str] = None
    time_bucket: Optional[str] = None
    familiarity: Optional[str] = None
    company: Optional[str] = None
    genre: Optional[str] = None
    media_type: Optional[str] = None   # 'movie' | 'series' | None (any)
    # Harter Altersfilter, Hoechstalter in Jahren. Der Assistent stellt dazu
    # ausdruecklich KEINE eigene Frage — das Feld ist fuer Aufrufer da, die
    # das Alter schon kennen (z. B. der Chat).
    max_age: Optional[int] = None


@app.post("/recommendations/quiz")
def rec_quiz(body: QuizRequest, user: User = Depends(require_user)):
    """Answer-based recommendation: 4-5 multiple-choice → 3 films.

    No LLM call — uses the existing mood-query vector search with hard
    filters (runtime, familiarity, genre). Returns films with per-film
    match_score + mechanical match_reason.
    """
    from recommendations import quiz_recommend
    antworten = body.model_dump(exclude_none=True)
    # max_age ist keine Antwort auf eine Frage, sondern ein Filter — es
    # gehoert nicht in den Abfragetext fuer die Vektorsuche.
    max_age = antworten.pop("max_age", None)
    return quiz_recommend(user.id, antworten, n=3, max_age=max_age)


@app.get("/recommendations/mood")
def rec_mood(q: str, k: int = Query(12, ge=1, le=30),
             media_type: Optional[str] = Query(None, pattern="^(movie|series|any)$"),
             include_episodes: bool = False,
             max_age: Optional[int] = Query(None, ge=0, le=18),
             user: User = Depends(require_user)):
    """Mood-based recommendations — free-text query.

    - ``media_type``: restrict to one of movie/series/any (default = movie+series)
    - ``include_episodes``: opt-in to also surface single episodes among hits
    - ``max_age``: harter Altersfilter (Hoechstalter in Jahren). Ohne Angabe
      erkennt `by_mood` Kinder-Anfragen selbst am Fragetext.

    ``results`` kann leer sein — dann steht der Grund in ``hinweis``.
    """
    from recommendations import by_mood
    effective_type = "any" if include_episodes else media_type
    return by_mood(user.id, q, k=k, media_type=effective_type, max_age=max_age)


@app.get("/search/episode")
def search_episode(
    q: str,
    k: int = Query(15, ge=1, le=40),
    user: User = Depends(require_user),
):
    """Free-text semantic search across episodes only. Returns episode hits
    with series-name + S/E label so users can find 'die Bienen-Folge von
    Black Mirror' style queries."""
    from embeddings import search_by_text
    # Folgen gelten als sichtbar, wenn ihre Serie es ist (sichtbarkeit.py).
    sicht = sichtbarkeit.laden(user.id)
    raw = search_by_text(normalize_user_query(q), k=k, media_type="episode",
                         exclude_ids=sicht.verborgen(folgen=True))
    if not raw:
        return {"query": q, "results": []}
    ids = [it["movie_id"] for it in raw]
    with get_session() as s:
        eps = s.exec(select(Episode).where(Episode.id.in_(ids))).all()
        ep_by_id = {e.id: e for e in eps if sicht.serie(e.series_id)}
        # Series-name lookup
        sids = {e.series_id for e in eps}
        sers = s.exec(select(Series).where(Series.id.in_(sids))).all()
        ser_by_id = {ser.id: ser for ser in sers}
    out = []
    for it in raw:
        ep = ep_by_id.get(it["movie_id"])
        if not ep:
            continue
        ser = ser_by_id.get(ep.series_id)
        label = ""
        if ep.season is not None and ep.episode is not None:
            label = f"S{ep.season}E{ep.episode}"
        out.append({
            "id": ep.id,
            "name": ep.name,
            "label": label,
            "season": ep.season, "episode": ep.episode,
            "overview": ep.overview,
            "runtime_min": ep.runtime_min,
            "score": it.get("score"),
            "series_id": ep.series_id,
            "series_name": ser.name if ser else "",
            "series_url": f"/series/{ep.series_id}",
            "play_url": oeffentlicher_link(ep.id),
            "poster_url": f"/image/{ep.id}",
        })
    return {"query": q, "results": out}


# ---------------------------------------------------------------------------
# Chat (Phase 5)
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str
    history: list[dict] = []


@app.post("/chat")
def do_chat(body: ChatRequest, user: User = Depends(require_user)):
    """Kick off a chat turn and stream the assistant reply as SSE.

    The LLM runs on a background thread that persists tokens to SQLite
    (table ChatMessage). If the client disconnects, the generation keeps
    running and the final text stays available via /chat/history for 24 h.
    """
    from chat_engine import start_turn, stream_assistant

    row_id = start_turn(user.id, body.message)

    def sse_generator():
        # Emit the assistant row id upfront so the client can /chat/resume it
        yield f"data: {json.dumps({'row_id': row_id})}\n\n"
        try:
            for delta in stream_assistant(row_id):
                # Leerer Brocken = Lebenszeichen aus stream_assistant. Geht als
                # SSE-Kommentar raus: haelt die Verbindung offen, waehrend das
                # Modell nachdenkt, und der Client sieht davon nichts.
                if delta == "":
                    yield ": warten\n\n"
                    continue
                yield f"data: {json.dumps({'text': delta})}\n\n"
            yield 'data: {"done": true}\n\n'
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/chat/resume/{row_id}")
def do_chat_resume(row_id: int, user: User = Depends(require_user)):
    """Re-attach to a streaming assistant row (client reconnected)."""
    from chat_engine import stream_assistant, _read_row
    row = _read_row(row_id)
    if not row or row.user_id != user.id:
        raise HTTPException(404)

    def sse_generator():
        yield f"data: {json.dumps({'row_id': row_id, 'resumed': True})}\n\n"
        # First frame: send whatever is already in the row
        if row.content:
            yield f"data: {json.dumps({'text': row.content})}\n\n"
        if row.status == "complete":
            yield 'data: {"done": true}\n\n'
            return
        try:
            for delta in stream_assistant(row_id):
                if delta == "":          # Lebenszeichen, siehe /chat
                    yield ": warten\n\n"
                    continue
                yield f"data: {json.dumps({'text': delta})}\n\n"
            yield 'data: {"done": true}\n\n'
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/chat/history")
def do_chat_history(user: User = Depends(require_user)):
    """Return the user's chat history for the last 24 hours."""
    from chat_engine import get_history
    return {"items": get_history(user.id)}


@app.post("/chat/clear")
def do_chat_clear(user: User = Depends(require_user)):
    from chat_engine import clear_history
    n = clear_history(user.id)
    return {"deleted": n}


@app.post("/chat/context")
def do_chat_context(body: ChatRequest, user: User = Depends(require_user)):
    """Return the retrieved RAG context for a query without running the LLM."""
    from chat_engine import chat_context
    return chat_context(user.id, body.message)


@app.get("/chat/preload")
def do_chat_preload(user: User = Depends(require_user)):
    """Return the user's pre-computed chip-answers (Phase 6 preload).

    Frontend uses this to render the suggestion chips with instant answers
    instead of waiting 60-120 s for the LLM on click. Answers older than 48 h
    are omitted (the nightly cron refreshes them daily).
    """
    from database import ChatPreload
    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(hours=48)
    with get_session() as s:
        rows = s.exec(
            select(ChatPreload).where(ChatPreload.user_id == user.id)
        ).all()
    return {
        "items": [
            {
                "key": r.prompt_key,
                "prompt": r.prompt,
                "answer": r.answer,
                "created_at": r.created_at.isoformat(),
                "fresh": r.created_at >= cutoff,
            }
            for r in rows
        ]
    }


# ---------------------------------------------------------------------------
# Admin — cron + rebuild
# ---------------------------------------------------------------------------

@app.post("/cron/run-now")
def cron_run_now(job: str = Query(...), admin: User = Depends(require_admin)):
    """Blocking on-demand trigger (legacy). Use /admin/cron/run for async."""
    import cron as cron_mod
    if job == "all":
        return cron_mod.run_nightly()
    try:
        return cron_mod.run_job(job)
    except KeyError as e:
        raise HTTPException(400, str(e))


@app.get("/admin/cron/jobs")
def admin_cron_jobs(admin: User = Depends(require_admin)):
    """List all cron jobs with current run state + descriptions."""
    import cron as cron_mod
    return {"jobs": cron_mod.job_info()}


@app.post("/admin/cron/run")
def admin_cron_run(job: str = Query(...), admin: User = Depends(require_admin)):
    """Start a cron job in a background thread and return immediately.
    Frontend polls /admin/cron/jobs for status."""
    import cron as cron_mod
    try:
        return cron_mod.run_job_async(job)
    except KeyError as e:
        raise HTTPException(400, str(e))


@app.get("/admin/cron/schedule")
def admin_cron_get_schedule(admin: User = Depends(require_admin)):
    import cron as cron_mod
    return cron_mod.get_schedule()


class ScheduleRequest(BaseModel):
    hour: int
    minute: int
    enabled: bool = True
    # None/leer = Zone behalten. Optional, damit aeltere Aufrufer ohne das
    # Feld weiter funktionieren.
    timezone: Optional[str] = None


@app.put("/admin/cron/schedule")
def admin_cron_set_schedule(body: ScheduleRequest, admin: User = Depends(require_admin)):
    import cron as cron_mod
    try:
        return cron_mod.set_schedule(body.hour, body.minute, body.enabled, body.timezone)
    except ValueError as e:
        # settings.EinstellungFehler ist ein ValueError — ungueltige Uhrzeit
        # oder unbekannte Zeitzone kommen mit deutscher Meldung hier an.
        raise HTTPException(400, str(e))


@app.post("/rebuild-embeddings")
def rebuild_embeddings(admin: User = Depends(require_admin)):
    """Alles mit dem aktuellen Modell neu einbetten, im Hintergrund.

    Fuer den Notfall (kaputter Index, geaenderter Dokumenttext). Gleichbedeutend
    mit /embed-all?force=true. Die Marken ``embedded`` werden hier nicht mehr
    vorab zurueckgesetzt: force bettet ohnehin alles ein, und mitten in einem
    schon laufenden Lauf haette das Zuruecksetzen dessen Stand verfaelscht.
    """
    from embeddings import einbettung_starten
    return einbettung_starten(True)


@app.get("/recommendations/profile")
def rec_profile(user: User = Depends(require_user)):
    """Debug: show the user's computed taste profile."""
    from user_profile import build_profile, profile_summary_text
    p = build_profile(user.id)
    # seen_ids (a set) is not JSON serialisable
    p2 = {k: v for k, v in p.items() if k != "seen_ids"}
    p2["seen_count"] = len(p["seen_ids"])
    p2["summary"] = profile_summary_text(p)
    return p2
