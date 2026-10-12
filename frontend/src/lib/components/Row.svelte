<script>
  /* Eine horizontale Reihe. Die Ebene entscheidet ueber die Lautstaerke der
     Ueberschrift — vorher hatten alle zehn Sektionen dieselbe goldene
     Versalzeile, wodurch "Weiterschauen" so wichtig aussah wie "Zuletzt
     hinzugefuegt". 1 = laufend, 2 = kuratiert, 3 = Archiv. */
  import { t } from '$lib/i18n.svelte.js';

  let {
    title,
    level = 2,
    sub = null,       // kursive Beizeile in Serif
    moreHref = null,
    moreLabel = null, // ohne Angabe: "alle ansehen" in der aktuellen Sprache
    wide = false,     // Querformat-Karten statt Poster
    /* Quellenangabe hinter der Beizeile, z. B. das TMDB-Logo bei "Fehlt in
       deiner Sammlung". Ein Snippet, weil es ein Link mit Bild ist und kein
       Text. Steht innerhalb der Beizeile, damit es wie ein Wort mit ihr
       umbricht — die Kopfzeile selbst bleibt unveraendert. */
    quelle = null,
    children,
  } = $props();

  let rail = $state(null);
  let atStart = $state(true);
  let atEnd = $state(false);

  function measure() {
    if (!rail) return;
    atStart = rail.scrollLeft < 8;
    atEnd = rail.scrollLeft + rail.clientWidth >= rail.scrollWidth - 8;
  }

  /* atEnd wurde bisher erst beim ersten Scrollen berechnet. Eine Reihe mit
     drei Karten, die gar nicht scrollen kann, bot deshalb einen Pfeil nach
     rechts an, der ins Leere fuehrte. Beim Einhaengen einmal messen — und
     danach bei jeder Groessenaenderung, weil die Kartenbreite an der
     Fensterbreite haengt (clamp im CSS weiter unten). */
  $effect(() => {
    if (!rail) return;
    measure();
    const groesse = new ResizeObserver(measure);
    groesse.observe(rail);
    /* Der Kasten der Schiene aendert sich nicht, wenn Karten dazukommen:
       die Breite haengt am Elternteil, die Hoehe am festen Seitenverhaeltnis
       der Karten. Nachgeladene Reihen (die Startseite fuellt fast alle
       nachtraeglich) wuerden sonst mit dem Messwert von null Karten
       weiterlaufen und den rechten Pfeil verstecken. */
    const inhalt = new MutationObserver(measure);
    inhalt.observe(rail, { childList: true, subtree: true });
    return () => { groesse.disconnect(); inhalt.disconnect(); };
  });

  function scrollBy(dir) {
    if (!rail) return;
    /* Wer Bewegung reduziert haben moechte, bekommt den Sprung ohne die
       Rutschfahrt — dieselbe Ruecksicht nimmt der Rest der App auch. */
    const sanft = typeof matchMedia !== 'function'
      || !matchMedia('(prefers-reduced-motion: reduce)').matches;
    rail.scrollBy({
      left: dir * rail.clientWidth * 0.8,
      behavior: sanft ? 'smooth' : 'auto',
    });
  }
</script>

