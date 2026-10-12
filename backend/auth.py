"""CinematekAI authentication — Jellyfin passthrough + local session management."""
import secrets, logging
from datetime import datetime, timedelta
from typing import Optional
from fastapi import Cookie, HTTPException, Depends
from sqlmodel import select
from database import User, UserSession, get_session
import jellyfin
import settings
import texte

log = logging.getLogger("cinematekai.auth")

COOKIE_NAME = "cinematekai_session"

# Meldungen fuer die Oberflaeche. Erst beim Ausloesen gebaut (texte.waehle),
# in der Sprache der Anfrage.
_T = {
    "de": {
        "anmeldung": "Jellyfin auth failed: {fehler}",
        "erster_login": "Erster Login muss ein Jellyfin-Admin sein.",
        "allowlist": "Benutzer nicht auf der Allowlist. Admin muss dich hinzufügen.",
        "nicht_eingeloggt": "Nicht eingeloggt",
        "nur_admins": "Nur Admins",
    },
    "en": {
        "anmeldung": "Jellyfin sign-in failed: {fehler}",
        "erster_login": "The first sign-in must be a Jellyfin admin.",
        "allowlist": "User is not on the allowlist. An admin needs to add you.",
        "nicht_eingeloggt": "Not signed in",
        "nur_admins": "Admins only",
    },
}


def _text(schluessel: str, **werte) -> str:
    return texte.waehle(_T, schluessel, **werte)


def sitzungsdauer_tage() -> int:
    """Gueltigkeit einer Sitzung in Tagen, aus den Einstellungen.

    Bei jedem Aufruf gelesen: eine im Admin-Bereich geaenderte Dauer gilt ab
    der naechsten Anmeldung, ohne Neustart. Bestehende Sitzungen behalten
    ihr Ablaufdatum — sie nachtraeglich zu kuerzen, wuerde Leute mitten in
    der Nutzung abmelden.
    """
    return int(settings.get("general")["session_lifetime_days"])


def sitzung_anlegen(user_id: int) -> str:
    """Neue Sitzung fuer einen CinematekAI-Nutzer; gibt den Token zurueck.

    Eigene Funktion, weil neben ``login()`` auch die Einrichtung (/setup)
    den ersten Admin gleich angemeldet zuruecklaesst — beide sollen Sitzungen
    auf genau dieselbe Weise anlegen.
    """
    token = secrets.token_urlsafe(32)
    with get_session() as s:
        s.add(UserSession(
            token=token,
            user_id=user_id,
            # Bewusst leer: der Token wird nirgends verwendet — alle
            # Jellyfin-Aufrufe laufen ueber den geteilten Admin-Key. Im
            # Klartext gespeichert waere er reine Haftung ohne Gegenwert.
            # Sollte je auf echte Per-User-Zugriffe umgestellt werden, gehoert
            # er verschluesselt abgelegt, nicht so.
            jf_access_token="",
            expires_at=datetime.utcnow() + timedelta(days=sitzungsdauer_tage()),
        ))
        s.commit()
    return token


def login(username: str, password: str) -> dict:
    """Authenticate via Jellyfin, enforce allowlist, return session info.

    Bootstrap rule: if User table is empty, the first Jellyfin admin to log in
    is automatically added to the allowlist as CinematekAI admin.
    """
    # 1. Authenticate with Jellyfin
    try:
        jf = jellyfin.authenticate(username, password)
    except jellyfin.NichtEingerichtet:
        # Kein Anmeldefehler, sondern fehlende Einrichtung: main.py meldet
        # das als 503 mit Verweis auf /setup. Als 401 saehe es aus, als
        # stimme das Passwort nicht.
        raise
    except Exception as e:
        raise HTTPException(401, _text("anmeldung", fehler=e))

    jf_user_id = jf["jellyfin_user_id"]

    with get_session() as s:
        # 2. Check if this is bootstrap (no users yet)
        user_count = len(s.exec(select(User)).all())
        user = s.exec(
            select(User).where(User.jellyfin_user_id == jf_user_id)
        ).first()

        if user_count == 0 and user is None:
            # Bootstrap: only Jellyfin admins may bootstrap
            if not jf["is_jf_admin"]:
                raise HTTPException(403, _text("erster_login"))
            user = User(
                jellyfin_user_id=jf_user_id,
                jellyfin_username=username,
                display_name=jf["display_name"],
                is_admin=True,
            )
            s.add(user)
            s.commit()
            s.refresh(user)
            log.info("Bootstrap: admin user %s created", username)

        elif user is None:
            raise HTTPException(403, _text("allowlist"))

        # 3. Update display name + login timestamp
        user.display_name = jf["display_name"]
        user.last_login_at = datetime.utcnow()
        s.add(user)
        s.commit()
        s.refresh(user)

    # 4. Create session — erst nach dem Schliessen der Sitzung oben: SQLite
    # erlaubt nur einen Schreiber, eine zweite Verbindung mitten in einer
    # offenen Schreibtransaktion muesste auf die erste warten.
    token = sitzung_anlegen(user.id)

    return {
        "token": token,
        "user_id": user.id,
        "display_name": user.display_name,
        "is_admin": user.is_admin,
    }


def logout(token: str):
    with get_session() as s:
        sess = s.get(UserSession, token)
        if sess:
            s.delete(sess)
            s.commit()


def get_current_user_or_none(
    cinematekai_session: Optional[str] = Cookie(None),
) -> Optional[User]:
    """Dependency: returns the logged-in User or None."""
    if not cinematekai_session:
        return None
    with get_session() as s:
        sess = s.get(UserSession, cinematekai_session)
        if not sess or sess.expires_at < datetime.utcnow():
            return None
        user = s.get(User, sess.user_id)
        if not user:
            return None
        # Detach from session by copying attrs
        s.expunge(user)
    return user


def require_user(
    user: Optional[User] = Depends(get_current_user_or_none),
) -> User:
    if not user:
        raise HTTPException(401, _text("nicht_eingeloggt"))
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, _text("nur_admins"))
    return user
