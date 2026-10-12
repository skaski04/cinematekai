<script>
  /* Der Chat als Bild im Bild. Vorher war er eine eigene Seite — man musste
     das Stoebern verlassen, um zu fragen. Hier bleibt er offen, waehrend man
     weiterscrollt, und kann mitteilen, worauf man gerade schaut.

     Verlauf und Leitung teilt er sich mit /chat; wer lange Unterhaltungen
     fuehrt, wechselt ueber den Knopf oben rechts dorthin. */
  import { askStreaming, loadHistory } from '$lib/chatStream.js';
  import { t } from '$lib/i18n.svelte.js';
  import { tick } from 'svelte';

  let { context = null } = $props();   // z. B. "Das gibt Ärger" — was gerade offen ist

  let open = $state(false);
  let messages = $state([]);
  let input = $state('');
  let busy = $state(false);
  let minimieren = $state(null);
  let loaded = $state(false);
  let box = $state(null);
  let feld = $state(null);    // Eingabefeld
  let blase = $state(null);   // die zusammengeklappte Blase

  /* Das Modell denkt erst nach und schreibt dann — dieselbe Wartezeit wie auf
     der Chat-Seite. Also hier derselbe ehrliche Hinweis statt eines stummen
     Balkens, mit denselben Texten. */
  let wartet = $state(0);     // Sekunden seit dem Absenden
  let uhr = null;
  /* Fuer den Wiederholen-Knopf: die letzte Frage, die nicht durchkam. Ohne ihn
     war eine im Dock gestellte Frage nach einem Fehler verloren. */
  let letzteFrage = $state('');
  let strom = null;           // AbortController der laufenden Anfrage

  function uhrStarten() {
    const start = Date.now();
    wartet = 0;
    if (uhr) clearInterval(uhr);
    uhr = setInterval(() => (wartet = Math.floor((Date.now() - start) / 1000)), 1000);
  }
  function uhrStoppen() {
    if (uhr) { clearInterval(uhr); uhr = null; }
    wartet = 0;
  }

  /* Aufraeumen beim Zerstoeren: ein noch offener Stream wird abgebrochen,
     statt im Hintergrund weiterzulesen und in eine tote Komponente zu
     schreiben. */
  $effect(() => () => { strom?.abort(); uhrStoppen(); });

  async function toggle() {
    open = !open;
    if (open) {
      if (!loaded) {
        loaded = true;
        /* Eine Zeile, die beim Neustart des Backends auf 'streaming' stehen
           geblieben ist, laeuft nicht mehr — das Dock nimmt Antworten nicht
           wieder auf. Unveraendert uebernommen blinkte ihr Schreibbalken
           fuer immer weiter und behauptete Arbeit, die niemand tut. */
        messages = (await loadHistory(20)).map((m) =>
          m.role === 'assistant' && m.status === 'streaming'
            ? { ...m, status: 'abgebrochen' }
            : m);
      }
      /* scrollDown wartet auf das DOM-Update — vorher ist das Panel noch
         inert und das Feld nimmt den Fokus nicht an. */
      await scrollDown();
      /* Waehrend einer laufenden Antwort ist das Feld deaktiviert und nimmt
         den Fokus nicht an — dann auf den Minimieren-Knopf, damit die Tastatur
         ueberhaupt im Dock landet. */
      (busy ? minimieren : feld)?.focus();
    } else {
      await tick();
      blase?.focus();
    }
  }

  async function scrollDown() {
    await tick();
    if (box) box.scrollTop = box.scrollHeight;
  }

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    input = '';
    letzteFrage = '';
    messages.push({ role: 'user', content: text });
    messages.push({ role: 'assistant', content: '', status: 'streaming' });
    busy = true;
    uhrStarten();
    await scrollDown();

    /* /chat nimmt nur message und history entgegen (backend/main.py,
       class ChatRequest) — worauf der Nutzer gerade schaut, passt in kein
       eigenes Feld und muss deshalb in den Fragetext. Angezeigt wird
       weiterhin nur seine eigene Zeile. */
    const frage = context ? t('chatdock.kontextFrage', { kontext: context, text }) : text;

    const last = messages.length - 1;
    strom = new AbortController();
    const res = await askStreaming(frage, (chunk) => {
      messages[last].content += chunk;
      scrollDown();
    }, { signal: strom.signal });
    strom = null;

    /* Ein Abbruch ist kein Fehler: es gibt niemanden mehr, dem man ihn
       melden koennte. */
    if (res.abgebrochen) { busy = false; uhrStoppen(); return; }

    if (!res.ok) {
      /* Der bereits geschriebene Teiltext bleibt stehen — er kam wirklich vom
         Modell. Die Meldung haengt sich darunter, statt die halbe Antwort zu
         verschlucken. */
      messages[last].content = messages[last].content
        ? `${messages[last].content}\n${t('chatdock.fehlerAnhang', { fehler: res.error })}`
        : res.error;
      messages[last].status = 'error';
      letzteFrage = text;
    } else {
      messages[last].status = 'complete';
    }
    busy = false;
    uhrStoppen();
    await scrollDown();
  }

  function nochmal() {
    const frage = letzteFrage;
    letzteFrage = '';
    input = frage;
    send();
  }

  function onKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  }
