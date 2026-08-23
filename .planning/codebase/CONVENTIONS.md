# Conventions

**Analysis Date:** 2026-05-31

## Python Conventions

- Use snake_case for functions, route handlers, and local helpers.
- Keep route handlers in `backend/app.py` returning JSON with `jsonify()`.
- Validate request payloads explicitly before using them.
- Prefer small helper functions for reusable database or model logic.
- Keep side-effectful startup code obvious and near module top-level when it is required for initialization.

## JavaScript Conventions

- Use `const` by default and `let` only when reassignment is needed.
- Keep browser modules in singleton-style objects such as `ApiLayer` and `BioBERT`.
- Use camelCase for browser functions and state variables.
- Treat modules in `src/` as globals loaded in script order, not imported ES modules.
- Centralize remote API fetches in `src/api_layer.js`.

## HTML / CSS Conventions

- Keep markup semantic and section-based in `src/index.html`.
- Use CSS custom properties from `src/style.css` for color, spacing, and typography.
- Preserve the CDC-inspired visual language instead of introducing a new theme piecemeal.
- Favor utility classes and component blocks already defined in the stylesheet.

## Data / State Conventions

- Store durable local data in SQLite through `backend/database.py`.
- Store model artifacts in `backend/trained_models/`.
- Keep external API caches in browser-memory objects or backend helpers rather than scattered globals.
- Normalize drug and event strings before storing them in the database.

## Error Handling Conventions

- Backend errors should return JSON with an explanatory `error` field and a meaningful HTTP status.
- Frontend fetch failures should warn or fall back gracefully without breaking the rest of the dashboard.
- Optional subsystems should degrade cleanly when imports or services are unavailable.

## Startup / Loading Conventions

- The backend loads `.env`, the SQLite schema, and the NER model during import/startup.
- The frontend depends on the script order defined in `src/index.html`.
- New browser code should assume that DOM elements may not exist and guard accordingly.

## Things to Preserve

- Plain JavaScript instead of a new frontend framework.
- Direct Flask serving of the static frontend.
- The current charting and dashboard patterns.
- Existing naming patterns in `backend/` and `src/`.
