FROM nousresearch/hermes-agent:latest

USER root

COPY start-render.sh /usr/local/bin/hermes-render-start
RUN chmod 0755 /usr/local/bin/hermes-render-start

ENTRYPOINT ["/usr/local/bin/hermes-render-start"]
CMD ["dashboard", "--host", "0.0.0.0", "--port", "10000", "--no-open"]
