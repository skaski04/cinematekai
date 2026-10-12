/* Texte der Admin-Seite "Benutzer & Jobs" (routes/admin/+page.svelte):
   Benutzerverwaltung, Cron-Zeitplan und Jobs.
   Job-Namen, Beschreibungen und Ergebnisse kommen vom Backend. */
export default {
  de: {
    seitenueberschrift: 'CinematekAI — Admin: Benutzer & Jobs',
    status: 'Status',

    // Benutzerverwaltung
    benutzerverwaltung: 'Benutzerverwaltung',
    benutzernamePlatzhalter: 'Jellyfin-Benutzername',
    hinzufuegen: 'Hinzufügen',
    hinzugefuegt: '"{name}" hinzugefügt',
    entfernenFrage: '"{name}" wirklich entfernen?',
    thBenutzer: 'Benutzer',
    thAdmin: 'Admin',
    thLetzterLogin: 'Letzter Login',
    istAdmin: '✓ Admin',
    keinAdmin: '— User',
    entfernen: 'Entfernen',

    // Cron / Planung
    cronTitel: 'Cron / Planung',
    aktuellerZeitplan: 'Aktueller Zeitplan',
    taeglich: 'täglich {zeit} ({zone})',
    deaktiviert: 'deaktiviert',
    naechsterLauf: 'Nächster Lauf: {zeit}',
    stunde: 'Stunde',
    minute: 'Minute',
    zeitzone: 'Zeitzone',
    aktiv: 'aktiv',
    zeitplanGespeichert: 'Zeitplan gespeichert: {zeit} {zone} ({status})',
    thJob: 'Job',
    thGestartet: 'Gestartet',
    thDauer: 'Dauer',
    thErgebnis: 'Ergebnis',
    statusLaeuft: '⟳ läuft',
    statusOk: '✓ ok',
    statusFehler: '✗ Fehler',
    jobFehler: 'Fehler: {fehler}',
    start: 'Start',
    bereitsLaufend: '{name} läuft bereits seit {seit}',
    jobGestartet: '{name} gestartet',
    laufzeitHinweis: 'Laufzeiten mit dem Cloud-Modell: daily_recommendations 2–12 Min je Nutzer '
      + '(je nach Auslastung), preload_chat ≈ 4 Min, missing_suggestions ≈ 1 Min, Rest wenige '
      + 'Sekunden. Status wird alle 3 s aktualisiert.',
  },
  en: {
    seitenueberschrift: 'CinematekAI — Admin: Users & Jobs',
    status: 'Status',

    // Benutzerverwaltung
    benutzerverwaltung: 'User management',
    benutzernamePlatzhalter: 'Jellyfin username',
    hinzufuegen: 'Add',
    hinzugefuegt: '"{name}" added',
    entfernenFrage: 'Really remove "{name}"?',
    thBenutzer: 'User',
    thAdmin: 'Admin',
    thLetzterLogin: 'Last login',
    istAdmin: '✓ Admin',
    keinAdmin: '— User',
    entfernen: 'Remove',

    // Cron / Planung
    cronTitel: 'Cron / Scheduling',
    aktuellerZeitplan: 'Current schedule',
    taeglich: 'daily {zeit} ({zone})',
    deaktiviert: 'disabled',
    naechsterLauf: 'Next run: {zeit}',
    stunde: 'Hour',
    minute: 'Minute',
    zeitzone: 'Time zone',
    aktiv: 'enabled',
    zeitplanGespeichert: 'Schedule saved: {zeit} {zone} ({status})',
    thJob: 'Job',
    thGestartet: 'Started',
    thDauer: 'Duration',
    thErgebnis: 'Result',
    statusLaeuft: '⟳ running',
    statusOk: '✓ ok',
    statusFehler: '✗ Error',
    jobFehler: 'Error: {fehler}',
    start: 'Start',
    bereitsLaufend: '{name} has been running since {seit}',
    jobGestartet: '{name} started',
    laufzeitHinweis: 'Run times with the cloud model: daily_recommendations 2–12 min per user '
      + '(depending on load), preload_chat ≈ 4 min, missing_suggestions ≈ 1 min, the rest a few '
      + 'seconds. Status refreshes every 3 s.',
  },
};
