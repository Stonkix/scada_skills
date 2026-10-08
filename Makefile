PY ?= python

.PHONY: up down reset seed contracts test api ps logs
up down reset seed contracts test api ps logs:
	$(PY) dev.py $@
