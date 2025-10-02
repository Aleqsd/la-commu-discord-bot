IMAGE=la-commu-discord-bot:latest
REGISTRY=rg.fr-par.scw.cloud/la-commu-discord-bot
REMOTE_IMAGE=$(REGISTRY)/$(IMAGE)
CONTAINER_ID=2fed6267-5ce1-4331-80f8-241f411a782e

.PHONY: help build tag push deploy redeploy lint test clean

help:
	@echo "Available targets:"
	@echo "  make build       # Build the local Docker image"
	@echo "  make tag         # Tag the image for Scaleway registry"
	@echo "  make push        # Push the image to Scaleway"
	@echo "  make deploy      # Push + redeploy container"
	@echo "  make test        # Run pytest suite"
	@echo "  make lint        # Syntax check via compileall"
	@echo "  make clean       # Remove __pycache__" 

build:
	docker build -t $(IMAGE) .
	touch .last-build

post-build: .last-build
	docker tag $(IMAGE) $(REMOTE_IMAGE)

push: build post-build
	docker push $(REMOTE_IMAGE)

redeploy:
	scw container container deploy -w $(CONTAINER_ID) image=$(REMOTE_IMAGE)

deploy: push redeploy
	scw container container logs $(CONTAINER_ID) --since 5m --follow || true

lint:
	python3 -m compileall .

install-test-deps:
	python3 -m pip install -r requirements-test.txt
	touch .last-test-install

.test-env: .last-test-install
	touch .test-env

pytest: install-test-deps
	python3 -m pytest

clean:
	rm -rf __pycache__ */__pycache__
	rm -f .last-build .last-test-install .test-env
