# Drönarkoll Skåne

En gratis, reklamfri, statisk webbtjänst som för Skåne län visar var det finns
skyddade naturområden vars myndighetsbeslut kan innehålla föreskrifter som berör
drönarflygning. Tjänsten **citerar föreskrifterna ordagrant** ur besluten och
länkar till originaldokumentet och ansvarig myndighet.

Tjänsten är en **vägvisare till källor**. Den pekar och citerar. Den bedömer
inte, tolkar inte, sammanfattar inte och ger inga klartecken. Den ersätter inte
LFV:s drönarkarta för luftrumsinformation — den kompletterar den.

## Failsafe-kontraktet

Åtta regler går före bekvämlighet, prestanda och estetik. Går en regel inte att
uppfylla för ett enskilt objekt degraderas objektet till **länk-läge** ("här
finns ett beslut — läs det") i stället för att regeln försvagas.

| Regel | Innebörd | Var den lever i koden |
|-------|----------|----------------------|
| **R1** | Inga egenförfattade regeltexter. Allt som visas är ordagranna citat med paragraf, sidnummer, myndighet, länk och hämtningsdatum. | `scripts/03_extract.py` skär ut delsträngar; ingen omskrivning finns någonstans |
| **R2** | Tre svarslägen, aldrig ett fjärde. Orden *tillåtet*, *fritt*, *OK att flyga* förekommer aldrig. | `site/assets/app.js`, `scripts/06_build_site.py`, testfall C5 |
| **R3** | Geometri uppfinns aldrig. Inga buffertar, cirklar eller uppskattade zoner. | `scripts/lib/geom.py` (krympgaranti), testfall B och C1 |
| **R4** | Datans ålder alltid synlig, globalt och per lager. | `dataalder`-raden på varje sida |
| **R5** | Licensdisciplin. Bara CC0-källor bearbetas. LFV visas som ostylat raster direkt från LFV:s server. | `/kallor/`, testfall C6 |
| **R6** | Ett lager per rättskälla + täckningsdeklaration. | `/kallor/`, testfall C4 |
| **R7** | Extraktion utan strängverifiering är ogiltig. | `scripts/04_verify.py`, testfall A |
| **R8** | Vid osäkerhet: säg mindre, länka mer — men larma inte trubbigt. | genomgående; svarsläge "läs källan" i stället för gissat verdikt |

Den fasta ansvarstexten står ordagrant i `scripts/06_build_site.py` och visas på
varje områdessida och i varje positionssvar. Den får inte kortas eller mjukas
upp — den beskriver tjänstens faktiska beteende.

## Snabbstart

```bash
make all        # steg 1–6 + testsvit; dist/ skrivs bara om testerna är gröna
make servera    # http://localhost:8787
```

Första körningen laddar ned ~800 beslutsdokument (flera GB) med ≥1 s mellan
anrop och OCR-tolkar de inskannade. Räkna med 30–60 minuter. Efterföljande
körningar använder `cache/` och tar minuter.

### Förutsättningar

| Verktyg | Varför |
|---------|--------|
| Python 3.10+ | hela pipelinen, inga tredjepartsbibliotek |
| `poppler-utils` (`pdftotext`, `pdftoppm`, `pdfinfo`) | textextraktion ur PDF |
| `tesseract` med språkpaketet **`swe`** | OCR av inskannade beslut |
| GNU make | pipelinemål |

macOS: `brew install poppler tesseract tesseract-lang`
Debian/Ubuntu: `apt-get install poppler-utils tesseract-ocr tesseract-ocr-swe`

## Pipeline

Varje steg kan köras om enskilt.

| Steg | Kommando | Gör |
|------|----------|-----|
| 1 | `make ingest` | Hämtar alla skyddade områden i Skåne ur Naturvårdsverkets NVR (WFS 2.0, CC0) samt detaljposter med dokumentlänkar och säsongsdata. Skriver `data/manifest.json` och rå-svaren till `cache/raw/`. |
| 2 | `make docs` | Laddar ned beslutsdokument throttlat, hashar dem, extraherar text med `pdftotext` och OCR-tolkar inskannade original med `tesseract -l swe` parallellt över alla kärnor. |
| 3 | `make extract` | Skär ut föreskriftspunkter som kan beröra flygning som sammanhängande delsträngar ur dokumentets text och klassificerar dem med dokumenterade nyckelordsregler. |
| 4 | `make verify` | **Grinden.** Strängmatchar varje citat mot källdokumentets text efter dokumenterad normalisering. Miss ⇒ citatet kasseras, objektet degraderas till länk-läge. Skriver `data/verification-report.json`. |
| 5 | `make data` | Bygger `data/` som fristående CC0-produkt: `areas.geojson`, `bbox-index.json`, `omraden/{nvrid}.json` med oförenklad originalgeometri, `LICENSE`, schema-`README.md`. |
| 6 | `make site` | Bygger `dist/` **enbart ur `data/`**. |

`make all` kedjar ihop alltihop och rullar tillbaka `dist/` om testsviten faller.

## Testsvit

```bash
make test          # A–D
make test-snabb    # A–C (hoppar över nätberoende länkhälsotest)
make visuell       # plockar fram granskningsplanen för test E
```

| Test | Kontrollerar |
|------|--------------|
| **A** Citaträkenskap | Varje citat i `dist/` strängmatchar sitt källdokument. 100 % krävs. |
| **B** Geometriproveniens | Varje geometri spåras till en källhash; inga föräldralösa geometrier; ringar giltiga; förenklingen har aldrig ökat en yta. |
| **C** Golden tests | `tests/golden.json` — anti-ESMH-punkten i Höganäs, Kullaberg, säsongsdata, täckningsdeklaration, förbjudna ord, ingen LFV-vektor. |
| **D** Länkhälsa | Slumpurval av ≥50 dokumentlänkar svarar 200/redirect. |
| **E** Visuell granskning | Manuell i Chrome, skärmdumpar i `verification/screenshots/`, utfall i `VERIFIKATION.md`. |

**Regressionsprincip:** varje bugg som hittas blir ett nytt fall i
`tests/golden.json` *innan* den rättas.

## Månadsuppdatering

```bash
make uppdatera
```

Kör om steg 1, diffar manifestet mot föregående bygge, hämtar bara nytt/ändrat
material, kör verifieringen från noll och bygger om sajten bakom testgrinden.
Diffen skrivs till `data/uppdateringsdiff.json`.

GitHub Action: `.github/workflows/manadsuppdatering.yml`. **Schemat är
inaktiverat** — jobbet går bara att starta manuellt tills cron-blocket
avkommenteras.

## Deploy

Se [`DEPLOY.md`](DEPLOY.md). Innan första deploy: byt `base_url` i
`config.json` från placeholdern `https://EXEMPEL.se` till den skarpa domänen
och kör `make all` igen — det är den enda variabel som styr canonical-URL:er,
sitemap och OG-metadata.

## Katalogstruktur

```
config.json            sajtnamn, bas-URL, throttling, förenklingstolerans
scripts/01–07          pipelinesteg, ett per fil
scripts/lib/           common.py (HTTP/cache), textnorm.py (normalisering), geom.py
site/                  statiska tillgångar och lokalt vendorad Leaflet
data/                  CC0-produkten (se data/README.md för schema)
cache/                 rå-svar, PDF:er, extraherad text — gitignorerad
dist/                  byggd sajt
tests/                 testsvit + golden.json
verification/          skärmdumpar och granskningsplan
DECISIONS.md           varje icke-trivialt val med motivering
VERIFIKATION.md        utfallet av den visuella granskningen och statistiken
```

## Licens

- **Tjänstens databas** (`data/`): CC0 1.0 — se `data/LICENSE`.
- **Källdata**: Naturvårdsverkets naturvårdsregister, CC0.
- **Citaten**: ordagranna utdrag ur svenska myndighetsbeslut, som enligt 9 §
  upphovsrättslagen inte omfattas av upphovsrätt.
- **LFV:s luftrumsdata**: CC BY-NC-ND 4.0, visas enbart som raster direkt från
  LFV:s server. Ingen LFV-geometri hämtas, lagras, stylas om eller
  återpubliceras.
- **Koden**: se `LICENSE` i repotroten.

## Rapportera fel

Öppna ett ärende: <https://github.com/danielnymberg/dronarregler-skane/issues>.
Ta gärna med NVRID eller position, vilket svarsläge som visades och
datamanifestets hämtningsdatum — knappen "Rapportera fel" på varje områdessida
fyller i det åt dig.
