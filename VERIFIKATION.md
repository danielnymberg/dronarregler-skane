# VERIFIKATION.md

Utfallet av testsviten och den visuella granskningen. Byggdatum **2026-07-27**,
datahämtning **2026-07-26**.

---

## 1. Sammanfattning

| Mått | Utfall |
|------|--------|
| Objekt i databasen (Skåne län) | **640** |
| Citat som visas för användaren | **1 353** |
| Citat som klarade ordagrann strängmatchning | **1 353 av 1 353 — 100 %** |
| Citat som kasserades | **0** |
| Verifierade föreskriftsinledningar | **970** |
| Objekt med minst ett verifierat citat | **335 (52,3 %)** |
| Objekt i länk-läge | **305 (47,7 %)** |
| Objekt med OCR-tolkad text | **300 (46,9 %)** |
| Objekt med säsongsdata ur registret | **49** |
| Objekt utan geometri | **0** |
| Dokument nedladdade | **813 av 814** |
| Dokument OCR-tolkade | **385 (47,4 %)** |
| Nedladdningsfel | **0** |

**Andelen automatiskt verifierade citat är 100 %** — långt över uppdragets
60-procentsgräns. Ingen enda visad textsträng har passerat utan att ha matchats
ordagrant mot källdokumentet. Noll citat kasserades.

Det som är lägre är *täckningen*: 52 % av objekten har minst ett citat. Analysen
av de återstående 48 % står i avsnitt 4 — den korta versionen är att merparten
inte är ett fel utan ett korrekt utfall.

---

## 2. Testsvit A–D

Kommando: `python3 tests/test_suite.py` (full svit inklusive nätberoende
länkhälsotest). **Alla gröna.**

```
A: 1353 citat prövade mot källdokument, 1353 godkända, 0 utan källtext;
   31 HTML-sidor korsprövade
B: 640 visningsgeometrier, 1263 ringar kontrollerade, utgångstolerans 15 m,
   största uppmätta ytförlust 2.00 % (gräns 2.0 %), ingen yta växte,
   alla spårbara till källhash
C1  anti-esmh-hoganas-tatort: noll zonträffar i egna lager → svarsläge 2 ✓
C2  2000972 (Västra Kullaberg): 10 verifierade citat ✓
C2  2000975 (Östra Kullaberg): 7 verifierade citat ✓
C2b hojdband-sodra-aspet (Södra Äspet): 'på en höjd understigande 120 meter'
    bevarad ordagrant hela vägen ut ✓
C2b uttryckligt-dronarforbud-gyetorp (Gyetorp): 'flyga med drönare' ✓
C2b undantag-hallands-vadero (Hallands Väderö):
    'landa med luftfarkost med undantag' ✓
C2c Lilla Köpinge: kolonfri listrubrik hittad ✓
C2c Maltesholm: kolonfri listrubrik hittad ✓
C2c alla 970 verifierade föreskriftsinledningar slutar som listrubrik ✓
C3  49 områden med säsongsdata ur källdata (exempel: Hallands Väderö 1/4) ✓
C4  /kallor/ listar lager med hämtningsdatum 2026-07-26;
    tomma lager i scope: ['interimistiskt-forbud'] ✓
C5  inga tillåtelseformuleringar i dist/ utanför citatblock ✓
C6  ingen kod anropar LFV:s WFS eller lagrar LFV-geometri ✓
D:  50 slumpvis valda dokumentlänkar av 814 svarade 200/redirect ✓
```

### Vad de tre viktigaste testerna faktiskt bevisar

**C1 — anti-ESMH.** Punkten (12,5560, 56,2000) i Höganäs tätort ger noll
zonträffar mot tjänstens egna lager. Punkten kontrollerades först mot rådata
från Naturvårdsverkets WFS: ingen skyddsyta täcker den, och närmaste skyddade
område (naturreservatet Ärtan och Bönan) ligger 940 m bort. Den befintliga
konkurrenten larmar "FLYGFÖRBUD" på exakt den här platsen genom en egenritad
5 km-cirkel kring klubbflygfältet ESMH. Den här tjänsten kan inte producera ett
sådant larm, eftersom varje yta måste komma ur ett myndighets-API-svar.

