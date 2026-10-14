"""Export the ChromaDB vector index of CinematekAI 1.0.x for CinematekAI 1.1.

CinematekAI 1.1 no longer uses ChromaDB; it keeps its vectors in its own
table in the SQLite database. This script carries the existing vectors over,
so the library does not have to be embedded again (with a local model on a
CPU that takes hours).

Run it ONCE inside the still running OLD container (1.0.x), from the folder
that holds this repository, BEFORE updating:

    docker exec -i cinematekai python - < tools/chroma_export.py

It reads every collection under /app/data/chroma with the chromadb package of
the old image and writes /app/data/vektor_export/:

    export.json          table of contents: collections, counts, dimensions,
                         SHA-256 checksums
    <collection>.npz     "ids" and "vektoren" (float32 matrix, one row per id)
    <collection>.json    "ids", "metadaten", "dokumente" in the same order

Then update as usual. At start, CinematekAI 1.1 imports the export and renames
the folder to vektor_export.uebernommen-<date>.

The Chroma data is not changed: the script reads a copy in a temporary folder
next to it and removes that copy afterwards. data/chroma stays where it is, so
going back to 1.0.x keeps working. Best run it while no embedding run is
active (not during the daily run at night).

Optional argument: the data folder (default: $CINEMATEKAI_DATA or /app/data),
e.g. "python - /path/to/data < tools/chroma_export.py".

Exit code 0 = export written, 1 = error, 2 = no Chroma index found.
"""
# Bewusst eigenstaendig: kein Import aus backend/ (das alte Image bringt seine
# eigenen Module mit, und config.py legte beim Import Ordner an). Gebraucht
# werden nur die Standardbibliothek, numpy und chromadb - beides steckt im
# Image von 1.0.x.
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone

FORMAT = "cinematekai-vektor-export"
VERSION = 1

# Seitenweise lesen: alles auf einmal hielte bei 9000 Vektoren alle Dokumente,
# Metadaten und Vektoren doppelt im Speicher (Chroma-Antwort + Ergebnis).
SEITE = 500

# So viel freier Platz muss mindestens da sein, gemessen an der Groesse von
# data/chroma: einmal fuer die Kopie, dazu der Export (kleiner als der Index,
# er enthaelt keinen HNSW-Graphen) und Luft.
PLATZ_FAKTOR = 2.5


def melden(text=""):
    print(text, flush=True)


def daten_ordner():
    if len(sys.argv) > 1 and sys.argv[1]:
        return os.path.abspath(sys.argv[1])
    return os.environ.get("CINEMATEKAI_DATA") or os.environ.get("CINEAI_DATA") or "/app/data"


def ordner_groesse(pfad):
    gesamt = 0
    for wurzel, _, dateien in os.walk(pfad):
        for d in dateien:
            try:
                gesamt += os.path.getsize(os.path.join(wurzel, d))
            except OSError:
                pass
    return gesamt


def kopie_anlegen(quelle, ziel):
    """data/chroma nach ``ziel`` kopieren, ohne die Quelle zu veraendern.

    Zuerst die Datenbank chroma.sqlite3, und zwar ueber die Online-Backup-API
    von SQLite: das laufende Backend kann gerade hineinschreiben, eine blosse
    Dateikopie erwischte dann einen halben Stand. Danach die HNSW-Dateien der
    Sammlungen. Diese Reihenfolge, weil Chroma Vektoren, die noch nicht in den
    HNSW-Dateien stehen, beim Oeffnen aus seinem Protokoll in der Datenbank
    nachholt: sind die HNSW-Dateien gleich alt oder neuer als die
    Datenbank-Kopie, fehlt nichts. Umgekehrt koennte Chroma Protokolleintraege
    schon geloescht haben, die eine aeltere HNSW-Kopie noch braeuchte.

    Die Quelle wird nur lesend geoeffnet (mode=ro).
    """
    os.makedirs(ziel, exist_ok=True)
    db_quelle = os.path.join(quelle, "chroma.sqlite3")
    src = sqlite3.connect("file:%s?mode=ro" % db_quelle, uri=True, timeout=60)
    dst = sqlite3.connect(os.path.join(ziel, "chroma.sqlite3"))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    for wurzel, _, dateien in os.walk(quelle):
        rel = os.path.relpath(wurzel, quelle)
        for d in dateien:
            # Die Datenbank samt ihren Begleitdateien (-wal, -shm, -journal)
            # steckt schon in der Sicherung oben.
            if rel == "." and d.startswith("chroma.sqlite3"):
                continue
            os.makedirs(os.path.join(ziel, rel), exist_ok=True)
            shutil.copy2(os.path.join(wurzel, d), os.path.join(ziel, rel, d))


