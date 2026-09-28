# Hybrid Hermes updates

This repository drives **one** Cloudflare `hermes` Worker and **one** headless Render native executor. The chat UI remains intentionally undecided.

## Automated upstream refresh

`.github/workflows/update-hermes.yml` runs daily and can be started manually. It checks the official `nousresearch/hermes-agent:latest` Docker digest and the stable `omniroute` npm release. It pins both in `Dockerfile` and saves the corresponding version files. It also regenerates `cloudflare/omniroute-catalog.js` from that tagged public OmniRoute release for the selected OpenRouter/Gemini adapters. It commits only when anything changes. The full OmniRoute server remains **disabled on Render Free** to protect the 512 MB limit.

Changes to public provider URL formats fail closed for manual compatibility review. A custom Cloudflare dashboard cannot automatically inherit arbitrary upstream OmniRoute dashboard features.

## Automatic deploys

Render's main-branch GitHub auto-deploy is enabled for native executor updates. An optional repository secret `RENDER_DEPLOY_HOOK_URL` allows the scheduled updater to trigger a Render deploy explicitly if its Git webhook is unavailable. If you enable the hook, avoid double deployments by reviewing Render auto-deploy settings.

`.github/workflows/deploy-cloudflare.yml` syntax-checks and bundles the single Worker on pushes to main before deploying. Because GitHub's built-in workflow token commits do not initiate new push workflows, the scheduled updater also deploys Cloudflare directly after its own version-change commit.

**One-time required setup for GitHub-to-Cloudflare deploys:** Create a **scoped** Cloudflare API token permitting Workers deployments for this Cloudflare account; in this repository's **Settings > Secrets and variables > Actions > New repository secret**, save it as `CLOUDFLARE_API_TOKEN`. Never commit it to the repo. Until that secret is present, the Cloudflare deployment step fails; passing syntax and dry-run checks alone does not deploy.

All runtime provider API keys are entered through `https://hermes.aa4530607.workers.dev/admin/`, encrypted in a Durable Object, and never included in GitHub secrets or repository code. Admin and encryption secrets are already bound to the production Worker and survive Wrangler deployments. No Cloudflare KV or scheduled Cloudflare-to-Render calls are used.
