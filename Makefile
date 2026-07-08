COMPOSE ?= docker-compose

.PHONY: up down restart ps logs logs-api logs-web migrate seed test build urls

up:
	$(COMPOSE) up -d --build postgres adminer api web
	$(MAKE) urls

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) restart api web
	$(MAKE) urls

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f --tail=120 api web postgres

logs-api:
	$(COMPOSE) logs -f --tail=160 api

logs-web:
	$(COMPOSE) logs -f --tail=160 web

migrate:
	$(COMPOSE) run --rm api python -m alembic upgrade head

seed:
	$(COMPOSE) run --rm api python -m ict.cli db seed-defaults

test:
	python -m pytest

build:
	cd web && npm run build

urls:
	@echo ICT Trading Lab
	@echo   Web:     http://127.0.0.1:5173
	@echo   API:     http://127.0.0.1:8000/api/health
	@echo   Adminer: http://127.0.0.1:8080
	@echo   Postgres: localhost:5432
