FROM ecosyncbench/base/php-composer:8.4

ENV COMPOSER_ALLOW_SUPERUSER=1 \
    COMPOSER_CACHE_DIR=/ecosync-cache/composer/cache \
    COMPOSER_HOME=/ecosync-cache/composer/home

RUN apt-get update \
    && apt-get install -y --no-install-recommends git unzip libzip-dev libgmp-dev libssl-dev pkg-config \
    && docker-php-ext-install -j"$(nproc)" zip gmp \
    && pecl install mongodb \
    && docker-php-ext-enable mongodb \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY environment/run_laravel_profile_inside.sh /opt/ecosync/run-laravel-profile.sh
RUN chmod 755 /opt/ecosync/run-laravel-profile.sh

WORKDIR /workspace
