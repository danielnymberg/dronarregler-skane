# DECISIONS.md

Varje icke-trivialt val som fattats under bygget, med motivering. Det här är
granskningsytan: läs den innan du granskar koden, så syns det vad som är
medvetet och vad som är slump.

Kronologisk ordning. Datum är byggdatum (2026-07-27).

---

## Fas 0 — svar från beställaren

| # | Fråga | Svar | Följd |
|---|-------|------|-------|
| 1 | Repo | **Publikt GitHub-repo** | `~/dany/dronarregler-skane` → `github.com/danielnymberg/dronarregler-skane`, publikt |
| 2 | Deploy-mål | **Cloudflare Pages** | `DEPLOY.md` skriven för CF Pages; ingen deploy utförd utan uttryckligt godkännande |
| 3 | Domän | *obesvarad → default* | `base_url` = `https://EXEMPEL.se`, TODO-markerad, en (1) variabel i `config.json` |
| 4 | Sajtnamn | **"Drönarkoll Skåne"** | `site_name` i `config.json` |
| 5 | Felrapportering | **GitHub Issues** | Förifylld issue med NVRID, svarsläge och manifestversion |
| 6 | Schemalagd Action | *obesvarad → default* | Action skapad men **cron inaktiverad**; endast `workflow_dispatch` |

**Miljökontroll i Fas 0:** samtliga datakällor nåbara (Naturvårdsverkets WMS/WFS,
Skyddad natur, LFV DAIM, metadatakatalogen, Metria, Länsstyrelsen, LFV:s
drönarkarta). `gh` inloggat med `repo`+`workflow`. Python 3.14, Node 24,
`pdftotext`/`pdfinfo`/`pdftoppm`, `tesseract` med svenskt språkpaket (`swe`)
fanns redan. GDAL saknades — se D-07. Ingen nätverksblockering, ingen paus
behövdes.

---

## Datakällor och ingest

### D-01 — WFS i stället för WMS för kärndata
Naturvårdsverkets `naturvardsregistret` publicerar både WMS (raster) och WFS
2.0 (vektor). WFS valdes eftersom R3 kräver att varje polygon är spårbar till
ett API-svar och punkt-i-polygon kräver vektorgeometri. WMS hade tvingat fram
egna geometrier — precis det R3 förbjuder.
Endpoint: `https://geodata.naturvardsverket.se/naturvardsregistret/wfs`,
`outputFormat=GEOJSON`, `srsName=urn:ogc:def:crs:EPSG::4326`.

### D-02 — Serverns 500-tak kringgås med partitionering per skyddstyp, inte paginering
Tjänsten har `CountDefault=500`, respekterar inte `startIndex` (alla varianter
av skiftläge testade returnerar 0) och kapar även `count=2000` vid 500.
Naiv paginering gav tyst **500 av 644** objekt — en lucka som hade sett ut som
"inga fler områden finns". Lösning: ett uttag per `SKYDDSTYP` (varje delmängd
ligger under taket) plus avstämning mot `resultType=hits` för hela länet.
Summan av delmängderna är exakt 644 = totalen.

**Bygget avbryter hellre än att släppa igenom en lucka:** om en delmängd når
500 objekt, eller om `GetFeature` ger ett annat antal än `hits` sa, kastas ett
undantag. Se `fetch_layer()` i `scripts/01_ingest.py`.

Detta var den första riktiga buggen i bygget och den upptäcktes bara för att
antalen stämdes av mot en oberoende räkning. Avstämningen är kvar permanent.

### D-03 — `resultType=hits` måste köras utan `outputFormat=GEOJSON`
Med GEOJSON svarar tjänsten med en tom `FeatureCollection` i stället för
`numberMatched`. Parametern utelämnas därför för hits-anrop.

### D-04 — Dokumentlänkar hämtas ur Kartverktyget Skyddad natur, inte ur WFS
WFS-schemat innehåller inga dokumentreferenser. Kartverktyget Skyddad natur har
ett odokumenterat men öppet REST-API som hittades genom att läsa dess
klientbundle (`all-classes.js`):

- `rest/search/execute?nvrid=…&paramtype=NVR` → id-format `{nvrid}#{status}@NVR`
- `rest/detail/{urlkodat-id}` → `beslut[].beslutsDokument[].fileUrl` samt
  `foreskriftsOmraden[]` med `franDatum`/`tillDatum`

Dokument-URL:erna pekar på `geodata.naturvardsverket.se/handlingar/rest/dokument/{id}`.
`:443/` i svaret normaliseras bort.

Att API:et är odokumenterat är en känd risk: ändras det slutar dokumentlänkarna
fungera. Det ger inte tysta fel — dokumenten saknas och områdena hamnar i
länk-läge mot Skyddad natur-sidan, vilket är rätt failsafe-beteende.

### D-05 — Kommunala naturreservat härleds ur beslutsmyndighet
NVR har ingen egen `SKYDDSTYP` för kommunala naturreservat i Skåne
(`"Naturreservat (kommunalt beslutat)"` ger 0 träffar), trots att WMS-tjänsten
har ett separat lager `Naturreservat_kommunalt`. De skiljs därför ut på
`BESLUTSMYNDIGHET` som börjar på "Kommun". Alternativet — att påstå att Skåne
saknar kommunala naturreservat — hade varit fel.

### D-06 — `SkyddadePunkter` är tomt för Skåne; det deklareras i stället för att döljas
Punktlagret returnerar 0 objekt för Skåne län. Naturminnen finns i ytlagret
(17 st). Detta står som en känd lucka på `/kallor/` med hämtningsdatum. Ett tomt
lager får inte se ut som frånvaro av regler (R2, svarsläge 3).

### D-07 — Ingen GDAL, ingen shapely — ren stdlib
GDAL saknades på maskinen. I stället för att införa ett tungt binärberoende för
ett fåtal operationer implementerades förenkling, ytberäkning, punkt-i-polygon
och självskärningskontroll direkt i `scripts/lib/geom.py`. Skäl: pipelinen ska
kunna köras om på vilken maskin som helst utan installationssteg, och
geometrikoden är testbar (test B) och läsbar. Kostnaden är att koden är
långsammare än GDAL — irrelevant vid 644 objekt.

### D-08 — Throttling: 1,0 s mot dokumentservern, 0,5 s mot detalj-API:et
Uppdraget kräver ≥1 s mellan dokumenthämtningar mot samma server. Det gäller
`geodata.naturvardsverket.se` (WFS + dokumentarkiv). Detalj-API:et ligger på en
annan värd (`skyddadnatur.naturvardsverket.se`) och belastas med lätta
JSON-anrop; där används 0,5 s. Throttlingen är per värdnamn i
`lib/common.py::_throttle`. User-Agent innehåller repo-URL:en.

---

## Dokumenturval och OCR

