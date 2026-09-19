ARG ECOSYNC_MAVEN_UPSTREAM_IMAGE=maven:3.9.11-eclipse-temurin-17
FROM ${ECOSYNC_MAVEN_UPSTREAM_IMAGE}

LABEL org.ecosyncbench.layer=base \
      org.ecosyncbench.runtime=java-maven \
      org.ecosyncbench.version=17-bookworm

ENV MAVEN_OPTS="-Dmaven.repo.local=/opt/ecosync/m2"

WORKDIR /workspace
