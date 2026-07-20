BINARY     := kubectl-lambda_g
VERSION    ?= $(shell git describe --tags --always --dirty)
DIST       := dist
PLUGIN_DIR := kubectl-plugin

PLATFORMS := \
	linux/amd64 \
	linux/arm64 \
	darwin/amd64 \
	darwin/arm64 \
	windows/amd64

.PHONY: all build release clean

all: build

build:
	cd $(PLUGIN_DIR) && go build -ldflags="-s -w -X main.version=$(VERSION)" -o ../$(BINARY) ./cmd/...

release: clean
	@mkdir -p $(DIST)
	@for platform in $(PLATFORMS); do \
		os=$$(echo $$platform | cut -d/ -f1); \
		arch=$$(echo $$platform | cut -d/ -f2); \
		bin_name=$(BINARY)-$$os-$$arch; \
		if [ "$$os" = "windows" ]; then bin_name=$$bin_name.exe; fi; \
		echo "Building $$os/$$arch -> $$bin_name"; \
		(cd $(PLUGIN_DIR) && GOOS=$$os GOARCH=$$arch go build \
			-ldflags="-s -w -X main.version=$(VERSION)" \
			-o ../$(DIST)/$$bin_name \
			./cmd/...) || exit 1; \
		if [ "$$os" = "windows" ]; then \
			zip -j $(DIST)/$(BINARY)-$$os-$$arch.zip $(DIST)/$$bin_name LICENSE; \
		else \
			tar -czf $(DIST)/$(BINARY)-$$os-$$arch.tar.gz -C $(DIST) $$bin_name -C .. LICENSE; \
		fi; \
		rm -f $(DIST)/$$bin_name; \
	done
	@echo "Generating checksums..."
	@cd $(DIST) && shasum -a 256 *.tar.gz *.zip > checksums.txt && cat checksums.txt

clean:
	rm -rf $(DIST) $(BINARY)
