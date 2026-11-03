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

Payment status works on serverless: the browser polls `/api/invoices/{id}` and
each poll asks 1Click directly; there are no background jobs.

Storage: a function instance only has its own `/tmp`, so without a shared
database a cold start or a second instance forgets chats and invoices. Point
the app at a free [Turso](https://turso.tech) (libSQL, SQLite-compatible)
database and every instance uses the same data. The mock 1Click simulator
keeps its state there too, so mock mode also works on Vercel.

1. Turso (free tier): create a database, then copy its URL and a token
   (`turso db show <db> --url`, `turso db tokens create <db>`, or the web dashboard).
   Pick the region closest to your Vercel functions.
2. Vercel: Add New → Project → import this GitHub repo. No build settings
   needed, `vercel.json` covers them.
3. Environment variables (Project → Settings → Environment Variables):

   | name | value |
   |---|---|
   | `TURSO_DATABASE_URL` | `libsql://<db>-<org>.turso.io` |
   | `TURSO_AUTH_TOKEN` | token from step 1 |
   | `ONECLICK_MODE` | `live` (or `mock` for a no-money demo) |
   | `LLM_PROVIDER` / `GEMINI_API_KEY` | `gemini` / your key |
   | `MERCHANT_DASHBOARD_TOKEN` | long random string, protects `/merchant` |

4. Redeploy, open `https://<project>.vercel.app/api/health`: it should say
   `"storage": "turso"`.
5. Own domain: Project → Settings → Domains → add `pay.your-domain`, then
   create the DNS record Vercel shows (a `CNAME pay → ...vercel-dns...`).
   HTTPS is issued automatically.

Without `TURSO_DATABASE_URL` the app still runs on Vercel with SQLite in
`/tmp`; fine for a quick look, not for a judged demo.
