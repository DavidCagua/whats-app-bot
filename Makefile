# Project tasks. Use `make help` to list available targets.
.PHONY: help test check-drift check

help:
	@echo "Available targets:"
	@echo "  make test         Run unit + integration tests."
	@echo "  make check-drift  Detect SQLAlchemy model vs DB schema drift."
	@echo "                    Mirrors the CI 'alembic check' job. Run before"
	@echo "                    pushing migration / model changes."
	@echo "  make check        Run all pre-push checks (tests + drift)."

test:
	python -m pytest tests/unit tests/integration -q

# Mirrors the CI workflow's drift gate (.github/workflows/ci.yml: 'Drift
# check (models vs DB schema)'). Fails when SQLAlchemy models in
# app/database/models.py don't match what `alembic upgrade head`
# produces. Catches "forgot SET NOT NULL", "wrong column type",
# "model field has no migration" — the class of bug that broke this
# branch's first push.
#
# Requires DATABASE_URL pointing at a DB the alembic migrations can
# upgrade against. Fast (~3-5s) — safe to run before every push.
check-drift:
	alembic check

check: test check-drift
