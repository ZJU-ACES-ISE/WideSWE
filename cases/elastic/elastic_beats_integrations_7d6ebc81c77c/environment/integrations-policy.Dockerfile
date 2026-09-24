FROM ecosyncbench/base/go:1.26-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      docker.io \
      docker-compose \
      git \
      make \
    && rm -rf /var/lib/apt/lists/*

RUN printf '%s\n' \
      '#!/usr/bin/env bash' \
      'if [[ "${1:-}" == "compose" ]]; then' \
      '  shift' \
      '  exec docker-compose "$@"' \
      'fi' \
      'exec /usr/bin/docker "$@"' \
    > /usr/local/bin/docker \
    && chmod +x /usr/local/bin/docker

ENV PATH=/usr/local/go/bin:/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

WORKDIR /workspace
