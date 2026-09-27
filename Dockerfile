FROM nousresearch/hermes-agent:latest

USER root

RUN install -d -o hermes -g hermes -m 0755 /opt/data \
 && printf 'gateway:\n  platforms: {}\n' > /opt/data/config.yaml \
 && cp /opt/hermes/docker/SOUL.md /opt/data/SOUL.md \
 && touch /opt/data/.env \
 && rm -rf /opt/hermes/hermes_cli/web_dist /opt/hermes/ui-tui \
 && if [ -d /opt/hermes/plugins/platforms ]; then \
      find /opt/hermes/plugins/platforms -mindepth 1 -maxdepth 1 -type d ! -name telegram -exec rm -rf {} +; \
    fi \
 && npm install -g omniroute@3.8.50 \
 && chown -R hermes:hermes /opt/data \
 && chmod 600 /opt/data/.env

COPY start-render.sh /usr/local/bin/hermes-render-start
COPY backup.sh /usr/local/bin/hermes-backup
COPY backup-watch.py /usr/local/bin/hermes-backup-watch
COPY edge-server.py /usr/local/bin/hermes-edge-server
COPY sanitize-config.py /usr/local/bin/hermes-sanitize-config

RUN chmod 0755 \
  /usr/local/bin/hermes-render-start \
  /usr/local/bin/hermes-backup \
  /usr/local/bin/hermes-backup-watch \
  /usr/local/bin/hermes-edge-server \
  /usr/local/bin/hermes-sanitize-config

ENV HERMES_HOME=/opt/data
ENV HOME=/opt/data

USER hermes
WORKDIR /opt/data

ENTRYPOINT ["/usr/local/bin/hermes-render-start"]
CMD []
