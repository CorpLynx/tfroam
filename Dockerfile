# syntax=docker/dockerfile:1
#
# Same contract as the upstream Go image: `docker run ... im2nguyen/rover <flags>`.
# - /bin/terraform exists (the default for -tfPath inside the container)
# - /src is the working directory, mount your configuration there
# - Chromium is installed for -genImage
# The frontend is prebuilt and bundled in the package, so there is no Node stage.
# 1.5.5 matches upstream Rover's Dockerfile and is the last MPL-2.0 Terraform release (later ones are BUSL-1.1).
ARG TF_VERSION=1.5.5
# Terraform is a static Go binary: take it from the official image.
# Tag-based on purpose: the tag comes from ${TF_VERSION}, so a fixed digest would defeat the build arg.
FROM hashicorp/terraform:${TF_VERSION} AS terraform
# Build the wheel
# The python base is pinned to its multi-arch index digest; Dependabot (docker ecosystem) bumps it.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS build
WORKDIR /build
RUN pip install --no-cache-dir build
COPY pyproject.toml README.md LICENSE ./
COPY tfroam ./tfroam
RUN python -m build --wheel --outdir /dist
# Release image
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS release
# chromium: -genImage. git/openssh: Terraform modules with git sources (the official
# Terraform image ships them too).
RUN apt-get update \
 && apt-get install -y --no-install-recommends chromium git openssh-client ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=terraform /bin/terraform /bin/terraform
COPY --from=build /dist/*.whl /tmp/
RUN pip install --no-cache-dir "$(ls /tmp/tfroam-*.whl)[image]" && rm -f /tmp/*.whl
# Use the system Chromium; Playwright's own browser download is not needed.
ENV ROVER_BROWSER_PATH=/usr/bin/chromium
WORKDIR /src
# Runs as root, as upstream: the bind-mounted /src must stay writable for rover.zip, rover.svg and .terraform.
# Bind all interfaces so `-p 9000:9000` works (the native default is 127.0.0.1:9000). User flags are appended,
# and a later -ipPort overrides this one.
ENTRYPOINT ["rover", "-ipPort", "0.0.0.0:9000"]
