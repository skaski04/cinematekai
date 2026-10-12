<script>
  /* Breite Kachel fuer Sektionen mit ein bis zwei Eintraegen. "Serie der
     Woche" und "Weiter oder weg?" hatten je einen Treffer und liessen
     vier Fuenftel der Seitenbreite leer — nebeneinander fuellen sie sie.
     Kein Rahmen: die Abgrenzung macht die hellere Flaeche. */
  import { t } from '$lib/i18n.svelte.js';

  let {
    id,
    name,
    href,
    label = null,      // kleine Goldzeile darueber
    facts = null,
    reason = null,     // Serif — Stimme der KI
    imgSrc = null,
    children = null,   // optionale Knoepfe
  } = $props();

  let failed = $state(false);
  const src = $derived(imgSrc ?? `/api/image/${id}?w=300`);
</script>

<div class="tile">
  <a class="shot" {href} aria-label={t('karten.oeffnen', { name })}>
    {#if failed}
      <span class="fallback">{name}</span>
    {:else}
      <img src={src} alt="" loading="lazy" onerror={() => (failed = true)} />
    {/if}
  </a>

  <div class="tx">
    {#if label}<p class="label">{label}</p>{/if}
    <h3><a {href}>{name}</a></h3>
    {#if facts}<p class="facts">{facts}</p>{/if}
    {#if reason}<p class="reason">{reason}</p>{/if}
    {#if children}<div class="actions">{@render children()}</div>{/if}
  </div>
</div>

<style>
  .tile {
    display: flex; gap: var(--s-4); padding: var(--s-4);
    border-radius: var(--r-lg); background: var(--layer-1);
    transition: background var(--dur-base) var(--ease);
  }
  .tile:hover { background: var(--layer-2); }

  .shot {
    width: 96px; flex: none; aspect-ratio: 2 / 3;
    border-radius: var(--r-sm); overflow: hidden;
    background: var(--layer-2); display: block;
  }
  .shot img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .fallback {
    display: flex; height: 100%; align-items: center; justify-content: center;
    padding: var(--s-2); text-align: center;
    font-family: var(--font-display); font-size: 14px; color: var(--accent-muted);
  }

  .tx { display: flex; flex-direction: column; justify-content: center; min-width: 0; }
  .label {
    font-size: 11px; letter-spacing: 0.2em; text-transform: uppercase;
    color: var(--accent); margin: 0 0 6px;
  }
  h3 { margin: 0; font-family: var(--font-display); font-size: 24px; font-weight: 600; line-height: 1.15; }
  h3 a { color: inherit; text-decoration: none; }
  h3 a:hover { color: var(--gold-bright); }
  .facts { font-size: 12px; color: var(--text-dim); margin: 6px 0 0; }
  .reason {
    font-family: var(--font-display); font-style: italic; font-size: 16px;
    line-height: 1.5; color: var(--gold-bright); margin: 10px 0 0;
  }
  .actions { display: flex; gap: var(--s-2); margin-top: var(--s-3); flex-wrap: wrap; }

  @media (max-width: 820px) {
    h3 { font-size: 20px; }
    .shot { width: 74px; }
  }
</style>
