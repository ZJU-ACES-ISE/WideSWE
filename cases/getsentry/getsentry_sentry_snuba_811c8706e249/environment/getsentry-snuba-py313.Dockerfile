FROM python:3.13-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    cmake \
    curl \
    g++ \
    gcc \
    git \
    liblz4-dev \
    libpcre2-dev \
    libssl-dev \
    make \
    pkg-config \
    protobuf-compiler \
    rustc \
    cargo \
    zlib1g-dev \
  && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir --upgrade pip uv setuptools wheel && \
    python -m pip install --no-cache-dir --index-url https://pypi.devinfra.sentry.io/simple \
    pytest \
    pytest-cov \
    sentry-protos \
    protobuf \
    pyyaml \
    packaging \
    click \
    structlog \
    python-rapidjson \
    jsonschema \
    simplejson \
    parsimonious \
    sentry-sdk \
    datadog \
    urllib3 \
    blinker \
    redis \
    python-dateutil \
    flask \
    werkzeug \
    structlog-sentry \
    clickhouse-connect \
    clickhouse-driver \
    confluent-kafka \
    fastjsonschema \
    freezegun \
    google-api-core \
    google-api-python-client \
    google-cloud-storage \
    googleapis-common-protos \
    granian \
    maturin==1.4.0 \
    proto-plus \
    'pyjwt[crypto]' \
    sentry-arroyo \
    sentry-kafka-schemas \
    sentry-redis-tools \
    sentry-relay \
    sentry-usage-accountant \
    snuba-sdk \
    sql-metadata \
    sqlparse \
    time-machine

RUN python -m pip install --no-cache-dir --index-url https://pypi.devinfra.sentry.io/simple \
    sentry-conventions==0.3.0
