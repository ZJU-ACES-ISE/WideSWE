FROM ecosyncbench/base/php-composer:8.4.23
RUN apt-get update \
 && docker-php-ext-install -j"$(nproc)" bcmath \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
