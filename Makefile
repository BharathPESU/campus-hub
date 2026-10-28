# Campus Event & Hackathon Hub Makefile
# Entry points: check test build plan apply deploy-frontend smoke destroy
# See docs/plan.md section 12.

ENV ?= dev
TF  := terraform -chdir=infra/envs/$(ENV)

.PHONY: check test build plan apply deploy-frontend smoke destroy

check:            ## lint + validate everything
	pre-commit run --all-files

test:             ## unit tests
	cd backend && (test -f .venv/bin/pytest && .venv/bin/pytest -q || pytest -q)

build:            ## build Lambda package and frontend
	./scripts/build_backend.sh && ./scripts/build_frontend.sh

plan: build       ## terraform plan
	$(TF) init && $(TF) plan -out=tfplan

apply:            ## terraform apply
	$(TF) apply tfplan

deploy-frontend:  ## build and deploy frontend
	./scripts/gen_frontend_config.sh $(ENV) && ./scripts/deploy_frontend.sh $(ENV)

smoke:            ## smoke test deployment
	./scripts/smoke_test.sh $(ENV)

destroy:          ## destroy environment resources
	./scripts/destroy.sh $(ENV)
