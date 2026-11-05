FROM registry.access.redhat.com/ubi8/ubi:latest AS el8
RUN dnf install -y ca-certificates python3 python3-pip python3-requests ruby ruby-devel rpm-build findutils && \
    gem install --no-document fpm -v 1.15.1 && dnf clean all
WORKDIR /src
COPY agent/ agent/
COPY packaging/scripts/ packaging/scripts/
COPY tools/build-agent-package.sh tools/build-agent-package.sh
COPY version.py .
RUN bash tools/build-agent-package.sh el8

FROM registry.access.redhat.com/ubi9/ubi:latest AS el9
RUN dnf install -y ca-certificates python3 python3-pip ruby ruby-devel rpm-build findutils && \
    gem install --no-document fpm -v 1.15.1 && dnf clean all
WORKDIR /src
COPY agent/ agent/
COPY packaging/scripts/ packaging/scripts/
COPY tools/build-agent-package.sh tools/build-agent-package.sh
COPY version.py .
RUN bash tools/build-agent-package.sh el9

FROM registry.access.redhat.com/ubi10/ubi:latest AS el10
RUN dnf install -y ca-certificates python3 python3-pip ruby ruby-devel rpm-build findutils && \
    gem install --no-document fpm -v 1.15.1 && dnf clean all
WORKDIR /src
COPY agent/ agent/
COPY packaging/scripts/ packaging/scripts/
COPY tools/build-agent-package.sh tools/build-agent-package.sh
COPY version.py .
RUN bash tools/build-agent-package.sh el10

FROM debian:bookworm-slim AS debian
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates python3 python3-pip ruby ruby-dev binutils xz-utils && \
    gem install --no-document fpm -v 1.15.1 && rm -rf /var/lib/apt/lists/*
ENV PIP_BREAK_SYSTEM_PACKAGES=1
WORKDIR /src
COPY agent/ agent/
COPY packaging/scripts/ packaging/scripts/
COPY tools/build-agent-package.sh tools/build-agent-package.sh
COPY version.py .
RUN bash tools/build-agent-package.sh debian

FROM registry.access.redhat.com/ubi10/ubi:latest
WORKDIR /opt/tuxcmdb-webui-agents
COPY --from=el8 /src/dist/el8/ ./
COPY --from=el9 /src/dist/el9/ ./
COPY --from=el10 /src/dist/el10/ ./
COPY --from=debian /src/dist/debian/ ./
COPY agent/windows/tuxcmdb-agent.ps1 ./tuxcmdb-agent.ps1
COPY version.py /tmp/version.py
RUN version="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' /tmp/version.py)" && \
    test -n "$version" && \
    cp tuxcmdb-agent.ps1 "tuxcmdb-agent-${version}.ps1" && \
    chmod 644 ./* && rm /tmp/version.py
USER 1001
