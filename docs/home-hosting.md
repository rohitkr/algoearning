# Hosting AlgoEarning from this Mac

The app runs on this Mac and is reached at **https://app.algoearning.com** (web), **https://api.algoearning.com**
(API) and **https://monitor.algoearning.com** (admin panel) through a free Cloudflare Tunnel. `algoearning.com`
and `www` keep serving the existing Render site. Why it works this way: [ADR 0019](adr/0019-home-hosting.md).

Steps marked **(you)** need your accounts. Everything else is one command.

## 1. Move the domain's DNS to Cloudflare (you, once)

The domain is registered with PublicDomainRegistry through a reseller (nameservers `dns1-4.india-to.com`). Today
it has two records: the apex `A 216.24.57.1` (Render) and `www CNAME algoearning.onrender.com`.

1. Sign up at https://dash.cloudflare.com and choose **Add a domain**. Enter `algoearning.com`, pick the **Free**
   plan, and let Cloudflare scan the existing records.
2. On the review page, check that both records above are listed. Add them if they are missing. Set both to
   **DNS only** (grey cloud) so Render keeps serving them exactly as it does today.
3. Cloudflare shows two nameservers, such as `xxx.ns.cloudflare.com`. In your registrar's control panel (where you
   bought the domain), turn off DNSSEC if it is on. Then replace the four `india-to.com` nameservers with the two
   Cloudflare ones.
4. Wait until Cloudflare emails you that the domain is **Active**. This usually takes minutes, sometimes a few
   hours. Then check that https://algoearning.com still opens the Render site.

## 2. Create the tunnel

```bash
brew install cloudflared
cloudflared tunnel login                 # (you) opens the browser: pick algoearning.com
scripts/home-host.sh setup               # writes .env.production and apps/web/.env.production.local
scripts/home-host.sh tunnel              # creates the tunnel + DNS for app., api. and monitor.
```

`tunnel` only adds the three `app`, `api` and `monitor` CNAMEs. It never changes the apex or `www`.

## 3. Sign-in (Clerk)

**Nothing to change for now.** The current Clerk keys belong to a development instance, and those work on any
domain, including `app.algoearning.com`. The sign-in page shows a small "Development mode" badge. The API already
accepts session tokens from `app.` and `monitor.` (see `WEB_ORIGIN` in `.env.production`).

Later, for a production Clerk instance **(you)**: create it in the Clerk dashboard for `algoearning.com`. Add the
CNAME records Clerk lists to Cloudflare DNS as **DNS only**, set up your own Google OAuth credentials, and put the
new `pk_live`/`sk_live` keys in `.env` and `apps/web/.env.local`. A production instance has its own users, so you
and your friend would sign up again and start from empty accounts. Stay on the development keys until that is worth
it.

Optional **(you)**: in Clerk, go to **Webhooks** and point the endpoint at
`https://api.algoearning.com/v1/webhooks/clerk`. In Razorpay (test mode), go to **Webhooks** and use
`https://api.algoearning.com/v1/webhooks/razorpay`. Both work now that the API is public.

## 4. Brokers and market data (you, and your friend for their own Kite app)

- **Zerodha (each user's Kite app on https://developers.kite.trade/apps):** set the **Redirect URL** to
  `https://api.algoearning.com/v1/brokers/zerodha/callback`. Add this Mac's public IP to the app's IP list. The
  Brokers page in the app shows the IP, and so does `scripts/home-host.sh status`.
- **ICICI Breeze:** set the app's Redirect URL to `https://app.algoearning.com`.

## 5. Keep the Mac up (you)

This Mac is a MacBook with FileVault on, so:

- Keep it plugged in with the lid open. A closed lid puts it to sleep unless an external display is connected.
  The `awake` service stops idle sleep, but not lid sleep.
- Go to **System Settings > Battery > Options** and turn on **Prevent automatic sleeping on power adapter when the
  display is off**. Also turn on **Wake for network access**.
- After a reboot or power cut, nothing starts until someone types the password at the login screen (FileVault).
  The services then start by themselves.

## 6. Switch from `make dev` to production

```bash
# stop make dev first (Ctrl-C in its terminal), outside market hours if a live strategy is running
scripts/home-host.sh build               # production build (about a minute)
scripts/home-host.sh install             # starts every service now and at every login
scripts/home-host.sh status              # services, local + public health, the server IP
```

Then open https://app.algoearning.com and sign in.

## Day to day

| Task                         | Command                                                      |
| ---------------------------- | ------------------------------------------------------------ |
| After `git pull`             | `scripts/home-host.sh deploy` (restarts all but engine)      |
| Engine code changed          | `scripts/home-host.sh restart engine` (outside market hours) |
| Watch logs                   | `scripts/home-host.sh logs` or `logs api`                    |
| Back to development          | `scripts/home-host.sh stop`, then `make dev`                 |
| Remove the services entirely | `scripts/home-host.sh uninstall`                             |

`stop` lasts until the next login. Use `uninstall` to make it permanent. Logs are in `.data/logs/`.

**When the IP changes:** Zerodha users get an "IP changed" notification by email or Telegram, and the dashboard
shows a warning for 3 days. Each of you replaces the old IP in your Kite app before the next live trade. Paper
trading is not affected.
