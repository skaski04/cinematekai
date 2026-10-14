"""CinematekAI — schlanke, versionierte Datenbank-Migrationen.

Bis hierhin gab es nur `SQLModel.metadata.create_all()`. Das legt fehlende
Tabellen und Spalten *nicht nachtraeglich* an und aendert nie Daten — jede
Schema-Aenderung brauchte deshalb ein Wegwerf-Skript von Hand
(`_migrate_tvc.py` und Verwandte). Alembic waere fuer zwei Nutzer und eine
SQLite-Datei ueberdimensioniert; hier reicht eine nummerierte Liste.

Regeln:
  * Migrationen laufen genau einmal, in Reihenfolge, beim Start.
  * Jede bekommt eine fortlaufende Nummer und eine kurze Beschreibung.
  * Sie muessen idempotent sein — ein zweiter Lauf darf nichts kaputtmachen.
  * Angewandte Versionen stehen in `schema_migration`.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import text
# database.py haelt die Engine privat; hier wird sie bewusst direkt genutzt,
# weil Migrationen unterhalb der ORM-Ebene arbeiten.
from database import _engine as engine

log = logging.getLogger("cinematekai.migrations")


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(text(f'PRAGMA table_info("{table}")')).fetchall()
    return any(r[1] == column for r in rows)


# --------------------------------------------------------------------------
# Die einzelnen Schritte
# --------------------------------------------------------------------------

def _m001_blank_jf_access_tokens(conn) -> str:
    """Jellyfin-Zugriffstoken aus bestehenden Sitzungen entfernen.

    Der Token wurde beim Login gespeichert, aber nirgends verwendet: alle
    Jellyfin-Aufrufe laufen ueber den geteilten Admin-Key. Im Klartext in der
    Datenbank war er damit reine Haftung ohne Gegenwert. Die Spalte bleibt
    bestehen (leerer String), damit kein Schema-Umbau noetig ist.
    """
    if not _column_exists(conn, "usersession", "jf_access_token"):
        return "Spalte nicht vorhanden — nichts zu tun"
    n = conn.execute(text(
        "select count(*) from usersession where jf_access_token != ''"
    )).scalar_one()
    conn.execute(text("update usersession set jf_access_token = ''"))
    return f"{n} Token geleert"


def _m002_tmdb_ids(conn) -> str:
    """Spalte ``tmdb_id`` fuer Filme und Serien.

    Gefuellt wird sie vom naechsten Bibliotheks-Sync aus Jellyfins ProviderIds.
    """
    added = []
    for table in ("movie", "series"):
        if not _column_exists(conn, table, "tmdb_id"):
            conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN tmdb_id VARCHAR'))
            added.append(table)
    return ("ergaenzt in " + ", ".join(added)) if added else "bereits vorhanden"


def _m003_special_count(conn) -> str:
    """Spalte ``special_count`` fuer Serien (Staffel 0).

    Gefuellt vom naechsten Episoden-Sync ueber ``recount_series_episodes``.
    """
    if _column_exists(conn, "series", "special_count"):
        return "bereits vorhanden"
    conn.execute(text('ALTER TABLE "series" ADD COLUMN special_count INTEGER NOT NULL DEFAULT 0'))
    return "ergaenzt"


def _m004_sprachspalten(conn) -> str:
    """Spalten ``audio_languages`` und ``subtitle_languages`` fuer Filme und Serien.

    Bis hierhin stand in der Datenbank nirgends, in welcher Sprache ein Titel
    zu hoeren ist. Auf „einen Film auf Deutsch" konnte der Chat deshalb nur
    raten — und lag dabei meistens falsch: gemessen am 29.09.2026 haben 385
    von 450 Filmen gar keine deutsche Tonspur.

    Gefuellt werden die Spalten vom naechsten Bibliotheks-Sync (Filme) bzw.
    Episoden-Sync (Serien). Leerer String heisst „keine Angabe", nicht „kein
    Ton" — es gibt Dateien mit Tonspur ohne Sprachkennung.
    """
    added = []
    for table in ("movie", "series"):
        for column in ("audio_languages", "subtitle_languages"):
            if _column_exists(conn, table, column):
                continue
            # NOT NULL braucht in SQLite einen Default; '' ist zugleich der
            # Wert, den das Modell fuer "noch nichts bekannt" vorsieht.
            conn.execute(text(
                'ALTER TABLE "%s" ADD COLUMN %s VARCHAR NOT NULL DEFAULT \'\''
                % (table, column)
            ))
            added.append(f"{table}.{column}")
    return ("ergaenzt: " + ", ".join(added)) if added else "bereits vorhanden"


def _m005_sprache_je_nutzer(conn) -> str:
    """Spalte ``language`` fuer Nutzer: gewaehlte Sprache der Oberflaeche.

    Leer heisst "keine eigene Wahl" — dann gilt die Vorgabe aus den
    Einstellungen (Allgemein -> Sprache). Bestehende Nutzer behalten damit
    genau das bisherige Deutsch.
    """
    if _column_exists(conn, "user", "language"):
        return "bereits vorhanden"
    conn.execute(text('ALTER TABLE "user" ADD COLUMN language VARCHAR NOT NULL DEFAULT \'\''))
    return "ergaenzt"


def _table_exists(conn, table: str) -> bool:
    return conn.execute(text(
        "select 1 from sqlite_master where type = 'table' and name = :n"
    ), {"n": table}).first() is not None


def _m006_sichtbarkeit(conn) -> str:
    """Tabellen fuer die sichtbaren Titel je Konto (siehe sichtbarkeit.py).

    Bisher bekam jede Person den ganzen Katalog des Bibliothekskontos zu sehen,
    auch Titel, die Jellyfin ihrem eigenen Konto sperrt. Die Tabellen starten
    leer; leer heisst "kein Filter" (Verhalten wie bisher), bis der naechste
    Sync die Mengen fuellt.

    ``create_all`` beim Start legt die Tabellen in der Regel schon vorher an.
    Die Migration sorgt trotzdem selbst dafuer, damit das Schema nicht davon
    abhaengt, in welcher Reihenfolge jemand init_db und die Migrationen
    aufruft. Angelegt wird aus der Modellbeschreibung (SQLAlchemy-Core), damit
    Spalten und Schluessel exakt zu database.py passen.
    """
    from database import SichtbarerTitel, SichtbarkeitStand
    angelegt = []
    for modell in (SichtbarkeitStand, SichtbarerTitel):
        tabelle = modell.__table__
        if not _table_exists(conn, tabelle.name):
            tabelle.create(conn)
            angelegt.append(tabelle.name)
    return ("angelegt: " + ", ".join(angelegt)) if angelegt else "bereits vorhanden"


def _m007_vektorspeicher(conn) -> str:
    """Tabelle ``vektor`` fuer den eigenen Vektorspeicher (vektorspeicher.py).

    Seit 1.1.0 liegen die Einbettungen hier statt in ChromaDB (data/chroma).
    Die Tabelle startet leer. Die alten Vektoren uebernimmt erst der
    Startschritt embeddings.beim_start aus einem Export
    (tools/chroma_export.py), nicht diese Migration: sie laeuft nur einmal,
    ein Export kann aber auch spaeter noch kommen. Ohne Export baut der
    naechste Einbett-Lauf den Bestand neu auf.

    Dieselbe Anweisung wie im Speicher selbst (CREATE TABLE IF NOT EXISTS),
    damit Schema und Speicher nicht auseinanderlaufen koennen.
    """
    from vektorspeicher import TABELLE_SQL
    if _table_exists(conn, "vektor"):
        return "bereits vorhanden"
    conn.execute(text(TABELLE_SQL))
    return "angelegt"


MIGRATIONS: list[tuple[int, str, callable]] = [
    (1, "Ungenutzte Jellyfin-Token aus usersession entfernen",
     _m001_blank_jf_access_tokens),
    (2, "TMDB-ID fuer Filme und Serien", _m002_tmdb_ids),
    (3, "Specials-Zaehler fuer Serien", _m003_special_count),
    (4, "Ton- und Untertitelsprachen fuer Filme und Serien", _m004_sprachspalten),
    (5, "Sprache der Oberflaeche je Nutzer", _m005_sprache_je_nutzer),
    (6, "Sichtbare Titel je Jellyfin-Konto", _m006_sichtbarkeit),
    (7, "Eigener Vektorspeicher statt ChromaDB", _m007_vektorspeicher),
]


# --------------------------------------------------------------------------
# Ausfuehrung
# --------------------------------------------------------------------------

def _ensure_table(conn) -> None:
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS schema_migration (
            version     INTEGER PRIMARY KEY,
            description TEXT,
            applied_at  TEXT
        )
    """))


def run_migrations() -> dict:
    """Wendet alle noch offenen Migrationen an. Gibt eine Zusammenfassung
    zurueck; Fehler werden protokolliert und weitergereicht, damit ein
    kaputtes Schema nicht stillschweigend weiterlaeuft."""
    applied: list[str] = []
    with engine.begin() as conn:
        _ensure_table(conn)
        done = {r[0] for r in conn.execute(
            text("select version from schema_migration")).fetchall()}

        for version, description, fn in MIGRATIONS:
            if version in done:
                continue
            log.info("Migration %03d: %s ...", version, description)
            detail = fn(conn)
            conn.execute(
                text("insert into schema_migration (version, description, applied_at) "
                     "values (:v, :d, :t)"),
                {"v": version, "d": description, "t": datetime.utcnow().isoformat()},
            )
            log.info("Migration %03d angewandt (%s)", version, detail)
            applied.append(f"{version:03d} {description} — {detail}")

    if not applied:
        log.info("Migrationen: nichts zu tun (Stand %d)",
                 max((v for v, _, _ in MIGRATIONS), default=0))
    return {"applied": applied, "count": len(applied)}
