# syntax=docker/dockerfile:1.7
FROM debian:12-slim AS base

ARG DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/opt/uv-python

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        openssh-client \
        qemu-system-arm \
        qemu-system-x86 \
        qemu-utils \
        cloud-image-utils \
        dnsmasq-base \
        iptables \
        tmux \
        xz-utils \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_UNMANAGED_INSTALL=/usr/local/bin sh \
    && /usr/local/bin/uv --version > /opt/uv-version

RUN /usr/local/bin/uv python install 3.12

WORKDIR /app

COPY pyproject.toml uv.lock /app/
RUN /usr/local/bin/uv sync --frozen --all-groups --no-install-project

COPY . /app/
RUN /usr/local/bin/uv sync --frozen --all-groups

ENV PATH="/app/.venv/bin:$PATH"

ENTRYPOINT ["/app/.venv/bin/arenabench"]
CMD ["--help"]
