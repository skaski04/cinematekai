"""CinematekAI - eigener Vektorspeicher (ersetzt ChromaDB seit 1.1.0).

Warum selbst gebaut: Gebraucht wurde von Chroma nur das Ablegen von Vektoren
je Sammlung, die Suche nach dem kleinsten Kosinus-Abstand mit einem Filter auf
den Medientyp und das Lesen aller Vektoren fuer die Passung. Dafuer zog
chromadb rund 64 Pakete ins Image (onnxruntime, kubernetes, grpcio, posthog
...), etwa zwei Drittel der installierten Python-Pakete, brachte drei offene
Sicherheitswarnungen ohne Fix-Version mit (sie betrafen nur den Server-Modus,
der hier nie lief) und eine Telemetrie, die trotz Abschaltung Fehlerzeilen ins
Log schrieb. Bei einigen tausend Titeln ist ein Skalarprodukt ueber ALLE
Vektoren ohnehin schnell genug (gemessen: 10.000 x 1024 in wenigen
Millisekunden) und dazu exakt, waehrend der HNSW-Index von Chroma nur
naeherungsweise sucht.

Ablage: Tabelle ``vektor`` in derselben SQLite-Datei wie der Rest
(data/cinematekai.db), nicht in einer eigenen Datei. Gruende:
  * eine Datei, eine Sicherung: das Sicherungsskript nimmt die Vektoren ueber
    die Online-Backup-API konsistent mit. Den Chroma-Ordner musste es dagegen
    zusaetzlich packen, waehrend das Backend womoeglich gerade hineinschrieb;
  * die Tabelle kommt ueber die vorhandenen Migrationen (007), wie jede andere;
  * Groesse: 3187 Vektoren x 1024 Dimensionen x 4 Byte sind 13 MB je Modell -
    fuer SQLite klein.
Dagegen sprach nichts Gewichtiges: geschrieben wird hoechstens ein Vektor je
eingebettetem Titel (mit dem lokalen Modell alle paar Sekunden einer), und
jeder Schreibzugriff ist eine kurze Transaktion.

Schluessel ist (sammlung, item_id). Die Sammlung ist der Name aus
embeddings._sammlungsname und steht damit fuer Modell UND Art
("movies__bge_m3" und "user_profiles__bge_m3"; nomic-embed-text behaelt
"movies"/"user_profiles"). So landen die aus Chroma exportierten Sammlungen
unter genau dem Namen, unter dem die App sie sucht, ohne dass aus einem Namen
das Modell zurueckgerechnet werden muesste - das ginge nicht eindeutig,
"bge-m3" und "bge_m3" ergeben denselben Namen.

Abstand: Kosinus wie in den Chroma-Sammlungen ("hnsw:space": "cosine"),
Abstand = 1 - Skalarprodukt der normierten Vektoren, in float32 gerechnet wie
in hnswlib. Die Skala der Prozentanzeigen (embeddings.skala) ist an diesen
Werten kalibriert; sie duerfen sich nicht verschieben.

Abgelegt wird der Vektor so, wie das Modell ihn geliefert hat (float32, little
endian). Im Speicher haelt jede Sammlung beim ersten Zugriff eine Matrix der
NORMIERTEN Vektoren, die ID-Liste und die Metadaten; Schreibzugriffe aendern
Datenbank und Matrix gemeinsam. Die Reihenfolge der Zeilen ist die des
Anlegens (rowid). Sie entscheidet bei gleichem Abstand und ist nach einem
Neustart dieselbe, weil ein Ueberschreiben die Zeile behaelt und neue Zeilen
immer hinten anstehen.

Faeden: Einbetten und Cron laufen in Hintergrund-Faeden, Abfragen in den
Anfrage-Faeden. Zwei Sperren:
  * ``_lock`` schuetzt die Matrizen im Speicher. Lesen haelt sie fuer die
    ganze Rechnung - eine Abfrage ueber 10.000 Vektoren dauert Millisekunden,
    und so sieht keine Abfrage einen halb ueberschriebenen Vektor.
  * ``_schreib_lock`` reiht die Schreibzugriffe hintereinander. Waehrend ein
    Schreiber auf die Datenbank wartet, haelt er nur diese Sperre; Abfragen
    laufen weiter. Haelte er dabei ``_lock``, koennte ein Anfrage-Faden mit
    offener Schreibtransaktion (SQLAlchemy) auf die Matrix warten, waehrend
    der Schreiber auf dessen Transaktion wartet - bis zum Zeitablauf.
Jeder Zugriff oeffnet eine eigene, kurze Verbindung: so teilen sich zwei
Faeden nie eine Transaktion, und Verbindungen zu oeffnen kostet bei SQLite
nur Mikrosekunden.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np

log = logging.getLogger("cinematekai.vektorspeicher")

# Auch migrations.py (007) legt die Tabelle damit an. Der Speicher fuehrt die
# Anweisung zusaetzlich selbst aus, damit er nicht davon abhaengt, ob und wann
# jemand die Migrationen aufgerufen hat (Tests, Werkzeuge) - IF NOT EXISTS
# macht beides unschaedlich.
#   dim:      Zahl der Dimensionen, zur Pruefung der Laenge von ``vektor``
#   meta:     JSON der Metadaten (type, name, year ...) oder NULL
#   dokument: der eingebettete Text - fuer Nachpruefungen und einen spaeteren
#             Export; gesucht wird darin nicht
TABELLE_SQL = """
CREATE TABLE IF NOT EXISTS vektor (
    sammlung     TEXT    NOT NULL,
    item_id      TEXT    NOT NULL,
    dim          INTEGER NOT NULL,
    vektor       BLOB    NOT NULL,
    meta         TEXT,
    dokument     TEXT,
    geaendert_am TEXT    NOT NULL,
    PRIMARY KEY (sammlung, item_id)
)
"""

_EINFUEGEN = (
    "INSERT INTO vektor (sammlung, item_id, dim, vektor, meta, dokument, geaendert_am) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)")
# ON CONFLICT ... DO UPDATE statt INSERT OR REPLACE: REPLACE loescht die alte
# Zeile und legt eine neue mit neuer rowid an - der Titel rutschte ans Ende,
# und die Reihenfolge bei Gleichstand haenge davon ab, wann zuletzt neu
# eingebettet wurde.
_SCHREIBEN = _EINFUEGEN + (
    " ON CONFLICT(sammlung, item_id) DO UPDATE SET dim = excluded.dim, "
    "vektor = excluded.vektor, meta = excluded.meta, dokument = excluded.dokument, "
    "geaendert_am = excluded.geaendert_am")

# Bedingungen, die ein Filter kennt - dieselbe Schreibweise wie bei Chroma,
# damit embeddings.py seine Filter unveraendert behaelt: {"type": "movie"},
# {"type": {"$eq": "movie"}}, {"type": {"$in": ["movie", "series"]}}; mehrere
# Felder muessen alle passen. Andere Operatoren werden abgewiesen statt
# stillschweigend ignoriert.
_OPERATOREN = ("$eq", "$in")

# Wie Chroma: Buchstaben, Ziffern, Punkt, Unterstrich, Bindestrich, hoechstens
# 63 Zeichen. Gilt auch fuer die Namen in einem Export - sie werden dort zu
# Dateinamen und duerfen keinen Pfad enthalten.
_NAME_ERLAUBT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")


def _jetzt_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normiert(m) -> np.ndarray:
    """Vektor oder Matrix (Zeilen) auf Laenge 1 bringen, float32.

    Der Betrag wird in float64 gerechnet, und zwar fuer einen einzelnen Vektor
    auf demselben Weg wie fuer jede Zeile einer Matrix. So ist ein Vektor nach
    dem Schreiben bitgleich mit demselben Vektor nach einem Neustart (dann
    kommt er mit der ganzen Matrix aus der Datenbank) - sonst koennten
    Rundungen in der letzten Stelle eine Rangfolge bei Gleichstand kippen.
    Nullvektoren bleiben Null (Abstand 1 zu allem, wie bei hnswlib).
    """
    a = np.asarray(m, dtype=np.float32)
    einzeln = a.ndim == 1
    a = a.reshape(1, -1) if einzeln else a
    betrag = np.empty(a.shape[0], dtype=np.float64)
    # Blockweise, damit eine grosse Matrix nicht auf einmal als float64-Kopie
    # im Speicher liegt (10.000 x 1024 waeren 80 MB).
    for start in range(0, a.shape[0], 2048):
        blk = a[start:start + 2048].astype(np.float64)
        betrag[start:start + 2048] = np.sqrt((blk * blk).sum(axis=1))
    betrag[betrag == 0] = 1.0
    out = a / betrag.astype(np.float32)[:, None]
    return out[0] if einzeln else out


def _als_vektor(vektor) -> np.ndarray:
    v = np.asarray(vektor, dtype=np.float32).reshape(-1)
    if v.size == 0:
        raise ValueError("leerer Vektor")
    if not np.isfinite(v).all():
        raise ValueError("Vektor enthaelt NaN oder Unendlich")
    return v


def _filter_pruefen(filt: dict | None) -> list[tuple[str, tuple]]:
    """Filter in eine Liste (Feld, erlaubte Werte) uebersetzen."""
    if not filt:
        return []
    if not isinstance(filt, dict):
        raise ValueError(f"Filter muss ein dict sein: {filt!r}")
    out = []
    for feld, bed in filt.items():
        if isinstance(bed, dict):
            if len(bed) != 1 or next(iter(bed)) not in _OPERATOREN:
                raise ValueError(f"Filter fuer {feld!r} nicht unterstuetzt: {bed!r}")
            op, soll = next(iter(bed.items()))
            if op == "$in":
                if not isinstance(soll, (list, tuple)):
                    raise ValueError(f"$in braucht eine Liste: {soll!r}")
                out.append((feld, tuple(soll)))
            else:
                out.append((feld, (soll,)))
        else:
            out.append((feld, (bed,)))
    return out


def _meta_laden(text):
    return None if text is None else json.loads(text)


def _meta_kopie(meta):
    return None if meta is None else dict(meta)


class _Bestand:
    """Eine Sammlung im Speicher. Nur unter ``Vektorspeicher._lock`` benutzen."""

    __slots__ = ("ids", "pos", "metas", "matrix", "dim", "n")

    def __init__(self, ids: list[str], matrix: np.ndarray, metas: list, dim: int | None):
        self.ids = ids
        self.pos = {i: k for k, i in enumerate(ids)}
        self.metas = metas
        self.matrix = matrix          # (Kapazitaet, dim); belegt sind die ersten n Zeilen
        self.dim = dim
        self.n = len(ids)

    def setzen(self, item_id: str, v_norm: np.ndarray, meta) -> None:
        k = self.pos.get(item_id)
        if k is None:
            if self.n == 0 and self.dim != v_norm.size:
                self.dim = v_norm.size
                self.matrix = np.empty((0, self.dim), dtype=np.float32)
            if self.n >= self.matrix.shape[0]:
                # Kapazitaet um die Haelfte vergroessern: ein Lauf ueber die
                # ganze Bibliothek kopiert die Matrix so nur ein paar Dutzend
                # Mal statt bei jedem Titel.
                neu = np.empty((max(16, self.matrix.shape[0] * 3 // 2 + 1), self.dim),
                               dtype=np.float32)
                neu[:self.n] = self.matrix[:self.n]
                self.matrix = neu
            k = self.n
            self.n += 1
            self.ids.append(item_id)
            self.metas.append(meta)
            self.pos[item_id] = k
        else:
            self.metas[k] = meta
        self.matrix[k] = v_norm

    def entfernen(self, weg: set[str]) -> None:
        bleibt = [k for k in range(self.n) if self.ids[k] not in weg]
        self.matrix = self.matrix[np.asarray(bleibt, dtype=np.intp)]
        self.ids = [self.ids[k] for k in bleibt]
        self.metas = [self.metas[k] for k in bleibt]
        self.pos = {i: k for k, i in enumerate(self.ids)}
        self.n = len(self.ids)

    def maske(self, bedingungen: list[tuple[str, tuple]]) -> np.ndarray | None:
        if not bedingungen:
            return None
        ok = np.ones(self.n, dtype=bool)
        for feld, werte in bedingungen:
            # Fehlt das Feld (oder die ganzen Metadaten), passt der Eintrag
            # nicht - wie bei Chroma.
            ok &= np.fromiter(((m.get(feld) if m is not None else None) in werte
                               for m in self.metas), dtype=bool, count=self.n)
        return ok


class Sammlung:
    """Griff auf eine Sammlung; billig, haelt selbst keine Daten."""

    def __init__(self, speicher: "Vektorspeicher", name: str):
        self.speicher = speicher
        self.name = name

    def __repr__(self) -> str:
        return f"Sammlung({self.name!r})"

    def anzahl(self) -> int:
        return self.speicher._anzahl(self.name)

    def dimension(self) -> int | None:
        with self.speicher._lock:
            return self.speicher._bestand(self.name).dim

    def ids(self) -> list[str]:
        with self.speicher._lock:
            return list(self.speicher._bestand(self.name).ids)

    def schreiben(self, item_id: str, vektor, meta: dict | None = None,
                  dokument: str | None = None) -> None:
        self.speicher._schreiben(self.name, str(item_id), vektor, meta, dokument)

    def loeschen(self, ids: Iterable[str]) -> int:
        return self.speicher._loeschen(self.name, ids)

    def metadaten(self) -> dict[str, dict | None]:
        """{item_id: Metadaten} in der Reihenfolge der Sammlung (Kopien)."""
        with self.speicher._lock:
            b = self.speicher._bestand(self.name)
            return {i: _meta_kopie(m) for i, m in zip(b.ids, b.metas)}

    def metadaten_setzen(self, neu: dict[str, dict | None]) -> int:
        """Metadaten vorhandener Eintraege ERSETZEN; unbekannte IDs werden
        uebergangen. Gibt die Zahl der geaenderten Eintraege zurueck."""
        return self.speicher._metadaten_setzen(self.name, neu)

    def vektor(self, item_id: str) -> np.ndarray | None:
        """Normierter Vektor (Kopie) oder None."""
        with self.speicher._lock:
            b = self.speicher._bestand(self.name)
            k = b.pos.get(str(item_id))
            return None if k is None else b.matrix[k].copy()

    def alle(self) -> tuple[list[str], np.ndarray, list]:
        """(IDs, Matrix der normierten Vektoren, Metadaten) - alles Kopien."""
        with self.speicher._lock:
            b = self.speicher._bestand(self.name)
            dim = b.dim or 0
            return (list(b.ids), b.matrix[:b.n].reshape(b.n, dim).copy(),
                    [_meta_kopie(m) for m in b.metas])

    def suchen(self, vektor, n: int, filter: dict | None = None
               ) -> list[tuple[str, float, dict | None]]:
        """Die ``n`` naechsten Eintraege zu ``vektor`` nach Kosinus-Abstand:
        Liste von (item_id, abstand, metadaten), aufsteigend nach Abstand, bei
        Gleichstand in der Reihenfolge der Sammlung."""
        return self.speicher._suchen(self.name, vektor, n, filter)

    def aehnlichkeiten(self, vektor) -> tuple[list[str], np.ndarray]:
        """Kosinus-Aehnlichkeit von ``vektor`` zu jedem Eintrag (nicht
        beschnitten): (IDs, float32-Feld gleicher Laenge)."""
        return self.speicher._aehnlichkeiten(self.name, vektor)

    def rohdaten(self) -> list[tuple[str, np.ndarray, dict | None, str | None]]:
        """Alle Eintraege so, wie sie in der Datenbank stehen: (item_id,
        Vektor wie geliefert, Metadaten, Dokument). Fuer Pruefungen und
        Werkzeuge; die App selbst arbeitet mit den normierten Vektoren."""
        with closing(self.speicher._verbinden()) as con:
            zeilen = con.execute(
                "SELECT item_id, vektor, meta, dokument FROM vektor WHERE sammlung = ? "
                "ORDER BY rowid", (self.name,)).fetchall()
        return [(i, np.frombuffer(v, dtype="<f4").astype(np.float32), _meta_laden(m), d)
                for i, v, m, d in zeilen]

    def uebernehmen(self, ids: list[str], vektoren, metas: list | None = None,
                    dokumente: list | None = None) -> list[str]:
        """Viele Eintraege auf einmal anlegen (Uebernahme eines Exports).

        Legt nur IDs an, die noch fehlen; vorhandene Eintraege bleiben, wie sie
        sind - sie sind neuer als jeder Export. Eine Transaktion fuer alles.
        Gibt die angelegten IDs zurueck."""
        return self.speicher._uebernehmen(self.name, ids, vektoren, metas, dokumente)


class Vektorspeicher:
    """Alle Sammlungen in der Tabelle ``vektor`` einer SQLite-Datei."""

    def __init__(self, pfad):
        self.pfad = Path(pfad)
        self._lock = threading.RLock()
        self._schreib_lock = threading.RLock()
        self._bestaende: dict[str, _Bestand] = {}
        self._tabelle_da = False

    def __repr__(self) -> str:
        return f"Vektorspeicher({str(self.pfad)!r})"

    # ── Verbindung ─────────────────────────────────────────────────────────
    def _verbinden(self) -> sqlite3.Connection:
        # isolation_level=None: keine stillen Transaktionen des sqlite3-Moduls;
        # geschrieben wird nur in ausdruecklichen BEGIN IMMEDIATE ... COMMIT.
        # 30 s Wartezeit, falls gerade ein anderer Zugriff (SQLAlchemy) die
        # Datei sperrt.
        con = sqlite3.connect(str(self.pfad), timeout=30, isolation_level=None,
                              check_same_thread=False)
        if not self._tabelle_da:
            con.execute(TABELLE_SQL)
            self._tabelle_da = True
        return con

    @staticmethod
    def _transaktion(con: sqlite3.Connection, schritte) -> None:
        con.execute("BEGIN IMMEDIATE")
        try:
            schritte(con)
            con.execute("COMMIT")
        except BaseException:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise

    # ── Bestand im Speicher ────────────────────────────────────────────────
    def sammlung(self, name: str) -> Sammlung:
        if not isinstance(name, str) or not _NAME_ERLAUBT.match(name):
            raise ValueError(f"ungueltiger Sammlungsname: {name!r}")
        return Sammlung(self, name)

    def sammlungen(self) -> dict[str, int]:
        """{Name: Anzahl} aller Sammlungen in der Datenbank."""
        with closing(self._verbinden()) as con:
            return {n: int(c) for n, c in con.execute(
                "SELECT sammlung, COUNT(*) FROM vektor GROUP BY sammlung ORDER BY sammlung")}

    def vergessen(self, name: str | None = None) -> None:
        """Bestand im Speicher verwerfen (alle, ohne Namen); der naechste
        Zugriff laedt neu aus der Datenbank."""
        with self._lock:
            if name is None:
                self._bestaende.clear()
            else:
                self._bestaende.pop(name, None)

    def _bestand(self, name: str) -> _Bestand:
        """Bestand holen, beim ersten Zugriff aus der Datenbank laden. Der
        Aufrufer haelt ``_lock``."""
        b = self._bestaende.get(name)
        if b is not None:
            return b
        with closing(self._verbinden()) as con:
            zeilen = con.execute(
                "SELECT item_id, dim, vektor, meta FROM vektor WHERE sammlung = ? "
                "ORDER BY rowid", (name,)).fetchall()
        dim = zeilen[0][1] if zeilen else None
        gut = [z for z in zeilen if z[1] == dim and len(z[2]) == 4 * dim]
        if len(gut) != len(zeilen):
            # Kann ueber die Schreibwege nicht entstehen (sie pruefen die
            # Dimension); nur ein von Hand veraenderter Bestand sieht so aus.
            # Lieber diese Eintraege auslassen als die ganze Sammlung.
            log.warning("Vektorspeicher: %d Eintraege in %r passen nicht zur Dimension %s "
                        "und werden uebergangen", len(zeilen) - len(gut), name, dim)
        if gut:
            roh = np.frombuffer(b"".join(z[2] for z in gut), dtype="<f4").reshape(len(gut), dim)
            matrix = _normiert(roh)
        else:
            matrix = np.empty((0, dim or 0), dtype=np.float32)
        b = _Bestand([z[0] for z in gut], matrix, [_meta_laden(z[3]) for z in gut], dim)
        self._bestaende[name] = b
        return b

    def _anzahl(self, name: str) -> int:
        with self._lock:
            b = self._bestaende.get(name)
            if b is not None:
                return b.n
        # Nicht geladen: zaehlen, ohne die Matrix zu laden. Wichtig fuer die
        # Sammlungen anderer Modelle, die sonst nur fuer eine Zahl im Log
        # Dutzende MB in den Speicher holten.
        with closing(self._verbinden()) as con:
            return int(con.execute("SELECT COUNT(*) FROM vektor WHERE sammlung = ?",
                                   (name,)).fetchone()[0])

    # ── Schreiben ──────────────────────────────────────────────────────────
    def _schreiben(self, name, item_id, vektor, meta, dokument) -> None:
        v = _als_vektor(vektor)
        meta_text = None if meta is None else json.dumps(meta, ensure_ascii=False)
        with self._schreib_lock:
            with self._lock:
                b = self._bestand(name)
                if b.n and b.dim != v.size:
                    # Wie Chroma: ein Vektor mit anderer Dimension gehoert zu
                    # einem anderen Modell und wuerde jede Abfrage verderben.
                    raise ValueError(f"Dimension {v.size} passt nicht zur Sammlung "
                                     f"{name!r} ({b.dim})")
            zeile = (name, item_id, int(v.size), v.astype("<f4").tobytes(), meta_text,
                     dokument, _jetzt_iso())
            with closing(self._verbinden()) as con:
                self._transaktion(con, lambda c: c.execute(_SCHREIBEN, zeile))
            with self._lock:
                b = self._bestaende.get(name)
                if b is not None:
                    # Metadaten so ablegen, wie ein Neuladen sie liefert (JSON).
                    b.setzen(item_id, _normiert(v), _meta_laden(meta_text))

    def _loeschen(self, name, ids) -> int:
        ids = list(dict.fromkeys(str(i) for i in ids))
        with self._schreib_lock:
            with self._lock:
                b = self._bestand(name)
                weg = [i for i in ids if i in b.pos]
            if not weg:
                return 0
            with closing(self._verbinden()) as con:
                self._transaktion(con, lambda c: c.executemany(
                    "DELETE FROM vektor WHERE sammlung = ? AND item_id = ?",
                    [(name, i) for i in weg]))
            with self._lock:
                b = self._bestaende.get(name)
                if b is not None:
                    b.entfernen(set(weg))
            return len(weg)

    def _metadaten_setzen(self, name, neu: dict) -> int:
        with self._schreib_lock:
            with self._lock:
                b = self._bestand(name)
                aenderung = [(str(i), None if m is None else json.dumps(m, ensure_ascii=False))
                             for i, m in neu.items() if str(i) in b.pos]
            if not aenderung:
                return 0
            jetzt = _jetzt_iso()
            with closing(self._verbinden()) as con:
                self._transaktion(con, lambda c: c.executemany(
                    "UPDATE vektor SET meta = ?, geaendert_am = ? WHERE sammlung = ? AND item_id = ?",
                    [(m, jetzt, name, i) for i, m in aenderung]))
            with self._lock:
                b = self._bestaende.get(name)
                if b is not None:
                    for i, m in aenderung:
                        k = b.pos.get(i)
                        if k is not None:
                            b.metas[k] = _meta_laden(m)
            return len(aenderung)

    def _uebernehmen(self, name, ids, vektoren, metas, dokumente) -> list[str]:
        m = np.asarray(vektoren, dtype=np.float32)
        ids = [str(i) for i in ids]
        if m.ndim != 2 or m.shape[0] != len(ids):
            raise ValueError(f"{name}: {len(ids)} IDs, aber Vektoren der Form {m.shape}")
        if len(set(ids)) != len(ids):
            raise ValueError(f"{name}: doppelte IDs")
        if not np.isfinite(m).all():
            raise ValueError(f"{name}: Vektoren enthalten NaN oder Unendlich")
        metas = list(metas) if metas is not None else [None] * len(ids)
        dokumente = list(dokumente) if dokumente is not None else [None] * len(ids)
        if len(metas) != len(ids) or len(dokumente) != len(ids):
            raise ValueError(f"{name}: Metadaten/Dokumente passen nicht zur Zahl der IDs")
        with self._schreib_lock:
            with closing(self._verbinden()) as con:
                vorhanden = {r[0] for r in con.execute(
                    "SELECT item_id FROM vektor WHERE sammlung = ?", (name,))}
                dims = {r[0] for r in con.execute(
                    "SELECT DISTINCT dim FROM vektor WHERE sammlung = ?", (name,))}
                if vorhanden and m.shape[0] and dims != {m.shape[1]}:
                    raise ValueError(f"{name}: Dimension {m.shape[1]} passt nicht zum "
                                     f"vorhandenen Bestand ({sorted(dims)})")
                neu = [k for k, i in enumerate(ids) if i not in vorhanden]
                jetzt = _jetzt_iso()
                le = m.astype("<f4")
                zeilen = [(name, ids[k], int(m.shape[1]), le[k].tobytes(),
                           None if metas[k] is None else json.dumps(metas[k], ensure_ascii=False),
                           dokumente[k], jetzt) for k in neu]
                if zeilen:
                    self._transaktion(con, lambda c: c.executemany(_EINFUEGEN, zeilen))
            # Neu laden statt im Speicher nachzutragen: die Uebernahme ist
            # selten, und so ist sicher, dass Speicher und Datenbank gleich sind.
            self.vergessen(name)
        return [ids[k] for k in neu]

    # ── Lesen ──────────────────────────────────────────────────────────────
    def _anfrage(self, b: _Bestand, vektor) -> np.ndarray:
        q = _als_vektor(vektor)
        if b.dim is not None and q.size != b.dim:
            raise ValueError(f"Anfrage mit {q.size} Dimensionen, die Sammlung hat {b.dim}")
        return _normiert(q)

    def _suchen(self, name, vektor, n, filt) -> list[tuple[str, float, dict | None]]:
        bedingungen = _filter_pruefen(filt)
        n = int(n)
        with self._lock:
            b = self._bestand(name)
            if b.n == 0 or n <= 0:
                return []
            q = self._anfrage(b, vektor)
            # float32 wie hnswlib (InnerProductDistance: 1.0f - <a, b>).
            abstand = np.float32(1.0) - b.matrix[:b.n] @ q
            maske = b.maske(bedingungen)
            stellen = np.flatnonzero(maske) if maske is not None else np.arange(b.n)
            if stellen.size == 0:
                return []
            teil = abstand[stellen]
            # stable: bei gleichem Abstand gewinnt die fruehere Zeile.
            folge = np.argsort(teil, kind="stable")[:n]
            return [(b.ids[stellen[j]], float(teil[j]), _meta_kopie(b.metas[stellen[j]]))
                    for j in folge]

    def _aehnlichkeiten(self, name, vektor) -> tuple[list[str], np.ndarray]:
        with self._lock:
            b = self._bestand(name)
            if b.n == 0:
                return [], np.empty(0, dtype=np.float32)
            q = self._anfrage(b, vektor)
            return list(b.ids), b.matrix[:b.n] @ q


# ---------------------------------------------------------------------------
# Uebernahme eines Chroma-Exports (tools/chroma_export.py)
# ---------------------------------------------------------------------------
# Das Format schreibt tools/chroma_export.py in einem laufenden 1.0.x-Container:
#   export.json             Inhaltsverzeichnis mit Anzahl, Dimension, Pruefsummen
#   <sammlung>.npz          "ids" (Unicode-Feld) und "vektoren" (float32, N x D)
#   <sammlung>.json         "ids", "metadaten", "dokumente" - gleiche Reihenfolge
# Geladen wird mit allow_pickle=False: eine .npz kann sonst beim Laden
# beliebigen Code ausfuehren.
EXPORT_FORMAT = "cinematekai-vektor-export"
EXPORT_VERSION = 1


def _sha256(pfad: Path) -> str:
    h = hashlib.sha256()
    with pfad.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _datei_im_ordner(ordner: Path, name) -> Path:
    if not isinstance(name, str) or Path(name).name != name or name in ("", ".", ".."):
        raise ValueError(f"ungueltiger Dateiname im Export: {name!r}")
    return ordner / name


def _sammlung_lesen(ordner: Path, eintrag: dict) -> tuple[str, list[str], np.ndarray, list, list]:
    """Eine Sammlung des Exports lesen und pruefen. Wirft ValueError mit
    einer Beschreibung, was nicht stimmt."""
    name = eintrag.get("name")
    if not isinstance(name, str) or not _NAME_ERLAUBT.match(name):
        raise ValueError(f"ungueltiger Sammlungsname {name!r}")
    npz = _datei_im_ordner(ordner, eintrag.get("vektoren"))
    js = _datei_im_ordner(ordner, eintrag.get("eintraege"))
    for pfad, schluessel in ((npz, "sha256_vektoren"), (js, "sha256_eintraege")):
        if not pfad.is_file():
            raise ValueError(f"{name}: Datei {pfad.name} fehlt")
        if _sha256(pfad) != eintrag.get(schluessel):
            raise ValueError(f"{name}: Pruefsumme von {pfad.name} stimmt nicht")
    with np.load(npz, allow_pickle=False) as daten:
        ids = [str(i) for i in daten["ids"].tolist()]
        vektoren = np.asarray(daten["vektoren"], dtype=np.float32)
    inhalt = json.loads(js.read_text(encoding="utf-8"))
    anzahl, dim = int(eintrag.get("anzahl", -1)), int(eintrag.get("dimension", -1))
    if len(ids) != anzahl or inhalt.get("ids") != ids:
        raise ValueError(f"{name}: IDs passen nicht zum Inhaltsverzeichnis "
                         f"({len(ids)} statt {anzahl} oder andere Reihenfolge)")
    if anzahl and (vektoren.ndim != 2 or vektoren.shape != (anzahl, dim)):
        raise ValueError(f"{name}: Vektoren der Form {vektoren.shape}, erwartet ({anzahl}, {dim})")
    metas, doks = inhalt.get("metadaten"), inhalt.get("dokumente")
    if not isinstance(metas, list) or len(metas) != anzahl \
            or not isinstance(doks, list) or len(doks) != anzahl:
        raise ValueError(f"{name}: Metadaten oder Dokumente unvollstaendig")
    if anzahl and not np.isfinite(vektoren).all():
        raise ValueError(f"{name}: Vektoren enthalten NaN oder Unendlich")
    if len(set(ids)) != len(ids):
        raise ValueError(f"{name}: doppelte IDs")
    return name, ids, vektoren.reshape(anzahl, dim if anzahl else 0), metas, doks


def export_uebernehmen(speicher: Vektorspeicher, ordner: Path,
                       stempel: str | None = None) -> dict | None:
    """Einen Export aus ``ordner`` (data/vektor_export) in den Speicher holen.

    Je Sammlung: ist sie im neuen Speicher leer, kommt der ganze Export
    hinein; hat sie schon Eintraege (diese Version lief bereits und hat neu
    eingebettet), nur die fehlenden IDs - Vorhandenes ist neuer und bleibt.
    Geprueft werden vorher Pruefsummen, Anzahl, Dimension und Reihenfolge.

    Ist alles uebernommen, wird der Ordner in
    ``vektor_export.uebernommen-<datum>`` umbenannt, damit der naechste Start
    ihn nicht erneut liest; geloescht wird nichts. Schlaegt eine Sammlung
    fehl, bleibt der Ordner liegen (der naechste Start versucht es wieder,
    Uebernommenes wird dabei nicht doppelt angelegt), und der Fehler steht
    im Ergebnis und im Log.

    Rueckgabe: None, wenn es keinen Export gibt; sonst
    ``{"sammlungen": {name: {"im_export", "uebernommen", "ids"}},
    "fehler": [...], "umbenannt_in": str | None}``.
    """
    ordner = Path(ordner)
    if not ordner.is_dir():
        return None
    erg: dict = {"sammlungen": {}, "fehler": [], "umbenannt_in": None}
    verzeichnis = ordner / "export.json"
    try:
        if not verzeichnis.is_file():
            raise ValueError("export.json fehlt - der Export ist unvollstaendig "
                             "(abgebrochen?) und wird nicht uebernommen")
        inhalt = json.loads(verzeichnis.read_text(encoding="utf-8"))
        if inhalt.get("format") != EXPORT_FORMAT or inhalt.get("version") != EXPORT_VERSION:
            raise ValueError(f"unbekanntes Format {inhalt.get('format')!r} "
                             f"Version {inhalt.get('version')!r}")
        eintraege = inhalt.get("sammlungen")
        if not isinstance(eintraege, list):
            raise ValueError("export.json ohne Liste der Sammlungen")
    except (ValueError, OSError) as e:
        log.error("Vektor-Export in %s nicht uebernommen: %s", ordner, e)
        erg["fehler"].append(str(e))
        return erg

    for eintrag in eintraege:
        name = (eintrag or {}).get("name") if isinstance(eintrag, dict) else None
        try:
            if not isinstance(eintrag, dict):
                raise ValueError(f"ungueltiger Eintrag {eintrag!r}")
            name, ids, vektoren, metas, doks = _sammlung_lesen(ordner, eintrag)
            sammlung = speicher.sammlung(name)
            vorher = sammlung.anzahl()
            neu = sammlung.uebernehmen(ids, vektoren, metas, doks) if ids else []
        except Exception as e:
            log.error("Vektor-Export: Sammlung %r nicht uebernommen: %s", name, e)
            text = str(e)
            erg["fehler"].append(text if text.startswith(f"{name}: ") else f"{name}: {text}")
            continue
        erg["sammlungen"][name] = {"im_export": len(ids), "uebernommen": len(neu), "ids": neu}
        if vorher:
            log.info("Vektor-Export: %r hatte schon %d Vektoren - %d fehlende aus dem "
                     "Export ergaenzt, %d vorhandene behalten", name, vorher, len(neu),
                     len(ids) - len(neu))
        else:
            log.info("Vektor-Export: %r mit %d Vektoren (%d Dimensionen, Abstand %s) "
                     "uebernommen", name, len(neu), vektoren.shape[1] if len(ids) else 0,
                     eintrag.get("space") or "?")
        if eintrag.get("space") not in (None, "cosine"):
            log.warning("Vektor-Export: %r wurde in Chroma mit Abstand %r gefuehrt; "
                        "dieser Speicher rechnet immer mit Kosinus - die Reihenfolge bei "
                        "normierten Vektoren bleibt gleich, die Werte nicht",
                        name, eintrag.get("space"))

    if erg["fehler"]:
        log.error("Vektor-Export: %d Fehler - %s bleibt liegen und wird beim naechsten "
                  "Start erneut gelesen", len(erg["fehler"]), ordner)
        return erg
    stempel = stempel or datetime.now().strftime("%Y-%m-%d_%H%M%S")
    ziel = ordner.with_name(f"{ordner.name}.uebernommen-{stempel}")
    zaehler = 2
    while ziel.exists():
        ziel = ordner.with_name(f"{ordner.name}.uebernommen-{stempel}-{zaehler}")
        zaehler += 1
    try:
        ordner.rename(ziel)
        erg["umbenannt_in"] = str(ziel)
        log.info("Vektor-Export uebernommen und umbenannt in %s", ziel.name)
    except OSError as e:
        # Uebernommen ist er trotzdem; der naechste Start findet alles schon
        # vor und legt nichts doppelt an.
        log.warning("Vektor-Export uebernommen, aber Umbenennen fehlgeschlagen: %s", e)
        erg["fehler"].append(f"umbenennen: {e}")
    return erg
