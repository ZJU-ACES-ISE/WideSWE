ARG PHP_BASE_IMAGE=php:8.4.23-cli-bookworm
FROM ${PHP_BASE_IMAGE}

ENV COMPOSER_ALLOW_SUPERUSER=1 \
    COMPOSER_HOME=/tmp/composer \
    COMPOSER_CACHE_DIR=/opt/ecosync/composer-cache

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        unzip \
        zip \
        libgmp-dev \
        libicu-dev \
        libonig-dev \
        libxml2-dev \
        libzip-dev \
    && docker-php-ext-install -j"$(nproc)" ftp gmp intl mbstring pcntl sockets zip \
    && php -r "copy('https://getcomposer.org/installer', '/tmp/composer-setup.php');" \
    && php /tmp/composer-setup.php --install-dir=/usr/local/bin --filename=composer \
    && rm /tmp/composer-setup.php \
    && mkdir -p /opt/ecosync/composer-cache /tmp/composer \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
