/* Zwei Sprachen für die Oberfläche: Deutsch und Englisch.

   Jeder Bereich der Oberfläche hat sein eigenes Wörterbuch unter
   src/lib/i18n/<bereich>.js:

       export default {
         de: { titel: 'Filme', treffer: ({ n }) => (n === 1 ? '1 Treffer' : `${n} Treffer`) },
         en: { titel: 'Movies', treffer: ({ n }) => (n === 1 ? '1 match' : `${n} matches`) },
       };

   Aufgerufen wird mit Bereich und Schlüssel: t('filme.titel'),
   t('filme.treffer', { n: 3 }). Platzhalter in Texten: 'Hallo {name}'.
   Fehlt ein Text auf Englisch, gilt der deutsche; fehlt er ganz, steht der
   Schlüssel da (und eine Warnung in der Konsole) — eine Lücke fällt so auf,
   ohne dass die Seite bricht.

   Die Sprache gilt sofort überall: t() liest den Zustand `sprache`, und
   Svelte zeichnet jede Stelle neu, die ihn gelesen hat.

   Woher die Sprache kommt (in dieser Reihenfolge):
     1. die eigene Wahl des angemeldeten Nutzers (Backend, /api/auth/me),
     2. die zuletzt in diesem Browser gewählte (localStorage),
     3. die Vorgabe des Admins (/api/setup/status),
     4. die Sprache des Browsers (de… → Deutsch, sonst Englisch).

   Jede Anfrage an /api trägt die Sprache im Kopf X-Sprache. So antwortet das
   Backend (Begründungen, Hinweise, Fehlermeldungen, KI-Antworten) in
   derselben Sprache — siehe backend/texte.py. */

const MODULE = import.meta.glob('./i18n/*.js', { eager: true });
const TABELLEN = { de: {}, en: {} };
for (const [pfad, mod] of Object.entries(MODULE)) {
  const bereich = pfad.match(/\/([^/]+)\.js$/)[1];
  for (const code of Object.keys(TABELLEN)) {
    for (const [k, v] of Object.entries(mod.default?.[code] ?? {})) {
      TABELLEN[code][`${bereich}.${k}`] = v;
    }
  }
}

export const SPRACHEN = [
  { code: 'de', name: 'Deutsch', kurz: 'DE' },
  { code: 'en', name: 'English', kurz: 'EN' },
];
const CODES = SPRACHEN.map((s) => s.code);
const SPEICHER = 'cinematekai_sprache';

function gespeichert() {
  try {
    const s = localStorage.getItem(SPEICHER);
    return CODES.includes(s) ? s : null;
  } catch {
    return null;
  }
}

function browserSprache() {
  const roh = (typeof navigator !== 'undefined' && (navigator.languages?.[0] || navigator.language)) || 'de';
  return String(roh).toLowerCase().startsWith('de') ? 'de' : 'en';
}

/** Aktuelle Sprache. Nur über sprachWaehlen() ändern. */
export const sprache = $state({ code: gespeichert() ?? browserSprache() });

function htmlLang(code) {
  if (typeof document !== 'undefined') document.documentElement.lang = code;
}
htmlLang(sprache.code);

const gemeldet = new Set();

/** Gibt es einen Text zu diesem Schlüssel? Ohne Warnung — für Schlüssel, die aus
    Daten gebaut werden (z. B. Fehlerarten) und nicht alle übersetzt sein müssen. */
export function hat(schluessel) {
  return schluessel in TABELLEN.de || schluessel in (TABELLEN[sprache.code] ?? {});
}

/** Text zum Schlüssel 'bereich.name' in der aktuellen Sprache. */
export function t(schluessel, werte) {
  const tab = TABELLEN[sprache.code] ?? TABELLEN.de;
  let eintrag = tab[schluessel];
  if (eintrag === undefined) eintrag = TABELLEN.de[schluessel];
  if (eintrag === undefined) {
    if (!gemeldet.has(schluessel)) {
      gemeldet.add(schluessel);
      console.warn(`i18n: Text fehlt: ${schluessel}`);
    }
    return schluessel;
  }
  if (typeof eintrag === 'function') return eintrag(werte ?? {});
  if (!werte) return eintrag;
  return eintrag.replace(/\{(\w+)\}/g, (m, name) => (werte[name] ?? m));
}

