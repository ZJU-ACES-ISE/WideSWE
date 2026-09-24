FROM rust:1.91-bookworm

ENV DEBIAN_FRONTEND=noninteractive
ENV RUSTUP_HOME=/usr/local/rustup
ENV CARGO_HOME=/usr/local/cargo
ENV PATH=/usr/local/cargo/bin:/usr/lib/dart/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

RUN apt-get update && apt-get install -y --no-install-recommends \
    apt-transport-https \
    ca-certificates \
    curl \
    g++ \
    gcc \
    git \
    gnupg \
    make \
    pkg-config \
    python3 \
    unzip \
    wget \
    xz-utils \
    && wget -qO- https://dl-ssl.google.com/linux/linux_signing_key.pub \
      | gpg --dearmor > /usr/share/keyrings/dart.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/dart.gpg] https://storage.googleapis.com/download.dartlang.org/linux/debian stable main" \
      > /etc/apt/sources.list.d/dart_stable.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends dart \
    && rm -rf /var/lib/apt/lists/*

RUN rustup toolchain install 1.93.1 --profile minimal \
    && rustup default 1.93.1 \
    && chmod -R a+rwx /usr/local/cargo /usr/local/rustup
