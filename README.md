# Hermes + Free Models

One split deployment: Cloudflare Workers runs state, AI orchestration, the on-demand Free Models manager, R2 backups, and browser rendering. Render Free runs only Hermes native tools (terminal, filesystem, native browser and code execution).

## URLs

- Cloudflare API and future main frontend: https://hermes.aa4530607.workers.dev
- **Free Models manager:** https://hermes.aa4530607.workers.dev/free-models
- Render's headless native executor: https://hermes-agent-z8em.onrender.com

Free Models is our own small Cloudflare module, not the upstream OmniRoute server. It uses no KV, polling or keep-alives. Neither Render nor a persistent management process is needed for model requests. The management HTML and provider settings are fetched only when opened.

### Automatic free model routing

Use `model: "auto"` with `POST /v1/chat/completions` or a Hermes session's `/chat` endpoint. The router tries available free models in this order:

1. Cloudflare Llama 3.3 70B fast (no user API key).
2. Cloudflare Llama 3.1 8B fast (no user API key), unless the account's shared free daily allocation is exhausted.
3. OpenRouter **free** models, if the user has added a free-tier OpenRouter API key.
4. A configured Gemini model, if a Gemini API key is supplied and a free quota is available.

Explicit model selections can bypass fallback. Requests to optional providers are made directly from Cloudflare, never via Render. **There are no shared/stolen API keys.** Cloudflare's free daily AI allocation, provider outages, and each provider's own limits still apply.

The Free Models manager stores any optional provider keys as encrypted AES-GCM data in the existing SQLite Durable Object. The encryption key and admin token are Cloudflare Worker secrets and are not stored in GitHub. The manager is available only after unlocking with the `HERMES_ADMIN_TOKEN` secret.

The main Hermes chat UI has deliberately not been selected or bundled.

## Updating this split deployment

- `.github/workflows/update-hermes.yml` checks the official `nousresearch/hermes-agent` Docker digest every day. If it changes, it updates the pinned Dockerfile digest. Render's GitHub auto-deploy deploys the new Docker image, leaving the Cloudflare code and native bridge untouched.
- `.github/workflows/deploy-cloudflare.yml` validates Free Models routing tests and deploys the **same existing** `hermes` Worker after a push to `main`. Its bindings and runtime configuration are tracked in `wrangler.toml`; Wrangler preserves existing production secrets.
- An upstream-update commit also deploys the Cloudflare Worker directly in its own workflow because pushes made with `GITHUB_TOKEN` do not start a second GitHub Actions push workflow.
- For GitHub Actions deployment, configure the repository's `CLOUDFLARE_API_TOKEN` secret with the necessary Cloudflare Worker edit permissions. Without that one-time credential, direct Cloudflare API deployments still work, but automatic **GitHub → Cloudflare** deployment is not active.

No upstream OmniRoute package remains to update. Free Models is project-owned source code and is deployed through the Cloudflare workflow whenever it changes. Automatic changes to official AI provider models are intentionally not inferred from new releases without compatibility tests.

R2 backups remain event-driven (only after relevant Hermes state changes). The prior periodic Cloudflare↔Render backup polling loop is removed.
