<script>
  /* Der Einstiegsmoment. Vorher begann die Startseite oben links mit einer
     Reihe 85px breiter Poster; der "Film des Tages" steckte weit unten in
     einem umrandeten Kasten. Hier fuellt er den Bildschirm.

     Backdrop und Logo liefert der Bild-Proxy bereits (backend/main.py:1412
     erlaubt Primary|Backdrop|Thumb|Logo) — die App hat beides nur nie
     abgerufen. Faellt das Logo aus, traegt der Titel in Cormorant. */
  import { t } from '$lib/i18n.svelte.js';

  let {
    id,
    name,
    year = null,
    facts = null,
    reason = null,      // die Begruendung der KI — Serif
    overview = null,    // Handlung — Sans, zurueckgenommen
    playHref = null,
    detailHref = null,
    label = null,       // ohne Angabe: "Film des Tages" in der aktuellen Sprache
    useLogo = true,
    onRefresh = null,
    onDismiss = null,
    busy = false,
    trailerSrc = null,    // URL des lokalen Trailers, oder null
  } = $props();

  /* Der Trailer legt sich nach kurzer Verzoegerung ueber das Standbild.
     Die Wartezeit ist Absicht: sofort startendes Video laesst die Seite
     unruhig wirken, und wer gleich weiterscrollt, laedt ihn gar nicht erst.

     Die Datei kommt aus der eigenen Bibliothek — die Trailer liegen als
     <Filmordner>/backdrops/trailer.mp4 neben den Filmen (318 von 444).
     Kein YouTube, kein Netz, keine Einbettungsrechte.

     Auf Touch-Geraeten startet nichts von allein: mobile Daten, und iOS
     unterbindet Autoplay ohnehin haeufig. */
  const START_NACH_MS = 2600;

  let zeigeTrailer = $state(false);
  let laeuft = $state(false);   // erst wenn wirklich Bilder kommen
  let ton = $state(false);
  let video = $state(null);
  let uhr = null;

  const magBewegung = () =>
    typeof matchMedia === 'function' &&
    !matchMedia('(prefers-reduced-motion: reduce)').matches &&
    !matchMedia('(hover: none)').matches;

  $effect(() => {
    const quelle = trailerSrc;
    zeigeTrailer = false;
    laeuft = false;
    ton = false;
    clearTimeout(uhr);
    if (!quelle || !magBewegung()) return;
    uhr = setTimeout(() => (zeigeTrailer = true), START_NACH_MS);
    return () => clearTimeout(uhr);
  });

  /* Der Trailer lief bisher weiter, sobald der Hero aus dem Bild gescrollt
     war — mitsamt Ton, falls der Nutzer ihn eingeschaltet hatte. Man hoerte
     einen Film, den man nicht mehr sah. */
  let abschnitt = $state(null);
  let imBild = $state(true);
  let imBlick = $state(true);

  $effect(() => {
    const el = abschnitt;
    if (!el || typeof IntersectionObserver !== 'function') return;
    const beobachter = new IntersectionObserver(
      ([eintrag]) => (imBild = eintrag.isIntersecting),
      { threshold: 0.2 },
    );
    beobachter.observe(el);
    return () => beobachter.disconnect();
  });

  /* Ein Tab im Hintergrund haelt Videos nicht von selbst an: der Hero steht
     dann zwar im Bild, zu sehen ist er aber nicht — und der Ton liefe weiter,
     waehrend der Nutzer laengst woanders ist. Die Sichtbarkeit des Dokuments
     zaehlt deshalb genauso wie die des Abschnitts. */
  $effect(() => {
    const merken = () => (imBlick = !document.hidden);
    merken();
    document.addEventListener('visibilitychange', merken);
    return () => document.removeEventListener('visibilitychange', merken);
  });

  $effect(() => {
    const v = video;
    if (!v) return;
    if (imBild && imBlick) {
      const p = v.play();
      /* Browser lehnen Autoplay gelegentlich ab — das ist kein Fehlerfall,
         dann bleibt schlicht das Standbild stehen. */
      if (p && typeof p.catch === 'function') p.catch(() => {});
    } else {
      v.pause();
    }
  });

  /* Das Standbild weicht erst, wenn das Video tatsaechlich spielt. Hat der
     Film keinen Trailer, antwortet der Endpunkt mit 404 — dann bleibt es
     beim Bild, und zu sehen ist davon nichts. */
  function laeuftJetzt() { laeuft = true; }
  function ging_nicht() { zeigeTrailer = false; laeuft = false; }

  function tonUmschalten() {
    ton = !ton;
    if (video) video.muted = !ton;
  }

  /* Merkt sich, bei WELCHEM Film das Logo nicht geladen hat, nicht bloss
     DASS es nicht geladen hat. Sonst bliebe nach einem Fehlschlag auch der
     naechste Vorschlag ohne Logo, weil der Merker nie zuruecksetzt. */
  let logoFehlteBei = $state(null);
  const logoOk = $derived(useLogo && logoFehlteBei !== id);

  /* Die Handlung steht auf zwei Zeilen gekuerzt; der Rest war gar nicht mehr
     zu erreichen. Der Schalter erscheint nur, wenn wirklich etwas
     abgeschnitten ist — sonst stuende unter jedem Zweizeiler ein sinnloses
     "mehr". */
  let handlungEl = $state(null);
  let handlungOffen = $state(false);
  let handlungGekuerzt = $state(false);

  /* Ein neuer Vorschlag faengt wieder gekuerzt an. */
  $effect(() => {
    void overview;
    handlungOffen = false;
  });

  $effect(() => {
    void overview;                       // bei neuem Film neu messen
    const el = handlungEl;
    if (!el || handlungOffen) return;    // aufgeklappt gibt es nichts zu messen
    const messen = () => { handlungGekuerzt = el.scrollHeight > el.clientHeight + 1; };
    messen();
    /* Die Textbreite haengt an der Fensterbreite (clamp weiter unten), also
       kann aus zwei Zeilen beim Ziehen des Fensters eine werden. */
    const beobachter = new ResizeObserver(messen);
    beobachter.observe(el);
    return () => beobachter.disconnect();
  });
