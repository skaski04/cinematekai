/* Die SSE-Verarbeitung des Chats, herausgeloest aus routes/chat/+page.svelte,
   damit das Dock dieselbe Leitung benutzt statt einer zweiten Kopie.
   Die bestehende Chat-Seite bleibt vorerst unveraendert — sie kann spaeter
   hierauf umgestellt werden, ohne dass sich ihr Verhalten aendert.

   Die eigenen Meldungen kommen aus dem Woerterbuch `chatdock`; Fehlertexte,
   die das Backend im Strom schickt, sind schon in der richtigen Sprache. */
import { t } from './i18n.svelte.js';

export function fehlertext(status) {
  if (status === 503) return t('chatdock.dienstWeg');
  if (status === 401) return t('chatdock.sitzungAbgelaufen');
  if (status >= 500) return t('chatdock.serverfehler', { status });
  return t('chatdock.abgelehnt', { status });
}

/* Reisst die Leitung mitten in der Antwort ab, ist der bis dahin gelesene
   Text trotzdem echt — er stammt vom Modell und steht schon auf dem Schirm.
   Der Aufrufer bekommt deshalb ``teiltext`` mitgeliefert und entscheidet
   selbst, ob er ihn behaelt. Der Text wird erst im Fehlerfall geholt, damit
   er der Sprache folgt, die dann gilt. */
const abbruch = () => t('chatdock.abgerissen');

/**
 * Liest den SSE-Strom und meldet jedes Ereignis einzeln zurueck.
 * @param {Response} res
 * @param {(ev: {text?: string, done?: boolean, error?: string, row_id?: number}) => void} onEvent
 */
export async function consumeSSE(res, onEvent) {
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = '';

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });

    const parts = buf.split('\n\n');
    buf = parts.pop() || '';
    for (const part of parts) {
      const line = part.split('\n').find(l => l.startsWith('data: '));
      if (!line) continue;
      try { onEvent(JSON.parse(line.slice(6))); } catch {}
    }
  }
}

/**
 * Schickt eine Frage und streamt die Antwort.
 *
 * ``signal`` ist der Abbruchweg: wer die Anfrage gestartet hat, kann sie ueber
 * einen AbortController wieder einstellen — etwa wenn das Dock geschlossen und
 * die Komponente zerstoert wird. Ein Abbruch ist kein Fehler und liefert
 * deshalb ``abgebrochen`` statt ``error``.
 *
 * @param {string} message
 * @param {(chunk: string) => void} onChunk  Textstueck angehaengt
 * @param {{signal?: AbortSignal}} [opt]
 * @returns {Promise<{ok: boolean, error?: string, abgebrochen?: boolean, teiltext?: boolean}>}
 */
export async function askStreaming(message, onChunk, opt = {}) {
  const { signal } = opt;
  let res;
  try {
    res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      signal,
      body: JSON.stringify({ message, history: [] }),
    });
  } catch (e) {
    if (e?.name === 'AbortError') return { ok: false, abgebrochen: true };
    return { ok: false, error: t('chatdock.keineVerbindung') };
  }
  if (!res.ok || !res.body) return { ok: false, error: fehlertext(res.status) };

  let failure = null;
  let gesehen = false;   // kam schon Text an, bevor es schiefging?
  try {
    await consumeSSE(res, (ev) => {
      if (ev.text) { gesehen = true; onChunk(ev.text); }
      else if (ev.error) failure = ev.error;
    });
  } catch (e) {
    /* Bricht der Lesevorgang selbst ab, warf askStreaming die Ausnahme bisher
       weiter — der Aufrufer blieb dann fuer immer im Zustand "antwortet". */
    if (e?.name === 'AbortError') return { ok: false, abgebrochen: true, teiltext: gesehen };
    failure = failure ?? abbruch();
  }
  return failure ? { ok: false, error: failure, teiltext: gesehen } : { ok: true };
}

/** Laedt den bisherigen Verlauf. Dock und Vollbildseite teilen ihn sich. */
export async function loadHistory(limit = 20) {
  try {
    const r = await fetch('/api/chat/history', { credentials: 'include' });
    if (!r.ok) return [];
    const data = await r.json();
    return (data.items || [])
      .map(it => ({ id: it.id, role: it.role, content: it.content, status: it.status }))
      .slice(-limit);
  } catch { return []; }
}
