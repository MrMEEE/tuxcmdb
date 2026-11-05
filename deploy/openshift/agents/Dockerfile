FROM registry.access.redhat.com/ubi10/ubi:latest
RUN dnf install -y git python3 && dnf clean all && \
    mkdir -p /var/lib/tuxcmdb-agents && chmod 775 /var/lib/tuxcmdb-agents
COPY deploy/openshift/agents/controller.py /usr/local/lib/agent-controller.py
COPY deploy/openshift/agents/publish /usr/local/bin/publish-agents
COPY deploy/openshift/agents/build-job /usr/local/bin/agent-build-job
COPY tools/openshift-agent-version.sh /usr/local/bin/agent-version
USER 1001
EXPOSE 8080
CMD ["/usr/bin/python3", "-u", "/usr/local/lib/agent-controller.py"]
