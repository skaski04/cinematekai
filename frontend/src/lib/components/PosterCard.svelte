<script>
  /* Die 2:3-Karte. Im Ruhezustand ist nur das Bild zu sehen — Titel und
     Fakten blenden erst beim Ueberfahren ein. Das ist der Grund, warum die
     Karten jetzt 184px statt 85px breit sein koennen, ohne dass die Reihe
     hoeher wird: der Text sitzt im Bild, nicht darunter. Auf Touch-Geraeten
     gibt es kein Hover, deshalb ist das Overlay dort dauerhaft sichtbar. */
  import { t } from '$lib/i18n.svelte.js';

  let {
    id,
    name,
    href,
    year = null,
    facts = null,        // vorformatiert, z. B. "2014 · 122 min · ★ 6.8"
    badge = null,        // oben links, z. B. "S2 E2"
    badgeStrong = false, // Vollgold statt transparent
    progress = null,     // 0-100, zeigt den Balken unten
    hint = null,         // eine Zeile in Serif, z. B. die KI-Begruendung
    imgSrc = null,       // ueberschreibt den Bild-Pfad (fuer TMDB-Vorschlaege)
    width = 400,
    /* Marker fuer die Bibliotheksansicht. Bewusst einzelne Eigenschaften
       statt einer Liste: so steht im Aufruf, was gemeint ist, und die Karte
       entscheidet ueber die Platzierung. */
    score = null,        // Passung zum Profil, 0-100
    seen = false,
    favorite = false,
  } = $props();

  let failed = $state(false);
  const src = $derived(imgSrc ?? `/api/image/${id}?w=${width}`);
</script>

<a class="card" {href} title={name}>
  <div class="shot" class:empty={failed}>
    {#if failed}
      <span class="fallback">{name}</span>
    {:else}
      <img src={src} alt="" loading="lazy" onerror={() => (failed = true)} />
    {/if}

    {#if badge}
      <span class="badge" class:strong={badgeStrong}>{badge}</span>
    {/if}

    <div class="meta">
      <div class="t">{name}</div>
      {#if facts}<div class="f">{facts}</div>{:else if year}<div class="f">{year}</div>{/if}
      {#if hint}<div class="hint">{hint}</div>{/if}
    </div>

    {#if seen || favorite}
      <span class="marker">
        {#if seen}<i class="gesehen" title={t('karten.gesehen')} aria-label={t('karten.gesehen')}>✓</i>{/if}
        {#if favorite}<i class="favorit" title={t('karten.favorit')} aria-label={t('karten.favorit')}>♥︎</i>{/if}
      </span>
    {/if}

    {#if score != null && score >= 0}
      <span class="score" title={t('karten.passung')}>{score}</span>
    {/if}

    {#if progress != null}
      <div class="bar"><i style="width: {Math.max(2, Math.min(100, progress))}%"></i></div>
    {/if}
  </div>
</a>

<style>
  .card {
    display: block; text-decoration: none; color: inherit;
    scroll-snap-align: start;
    transition: transform var(--dur-base) var(--ease);
    transform-origin: center bottom;
  }
  .card:hover, .card:focus-visible { transform: scale(1.07) translateY(-6px); z-index: 10; }

  .shot {
    position: relative; aspect-ratio: 2 / 3;
    border-radius: var(--r-md); overflow: hidden;
    background: var(--layer-2); box-shadow: var(--elev-1);
    transition: box-shadow var(--dur-base) var(--ease);
  }
  .card:hover .shot, .card:focus-visible .shot { box-shadow: var(--elev-3); }
  .shot img { width: 100%; height: 100%; object-fit: cover; display: block; }

  /* Fehlt das Bild, stand hier bisher eine graue Box. Der Titel in Cormorant
     auf dunklem Verlauf ist kein Notbehelf, sondern eine eigene Gestalt. */
  .shot.empty {
    display: flex; align-items: center; justify-content: center;
    padding: var(--s-4); text-align: center;
    background: linear-gradient(145deg, var(--layer-3), var(--layer-1));
    border: 1px solid var(--gold-wash);
  }
  .fallback {
    font-family: var(--font-display); font-size: 19px; line-height: 1.3;
    color: var(--accent-muted);
  }

  .meta {
    position: absolute; inset: auto 0 0 0;
    padding: var(--s-6) var(--s-3) var(--s-3);
    background: linear-gradient(transparent, rgba(8, 8, 10, 0.94) 55%);
    opacity: 0; transform: translateY(8px);
    transition: opacity var(--dur-base) var(--ease), transform var(--dur-base) var(--ease);
  }
  .card:hover .meta, .card:focus-visible .meta { opacity: 1; transform: none; }
  .meta .t {
    font-size: 13px; font-weight: 500; line-height: 1.3;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .meta .f { font-size: 11px; color: var(--text-muted); margin-top: 3px; }
  /* Alles, was die KI sagt, spricht Serif. Fakten sprechen DM Sans. */
  .meta .hint {
    font-family: var(--font-display); font-style: italic; font-size: 13px;
    line-height: 1.35; color: var(--gold-bright); margin-top: 5px;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
    overflow: hidden;
  }

  /* 12px ist die Untergrenze, die fuer alle Beschriftungen gilt — hier stand
     10px. Der zurueckgenommene Sperrsatz und das knappere Polster halten das
     Etikett trotz der groesseren Schrift in der Kachel; die Deckelung faengt
     lange Texte auf schmalen Karten ab, damit nichts hinausragt. */
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

  /* Oben rechts, damit sie sich nicht mit dem Kennzeichen oben links
     ueberlagern. Keine farbigen Emoji, sondern Schriftzeichen — die passen
     sich der Textfarbe an und rendern auf jedem System gleich. */
  .marker {
    position: absolute; top: var(--s-2); right: var(--s-2);
    display: flex; gap: 5px; align-items: center;
    padding: 3px 7px; border-radius: var(--r-sm);
    background: rgba(8, 8, 10, 0.72); backdrop-filter: blur(6px);
    font-size: 11px; line-height: 1; font-style: normal;
  }
  .marker i { font-style: normal; }
  .marker .gesehen { color: var(--success); }
  .marker .favorit { color: var(--accent); }

  /* Der Passungswert ist ein Messwert, kein Satz der KI — also DM Sans.
     Stand versehentlich in Cormorant und widersprach damit der Regel, die
     die Serifenschrift der Stimme des Modells vorbehaelt. */
  .score {
    position: absolute; bottom: var(--s-2); right: var(--s-2);
    font-family: var(--font-ui); font-weight: 600; font-size: 12px;
    letter-spacing: 0.02em;
    padding: 2px 9px; border-radius: var(--r-full);
    background: var(--accent); color: var(--bg);
    box-shadow: var(--elev-1);
  }
  /* Beim Ueberfahren uebernimmt die Textebene — der Wert wuerde sonst
     mitten im Titel stehen. */
  .card:hover .score { opacity: 0; transition: opacity var(--dur-fast) var(--ease); }

  .bar {
    position: absolute; left: 0; right: 0; bottom: 0;
    height: 3px; background: rgba(255, 255, 255, 0.18);
  }
  .bar i { display: block; height: 100%; background: var(--accent); }

  /* Ohne Hover kein Overlay — auf dem Handy waere der Titel sonst nie zu sehen. */
  @media (hover: none) {
    .meta { opacity: 1; transform: none; }
    .card:hover { transform: none; }
  }
</style>
