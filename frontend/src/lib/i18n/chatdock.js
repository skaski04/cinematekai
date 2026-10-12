/* Der Chat als Bild im Bild (components/ChatDock.svelte) und die gemeinsame
   Leitung zum Chat (lib/chatStream.js). */
export default {
  de: {
    // ChatDock
    oeffnen: 'Assistent öffnen',
    assistent: 'Assistent',
    vollbild: 'Im Vollbild öffnen',
    minimieren: 'Minimieren',
    siehtGerade: 'Sieht gerade: {kontext}',
    start: 'Frag mich, worauf du Lust hast — ich kenne deine Bibliothek und was du zuletzt geschaut hast.',
    abgebrochen: 'Abgebrochen — diese Antwort wurde nie zu Ende geschrieben.',
    erneutVersuchen: 'Erneut versuchen',
    geduldLang: 'CinematekAI denkt noch nach. Das dauert heute ungewöhnlich lange — du kannst die Seite verlassen und später zurückkommen, der Verlauf bleibt 24 h erhalten.',
    geduldVor: 'CinematekAI denkt nach und antwortet meist nach',
    geduldDauer: '20–60 Sekunden',
    geduldNach: '.',
    platzhalter: 'Frag mich etwas…',
    nachricht: 'Nachricht',
    senden: 'Senden',
    fehlerAnhang: '[Fehler: {fehler}]',
    // Geht als Teil der Frage an das Modell und steht danach im Verlauf.
    kontextFrage: ({ kontext, text }) => `Ich schaue gerade auf ${kontext}. ${text}`,

    // chatStream.js
    dienstWeg: 'Der Chat-Dienst ist gerade nicht erreichbar (503).',
    sitzungAbgelaufen: 'Deine Sitzung ist abgelaufen — bitte neu anmelden.',
    serverfehler: 'Serverfehler ({status}). Das lag nicht an dir.',
    abgelehnt: 'Die Anfrage wurde abgelehnt ({status}).',
    abgerissen: 'Die Verbindung ist mitten in der Antwort abgerissen.',
    keineVerbindung: 'Keine Verbindung zum Server. Prüfe, ob CinematekAI erreichbar ist.',
  },
  en: {
    // ChatDock
    oeffnen: 'Open assistant',
    assistent: 'Assistant',
    vollbild: 'Open full screen',
    minimieren: 'Minimize',
    siehtGerade: 'Looking at: {kontext}',
    start: "Ask me what you're in the mood for — I know your library and what you've watched lately.",
    abgebrochen: 'Cancelled — this answer was never finished.',
    erneutVersuchen: 'Try again',
    geduldLang: 'CinematekAI is still thinking. This is taking unusually long today — you can leave the page and come back later; the history is kept for 24 h.',
    geduldVor: 'CinematekAI is thinking and usually replies within',
    geduldDauer: '20–60 seconds',
    geduldNach: '.',
    platzhalter: 'Ask me something…',
    nachricht: 'Message',
    senden: 'Send',
    fehlerAnhang: '[Error: {fehler}]',
    kontextFrage: ({ kontext, text }) => `I'm currently looking at ${kontext}. ${text}`,

    // chatStream.js
    dienstWeg: 'The chat service is currently unavailable (503).',
    sitzungAbgelaufen: 'Your session has expired — please sign in again.',
    serverfehler: "Server error ({status}). It wasn't your fault.",
    abgelehnt: 'The request was rejected ({status}).',
    abgerissen: 'The connection dropped in the middle of the answer.',
    keineVerbindung: 'No connection to the server. Check that CinematekAI is reachable.',
  },
};
