.PHONY: up down db-shell test lint

up:            ## start db + api
	docker compose up --build -d

down:
	docker compose down

db-shell:
	docker compose exec db psql -U bluewake bluewake

test:
	cd backend && python -m pytest

lint:
	cd backend && ruff check app tests
