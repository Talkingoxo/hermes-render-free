FROM nousresearch/hermes-agent:latest

USER root

RUN install -d -o hermes -g hermes -m 0755 /opt/data \
 && cp /opt/hermes/cli-config.yaml.example /opt/data/config.yaml \
 && cp /opt/hermes/docker/SOUL.md /opt/data/SOUL.md \
 && touch /opt/data/.env \
 && chown -R hermes:hermes /opt/data /opt/hermes/ui-tui /opt/hermes/node_modules \
 && chmod 600 /opt/data/.env

COPY start-render.sh /usr/local/bin/hermes-render-start
COPY backup.sh /usr/local/bin/hermes-backup
COPY backup-watch.py /usr/local/bin/hermes-backup-watch
RUN chmod 0755 /usr/local/bin/hermes-render-start /usr/local/bin/hermes-backup /usr/local/bin/hermes-backup-watch

ENV HERMES_HOME=/opt/data
ENV HOME=/opt/data

USER hermes
WORKDIR /opt/data

ENTRYPOINT ["/usr/local/bin/hermes-render-start"]
CMD []
