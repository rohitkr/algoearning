# Landing page (algoearning.com)

A static page: `index.html`, `landing.css`, `favicon.svg`, `robots.txt`. It has no build step and no
dependencies, so it stays up even when the Mac that runs the app (`app.algoearning.com`) is asleep. Its buttons go
to `https://app.algoearning.com/dashboard` and `/sign-up`.

The same page is also the home page of `app.algoearning.com` for signed-out visitors. The web app copies these
files into `apps/web/public` when it builds (`apps/web/scripts/sync-landing.mjs`), so a change here appears there
after the next `scripts/home-host.sh deploy`. On any host other than algoearning.com, the page's script keeps its
links on the current host.

Preview it locally:

```bash
python3 -m http.server 4000 -d apps/landing    # then open http://localhost:4000
```

Themes: dark by default, with a dark / light / system switch in the header. The choice is kept in `localStorage`
under `theme`, the same key the app uses, so the two stay in step on app.algoearning.com.

## Deploy on Render (where algoearning.com already points)

1. In Render, click **New > Static Site** and connect the `rohitkr/algoearning` repository.
2. Choose the branch to serve. Leave **Root Directory** empty and **Build Command** empty, and set **Publish
   Directory** to `apps/landing`.
3. Once it's live at `<name>.onrender.com`, go to **Settings > Custom Domains** and add `algoearning.com` and
   `www.algoearning.com`. Remove both from the old Render service first.
4. In Cloudflare DNS, point the apex `A` record and the `www` CNAME at the new service's addresses, as Render's
   custom-domain page shows them. Keep both records **DNS only** (grey cloud).

Every push to that branch redeploys the page automatically.
