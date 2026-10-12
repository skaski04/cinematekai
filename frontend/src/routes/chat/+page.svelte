<script>
  import { onMount, tick } from 'svelte';
  import { t } from '$lib/i18n.svelte.js';

  /* `fehler`: eine Fehlermeldung der Seite selbst (nicht des Modells), als
     Schluessel gemerkt und erst beim Anzeigen uebersetzt — so wechselt sie
     mit der Sprache. Inhalte vom Backend stehen in `content`. */
  /** @typedef {{id?:number, role:'user'|'assistant', content:string, status?:string, fehler?:{key:string, werte?:object}}} Msg */

  let messages = $state(/** @type {Msg[]} */([]));
  let input = $state('');
  let streaming = $state(false);
  let scrollBox;
  let loadingHistory = $state(true);
  let streamStartedAt = $state(0);
  let streamingNow = $state(0);   // seconds since stream start, ticks every 1s
  let streamTimer = null;
  // Fuer den Wiederholen-Knopf: die letzte Frage, die nicht durchkam.
  let letzteFrage = $state('');

  function fehlertext(status) {
    if (status === 503) return { key: 'chat.dienstWeg' };
    if (status === 401) return { key: 'chat.sitzungAbgelaufen' };
    if (status >= 500) return { key: 'chat.serverfehler', werte: { status } };
    return { key: 'chat.abgelehnt', werte: { status } };
  }

  function startStreamClock() {
    streamStartedAt = Date.now();
    streamingNow = 0;
    if (streamTimer) clearInterval(streamTimer);
    streamTimer = setInterval(() => {
      streamingNow = Math.floor((Date.now() - streamStartedAt) / 1000);
    }, 1000);
  }
  function stopStreamClock() {
    if (streamTimer) { clearInterval(streamTimer); streamTimer = null; }
    streamingNow = 0;
  }

  /* Die Frage geht in der Sprache der Oberflaeche ans Backend, deshalb
     $derived: nach dem Umschalten stehen und gehen die neuen Texte. */
  const suggestions = $derived([
    { key: 'recent',        prompt: t('chat.vorschlagRecent') },
    { key: 'sunday_calm',   prompt: t('chat.vorschlagSunday') },
    { key: 'dark_thriller', prompt: t('chat.vorschlagThriller') },
    { key: 'mcu_unseen',    prompt: t('chat.vorschlagMcu') },
  ]);
  /* Vorbereitete Antworten mit der Frage, zu der sie entstanden sind. Eine
     Antwort gilt nur, wenn ihre Frage wortgleich der Frage des Knopfs in der
     aktuellen Sprache ist — sonst stuende nach dem Umschalten eine deutsche
     Antwort unter einer englischen Frage. Dann wird live gefragt. */
  let preloaded = $state(/** @type {Record<string,{prompt:string, answer:string}>} */({}));
  function vorbereitet(s) {
    const p = preloaded[s.key];
    return p && (p.prompt ?? '').trim() === s.prompt.trim() ? p.answer : null;
  }

  onMount(async () => {
    try {
      const r = await fetch('/api/chat/history');
      if (r.ok) {
        const data = await r.json();
        messages = (data.items || []).map(it => ({
          id: it.id, role: it.role, content: it.content, status: it.status,
        }));
        await scrollBottom();
        const last = messages[messages.length - 1];
        if (last && last.role === 'assistant' && last.status === 'streaming') {
          streaming = true; startStreamClock();
          resumeStream(last.id);
        }
      }
    } catch {}
    try {
      const r = await fetch('/api/chat/preload');
      if (r.ok) {
        const data = await r.json();
        for (const it of data.items || []) {
          if (it.fresh && it.answer) preloaded[it.key] = { prompt: it.prompt, answer: it.answer };
        }
      }
    } catch {}
    loadingHistory = false;
  });

  async function send() {
    const text = input.trim();
    if (!text || streaming) return;
    input = '';
    messages.push({ role: 'user', content: text });
    messages.push({ role: 'assistant', content: '', status: 'streaming' });
    streaming = true; startStreamClock();
    await scrollBottom();

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, history: [] }),
      });
      if (!res.ok || !res.body) {
        // Vorher stand hier nur "[Fehler 503]" bzw. bei jedem Problem
        // "[Netzwerk-Fehler: TypeError]" — auch dann, wenn der Server sauber
        // mit einem Status geantwortet hatte.
        messages[messages.length - 1].fehler = fehlertext(res.status);
        messages[messages.length - 1].status = 'error';
        letzteFrage = text;
        streaming = false; stopStreamClock();
        return;
      }
      letzteFrage = '';
      await consumeSSE(res);
    } catch (e) {
      /* Der Strom kann abreissen, waehrend die Antwort im Hintergrund
         weiterlaeuft — das Backend schreibt sie ohnehin in die Datenbank.
         Solange wir die Zeilennummer kennen, haengen wir uns wieder an,
         statt einen Fehler zu behaupten, den es gar nicht gibt. */
      const letzte = messages[messages.length - 1];
      if (letzte?.id) await resumeStream(letzte.id);
      if (!letzte?.id || letzte.status === 'streaming') {
        messages[messages.length - 1].fehler = { key: 'chat.keineVerbindung' };
        messages[messages.length - 1].status = 'error';
        letzteFrage = text;
      }
    } finally {
      streaming = false; stopStreamClock();
    }
  }

  async function sendChip(s) {
    if (streaming) return;
    const cached = vorbereitet(s);
    if (cached) {
      messages.push({ role: 'user', content: s.prompt });
      messages.push({ role: 'assistant', content: cached, status: 'complete' });
      await scrollBottom();
      return;
    }
    input = s.prompt;
    await send();
  }

  async function resumeStream(rowId) {
    try {
      const res = await fetch(`/api/chat/resume/${rowId}`);
      if (!res.ok || !res.body) {
        streaming = false; stopStreamClock();
        return;
      }
      await consumeSSE(res, true);
    } catch {}
    streaming = false; stopStreamClock();
  }

  async function consumeSSE(res, replaceContent = false) {
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    let firstText = replaceContent;

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });

      const parts = buf.split('\n\n');
      buf = parts.pop() || '';
      for (const part of parts) {
        const line = part.split('\n').find(l => l.startsWith('data: '));
        if (!line) continue;
        try {
          const j = JSON.parse(line.slice(6));
          if (j.text) {
            if (firstText) {
              messages[messages.length - 1].content = j.text;
              firstText = false;
            } else {
              messages[messages.length - 1].content += j.text;
            }
            await scrollBottom();
          } else if (j.done) {
            messages[messages.length - 1].status = 'complete';
          } else if (j.error) {
            messages[messages.length - 1].content += `\n${t('chat.fehlerImStrom', { fehler: j.error })}`;
            messages[messages.length - 1].status = 'error';
          } else if (j.row_id) {
            messages[messages.length - 1].id = j.row_id;
          }
        } catch {}
      }
    }
  }

  async function clearChat() {
    if (streaming) return;
    if (!confirm(t('chat.verlaufLoeschenFrage'))) return;
    try {
      await fetch('/api/chat/clear', { method: 'POST' });
    } catch {}
    messages = [];
  }

  async function scrollBottom() {
    await tick();
    if (scrollBox) scrollBox.scrollTop = scrollBox.scrollHeight;
  }

  function onKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }
