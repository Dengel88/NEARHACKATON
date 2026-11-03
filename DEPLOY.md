# Deploy

Two ways to put AI-Shtat Pay on a public URL. Both use the same code; no
secrets live in the repo, everything comes from environment variables
(see `.env.example`).

## A. Docker on any VPS (recommended for the public demo)

One container for the app, one for [Caddy](https://caddyserver.com/) which
gets a Let's Encrypt certificate automatically. SQLite sits on a Docker
volume, so chats, invoices and the merchant desk survive restarts.

Needs: a Linux box with Docker, ports 80/443 open, and a DNS `A` record
`pay.your-domain` → the server IP.

```bash
git clone https://github.com/Dengel88/NEARHACKATON.git aishtat-pay && cd aishtat-pay
cp .env.example .env
# edit .env:
#   DOMAIN=pay.your-domain
#   ONECLICK_MODE=live  LLM_PROVIDER=gemini  GEMINI_API_KEY=...
#   MERCHANT_DASHBOARD_TOKEN=<long random string>   # protects /merchant
docker compose up -d --build
curl https://pay.your-domain/api/health
```

Update: `git pull && docker compose up -d --build`. Logs: `docker compose logs -f app`.

Without a domain (quick check on the IP, plain HTTP):
`docker build -t aishtat-pay . && docker run -d -p 80:8000 --env-file .env -v aishtat:/data aishtat-pay`.

## B. Vercel (serverless)

`vercel.json` is ready: `vercel --prod`, then add the same env vars in the
Vercel dashboard. Payment status works on serverless because the browser
polls `/api/invoices/{id}` and each poll asks 1Click directly; there are no
background jobs.

Limitation: storage is SQLite in `/tmp` of each function instance. A cold
start or a second instance starts with an empty database, so an open chat,
an invoice, or the merchant desk can "forget" earlier data. Mock mode keeps
its fake deposits in memory and is affected the same way. Fine for a quick
preview; for the judged demo use option A.
