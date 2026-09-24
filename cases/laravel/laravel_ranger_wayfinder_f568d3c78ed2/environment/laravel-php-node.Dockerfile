FROM ecosyncbench/base/php-composer:8.4

ENV COMPOSER_ALLOW_SUPERUSER=1 \
    COMPOSER_CACHE_DIR=/ecosync-cache/composer/cache \
    COMPOSER_HOME=/ecosync-cache/composer/home \
    npm_config_cache=/ecosync-cache/npm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && node --version \
    && npm --version \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY environment/run_laravel_profile_inside.sh /opt/ecosync/run-laravel-profile.sh
RUN chmod +x /opt/ecosync/run-laravel-profile.sh

WORKDIR /workspace
