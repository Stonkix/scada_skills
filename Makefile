PY ?= python

.PHONY: up down reset migrate seed contracts test api ps logs revision
up down reset migrate seed contracts test api ps logs:
	$(PY) dev.py $@

# make revision m="add vehicles.vin"
revision:
	$(PY) dev.py revision "$(m)"
