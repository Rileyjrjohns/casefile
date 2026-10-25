# ADR 013: One review screen and a local Compose application

Status: Accepted.

React, strict TypeScript and Vite implement a single case/review screen. Vitest
and Testing Library exercise confirmation, correction and error states; Chromium
validates the real application. No global state library is needed for one screen.
Nginx serves the static build and proxies API requests on the same origin.

Compose starts PostgreSQL, a one-shot Alembic migration, the API/worker/MCP
process and nginx. Health/dependency checks order startup. Ports bind to loopback
and the database persists in a named volume. The API runs as a non-root user.
This is a reproducible local demo, not a production deployment specification.
