FROM nousresearch/hermes-agent:latest

USER root

# Prepare Hermes' writable state directory without using the upstream s6 PID-1 bootstrap.
RUN install -d -o hermes -g hermes -m 0755 /opt/data \
 && cp /opt/hermes/cli-config.yaml.example /opt/data/config.yaml \
 && cp /opt/hermes/docker/SOUL.md /opt/data/SOUL.md \
 && touch /opt/data/.env \
 && chown -R hermes:hermes /opt/data /opt/hermes/ui-tui /opt/hermes/node_modules \
 && chmod 600 /opt/data/.env

ENV HERMES_HOME=/opt/data
ENV HOME=/opt/data

USER hermes
WORKDIR /opt/data

ENTRYPOINT ["/opt/hermes/.venv/bin/hermes"]
CMD ["dashboard", "--host", "0.0.0.0", "--port", "10000", "--no-open"]
