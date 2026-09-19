ARG ECOSYNC_DOTNET_BASE_IMAGE=ecosyncbench/base/dotnet-sdk:8.0.100
FROM ${ECOSYNC_DOTNET_BASE_IMAGE} AS restore

ARG ECOSYNC_TASK_ID
ARG ECOSYNC_REPO=bitwarden/server
ARG ECOSYNC_BASE_COMMIT
ARG ECOSYNC_DEPENDENCY_FINGERPRINT
ARG ECOSYNC_RESTORE_PROJECT=test/Api.Test/Api.Test.csproj
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG ALL_PROXY
ARG http_proxy
ARG https_proxy
ARG all_proxy
ARG NO_PROXY
ARG no_proxy

ENV DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_NOLOGO=1 \
    DOTNET_ROLL_FORWARD=LatestMajor \
    NUGET_PACKAGES=/root/.nuget/packages

WORKDIR /src
COPY repos/bitwarden/server/ ./

RUN --mount=type=cache,id=ecosyncbench-nuget-bitwarden-server,target=/root/.nuget/packages \
    HTTP_PROXY="$HTTP_PROXY" HTTPS_PROXY="$HTTPS_PROXY" ALL_PROXY="$ALL_PROXY" \
    http_proxy="$http_proxy" https_proxy="$https_proxy" all_proxy="$all_proxy" \
    NO_PROXY="$NO_PROXY" no_proxy="$no_proxy" \
    dotnet restore "$ECOSYNC_RESTORE_PROJECT" \
    --disable-parallel -v minimal /nr:false \
    /p:RestoreUseStaticGraphEvaluation=true /p:NuGetAudit=false \
    && mkdir -p /opt/ecosync/nuget-packages \
    && cp -a /root/.nuget/packages/. /opt/ecosync/nuget-packages/

FROM ${ECOSYNC_DOTNET_BASE_IMAGE}

ARG ECOSYNC_TASK_ID
ARG ECOSYNC_REPO=bitwarden/server
ARG ECOSYNC_BASE_COMMIT
ARG ECOSYNC_DEPENDENCY_FINGERPRINT

LABEL org.ecosyncbench.layer=deps \
      org.ecosyncbench.ecosystem=bitwarden \
      org.ecosyncbench.repo=$ECOSYNC_REPO \
      org.ecosyncbench.base_commit=$ECOSYNC_BASE_COMMIT \
      org.ecosyncbench.dependency_fingerprint=$ECOSYNC_DEPENDENCY_FINGERPRINT \
      org.ecosyncbench.task=$ECOSYNC_TASK_ID

ENV DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_NOLOGO=1 \
    DOTNET_ROLL_FORWARD=LatestMajor \
    NUGET_PACKAGES=/root/.nuget/packages

COPY --from=restore /opt/ecosync/nuget-packages /opt/ecosync/nuget-packages
WORKDIR /workspace/repos/bitwarden/server
