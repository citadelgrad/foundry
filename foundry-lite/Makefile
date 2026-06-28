IMAGE := ghcr.io/citadelgrad/foundry-runner:latest

.PHONY: install docker-build docker-push

install:
	uv tool install --reinstall .

docker-build:
	docker build -t $(IMAGE) .

docker-push: docker-build
	gh auth token | docker login ghcr.io -u citadelgrad --password-stdin
	docker push $(IMAGE)
