FROM ecosyncbench/base/dotnet-sdk:10.0

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        cmake \
        git \
        make \
        openjdk-17-jdk-headless \
    && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
ENV JAVA_HOME_17_X64=/usr/lib/jvm/java-17-openjdk-amd64
ENV JAVA_HOME_11_X64=/usr/lib/jvm/java-17-openjdk-amd64
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1
ENV DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1

RUN git clone https://github.com/xamarin/xamarin-android-tools.git \
        /opt/ecosync/xamarin-android-tools-091e3a66c0cfcb4fcaaaaa7bc9c5fa85947eecb6 \
    && git -C /opt/ecosync/xamarin-android-tools-091e3a66c0cfcb4fcaaaaa7bc9c5fa85947eecb6 \
        checkout 091e3a66c0cfcb4fcaaaaa7bc9c5fa85947eecb6 \
    && chmod -R a+rX /opt/ecosync/xamarin-android-tools-091e3a66c0cfcb4fcaaaaa7bc9c5fa85947eecb6
