IMAGE=la-commu-discord-bot:latest
REGISTRY=rg.fr-par.scw.cloud/la-commu-discord-bot
REMOTE_IMAGE=$(REGISTRY)/$(IMAGE)
CONTAINER_ID=2fed6267-5ce1-4331-80f8-241f411a782e
PYTHON?=python
export PYTHONPATH:=$(PWD)

.PHONY: help build push redeploy deploy lint test clean

help:
	@echo "Available targets:"
	@echo "  make build       # Build the local Docker image"
	@echo "  make push        # Push the image to Scaleway"
	@echo "  make redeploy    # Redeploy the container"
	@echo "  make deploy      # Push + redeploy container"
	@echo "  make lint        # Syntax check via compileall"
	@echo "  make test        # Install test deps & run pytest"
	@echo "  make clean       # Remove build caches"

build:
	docker build -t $(IMAGE) .

push: build
	docker tag $(IMAGE) $(REMOTE_IMAGE)
	docker push $(REMOTE_IMAGE)

redeploy:
	scw container container deploy $(CONTAINER_ID)

deploy: push redeploy

lint:
	$(PYTHON) -m compileall .

test:
	$(PYTHON) -m pip install -r requirements-test.txt
	$(PYTHON) -m pytest

clean:
	rm -rf __pycache__ */__pycache__
