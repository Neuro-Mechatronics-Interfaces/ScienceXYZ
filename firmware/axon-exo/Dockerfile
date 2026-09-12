# Reuse the repository's existing C++ App builder/dependency ABI. No FPGA SDK.
ARG BASE_IMAGE=scifi2-hub-manager:latest-amd64
FROM ${BASE_IMAGE}
USER root
RUN apt-get update -qq && apt-get install -y scifi-peripheral-sdk=0.2.0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /work
