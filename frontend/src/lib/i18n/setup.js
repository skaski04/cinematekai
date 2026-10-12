/* Einrichtungsassistent (src/routes/setup/+page.svelte). */

// Abschnittsnamen fuer Fehlermeldungen beim Speichern der Extras.
const ABSCHNITT_DE = { tmdb: 'TMDB', seerr: 'Seerr', schedule: 'Zeitplan' };
const ABSCHNITT_EN = { tmdb: 'TMDB', seerr: 'Seerr', schedule: 'Schedule' };

export default {
  de: {
    seitenTitel: 'Einrichtung · CinematekAI',
    titel: 'Einrichtung',

    // Schrittanzeige
    schrittJellyfin: 'Jellyfin',
    schrittSprachmodell: 'Sprachmodell',
    schrittEmbeddings: 'Embeddings',
    schrittExtras: 'Extras',
    schrittFertig: 'Fertig',
    titelJellyfin: 'Jellyfin verbinden',
    titelSprachmodell: 'Sprachmodell',
    titelEmbeddings: 'Embeddings',
    titelExtras: 'Extras',
    titelFertig: 'Bibliothek einlesen',
    schritteLabel: 'Schritte der Einrichtung',
    zusatzUebersprungen: ' (übersprungen)',
    zusatzErledigt: ' (erledigt)',
    schrittLabel: 'Schritt {nr}: {name}{zusatz}',
    schrittVon: 'Schritt {nr} von {gesamt}',

    // Laden, gesperrt
    serverNichtErreichbar: 'Server nicht erreichbar',
    neuLaden: 'Neu laden',
    schonEingerichtet: 'Schon eingerichtet',
    schonEingerichtetAdmin: 'CinematekAI ist bereits eingerichtet. Änderungen an Jellyfin, Sprachmodell und Co. '
      + 'kann nur ein Admin vornehmen — unter Admin → Einstellungen.',
    schonEingerichtetAnmelden: 'CinematekAI ist bereits eingerichtet. Melde dich mit deinem Jellyfin-Konto an.',
    zurStartseite: 'Zur Startseite',
    zurAnmeldung: 'Zur Anmeldung',
    verbindungFehlt: 'Jellyfin-Verbindung fehlt',
    verbindungFehltText: 'Es gibt schon Nutzer, aber keine Verbindung zu Jellyfin — deshalb klappt gerade auch '
      + 'keine Anmeldung. Ein Admin, der in diesem Browser noch angemeldet ist, kann die '
      + 'Verbindung hier neu eintragen.',

    // Ansagen beim Schrittwechsel
    ansageSchonVerbunden: 'Jellyfin ist schon verbunden. Weiter mit dem Sprachmodell.',
    ansageVerbunden: 'Verbunden mit {server}.',
    ansageVerbundenAls: 'Verbunden mit {server}. Angemeldet als {name}.',
    ansageJellyfinVerbunden: 'Jellyfin ist verbunden.',
    ansageLlmGespeichert: 'Sprachmodell gespeichert.',
    ansageLlmUebersprungen: 'Sprachmodell übersprungen.',
    ansageEmbeddingGespeichert: 'Embeddings gespeichert.',
    ansageEmbeddingUebersprungen: 'Embeddings übersprungen.',
    ansageExtrasGespeichert: 'Extras gespeichert.',

    // Schritt 1
    nachtragText: 'Trag die Jellyfin-Verbindung neu ein: Adresse, einen API-Schlüssel '
      + '(Jellyfin → Dashboard → API-Schlüssel) und das Konto, dessen Bibliothek '
      + 'eingelesen wird.',
    einstellungenNichtGeladen: 'Einstellungen nicht geladen.',
    einstellungenNichtGeladenGrund: 'Einstellungen nicht geladen: {fehler}',
    erneutVersuchen: 'Erneut versuchen',
    jfIntro: 'CinematekAI liest deine Filme und Serien aus Jellyfin. Gib zuerst die Adresse '
      + 'deines Servers an.',
    jfAdresse: 'Adresse des Jellyfin-Servers',
    pruefen: 'Prüfen',
    pruefe: 'Prüfe …',
    jfAdresseHilfeVor: 'So, wie CinematekAI den Server erreicht.',
    jfAdresseHilfeNach: 'wäre hier der Container selbst — nimm die Adresse des Rechners, auf dem Jellyfin läuft.',
    gefunden: 'Gefunden:',
    gefundenVersion: ' · Jellyfin {version}',
    jfAnmeldenVor: 'Melde dich jetzt mit einem',
    jfAnmeldenFett: 'Jellyfin-Admin',
    jfAnmeldenNach: 'an. CinematekAI legt damit einen eigenen API-Schlüssel in Jellyfin an und macht '
      + 'dich zum ersten Admin. Dein Passwort wird nicht gespeichert.',
    benutzername: 'Benutzername',
    passwort: 'Passwort',
    oeffentlichOptional: 'Öffentliche Adresse (optional)',
    oeffentlich: 'Öffentliche Adresse',
    oeffentlichBeispiel: 'https://jellyfin.example.de',
    oeffentlichHilfe: 'Nur nötig, wenn dein Browser Jellyfin unter einer anderen Adresse öffnet als '
      + 'CinematekAI, z. B. über einen Reverse-Proxy. Leer = wie oben.',
    schluesselSelbst: 'API-Schlüssel selbst eintragen',
    schluessel: 'API-Schlüssel',
    schluesselHilfe: 'Statt einen neuen anzulegen, nimmt CinematekAI diesen (Jellyfin → Dashboard → '
      + 'API-Schlüssel). Leer lassen, um automatisch einen anzulegen.',
    verbinden: 'Verbinden',
    verbinde: 'Verbinde …',
    fehltAdresse: 'Trag die Adresse deines Jellyfin-Servers ein.',
    fehltAnmeldung: 'Trag Benutzername und Passwort eines Jellyfin-Admins ein.',

    // Schritte 2, 3
    llmText: 'Das Sprachmodell führt den Chat, wählt den Film des Tages aus und begründet '
      + 'Empfehlungen. Es kann bei einem Cloud-Anbieter laufen oder bei dir zu Hause, '
      + 'z. B. mit Ollama oder LM Studio.',
    speichernWeiter: 'Speichern und weiter',
    llmOhne: 'Ohne Sprachmodell gibt es keinen Chat und keine KI-Auswahl. Du kannst es später '
      + 'unter Admin → Einstellungen nachholen.',
    ueberspringen: 'Überspringen',
    embText1: 'Embeddings machen aus Titel, Handlung und Genres jedes Films einen Zahlenvektor. '
      + 'Darauf beruhen die',
    embStimmung: 'Suche nach Stimmung',
    embText2: '(„etwas Leichtes zum Abschalten“), die',
    embPassung: 'Passung in Prozent',
    embText3: 'und',
    embAehnlich: 'ähnliche Titel',
    embDatenschutz: 'Datenschutz: Bei einem Cloud-Anbieter gehen Titel, Handlung und Genres deiner '
      + 'Bibliothek an diesen Anbieter. Ein lokales Modell (Ollama, LM Studio) behält alles bei dir.',
    embOhne: 'Ohne Embeddings funktionieren die Suche nach Stimmung, die Passung in Prozent und '
      + 'ähnliche Titel nicht. Du kannst sie später unter Admin → Einstellungen einrichten.',
    trotzdemUeberspringen: 'Trotzdem überspringen',
    dochEinrichten: 'Doch einrichten',

    // Schritt 4
    extrasIntro: 'Alles hier ist freiwillig und lässt sich später unter Admin → Einstellungen ändern.',
    tmdbLegende: 'TMDB (The Movie Database)',
    tmdbText: 'Für „Wer das mochte, mochte auch“ und „Fehlt in deiner Sammlung“. Den '
      + 'Lesezugriffs-Token (API Read Access Token) gibt es kostenlos bei themoviedb.org '
      + 'unter Einstellungen → API.',
    tokenOptional: 'Token (optional)',
    tokenBehalten: 'Leer lassen = gespeicherten behalten',
    testen: 'Testen',
    teste: 'Teste …',
    tokenGesetzt: 'gesetzt',
    keinToken: 'Kein Token gespeichert',
    tokenZuerst: 'Trag zuerst den Token ein.',
    tokenGueltig: ({ zeit }) => `OK · Token gültig${zeit ? ` · ${zeit}` : ''}`,
    tokenAbgelehnt: 'TMDB lehnt den Token ab.',
    seerrAdresse: 'Adresse (optional)',
    seerrBeispiel: 'https://seerr.example.de',
    seerrHilfe: 'Mit Seerr (früher Jellyseerr) lassen sich fehlende Filme anfragen. Ist eine Adresse '
      + 'eingetragen, führen die Vorschläge „Fehlt in deiner Sammlung“ direkt dorthin.',
    laufLegende: 'Täglicher Lauf',
    laufText: 'Einmal am Tag gleicht CinematekAI die Bibliothek ab und rechnet die Empfehlungen neu.',
    uhrzeit: 'Uhrzeit',
    zeitzone: 'Zeitzone',
    browserZone: 'Dein Browser steht auf {zone}.',
    uebernehmen: 'Übernehmen',
    zoneBeispiel: 'Z. B. Europe/Berlin oder Europe/Zurich.',
    taeglichAusfuehren: 'Täglich automatisch ausführen',
    rueckfall: 'Rückfallmodell (optional)',
    rueckfallText: 'Springt ein, wenn das Sprachmodell nicht antwortet — z. B. ein lokales Modell als '
      + 'Ersatz für einen Cloud-Anbieter. Es hat einen eigenen Speichern-Knopf.',
    rueckfallSpeichern: 'Rückfallmodell speichern',
    speichere: 'Speichere …',
    seerrSchema: 'Seerr: Die Adresse muss mit http:// oder https:// beginnen.',
    zeitFormat: 'Gib die Uhrzeit als SS:MM an, z. B. 03:30.',
    zoneFehlt: 'Trag eine Zeitzone ein, z. B. Europe/Berlin.',
    abschnittFehler: ({ abschnitt, fehler }) => `${ABSCHNITT_DE[abschnitt] ?? abschnitt}: ${fehler}`,

    // Schritt 5: Uebersicht
    zJellyfin: 'Jellyfin',
    zSprachmodell: 'Sprachmodell',
    zEmbeddings: 'Embeddings',
    zTmdb: 'TMDB',
    zSeerr: 'Seerr',
    zZeitplan: 'Zeitplan',
    verbunden: 'verbunden',
    nichtVerbunden: 'nicht verbunden',
    nichtEingerichtet: 'nicht eingerichtet',
    tokenGesetztWert: 'Token gesetzt',
    ohne: 'ohne',
    taeglichUm: 'täglich {zeit} ({zone})',
    aus: 'aus',

    // Schritt 5: Lauf
    fertigText: 'Jetzt fehlt nur noch deine Bibliothek. Der erste Lauf liest Filme und Serien aus '
      + 'Jellyfin, berechnet die Embeddings und die ersten Empfehlungen. Bei einer großen '
      + 'Bibliothek und einem lokalen Modell kann das Stunden dauern. Du kannst die Seite '
      + 'währenddessen verlassen — der Lauf geht im Hintergrund weiter.',
    laeuftKnopf: 'Läuft …',
    nochEinmal: 'Noch einmal einlesen',
    jetztEinlesen: 'Bibliothek jetzt einlesen',
    starte: 'Starte …',
    schonUnterwegs: 'Der Lauf ist schon unterwegs.',
    laeuftSeit: 'Läuft seit {zeit}',
    mitFehler: 'Mit Fehler beendet',
    fertig: 'Fertig',
    schritteFertig: '{fertig} von {gesamt} Schritten',
    fortschrittLauf: 'Fortschritt des ersten Laufs',
    embFortschritt: 'Embeddings · {phase}: {fertig} von {gesamt}',
    restZeit: ({ min, n }) => (min < 1 ? 'noch etwa eine Minute' : `noch etwa ${n} min`),
    fortschrittEmb: 'Fortschritt der Embeddings',
    standLaeuft: 'läuft',
    standWartet: 'wartet',
    standUebersprungen: 'übersprungen',
    standFertig: 'fertig',
    ansageLaeuftJob: 'Läuft: {name}',
    ansageLaeuft: 'Läuft …',
    ansageFertig: 'Erster Lauf fertig: {ergebnis}',
    ansageFehler: 'Lauf mit Fehler beendet: {fehler}',
  },
  en: {
    seitenTitel: 'Setup · CinematekAI',
    titel: 'Setup',

    // Schrittanzeige
    schrittJellyfin: 'Jellyfin',
    schrittSprachmodell: 'Language model',
    schrittEmbeddings: 'Embeddings',
    schrittExtras: 'Extras',
    schrittFertig: 'Done',
    titelJellyfin: 'Connect Jellyfin',
    titelSprachmodell: 'Language model',
    titelEmbeddings: 'Embeddings',
    titelExtras: 'Extras',
    titelFertig: 'Scan library',
    schritteLabel: 'Setup steps',
    zusatzUebersprungen: ' (skipped)',
    zusatzErledigt: ' (done)',
    schrittLabel: 'Step {nr}: {name}{zusatz}',
    schrittVon: 'Step {nr} of {gesamt}',

    // Laden, gesperrt
    serverNichtErreichbar: 'Server unreachable',
    neuLaden: 'Reload',
    schonEingerichtet: 'Already set up',
    schonEingerichtetAdmin: 'CinematekAI is already set up. Only an admin can change Jellyfin, the language '
      + 'model and the rest — under Admin → Settings.',
    schonEingerichtetAnmelden: 'CinematekAI is already set up. Sign in with your Jellyfin account.',
    zurStartseite: 'Go to home page',
    zurAnmeldung: 'Go to sign-in',
    verbindungFehlt: 'Jellyfin connection missing',
    verbindungFehltText: "There are users already, but no connection to Jellyfin — that's why signing in "
      + "doesn't work right now either. An admin who is still signed in in this browser can "
      + 're-enter the connection here.',

    // Ansagen beim Schrittwechsel
    ansageSchonVerbunden: 'Jellyfin is already connected. Next up: the language model.',
    ansageVerbunden: 'Connected to {server}.',
    ansageVerbundenAls: 'Connected to {server}. Signed in as {name}.',
    ansageJellyfinVerbunden: 'Jellyfin is connected.',
    ansageLlmGespeichert: 'Language model saved.',
    ansageLlmUebersprungen: 'Language model skipped.',
    ansageEmbeddingGespeichert: 'Embeddings saved.',
    ansageEmbeddingUebersprungen: 'Embeddings skipped.',
    ansageExtrasGespeichert: 'Extras saved.',

    // Schritt 1
    nachtragText: 'Re-enter the Jellyfin connection: the address, an API key '
      + '(Jellyfin → Dashboard → API Keys) and the account whose library gets '
      + 'scanned.',
    einstellungenNichtGeladen: 'Settings not loaded.',
    einstellungenNichtGeladenGrund: 'Settings not loaded: {fehler}',
    erneutVersuchen: 'Try again',
    jfIntro: 'CinematekAI reads your movies and shows from Jellyfin. First, enter the address '
      + 'of your server.',
    jfAdresse: 'Jellyfin server address',
    pruefen: 'Check',
    pruefe: 'Checking …',
    jfAdresseHilfeVor: 'The address CinematekAI uses to reach the server.',
    jfAdresseHilfeNach: 'would be the container itself here — use the address of the machine Jellyfin runs on.',
    gefunden: 'Found:',
    gefundenVersion: ' · Jellyfin {version}',
    jfAnmeldenVor: 'Now sign in with a',
    jfAnmeldenFett: 'Jellyfin admin',
    jfAnmeldenNach: 'account. CinematekAI uses it to create its own API key in Jellyfin and makes '
      + 'you the first admin. Your password is not stored.',
    benutzername: 'Username',
    passwort: 'Password',
    oeffentlichOptional: 'Public address (optional)',
    oeffentlich: 'Public address',
    oeffentlichBeispiel: 'https://jellyfin.example.com',
    oeffentlichHilfe: 'Only needed if your browser opens Jellyfin at a different address than '
      + 'CinematekAI, e.g. via a reverse proxy. Empty = same as above.',
    schluesselSelbst: 'Enter an API key yourself',
    schluessel: 'API key',
    schluesselHilfe: 'Instead of creating a new one, CinematekAI uses this one (Jellyfin → Dashboard → '
      + 'API Keys). Leave empty to create one automatically.',
    verbinden: 'Connect',
    verbinde: 'Connecting …',
    fehltAdresse: 'Enter the address of your Jellyfin server.',
    fehltAnmeldung: 'Enter the username and password of a Jellyfin admin.',

    // Schritte 2, 3
    llmText: 'The language model runs the chat, picks the movie of the day and explains '
      + 'recommendations. It can run at a cloud provider or at your home, '
      + 'e.g. with Ollama or LM Studio.',
    speichernWeiter: 'Save and continue',
    llmOhne: "Without a language model there's no chat and no AI picks. You can set it up later "
      + 'under Admin → Settings.',
    ueberspringen: 'Skip',
    embText1: 'Embeddings turn the title, plot and genres of every movie into a vector of numbers. '
      + 'They power the',
    embStimmung: 'mood search',
    embText2: '(“something light to unwind with”), the',
    embPassung: 'match percentage',
    embText3: 'and',
    embAehnlich: 'similar titles',
    embDatenschutz: 'Privacy: with a cloud provider, the titles, plots and genres of your '
      + 'library go to that provider. A local model (Ollama, LM Studio) keeps everything with you.',
    embOhne: "Without embeddings, mood search, the match percentage and similar titles won't "
      + 'work. You can set them up later under Admin → Settings.',
    trotzdemUeberspringen: 'Skip anyway',
    dochEinrichten: 'Set up after all',

    // Schritt 4
    extrasIntro: 'Everything here is optional and can be changed later under Admin → Settings.',
    tmdbLegende: 'TMDB (The Movie Database)',
    tmdbText: 'For “People who liked this also liked” and “Missing from your collection”. You can get '
      + 'the read access token (API Read Access Token) for free at themoviedb.org '
      + 'under Settings → API.',
    tokenOptional: 'Token (optional)',
    tokenBehalten: 'Leave empty = keep saved one',
    testen: 'Test',
    teste: 'Testing …',
    tokenGesetzt: 'set',
    keinToken: 'No token saved',
    tokenZuerst: 'Enter the token first.',
    tokenGueltig: ({ zeit }) => `OK · Token valid${zeit ? ` · ${zeit}` : ''}`,
    tokenAbgelehnt: 'TMDB rejected the token.',
    seerrAdresse: 'Address (optional)',
    seerrBeispiel: 'https://seerr.example.com',
    seerrHilfe: 'Seerr (formerly Jellyseerr) lets you request missing movies. With an address '
      + 'entered, the “Missing from your collection” suggestions link straight to it.',
    laufLegende: 'Daily run',
    laufText: 'Once a day CinematekAI syncs the library and recalculates the recommendations.',
    uhrzeit: 'Time',
    zeitzone: 'Time zone',
    browserZone: 'Your browser is set to {zone}.',
    uebernehmen: 'Use it',
    zoneBeispiel: 'E.g. Europe/London or America/New_York.',
    taeglichAusfuehren: 'Run automatically every day',
    rueckfall: 'Fallback model (optional)',
    rueckfallText: "Steps in when the language model doesn't respond — e.g. a local model as a "
      + 'stand-in for a cloud provider. It has its own Save button.',
    rueckfallSpeichern: 'Save fallback model',
    speichere: 'Saving …',
    seerrSchema: 'Seerr: The address must start with http:// or https://.',
    zeitFormat: 'Enter the time as HH:MM, e.g. 03:30.',
    zoneFehlt: 'Enter a time zone, e.g. Europe/Berlin.',
    abschnittFehler: ({ abschnitt, fehler }) => `${ABSCHNITT_EN[abschnitt] ?? abschnitt}: ${fehler}`,

    // Schritt 5: Uebersicht
    zJellyfin: 'Jellyfin',
    zSprachmodell: 'Language model',
    zEmbeddings: 'Embeddings',
    zTmdb: 'TMDB',
    zSeerr: 'Seerr',
    zZeitplan: 'Schedule',
    verbunden: 'connected',
    nichtVerbunden: 'not connected',
    nichtEingerichtet: 'not set up',
    tokenGesetztWert: 'Token set',
    ohne: 'none',
    taeglichUm: 'daily at {zeit} ({zone})',
    aus: 'off',

    // Schritt 5: Lauf
    fertigText: "Now all that's missing is your library. The first run reads movies and shows from "
      + 'Jellyfin and computes the embeddings and the first recommendations. With a large '
      + 'library and a local model this can take hours. You can leave the page '
      + 'in the meantime — the run keeps going in the background.',
    laeuftKnopf: 'Running …',
    nochEinmal: 'Scan again',
    jetztEinlesen: 'Scan library now',
    starte: 'Starting …',
    schonUnterwegs: 'The run is already in progress.',
    laeuftSeit: 'Running since {zeit}',
    mitFehler: 'Finished with an error',
    fertig: 'Done',
    schritteFertig: '{fertig} of {gesamt} steps',
    fortschrittLauf: 'Progress of the first run',
    embFortschritt: 'Embeddings · {phase}: {fertig} of {gesamt}',
    restZeit: ({ min, n }) => (min < 1 ? 'about a minute left' : `about ${n} min left`),
    fortschrittEmb: 'Embedding progress',
    standLaeuft: 'running',
    standWartet: 'waiting',
    standUebersprungen: 'skipped',
    standFertig: 'done',
    ansageLaeuftJob: 'Running: {name}',
    ansageLaeuft: 'Running …',
    ansageFertig: 'First run finished: {ergebnis}',
    ansageFehler: 'Run finished with an error: {fehler}',
  },
};
