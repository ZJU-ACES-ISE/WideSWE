FROM ecosyncbench/base/getsentry-rust-py314-cmake:20260630

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libffi-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*
