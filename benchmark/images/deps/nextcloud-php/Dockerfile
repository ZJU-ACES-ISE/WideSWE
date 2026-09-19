FROM ecosyncbench/base/php-composer:8.4

LABEL org.ecosyncbench.layer=deps \
      org.ecosyncbench.ecosystem=nextcloud \
      org.ecosyncbench.runtime=php \
      org.ecosyncbench.version=8.4

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libfreetype6-dev \
        libjpeg62-turbo-dev \
        libpng-dev \
        libwebp-dev \
        libavif-dev \
        libbz2-dev \
    && docker-php-ext-configure gd --with-freetype --with-jpeg --with-webp --with-avif \
    && docker-php-ext-install -j"$(nproc)" bz2 gd \
    && pecl install apcu \
    && docker-php-ext-enable apcu \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
