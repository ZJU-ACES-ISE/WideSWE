FROM python:3.12-bookworm

ARG GODOT_VERSION=4.5.1-stable
ARG GODOT_ARCHIVE=Godot_v4.5.1-stable_linux.x86_64.zip
ARG GODOT_BIN=Godot_v4.5.1-stable_linux.x86_64

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    clang \
    cmake \
    curl \
    g++ \
    gcc \
    git \
    libasound2 \
    libfontconfig1 \
    libgl1 \
    libglu1-mesa \
    libxi6 \
    libxinerama1 \
    libxrandr2 \
    make \
    pkg-config \
    unzip \
    zlib1g-dev \
  && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir scons

RUN mkdir -p /opt/godot \
  && curl -L -o /tmp/godot.zip "https://github.com/godotengine/godot-builds/releases/download/${GODOT_VERSION}/${GODOT_ARCHIVE}" \
  && unzip /tmp/godot.zip -d /opt/godot \
  && rm /tmp/godot.zip \
  && chmod +x "/opt/godot/${GODOT_BIN}" \
  && ln -s "/opt/godot/${GODOT_BIN}" /usr/local/bin/godot \
  && godot --version

COPY godot_to_junit.py /opt/ecosync/godot_to_junit.py
RUN chmod 0644 /opt/ecosync/godot_to_junit.py
