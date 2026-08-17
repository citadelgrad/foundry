IMAGE := foundry-runner:local

.PHONY: install docker-build

install:
	uv tool install --reinstall .

docker-build:
	docker build -t $(IMAGE) .
