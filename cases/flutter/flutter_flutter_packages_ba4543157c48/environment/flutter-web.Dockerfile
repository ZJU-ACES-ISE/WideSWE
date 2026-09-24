ARG ECOSYNC_FLUTTER_BASE=debian:bookworm
FROM ${ECOSYNC_FLUTTER_BASE}

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    chromium \
    chromium-driver \
    coreutils \
    curl \
    findutils \
    git \
    procps \
    python3 \
    unzip \
    xz-utils \
    zip \
  && rm -rf /var/lib/apt/lists/*

ENV CHROME_EXECUTABLE=/usr/bin/chromium
