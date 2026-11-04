.PHONY: up down logs build migrate revision test lint shell

up:        ## поднять всё
	docker compose up -d --build
down:
	docker compose down
logs:
	docker compose logs -f bot
build:
	docker compose build
migrate:
	docker compose run --rm bot alembic upgrade head
revision:
	docker compose run --rm bot alembic revision --autogenerate -m "$(m)"
test:
	.venv/bin/python -m pytest -q
lint:
	.venv/bin/python -m ruff check app tests
shell:
	docker compose exec bot sh
