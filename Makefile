.PHONY: install test eval data api ui mcp docker

install:
	pip install -e ".[api,ui,mcp,dev]"
test:
	pytest -q
eval:
	python eval/run_eval.py dataset && python eval/run_eval.py holdout
data:
	python eval/generate.py
api:
	uvicorn docflow.api:app --reload
ui:
	streamlit run ui/app.py
mcp:
	python -m docflow.mcp_server
docker:
	docker compose up --build
