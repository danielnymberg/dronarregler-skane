# Deploy

Sajten är helt statisk: `dist/` innehåller HTML, CSS, JS, GeoJSON och lokalt
vendorad Leaflet. Ingen server, ingen databas, inga miljövariabler vid drift.

## Innan första deploy — byt domän

`config.json` har en enda variabel som styr canonical-URL:er, `sitemap.xml` och
OG-metadata:

```json
"base_url": "https://EXEMPEL.se"
```

Byt till den skarpa domänen och bygg om:

```bash
make site      # räcker — data/ behöver inte hämtas om
make test-snabb
```

Inget annat behöver ändras. Länkar inom sajten är rotrelativa och fungerar
oavsett domän.

---

## Cloudflare Pages (valt mål i Fas 0)

### Alternativ A — direktuppladdning från maskinen

```bash
npx wrangler pages project create dronarkoll-skane --production-branch main
npx wrangler pages deploy dist --project-name dronarkoll-skane
```

Wrangler är inte installerat globalt på MMDN; `npx` hämtar det vid behov. Om
autentisering saknas: `npx wrangler login` (öppnar webbläsare), eller sätt
`CLOUDFLARE_API_TOKEN` från valvet i miljön.

### Alternativ B — kopplat till GitHub-repot

I Cloudflare-dashboarden: **Workers & Pages → Create → Pages → Connect to Git**,
välj `danielnymberg/dronarregler-skane`.

| Inställning | Värde |
|-------------|-------|
| Framework preset | `None` |
| Build command | *(tomt)* |
| Build output directory | `dist` |
| Root directory | `/` |

`dist/` är committad, så Cloudflare behöver inte bygga något — och behöver
därmed varken Python, poppler eller tesseract.

### Egen domän

**Custom domains → Set up a domain**, lägg till domänen och följ CNAME-
anvisningen. Ligger domänen redan i Cloudflare sätts DNS automatiskt. Byt sedan
`base_url` enligt ovan och deploya om.

### Headers

`dist/_headers` sätter `X-Content-Type-Options`, `Referrer-Policy: no-referrer`
och `Permissions-Policy: geolocation=(self), interest-cohort=()`. Cloudflare
Pages läser filen automatiskt. **Positionsknappen kräver HTTPS** — det får man
gratis på Pages.

---

## GitHub Pages (alternativ)

Repot är publikt, så gratisnivån räcker.

1. **Settings → Pages → Source: GitHub Actions**
2. Lägg till `.github/workflows/pages.yml`:

```yaml
name: Deploy till GitHub Pages
on:
  push:
    branches: [main]
    paths: ["dist/**"]
  workflow_dispatch:
permissions:
  contents: read
  pages: write
  id-token: write
concurrency:
  group: pages
  cancel-in-progress: true
jobs:
  deploy:
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/configure-pages@v5
      - uses: actions/upload-pages-artifact@v3
        with:
          path: dist
      - id: deployment
        uses: actions/deploy-pages@v4
```

Utan egen domän serveras sajten under `/dronarregler-skane/`. Rotrelativa
länkar bryts då — sätt i så fall en egen domän, eller bygg om med en
`base_path`. Med egen domän (CNAME) fungerar allt oförändrat.

---

## Efter deploy — kontrollera

```bash
curl -sI https://DOMÄN/ | head -1                       # 200
curl -s  https://DOMÄN/robots.txt                       # Sitemap-raden pekar rätt
curl -s  https://DOMÄN/sitemap.xml | grep -c "<url>"    # ≈ antal områden + 4
curl -sI https://DOMÄN/data/areas.geojson | head -1     # 200
curl -s  https://DOMÄN/ | grep -c "EXEMPEL.se"          # ska vara 0
```

Och i webbläsaren: positionsknappen (kräver HTTPS), LFV-togglen, en områdessida.

---

## Månadsuppdatering i drift

`.github/workflows/manadsuppdatering.yml` kör hela pipelinen, verifierar,
bygger om `dist/` bakom testgrinden och committar. Med Pages kopplat till
GitHub (alternativ B) deployas den nya sajten automatiskt vid den committen.

**Cron är avstängd.** Aktivera genom att avkommentera `schedule`-blocket i
workflow-filen. Kör gärna jobbet manuellt någon gång först — första körningen i
CI saknar cache och laddar ned samtliga dokument, vilket tar timmar.
