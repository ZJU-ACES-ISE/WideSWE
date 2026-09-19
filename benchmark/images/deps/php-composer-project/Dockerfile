ARG ECOSYNC_BASE_IMAGE=ecosyncbench/base/php-composer:8.4
FROM ${ECOSYNC_BASE_IMAGE}

ARG ECOSYNC_TASK_ID
ARG ECOSYNC_REPO
ARG ECOSYNC_REPO_PATH
ARG ECOSYNC_BASE_COMMIT
ARG ECOSYNC_DEPENDENCY_FINGERPRINT
ARG ECOSYNC_COMPOSER_ROOT_VERSION=1.0.0
ARG ECOSYNC_COMPOSER_INSTALL_FLAGS="--no-interaction --no-progress --prefer-dist --no-scripts --ignore-platform-req=ext-ftp --ignore-platform-req=ext-sockets"
ARG ECOSYNC_COMPOSER_REQUIRE_DEV=""

ENV COMPOSER_ROOT_VERSION=${ECOSYNC_COMPOSER_ROOT_VERSION}

LABEL org.ecosyncbench.task_id="${ECOSYNC_TASK_ID}" \
      org.ecosyncbench.repo="${ECOSYNC_REPO}" \
      org.ecosyncbench.base_commit="${ECOSYNC_BASE_COMMIT}" \
      org.ecosyncbench.dependency_fingerprint="${ECOSYNC_DEPENDENCY_FINGERPRINT}"

WORKDIR /opt/ecosync/deps
COPY ${ECOSYNC_REPO_PATH}/composer.json ./composer.json
RUN composer config --global audit.block-insecure false \
    && if [ -n "$ECOSYNC_COMPOSER_REQUIRE_DEV" ]; then composer require --dev --no-update --no-interaction $ECOSYNC_COMPOSER_REQUIRE_DEV; fi \
    && composer install ${ECOSYNC_COMPOSER_INSTALL_FLAGS}
