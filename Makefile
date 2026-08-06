# Variables
DOCKER_REGISTRY ?= harbor.remystorage.ru
DOCKER_ORG ?= eestec
IMAGE_NAME ?= masterchance
VERSION ?= 1.0.2
DOCKER_IMAGE = $(DOCKER_REGISTRY)/$(DOCKER_ORG)/$(IMAGE_NAME)

# Docker build flags
DOCKER_BUILD_FLAGS ?= --no-cache

.PHONY: build push all clean help run run-web run-bot run-desktop seed snapshot publish-snapshot server-update exe test check-imports web-docker compose-up compose-down version bump-version

help: ## Display this help message
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@awk -F ':|##' '/^[^\t].+?:.*?##/ { printf "  %-20s %s\n", $$1, $$NF }' $(MAKEFILE_LIST)

build: ## Build the Docker image
	@echo "Building $(DOCKER_IMAGE):$(VERSION)"
	docker build $(DOCKER_BUILD_FLAGS) \
		--build-arg VERSION=$(VERSION) \
		-t $(DOCKER_IMAGE):$(VERSION) \
		-t $(DOCKER_IMAGE):latest \
		.

push: ## Push the Docker image to registry
	@echo "Pushing $(DOCKER_IMAGE):$(VERSION)"
	docker push $(DOCKER_IMAGE):$(VERSION)
	docker push $(DOCKER_IMAGE):latest

all: build push ## Build and push the Docker image

clean: ## Remove local Docker images
	@echo "Removing $(DOCKER_IMAGE):$(VERSION)"
	docker rmi $(DOCKER_IMAGE):$(VERSION) || true
	docker rmi $(DOCKER_IMAGE):latest || true

# Development commands
run: build ## Build and run the container locally
	docker run -p 8080:8080 $(DOCKER_IMAGE):$(VERSION)

run-web: ## Run the web frontend locally (uvicorn)
	python web.py

run-bot: ## Run the Telegram bot locally
	python bot.py

seed: ## Seed the DB with synthetic data for local testing
	python seed_synthetic.py --reset

run-desktop: ## Run the desktop client from source
	python desktop.py

snapshot: ## Build the DB snapshot for the desktop client (dist/master-snapshot.db.gz)
	python build_snapshot.py

publish-snapshot: ## Upload the snapshot to the GitHub release (needs GITHUB_TOKEN)
	scripts/publish_snapshot.sh

server-update: ## Full server cycle: fetch lists, recalculate, snapshot, publish
	scripts/server_update.sh

exe: ## Build MasterChance.exe (Windows only; CI does this on windows-latest)
	pyinstaller packaging/masterchance.spec

test: ## Run the offline test suite (pytest)
	python -m pytest

check-imports: ## Verify server entrypoints import (catches missing requirements)
	python scripts/check_imports.py

web-docker: build ## Run the web frontend in a container (port 8080)
	docker run -p 8080:8080 --env-file .env -v $(PWD)/data:/app/data $(DOCKER_IMAGE):$(VERSION) web.py

compose-up: ## Build and start bot + web via docker compose
	docker compose up --build

compose-down: ## Stop docker compose services
	docker compose down

# Version management
version: ## Show current version
	@echo $(VERSION)

bump-version: ## Bump version (usage: make bump-version VERSION=1.0.2)
	@echo $(VERSION) > VERSION
	@echo "Version bumped to $(VERSION)"