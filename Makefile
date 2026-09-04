# RecoverAI — one-command Docker workflow.
#
#   make up      build and run the whole stack (Postgres + API + frontend)
#   make down    stop it
#   make help    list everything
#
# Requires Docker (with the Compose v2 plugin) and GNU Make.

COMPOSE := docker compose

.DEFAULT_GOAL := help
.PHONY: help up up-d down stop restart rebuild logs ps migrate seed test clean

help: ## List the available targets
	@awk 'BEGIN{FS":.*?## "} /^[a-zA-Z_-]+:.*?## /{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

up: ## Build (if needed) and run the full stack in the foreground
	$(COMPOSE) up --build

up-d: ## Build and run detached, then print the URLs
	$(COMPOSE) up --build -d
	@echo ""
	@echo "  Frontend   ->  http://localhost:8080"
	@echo "  API + docs ->  http://localhost:8000/docs"
	@echo "  Health     ->  http://localhost:8000/health"
	@echo ""
	@echo "  make logs   to follow output   |   make down   to stop"

down: ## Stop and remove the containers (keeps the database volume)
	$(COMPOSE) down

stop: ## Stop the containers without removing them
	$(COMPOSE) stop

restart: ## Restart the backend and frontend containers
	$(COMPOSE) restart backend frontend

rebuild: ## Rebuild the images from scratch (no cache)
	$(COMPOSE) build --no-cache

logs: ## Follow logs from all services
	$(COMPOSE) logs -f

ps: ## Show container status
	$(COMPOSE) ps

migrate: ## Run alembic migrations against the running database
	$(COMPOSE) exec backend python -m alembic upgrade head

seed: ## Load the demo batch (30 cases through the real pipeline, fake I/O)
	$(COMPOSE) exec backend python -m scripts.seed_demo_batch

test: ## Run the backend test suite in a throwaway container
	$(COMPOSE) run --rm --build backend-test

clean: ## Stop everything and DELETE the database volume (destructive)
	$(COMPOSE) down -v