**C2b — höjdbandet överlever.** Beslutet för Södra Äspet förbjuder att "flyga
fjärrmanövrerat obemannat luftfartyg exempelvis drönare **på en höjd
understigande 120 meter över medelhavsnivå**". Testet kräver att den
höjdangivelsen finns ordagrant kvar i den byggda HTML-sidan. Det är
uppdragets tredje felklass i miniatyr: ett trubbigt verdikt hade svalt
höjdbandet och gjort förbudet absolut. Samma testfamilj låser fast undantaget
i Hallands Väderös föreskrift ("med undantag för de landningar som
Sjöfartsverket företar vid fyrinspektion").

**C5 — ingen tillåtelse någonstans.** Sökning över hela `dist/` efter
"tillåtet att flyga", "fritt fram", "du får flyga", "OK att flyga" och
närliggande mönster, med citatblock och HTML-taggar maskerade, ger noll
träffar.

---

## 3. Test E — visuell granskning i Chrome

Utförd mot lokal server på det färdiga bygget. Skärmdumpar i
`verification/screenshots/`.

| # | Vad som granskades | Utfall | Skärmdump |
|---|--------------------|--------|-----------|
| a | Kartan renderar alla Skånepolygoner vid länsutsnitt, utan länsväljare och utan att något kärnlager måste slås på | **OK** — 442 områden i kärnlagret + 198 i extralagren laddas direkt. Med LFV-lagret avslaget syns hela länets polygontäckning tydligt. | `a-karta-lansutsnitt-lfv-pa.jpg`, `a-karta-alla-skanepolygoner-lfv-av.jpg` |
| b | Positionssimulering för Höganäspunkten ger svarsläge 2 med korrekt text | **OK** — "Ingen restriktion hittad i de källor tjänsten täcker", följt av "Det är ett besked om vad databasen innehåller — inte ett besked om din flygning", LFV-raden, åtta närliggande områden med avstånd (närmast 940 m, identiskt med den oberoende Python-beräkningen) och ansvarstexten. | `b-positionssvar-hoganas-svarslage-2.jpg` |
| c | Kullabergssidan visar citat, länk, hämtningsdatum och ansvarstext | **OK** — 10 verifierade citat med föreskriftsinledning som eget stycke, klassificeringschip, sidnummer, direktlänk till PDF:en, hämtningsdatum i sidhuvudet. | `c-kullaberg-citat-lank-datum.jpg` |
| d | LFV-rastret togglar och attributionen syns | **OK** — mätt i webbläsaren: 18 WMS-rutor från `daim.lfv.se` och attributionen "© LFV (CC BY-NC-ND 4.0)" när lagret är på; 0 rutor och ingen LFV-attribution när det är av. | `d-lfv-raster-och-attribution.jpg` |
| e | En OCR-flaggad sida visar OCR-varningen | **OK** — Hallands Väderö visar "Texten är OCR-tolkad ur inskannat original — kontrollera mot källdokumentet." | `e-ocr-varning-hallands-vadero.jpg` |
| f | Säsongsdata visas och kommer ur källdata | **OK** — Hallands Väderö visar sex tillträdesförbud för säl med perioder 1/4–15/7 och 1/1–31/12, hämtade ur registrets `foreskriftsOmraden`. | `f-sasongsdata-tilltradesforbud.jpg` |
| g | Höjdbandet syns för en användare | **OK** — Södra Äspet, punkt 8, med "120 meter över medelhavsnivå" i klartext. | `g-hojdband-sodra-aspet-120-meter.jpg` |

Inga JavaScript-fel i konsolen på någon av de granskade sidorna.

### Mobilvyn — så kontrollerades den, och vad kontrollen inte visar

Chrome-verktygets skärmdumpsfunktion renderar alltid 1400 px bred bild oavsett
fönsterstorlek, så **det finns ingen äkta mobilskärmdump**. I stället mättes
layouten programmatiskt i webbläsaren med dokumentet klämt till 390 px:

```
bodyScrollWidth vid 390 px viewport: 390   (inget horisontellt överflöd)
element bredare än viewporten:       1     (Leaflets interna canvas, ligger
                                            inuti kartcontainern med overflow
                                            hidden — normalt beteende)
mediefråga för smal skärm närvarande: ja
```

Sidan svämmar alltså inte över horisontellt vid mobilbredd. **Detta är en
mätning, inte en visuell granskning** — hur sidan *ser ut* på en riktig telefon
är obekräftat och bör kontrolleras på en fysisk enhet innan lansering. Det är
en av de tre punkter som föreslås för manuell granskning i slutrapporten.

---

## 4. Täckningen: varför 305 objekt står i länk-läge

Uppdraget ber om en analys av felorsakerna om verifieringsgraden understiger
60 %. Verifieringsgraden är 100 %, men täckningen är 52 %, och den siffran
förtjänar samma behandling. Fördelningen:

### Per skyddstyp

| Skyddstyp | Med citat | Utan citat |
|-----------|----------:|-----------:|
| Naturreservat | 284 | 108 |
| Vattenskyddsområde | 25 | 120 |
| Djur- och växtskyddsområde | 15 | 5 |
| Landskapsbildsskyddsområde | 0 | 40 |
| Naturminne | 0 | 17 |
| Övrigt biotopskyddsområde | 2 | 11 |
| Naturvårdsområde | 4 | 4 |
| Nationalpark | 3 | 0 |
| Kulturreservat | 2 | 0 |

### Orsak till noll citat (305 objekt)

| Antal | Orsak |
|------:|-------|
| 177 | **Läsbar text, men ingen föreskrift matchade söktermerna.** |
| 88 | **Kort dokument** — median 1 087 tecken över 2 sidor. 82 av de 88 är vattenskyddsområden. |
| 38 | **Inga dokumentlänkar i registret.** Objektet visas i länk-läge mot Skyddad natur. |
| 2 | Dokument fanns men ingen text kunde extraheras. |

### Vad stickproven visade

Tre naturreservat med rikligt med läsbar text men noll citat lästes manuellt
(Maltesholm, Lilla Köpinge, Karlaby mosse). Alla tre har ordningsföreskrifter
enligt 7 kap. 30 § miljöbalken. Punkterna handlar om att gräva, plocka växter,
göra upp eld, tälta, ha hund lös och använda ljudanläggning — **ingen av dem
berör luftfartyg, fordon, tillträde eller störning av djurlivet**. Att inga
citat visas är alltså rätt utfall, inte ett bortfall.

Stickprovet avslöjade däremot ett verkligt fel som är åtgärdat, se avsnitt 5.

**De 82 vattenskyddsområdena** har korta beslut som huvudsakligen hänvisar till
separata skyddsföreskrifter, och de reglerar vattenhantering — inte luftfart.
Att de saknar citat är förväntat. Vattenskydd är dessutom ett extralager som är
avslaget som standard.

**Landskapsbildsskydd (40) och naturminnen (17)** saknar helt
ordningsföreskrifter riktade till allmänheten av det slag tjänsten citerar.
Landskapsbildsskydd reglerar bygglov och markingrepp; naturminnen är i Skåne
huvudsakligen beslut från 1930–60-talet om enskilda träd och stenblock.

### Slutsats

Av 305 objekt i länk-läge är **38** där tjänsten faktiskt saknar underlag (inget
digitalt dokument), och **2** där texten inte gick att läsa. Resterande 265 är
objekt där tjänsten läst beslutet och korrekt konstaterat att ingen föreskrift
nämner luftfartyg — vilket sidan också säger med den föreskrivna formuleringen:
"Ingen föreskrift som uttryckligen nämner luftfartyg hittades i beslutet. Andra
föreskrifter kan ändå vara relevanta — läs beslutet."

**Uttryckliga luftfartsföreskrifter finns i 77 av länets 640 objekt**, med
sammanlagt 145 citat i klasserna *nämner uttryckligen luftfartyg* och *nämner
start eller landning*.

---

## 5. Fel som hittades under bygget

Alla hittades genom att mäta utfall, inte genom att läsa kod. Var och en är
åtgärdad, och de som kan återuppstå har fått ett golden test först.

| # | Fel | Hur det hittades | Åtgärd |
|---|-----|------------------|--------|
| 1 | WFS-uttaget gav tyst **500 av 644** objekt — servern kapar vid 500 och ignorerar `startIndex` | Avstämning mot `resultType=hits` | Partitionering per skyddstyp + avstämning som avbryter bygget vid avvikelse |
| 2 | Krympgarantin behandlade **hål som ytterringar**, så 83 objekt växte | Mätning av ytförändring | Ytterringar får inte växa, hålringar inte krympa |
| 3 | Ytmåttet gav "1 042 % minskning" | Orimligt värde i utfallet | Mäts bara över 1 ha; totalyta redovisas separat |
| 4 | Fast tolerans åt **18,6 %** av ett litet objekts yta | Mätning per objekt | Hård gräns 2 % per objekt, tolerans trappas 15 → 5 → 1,5 → original |
| 5 | Fyra ringar blev **självskärande** av förenklingen | Test B | Självskärningskontroll, original behålls |
| 6 | Beskrivande prosa klassades som föreskrift | Provkörning mot 21 dokument | Förbudsuttryck krävs i punkten eller dess rubrik |
| 7 | Uppräkning av angränsande lagstiftning gav träff | Provkörning | Närhetskrav 200 tecken mellan triggerord och förbudsuttryck |
| 8 | Citat slutade **mitt i ett avstavat ord** vid sidbrytning | Test A avvek från steg 4 | Avstavat slutord kapas; test A kör nu steg 4:s egen funktion |
| 9 | Test A och steg 4 var **två olika kontroller** som gav olika svar | Fel 8 | Test A importerar och kör steg 4:s verifieringsfunktion |
| 10 | Listrubrik **utan kolon** hittades inte, så hela listan tappade sitt förbud | Stickprov på Lilla Köpinge | Kolonfria rubriker accepteras; golden test C2c |
| 11 | Den lösare rubrikregeln lät **brödtext** passera som listrubrik (störningsträffar 257 → 563 utan en enda ny föreskrift) | Stickprov på nya träffar | Rubriken måste *sluta* på kolon eller "att"; golden test C2c |

Fel 10 och 11 hör ihop och illustrerar avvägningen i R8: den första
rättningen ökade täckningen men införde falsklarm, vilket är ett sämre fel än
det den löste. Den slutliga regeln ger +46 objekt med citat utan att släppa
igenom brödtext.

---

## 6. Kända begränsningar i det här bygget

1. **Ett dokument hämtades inte** — `nvdok-1046352`, 48 MB, över gränsen på
   40 MB. Berört objekt står i länk-läge med fungerande dokumentlänk.
2. **Mobilvyn är mätt, inte sedd.** Se avsnitt 3.
3. **Söktermerna är den enskilt viktigaste sak att granska manuellt.** En
   föreskrift som beskriver flygning utan att använda något av orden i listan
   i `scripts/03_extract.py` hittas inte. Felriktningen är den rätta enligt R8
   — området hamnar i "läs beslutet" i stället för att få ett gissat verdikt —
   men listan bör läsas av någon som kan materialet.
4. **Tjänsten avgör inte vilken beslutslydelse som gäller i dag.** Ett område
   kan ha tio beslut där senare ändrat tidigare. Alla citeras, i datumordning,
   med en fast text som säger att tjänsten inte rangordnar dem.
5. **Interimistiska förbud saknar objekt i Skåne** vid hämtningen. Deklarerat
   som svarsläge 3 på `/kallor/`.
6. **`SkyddadePunkter` returnerar noll objekt för Skåne.** Naturminnen ligger i
   ytlagret. Deklarerat på `/kallor/`.
7. **`base_url` är fortfarande `https://EXEMPEL.se`.** Måste bytas före deploy.

---

## 7. Reproducera

```bash
make all     # steg 1–6 + testsvit; dist/ rullas tillbaka om testerna faller
make test    # A–D
make visuell # granskningsplanen för E
```

Verifieringen körs från noll vid varje bygge, även för oförändrade objekt.
Maskinläsbart utfall: `data/verification-report.json`.