### D-09 — Bara föreskriftsbärande dokument laddas ned
1 211 dokumentlänkar fanns. Skötselplaner, kartbilagor och visningsbilder bär
inte föreskrifter men är ofta de största filerna. Urvalet görs på filnamn:
dokument som matchar `beslut|föreskrift|förordnand|kungörels|stadga|bildand|
utvidgn|ändr|reservatsbeslut|tillträdesförbud|interimist` laddas ned, resten
hoppas över — **utom** när ett objekt inte har något sådant dokument alls, då
laddas allt utom bilder som fallback.

Risken är att en föreskrift ligger i en fil med oväntat namn. Den risken
hanteras genom transparens i stället för gissning: varje hoppat dokument listas
med orsak på områdets sida under "Dokument som inte lästs av tjänsten", och
regeln står på `/kallor/`. Användaren kan alltså se exakt vad tjänsten *inte*
läst och klicka sig till det.

### D-10 — Steg 2 delades i throttlad nedladdning och parallell textextraktion
Första implementationen gjorde nedladdning och OCR i samma serieloop. Mätt
takt: 4 dokument på 5 minuter — ~18 timmar för hela länet, eftersom ~70 % av
besluten är inskannade original från 1970–90-talet. Uppdragets tidsdisciplin
säger att ett delproblem som äter oproportionerlig tid ska lösas eller
degraderas.

Lösning: fas 2a laddar ned seriellt och throttlat (nätbundet), fas 2b kör
textextraktion i en processpool över `cpu_count()-2` kärnor (CPU-bundet).
OCR-parametrar sänktes från 300 → 200 dpi och `--psm 1` → `--psm 3 --oem 1`;
200 dpi räcker gott för maskinskriven text och är ~2× snabbare.
Nedladdningen är fortfarande throttlad — parallelliseringen rör inte nätet.

### D-11 — OCR hoppas över för dokument längre än 60 sidor
En handfull skötselplansliknande beslutsbilagor är hundratals sidor. De flaggas
med varning i manifestet i stället för att blockera bygget. Områdena hamnar i
länk-läge om inget annat dokument gav citat.

---

## Extraktion och verifiering

### D-12 — Deterministisk extraktion i stället för fri LLM-extraktion
Uppdraget tillåter LLM-extraktion. Den valdes ändå **bort** till förmån för
deterministisk segmentering, av tre skäl:

1. **Ordagrannhet per konstruktion.** Ett citat som skärs ut som en
   sammanhängande delsträng ur dokumentets text *kan* inte vara omskrivet. En
   språkmodell kan producera text som ser ut som ett citat men som skiljer sig
   på ett ord — och just den felklassen är en av de tre som uppdraget säger att
   tjänsten ska vara immun mot.
2. **Reproducerbarhet.** Samma dokument ger samma citat vid varje ombygge.
   Månadsuppdateringens diff visar då verkliga ändringar i besluten, inte brus
   från modellen.
3. **Granskningsbarhet.** Reglerna står som läsbara reguljära uttryck i
   `scripts/03_extract.py`. Daniel kan se exakt vad som fångas och vad som inte
   gör det.

Verifieringen i steg 4 är oberoende och skulle ha fångat felen ändå — men R7
säger att en icke-verifierad utsaga inte får nå `dist/`, inte att det räcker att
kunna upptäcka den i efterhand. Det här är bältet *och* hängslena.

**Kostnaden:** deterministiska nyckelord missar föreskrifter som beskriver
flygning utan att använda något av orden i listan. Det ger falska negativ, som
enligt R8 är rätt felriktning — området hamnar i "läs beslutet" i stället för
att få ett gissat verdikt. Ordlistan står i `scripts/03_extract.py` och är den
enskilt viktigaste saken att granska manuellt.

### D-13 — Klassificering är en etikett, inte en bedömning
De fem klasserna sätts med nyckelordsregler och prioritetsordning: uttrycklig
luftfart > start/landning > motordrivet fordon > störning av djurliv > annat.
`konfidens` är "hög" när segmentet också innehåller ett förbuds- eller
tillståndsuttryck, annars "medel" — det säger något om hur säkert segmentet är
en föreskrift, aldrig något om vad föreskriften betyder.

De användarvända etiketterna formulerades om så att de beskriver citatet i
stället för att påstå en rättsföljd: `uttryckligt-luftfartygsförbud` visas som
"Föreskrift som uttryckligen nämner luftfartyg". Nyckelvärdena i JSON behåller
uppdragets vokabulär för maskinläsning.

### D-13b — Föreskriftsinledningen hämtas som en egen delsträng
I svenska reservatsbeslut står förbudsordet i blockets rubrik, inte i
punkterna: `Det är förbjudet att inom reservatet:` följt av `6. framföra cykel
eller motordrivet fordon annat än på allmän väg,`. Punkten ensam saknar både
sitt förbud och sitt sammanhang — vid provkörning missades den helt.

Att limma ihop rubrik + punkt till en textmassa hade brutit R1 (det blir en
konstruerad text som inte står så i beslutet). Lösningen är att hämta rubriken
som en **egen sammanhängande delsträng** ur samma dokument, verifiera den
separat i steg 4, och visa den som ett eget stycke ovanför punkten. Rubriken
måste dessutom stå *före* punkten på sidan, annars kasseras den.

Långa föreskriftslistor löper över sidbrytningar, så rubriken får hämtas från
föregående sida — då verifieras den mot den sidan, och sidnumret följer med i
`inledning_sidnummer`.

### D-13c — Tre klasser av falsklarm hittade vid provkörning och åtgärdade
Extraktionen provkördes mot de 21 dokument som hunnit textextraheras innan
resten var klara. Tre systematiska falsklarm syntes direkt:

1. **Beskrivande prosa klassad som föreskrift.** "Vid en fågelinventering av
   området har totalt 36 arter noterats" hamnade i `störningsförbud-djurliv`.
   Det är text ur områdesbeskrivningen, inte en föreskrift.
   *Åtgärd:* ett förbuds- eller tillståndsuttryck krävs nu i punkten eller i
   dess verifierade rubrik. Utan det kasseras träffen.

2. **Uppräkningar av angränsande lagstiftning.** En punktlista där ett stycke
   nämnde "förbud mot körning i terräng" och ett annat "flygning med
   flygskärm" gav en träff, trots att orden inte hade något med varandra att
   göra. Det är exakt det trubbiga larm R8 varnar för.
   *Åtgärd:* närhetskrav på 200 tecken mellan triggerordet och
   förbudsuttrycket — såvida inte förbudet kommer ur den verifierade rubriken.

3. **Svaga luftfartstermer.** "flyga", "flygning" och "flyger" träffar lika
   gärna text om fågellivet som om drönare.
   *Åtgärd:* termerna delades i starka (drönare, UAS, UAV, luftfartyg,
   luftfarkost, modellflyg, helikopter, flygplan — kan bara syfta på farkoster)
   och svaga. Starka termer visas alltid; svaga bara i föreskriftssammanhang.

