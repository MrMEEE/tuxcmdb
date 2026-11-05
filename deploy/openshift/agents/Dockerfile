FROM registry.access.redhat.com/ubi10/ubi:latest AS version
RUN dnf install -y git && dnf clean all
WORKDIR /src
COPY .git/ .git/
COPY tools/openshift-agent-version.sh /tmp/openshift-agent-version.sh
ARG SOURCE_REPOSITORY_URL=https://github.com/MrMEEE/tuxcmdb.git
RUN git remote set-url origin "$SOURCE_REPOSITORY_URL" && \
    bash /tmp/openshift-agent-version.sh > /agent-version && \
    git rev-parse HEAD > /agent-commit

FROM registry.access.redhat.com/ubi8/ubi:latest AS el8
RUN dnf install -y ca-certificates python3 python3-pip python3-requests ruby ruby-devel rpm-build findutils && \
    gem install --no-document fpm -v 1.15.1 && dnf clean all
WORKDIR /src
COPY agent/ agent/
COPY packaging/scripts/ packaging/scripts/
COPY tools/build-agent-package.sh tools/build-agent-package.sh
COPY version.py .
COPY --from=version /agent-version /tmp/agent-version
RUN bash tools/build-agent-package.sh el8 "$(cat /tmp/agent-version)"

FROM registry.access.redhat.com/ubi9/ubi:latest AS el9
RUN dnf install -y ca-certificates python3 python3-pip ruby ruby-devel rpm-build findutils && \
    gem install --no-document fpm -v 1.15.1 && dnf clean all
WORKDIR /src
COPY agent/ agent/
COPY packaging/scripts/ packaging/scripts/
COPY tools/build-agent-package.sh tools/build-agent-package.sh
COPY version.py .
COPY --from=version /agent-version /tmp/agent-version
RUN bash tools/build-agent-package.sh el9 "$(cat /tmp/agent-version)"

FROM registry.access.redhat.com/ubi10/ubi:latest AS el10
RUN dnf install -y ca-certificates python3 python3-pip ruby ruby-devel rpm-build findutils && \
    gem install --no-document fpm -v 1.15.1 && dnf clean all
WORKDIR /src
COPY agent/ agent/
COPY packaging/scripts/ packaging/scripts/
COPY tools/build-agent-package.sh tools/build-agent-package.sh
COPY version.py .
COPY --from=version /agent-version /tmp/agent-version
RUN bash tools/build-agent-package.sh el10 "$(cat /tmp/agent-version)"

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
COPY --from=version /agent-version /tmp/agent-version
RUN bash tools/build-agent-package.sh debian "$(cat /tmp/agent-version)"

FROM registry.access.redhat.com/ubi10/ubi:latest
WORKDIR /opt/tuxcmdb-webui-agents
COPY --from=el8 /src/dist/el8/ ./
COPY --from=el9 /src/dist/el9/ ./
COPY --from=el10 /src/dist/el10/ ./
COPY --from=debian /src/dist/debian/ ./
COPY agent/windows/tuxcmdb-agent.ps1 ./tuxcmdb-agent.ps1
COPY --from=version /agent-version ./agent-version
COPY --from=version /agent-commit ./agent-commit
RUN version="$(cat agent-version)" && \
    cp tuxcmdb-agent.ps1 "tuxcmdb-agent-${version}.ps1" && \
    chmod 644 ./* && sha256sum tuxcmdb-agent* > SHA256SUMS
COPY deploy/openshift/agents/publish /usr/local/bin/publish-agents
COPY deploy/openshift/agents/run-updater /usr/local/bin/run-updater
RUN chmod 755 /usr/local/bin/publish-agents /usr/local/bin/run-updater && \
    mkdir -p /var/lib/tuxcmdb-agents && chmod 775 /var/lib/tuxcmdb-agents
USER 1001
CMD ["/bin/bash", "/usr/local/bin/run-updater"]
