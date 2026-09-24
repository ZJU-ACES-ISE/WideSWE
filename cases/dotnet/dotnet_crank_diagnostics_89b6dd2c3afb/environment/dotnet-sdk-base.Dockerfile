ARG ECOSYNC_DOTNET_UPSTREAM_IMAGE=mcr.microsoft.com/dotnet/sdk:8.0
FROM ${ECOSYNC_DOTNET_UPSTREAM_IMAGE}

LABEL org.ecosyncbench.layer=base \
      org.ecosyncbench.runtime=dotnet-sdk

ENV DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_NOLOGO=1 \
    DOTNET_ROLL_FORWARD=LatestMajor \
    NUGET_PACKAGES=/root/.nuget/packages

WORKDIR /workspace
