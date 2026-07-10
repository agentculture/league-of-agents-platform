# Custom `sam build` targets (see template.yaml's `Metadata: BuildMethod:
# makefile` on HttpHandlerFunction and CleanupFunction).
#
# Why a Makefile and not SAM's built-in Python build workflow: that workflow
# only installs from a `requirements.txt`. This project uses uv and a PEP 621
# pyproject.toml; `pip install --target ... .` reads pyproject.toml directly
# via its build backend (hatchling), so no requirements.txt is needed.
#
# SAM copies the function's CodeUri (../ from infra/, i.e. this repo root)
# to a scratch dir and runs `make build-<LogicalId>` FROM THAT COPY with
# $(ARTIFACTS_DIR) set - hence this file lives at the repo root and installs
# from the current directory.
build-HttpHandlerFunction:
	pip install --no-cache-dir --target "$(ARTIFACTS_DIR)" .

build-CleanupFunction:
	pip install --no-cache-dir --target "$(ARTIFACTS_DIR)" .
