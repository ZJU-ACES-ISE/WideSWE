# syntax=docker/dockerfile:1.6
ARG ECOSYNC_BASE_IMAGE=ecosyncbench/base/java-maven:21-bookworm
FROM ${ECOSYNC_BASE_IMAGE} AS warm

ARG ECOSYNC_TASK_ID
ARG ECOSYNC_REPO
ARG ECOSYNC_REPO_PATH
ARG ECOSYNC_BASE_COMMIT
ARG ECOSYNC_DEPENDENCY_FINGERPRINT
ARG ECOSYNC_MAVEN_GOALS="dependency:go-offline"
ARG ECOSYNC_MAVEN_WARMUP_TEST_SELECTOR=""
ARG ECOSYNC_MAVEN_MIRROR_URL=""
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG ALL_PROXY
ARG http_proxy
ARG https_proxy
ARG all_proxy
ARG NO_PROXY
ARG no_proxy

WORKDIR /tmp/ecosync-project
RUN --mount=type=bind,source=${ECOSYNC_REPO_PATH},target=/src,readonly \
    --mount=type=cache,target=/root/.m2 \
    HTTP_PROXY="$HTTP_PROXY" HTTPS_PROXY="$HTTPS_PROXY" ALL_PROXY="$ALL_PROXY" \
    http_proxy="$http_proxy" https_proxy="$https_proxy" all_proxy="$all_proxy" \
    NO_PROXY="$NO_PROXY" no_proxy="$no_proxy" \
    bash -c 'set -euo pipefail; cp -a /src/. /tmp/ecosync-project/; mkdir -p /opt/ecosync/m2 /opt/ecosync/maven /root/.m2; if [[ -n "${ECOSYNC_MAVEN_MIRROR_URL:-}" ]]; then printf "%s\n" "<settings>" "  <mirrors>" "    <mirror>" "      <id>ecosyncbench-mirror</id>" "      <mirrorOf>*</mirrorOf>" "      <url>${ECOSYNC_MAVEN_MIRROR_URL}</url>" "    </mirror>" "  </mirrors>" "</settings>" > /opt/ecosync/maven/settings.xml; else printf "%s\n" "<settings/>" > /opt/ecosync/maven/settings.xml; fi; cp /opt/ecosync/maven/settings.xml /root/.m2/settings.xml; if [[ -f .git ]]; then gitdir="$(sed -n "s/^gitdir: //p" .git | head -1)"; if [[ -n "$gitdir" && ! -d "$gitdir" ]]; then rm -f .git; fi; fi; if [[ ! -d .git ]]; then git init -q; git config user.email ecosyncbench@example.invalid; git config user.name WIDESWE; git commit --allow-empty -qm ecosyncbench-maven-metadata; fi; if [[ -n "${ECOSYNC_MAVEN_GOALS:-}" ]]; then mvn -B -q -DskipTests ${ECOSYNC_MAVEN_GOALS}; fi; if [[ -n "$ECOSYNC_MAVEN_WARMUP_TEST_SELECTOR" ]]; then mvn -B -Dgit.commit.id.skip=true -Dformatter.skip=true -DskipITs=true -DskipIntegrationTests=true -DskipScenarioTests=true -Dtest="$ECOSYNC_MAVEN_WARMUP_TEST_SELECTOR" test; fi'

FROM ${ECOSYNC_BASE_IMAGE}

ARG ECOSYNC_TASK_ID
ARG ECOSYNC_REPO
ARG ECOSYNC_BASE_COMMIT
ARG ECOSYNC_DEPENDENCY_FINGERPRINT

LABEL org.ecosyncbench.layer=deps \
      org.ecosyncbench.task_id="${ECOSYNC_TASK_ID}" \
      org.ecosyncbench.repo="${ECOSYNC_REPO}" \
      org.ecosyncbench.base_commit="${ECOSYNC_BASE_COMMIT}" \
      org.ecosyncbench.dependency_fingerprint="${ECOSYNC_DEPENDENCY_FINGERPRINT}"

COPY --from=warm /opt/ecosync/m2 /opt/ecosync/m2
RUN mkdir -p /root/.m2
COPY --from=warm /opt/ecosync/maven/settings.xml /root/.m2/settings.xml

ENV MAVEN_OPTS="-Dmaven.repo.local=/opt/ecosync/m2"
WORKDIR /workspace
