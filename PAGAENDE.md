# Läget

Rikstäckningen är klar. Ingen körning pågår.

## Vad som finns

10 681 skyddade områden i hela Sverige, 20 564 verifierade citat, 259
luftrumszoner från LFV, 8 författningsavsnitt på `/regler/`. Byggt 2026-07-28.

## Så här kör du om allt

```bash
make all        # steg 1–9 + testsvit; dist/ rullas tillbaka om testerna faller
make servera    # granska på http://localhost:8787
npx wrangler pages deploy dist --project-name dronarkoll-skane
```

Steg 2 (dokument + OCR) tar ~15 timmar från tom cache. Med varm cache är hela
kedjan under fem minuter. Cachen gör körningen resumbar: ett avbrott kostar
aldrig mer än den post som var igång.

Kör det som en lång körning under **launchd**, inte i ett terminalfönster —
bakgrundsprocesser knutna till en session dog två gånger under
rikstäckningsbygget:

```bash
launchctl submit -l dronarkoll -- /bin/zsh -c "cd $PWD && make all"
launchctl list | grep dronarkoll
```

Ta bort jobbet när det är klart (`launchctl remove dronarkoll`) — annars startar
det om kedjan direkt när den avslutas.

## Att veta om datan

- Omfattningen styrs av `lan_kort` i `config.json`. `null` = hela landet.
- `luftfartslage` per område har sex värden och skiljer på vad tjänsten LÄST
  och vad den INTE läst. Se `luftfartslage()` i `scripts/05_build_data.py`.
- `dist/` innehåller inte `data/omraden/` (filtaket hos Cloudflare Pages).
  Per-områdesfilerna finns kvar i förrådet som CC0-produkt.
- `data/lfv.json` är **inte** CC0. Se `data/LICENSE` och D-51.
