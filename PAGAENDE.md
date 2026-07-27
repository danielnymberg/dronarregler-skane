# Pågående körning — rikstäckning

Startad natten 2026-07-28. Läs det här först om du kommer in mitt i.

## Vad som kör

En självläkande kedja under **launchd**, inte i ett terminalfönster:

```bash
launchctl list | grep dronarkoll        # jobbet heter dronarkoll-natt
```

Skriptet ligger i sessionens scratchpad och gör, i ordning:

1. `01_ingest.py` — registeruttag för hela landet (10 681 objekt i scope)
2. bygge utan dokument, så att allt hamnar i tillståndet **"beslutet ej läst"**
3. `02_fetch_docs.py` — ~13 000 beslutsdokument + OCR, räkna med ~15 h
4. bygge med dokument
5. testsvit efter varje bygge

Varje steg försöker om upp till tre gånger. Cachen gör att ett avbrott aldrig
kostar mer än den post som var igång: `cache/raw/detail/` per objekt,
`cache/docs/` per dokument, `cache/text/` per extraherad text.

## Loggar

```
scratchpad/nattkorning.log     kedjans egen logg, en rad per fas
scratchpad/step1-sverige.log   registeruttaget
scratchpad/step2-sverige.log   dokument och OCR
```

## Om körningen dött

Den är resumbar. Starta om den:

```bash
launchctl remove dronarkoll-natt 2>/dev/null
launchctl submit -l dronarkoll-natt -- /bin/zsh <scratchpad>/nattkorning.sh
```

Eller kör kedjan manuellt — det går lika bra, det tar bara längre tid:

```bash
make all
```

## När den är klar

`nattkorning.log` slutar med `== NATTKORNING KLAR ==` och `TESTSVIT GRON`.
Då återstår:

1. **Granska bygget** — `make servera`, titta på kartan och några områdessidor.
2. **Deploy** — `npx wrangler pages deploy dist --project-name dronarkoll-skane`
   (`CLOUDFLARE_API_TOKEN` finns redan i MMDN:s miljö).
3. **Uppdatera** `VERIFIKATION.md` med den rikstäckande statistiken.

Deploy sker **inte** automatiskt. Bygget ska ses innan det går ut.

## Att veta om datan

- Omfattningen styrs av `lan_kort` i `config.json`. `null` = hela landet.
- Områden vars beslut ännu inte lästs visas med **"Beslutet har ännu inte lästs
  av tjänsten"** — inte som att ingen luftfartsföreskrift finns. Tystnad får
  inte se ut som ett besked.
- `dist/` innehåller inte `data/omraden/` (filtaket hos Cloudflare Pages).
  Per-områdesfilerna finns kvar i förrådet som CC0-produkt.
