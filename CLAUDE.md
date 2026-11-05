@AGENTS.md

## Python environment
Always use the project virtualenv, never the system Python: run everything through `.venv/bin/python` (e.g. `.venv/bin/python -m pytest tests`, `.venv/bin/python -m pyflakes bot core scripts tests main.py`) and install packages with `.venv/bin/pip`. The system `python3` lacks dependencies such as `boto3`, so tests fail there for the wrong reason.