</script>

<svelte:window on:keydown={(e) => {
  if (e.key === 'Escape' && open) toggle();
}} />

<!-- inert statt nur opacity: das zugeklappte Teil war zwar unsichtbar, seine
     Bedienelemente (Vollbild-Link, Minimieren, Feld, Senden) lagen aber
     weiter in der Tab-Reihenfolge. Man tabbte ins Nichts. -->
<button class="bubble" class:hide={open} inert={open} bind:this={blase}
        onclick={toggle} aria-label={t('chatdock.oeffnen')}>✦</button>

<div class="pip" class:open inert={!open} aria-hidden={!open}>
  <div class="head">
    <span class="mark" aria-hidden="true">✦</span>
    <span class="name">{t('chatdock.assistent')}</span>
    <a class="expand" href="/chat" title={t('chatdock.vollbild')}
       aria-label={t('chatdock.vollbild')}>⤢</a>
    <button bind:this={minimieren} onclick={toggle} title={t('chatdock.minimieren')}
            aria-label={t('chatdock.minimieren')}>−</button>
  </div>

  <div class="body" bind:this={box}>
    {#if !messages.length}
      <p class="msg ai start">
        {#if context}<span class="ctx">{t('chatdock.siehtGerade', { kontext: context })}</span>{/if}
        {t('chatdock.start')}
      </p>
    {/if}

    {#each messages as m}
      {#if m.role === 'user'}
        <p class="msg me">{m.content}</p>
      {:else}
        <p class="msg ai" class:err={m.status === 'error'}>
          {m.content}{#if m.status === 'streaming'}<span class="caret"></span>{/if}
        </p>
        {#if m.status === 'abgebrochen'}
          <p class="abbruch">{t('chatdock.abgebrochen')}</p>
        {/if}
      {/if}
    {/each}

    {#if letzteFrage && !busy}
      <div class="retry-zeile">
        <button class="retry" onclick={nochmal}>
          <span aria-hidden="true">⟳</span> {t('chatdock.erneutVersuchen')}
        </button>
      </div>
    {/if}

    <!-- Vor dem Ende des Nachdenkens kommt kein sichtbarer Text. Deshalb sagen
         wir von der ersten Sekunde an, was gerade passiert. -->
    {#if busy}
      <p class="geduld">
        {#if wartet >= 90}
          {t('chatdock.geduldLang')}
        {:else}
          {t('chatdock.geduldVor')} <strong>{t('chatdock.geduldDauer')}</strong>{t('chatdock.geduldNach')}
        {/if}
        <span class="verstrichen">{Math.floor(wartet / 60)}:{(wartet % 60).toString().padStart(2, '0')}</span>
      </p>
    {/if}
  </div>

  <div class="foot">
    <input bind:this={feld} bind:value={input} onkeydown={onKey} disabled={busy}
           placeholder={t('chatdock.platzhalter')} aria-label={t('chatdock.nachricht')} />
    <button onclick={send} disabled={busy || !input.trim()}
            aria-label={t('chatdock.senden')}>↑</button>
  </div>
</div>

<style>
  .bubble {
    position: fixed; right: var(--s-5); bottom: var(--s-5); z-index: 200;
    width: 60px; height: 60px; border-radius: 50%; border: 0; cursor: pointer;
    background: var(--accent); color: var(--bg); font-size: 24px;
    box-shadow: var(--elev-2);
    transition: transform var(--dur-base) var(--ease),
                background var(--dur-base) var(--ease),
                opacity var(--dur-base) var(--ease);
  }
  .bubble:hover { transform: scale(1.08); background: var(--gold-bright); }
  .bubble.hide { opacity: 0; pointer-events: none; transform: scale(0.6); }

  .pip {
    position: fixed; right: var(--s-5); bottom: var(--s-5); z-index: 201;
    width: 392px; height: 560px; max-height: calc(100vh - 2 * var(--s-5));
    border-radius: var(--r-lg); overflow: hidden;
    background: var(--layer-1); border: 1px solid rgba(255, 255, 255, 0.09);
    box-shadow: var(--elev-3);
    display: flex; flex-direction: column;
    opacity: 0; pointer-events: none; transform: translateY(20px) scale(0.96);
    transition: opacity var(--dur-base) var(--ease), transform var(--dur-base) var(--ease);
  }
  .pip.open { opacity: 1; pointer-events: auto; transform: none; }

  .head {
    display: flex; align-items: center; gap: var(--s-3);
    padding: var(--s-4); background: var(--layer-2);
    border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  }
  .mark { color: var(--accent); }
  .name { font-family: var(--font-display); font-size: 18px; color: var(--accent); flex: 1; }
  .head button, .head .expand {
    background: none; border: 0; color: var(--text-dim); cursor: pointer;
    font-size: 17px; padding: 4px 7px; border-radius: var(--r-sm);
    text-decoration: none; line-height: 1;
  }
  .head button:hover, .head .expand:hover {
    color: var(--text); background: rgba(255, 255, 255, 0.07);
  }

  .body {
    flex: 1; overflow-y: auto; padding: var(--s-4);
    display: flex; flex-direction: column; gap: var(--s-4);
  }
  /* pre-wrap braucht es fuer die Antworten des Modells, die Absaetze
     enthalten. Fuer fest im Quelltext stehenden Text nicht — dort wuerde
     sonst die Einrueckung der Vorlage mitgerendert. */
  .msg { max-width: 88%; font-size: 14px; line-height: 1.55; margin: 0; white-space: pre-wrap; }
  .msg.start { white-space: normal; }
  .msg.me {
    align-self: flex-end; background: var(--layer-3);
    padding: 10px var(--s-4);
    border-radius: var(--r-lg) var(--r-lg) var(--r-sm) var(--r-lg);
  }
  /* Die KI spricht Serif — dieselbe Regel wie im Hero und auf den Karten. */
  .msg.ai {
    font-family: var(--font-display); font-style: italic;
    font-size: 17px; line-height: 1.55; color: var(--gold-bright);
  }
  /* Fehler sind Systemtext: aufrecht und in DM Sans, nicht die Stimme der KI. */
  .msg.ai.err {
    color: var(--danger); font-family: var(--font-ui); font-style: normal;
    font-size: 14px;
  }
  .ctx {
    display: block; font-family: var(--font-ui); font-size: 11px;
    color: var(--text-dim); margin-bottom: 6px;
    letter-spacing: 0.06em; text-transform: uppercase;
  }
  .caret {
    display: inline-block; width: 7px; height: 1.1em; margin-left: 2px;
    background: var(--accent); vertical-align: -2px;
    animation: blink 1s steps(2) infinite;
  }
  @keyframes blink { 50% { opacity: 0; } }

  /* Fest im Quelltext stehende Systemtexte: white-space normal, sonst stuende
     die Einrueckung der Vorlage sichtbar im Fenster. */
  .abbruch {
    margin: calc(-1 * var(--s-3)) 0 0; white-space: normal;
    font-family: var(--font-ui); font-size: 12px; color: var(--text-dim);
  }

  .retry-zeile { display: flex; justify-content: center; }
  /* Rand statt Flaeche — Vollgold bleibt dem Senden vorbehalten. */
  .retry {
    background: transparent; border: 1px solid var(--border-strong);
    color: var(--text-muted); min-height: 34px; padding: 0 var(--s-4);
    border-radius: var(--r-full); font-family: var(--font-ui); font-size: 13px;
    cursor: pointer; white-space: nowrap;
    transition: border-color var(--dur-fast) var(--ease),
                color var(--dur-fast) var(--ease);
  }
  .retry:hover { border-color: var(--accent); color: var(--accent); }

  .geduld {
    margin: 0; white-space: normal; text-align: center;
    background: var(--gold-wash); border-radius: var(--r-md);
    padding: var(--s-2) var(--s-3);
    color: var(--text-dim); font-size: 12px; line-height: 1.5;
  }
  .geduld strong { color: var(--accent); font-weight: 600; }
  /* Gemessene Zeit — feste Ziffernbreiten gegen das Zappeln im Sekundentakt. */
  .verstrichen {
    display: inline-block; margin-left: var(--s-2);
    color: var(--accent); font-variant-numeric: tabular-nums;
  }

  .foot {
    padding: var(--s-3); border-top: 1px solid rgba(255, 255, 255, 0.06);
    display: flex; gap: var(--s-2);
  }
  .foot input {
    flex: 1; min-width: 0; height: 40px; padding: 0 var(--s-4);
    border-radius: var(--r-full); background: var(--layer-2);
    border: 1px solid transparent; color: var(--text);
    font-family: var(--font-ui); font-size: 14px; outline: 0;
  }
  .foot input:focus { border-color: var(--gold-ring); }
  .foot button {
    width: 40px; height: 40px; flex: none; border-radius: 50%; border: 0;
    background: var(--accent); color: var(--bg); cursor: pointer; font-size: 16px;
  }
  .foot button:disabled { opacity: 0.4; cursor: default; }

  @media (max-width: 820px) {
    .pip {
      right: 0; bottom: 0; width: 100%; height: 78vh;
      border-radius: var(--r-lg) var(--r-lg) 0 0;
      padding-bottom: env(safe-area-inset-bottom);
    }
    .bubble { right: var(--s-4); bottom: calc(var(--s-4) + env(safe-area-inset-bottom)); }
    /* Auf Touch bleibt kein Ziel unter 44px. */
    .retry { min-height: 44px; }
  }
</style>