Provkörningen visade också ett fynd som bekräftar varför ordagrannhet är rätt
princip: ett beslut förbjuder att "flyga fjärrmanövrerat obemannat luftfartyg
exempelvis drönare **på en höjd understigande 120 meter över medelhavsnivå**".
En sammanfattning hade med stor sannolikhet tappat höjdbandet — och det är
just den tredje felklassen uppdraget säger att tjänsten ska vara immun mot.
Ordagrant citat bevarar det utan att någon behöver tänka på saken.

### D-13d — Konfidens säger var förbudsordet står, inget annat
- **hög** — förbudsuttrycket står i punkten själv
- **medel** — det står i den verifierade rubriken, eller punkten är numrerad
  (en numrerad punkt i ett myndighetsbeslut är strukturellt en
  föreskriftspunkt även när textextraktionen tappat rubriken)
- **låg** — varken eller

Citat med låg konfidens visas fortfarande, men får en egen etikett: "står i
beslutstexten utan att tjänsten kunnat knyta det till en föreskriftspunkt". Det
träffar i praktiken beslutens *skäl* — meningar som "Drönare och andra
motordrivna luftfarkoster blir allt vanligare" — som är relevant läsning men
inte föreskrifter. Att kalla dem föreskrifter hade varit fel; att dölja dem
hade varit att säga mindre än källan gör.

### D-14 — Citat kapas vid meningsgräns, aldrig mitt i
Segment kan vara långa. Citat kapas vid 700 tecken, alltid vid närmaste
meningsslut inom gränsen, och alltid som ett prefix av segmentet. Ett kapat
citat är därmed fortfarande en exakt delsträng av källtexten.

### D-15 — Normaliseringen inför strängmatchning
Sju transformationer, alla dokumenterade i `scripts/lib/textnorm.py` och
återgivna i `verification-report.json`: NFKC, ligaturexpansion, typografiska
citattecken/apostrofer, streckvarianter, borttagning av mjukt bindestreck och
nollbreddstecken, ihopslagning av avstavning över radbryt, whitespace-kollaps.
**Efter** normalisering krävs exakt delsträngsförekomst. Ingen fuzzy-matchning,
ingen likhetströskel, ingen skiftlägesokänslighet.

Citat kortare än 25 tecken underkänns automatiskt — de är för korta för att en
träff ska betyda något.

### D-16 — Verifieringen kräver träff på *angiven sida*
Ett citat som finns i dokumentet men inte på den sida som anges underkänns med
egen felorsak. Sidnumret är en del av källhänvisningen; stämmer det inte är
hänvisningen fel även om texten finns.

### D-17 — Verifieringen körs alltid från noll
Även för oförändrade objekt, vid varje bygge. Det kostar sekunder och skyddar
mot att en regression i normaliseringen släpper igenom gamla citat.

---

## Geometri och rendering

### D-18 — Förenkling med krympgaranti, tolerans 15 m
Douglas–Peucker med 15 m utgångstolerans för visningsgeometrin. R3 tillåter
förenkling som "krymper eller behåller" ytan. DP garanterar inte det, så
resultatet kontrolleras mot originalet.

Originalgeometrin ligger oförenklad i `data/omraden/{nvrid}.json` och är den
enda som används för punkt-i-polygon — både i testerna och i webbklienten.

### D-18b — Fyra fel i krympgarantin, funna genom att mäta i stället för att anta
Första implementationen såg korrekt ut och var det inte. Mätning av utfallet
avslöjade fyra separata fel, alla åtgärdade:

1. **Hål behandlades som ytterringar.** Garantin "ringen får inte växa"
   tillämpades på alla ringar. Men nettoytan är ytterring *minus* hål — en
   krympt hålring gör alltså ytan **större**. Resultatet: 83 objekt växte.
   *Åtgärd:* ytterringar får inte bli större, hålringar inte mindre.
2. **Mätetalet var meningslöst för små ytor.** "Största ytminskning" landade på
   1 042 % — matematiskt omöjligt. Orsaken var objekt vars hål nästan äter upp
   ytterringen: nettoytan är nära noll och kvoten exploderar utan att geometrin
   flyttat sig mer än någon meter.
   *Åtgärd:* relativ minskning mäts bara för objekt över 1 ha, och totalytan
   före/efter redovisas separat (−0,05 % för hela datamängden).
3. **En fast tolerans slog orimligt mot små objekt.** Ett objekt tappade 18,6 %
   av sin yta — 15 m är rimligt för ett stort reservat men inte för ett litet,
   avlångt. *Åtgärd:* hård gräns på **2 % ytförlust per objekt**; toleransen
   trappas ned (15 → 5 → 1,5 m) tills kravet är uppfyllt, och i sista hand
   behålls originalet. Utfall: 579 objekt vid 15 m, 50 vid 5 m, 6 vid 1,5 m,
   5 oförenklade.
4. **Fyra ringar blev självskärande.** DP på en sluten ring kan låta två
   förenklade segment korsa varandra. Självskärande polygoner renderas
   oförutsägbart. *Åtgärd:* självskärningskontroll efter förenkling; slår den
   till behålls originalringen. Test B faller på självskärning.

Koordinaterna avrundas dessutom till 5 decimaler (~1 m) **innan** garantin
kontrolleras, så att kravet gäller exakt de siffror som hamnar i filen och inte
ett mellanled.

Lärdomen är inte att förenkling är svårt, utan att en garanti som inte mäts
inte är en garanti. Alla fyra felen fanns i kod som såg rätt ut.

### D-19 — Flera WFS-rader med samma NVRID slås ihop till en MultiPolygon
644 WFS-rader gav 640 unika NVRID. Delarna läggs i samma MultiPolygon utan att
någon ny geometri skapas.

### D-20 — Positionssvar: bbox-index → per-områdesfil → exakt punkt-i-polygon
Att skicka hela originalgeometrin (3,4 MB) till klienten för varje
positionskontroll är onödigt på mobil. I stället laddas ett litet
`bbox-index.json`, kandidaterna filtreras på omslutande rektangel, och bara
kandidaternas `omraden/{nvrid}.json` hämtas för exakt punkt-i-polygon mot
**originalgeometrin**. Bbox-filtret kan aldrig ge falska negativ: en punkt
utanför en geometris omslutande rektangel ligger med säkerhet utanför
geometrin.

