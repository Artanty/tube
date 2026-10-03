# Tube — web

Angular 19 frontend for the downloader API in [`../back`](../back).

## Run

```bash
npm install
npm start          # http://localhost:4200
```

The API must be running on `:5001`; `ng serve` forwards `/api` there via `proxy.conf.json`
(wired up in `angular.json` under `architect.serve.options.proxyConfig`).

## Structure

| File | Role |
| --- | --- |
| `src/app/models.ts` | `Task`, `FormatOption`, response shapes |
| `src/app/download.service.ts` | the three calls: start, list, formats |
| `src/app/app.component.ts` | signals, polling, labels |
| `src/app/app.component.html` | URL field, quality picker, task list |
| `src/app/app.component.css` | styles; theme vars live in `src/styles.css` |

State is Angular signals. One `setInterval` polls `GET /api/tasks` once a second and is torn down
as soon as nothing is in flight — a single request covers the whole list regardless of how many
downloads are running. `GET /api/tasks` on startup means a browser refresh does not lose the view
of finished downloads.

## Build

```bash
npm run build      # -> dist/web
```

The build is wired to `dist/web`, not to Flask. To serve it from the API instead, add a static
route and set `baseHref`; right now the dev-server proxy is the supported setup.