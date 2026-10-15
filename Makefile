.PHONY: eval baseline seed

eval:
	python -m docpilot.eval --set irs --report-dir eval/reports

baseline:
	python eval/langchain_baseline.py --set irs --report-dir eval/reports

seed:
	python -m docpilot.seed --set $(SET)
