<script>
  import { page } from '$app/stores';
  import { t } from '$lib/i18n.svelte.js';
</script>

<svelte:head><title>{t('layout.fehlerSeitentitel')}</title></svelte:head>

<main class="fehler" id="inhalt">
  <h1>{$page.status === 404 ? t('layout.nichtGefunden') : t('layout.schiefgelaufen')}</h1>
  <p>
    {#if $page.status === 404}
      {t('layout.nichtGefundenText')}
    {:else}
      {$page.error?.message ?? t('layout.unbekannterFehler')} {t('layout.fehlerCode', { status: $page.status })}
    {/if}
  </p>
  <div class="aktionen">
    <a class="knopf" href="/">{t('layout.zurStartseite')}</a>
    <a class="knopf ghost" href="/movies">{t('layout.zurFilmliste')}</a>
  </div>
</main>

<style>
  .fehler {
    max-width: 640px; margin: 0 auto; padding: 80px 24px;
    text-align: center; color: var(--text); font-family: var(--font-ui);
  }
  h1 { font-family: var(--font-display); color: var(--accent); font-weight: 400; }
  p { color: var(--text-muted); line-height: 1.6; }
  .aktionen { display: flex; gap: 10px; justify-content: center; margin-top: 24px; }
  .knopf {
    background: var(--accent); color: var(--bg); text-decoration: none;
    padding: 8px 16px; border-radius: 2px; font-size: 14px; font-weight: 600;
  }
  .knopf.ghost {
    background: transparent; color: var(--text-muted);
    border: 1px solid var(--border-strong);
  }
</style>
