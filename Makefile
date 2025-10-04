IMAGE=la-commu-discord-bot:latest
REGISTRY=rg.fr-par.scw.cloud/la-commu-discord-bot
REMOTE_IMAGE=$(REGISTRY)/$(IMAGE)
CONTAINER_ID=2fed6267-5ce1-4331-80f8-241f411a782e
GHCR_REGISTRY?=ghcr.io
GHCR_NAMESPACE?=
SYSTEMD_UNIT?=la-commu-discord-bot
SYSTEMCTL?=sudo systemctl
JOURNALCTL?=sudo journalctl
PYTHON?=python
export PYTHONPATH:=$(PWD)

.PHONY: help build push redeploy deploy lint test clean systemd-restart systemd-tail tail ghcr-push

help:
	@echo "Available targets:"
	@echo "  make build       # Build the local Docker image"
	@echo "  make push        # Push the image to Scaleway"
	@echo "  make redeploy    # Redeploy the container"
	@echo "  make deploy      # Push + redeploy container"
	@echo "  make lint        # Syntax check via compileall"
	@echo "  make test        # Install test deps & run pytest"
	@echo "  make clean       # Remove build caches"
	@echo "  make systemd-restart  # Restart the systemd service (uses SYSTEMCTL/SYSTEMD_UNIT)"
	@echo "  make systemd-tail     # Follow journalctl logs for the service"
	@echo "  make tail        # Tail the log file (requires --log-file in ExecStart)"
	@echo "  make ghcr-push   # Push the image to GitHub Container Registry (set GHCR_NAMESPACE)"

build:
	docker build -t $(IMAGE) .

push: build
	docker tag $(IMAGE) $(REMOTE_IMAGE)
	docker push $(REMOTE_IMAGE)

redeploy:
	scw container container deploy $(CONTAINER_ID)

deploy: push redeploy

ghcr-push: build
	@if [ -z "$(GHCR_NAMESPACE)" ]; then \
		echo "GHCR_NAMESPACE is empty. Run 'make GHCR_NAMESPACE=your-gh-username ghcr-push'."; \
		exit 1; \
	fi
	docker tag $(IMAGE) $(GHCR_REGISTRY)/$(GHCR_NAMESPACE)/$(IMAGE)
	docker push $(GHCR_REGISTRY)/$(GHCR_NAMESPACE)/$(IMAGE)

lint:
	$(PYTHON) -m compileall .

test tests:
	$(PYTHON) -m pip install -r requirements-test.txt
	$(PYTHON) -m pytest

clean:
	rm -rf __pycache__ */__pycache__

systemd-restart:
	$(SYSTEMCTL) restart $(SYSTEMD_UNIT)

systemd-tail:
	$(JOURNALCTL) -u $(SYSTEMD_UNIT) -f

tail:
	tail -f $(SYSTEMD_UNIT).log
