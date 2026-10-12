<script>
  import { onMount, onDestroy } from 'svelte';
  import { goto } from '$app/navigation';
  import { user } from '$lib/auth.svelte.js';
  import { zeitzonen } from '$lib/api.js';
  import { t, datum } from '$lib/i18n.svelte.js';
  import AdminNav from '$lib/components/AdminNav.svelte';
  import KiProtokoll from '$lib/components/KiProtokoll.svelte';
  let users = $state([]);
  let newUsername = $state('');
  let error = $state('');
  let success = $state('');

  // Cron state
  let jobs = $state([]);
  let schedule = $state({ hour: 10, minute: 30, enabled: true, next_run_at: null, timezone: 'Europe/Berlin' });
  let scheduleDraft = $state({ hour: 10, minute: 30, enabled: true, timezone: '' });
  /* Vorschlagsliste fuer das Zeitzonen-Feld. Ein Textfeld mit Vorschlaegen
     statt einer Auswahl mit ~400 Eintraegen: "Europe/Ber" tippen geht
     schneller als scrollen, und das Backend prueft den Namen ohnehin. */
  const ZONEN = zeitzonen();
  let cronMsg = $state('');
  let cronErr = $state('');
  let pollTimer = null;

  async function loadUsers() {
    const r = await fetch('/api/admin/users');
    if (r.ok) users = await r.json();
    else if (r.status === 403) goto('/');
  }

  async function addUser() {
    error = ''; success = '';
    const r = await fetch('/api/admin/users', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ jellyfin_username: newUsername }),
    });
    if (r.ok) {
      success = t('admin.hinzugefuegt', { name: newUsername });
      newUsername = '';
      await loadUsers();
    } else {
      const d = await r.json().catch(() => ({}));
      error = d.detail || t('allgemein.fehlerStatus', { status: r.status });
    }
  }

  async function removeUser(id, name) {
    if (!confirm(t('admin.entfernenFrage', { name }))) return;
    error = ''; success = '';
    const r = await fetch(`/api/admin/users/${id}`, { method: 'DELETE' });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      error = d.detail || t('allgemein.fehlerStatus', { status: r.status });
    }
    await loadUsers();
  }

  async function toggleAdmin(id, current) {
    error = ''; success = '';
    const r = await fetch(`/api/admin/users/${id}/admin`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ is_admin: !current }),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      error = d.detail || t('allgemein.fehlerStatus', { status: r.status });
    }
    await loadUsers();
  }

  // --- Cron ---

  async function loadJobs() {
    const r = await fetch('/api/admin/cron/jobs');
    if (r.ok) {
      const d = await r.json();
      jobs = d.jobs;
    }
  }

  async function loadSchedule() {
    const r = await fetch('/api/admin/cron/schedule');
    if (r.ok) {
      schedule = await r.json();
      scheduleDraft = { hour: schedule.hour, minute: schedule.minute, enabled: schedule.enabled,
                        timezone: schedule.timezone ?? '' };
    }
  }

  async function runJob(name) {
    cronMsg = ''; cronErr = '';
    const r = await fetch(`/api/admin/cron/run?job=${encodeURIComponent(name)}`, { method: 'POST' });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) {
      cronErr = d.detail || t('allgemein.fehlerStatus', { status: r.status });
    } else if (d.status === 'already_running') {
      cronMsg = t('admin.bereitsLaufend', { name, seit: formatStarted(d.started_at) });
    } else {
      cronMsg = t('admin.jobGestartet', { name });
    }
    await loadJobs();
  }

  async function saveSchedule() {
    cronMsg = ''; cronErr = '';
    const r = await fetch('/api/admin/cron/schedule', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      /* Leere Zone = die gespeicherte behalten (so behandelt es cron.set_schedule). */
      body: JSON.stringify({ ...scheduleDraft, timezone: scheduleDraft.timezone.trim() || null }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) {
      cronErr = d.detail || t('allgemein.fehlerStatus', { status: r.status });
    } else {
      schedule = d;
      scheduleDraft.timezone = d.timezone ?? scheduleDraft.timezone;
      cronMsg = t('admin.zeitplanGespeichert', {
        zeit: `${String(d.hour).padStart(2,'0')}:${String(d.minute).padStart(2,'0')}`,
        zone: d.timezone ?? '',
        status: d.enabled ? t('admin.aktiv') : t('admin.deaktiviert'),
      });
    }
  }

  /* Datum mit Sekunden in der Schreibweise der gewaehlten Sprache. */
  const MIT_SEKUNDEN = { dateStyle: 'medium', timeStyle: 'medium' };

  function formatStarted(iso) {
    return datum(iso, MIT_SEKUNDEN);
  }

  function formatElapsed(s) {
    if (s == null) return '';
    if (s < 60) return `${s.toFixed(0)}s`;
    const m = Math.floor(s / 60);
    const r = Math.round(s - m * 60);
    return `${m}m ${r}s`;
  }

  function statusBadge(s) {
    return { idle: '—', running: t('admin.statusLaeuft'), ok: t('admin.statusOk'),
             error: t('admin.statusFehler') }[s] ?? s;
  }

  onMount(async () => {
    await Promise.all([loadUsers(), loadJobs(), loadSchedule()]);
    // Poll jobs every 3 s
    pollTimer = setInterval(loadJobs, 3000);
  });

  onDestroy(() => {
    if (pollTimer) clearInterval(pollTimer);
  });
</script>

<div class="admin" id="inhalt">
  <h1 class="sr-only">{t('admin.seitenueberschrift')}</h1>
  <AdminNav />
  <h2>{t('admin.benutzerverwaltung')}</h2>

  <div class="add-row">
    <input type="text" placeholder={t('admin.benutzernamePlatzhalter')} bind:value={newUsername} />
    <button onclick={addUser}>{t('admin.hinzufuegen')}</button>
  </div>
  {#if error}<div class="msg error">{error}</div>{/if}
  {#if success}<div class="msg success">{success}</div>{/if}

  <table>
    <thead>
      <tr>
        <th>{t('admin.thBenutzer')}</th>
        <th>{t('admin.thAdmin')}</th>
        <th>{t('admin.thLetzterLogin')}</th>
        <th></th>
      </tr>
    </thead>
    <tbody>
      {#each users as u (u.id)}
        <tr>
          <td>{u.display_name} <span class="dim">({u.jellyfin_username})</span></td>
          <td>
            <button class="small" onclick={() => toggleAdmin(u.id, u.is_admin)}>
              {u.is_admin ? t('admin.istAdmin') : t('admin.keinAdmin')}
            </button>
          </td>
          <td class="dim">{datum(u.last_login_at, { dateStyle: 'medium' })}</td>
          <td>
            {#if u.id !== user.userId}
              <button class="small danger" onclick={() => removeUser(u.id, u.display_name)}>{t('admin.entfernen')}</button>
            {/if}
          </td>
        </tr>
      {/each}
    </tbody>
  </table>

  <h2 style="margin-top: 32px">{t('admin.cronTitel')}</h2>

  <section class="schedule-panel">
    <div class="sched-current">
      <div>
        <div class="label">{t('admin.aktuellerZeitplan')}</div>
        <div class="val">
          {schedule.enabled
            ? t('admin.taeglich', {
                zeit: `${String(schedule.hour).padStart(2,'0')}:${String(schedule.minute).padStart(2,'0')}`,
                zone: schedule.timezone ?? '' })
            : t('admin.deaktiviert')}
        </div>
        {#if schedule.next_run_at}
          <div class="dim">{t('admin.naechsterLauf', { zeit: datum(schedule.next_run_at, MIT_SEKUNDEN) })}</div>
        {/if}
      </div>
    </div>
    <div class="sched-edit">
      <label>
        {t('admin.stunde')}
        <input type="number" min="0" max="23" bind:value={scheduleDraft.hour} />
      </label>
      <label>
        {t('admin.minute')}
        <input type="number" min="0" max="59" bind:value={scheduleDraft.minute} />
      </label>
      <label class="zone">
        {t('admin.zeitzone')}
        <input type="text" list="admin-zeitzonen" autocomplete="off" autocapitalize="off"
               spellcheck="false" placeholder="Europe/Berlin" bind:value={scheduleDraft.timezone} />
      </label>
      <datalist id="admin-zeitzonen">
        {#each ZONEN as z (z)}<option value={z}></option>{/each}
      </datalist>
      <label class="check">
        <input type="checkbox" bind:checked={scheduleDraft.enabled} />
        {t('admin.aktiv')}
      </label>
      <button onclick={saveSchedule}>{t('allgemein.speichern')}</button>
    </div>
  </section>

  {#if cronErr}<div class="msg error">{cronErr}</div>{/if}
  {#if cronMsg}<div class="msg success">{cronMsg}</div>{/if}

  <table class="jobs">
    <thead>
      <tr>
        <th>{t('admin.thJob')}</th>
        <th>{t('admin.status')}</th>
        <th>{t('admin.thGestartet')}</th>
        <th>{t('admin.thDauer')}</th>
        <th>{t('admin.thErgebnis')}</th>
        <th></th>
      </tr>
    </thead>
    <tbody>
      {#each jobs as j (j.name)}
        <tr class:running={j.status === 'running'}>
          <td>
            <div class="job-name">{j.name}</div>
            <div class="dim">{j.description}</div>
          </td>
          <td class="status status-{j.status}">{statusBadge(j.status)}</td>
          <td class="dim">{formatStarted(j.started_at)}</td>
          <td class="dim">{formatElapsed(j.elapsed_s)}</td>
          <td class="dim">
            {j.error ? t('admin.jobFehler', { fehler: j.error }) : (j.last_result ?? '')}
          </td>
          <td>
            <button class="small" onclick={() => runJob(j.name)} disabled={j.status === 'running'}>
              {j.status === 'running' ? '…' : t('admin.start')}
            </button>
          </td>
        </tr>
      {/each}
    </tbody>
  </table>
  <div class="dim" style="margin-top: 8px; font-size: 11px">
    {t('admin.laufzeitHinweis')}
  </div>

  <KiProtokoll />
</div>

<style>
  .admin { padding: 24px 28px; max-width: 1000px; }
  h2 {
    margin: 0 0 16px;
    font-family: var(--font-display);
    font-weight: 600;
    letter-spacing: 0.02em;
  }
  .add-row { display: flex; gap: 8px; margin-bottom: 12px; }
  .add-row input {
    flex: 1; padding: 8px 10px; background: var(--surface); color: var(--text);
    border: 1px solid var(--border-strong); border-radius: 2px; font-size: 14px;
  }
  .add-row button {
    background: var(--accent); color: var(--bg); border: 0; padding: 8px 16px;
    border-radius: 2px; cursor: pointer; font-weight: 600;
    letter-spacing: 0.06em; text-transform: uppercase; font-size: 12px;
  }
  .msg { font-size: 13px; margin-bottom: 12px; }
  .error { color: var(--danger); }
  .success { color: var(--success); }
  table { width: 100%; border-collapse: collapse; margin-top: 4px; }
  th, td { text-align: left; padding: 8px; border-bottom: 1px solid var(--border); }
  th { color: var(--text-muted); font-size: 12px; letter-spacing: 0.06em;
       text-transform: uppercase; font-weight: 500; }
  .dim { color: var(--text-dim); font-size: 12px; }
  button.small {
    background: var(--surface); color: var(--text-muted);
    border: 1px solid var(--border-strong); padding: 3px 12px;
    border-radius: 2px; cursor: pointer; font-size: 12px;
  }
  button.small:hover:not(:disabled) { color: var(--accent); border-color: var(--accent); }
  button.small:disabled { opacity: 0.4; cursor: wait; }
  button.danger { color: var(--danger); border-color: rgba(184,92,92,0.35); }

  .schedule-panel {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 2px;
    padding: 16px;
    margin-bottom: 16px;
  }
  .sched-current .label {
    font-size: 11px; letter-spacing: 0.1em; text-transform: uppercase;
    color: var(--text-muted); margin-bottom: 4px;
  }
  .sched-current .val {
    font-family: var(--font-display); font-size: 18px; color: var(--accent);
  }
  .sched-edit {
    display: flex; gap: 12px; align-items: flex-end;
    margin-top: 14px; flex-wrap: wrap;
  }
  .sched-edit label {
    display: flex; flex-direction: column; gap: 4px;
    font-size: 12px; color: var(--text-muted);
  }
  .sched-edit label.check {
    flex-direction: row; align-items: center; gap: 6px;
  }
  .sched-edit input[type=number] {
    width: 70px; padding: 6px 8px;
    background: var(--bg); color: var(--text);
    border: 1px solid var(--border-strong); border-radius: 2px;
    font-family: var(--font-ui);
  }
  .sched-edit input[type=checkbox] { accent-color: var(--accent); }
  /* Zonennamen wie "America/Argentina/Buenos_Aires" sind lang; das Feld
     waechst mit, bleibt aber auf dem Handy in der Zeile. */
  .sched-edit label.zone { flex: 1 1 200px; max-width: 280px; min-width: 0; }
  .sched-edit input[type=text] {
    width: 100%; box-sizing: border-box; padding: 6px 8px;
    background: var(--bg); color: var(--text);
    border: 1px solid var(--border-strong); border-radius: 2px;
    font-family: var(--font-ui); font-size: 14px;
  }
  .sched-edit button {
    background: var(--accent); color: var(--bg); border: 0;
    padding: 8px 18px; border-radius: 2px; cursor: pointer;
    font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase;
    font-size: 12px;
  }

  table.jobs td { vertical-align: top; }
  table.jobs tr.running { background: rgba(200, 169, 110, 0.04); }
  .job-name { font-weight: 600; color: var(--text); }
  .status { font-size: 12px; white-space: nowrap; }
  .status-running { color: var(--accent); }
  .status-ok { color: var(--success); }
  .status-error { color: var(--danger); }
  .status-idle { color: var(--text-dim); }

  @media (max-width: 720px) {
    .admin { padding: 16px 14px; }
    table, thead, tbody, tr, th, td { display: block; }
    thead { display: none; }
    table.jobs tr { border: 1px solid var(--border); margin-bottom: 10px; padding: 8px; }
    table.jobs td { border: 0; padding: 4px 0; }
    .sched-edit { gap: 8px; }
  }
</style>
