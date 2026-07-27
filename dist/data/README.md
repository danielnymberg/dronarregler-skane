# Databas: skyddade områden i Skåne län med föreskriftscitat

Hämtningsdatum: **2026-07-26**
Licens: **CC0 1.0** (se `LICENSE`)
Antal objekt: **640**

Databasen är byggd för att kunna läsas fristående från webbtjänsten. Varje
uppgift går att spåra till det myndighets-API-svar den kom ur.

## Filer

| Fil | Innehåll |
|-----|----------|
| `areas.geojson` | Förenklad visningsgeometri för kartrendering. **Använd inte för punkt-i-polygon.** |
| `bbox-index.json` | Omslutande rektangel per objekt, för snabb kandidatsökning. |
| `omraden/{nvrid}.json` | Ett objekt: **oförenklad originalgeometri**, verifierade citat, dokumentlänkar, proveniens. |
| `manifest.json` | Proveniens per objekt och per lager: källa, svarshash, hämtningsdatum. |
| `verification-report.json` | Verifieringens utfall, inklusive varje kasserat citat och orsak. |
| `verifierade-citat.json` | Citaten som klarade verifieringen, per NVRID. |
| `extraktion.json` | Extraktionens råutfall före verifiering (för granskning). |

## Schema: `omraden/{nvrid}.json`

```
nvrid                 str    Naturvårdsregistrets objekt-id
slug                  str    URL-slug
namn, skyddstyp       str
lager                 str    lager-id (en rättskälla per lager)
karnlager             bool   ingår i kärnlagret som alltid laddas
kommun, lan           str
beslutsstatus         str
beslutsmyndighet      str|null
forvaltare            str|null
tillsynsmyndighet     str|null
urspr_beslutsdatum    str|null   ISO-datum
senaste_gallandedatum str|null   ISO-datum
area_ha               float|null
sknat_url             str    områdets sida i Kartverktyget Skyddad natur
svarslage             str    "reglerat-las-beslutet" | "lanklage"
ocr                   bool   minst ett dokument är OCR-tolkat
foreskriftsomraden    list   råa poster ur registret
sasongsdata           list   delmängd med franDatum/tillDatum satta
dokument              list   {namn, url, dokument_id, sha256, sidor, ocr, beslutsstatus, fel}
dokument_hoppade      list   {namn, url, orsak} — dokument tjänsten inte läst
citat                 list   se nedan
geometri              GeoJSON-geometri | null  — OFÖRENKLAD originalgeometri
bbox                  [minx, miny, maxx, maxy] | null
geometri_kalla        {tjanst, url, typeName, svarshash_sha256, hamtningsdatum, licens}
hamtningsdatum        str
```

### Citatobjekt

```
citat                 str   ordagrann delsträng ur dokumentets extraherade text
inledning             str|null  föreskriftsblockets rubrik ("Det är förbjudet att:"),
                            en EGEN sammanhängande delsträng ur samma dokument —
                            aldrig hopskarvad med citatet — separat verifierad.
                            null om ingen rubrik kunde verifieras.
inledning_sidnummer   int|null  sidan rubriken står på (kan vara sidan före)
punkt                 str|null  punkt-/paragrafmarkör, t.ex. "7." eller "§ 5"
sidnummer             int   1-indexerat sidnummer i källdokumentet
teckenoffset_pa_sidan int   startposition i sidans text
klassificering        str   uttryckligt-luftfartygsförbud | start-landningsförbud |
                            motorfordon-möjligen-relevant | störningsförbud-djurliv |
                            annat-läs-beslutet
konfidens             str   "hög"  = förbudsuttryck står i punkten själv
                            "medel" = förbudsuttryck står i den verifierade
                                      rubriken, eller punkten är numrerad
                            "låg"   = varken eller; texten kan lika gärna komma
                                      ur beslutets skäl som ur dess föreskrifter
dokument_id           str
dokument_namn         str
dokument_url          str   direktlänk till källdokumentet
dokument_sha256       str   hash av den nedladdade filen
ocr                   bool  texten är OCR-tolkad ur inskannat original
verifierad            bool  alltid true i denna fil
verifieringsmetod     str
```

## Klassificeringen är en etikett, inte en bedömning

`klassificering` säger vilken sorts nyckelord citatet innehåller. Den säger
ingenting om vad föreskriften betyder eller om en viss flygning är i sin
ordning. Substansen är citatet.

## Geometri

- `omraden/{nvrid}.json` → `geometri` är **oförenklad** och identisk med
  WFS-svaret från Naturvårdsverket, bortsett från att flera WFS-rader med
  samma NVRID lagts i samma MultiPolygon. Det är den geometri som ska
  användas för punkt-i-polygon.
- `areas.geojson` är förenklad med Douglas–Peucker, utgångstolerans
  **15 m**, koordinater avrundade till
  5 decimaler (~1 m), med två garantier:

  1. **Ytan växer aldrig.** Ytterringar får inte bli större, hålringar inte
     mindre, och skulle en polygons nettoyta ändå ha vuxit behålls polygonen
     oförenklad. Garantin kontrolleras på exakt de koordinater som hamnar i
     filen, efter avrundning.
  2. **Ytförlusten per objekt är högst
     2.0 %.** Toleransen trappas
     ned per objekt tills kravet är uppfyllt, och i sista hand behålls
     originalgeometrin. En fast tolerans är rimlig för ett stort reservat men
     äter en orimlig andel av ett litet, avlångt objekt.

  Vid detta bygge: punktantalet gick från 166672 till
  98810, 660 ringar behölls
  oförenklade, största ytminskning för ett enskilt objekt var
  1.996 % (NVRID
  2043746), och hela datamängdens yta
  gick från 2649382693 till
  2648050020 m².
  Använd tolerans per objekt: {'15 m': 582, '1.5 m': 7, '5 m': 48, 'oförenklad': 3}.
- Inga buffertar, cirklar eller uppskattade zoner förekommer någonstans i
  databasen.

## Kända luckor

- Naturvårdsverkets punktlager `SkyddadePunkter` returnerar noll objekt för
  Skåne län vid hämtningen. Naturminnen finns i ytlagret.
- Endast dokument vars filnamn pekar ut dem som beslut, föreskrifter,
  förordnanden, kungörelser eller ändringsbeslut har lästs. Övriga listas per
  objekt i `dokument_hoppade`.
- Ett område kan ha flera beslut där ett senare ändrat ett tidigare.
  Databasen anger inte vilken lydelse som gäller i dag.
- Luftrumsdata (flygplatser, restriktionsområden, NOTAM, geografiska
  UAS-zoner) ingår inte.

## Statistik vid detta bygge

- Objekt med minst ett verifierat citat: 335
- Objekt i länk-läge: 304
- Objekt med OCR-tolkad text: 300
- Objekt med säsongsdata: 49
- Objekt utan geometri: 0
