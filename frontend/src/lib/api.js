/* Kleine Helfer fuer die Einrichtungs- und Einstellungsseiten.

   Die aelteren Seiten rufen fetch() direkt auf und lesen `detail` selbst aus.
   Fuer die neuen Formulare waere das in jedem Knopf dieselbe Handvoll Zeilen:
   Antwort lesen, Fehlertext finden (FastAPI liefert ihn mal als Text, bei
   Pruefungsfehlern als Liste), Netzwerkfehler abfangen. Das steht deshalb
   einmal hier.

   Die Fehlertexte kommen aus dem Woerterbuch `allgemein`, Zahlen und Zeiten
   in der Schreibweise der gewaehlten Sprache (lib/i18n.svelte.js). Die
   Namen zahl/zeitpunkt/sekunden bleiben, damit die Seiten nichts merken. */
import { t, zahl as zahlI18n, datum } from './i18n.svelte.js';

/** JSON-Aufruf ans Backend. Wirft nie; Ergebnis {ok, status, daten, fehler}. */
export async function api(pfad, { method = 'GET', body } = {}) {
  const init = { method };
  if (body !== undefined) {
    init.headers = { 'Content-Type': 'application/json' };
    init.body = JSON.stringify(body);
  }
  let r;
  try {
    r = await fetch(pfad, init);
  } catch {
    return { ok: false, status: 0, daten: null,
             fehler: t('allgemein.keineVerbindung') };
  }
  let daten = null;
  try { daten = await r.json(); } catch { /* leere oder Nicht-JSON-Antwort */ }
  if (!r.ok) return { ok: false, status: r.status, daten, fehler: fehlerText(daten, r.status) };
  return { ok: true, status: r.status, daten, fehler: '' };
}

function fehlerText(daten, status) {
  const d = daten?.detail;
  if (typeof d === 'string' && d) return d;
  // Pruefungsfehler von FastAPI: [{loc: [...], msg: "..."}]
  if (Array.isArray(d) && d.length) {
    return d.map((x) => x?.msg).filter(Boolean).join(' · ')
      || t('allgemein.fehlerStatus', { status });
  }
  if (status === 401) return t('allgemein.nichtAngemeldet');
  if (status === 403) return t('allgemein.keineRechte');
  return t('allgemein.fehlerStatus', { status });
}

/** 1234 -> "1,2 s" (englisch "1.2 s") */
export function sekunden(ms) {
  if (ms == null || Number.isNaN(Number(ms))) return '';
  return `${zahlI18n(Number(ms) / 1000, 1)} s`;
}

/** ISO-Zeitpunkt -> "10.10.2026, 11:23" (englisch "10 Oct 2026, 11:23") */
export function zeitpunkt(iso) {
  return datum(iso, { dateStyle: 'medium', timeStyle: 'short' });
}

/** Zahl in der Schreibweise der Sprache, z. B. 0.38 -> "0,38" / "0.38" */
export function zahl(x, stellen = 0) {
  return zahlI18n(x, stellen);
}

/** Adresse fuer die Speicherung saeubern: Leerraum und Schraegstriche am Ende weg. */
export function adresse(u) {
  return String(u ?? '').trim().replace(/\/+$/, '');
}

/* Die Zeitzonen, die der Browser kennt — fuer die Vorschlagsliste. Aeltere
   Browser haben supportedValuesOf nicht; dann reichen die naheliegenden. UTC
   fehlt in der Liste mancher Browser, obwohl das Backend es annimmt. */
export function zeitzonen() {
  let liste = [];
  try { liste = Intl.supportedValuesOf('timeZone'); } catch { /* alter Browser */ }
  if (!liste.length) liste = ['Europe/Berlin', 'Europe/Vienna', 'Europe/Zurich', 'Europe/London'];
  return liste.includes('UTC') ? liste : [...liste, 'UTC'];
}

/* /api/app-info wird von mehreren Seiten gebraucht (Seerr-Link, Jellyfin-
   Adresse) und aendert sich nur, wenn ein Admin die Einstellungen speichert.
   Deshalb einmal je Sitzung geholt; die Einstellungsseite vergisst den
   Stand nach dem Speichern. */
let appInfoVersprechen = null;

export function appInfo() {
  if (!appInfoVersprechen) {
    appInfoVersprechen = api('/api/app-info').then((r) => {
      if (!r.ok) { appInfoVersprechen = null; return null; }
      return r.daten;
    });
  }
  return appInfoVersprechen;
}

export function appInfoVergessen() {
  appInfoVersprechen = null;
}
