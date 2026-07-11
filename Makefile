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
#
# HttpHandlerFunction additionally installs the `league-of-agents` game
# package (the separate PyPI package providing the `league` CLI - see
# docs/game-integration.md) into the same artifact dir, since
# league_site/game/runner.py drives it as a subprocess at request time. It
# is NOT a project dependency (pyproject.toml) - subprocess-only is a hard
# architectural rule (tests/test_game_import_boundary.py bans `import
# league`); this Makefile pin is the only place the package name appears.
# The `league` console-script this install produces is not guaranteed to
# land on PATH inside a --target artifact; runner.py's LEAGUE_CLI_MODULE
# env var mode (`sys.executable -m league`) is how the deployed handler
# runs it regardless.
# The docs/ tree must ship inside the artifact: league_site/web/app.py's
# _repo_docs_dir() walks up from the installed package looking for a docs/
# dir, and the site's own content (index.md, agents.md) links to pages that
# live there (/agent-onboarding, /agent-tokens, /skill-sources). Without
# this copy those pages 404 in the deployed Lambda while working in every
# dev install — the local link-crawl test can't see the difference, only a
# live crawl can (how this was found, post-0.7.0).
build-HttpHandlerFunction:
	pip install --no-cache-dir --target "$(ARTIFACTS_DIR)" .
	pip install --no-cache-dir --target "$(ARTIFACTS_DIR)" "league-of-agents>=0.16,<0.17"
	cp -r docs "$(ARTIFACTS_DIR)/docs"

build-CleanupFunction:
	pip install --no-cache-dir --target "$(ARTIFACTS_DIR)" .