</script>

<svelte:head><title>{t('chat.seitentitel')}</title></svelte:head>

<div class="chat" id="inhalt">
  <h1 class="sr-only">{t('chat.ueberschriftSr')}</h1>

  <header>
    <div class="spalte kopf">
      <div>
        <h2>{t('chat.titel')}</h2>
        <p class="sub">{t('chat.verlaufHinweis')}</p>
      </div>
      <button class="sekundaer" onclick={clearChat} disabled={streaming || !messages.length}>
        {t('chat.verlaufLoeschen')}
      </button>
    </div>
  </header>

  <div class="verlauf" bind:this={scrollBox}>
    <div class="spalte strang">
      {#if loadingHistory}
        <p class="status">{t('chat.ladeVerlauf')}</p>
      {:else if !messages.length}
        <div class="start">
          <p class="start-text">{t('chat.start')}</p>
          <div class="chips">
            {#each suggestions as s (s.key)}
              <button class="chip" onclick={() => sendChip(s)} disabled={streaming}>
                {s.prompt}
                {#if vorbereitet(s)}<span class="bereit"><span aria-hidden="true">✓</span><span class="sr-only">{t('chat.vorbereitet')}</span></span>{/if}
              </button>
            {/each}
          </div>
        </div>
      {/if}

      <!-- Schluessel muessen eindeutig sein: aus der Historie geladene Nachrichten
           haben eine DB-id, lokal angehaengte nicht. Ohne Praefix kollidiert die
           DB-id 2 mit dem Index 2 einer neuen Nachricht → each_key_duplicate,
           und die Seite reagiert nicht mehr. -->
      {#each messages as m, i (m.id != null ? `db${m.id}` : `tmp${i}`)}
        {#if m.role === 'user'}
          <p class="msg ich">{m.content}</p>
        {:else}
          <!-- Der Inhalt steht bewusst dicht am Tag: .msg traegt pre-wrap, jede
               Einrueckung im Quelltext wuerde sonst mitgerendert. -->
          <p class="msg ki" class:fehler={m.status === 'error'}>{m.fehler ? t(m.fehler.key, m.fehler.werte) : m.content}{#if m.status === 'streaming'}<span class="cursor"></span>{/if}</p>
        {/if}
      {/each}

      {#if letzteFrage && !streaming}
        <div class="retry-zeile">
          <button class="sekundaer" onclick={() => { const frage = letzteFrage; letzteFrage = ''; input = frage; send(); }}>
            <span aria-hidden="true">⟳</span> {t('chat.erneut')}
          </button>
        </div>
      {/if}

      <!-- Das Modell denkt erst nach und schreibt dann — vor dem Ende des
           Nachdenkens kommt kein sichtbarer Text. Deshalb sagen wir von der
           ersten Sekunde an, was gerade passiert, statt nur zu blinken. -->
      {#if streaming}
        <p class="geduld">
          {#if streamingNow >= 90}
            {t('chat.geduldLang')}
          {:else}
            {t('chat.geduldVor')}<strong>{t('chat.geduldDauer')}</strong>{t('chat.geduldNach')}
          {/if}
          <span class="verstrichen">{Math.floor(streamingNow / 60)}:{(streamingNow % 60).toString().padStart(2,'0')}</span>
        </p>
      {/if}
    </div>
  </div>

  <div class="eingabe">
    <div class="spalte zeile">
      <textarea
        bind:value={input}
        onkeydown={onKey}
        placeholder={streaming ? t('chat.platzhalterAntwortet') : t('chat.platzhalter')}
        disabled={streaming}
        rows="2"
        aria-label={t('chat.nachricht')}
      ></textarea>
      <button class="senden" onclick={send} disabled={streaming || !input.trim()}
              aria-label={t('chat.senden')}>
        {streaming ? '…' : '↑'}
      </button>
    </div>
  </div>
</div>

<style>
  /* Der Chat fuellt den Rest des Fensters, damit die Eingabezeile unten
     stehen bleibt. Die Leistenhoehe kommt aus --topbar-h (im Layout gesetzt)
     statt aus einer geratenen Zahl — dort standen 52px, die Leiste misst
     aber 61px, das Fenster war also zu hoch und erzeugte eine Bildlaufleiste.
     dvh statt vh, weil mobile Browser die Adressleiste ein- und ausblenden. */
  .chat {
    display: flex; flex-direction: column;
    height: calc(100dvh - var(--topbar-h, 61px));
    font-family: var(--font-ui); color: var(--text);
  }

  /* Eine gemeinsame Spalte fuer Kopf, Verlauf und Eingabe. Bei voller
     Fensterbreite klebten Frage und Antwort sonst an gegenueberliegenden
     Bildschirmraendern. */
  .spalte { width: 100%; max-width: 900px; margin: 0 auto; box-sizing: border-box; }

  header { padding: var(--s-5) var(--s-5) var(--s-4); }
  .kopf {
    display: flex; align-items: flex-start; justify-content: space-between;
    gap: var(--s-4);
  }
  .kopf h2 {
    margin: 0; font-family: var(--font-display); font-size: 30px;
    font-weight: 600; letter-spacing: 0.01em;
  }
  .sub { margin: var(--s-1) 0 0; font-size: 13px; color: var(--text-dim); }

  /* Alle Knoepfe ausser Senden: Rand statt Flaeche. */
  .sekundaer {
    flex: none; background: transparent;
    border: 1px solid var(--border-strong); color: var(--text-muted);
    min-height: 38px; padding: 0 var(--s-4); border-radius: var(--r-full);
    font-family: var(--font-ui); font-size: 13px; cursor: pointer;
    white-space: nowrap;
    transition: border-color var(--dur-fast) var(--ease),
                color var(--dur-fast) var(--ease);
  }
  .sekundaer:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
  .sekundaer:disabled { opacity: 0.4; cursor: not-allowed; }

  .verlauf { flex: 1; overflow-y: auto; padding: var(--s-2) var(--s-5) var(--s-6); }
  .strang { display: flex; flex-direction: column; gap: var(--s-4); }

  /* Zustandsmeldung des Systems, nicht Rede der KI — deshalb Grotesk. */
  .status {
    margin: 0; padding: var(--s-5) 0; white-space: normal;
    color: var(--text-dim); font-size: 14px;
  }

  .start { padding: var(--s-6) 0 var(--s-2); }
  /* Die Begruessung ist fest im Quelltext: hier darf kein pre-wrap greifen. */
  .start-text {
    margin: 0; white-space: normal;
    font-family: var(--font-display); font-style: italic;
    font-size: 19px; color: var(--gold-bright); line-height: 1.5;
  }

  .chips { display: flex; flex-wrap: wrap; gap: var(--s-2); margin-top: var(--s-5); }
  .chip {
    display: inline-flex; align-items: center; gap: var(--s-2);
    background: transparent; border: 1px solid var(--border-strong);
    color: var(--text-muted); min-height: 38px; padding: var(--s-2) var(--s-4);
    border-radius: var(--r-full); font-family: var(--font-ui); font-size: 13px;
    text-align: left; cursor: pointer;
    transition: border-color var(--dur-fast) var(--ease),
                color var(--dur-fast) var(--ease);
  }
  .chip:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
  .chip:disabled { opacity: 0.4; cursor: not-allowed; }
  /* Der Haken sagt: die Antwort liegt schon bereit und kommt ohne Wartezeit. */
  .bereit { color: var(--accent); font-size: 12px; line-height: 1; }

  /* pre-wrap gehoert nur an Text, der aus dem Modell oder von der Tastatur
     kommt — dort stehen Absaetze drin. */
  .msg {
    max-width: 78%; margin: 0; line-height: 1.55;
    white-space: pre-wrap; overflow-wrap: anywhere;
  }
  .msg.ich {
    align-self: flex-end; background: var(--layer-3); color: var(--text);
    font-size: 14px; padding: var(--s-3) var(--s-4);
    border-radius: var(--r-lg) var(--r-lg) var(--r-sm) var(--r-lg);
  }
  /* Die KI spricht Serif und ohne Blase — dieselbe Erscheinung wie im Dock,
     damit der Wechsel ins Vollbild nichts veraendert. Aufrecht statt kursiv,
     weil hier ganze Absaetze stehen und nicht ein Satz Begruendung. */
  /* Kursiv gehoert zur Stimme der KI: Hero, Karten und das Chat-Fenster
     setzen sie so, hier fehlte sie. */
  .msg.ki {
    align-self: flex-start; font-family: var(--font-display); font-style: italic;
    font-size: 17px; color: var(--gold-bright); line-height: 1.55;
  }
  /* Eine Fehlermeldung ist Systemtext, nicht die Stimme des Modells — sie
     setzt deshalb auch die Kursive von .msg.ki wieder zurueck. */
  .msg.ki.fehler {
    color: var(--danger); font-family: var(--font-ui); font-style: normal;
    font-size: 14px;
  }

  .cursor {
    display: inline-block; width: 7px; height: 1.1em; margin-left: 2px;
    background: var(--accent); vertical-align: -2px;
    animation: blinken 1s steps(2) infinite;
  }
  @keyframes blinken { 50% { opacity: 0; } }

  .retry-zeile { display: flex; justify-content: center; }

  /* Fest im Quelltext stehender Hinweis: white-space normal, sonst stuende
     die Einrueckung der Vorlage sichtbar in der Seite. */
  .geduld {
    margin: 0; white-space: normal; text-align: center;
    background: var(--gold-wash); border-radius: var(--r-md);
    padding: var(--s-3) var(--s-4);
    color: var(--text-dim); font-size: 13px; line-height: 1.55;
  }
  .geduld strong { color: var(--accent); font-weight: 600; }
  /* Gemessene Zeit — Grotesk, mit festen Ziffernbreiten gegen das Zappeln. */
  .verstrichen {
    display: inline-block; margin-left: var(--s-2);
    color: var(--accent); font-variant-numeric: tabular-nums;
  }

  /* Die hellere Flaeche trennt Eingabe und Verlauf, nicht ein Strich. */
  .eingabe { background: var(--layer-1); padding: var(--s-4) var(--s-5); }
  .zeile { display: flex; align-items: flex-end; gap: var(--s-3); }
  .zeile textarea {
    flex: 1; min-width: 0; resize: none;
    background: var(--layer-2); color: var(--text);
    border: 1px solid transparent; border-radius: var(--r-lg);
    padding: var(--s-3) var(--s-4); outline: 0;
    font-family: var(--font-ui); font-size: 15px; line-height: 1.5;
    transition: border-color var(--dur-base) var(--ease),
                box-shadow var(--dur-base) var(--ease);
  }
  .zeile textarea:focus {
    border-color: var(--gold-ring); box-shadow: 0 0 0 4px var(--gold-wash);
  }
  .zeile textarea::placeholder { color: var(--text-dim); }
  .zeile textarea:disabled { opacity: 0.6; }

  /* Der einzige Vollgold-Knopf dieser Seite — Senden ist, worum es hier geht. */
  .senden {
    flex: none; width: 48px; height: 48px; border: 0; border-radius: 50%;
    background: var(--accent); color: var(--bg);
    font-size: 18px; cursor: pointer;
    transition: background var(--dur-fast) var(--ease);
  }
  .senden:hover:not(:disabled) { background: var(--gold-bright); }
  .senden:disabled { opacity: 0.4; cursor: default; }

  @media (max-width: 820px) {
    header { padding: var(--s-4) var(--s-4) var(--s-3); }
    .kopf h2 { font-size: 24px; }
    .verlauf { padding: var(--s-2) var(--s-4) var(--s-5); }
    .msg { max-width: 92%; }
    .chip, .sekundaer { min-height: 44px; }
    .eingabe {
      padding: var(--s-3) var(--s-4);
      padding-bottom: calc(var(--s-3) + env(safe-area-inset-bottom));
    }
    /* 16px verhindert, dass iOS beim Fokus in das Feld hineinzoomt. */
    .zeile textarea { font-size: 16px; min-height: 44px; }
  }
</style>
