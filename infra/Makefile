# Custom `sam build` target for HttpHandlerFunction (see template.yaml's
# `Metadata: BuildMethod: makefile` on that resource).
#
# Why a Makefile and not SAM's built-in Python build workflow: that workflow
# only knows how to install from a `requirements.txt` file. This project
# uses `uv` and a PEP 621 `pyproject.toml` (frozen for this task — no
# requirements.txt is added at the repo root to work around that). `pip
# install --target ... .` reads pyproject.toml directly via its declared
# build backend (hatchling), so it works without one.
#
# SAM invokes `make build-<LogicalId>` from this directory (the template's
# location) with $(ARTIFACTS_DIR) and $(CODE_ROOT) already set in the
# environment: CODE_ROOT is the function's CodeUri (../, i.e. the repo
# root) resolved to an absolute path; ARTIFACTS_DIR is where the built
# Lambda package must land.
build-HttpHandlerFunction:
	pip install --no-cache-dir --target "$(ARTIFACTS_DIR)" "$(CODE_ROOT)"
