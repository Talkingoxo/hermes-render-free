# Hermes on Render Free

Minimal Render compatibility wrapper around the official
`nousresearch/hermes-agent:latest` image.

It does not fork or modify Hermes source code. The wrapper only bypasses the
s6 PID-1 path that is incompatible with Render's container launcher, while
still running Hermes' upstream `stage2-hook.sh` and `main-wrapper.sh`.

Render should run this as a Docker Web Service on port 10000.
