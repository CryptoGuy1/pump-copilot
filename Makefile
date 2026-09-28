.PHONY: dev web-install gen-api test test-web build-web e2e e2e-timing

dev:            ## database, worker, API and web dev server
	./scripts/dev.sh

web-install:
	cd web && npm ci

gen-api:        ## regenerate api/openapi.json and the typed web client
	.venv/bin/pumpcopilot api --export-openapi
	cd web && npm run gen:api

test:           ## Python tests (database tests skip if it is down)
	.venv/bin/python -m pytest -q

test-web:       ## Vitest (stream hook) and a type check
	cd web && npm test && npm run typecheck

build-web:      ## fails if the typed client is out of date
	cd web && npm run build

e2e:            ## Playwright end-to-end, on its own database, API (8001) and web (5174)
	cd web && npx playwright test

e2e-timing:     ## the timing run against the production build
	cd web && E2E_WEB=preview npx playwright test timing
