# Packaged API explorer JavaScript

These scripts power the lightweight explorer served by the Python API under `/explorer` and `/public/js/`. This is a diagnostic UI packaged with the API; the main chart website is a separate Vite app.

Current modules in this directory are `app.js`, `utils.js`, `health.js`, `groups.js`, `analysis.js`, `ticker-explorer.js`, `refresh.js`, `rrg.js`, `sector-analysis.js`, and `volume-profile.js`. `app.js` initializes the page and loads feature modules; `utils.js` builds same-origin API URLs and shared formatting; the remaining modules handle the corresponding explorer forms and views.

When changing an endpoint or query builder, verify the generated URL and response parsing against `aipriceaction-api/CONTRACT.md` and the API tests. Keep API calls same-origin so the packaged explorer uses the origin serving the Python API.