def modell_hinweis(name):
    """Welches Modell vermutlich hinter einer Sammlung steckt - nur zur
    Anzeige. Die neue Version braucht das nicht: sie uebernimmt jede Sammlung
    unter genau ihrem Namen und findet sie dort wieder, weil sie die Namen
    nach derselben Regel bildet (embeddings._sammlungsname):
        movies, user_profiles                 -> nomic-embed-text (alte Namen)
        movies__<modell>, user_profiles__...  -> <modell>, Sonderzeichen als _
    """
    for basis, art in (("movies", "filme"), ("user_profiles", "profile")):
        if name == basis:
            return art, "nomic-embed-text"
        if name.startswith(basis + "__"):
            return art, name[len(basis) + 2:]
    return "unbekannt", ""


def chroma_schliessen(client):
    """Dateien der Kopie freigeben. Unter Linux liesse sich die Kopie auch mit
    offenem Client loeschen; unter Windows (Test, eigene Installation) haelt
    er die Dateien fest, und die Kopie bliebe im Datenordner liegen."""
    if client is None:
        return
    try:
        client._system.stop()
    except Exception:
        pass
    try:
        from chromadb.api.shared_system_client import SharedSystemClient
        SharedSystemClient.clear_system_cache()
    except Exception:
        pass
    import gc
    gc.collect()


