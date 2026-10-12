"""Pfade und das einmalige Einlesen der alten config.yaml.

Seit Oktober 2026 stehen alle Einstellungen in der Datenbank und werden im
Admin-Bereich gepflegt (siehe settings.py). Hier bleibt nur, was VOR der
Datenbank feststehen muss — wo die Daten liegen — und das Lesen der alten
Datei, damit eine bestehende Installation ihre Werte beim ersten Start
uebernimmt. Ein frischer Container braucht keine config.yaml mehr.
"""
import logging, os
from pathlib import Path

log = logging.getLogger("cinematekai.config")


# Umgebungsvariablen heissen CINEMATEKAI_*. Die alten CINEAI_*-Namen werden
# noch gelesen, damit ein alter Container oder ein vergessenes Skript nicht
# stillschweigend auf die Standardpfade zurueckfaellt.
def _env(name: str, legacy: str, default: str) -> str:
    return os.environ.get(name) or os.environ.get(legacy) or default


CONFIG_PATH = Path(_env("CINEMATEKAI_CONFIG", "CINEAI_CONFIG", "/app/config.yaml"))


def alte_config_lesen() -> dict | None:
    """Inhalt der alten config.yaml oder ``None``, wenn es keine gibt.

    Nur fuer die einmalige Uebernahme in settings.py gedacht. Eine kaputte
    Datei wird gemeldet und uebergangen statt den Start zu verhindern — die
    Werte lassen sich danach im Admin-Bereich eintragen.
    """
    if not CONFIG_PATH.is_file():
        return None
    try:
        import yaml
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            daten = yaml.safe_load(f)
    except Exception as e:
        log.warning("config.yaml (%s) nicht lesbar, wird uebergangen: %s", CONFIG_PATH, e)
        return None
    return daten if isinstance(daten, dict) else None


DATA_DIR = Path(_env("CINEMATEKAI_DATA", "CINEAI_DATA", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Datenbankdatei heisst jetzt cinematekai.db. Sicherheitsnetz: liegt sie nicht
# vor, wohl aber die alte cineai.db, wird die alte weiterbenutzt. Sonst wuerde
# ein misslungener Umzug eine *leere* Datenbank anlegen — der Ausfall waere
# still und faellt erst auf, wenn die Bibliothek verschwunden ist.
_NEW_DB = DATA_DIR / "cinematekai.db"
_OLD_DB = DATA_DIR / "cineai.db"
if not _NEW_DB.exists() and _OLD_DB.exists():
    log.warning("cinematekai.db fehlt, nutze weiterhin die alte cineai.db (%s). "
                "Umbenennen nicht vergessen.", _OLD_DB)
    DB_PATH = _OLD_DB
else:
    DB_PATH = _NEW_DB

CHROMA_DIR = DATA_DIR / "chroma"

# Gebaute Oberflaeche (SvelteKit, statisch). Im Container liegt sie neben dem
# Backend; fehlt der Ordner, liefert server.py nur die API aus.
STATIC_DIR = Path(_env("CINEMATEKAI_STATIC", "CINEAI_STATIC", "/app/static"))
