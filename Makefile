.PHONY: install dev-api dev-ui build test serve experiments examples bounds

install:
	python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
	cd frontend && npm install

dev-api:
	.venv/bin/uvicorn backend.app.main:app --reload --port 8000

dev-ui:
	cd frontend && npm run dev

build:
	cd frontend && npm run build

test:
	.venv/bin/pytest -q

serve: build
	.venv/bin/uvicorn backend.app.main:app --host 0.0.0.0 --port 8000

experiments:
	.venv/bin/python -m experiments.run_experiments

examples:
	.venv/bin/python -m experiments.run_experiments --examples

bounds:
	.venv/bin/python -m experiments.upper_bound