def sha256(pfad):
    h = hashlib.sha256()
    with open(pfad, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def sammlung_lesen(sammlung, np):
    """Alle Eintraege einer Sammlung: (ids, Matrix, Metadaten, Dokumente)."""
    anzahl = sammlung.count()
    ids, vektoren, metas, doks = [], [], [], []
    start = 0
    while start < anzahl:
        teil = sammlung.get(include=["embeddings", "metadatas", "documents"],
                            limit=SEITE, offset=start)
        t_ids = list(teil.get("ids") or [])
        if not t_ids:
            break
        t_vek = teil.get("embeddings")
        t_meta = teil.get("metadatas")
        t_dok = teil.get("documents")
        if t_vek is None or len(t_vek) != len(t_ids):
            raise RuntimeError("Chroma lieferte zu %d IDs keine passenden Vektoren"
                               % len(t_ids))
        for k, item_id in enumerate(t_ids):
            v = t_vek[k]
            if v is None:
                raise RuntimeError("Vektor fuer %r fehlt" % item_id)
            ids.append(str(item_id))
            vektoren.append(np.asarray(v, dtype=np.float32))
            metas.append(dict(t_meta[k]) if t_meta is not None and t_meta[k] is not None else None)
            doks.append(t_dok[k] if t_dok is not None else None)
        start += len(t_ids)
    if len(ids) != anzahl:
        raise RuntimeError("count() meldet %d Eintraege, gelesen wurden %d" % (anzahl, len(ids)))
    if len(set(ids)) != len(ids):
        raise RuntimeError("doppelte IDs beim seitenweisen Lesen")
    if not vektoren:
        return ids, np.zeros((0, 0), dtype=np.float32), metas, doks
    dims = {v.shape for v in vektoren}
    if len(dims) != 1 or len(next(iter(dims))) != 1:
        raise RuntimeError("Vektoren unterschiedlicher Form: %s" % sorted(dims))
    matrix = np.vstack(vektoren).astype(np.float32)
    if not np.isfinite(matrix).all():
        raise RuntimeError("Vektoren enthalten NaN oder Unendlich")
    return ids, matrix, metas, doks


def export_schreiben(client, ziel, np, chromadb_version, quelle):
    """Alle Sammlungen nach ``ziel`` schreiben; gibt die Eintraege fuer
    export.json zurueck."""
    namen = sorted(c if isinstance(c, str) else c.name for c in client.list_collections())
    eintraege = []
    for name in namen:
        sammlung = client.get_collection(name)
        ids, matrix, metas, doks = sammlung_lesen(sammlung, np)
        meta = dict(sammlung.metadata or {})
        art, modell = modell_hinweis(name)
        npz = name + ".npz"
        js = name + ".json"
        # ids als Unicode-Feld, nicht als Objekt-Feld: so laedt die neue
        # Version die Datei mit allow_pickle=False.
        np.savez(os.path.join(ziel, npz), ids=np.array(ids, dtype=str),
                 vektoren=matrix)
        with open(os.path.join(ziel, js), "w", encoding="utf-8") as f:
            json.dump({"name": name, "metadata": meta, "ids": ids,
                       "metadaten": metas, "dokumente": doks}, f, ensure_ascii=False)
        eintraege.append({
            "name": name,
            "art": art,
            "modell": modell,
            # Chroma rechnet ohne Angabe mit l2; die App legte ihre Sammlungen
            # immer mit cosine an.
            "space": str(meta.get("hnsw:space", "l2")),
            "anzahl": len(ids),
            "dimension": int(matrix.shape[1]) if len(ids) else 0,
            "vektoren": npz,
            "eintraege": js,
            "sha256_vektoren": sha256(os.path.join(ziel, npz)),
            "sha256_eintraege": sha256(os.path.join(ziel, js)),
        })
    return {
        "format": FORMAT,
        "version": VERSION,
        "erstellt_am": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "quelle": quelle,
        "chromadb": chromadb_version,
        "sammlungen": eintraege,
    }


def main():
    daten = daten_ordner()
    quelle = os.path.join(daten, "chroma")
    ziel = os.path.join(daten, "vektor_export")
    melden("CinematekAI: Chroma-Index exportieren")
    melden("  Quelle: %s" % quelle)
    if not os.path.isfile(os.path.join(quelle, "chroma.sqlite3")):
        melden("Kein Chroma-Index gefunden (%s fehlt). Nichts zu exportieren." %
               os.path.join(quelle, "chroma.sqlite3"))
        return 2

    # Telemetrie aus, bevor chromadb geladen wird: das Image setzt die
    # Variable schon, ein Aufruf ausserhalb (Test, eigene Installation) nicht.
    os.environ["ANONYMIZED_TELEMETRY"] = "False"
    try:
        import numpy as np
        import chromadb
        from chromadb.config import Settings
    except ImportError as e:
        melden("FEHLER: %s. Das Skript gehoert in einen Container der Version 1.0.x "
               "(dort ist chromadb installiert), nicht in 1.1 oder neuer." % e)
        return 1

    groesse = ordner_groesse(quelle)
    frei = shutil.disk_usage(daten).free
    if frei < groesse * PLATZ_FAKTOR:
        melden("FEHLER: zu wenig freier Platz in %s: %.0f MB frei, gebraucht etwa %.0f MB "
               "(Kopie des Index und Export)." % (daten, frei / 1e6, groesse * PLATZ_FAKTOR / 1e6))
        return 1

    # Kopie und Zwischenstand liegen im Datenordner selbst: derselbe
    # Datentraeger, also ist das Umbenennen am Ende ein einziger Schritt, und
    # die beschreibbare Schicht des Containers fuellt sich nicht.
    kopie = tempfile.mkdtemp(prefix=".chroma_kopie-", dir=daten)
    tmp = tempfile.mkdtemp(prefix=".vektor_export_tmp-", dir=daten)
    client = None
    try:
        melden("  Kopie anlegen (%.0f MB) ..." % (groesse / 1e6))
        kopie_anlegen(quelle, kopie)
        client = chromadb.PersistentClient(
            path=kopie, settings=Settings(anonymized_telemetry=False, allow_reset=False))
        inhalt = export_schreiben(client, tmp, np, getattr(chromadb, "__version__", "?"),
                                  quelle)
        # Das Inhaltsverzeichnis zuletzt: die neue Version uebernimmt nur
        # einen Ordner mit export.json, ein abgebrochener Export hat keins.
        with open(os.path.join(tmp, "export.json"), "w", encoding="utf-8") as f:
            json.dump(inhalt, f, ensure_ascii=False, indent=1)
        chroma_schliessen(client)
        client = None

        vorher = None
        if os.path.exists(ziel):
            # Ein frueherer, noch nicht uebernommener Export wird nicht
            # geloescht, nur beiseitegelegt.
            vorher = "%s.vorher-%s" % (ziel, datetime.now().strftime("%Y-%m-%d_%H%M%S"))
            os.rename(ziel, vorher)
        os.rename(tmp, ziel)
        tmp = None
    except Exception as e:
        melden("FEHLER beim Export: %s: %s" % (type(e).__name__, e))
        melden("Es wurde nichts veraendert; %s ist unberuehrt." % quelle)
        return 1
    finally:
        chroma_schliessen(client)
        shutil.rmtree(kopie, ignore_errors=True)
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    melden("")
    melden("Export geschrieben: %s" % ziel)
    if vorher:
        melden("  (ein frueherer Export liegt jetzt unter %s)" % vorher)
    gesamt = 0
    for s in inhalt["sammlungen"]:
        gesamt += s["anzahl"]
        melden("  %-34s %6d Vektoren  %4d Dim.  %-8s %s" % (
            s["name"], s["anzahl"], s["dimension"], s["art"],
            ("Modell " + s["modell"]) if s["modell"] else ""))
    melden("  zusammen %d Vektoren in %d Sammlungen" % (gesamt, len(inhalt["sammlungen"])))
    melden("")
    melden("Naechster Schritt: wie gewohnt auf 1.1 aktualisieren. Die neue Version")
    melden("uebernimmt den Export beim Start und benennt den Ordner um. data/chroma")
    melden("bleibt unveraendert liegen (Rueckweg auf 1.0.x).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
