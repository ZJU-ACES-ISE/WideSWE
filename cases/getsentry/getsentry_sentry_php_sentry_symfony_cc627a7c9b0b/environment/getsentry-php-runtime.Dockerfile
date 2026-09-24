FROM ecosyncbench/base/php-composer:8.4

ENV FRANKENPHP_VERSION=v1.11.2 \
    ROADRUNNER_VERSION=v2025.1.7

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl tar; \
    case "$(uname -m)" in \
      x86_64) franken_asset="frankenphp-linux-x86_64"; rr_arch="amd64" ;; \
      aarch64|arm64) franken_asset="frankenphp-linux-aarch64"; rr_arch="arm64" ;; \
      *) echo "Unsupported architecture: $(uname -m)" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://github.com/php/frankenphp/releases/download/${FRANKENPHP_VERSION}/${franken_asset}" -o /usr/local/bin/frankenphp; \
    chmod 0755 /usr/local/bin/frankenphp; \
    rr_version_no_prefix="${ROADRUNNER_VERSION#v}"; \
    rr_asset="roadrunner-${rr_version_no_prefix}-linux-${rr_arch}.tar.gz"; \
    curl -fsSL "https://github.com/roadrunner-server/roadrunner/releases/download/${ROADRUNNER_VERSION}/${rr_asset}" -o /tmp/rr.tar.gz; \
    tar -xzf /tmp/rr.tar.gz -C /tmp; \
    install -m 0755 "/tmp/${rr_asset%.tar.gz}/rr" /usr/local/bin/rr; \
    rm -rf /tmp/rr.tar.gz "/tmp/${rr_asset%.tar.gz}"; \
    frankenphp version; \
    rr --version; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

COPY run_getsentry_php_profile_inside.sh /opt/ecosync/run-getsentry-php-profile.sh
RUN chmod 0755 /opt/ecosync/run-getsentry-php-profile.sh

WORKDIR /workspace