</script>

<section class="hero" bind:this={abschnitt}>
  <div class="media">
    <img class="backdrop" class:zurueck={zeigeTrailer && laeuft}
         src="/api/image/{id}?type=Backdrop&w=1920" alt="" />
    {#if zeigeTrailer && trailerSrc}
      <video bind:this={video} class="trailer" src={trailerSrc}
             autoplay muted loop playsinline preload="none"
             onplaying={laeuftJetzt} onerror={ging_nicht}></video>
    {/if}
  </div>

  <div class="body">
    <p class="eyebrow">{label ?? t('karten.filmDesTages')}</p>

    {#if logoOk}
      <img class="logo" src="/api/image/{id}?type=Logo" alt={name}
           onerror={() => (logoFehlteBei = id)} />
    {:else}
      <h2 class="title">{name}{#if year}<span class="year"> ({year})</span>{/if}</h2>
    {/if}

    {#if facts}<p class="facts">{facts}</p>{/if}
    {#if reason}<p class="reason">{reason}</p>{/if}
    {#if overview}
      <div class="handlung">
        <p class="overview" class:offen={handlungOffen} bind:this={handlungEl}>{overview}</p>
        {#if handlungGekuerzt}
          <button class="mehr" onclick={() => (handlungOffen = !handlungOffen)}
                  aria-expanded={handlungOffen}>{handlungOffen ? t('karten.weniger') : t('karten.mehr')}</button>
        {/if}
      </div>
    {/if}

    <div class="actions">
      {#if playHref}
        <a class="btn play" href={playHref} target="_blank" rel="noopener">▶︎ {t('karten.inJellyfinAbspielen')}</a>
      {/if}
      {#if detailHref}
        <a class="btn ghost" href={detailHref}>{t('karten.details')}</a>
      {/if}
      {#if onRefresh}
        <button class="btn ghost icon" onclick={onRefresh} disabled={busy}
                title={t('karten.neuerVorschlag')} aria-label={t('karten.neuerVorschlag')}>⟳</button>
      {/if}
      {#if onDismiss}
        <button class="btn ghost icon" onclick={onDismiss} disabled={busy}
                title={t('karten.ausblenden60')} aria-label={t('karten.ausblenden60')}>✕</button>
      {/if}
      {#if zeigeTrailer && laeuft}
        <button class="btn ghost icon ton" onclick={tonUmschalten}
                title={ton ? t('karten.tonAus') : t('karten.tonAn')}
                aria-label={ton ? t('karten.tonAus') : t('karten.tonAn')}>
          <!-- SVG statt Emoji: das Lautsprecher-Emoji rendert bunt und je
               nach System anders; der Strich erbt die Knopffarbe. -->
          {#if ton}<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 5 6 9H2v6h4l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M19 5a10 10 0 0 1 0 14"/></svg>{:else}<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 5 6 9H2v6h4l5 4z"/><path d="m22 9-6 6"/><path d="m16 9 6 6"/></svg>{/if}
        </button>
      {/if}
    </div>
  </div>
</section>

<style>
  /* Die Deckelung ist fuer grosse Schirme: auf einem 4K-Monitor ergab 88vh
     einen 1898px hohen Hero — man scrollte an zwei Bildschirmhoehen vorbei,
     bevor die erste Reihe kam. */
  .hero {
    position: relative; height: 88vh; min-height: 620px; max-height: 1040px;
    display: flex; align-items: flex-end;
    padding: 0 var(--s-7) var(--s-9);
  }

  .media { position: absolute; inset: 0; overflow: hidden; }
  .backdrop {
    width: 100%; height: 100%; object-fit: cover; object-position: center 22%;
    transform: scale(1.04);
    animation: kenburns 28s ease-out infinite alternate;
  }
  @keyframes kenburns { to { transform: scale(1.14) translateY(-1.5%); } }

  /* Das Standbild bleibt liegen und tritt zurueck, statt zu verschwinden —
     so ist der Uebergang ein Ueberblenden und kein Sprung, und falls der
     Trailer nicht startet, steht immer noch ein Bild da. */
  .backdrop.zurueck { opacity: 0; transition: opacity var(--dur-slow) var(--ease); }

  /* Eigene Datei statt Einbettung: object-fit erledigt hier, wofuer bei
     einem iframe die Ueberdimensionierung noetig waere. */
  .trailer {
    position: absolute; inset: 0;
    width: 100%; height: 100%; object-fit: cover; object-position: center 22%;
    pointer-events: none;
    animation: einblenden var(--dur-slow) var(--ease) both;
  }
  @keyframes einblenden { from { opacity: 0; } to { opacity: 1; } }

  .btn.ton { font-size: 15px; }

  /* Zwei Verlaeufe: einer traegt den Text unten, einer haelt die linke Seite
     frei. Auf dem Handy uebernimmt weiter unten ein rein vertikaler. */
  .media::after {
    content: ''; position: absolute; inset: 0;
    background:
      linear-gradient(180deg, rgba(8,8,10,0.55) 0%, rgba(8,8,10,0.1) 32%,
                      rgba(8,8,10,0.82) 74%, var(--layer-0) 100%),
      linear-gradient(90deg, rgba(8,8,10,0.88) 0%, rgba(8,8,10,0.35) 46%, transparent 78%);
  }

  /* Waechst auf breiten Schirmen mit, bleibt aber lesbar: bei 3840px sassen
     620px Text als schmale Spalte in der Ecke des Bildes. */
  .body { position: relative; max-width: clamp(620px, 34vw, 980px); min-width: 0; }

  .eyebrow {
    display: flex; align-items: center; gap: var(--s-2);
    font-size: 11px; letter-spacing: 0.24em; text-transform: uppercase;
    color: var(--accent); margin: 0 0 var(--s-5);
  }
  .eyebrow::before { content: ''; width: 22px; height: 1px; background: var(--accent); }

  /* Feste Pixelbreite war bei sehr schmalen Fenstern zwei Pixel breiter als
     der Platz und hat die ganze Seite seitlich scrollen lassen. Die Deckelung
     greift jetzt erst, wenn der Platz ueberhaupt da ist. */
  .logo {
    max-width: min(clamp(400px, 26vw, 620px), 100%);
    max-height: clamp(130px, 9vw, 200px);
    margin-bottom: var(--s-4); object-fit: contain;
    filter: drop-shadow(0 4px 18px rgba(0, 0, 0, 0.7));
  }
  .title {
    font-family: var(--font-display); font-size: clamp(56px, 4vw, 92px);
    font-weight: 600; line-height: 1.05; margin: 0 0 var(--s-4);
    text-shadow: 0 4px 18px rgba(0, 0, 0, 0.7);
  }
  .year { color: var(--text-dim); font-weight: 400; }

  .facts { font-size: clamp(14px, 0.95vw, 20px); color: var(--text-muted); margin: 0 0 var(--s-5); }

  /* Die Stimme der KI. Serif und kursiv, damit ohne zweite Akzentfarbe
     erkennbar ist, was gemessen und was gefolgert wurde. */
  .reason {
    font-family: var(--font-display); font-size: clamp(23px, 1.5vw, 34px);
    font-style: italic; line-height: 1.5; color: var(--gold-bright);
    margin: 0 0 var(--s-3); padding-left: var(--s-4);
    border-left: 2px solid var(--gold-ring);
  }

  .handlung { margin: 0 0 var(--s-6); }
  .overview {
    font-size: clamp(15px, 1vw, 21px); line-height: 1.65; color: var(--text-muted);
    margin: 0;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
    overflow: hidden;
  }
  /* Der Hero hat eine feste Hoehe und steht am unteren Rand — ohne Deckelung
     waechst die aufgeklappte Handlung oben aus dem Bild heraus. Lieber eine
     eigene Bildlaufleiste als Text unter der Kopfleiste. */
  .overview.offen {
    display: block; overflow-y: auto;
    max-height: min(34vh, 320px);
  }

  .mehr {
    margin-top: var(--s-2); padding: 0; background: none; border: 0;
    color: var(--accent); font-family: var(--font-ui); font-size: 14px;
    cursor: pointer; text-decoration: underline; text-underline-offset: 3px;
  }
  .mehr:hover { color: var(--gold-bright); }

  .actions { display: flex; align-items: center; gap: var(--s-3); flex-wrap: wrap; }

  .btn {
    display: inline-flex; align-items: center; gap: 10px;
    height: 48px; padding: 0 var(--s-5); border: 0;
    border-radius: var(--r-md);
    font-family: var(--font-ui); font-size: 15px; font-weight: 500;
    text-decoration: none; cursor: pointer; white-space: nowrap;
    transition: background var(--dur-base) var(--ease),
                transform var(--dur-base) var(--ease),
                box-shadow var(--dur-base) var(--ease);
  }
  /* Der einzige Vollgold-Knopf der App. Gold als Flaeche wirkt billig,
     Gold als Akzent edel — also bekommt ihn nur die wichtigste Handlung. */
  .btn.play {
    background: var(--accent); color: var(--bg);
    font-weight: 700; padding: 0 var(--s-6);
  }
  .btn.play:hover {
    background: var(--gold-bright); transform: translateY(-1px);
    box-shadow: 0 8px 24px rgba(200, 169, 110, 0.32);
  }
  .btn.ghost {
    background: rgba(255, 255, 255, 0.09); color: var(--text);
    border: 1px solid rgba(255, 255, 255, 0.14); backdrop-filter: blur(8px);
  }
  .btn.ghost:hover { background: rgba(255, 255, 255, 0.16); }
  .btn.icon { width: 48px; padding: 0; justify-content: center; font-size: 18px; }
  .btn:disabled { opacity: 0.5; cursor: wait; }

  @media (max-width: 820px) {
    .hero { height: 72vh; min-height: 480px; padding: 0 var(--s-4) var(--s-7); }
    .body { max-width: 100%; }
    .backdrop { object-position: center 12%; }
    .media::after {
      background: linear-gradient(180deg,
        rgba(8,8,10,0.55) 0%, rgba(8,8,10,0.15) 20%, rgba(8,8,10,0.8) 42%,
        rgba(8,8,10,0.95) 62%, var(--layer-0) 100%);
    }
    .logo { max-width: min(240px, 100%); max-height: 80px; }
    .title { font-size: 30px; overflow-wrap: break-word; }
    .reason {
      font-size: 18px; padding-left: var(--s-3);
      display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical;
      overflow: hidden;
    }
    /* Die Handlung faellt auf dem Handy weg. Mit ihr wuchs der Hero auf
       ueber 1300px und man scrollte an der halben Startseite vorbei, bevor
       die erste Reihe kam; nachzulesen ist sie auf der Detailseite. */
    .handlung { display: none; }
    .btn { height: 44px; font-size: 14px; }
  }
</style>
