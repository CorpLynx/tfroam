# syntax=docker/dockerfile:1
#
# Same contract as the upstream Go image: `docker run ... im2nguyen/rover <flags>`.
# - /bin/terraform exists (the default for -tfPath inside the container)
# - /src is the working directory, mount your configuration there
# - Chromium is installed for -genImage
# The frontend is prebuilt and bundled in the package, so there is no Node stage.

ARG TF_VERSION=1.5.5
ARG PYTHON_VERSION=3.12

# Terraform is a static Go binary: take it from the official image.
FROM hashicorp/terraform:${TF_VERSION} AS terraform

# Build the wheel
FROM python:${PYTHON_VERSION}-slim AS build
WORKDIR /build
RUN pip install --no-cache-dir build
COPY pyproject.toml README.md LICENSE ./
COPY rover ./rover
RUN python -m build --wheel --outdir /dist

# Release image
FROM python:${PYTHON_VERSION}-slim AS release

# chromium: -genImage. git/openssh: Terraform modules with git sources (the official
# Terraform image ships them too).
RUN apt-get update \
 && apt-get install -y --no-install-recommends chromium git openssh-client ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=terraform /bin/terraform /bin/terraform

COPY --from=build /dist/*.whl /tmp/
RUN pip install --no-cache-dir "$(ls /tmp/rover-*.whl)[image]" && rm -f /tmp/*.whl

# Use the system Chromium; Playwright's own browser download is not needed.
ENV ROVER_BROWSER_PATH=/usr/bin/chromium

WORKDIR /src
ENTRYPOINT ["rover"]
