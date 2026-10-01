# 0019 Home hosting: this Mac behind a Cloudflare Tunnel, while there are two users

**Context.** Two people use the app for now (Rohit and one friend). A VPS is not worth paying for yet, and the
stack already runs on Rohit's Mac. The broadband (Hathway) has no static IP and no inbound ports to open.

**Decision.** Serve production from the Mac through a free Cloudflare Tunnel. `cloudflared` dials out to
Cloudflare, so no router port is opened and the changing IP does not matter for visitors. The zone
`algoearning.com` moves to Cloudflare DNS, with every existing record kept. The apex and `www` keep pointing at
Render. The tunnel answers three names: `app.` (web), `monitor.` (the same web app, opening on the admin panel)
and `api.`.

**Running it.** `scripts/home-host.sh` installs each service (Postgres, Redis, API, web, feed, worker, engine,
tunnel, and a `caffeinate` that keeps the Mac awake) as a launchd agent. launchd starts them at login and restarts
any that exit. The web app runs from its standalone production build, and the API runs with `APP_ENV=production`,
so `/docs` is off and `DEV_AUTH` is refused. `.env.production` holds only the public URLs on top of `.env`.
Development (`make dev`) and production share the database on purpose, so they never run at the same time:
`install` refuses while `make dev` runs, and `make dev` refuses while the services are installed, because two
engines would place every order twice.

**The IP still matters for orders.** Zerodha only takes orders from an IP listed in each user's Kite app (SEBI's
static-IP rule, ADR 0009). The worker reads the Mac's public IPv4 every 5 minutes (`check-public-ip`) and keeps it
in the `public_ip` platform setting. On a change it sends `ip_changed` to every user with a Zerodha account. The
dashboard warns them for 3 days, and the Brokers page always shows the IP to register.

**Limits, accepted for now.** The site is down whenever the Mac is asleep, off or offline. The data has a single
copy on one disk, and everything else in that Mac's user account can read it. If Hathway puts the line behind
CGNAT, the IP is shared and can change often. Move to a VPS with a static IP (ADR 0009) before inviting more users.
