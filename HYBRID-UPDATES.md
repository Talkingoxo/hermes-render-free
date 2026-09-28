# Hybrid Hermes updates

This repository drives **one** Cloudflare `hermes` Worker and **one** headless Render native executor. The chat UI remains intentionally undecided.

## Automated upstream refresh

`.github/workflows/update-hermes.yml` runs daily and can be started manually. It checks the official `nousresearch/hermes-agent:latest` Docker digest, pins it in `Dockerfile`, and records it in `.hermes-image-digest`. It commits only when the digest changes. No upstream router package is installed any more: Free Models is project-owned source in `cloudflare/`, so it is deployed by the Cloudflare workflow whenever it changes.

If the base image declaration in `Dockerfile` ever stops matching the expected `FROM nousresearch/hermes-agent…` form, the updater fails closed for manual review. Provider URL formats and model allow-lists are also changed by hand only, never inferred from new upstream releases.

## Automatic deploys

Render's main-branch GitHub auto-deploy is enabled for native executor updates. An optional repository secret `RENDER_DEPLOY_HOOK_URL` allows the scheduled updater to trigger a Render deploy explicitly if its Git webhook is unavailable. If you enable the hook, avoid double deployments by reviewing Render auto-deploy settings.

`.github/workflows/deploy-cloudflare.yml` syntax-checks and bundles the single Worker on pushes to main before deploying. Because GitHub's built-in workflow token commits do not initiate new push workflows, the scheduled updater also deploys Cloudflare directly after its own version-change commit.

**One-time required setup for GitHub-to-Cloudflare deploys:** Create a **scoped** Cloudflare API token permitting Workers deployments for this Cloudflare account; in this repository's **Settings > Secrets and variables > Actions > New repository secret**, save it as `CLOUDFLARE_API_TOKEN`. Never commit it to the repo. Until that secret is present, the Cloudflare deployment step fails; passing syntax and dry-run checks alone does not deploy.

All runtime provider API keys are entered through `https://hermes.aa4530607.workers.dev/admin/`, encrypted in a Durable Object, and never included in GitHub secrets or repository code. Admin and encryption secrets are already bound to the production Worker and survive Wrangler deployments. No Cloudflare KV or scheduled Cloudflare-to-Render calls are used.
