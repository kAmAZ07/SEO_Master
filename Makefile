PYTHON ?= python
MODE ?= dev
PHP ?= php
PROJECT_ROOT := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

.PHONY: bootstrap config up test e2e lint build down
bootstrap config up test e2e lint build down:
	"$(PYTHON)" "$(PROJECT_ROOT)scripts/project.py" $@ --mode "$(MODE)" --php "$(PHP)"
