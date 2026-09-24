ARG ECOSYNC_BASE_IMAGE=ecosyncbench/base/php-composer:8.4
FROM ${ECOSYNC_BASE_IMAGE}

ARG ECOSYNC_TASK_ID
ARG ECOSYNC_REPO
ARG ECOSYNC_REPO_PATH
ARG ECOSYNC_BASE_COMMIT
ARG ECOSYNC_DEPENDENCY_FINGERPRINT
ARG ECOSYNC_COMPOSER_ROOT_VERSION=8.1.x-dev
ARG ECOSYNC_COMPOSER_INSTALL_FLAGS="--no-interaction --no-progress --prefer-dist --no-scripts --ignore-platform-req=ext-ftp --ignore-platform-req=ext-sockets --ignore-platform-req=ext-xsl"

ENV COMPOSER_ROOT_VERSION=${ECOSYNC_COMPOSER_ROOT_VERSION}

LABEL org.ecosyncbench.task_id="${ECOSYNC_TASK_ID}" \
      org.ecosyncbench.repo="${ECOSYNC_REPO}" \
      org.ecosyncbench.base_commit="${ECOSYNC_BASE_COMMIT}" \
      org.ecosyncbench.dependency_fingerprint="${ECOSYNC_DEPENDENCY_FINGERPRINT}"

WORKDIR /opt/ecosync/deps
COPY ${ECOSYNC_REPO_PATH}/composer.json ./composer.json
COPY ${ECOSYNC_REPO_PATH}/src/Symfony ./src/Symfony
RUN composer config --global audit.block-insecure false \
    && php -r '$j = json_decode(file_get_contents("composer.json"), true); $j["repositories"][1]["options"]["versions"]["symfony/runtime"] = getenv("COMPOSER_ROOT_VERSION"); unset($j["require-dev"]["symfony/mercure-bundle"]); file_put_contents("composer.json", json_encode($j, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES).PHP_EOL);' \
    && composer install ${ECOSYNC_COMPOSER_INSTALL_FLAGS}
