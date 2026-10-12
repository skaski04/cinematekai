import adapter from '@sveltejs/adapter-static';

/* Statisch gebaut statt eigener Node-Server: das Backend liefert die fertigen
   Dateien unter demselben Port aus wie die API (backend/server.py). Damit
   gibt es nur noch ein Image, einen Prozess und keinen Proxy dazwischen.
   Jede Seite laedt ihre Daten ohnehin im Browser (siehe +layout.js), deshalb
   reicht eine einzige index.html als Einstieg fuer alle Pfade. */

/** @type {import('@sveltejs/kit').Config} */
const config = {
  kit: {
    adapter: adapter({
      pages: 'build',
      assets: 'build',
      fallback: 'index.html',
      /* Keine Seite wird vorgerendert, alles laeuft ueber die index.html.
         strict: false haelt fest, dass das Absicht ist und kein Versehen. */
      strict: false,
    }),
  },
};

export default config;
