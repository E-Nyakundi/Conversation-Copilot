# Conversation Copilot — dev/prod command wrappers.
#
# Why this exists: this repo runs two separate Docker Compose stacks on
# the VM — a dev one (docker-compose.yml, service `backend`, container
# conversation-copilot-backend-1) and the actual prod one that real
# traffic hits (infra/docker-compose.prod.yml, service `copilot_backend`,
# routed to by BizYangu's Caddy — see infra/caddy-copilot.snippet). Both
# used to require remembering `-f infra/docker-compose.prod.yml -p
# copilot` by hand for prod, and it's easy to forget — the dev command
# succeeds silently either way, so a forgotten flag looks identical to a
# working deploy. These targets make that mistake structurally harder:
# `make dev-*` and `make prod-*` are typed differently on purpose, and
# each echoes which stack it's touching before running anything.
#
# Run `make help` (or just `make`) for the full list.

.DEFAULT_GOAL := help

.PHONY: help dev-build dev-up dev-deploy dev-logs dev-shell dev-env \
        prod-build prod-up prod-deploy prod-logs prod-shell prod-env

help:
	@echo "Dev stack   (docker-compose.yml, container conversation-copilot-backend-1):"
	@echo "  make dev-build    build the dev image"
	@echo "  make dev-up       start/restart the dev container"
	@echo "  make dev-deploy   build + start in one step"
	@echo "  make dev-logs     tail dev backend logs"
	@echo "  make dev-shell    Django shell in the dev container"
	@echo "  make dev-migrate  run pending migrations in the dev container"
	@echo "  make dev-env      print GROQ_/WHISPER_ env vars in the dev container"
	@echo ""
	@echo "Prod stack  (infra/docker-compose.prod.yml, container copilot_backend"
	@echo "             — this is the one real traffic / wss://...sslip.io hits):"
	@echo "  make prod-build   build the prod image"
	@echo "  make prod-up      start/restart the prod container"
	@echo "  make prod-deploy  build + start in one step (use this after any code change)"
	@echo "  make prod-logs    tail prod backend logs"
	@echo "  make prod-shell   Django shell in the prod container"
	@echo "  make prod-migrate run pending migrations in the prod container"
	@echo "  make prod-env     print GROQ_/WHISPER_ env vars in the prod container"

# --- dev -------------------------------------------------------------

dev-build:
	@echo "=== DEV: building conversation-copilot-backend-1 ==="
	docker compose build backend

dev-up:
	@echo "=== DEV: starting conversation-copilot-backend-1 ==="
	docker compose up -d backend

dev-deploy:
	@echo "=== DEV: build + start conversation-copilot-backend-1 ==="
	docker compose up -d --build backend

dev-logs:
	@echo "=== DEV: tailing conversation-copilot-backend-1 ==="
	docker compose logs -f backend

dev-shell:
	docker compose exec backend python manage.py shell

dev-migrate:
	docker compose exec backend python manage.py migrate

dev-env:
	docker compose exec backend env | grep -E "GROQ|WHISPER"

# --- prod --------------------------------------------------------------

prod-build:
	@echo "=== PROD: building copilot_backend (this is what real traffic hits) ==="
	docker compose -f infra/docker-compose.prod.yml -p copilot build copilot_backend

prod-up:
	@echo "=== PROD: starting copilot_backend (this is what real traffic hits) ==="
	docker compose -f infra/docker-compose.prod.yml -p copilot up -d copilot_backend

prod-deploy:
	@echo "=== PROD: build + start copilot_backend (this is what real traffic hits) ==="
	docker compose -f infra/docker-compose.prod.yml -p copilot up -d --build copilot_backend

prod-logs:
	@echo "=== PROD: tailing copilot_backend (this is what real traffic hits) ==="
	docker compose -f infra/docker-compose.prod.yml -p copilot logs -f copilot_backend

prod-shell:
	docker compose -f infra/docker-compose.prod.yml -p copilot exec copilot_backend python manage.py shell

prod-migrate:
	docker compose -f infra/docker-compose.prod.yml -p copilot exec copilot_backend python manage.py migrate

prod-env:
	docker compose -f infra/docker-compose.prod.yml -p copilot exec copilot_backend env | grep -E "GROQ|WHISPER"
