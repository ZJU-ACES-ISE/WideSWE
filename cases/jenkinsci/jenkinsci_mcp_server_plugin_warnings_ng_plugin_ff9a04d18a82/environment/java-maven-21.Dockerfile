FROM ecosyncbench/base/elastic-java:21

LABEL org.ecosyncbench.layer=base \
      org.ecosyncbench.runtime=java-maven \
      org.ecosyncbench.version=21-ubuntu

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends maven ca-certificates git bash \
    && rm -rf /var/lib/apt/lists/*

ENV MAVEN_OPTS="-Dmaven.repo.local=/m2"

WORKDIR /workspace