### D-21 — Avstånd till närliggande områden beräknas mot förenklad geometri
Avståndet mäts till närmaste hörn i visningsgeometrin, vilket ger ett fel på
högst förenklingstoleransen. Det anges i gränssnittet ("beräknade mot den
förenklade kartgeometrin, tolerans 15 m, ungefärliga"). Ett avstånd är en
beräkning, inte en regelutsaga — men det ska ändå inte påstå mer precision än
det har.

---

## Sajten

### D-22 — Statisk HTML från Python i stället för Astro
Uppdraget lämnar teknikvalet fritt. Ett statiskt sitegenerator-ramverk hade
lagt till Node-beroenden, en byggkedja och en mall-DSL utan att lösa något som
inte redan var löst: sidorna är renderade en gång ur `data/`, allt innehåll är
serversidigt statiskt, och `dist/` är ren HTML/CSS/JS. Hela sajtbyggaren är en
fil på ~450 rader som Daniel kan läsa. Färre rörliga delar = färre sätt att
tyst gå sönder.

### D-23 — Leaflet vendoras lokalt, ingen CDN
R5 förbjuder spårning. Ett CDN-anrop läcker besökarens IP till tredje part vid
varje sidladdning. Leaflet 1.9.4 ligger därför i `site/vendor/leaflet/`.

### D-24 — Bakgrundskarta: OpenStreetMap standard-tiles
Behövs för orientering. OSM:s standardtiles är gratis och kräver ingen nyckel;
attributionen är obligatorisk och finns. **Detta är den enda tredjepartsresurs
sajten laddar vid drift**, vid sidan av LFV-rastret.

Två saker att veta: OSM:s användarpolicy avråder från tung trafik, och tiles är
tredjepartsanrop. Vid växande trafik bör bakgrundskartan bytas mot en nyckelbaserad
leverantör eller egna tiles. Polygonlagret är oberoende av bakgrundskartan och
fungerar även om tiles uteblir.

### D-25 — LFV: layerurval och att lagret är default på
LFV:s GeoServer publicerar inte ett samlat "drönarlager". De luftrumslager som
är relevanta för drönarflygning valdes: `mais:CTR, mais:TIZ, mais:TIA,
mais:ATZ, mais:RSTA, mais:DNGA`. Hämtas som WMS 1.1.1 PNG i EPSG:3857 direkt
från `daim.lfv.se` med tom `styles=`-parameter (LFV:s egen styling, ingen av
vår). Attribution "© LFV (CC BY-NC-ND 4.0)" i kartans attributionsfält.
Licensen är bekräftad i LFV:s egen GetCapabilities (`AccessConstraints`).

Lagret är **default på** enligt uppdraget. Ingen LFV-data cachas, lagras,
stylas om eller används i beräkningar. Test C6 letar aktivt efter kod som
skulle bryta mot detta.

### D-26 — Vattenskydd, landskapsbildsskydd och biotopskydd är extralager, default av
Uppdraget gör vattenskydd och landskapsbildsskydd valfria med default av.
"Övrigt biotopskyddsområde" nämns inte i scope-listan alls men finns i länets
data (13 objekt); det behandlas likadant — med i databasen, av på kartan,
deklarerat på `/kallor/`. Kärnlagret laddas alltid utan att användaren behöver
slå på något.

### D-27 — Flera beslut per område: tjänsten rangordnar dem inte
Ett område kan ha tio beslut där senare beslut ändrat föreskrifter i tidigare.
Att avgöra vilken lydelse som gäller i dag är en rättslig bedömning — precis
det tjänsten inte gör. Citaten grupperas därför per källdokument, varje
dokument namnges med sitt eget namn och datum, och en fast text säger att
tjänsten inte avgör vilken lydelse som gäller. Detta är ett medvetet val att
visa mer och påstå mindre.

### D-28 — Områden utan luftfartsträffar visas som "reglerat område", inte som fria
Ett naturreservat där ingen föreskrift nämner luftfartyg är fortfarande ett
naturreservat med föreskrifter. Sidan visar därför svarsläge 1 ("Reglerat
område — läs beslutet") plus den föreskrivna formuleringen "Ingen föreskrift
som uttryckligen nämner luftfartyg hittades i beslutet. Andra föreskrifter kan
ändå vara relevanta — läs beslutet." Svarsläge 2 används **bara** för
positionssvar utanför alla kända områden.

---

## Tester och grindar

### D-29 — `dist/` skrivs bara bakom en grön testsvit, med rullbakåt
`make all` tar en kopia av föregående `dist/`, bygger nytt, kör testsviten och
återställer den gamla katalogen om testerna faller. Ett trasigt bygge kan
därmed inte publicera sig självt — och en gammal sajt ser gammal ut i stället
för att försvinna (R4).

### D-30 — Anti-ESMH-punkten är verifierad mot rådata, inte vald på känsla
Golden-punkten (12.5560, 56.2000) i Höganäs tätort kontrollerades mot
Naturvårdsverkets WFS-svar innan den skrevs in: noll skyddsytor täcker den, och
närmaste skyddade område (naturreservatet Ärtan och Bönan) ligger 940 m bort.
Motiveringen står i `tests/golden.json`.

### D-31 — Testet för förbjudna ord tittar bort från citatblock
Ett myndighetsbeslut kan innehålla ordet "tillåtet". Testet maskerar därför
`<blockquote>`-block och HTML-taggar innan det söker, så att källtext inte
utlöser falsklarm medan tjänstens egen text fortfarande granskas.

### D-32 — Säsongstestet kontrollerar att datumen *inte* är hårdkodade
Utöver att datumen syns på sidan verifieras att samma strängar inte förekommer
som literaler i sajtbyggaren. Ett test som bara kollar att "1/3" står på sidan
hade passerat även om datumet var inskrivet för hand.

---

### D-33 — Avstavat slutord kapas, och test A blev samma kontroll som steg 4
Ett citat slutade "…eller styr-" — ett ord brutet vid sidbrytningen. Steg 4
godkände det (matchning mot sidan, där bindestrecket står kvar) medan test A
underkände det (matchning mot hela dokumentet, där normaliseringen slår ihop
avstavningen över sidbrytningen). Två kontroller, två svar.

Två åtgärder:
1. Extraktionen kapar bort ett avslutande avstavat ord. Citatet blir läsbart
   och matchar både sidan och hela dokumentet. Det är fortfarande en
   sammanhängande delsträng — bara slutet kapas.
2. **Test A importerar och kör steg 4:s egen funktion** i stället för att
   implementera om den. När grinden och dess kontroll inte är samma kontroll
   vet man inte längre vilken som gäller.

### D-34 — Beslutens skäl citeras inte som föreskrifter
Ett beslut återger ofta sina egna föreskrifter i skälen: "Enligt gällande
föreskrifterna är det bland annat förbjudet att…". Den återgivningen syntes på
områdessidorna som en föreskrift, trots att den riktiga punkten citeras separat
från föreskriftsavsnittet. Segment vars **första rad** är en känd icke-
föreskriftsrubrik — "Skäl för beslut", "Ärendets handläggning", "Länsstyrelsens
bedömning", "Upplysningar", "Hur man överklagar" m.fl. — utesluts. Matchningen
sker på avsnittsrubriken, inte på brödtexten, så en föreskrift som råkar
innehålla ordet "skäl" berörs inte. Utfall: 1 084 → 1 051 citat.

### D-35 — Listrubrik utan kolon: rätt problem, fel första lösning
Stickprov på tre reservat utan citat visade ett verkligt bortfall: rubriken
"Utöver föreskrifter och förbud i andra lagar och författningar är det
förbjudet att" står ofta **utan avslutande kolon**, direkt följd av punkt 1.
Sökningen krävde kolon och tappade därför förbudet för hela listan — punkten om
att framföra motordrivet fordon i naturreservatet Maltesholm försvann helt.

Första rättningen tog bort kolonkravet och accepterade varje rad som innehöll
ett förbudsord. Täckningen steg till 362 objekt — och störningsträffarna sköt
från 257 till 563 utan att en enda ny föreskrift hittats. Meningar som
"…diskuterat möjligheten att lägga ett helårs beträdnadsförbud på revlarna"
accepterades som listrubrik, varpå beslutens skäl började klassas som
föreskrifter. Nettoeffekten var sämre än problemet: R8 säger att trubbiga
larm är ett fel, inte bara ett skönhetsfel.

Slutlig regel: rubriken måste både **innehålla** ett förbuds- eller
tillståndsuttryck och **sluta** som en inledning till en uppräkning — på kolon,
eller på ordet "att". Avslutskravet är det som skiljer rubrik från brödtext.
Rubriken får sträcka sig över två rader och över en sidbrytning.

Utfall: 289 → 335 objekt med citat, utan att brödtext släpps igenom. Båda
felriktningarna har golden tests (C2c): två objekt vars kolonfria rubrik måste
hittas, och en regel som kräver att *varje* verifierad inledning slutar som en
listrubrik.

### D-36 — Etiketterna beskriver citatet, inte rättsföljden
Vid visuell granskning stod "Föreskrift om motordrivet fordon" på ett citat som
i själva verket sa "frambringa farkost" — träffen kom från ordet *farkost*, inte
från *motordrivet*. Etiketterna skrevs om så att de beskriver vad citatet
nämner: "Nämner fordon eller farkost — möjligen relevant", "Nämner uttryckligen
luftfartyg" och så vidare. En etikett får inte påstå mer än citatet gör.

### D-37 — Indragningen från PDF-layouten tas bort vid visning
`pdftotext -layout` behåller sidans indrag, vilket gör citatet svårläst i en
smal textspalt. Radernas gemensamma inledande blanksteg tas bort **vid
visning**. Orden och radbrytningarna är oförändrade, den lagrade strängen i
`data/` är orörd, och verifieringens normalisering kollapsar ändå all
whitespace — så ordagrannheten är opåverkad.

### D-38 — Deployad till Cloudflare Pages 2026-07-27 på Daniels instruktion
`https://dronarkoll-skane.pages.dev`. Token fanns redan som
`CLOUDFLARE_API_TOKEN` i MMDN:s miljö — inga hemligheter passerade chatten.

`base_url` sattes till pages.dev-domänen innan bygget, så canonical-URL:er,
sitemap och OG-metadata pekar rätt. När en skarp domän bestäms är det
fortfarande den enda variabel som behöver ändras.

Direkt efter deploy svarade en del rutter 522 (kallstart vid edgen). Det satte
sig av sig självt inom ett par minuter; samtliga rutter verifierades därefter
som 200, och `_headers` slog igenom (`referrer-policy: no-referrer`,
`permissions-policy: geolocation=(self), interest-cohort=()`,
`x-content-type-options: nosniff`).

---

## Andra omgången: rikstäckning och kartapp (2026-07-28)

Daniel gav tre invändningar: för många ord om vad tjänsten *inte* vet, enkla
saker onödigt komplicerade, och två frågor — vad hindrar hela Sverige, och hur
blir gränssnittet mer karta och mindre webbsida. Beslut: GUI och Sverige i en
körning, hård textstädning.

### D-39 — Mätning först, försvar sedan
Invändningen om brasklappar kontrollerades i stället för att bemötas: på
Kullabergssidan stod **523 ord citat mot 788 ord runtomkring**, kvot 1,5:1.
Raden "maskinellt verifierad ordagrant mot källdokumentet" stod tio gånger,
konfidenschipet tio gånger, och FLERA_BESLUT-texten två gånger på samma sida.
Daniel hade rätt, och det mesta av överskottet var sådant jag lagt till på eget
bevåg — inte sådant uppdraget krävde.

Efter städning: **1,09:1**. Kvar är i praktiken bara ansvarstexten (§7, får inte
kortas) och LFV-raden (§5 punkt 2), båda en gång per sida.

### D-40 — Klassificering och konfidens bort från ytan, kvar i datan
`klassificering` och `konfidens` var mina egna påhäng. De var korrekta men de
var metadata, och på en sida med tio citat blev de tio upprepningar av något
läsaren inte behöver. De finns kvar i `data/omraden/{nvrid}.json` för
granskning. Substansen på sidan är citatet, dess sidnummer och länken.

### D-41 — Listrubriken grupperas
Föreskriftsinledningen renderades före varje punkt, vilket gav "Utöver
föreskrifter och förbud i andra lagar och författningar är det förbjudet att:"
fem gånger på samma skärm. Citaten grupperas nu på (dokument, inledning) och
rubriken skrivs en gång per grupp — precis som i beslutet.

### D-42 — Startsidan är en app, områdessidorna är dokument
Två ytor som felaktigt såg likadana ut. Startsidan: kartan fyller skärmen, GPS
begärs direkt vid laddning, svaret kommer som en panel över kartan, ingen
brödtext. Områdessidorna förblir läsbara dokument — de är SEO-bärarna och ska
gå att läsa utan JavaScript. Förklaringstexten flyttade till `/om/`.

### D-43 — Rikstäckning krävde tre nivåers partitionering
6 027 naturreservat i landet mot 393 i Skåne. Skyddstyp räcker inte, län räcker
inte (Västra Götaland 546, Norrbotten 542 — båda över serverns tak på 500).
Tredje nivån är rekursiv bisektion på beslutsdatum, som alltid terminerar och
inte kräver något externt register.

Två gränsfall dök upp i verkligheten:
- **414 av 500 naturminnen har tomt LAN-fält** och fångas inte av något
  länsfilter. Länsuppdelningen faller därför tillbaka på datumbisektion för hela
  mängden när summan inte stämmer, i stället för att avbryta.
- **124 fågelskyddsområden i Blekinge delar beslutsdatum 1985** — ett beslut som
  bildade många små öar. Datum kan inte dela dem; cellen hämtas i ett stycke,
  vilket ryms under serverns tak.

### D-44 — GEOJSON-formatet är trasigt; ESRIGEOJSON används i stället
Det allvarligaste fyndet i hela bygget. Naturvårdsverkets WFS levererar felaktig
GeoJSON på två sätt:

1. **Svaret kapas mitt i JSON-strukturen** för celler med stora geometrier,
   deterministiskt, med en eller flera features borta.
2. **Flerdelade områden plattas ihop till EN polygon** där de övriga delarna
   hamnar som "hål". Naturreservatet Verkeån blev 374 ha i stället för
   registrets egna 1 424 ha. En position i den bortplattade delen fick svaret
   "ingen restriktion hittad" — fel åt exakt det farliga hållet.

Samma uttag i `outputFormat=ESRIGEOJSON` kommer komplett och med separata
ringar. Konverteringen ligger i `lib/geom.py`.

**Ringarna klassas på inneslutning, inte på orientering.** Den vanliga
ESRI-regeln (medurs = ytterring) håller inte i den här datan: i Verkeån har ett
5,2 km² stort hål samma rotationsriktning som den 9,2 km² stora ytterringen. En
orienteringsbaserad regel gjorde hålet till en egen yta och nästan fyrdubblade
arealen. Regeln som används: en ring innanför ett jämnt antal andra ringar är
ytterring, innanför ett udda antal är den ett hål.

Konverteringen validerades mot Skånes tidigare data och mot registrets eget
`AREA_HA`. Registret bekräftade den nya geometrin i samtliga stickprov.

### D-45 — Arealavstämning mot AREA_HA som permanent test
Felet i D-44 var osynligt för alla dåvarande tester. `AREA_HA` är en oberoende
uppgift ur samma post och avslöjar det direkt. Kontrollen skiljer på riktning,
för de betyder olika saker:

- **Geometri mindre än registrerad areal** är farligt — delar av området saknas
  och en position där får svaret "ingen restriktion hittad". Test B faller om
  någon geometri täcker under halva registrerade arealen.
- **Geometri större** är i praktiken registrets egen inkonsekvens. Naturreservatet
  Norra Ljunghusen har `AREA_HA` 15,64 medan dess egen polygon är 186 ha, i både
  GEOJSON- och ESRI-utskriften. Det går inte att rätta, men det redovisas.

### D-46 — Rutnätsdata i två storlekar, och egna filer åt jättarna
Rikstäckande geometri är 76 MB och går inte att skicka till en telefon.

- `oversikt.json` — hela landet, 400 m tolerans, områden över 50 ha. Laddas
  utzoomad. Hur många som utelämnas skrivs ut i kartan.
- `rutor/` — 1°-rutor med visningsgeometri, laddas per kartfönster.
- `geom/` + `bbox/` — 0,25°-rutor med **oförenklad** originalgeometri för
  punkt-i-polygon.

Storleksfördelningen är extremt skev: medianen är 1,9 kB medan **50 områden är
över 100 kB och tillsammans 30 av 76 MB**. Läggs de i varje ruta de rör kopieras
Vindelfjällen in i dussintals rutor — katalogen blev 386 MB med enskilda rutor
på 5,7 MB. De stora får därför egna filer och refereras från rutan: 120 MB, och
största ruta 692 kB.

Geometrin för punkt-i-polygon förenklas **inte**. En förenkling som krymper ytan
kan ge svaret "ingen restriktion hittad" åt någon som står strax innanför
gränsen, och en som växer ytan går inte att kombinera med meningsfull
komprimering — mätt: grow-garantin tog bort 94 av 228 492 punkter.

### D-47 — Per-områdesfilerna serveras inte styckvis
Cloudflare Pages tar 20 000 filer per utrullning. 10 772 HTML-sidor plus lika
många JSON-filer spränger taket. Per-områdesfilerna finns kvar i kodförrådet
som CC0-produkt men kopieras inte till `dist/`; kartan behöver dem inte, den
läser geometri ur `geom/` och citat ur HTML-sidorna.

### D-48 — Datatillståndet "beslutet ej läst"
Ett område vars beslutsdokument ännu inte hämtats får inte påstå att ingen
luftfartsföreskrift finns — den utsagan är inte förtjänad förrän texten är läst.
Det är inte ett fjärde svarsläge utan ett datatillstånd: sidan säger att
beslutet inte lästs och länkar dit. Det gör det möjligt att rulla ut
rikstäckningen stegvis utan att tystnad ser ut som ett besked.

### D-49 — Närhetslistan får inte bero på vad kartan råkar visa
Den första versionen av kartappen räknade avstånd utifrån de utritade ytorna.
Utzoomad fanns bara områden över 50 ha där, och svaret påstod att närmaste
område låg 3,3 km bort när det i själva verket låg 940 m bort. Listan bygger nu
på bbox-rutorna runt punkten — vad kartan visar och vad som finns är två skilda
saker.

### D-50 — Namnet
Tjänsten heter "Drönarkoll" utan länstillägg sedan den täcker hela landet.
Kodförrådet heter fortfarande `dronarregler-skane` och `base_url` pekar på
`dronarkoll-skane.pages.dev` — båda kan bytas, men ett namnbyte på förrådet
bryter länkar och gjordes inte utan Daniels besked.

### D-51 — LFV:s luftrum hämtas som vektor, och R5 är därmed delvis upphävt
Ursprungsregeln sa: aldrig LFV:s WFS, aldrig omstyling, aldrig punkt-i-polygon
mot deras data. Ett ostylat rasterlager gjorde tjänsten oförmögen att själv
svara på om du står i en kontrollzon — den kunde bara rita en bild du fick
tolka. Hela Sveriges luftrum är 259 polygoner och 209 kB; som vektor ryms det i
telefonen och svarar utan nät.

Beslutet är fattat för en pre-beta med en användare och utan kommersiellt syfte,
och det är avstängbart med `lfv_vektor: false`. Det som INTE ändrades är
licensdisciplinen: LFV:s data är CC BY-NC-ND, undantas uttryckligen från
tjänstens CC0-upplåtelse, och deras egna tiles cachas aldrig om. Testet C6
skrevs om från "rör aldrig deras vektordata" till att vakta just det.
ND-klämman — att konvertera och styla om är en bearbetning — är den första
frågan att ta om innan tjänsten görs publik.

### D-52 — Sex luftfartslägen i stället för tre svarslägen
`lanklage` omfattade 5 485 områden och dolde två olika besked bakom samma ord:
"beslutet är läst och nämner ingenting om luftfart" och "det finns inget beslut
att läsa". Det första är ett svar, det andra en lucka. Nu skiljs de åt, och en
lucka rankas som mer angelägen än ett besked — en oläst handling får aldrig se
lugnare ut än en läst.

Mätt över landet: 2,8 % nämner luftfart ordagrant, 25,7 % har störningsförbud,
50,6 % är lästa utan träff, 20,8 % saknar digitalt beslut.

### D-53 — Citatet visas i kartsvaret, inte en klick bort
Kartan sa "Reglerat område — läs beslutet" och lämnade dig där, trots att
citatet fanns i datan. De 298 områden vars föreskrifter nämner luftfart ryms i
306 kB och bärs offline; störningsförbuden (2 746 områden, 3,5 MB) hämtas per
ruta. Utan det kan vaktläget inte varna utan täckning.

### D-54 — Rubriken får inte påstå mer än citatet
Västra Kullaberg fick rubriken "Föreskriften förbjuder störning" över ett citat
som lyder "I området finns miljöer som kan utgöra häckningsplatser för fåglar
…" — bakgrundstext ur beslutets skäl, inte en föreskrift. 483 av 3 044 områden
gjorde samma övertramp. Saknas verifierad föreskriftsinledning säger rubriken
nu "Beslutet nämner". Nivån är oförändrad; påståendet är det inte. Vaktas av
test C9.

### D-55 — De allmänna reglerna citeras, men ordnas under frågor
Höjdgränsen, klareringskravet och tillståndet för R-område är inte platsbundna
och stod ingenstans i tjänsten. De hämtas nu ur EU 2019/947, TSFS 2017:110,
skyddslagen och luftfartslagen med samma verifieringsgrind som naturbesluten:
ordagrann strängmatchning mot normaliserad källtext, annars avbryts bygget.

Läsbarheten löses med rubriker i frågeform ("Hur högt får jag flyga?"). En fråga
pekar; den sammanfattar inte. Det är den enda formen av förenkling R1 tillåter.

EUR-Lex svarar 202 på maskinella anrop; EU:s publikationsbyrå levererar samma
officiella text via cellar-API:t med `Accept-Language: swe`.

### D-56 — Friskrivning som grind i stället för utspridd reservation
En kvittering vid första besöket, sparad lokalt med versionsnyckel. Poängen är
inte juridisk utan redaktionell: när den som använder tjänsten en gång fått veta
vad den är, kan reservationerna på varje enskild sida hållas korta i stället för
att upprepas tills de slutar läsas.

### D-57 — En mening i §7-texten är ändrad, för att den blev falsk
Originalet sa att luftrumsregler "omfattas inte av tjänstens databas". Sedan
LFV:s zoner ligger inne stämmer det inte. Meningen preciserar nu vad som
fortfarande inte täcks — tillfälliga restriktioner, NOTAM, militär verksamhet.
Rättelse av sakförhållande, inte uppmjukning: att underdriva vad tjänsten gör är
lika vilseledande som att överdriva det.

### D-58 — PWA med vaktläge
Installbar, standalone, ikoner ritade i kod ur stdlib så att bygget inte kräver
Pillow eller en SVG-konverterare. Service workern förcachar app-skalet,
luftrummet och luftfartscitaten — den halva megabyte som räcker för nivå
1-svaret utan nät. Vaktläget lyssnar på positionen och piper bara vid INTRÄDE i
ett nytt läge; ett pip per uppdatering hade blivit larmtrötthet (R8).

Service workerns version är innehållshashen av app.js, style.css och sw.js.
Utan sw.js i hashen kan bara den ändras utan att cachenamnen byts, och då lever
den gamla appen kvar i webbläsaren.

### D-59 — Dokumentfiltret kastade beslut, och steget kunde inte köras om
`SKIP_DOC` prövades före `RULE_DOC`. En fil som heter "beslut med skötselplan
lerberget.pdf" matchar bägge och kastades som "ej föreskriftsbärande" — trots
att den ÄR beslutet, med skötselplanen inbunden efter. Länsstyrelser och
kommuner buntar rutinmässigt ihop dem.

1 847 beslutshandlingar kastades. 961 områden fick beskedet "inget digitalt
beslut att läsa" när beslutet fanns och låg länkat en rad längre ned. Det är den
värsta sortens fel den här tjänsten kan göra: den skickade i väg folk att läsa
hela handlingen själva, vilket är exakt det den finns till för att slippa.

Två rättelser, och den andra är den viktigare:

1. Ordningen är omvänd. Säger namnet "beslut" eller "föreskrift" är det en
   beslutshandling, oavsett vad mer som står i namnet. Bara filändelsen kan
   fälla det (en jpeg är aldrig ett beslut).

2. Steg 2 skrev tillbaka sitt eget urval över `rec["dokument"]`. Källistan
   fanns alltså inte kvar, och ett filterfel gick inte att rätta med en
   omkörning — datan var förstörd, inte bara felaktig. Urvalet görs nu alltid
   mot `dokument_alla`, som aldrig skrivs över. Ett steg som förstör sin egen
   indata är inte omkörbart, och det är ett arkitekturfel oavsett om filtret
   råkar vara rätt.

Listan återställdes ur `cache/raw/detail/`, som lyckligtvis var orörd.

### D-60 — Träffordsmärkning i stället för att lämna över hela läsbördan
Daniels invändning: "Följden av tjänsten verkar bli att man ska sätta sig in i
extremt mycket själv?" Befogad. Ett citat är ofta en halv sida beslutstext, och
utan hjälp måste man läsa alltihop för att se varför det kom upp.

De ord som utlöste klassificeringen märks nu ut i citatet. Det är inte en
tolkning — det är ett faktum om urvalet, och det enda som ändras är
synligheten. Ordalydelsen är orörd, vilket test C11 vaktar genom att ta bort
märkningen och kräva att texten då är identisk med den i datan.

Högst tre ämnesord och ett förbudsord märks. Förbudsordet finns i så gott som
varje citat per konstruktion, och märks allt blir hela stycket gult och
signalen dör.

Panelen visar dessutom första citatet och fäller ihop resten. Gotska Sandön
staplade sex punkter i full längd, varav flera var undantag för räddningstjänst
och förvaltare.

### D-61 — Svaret bytte axel: från "vilka områden" till "vilka handlingar"
"Vilka områden träffar min position" är fel fråga — svaret blir en hög att
tolka. Daniel formulerade den rätta: får jag lyfta här, landa här, flyga över?

Handlingarna härleds mekaniskt ur vilka mönster som matchade citatet, inte ur
någon bedömning av vad föreskriften betyder. START_LANDNING ger lyfta+landa,
luftfartstermer ger flyga, motor/tillträde ger marken, störningsförbud ger alla
tre luftburna eftersom citatet inte skiljer dem åt.

Raden säger vad källorna NÄMNER om handlingen. "Inget hittat i källorna" är
neutralt formulerat och neutralt färgat — aldrig grönt, aldrig "tillåtet".

### D-62 — Luckredovisning i varje svar
Det som INTE kontrollerades står nu i svaret, med kommunnamnet ifyllt: "Kommunala
föreskrifter för Höganäs mark", "Tillfälliga restriktioner och NOTAM",
"Skyddsobjekt", "Markägarens medgivande", "Natura 2000".

Utan den läses tystnad som klartecken, vilket är hela felet tjänsten finns till
för att undvika. Det gör också "en kontroll" ärlig även när världen inte är
fullständigt kartlagd: du vet exakt vad du själv måste kolla.

### D-63 — Täckning mäts per skyddsform och yta, inte per objekt
Ett samlat "88 % av 10 681 objekt" dolde att samtliga fem interimistiska förbud
var olästa — de drunknade bland 765 naturminnen, som var och en är ett enskilt
träd på 0,00 hektar.

Två kolumner: andel objekt OCH andel yta. Skillnaden mellan dem är i sig
upplysande. Landskapsbildsskydd har 68 % av objekten lästa men bara 42 % av
ytan — luckan sitter i få men stora områden. Totalen är 88 % av objekten men
96 % av ytan, vilket är det ärligare huvudtalet.

Rader under 60 % markeras. Interimistiskt förbud står på 0 % och syns nu.

### D-64 — Avståndsreglerna A1/A2/A3 saknades
/regler/ citerade artikel 4 och höjdgränsen men inte underkategorierna — alltså
inte UAS.OPEN.040 punkt 2, som kräver minst 150 meter till bostads-, affärs-,
industri- eller rekreationsområden i A3. Det är den regel som avgör om man över
huvud taget får flyga i en hamn eller ett villaområde, och den saknades.

Artikel 20 är lika viktig: en drönare utan klassmärkning över 250 g hamnar i A3,
vilket gör 150-metersregeln till huvudregel för de flesta. 150-metersregeln är
spärrad i MASTE_FINNAS.

Luckan hittades genom en rak fråga, inte genom testerna. Det säger något om vad
testsviten kan och inte kan: den vaktar att det som finns är ordagrant, inte att
det som borde finnas är med.

### D-65 — Kommunala ordningsföreskrifter: mätt, inte antaget
Höganäs allmänna lokala ordningsföreskrifter (KFS 2026:11) nämner ingenting om
drönare — noll träffar på drönare, obemannad, luftfartyg, modellflyg, flygning.
Och en kommun får inte reglera luftrummet: det är Transportstyrelsen som
beslutar om geografiska UAS-zoner, och en lokal föreskrift får inte strida mot
nationell lag.

Kvar av frågan är det kommunen faktiskt råder över: MARKEN. Ett parkreglemente
kan träffa lyft och landning utan att nämna drönare. Det hör hemma i
handlingsaxeln (D-61) och i luckredovisningen (D-62), inte i ett försök att
skrapa 290 kommuners PDF:er.

### D-66 — Natura 2000 är en lagregel, inte 4 136 beslut
Undersökt: öppen WFS på geodata.naturvardsverket.se/n2000/wfs, 4 136 områden,
ESRIGEOJSON fungerar, rika attribut (områdeskod, naturtyper, arter, län,
kommun).

Men de har INGA egna föreskrifter. Regimen är en enda paragraf — 7 kap 28 a §
miljöbalken: "Tillstånd krävs för att bedriva verksamheter eller vidta åtgärder
som på ett betydande sätt kan påverka miljön i ett naturområde som har
förtecknats…".

Det ändrar kostnadsbilden helt: lagret är geometri plus ETT ordagrant citat,
inte 4 136 dokument att hämta och OCR:a. Villkoret "på ett betydande sätt kan
påverka miljön" är dessutom en bedömning tjänsten inte får göra — lagret ska
flagga tillståndsregimen och citera villkoret, inte påstå ett förbud.

Licensen står som "Villkor okända" i metadatan. Det måste redas ut innan lagret
läggs in.

### D-67 — Kommunala dokument: sök på ämnet, inte inuti ett dokument
Daniel hittade "Riktlinjer för drönarverksamhet i Höganäs kommun" på en enkel
sökning. Registret sa "Höganäs — ingenting om drönare". Det var sant om det
dokument jag kontrollerat och falskt om kommunen.

Felet var metodiskt: jag sökte drönartermer INUTI de allmänna lokala
ordningsföreskrifterna i stället för att söka på ÄMNET på kommunens webbplats.
Registret listar därför dokument, inte kommuner, och varje dokument har en typ:

  foreskrift  bindande för allmänheten
  intern      kommunens egna rutiner för SIN verksamhet, binder inte en privat
              pilot — men kan innehålla uppgifter värda att läsa

En kommun räknas som kontrollerad först när dess FÖRESKRIFT är läst i sin
helhet. Interna riktlinjer får inte ensamma göra en kommun "kontrollerad".

Ur Höganäs riktlinje, ordagrant och värt att bära vidare:

  "Kommunen har ett tiotal naturreservat, i vilka drönarflygning generellt är
   förbjuden. Det ska dock påpekas att rättsläget är osäkert, då
   länsstyrelserna inte äger befogenhet att själva besluta om sådana
   restriktioner."

Det är en invändning mot grunden för hela naturreservatslagret, från en kommun
som samrått med länsstyrelsen Skåne.

### D-68 — En säkerhetskontroll som inte kan köra får inte tiga
`wfs_url()` hoppade över argument vars värde var None i stället för att TA BORT
parametern. `resultType=hits` skickades därför med `outputFormat` kvar, servern
svarade JSON i stället för XML, antalet gick inte att läsa — och avstämningen
mot tyst kapning passerade som godkänd utan att ha körts. Loggen sa "servern
uppger None", och det lästes förbi.

Felet fanns i både steg 8 och steg 10. Steg 10 avbryter nu om antalet inte går
att läsa.

Kontrollen finns för att Naturvårdsregistrets server tyst kapade svaret vid 500
poster (D-02). Att den sedan kunde sluta fungera utan att någon märkte det är
samma felklass en nivå upp.

### D-69 — Ett steg som inte cachar sitt råsvar går inte att bygga om
Steg 10 hämtade 211 MB och cachade dem inte, till skillnad från steg 1 och 2.
När lagringsmodellen visade sig behöva göras om — en enda fil blev 37 MB mot
Cloudflares tak på 25 — kostade det en omhämtning, och ett hastigt
omindexeringsförsök sprängde katalogen till 4,8 GB innan källfilerna hunnit
säkras.

Råsvaret cachas nu. Regeln är enkel: ett steg vars utdata kan behöva byggas om
ska aldrig behöva fråga källan igen.

### D-70 — Jättar lagras del för del i rutnätet
"Torne och Kalix älvsystem" (SE0820430) har 3 980 polygondelar och 1,95
miljoner punkter spridda över hela Norrbotten. Som en fil blir den 37 MB.

Områden över 5 MB lagras därför del för del: varje polygondel läggs bara i de
rutor dess EGEN omslutande rektangel rör. Ingen geometri ändras — det är samma
polygoner, bara indexerade finare — och punkt-i-polygon blir snabbare, eftersom
en ruta bara bär de delar som faktiskt kan träffas.

### D-71 — Mönstret i dagens tre fel
Daniels fynd och mina två egna är samma sak i olika skepnad:

  * Jag sökte i ETT dokument och drog en slutsats om KOMMUNEN.
  * Avstämningen KUNDE INTE KÖRA och tolkades som godkänd.
  * Steget kunde inte BYGGAS OM utan att fråga källan igen.

Alla tre är fall där ett negativt utfall såg ut som ett positivt. Det är samma
grundfel tjänsten är byggd för att undvika hos andra — att tystnad läses som
besked — men i den egna metoden i stället för i datan.

## Öppna punkter

- `base_url` pekar på pages.dev — byt när en skarp domän är bestämd.
- Cron för månadsuppdateringen är avstängd.
- Bakgrundskartans tile-leverantör bör omprövas om trafiken växer (D-24).
- LFV:s ND-licens: ta om frågan innan tjänsten görs publik (D-51).
- Natura 2000: licensen står som "Villkor okända" — red ut innan lagret läggs in (D-66).
- Vaktläget är inte kört på en riktig telefon ute i fält — bara i
  webbläsare med simulerad position.
