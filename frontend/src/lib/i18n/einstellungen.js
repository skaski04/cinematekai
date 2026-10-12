/* Texte der Einstellungsseite (routes/admin/einstellungen/+page.svelte).
   Die Formulare für Jellyfin und die KI-Anbieter haben ihr eigenes Wörterbuch
   (formular.js). Gemeinsame Wörter wie "Speichern" stehen in allgemein.js. */
export default {
  de: {
    titel: 'Einstellungen · CinematekAI',
    seitenueberschrift: 'CinematekAI — Admin: Einstellungen',
    ladeFehler: 'Einstellungen nicht geladen: {fehler}',
    abschnitte: 'Abschnitte der Einstellungen',

    // Kartennamen (Sprungliste und Überschriften)
    kJellyfin: 'Jellyfin',
    kSprachmodell: 'Sprachmodell',
    kRueckfall: 'Rückfallmodell',
    kEmbeddings: 'Embeddings',
    kTmdb: 'TMDB',
    kSeerr: 'Seerr',
    kAllgemein: 'Allgemein',

    // Jellyfin, Sprachmodell, Rückfallmodell
    jellyfinText: 'Woher CinematekAI Filme, Serien und den Gesehen-Stand holt. Angemeldet wird '
      + 'weiterhin mit dem Jellyfin-Konto.',
    sprachmodellText: 'Führt den Chat, wählt den Film des Tages aus und begründet Empfehlungen. Mit einem '
      + 'Kontextfenster ab 16.000 Tokens laufen auch die KI-Auswahl und der Katalog im Chat.',
    rueckfallText: 'Springt ein, wenn das Sprachmodell nicht antwortet — z. B. ein lokales Modell als '
      + 'Ersatz für einen Cloud-Anbieter. Optional.',

    // Embeddings
    embeddingsText: 'Grundlage für die Suche nach Stimmung, die Passung in Prozent und ähnliche Titel. '
      + 'Bei einem Cloud-Anbieter gehen Titel, Handlung und Genres deiner Bibliothek an '
      + 'diesen Anbieter.',
    stand: 'Stand',
    modell: 'Modell',
    nichtEingerichtet: 'nicht eingerichtet',
    sammlung: 'Sammlung',
    vektoren: 'Vektoren',
    eingebettet: 'Eingebettet',
    eingebettetVon: '{fertig} von {gesamt}',
    fehlend: 'Fehlend',
    letzterLauf: 'Letzter Lauf',
    laufErgebnis: ({ ok, fehl }) => `${ok} ok${fehl ? `, ${fehl} fehlgeschlagen` : ''}`,
    fortschritt: '{phase}: {fertig} von {gesamt}',
    einbettungLaeuft: 'Einbettung läuft …',
    fortschrittLabel: 'Fortschritt der Einbettung',
    letzterLaufFehler: 'Letzter Lauf: {fehler}',
    skalaTitel: 'Passungs-Skala',
    skalaPassung: 'Passung {von} – {bis}',
    skalaStimmung: 'Stimmung ab {min} (Skala {von} – {bis})',
    skalaHinweis: 'Miss die Skala, sobald die Bibliothek eingebettet ist und es Nutzerprofile gibt — '
      + 'sonst passen die Prozentwerte nicht zum Modell.',
    quelleGemessen: 'gemessen',
    quelleGemessenAm: 'gemessen am {zeit}',
    quelleVoreingestellt: 'voreingestellt (Werte für bge-m3)',
    quelleVorlaeufig: 'vorläufig — für dieses Modell noch nicht gemessen',
    starte: 'Starte …',
    fehlendeEinbetten: 'Fehlende einbetten',
    allesNeuEinbetten: 'Alles neu einbetten',
    messe: 'Messe …',
    skalaNeuMessen: 'Skala neu messen',
    anbieterUndModell: 'Anbieter und Modell',
    statusNichtAbrufbar: 'Status nicht abrufbar: {fehler}',
    neuEinbettenFrage: 'Alle Einträge werden mit dem aktuellen Modell neu eingebettet. '
      + 'Mit einem lokalen Modell kann das Stunden dauern. Fortfahren?',
    einbettungLaeuftSchon: 'Es läuft schon eine Einbettung.',
    neuEingebettetGestartet: 'Neu-Einbettung gestartet.',
    fehlendeWerdenEingebettet: 'Fehlende Einträge werden eingebettet.',
    messeSkala: 'Messe die Skala … Das bettet einige Probe-Anfragen ein und kann etwas dauern.',
    skalaGemessen: 'Skala neu gemessen.',
    restMinute: 'noch etwa eine Minute',
    restMinuten: 'noch etwa {n} min',
    restStunden: 'noch etwa {n} h',

    // TMDB
    tmdbText: 'TMDB (The Movie Database) liefert „Wer das mochte, mochte auch“ und „Fehlt in deiner Sammlung“. '
      + 'Den Lesezugriffs-Token (API Read Access Token) gibt es kostenlos bei themoviedb.org '
      + 'unter Einstellungen → API.',
    token: 'Token',
    tokenZuerst: 'Trag zuerst den Token ein.',
    tokenGueltig: ({ zeit }) => `OK · Token gültig${zeit ? ` · ${zeit}` : ''}`,
    tokenAbgelehnt: 'TMDB lehnt den Token ab.',
    tokenEntfernenFrage: 'TMDB-Token wirklich entfernen? „Wer das mochte, mochte auch“ und „Fehlt in deiner '
      + 'Sammlung“ bekommen dann keine neuen Daten mehr.',
    tokenEntfernt: 'Token entfernt.',
    tokenEntfernen: 'Token entfernen',
    keinToken: 'Kein Token gespeichert',

    // Seerr
    seerrText: 'Mit Seerr (früher Jellyseerr) lassen sich fehlende Filme anfragen. Ist eine Adresse '
      + 'eingetragen, führen „Fehlt in deiner Sammlung“ und die Filmsuche dorthin; leer '
      + 'lassen blendet die Links aus.',
    seerrBeispiel: 'https://seerr.example.de',
    seerrHilfe: 'So, wie dein Browser Seerr öffnet.',
    seerrAusgeblendet: 'Gespeichert. Die Seerr-Links sind jetzt ausgeblendet.',

    // Allgemein
    sitzungsdauer: 'Sitzungsdauer (Tage)',
    sitzungsdauerHilfe: 'So lange bleibt man angemeldet. Gilt ab der nächsten Anmeldung.',
    sitzungsdauerGrenzen: 'Sitzungsdauer: erlaubt sind 1 bis 365 Tage.',
    medienpfad: 'Medienpfad im Container',
    medienpfadHilfe: 'Wo der Medienordner im Container eingehängt ist (z. B. /media). Dort sucht '
      + 'CinematekAI lokale Trailer.',
    medienpfadFehler: 'Der Medienpfad muss mit / beginnen, z. B. /media.',
    spracheVorgabe: 'Sprache (Vorgabe)',
    spracheVorgabeHilfe: 'Gilt für alle, die selbst keine Sprache gewählt haben, und für die Anmeldeseite.',
    zeitplanVor: 'Den Zeitplan für den täglichen Lauf stellst du unter',
    benutzerJobs: 'Benutzer & Jobs',
    zeitplanNach: ' ein.',
  },
  en: {
    titel: 'Settings · CinematekAI',
    seitenueberschrift: 'CinematekAI — Admin: Settings',
    ladeFehler: 'Settings could not be loaded: {fehler}',
    abschnitte: 'Settings sections',

    // Kartennamen (Sprungliste und Überschriften)
    kJellyfin: 'Jellyfin',
    kSprachmodell: 'Language model',
    kRueckfall: 'Fallback model',
    kEmbeddings: 'Embeddings',
    kTmdb: 'TMDB',
    kSeerr: 'Seerr',
    kAllgemein: 'General',

    // Jellyfin, Sprachmodell, Rückfallmodell
    jellyfinText: 'Where CinematekAI gets movies, series and watch status from. Sign-in still uses '
      + 'the Jellyfin account.',
    sprachmodellText: 'Runs the chat, picks the movie of the day and explains recommendations. With a '
      + 'context window of 16,000 tokens or more, the AI pick and the catalog in chat also run.',
    rueckfallText: "Steps in when the language model doesn't respond — e.g. a local model as a "
      + 'stand-in for a cloud provider. Optional.',

    // Embeddings
    embeddingsText: 'The basis for mood search, the match percentage and similar titles. '
      + 'With a cloud provider, the titles, plots and genres of your library are sent to '
      + 'that provider.',
    stand: 'Status',
    modell: 'Model',
    nichtEingerichtet: 'not set up',
    sammlung: 'Collection',
    vektoren: 'Vectors',
    eingebettet: 'Embedded',
    eingebettetVon: '{fertig} of {gesamt}',
    fehlend: 'Missing',
    letzterLauf: 'Last run',
    laufErgebnis: ({ ok, fehl }) => `${ok} ok${fehl ? `, ${fehl} failed` : ''}`,
    fortschritt: '{phase}: {fertig} of {gesamt}',
    einbettungLaeuft: 'Embedding in progress …',
    fortschrittLabel: 'Embedding progress',
    letzterLaufFehler: 'Last run: {fehler}',
    skalaTitel: 'Match scale',
    skalaPassung: 'Match {von} – {bis}',
    skalaStimmung: 'Mood from {min} (scale {von} – {bis})',
    skalaHinweis: "Measure the scale once the library is embedded and user profiles exist — "
      + "otherwise the percentages won't fit the model.",
    quelleGemessen: 'measured',
    quelleGemessenAm: 'measured on {zeit}',
    quelleVoreingestellt: 'preset (values for bge-m3)',
    quelleVorlaeufig: 'provisional — not yet measured for this model',
    starte: 'Starting …',
    fehlendeEinbetten: 'Embed missing',
    allesNeuEinbetten: 'Re-embed everything',
    messe: 'Measuring …',
    skalaNeuMessen: 'Re-measure scale',
    anbieterUndModell: 'Provider and model',
    statusNichtAbrufbar: 'Could not fetch status: {fehler}',
    neuEinbettenFrage: 'All entries will be re-embedded with the current model. '
      + 'With a local model this can take hours. Continue?',
    einbettungLaeuftSchon: 'An embedding run is already in progress.',
    neuEingebettetGestartet: 'Re-embedding started.',
    fehlendeWerdenEingebettet: 'Missing entries are being embedded.',
    messeSkala: 'Measuring the scale … This embeds a few sample queries and may take a moment.',
    skalaGemessen: 'Scale re-measured.',
    restMinute: 'about a minute left',
    restMinuten: 'about {n} min left',
    restStunden: 'about {n} h left',

    // TMDB
    tmdbText: 'TMDB (The Movie Database) provides “People who liked this also liked” and “Missing from your '
      + 'collection”. You can get the read access token (API Read Access Token) for free at '
      + 'themoviedb.org under Settings → API.',
    token: 'Token',
    tokenZuerst: 'Enter the token first.',
    tokenGueltig: ({ zeit }) => `OK · Token valid${zeit ? ` · ${zeit}` : ''}`,
    tokenAbgelehnt: 'TMDB rejects the token.',
    tokenEntfernenFrage: 'Really remove the TMDB token? “People who liked this also liked” and “Missing '
      + 'from your collection” will then get no new data.',
    tokenEntfernt: 'Token removed.',
    tokenEntfernen: 'Remove token',
    keinToken: 'No token saved',

    // Seerr
    seerrText: 'Seerr (formerly Jellyseerr) lets you request missing movies. With an address entered, '
      + '“Missing from your collection” and the movie search link there; leave it empty to hide '
      + 'the links.',
    seerrBeispiel: 'https://seerr.example.com',
    seerrHilfe: 'The address your browser uses to open Seerr.',
    seerrAusgeblendet: 'Saved. The Seerr links are now hidden.',

    // Allgemein
    sitzungsdauer: 'Session length (days)',
    sitzungsdauerHilfe: 'How long people stay signed in. Applies from the next sign-in.',
    sitzungsdauerGrenzen: 'Session length: 1 to 365 days allowed.',
    medienpfad: 'Media path in the container',
    medienpfadHilfe: 'Where the media folder is mounted in the container (e.g. /media). CinematekAI '
      + 'looks for local trailers there.',
    medienpfadFehler: 'The media path must start with /, e.g. /media.',
    spracheVorgabe: 'Language (default)',
    spracheVorgabeHilfe: "Applies to everyone who hasn't picked a language themselves, and to the sign-in page.",
    zeitplanVor: 'You set the schedule for the daily run under',
    benutzerJobs: 'Users & Jobs',
    zeitplanNach: '.',
  },
};
