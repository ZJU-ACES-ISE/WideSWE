ARG ECOSYNC_PYTHON_BASE=ecosyncbench/base/python:3.12-uv
FROM ${ECOSYNC_PYTHON_BASE}

LABEL org.ecosyncbench.layer=deps \
      org.ecosyncbench.ecosystem=pytorch \
      org.ecosyncbench.runtime=python \
      org.ecosyncbench.version=3.12

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG ALL_PROXY
ARG http_proxy
ARG https_proxy
ARG all_proxy
ARG NO_PROXY
ARG no_proxy

RUN apt-get update \
    && apt-get install -y --no-install-recommends cmake ninja-build \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir \
      --index-url https://download.pytorch.org/whl/cpu \
      torch \
    && python -m pip install --no-cache-dir \
      cloudpickle \
      cmake \
      importlib_metadata \
      numpy \
      packaging \
      pybind11[global] \
      pytest \
      pytest-benchmark \
      pytest-instafail \
      pytest-rerunfailures \
      pyyaml \
      pyvers \
      scipy

RUN python -m pip install --no-cache-dir orjson

WORKDIR /workspace