/**
 * Sprache wechseln. `merken`: im Browser speichern; `beimNutzer`: zusätzlich
 * beim angemeldeten Nutzer im Backend (gilt dann auf allen Geräten und für
 * Hintergrundarbeit wie den Film des Tages).
 */
export async function sprachWaehlen(code, { merken = true, beimNutzer = false } = {}) {
  if (!CODES.includes(code)) return;
  sprache.code = code;
  htmlLang(code);
  if (merken) {
    try { localStorage.setItem(SPEICHER, code); } catch { /* privater Modus */ }
  }
  if (beimNutzer) {
    try {
      await fetch('/api/auth/sprache', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sprache: code }),
      });
    } catch { /* offline: die Wahl gilt trotzdem in diesem Browser */ }
  }
}

/** Vorgabe übernehmen, aber nur, wenn dieser Browser noch nichts gewählt hat. */
export function vorgabeUebernehmen(code) {
  if (!gespeichert() && CODES.includes(code)) sprachWaehlen(code, { merken: false });
}

/** Locale für Intl (Zahlen, Datum). */
export function locale() {
  return sprache.code === 'en' ? 'en-GB' : 'de-DE';
}

/** Zahl in der Schreibweise der Sprache, z. B. 0.38 -> "0,38" / "0.38". */
export function zahl(x, stellen = 0) {
  if (x == null || x === '' || Number.isNaN(Number(x))) return '—';
  return Number(x).toLocaleString(locale(), {
    minimumFractionDigits: stellen, maximumFractionDigits: stellen });
}

/** Datum/Zeit, Optionen wie Intl.DateTimeFormat. */
export function datum(wert, optionen = { dateStyle: 'medium', timeStyle: 'short' }) {
  if (!wert) return '—';
  try {
    return new Date(wert).toLocaleString(locale(), optionen);
  } catch {
    return String(wert);
  }
}

/* Genre-Namen kommen aus Jellyfin in der Sprache seiner Metadaten (bei
   deutschen Metadaten deutsch). In der englischen Oberfläche werden die
   gängigen übersetzt; Unbekanntes bleibt, wie es ist. Dieselbe Liste steht
   in backend/texte.py. */
const GENRE_EN = {
  action: 'Action', abenteuer: 'Adventure', animation: 'Animation', komödie: 'Comedy',
  krimi: 'Crime', dokumentarfilm: 'Documentary', drama: 'Drama', familie: 'Family',
  fantasy: 'Fantasy', historie: 'History', horror: 'Horror', musik: 'Music', mystery: 'Mystery',
  liebesfilm: 'Romance', 'science fiction': 'Science Fiction', 'science-fiction': 'Science Fiction',
  'tv-film': 'TV Movie',
  thriller: 'Thriller', kriegsfilm: 'War', western: 'Western', reality: 'Reality',
  spielshow: 'Game Show', miniserie: 'Miniseries', kinder: 'Kids', seifenoper: 'Soap',
  talkshow: 'Talk', news: 'News', 'sci-fi & fantasy': 'Sci-Fi & Fantasy',
  'action & adventure': 'Action & Adventure', 'war & politics': 'War & Politics',
};

/** Genre-Namen für die Anzeige. */
export function genre(name) {
  if (sprache.code !== 'en' || !name) return name;
  return GENRE_EN[String(name).trim().toLowerCase()] ?? name;
}

/* X-Sprache an jede Anfrage an /api hängen. Ein Mal beim Laden, und nur für
   die eigene API — fremde Adressen (Jellyfin-Bilder, TMDB-Poster) bekommen
   den Kopf nicht. Die Seiten rufen fetch() direkt auf; so muss keine davon
   etwas davon wissen. */
if (typeof window !== 'undefined' && !window.__cinematekaiSprache) {
  window.__cinematekaiSprache = true;
  const original = window.fetch.bind(window);
  window.fetch = (eingabe, init) => {
    const url = typeof eingabe === 'string' ? eingabe
      : eingabe instanceof URL ? eingabe.href : eingabe?.url ?? '';
    const eigen = url.startsWith('/api') || url.startsWith(`${location.origin}/api`);
    if (!eigen) return original(eingabe, init);
    const kopf = new Headers(init?.headers ?? (eingabe instanceof Request ? eingabe.headers : undefined));
    if (!kopf.has('X-Sprache')) kopf.set('X-Sprache', sprache.code);
    return original(eingabe, { ...init, headers: kopf });
  };
}
