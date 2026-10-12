<script>
  /* Umschalter Deutsch/Englisch. Angemeldet wird die Wahl auch beim Nutzer
     gespeichert (gilt dann auf jedem Gerät und für das, was das Backend im
     Hintergrund schreibt, z. B. die Begründung des Films des Tages). */
  import { SPRACHEN, sprache, sprachWaehlen, t } from '$lib/i18n.svelte.js';
  import { user } from '$lib/auth.svelte.js';

  let { kompakt = false } = $props();
</script>

<div class="sprachwahl" class:kompakt role="group" aria-label={t('allgemein.sprache')}>
  {#each SPRACHEN as s (s.code)}
    <button
      type="button"
      class:aktiv={sprache.code === s.code}
      aria-pressed={sprache.code === s.code}
      title={s.name}
      lang={s.code}
      onclick={() => sprachWaehlen(s.code, { beimNutzer: user.loggedIn })}
    >{s.kurz}</button>
  {/each}
</div>

<style>
  .sprachwahl {
    display: inline-flex;
    border: 1px solid var(--border);
    border-radius: 2px;
    overflow: hidden;
  }
  button {
    background: transparent;
    color: var(--text-muted);
    border: 0;
    padding: 6px 10px;
    font: inherit;
    font-size: 12px;
    letter-spacing: 0.08em;
    cursor: pointer;
    min-width: 36px;
  }
  button + button { border-left: 1px solid var(--border); }
  button:hover { color: var(--accent); }
  button.aktiv { color: var(--bg); background: var(--accent); }
  button:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
  .kompakt button { padding: 4px 8px; font-size: 11px; min-width: 30px; }
</style>