<section class="row">
  <div class="head" class:r1={level === 1} class:r2={level === 2} class:r3={level === 3}>
    <h2>{title}</h2>
    {#if sub || quelle}<span class="sub">{sub ?? ''}{#if quelle}{' '}{@render quelle()}{/if}</span>{/if}
    {#if moreHref}<a class="more" href={moreHref}>{moreLabel ?? t('karten.alleAnsehen')} →</a>{/if}
  </div>

  <div class="rail-wrap">
    <button class="arrow l" class:hidden={atStart} onclick={() => scrollBy(-1)}
            aria-label={t('karten.zurueck')}>‹</button>
    <div class="rail" class:wide bind:this={rail} onscroll={measure}>
      {@render children()}
    </div>
    <button class="arrow r" class:hidden={atEnd} onclick={() => scrollBy(1)}
            aria-label={t('karten.weiter')}>›</button>
  </div>
</section>

<style>
  .row { margin-top: var(--s-8); }

  .head {
    display: flex; align-items: baseline; gap: var(--s-4);
    padding: 0 var(--s-7); margin-bottom: var(--s-3);
  }
  .head h2 { margin: 0; }

  /* Ebene 1 — laufend. Darf am lautesten sein. */
  .head.r1 h2 {
    font-family: var(--font-display); font-size: 30px;
    font-weight: 600; letter-spacing: 0.01em; text-transform: none;
  }
  /* Ebene 2 — kuratiert. */
  .head.r2 h2 {
    font-family: var(--font-ui); font-size: 17px; font-weight: 500;
    letter-spacing: 0.02em; text-transform: none; color: var(--text);
  }
  /* Ebene 3 — Archiv. Nachschlagen, kein Angebot. */
  .head.r3 h2 {
    font-family: var(--font-ui); font-size: 13px; font-weight: 500;
    letter-spacing: 0.14em; text-transform: uppercase; color: var(--text-muted);
  }

  .sub {
    font-family: var(--font-display); font-style: italic;
    font-size: 16px; color: var(--text-dim);
  }
  .more {
    margin-left: auto; font-size: 13px; color: var(--text-dim);
    text-decoration: none; opacity: 0;
    transition: opacity var(--dur-base) var(--ease), color var(--dur-base) var(--ease);
  }
  .row:hover .more { opacity: 1; color: var(--accent); }
  .more:focus-visible { opacity: 1; }

  .rail-wrap { position: relative; }
  .rail {
    display: flex; gap: var(--s-3);
    padding: var(--s-4) var(--s-7) var(--s-5);
    overflow-x: auto;
    /* proximity statt mandatory: mandatory springt beim Laden auf die erste
       Karte und frisst dabei das linke Seitenpolster. */
    scroll-snap-type: x proximity;
    scroll-padding-left: var(--s-7);
    scrollbar-width: none;
  }
  .rail::-webkit-scrollbar { display: none; }
  /* Waechst mit der Schirmbreite. Fest 184px hiessen auf einem 4K-Monitor
     zwanzig Poster nebeneinander — bei 443 Filmen ist die Bibliothek nicht
     gross genug, dass das noch nach Auswahl aussieht statt nach Briefmarken. */
  .rail > :global(*) { flex: 0 0 clamp(184px, 12.5vw, 290px); }
  .rail.wide > :global(*) { flex: 0 0 clamp(340px, 23vw, 480px); }

  .arrow {
    position: absolute; top: 50%; transform: translateY(-50%); z-index: 5;
    width: 44px; height: 88px; border: 0; cursor: pointer;
    color: var(--text); font-size: 20px;
    background: rgba(8, 8, 10, 0.75); backdrop-filter: blur(8px);
    opacity: 0; transition: opacity var(--dur-base) var(--ease);
  }
  .arrow.l { left: 0; border-radius: 0 var(--r-md) var(--r-md) 0; }
  .arrow.r { right: 0; border-radius: var(--r-md) 0 0 var(--r-md); }
  .rail-wrap:hover .arrow:not(.hidden) { opacity: 1; }
  .arrow:focus-visible { opacity: 1; }
  .arrow:hover { background: rgba(8, 8, 10, 0.95); color: var(--accent); }
  .arrow.hidden { opacity: 0 !important; pointer-events: none; }

  /* Auf Touch wird gewischt, Pfeile waeren nur im Weg. */
  @media (hover: none) {
    .arrow { display: none; }
    .more { opacity: 1; }
  }
  @media (max-width: 820px) {
    .row { margin-top: var(--s-6); }
    .head { padding: 0 var(--s-4); }
    .head.r1 h2 { font-size: 24px; }
    .rail { padding: var(--s-3) var(--s-4) var(--s-4); scroll-padding-left: var(--s-4); }
    .rail > :global(*) { flex: 0 0 138px; }
    .rail.wide > :global(*) { flex: 0 0 min(260px, 72vw); }
  }
</style>
