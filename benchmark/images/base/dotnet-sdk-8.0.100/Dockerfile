FROM mcr.microsoft.com/dotnet/sdk:8.0.100

LABEL org.ecosyncbench.layer=base \
      org.ecosyncbench.runtime=dotnet-sdk \
      org.ecosyncbench.version=8.0.100

ENV DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_NOLOGO=1 \
    DOTNET_ROLL_FORWARD=LatestMajor \
    NUGET_PACKAGES=/root/.nuget/packages

WORKDIR /workspace
