<script>
  /* Querformat fuer "Weiterschauen". Nutzt das Backdrop statt des Posters:
     wer eine Serie fortsetzt, erkennt sie an der Szene schneller als am
     Plakat. Titel steht hier dauerhaft unter der Karte, nicht im Overlay —
     bei angefangenen Sachen will man auf einen Blick sehen, wo man steht. */
  let {
    id,
    name,
    href,
    sub = null,
    badge = null,
    badgeStrong = false,
    progress = null,
  } = $props();

  let failed = $state(false);
</script>

<a class="card" {href} title={name}>
  <div class="shot" class:empty={failed}>
    {#if failed}
      <span class="fallback">{name}</span>
    {:else}
      <img src="/api/image/{id}?type=Backdrop&w=600" alt="" loading="lazy"
           onerror={() => (failed = true)} />
    {/if}

    {#if badge}<span class="badge" class:strong={badgeStrong}>{badge}</span>{/if}

    <div class="play"><span aria-hidden="true">▶︎</span></div>

    {#if progress != null}
      <div class="bar"><i style="width: {Math.max(2, Math.min(100, progress))}%"></i></div>
    {/if}
  </div>
  <div class="t">{name}</div>
  {#if sub}<div class="s">{sub}</div>{/if}
</a>

<style>
  .card {
    display: block; text-decoration: none; color: inherit;
    scroll-snap-align: start;
    transition: transform var(--dur-base) var(--ease);
  }
  .card:hover, .card:focus-visible { transform: scale(1.03) translateY(-4px); }

  .shot {
    position: relative; aspect-ratio: 16 / 9;
    border-radius: var(--r-md); overflow: hidden;
    background: var(--layer-2); box-shadow: var(--elev-1);
    transition: box-shadow var(--dur-base) var(--ease);
  }
  .card:hover .shot { box-shadow: var(--elev-2); }
  .shot img { width: 100%; height: 100%; object-fit: cover; display: block; }

  .shot.empty {
    display: flex; align-items: center; justify-content: center;
    padding: var(--s-4); text-align: center;
    background: linear-gradient(145deg, var(--layer-3), var(--layer-1));
  }
  .fallback { font-family: var(--font-display); font-size: 20px; color: var(--accent-muted); }

  .play {
    position: absolute; inset: 0;
    display: flex; align-items: center; justify-content: center;
    background: rgba(8, 8, 10, 0.4);
    opacity: 0; transition: opacity var(--dur-base) var(--ease);
  }
  .card:hover .play, .card:focus-visible .play { opacity: 1; }
  .play span {
    width: 52px; height: 52px; border-radius: 50%;
    background: var(--accent); color: var(--bg);
    display: grid; place-items: center; font-size: 19px;
    box-shadow: var(--elev-2);
  }

  /* 12px ist die Untergrenze, die fuer alle Beschriftungen gilt — hier stand
     10px. "REIHE · TEIL 2" ist das laengste Etikett; mit knapperem Sperrsatz
     und Polster passt es auch auf die 260px schmale Karte des Handys, die
     Deckelung faengt alles Laengere ab. */
  .badge {
    position: absolute; top: var(--s-2); left: var(--s-2);
    font-size: 12px; letter-spacing: 0.06em; text-transform: uppercase;
    padding: 3px 7px; border-radius: var(--r-sm);
    background: rgba(8, 8, 10, 0.8); color: var(--accent);
    backdrop-filter: blur(6px);
    max-width: calc(100% - 2 * var(--s-2)); box-sizing: border-box;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .badge.strong { background: var(--accent); color: var(--bg); font-weight: 600; }

  .bar {
    position: absolute; left: 0; right: 0; bottom: 0;
    height: 3px; background: rgba(255, 255, 255, 0.18);
  }
  .bar i { display: block; height: 100%; background: var(--accent); }

  .t {
    font-size: 14px; font-weight: 500; margin-top: var(--s-3);
    line-height: 1.3; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .s {
    font-size: 12px; color: var(--text-dim); margin-top: 2px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }

  @media (hover: none) { .play { opacity: 1; background: none; } }
</style>
