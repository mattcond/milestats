# milestats

Lettura di file `.fit` e calcolo di velocità/passo per bucket temporali da
T secondi, confrontando tre fonti di velocità: dispositivo, haversine
(GPS) e distanza cumulativa ufficiale.

## Struttura del file .fit

Ogni istante campionato produce **due record separati** con lo stesso
timestamp:

1. un record "GPS" con `position_lat` / `position_long` (più speed e
   quota del dispositivo);
2. un record "distanza" con il campo `distance`, la distanza cumulativa
   **ufficiale** calcolata dal dispositivo (combacia esattamente con
   `session.total_distance`), tipicamente il risultato di un filtro o
   fusione sensori più accurato della sola posizione GPS grezza.

milestats unisce questi due record per timestamp in un unico punto che
contiene sia lat/long sia la distanza cumulativa ufficiale.

## Modalità (`--mode`, `-m`)

| Modalità | Cosa calcola | Colonne della tabella per bucket |
|---|---|---|
| `s` (default) | solo la distanza ufficiale del dispositivo; haversine **non viene calcolato** | Timestamp, Lat, Lon, Dim. (s), N punti, Dist. (m), Dist. cum., Tempo, Tempo cum., Speed uff., Passo uff. |
| `h` | solo haversine sulle posizioni GPS | Timestamp, Lat, Lon, Dim. (s), N punti, Dist. (m), Dist. cum., Tempo, Tempo cum., Speed hav., Passo hav. |
| `a` | tutte le fonti a confronto, inclusa la velocità del dispositivo | Timestamp, Lat, Lon, Dim. (s), N punti, Dist. (m), Dist. cum., Tempo, Tempo cum., Speed dev./hav./uff. con i rispettivi passi |

In modalità `h` il riepilogo usa la distanza haversine e, se un delta è
stato scartato (vedi "Eventi timer e pause"), calcola la velocità
sul tempo residuo e lo segnala. In `s` e `a` la colonna "Dist. (m)" è la
distanza ufficiale.

Quanto differiscono ufficiale e haversine (file di esempio, scarto per
bucket): con T = 5 s in media 1,07 km/h (fino a 2,83); con T = 30 s
0,17 km/h; con T = 60 s 0,11 km/h. Sui totali la differenza è piccola
(5619,8 m contro 5665,5 m), ma a bucket brevi può essere rilevante: è il
motivo per cui `h` e `a` restano disponibili.

```bash
uv run milestats data/Corsa_dell_ora_di_pranzo.fit            # s (default)
uv run milestats data/Corsa_dell_ora_di_pranzo.fit -m h
uv run milestats data/Corsa_dell_ora_di_pranzo.fit -m a -t 30
```

## Bucket discreti per tempo e distanza (`--time-bucket`, `--distance-bucket`)

Oltre al bucket fine di dettaglio (`-t`/`--intervallo`, in secondi), ogni
riga della tabella riceve due etichette **testuali**, discrete e ordinabili
anche come testo (zero padding a 3 cifre), indipendenti da `-t`, nel
formato `NNN_inizio-fine unità`:

- `time_bucket_idx`: a quale finestra da `--time-bucket` minuti (default
  **5**) di tempo attivo appartiene l'inizio della riga (`001_0-5 min`,
  `002_5-10 min`, ...);
- `distance_bucket_idx`: a quale finestra da `--distance-bucket` metri
  (default **500**) di distanza appartiene l'inizio della riga
  (`001_0-500 m`, `002_500-1000 m`, ...).

Sono etichette di riga, non un'aggregazione fatta dal tool: servono a poter
raggruppare/pivotare il dettaglio a valle (Excel, pandas, ...) su queste
colonne, e sono incluse nell'export CSV/Excel esattamente in questo
formato. In più, per comodità, vengono usate anche per stampare a schermo
due tabelle di riepilogo — una per finestra temporale, una per finestra di
distanza — con velocità e passo calcolati come distanza totale della
finestra / tempo totale della finestra (non media dei bucket fini, stesso
principio spiegato nella Nota 2 più sotto). Rispondono direttamente a
"come sono andato dopo X minuti" e "come sono andato dopo D metri":

```bash
uv run milestats data/Corsa_dell_ora_di_pranzo.fit -m a --time-bucket 10 --distance-bucket 1000
```

```
Performance per bucket temporale da 10 min (come sono andato dopo X minuti):
Bucket               Dist. (m)   Tempo (s)   Speed dev. (km/h)   Passo dev.    Speed hav. (km/h)   Passo hav.    Speed uff. (km/h)   Passo uff.
001_0-10 min         1103.6      629         5.40                11:07 min/km  6.47                9:16 min/km   6.32                9:30 min/km
002_10-20 min        1113.8      600         5.90                10:10 min/km  6.71                8:56 min/km   6.68                8:59 min/km
...
```

## Categoria di passo (`pace_categoria`)

Terza etichetta di riga, sempre presente nell'export, ma con confini
**fissi** (non parametrizzabili da CLI): la fascia da 30 secondi in cui
cade il passo del bucket (calcolato sulla stessa fonte di "Dist. (m)":
ufficiale in `s`/`a`, haversine in `h`), dalla più lenta alla più veloce:

| Indice | Fascia | Etichetta |
|---|---|---|
| 0 | 10:00 min/km o più lento | `000_>=10:00` |
| 1 | 9:30–9:59 min/km | `001_9:30-9:59` |
| 2 | 9:00–9:29 min/km | `002_9:00-9:29` |
| 3 | 8:30–8:59 min/km | `003_8:30-8:59` |
| 4 | 8:00–8:29 min/km | `004_8:00-8:29` |
| 5 | 7:30–7:59 min/km | `005_7:30-7:59` |
| 6 | 7:00–7:29 min/km | `006_7:00-7:29` |
| 7 | 6:30–6:59 min/km | `007_6:30-6:59` |
| 8 | 6:00–6:29 min/km | `008_6:00-6:29` |
| 9 | 5:30–5:59 min/km | `009_5:30-5:59` |
| 10 | 5:00–5:29 min/km | `010_5:00-5:29` |
| 11 | 4:30–4:59 min/km | `011_4:30-4:59` |
| 12 | più veloce di 4:30 min/km | `012_<4:30` |

Le fasce da 30 secondi coprono l'intero intervallo da 10:00 a 4:30 min/km
senza buchi; le due fasce estreme (indice 0 e 12) sono aperte, quindi ogni
passo positivo ricade sempre in una di esse. Un passo non definito
(velocità nulla, es. un bucket fermo) vale `N/D`. Come
`time_bucket_idx`/`distance_bucket_idx`, è pensata per raggruppare/pivotare
il dettaglio a valle, non per un'aggregazione fatta dal tool.

## Punto del bucket, ID attività ed export

**Punto del bucket.** Ogni riga riporta `lat`/`lon` del **primo punto reale**
del bucket, non un punto medio, insieme a `ts_punto`, il timestamp di quel
punto. `ts_punto` può essere qualche secondo dopo l'inizio del bucket se nei
dati mancano dei campioni. Se il primo record del bucket non ha coordinate si
usa il primo che le ha (`ts_punto` dice quale); se nessuno le ha restano vuote.
Nei bucket che iniziano subito dopo una pausa il punto è il primo del
segmento, la cui posizione può essere ancora quella precedente alla pausa.

**ID attività.** Il formato FIT non ha un campo "id attività" universale, e
l'id di Strava (quello nell'URL) non è contenuto nel file. Si usa il primo
disponibile tra:

1. `--id VALORE` passato da riga di comando;
2. `live_activity_id` (developer field di Strava nella sessione), se diverso
   da 0 (nel file di esempio vale 0);
3. `file_id.time_created` come epoch UTC in secondi. Nello standard FIT
   l'identità di un file è data da manufacturer, product, serial_number e
   time_created; qui manca il serial_number;
4. i primi 12 caratteri dello SHA-1 del file.

La fonte usata è stampata a inizio esecuzione (`ID attività: ... (fonte: ...)`).

**Export** (`-e csv|xlsx|excel`, cartella con `-o`, creata se manca):

```bash
uv run milestats data/Corsa_dell_ora_di_pranzo.fit -t 30 -e csv
uv run milestats data/Corsa_dell_ora_di_pranzo.fit -t 30 -m a -e excel -o export
```

Nome file: `id_tsstart_tsend_tsnow.<csv|xlsx>` con timestamp **UTC** nel formato
`AAAAMMGGTHHMMSS`: primo e ultimo record dell'attività e momento dell'export.
Esempio: `1790417339_20260926T092056_20260926T100852_20260928T062546.csv`.

Colonne: `id_attivita`, `ts_min_attivita` (il minimo di `timestamp` tra tutte
le righe con lo stesso `id_attivita`: con un solo export coincide col
timestamp del primo bucket, ma resta corretto anche dopo un
`--merge-output` che accoda più attività, per poterle identificare e
raggruppare), `timestamp` (inizio bucket), `ts_punto`, `lat`, `lon`,
`dim_bucket_s`, `n_punti`, `dist_m`, `dist_cum_m`, `tempo_bucket_s`,
`tempo_cum_s`, `time_bucket_idx`, `distance_bucket_idx` (testo nel formato
`NNN_inizio-fine unità`, vedi sezione dedicata sopra), `pace_categoria`
(testo, vedi sezione dedicata), poi per ogni fonte della modalità
`speed_<fonte>_kmh` e `pace_<fonte>_min_km` (minuti per km, numerico
decimale: es. 9:05 min/km = 9,083). I valori sono numeri veri o date
(tranne le tre colonne testuali sopra), non testo formattato.

## Elaborazione in batch (`--process-all`)

Per elaborare in un colpo solo tutti i file `.fit` presenti in `data/`
(senza indicare `percorso_fit`):

```bash
uv run milestats --process-all
uv run milestats --process-all -m a -e xlsx --time-bucket 10 --distance-bucket 1000
```

Per ogni file trovato in `data/` (non ricorsivo):

1. viene elaborato come un file singolo (stessi `-m`/`-t`/`--time-bucket`/
   `--distance-bucket` passati sulla riga di comando);
2. l'output viene sempre esportato, con il nome standard, in `data/output/`
   (creata se manca); se `-e`/`--export` non è specificato l'export usa
   `csv`;
3. il file `.fit` elaborato con successo viene spostato in `data/processed/`
   (creata se manca).

Un file che fallisce l'elaborazione (es. non è un `.fit` valido, o non ha
record dentro le finestre attive) viene segnalato su stderr e **lasciato in
`data/`**, senza interrompere l'elaborazione degli altri file. Al termine
viene stampato un riepilogo con il numero di file elaborati e con errori.

`percorso_fit` e `--id` non sono compatibili con `--process-all` (l'id
attività viene sempre ricavato dal file, per evitare collisioni di nome tra
più file esportati).

## Unire tutti gli export in un unico file (`--merge-output`)

Per accodare in un unico file tutti gli export già prodotti in
`data/output/` (da run precedenti di `milestats`, incluso `--process-all`),
anche se non hanno tutti le stesse colonne (es. alcuni con `-m s`, altri
con `-m a`, o con `--time-bucket`/`--distance-bucket` diversi):

```bash
uv run milestats --merge-output
uv run milestats --merge-output -e xlsx
```

Legge tutti i file `.csv` e `.xlsx` presenti in `data/output/` (non
ricorsivo) e ne accoda le righe in un unico file, con **l'unione delle
intestazioni**: la colonna risultante include tutte le colonne viste in
almeno un file, nell'ordine in cui compaiono per la prima volta; una
colonna assente in un dato file vale **vuoto/null** in tutte le sue righe.
Un file che non si riesce a leggere (es. corrotto) viene segnalato su
stderr e saltato, senza interrompere il merge degli altri.

Il file risultante viene scritto anch'esso in `data/output/` — stessa
convenzione di `--process-all`, `-o`/`--output-dir` viene ignorato — con
nome `merge_<n>file_<timestamp UTC>.<csv|xlsx>` (formato secondo
`-e`/`--export`, `csv` se non specificato, indipendentemente dal formato
dei singoli file letti, che può essere misto csv/xlsx). Un merge
precedente presente nella cartella (`merge_*.csv`/`.xlsx`) non viene
riletto come input di un merge successivo.

`percorso_fit`, `--process-all` e `--id` non sono compatibili con
`--merge-output`.

- `dim_bucket_s`: dimensione del bucket in secondi, cioè il valore di `-t`
  (il numero di punti campionati è in `n_punti`).
- `dist_cum_m`: distanza cumulata dall'inizio **fino alla fine del bucket
  incluso**. Vale sempre `dist_cum_m` della riga precedente + `dist_m` della
  riga corrente (nella prima riga coincide con `dist_m`), **esattamente sui
  numeri riportati**: `dist_m` è arrotondata al millimetro prima di sommare, e
  non ci sono scarti da arrotondamento indipendente. Segue la fonte della
  modalità (ufficiale in `s` e `a`, haversine in `h`), come `dist_m`. Nell'ultima
  riga coincide col totale del riepilogo; in `h`, dove le distanze hanno più
  decimali, può differire di qualche millimetro-centimetro dal totale non
  arrotondato e dipende leggermente da T (5665,500 m con T = 30, 5665,514 m con
  T = 5). In `s` e `a` non c'è differenza.
- `tempo_bucket_s`: tempo **registrato** nel bucket, cioè la somma dei
  secondi trascorsi tra i suoi punti: dall'ultimo punto del bucket precedente
  all'ultimo punto di questo (nel primo bucket, dal primo punto). Le pause non
  contano. **Non è sempre T**: dove mancano campioni vale qualche secondo in
  più o in meno (con T = 30 nel file di esempio: 27, 28, 29, 30 o 31 s; con
  T = 5: 2, 4, 5 o 6 s). La somma di tutti i bucket è "Tempo totale".
- `tempo_cum_s`: tempo cumulato dall'inizio fino alla fine del bucket incluso.
  Vale sempre `tempo_cum_s` della riga precedente + `tempo_bucket_s` della riga
  corrente (nella prima riga coincide con `tempo_bucket_s`), esattamente sui
  numeri riportati. Non è `timestamp` + T né k x T, per lo stesso motivo. Non
  dipende dalla modalità: in `h` la distanza cumulata esclude i delta scartati
  mentre il tempo conta anche i loro secondi (un secondo per segmento).

Note: Excel in italiano apre male i CSV separati da virgola (si aspetta il
punto e virgola): per Excel conviene `-e xlsx`. Due export nello stesso
secondo sullo stesso file producono lo stesso nome e si sovrascrivono.

## Eventi timer e pause

Il file contiene messaggi `event` con `event = timer` e `event_type`
`start` / `stop` (con `timer_trigger` che indica se l'azione è stata
manuale o automatica). Ogni coppia start → stop delimita una **finestra
attiva**; milestats usa solo i record dentro le finestre e stampa
all'inizio quelle trovate.

- Una corsa senza pause ha una sola finestra (è il caso del file di
  esempio: 09:20:56 → 10:08:52, 2876 s).
- Con pause manuali o auto-pause le finestre sono più di una: il tempo tra
  uno stop e lo start successivo non viene contato, e non si calcola
  alcun delta di tempo o distanza attraverso la pausa.
- Il delta haversine che parte dal **primo punto di ogni segmento**
  (compreso il primo dell'attività) viene scartato: la posizione di quel
  punto può essere ancora quella precedente alla pausa, e produrrebbe un
  salto irreale. Il secondo corrispondente esce anche dal denominatore
  della velocità haversine. La distanza ufficiale non ne risente, perché
  da fermo il device non la incrementa.
- Se il file non ha eventi timer, milestats usa tutti i record.

## Tre fonti di velocità a confronto

Per ogni bucket da T secondi vengono calcolate tre coppie speed/passo:

1. **Speed dev. / Passo dev.**: media delle velocità istantanee
   **registrate dal dispositivo** (campo `speed`) — qui non deriviamo
   noi una distanza, quindi resta una media di campioni puntuali.
2. **Speed hav. / Passo hav.**: velocità del bucket calcolata come
   (distanza haversine **totale** del bucket) / (tempo **totale** del
   bucket) — non come media delle velocità istantanee (vedi nota 2).
3. **Speed uff. / Passo uff.**: velocità del bucket calcolata come
   (delta **totale** della distanza cumulativa ufficiale nel bucket) /
   (tempo **totale** del bucket) — la fonte più vicina a quella usata
   dalle app (Strava, Garmin Connect, ecc.) per i grafici di passo.

La colonna **Dist. (m)** del bucket usa la distanza ufficiale (somma dei
delta del campo `distance`), non la somma haversine punto-per-punto: è la
sorgente più affidabile disponibile nel file.

### Nota 1: perché non affidarsi solo ad haversine

Una versione precedente calcolava la distanza sommando gli step haversine
punto-per-punto sulla sola posizione GPS. Questo evita il bias da
"corner-cutting" dei centroidi (vedi versione ancora precedente), ma
resta comunque soggetto al rumore della posizione GPS grezza, che porta
a una distanza totale sistematicamente diversa da quella ufficiale del
dispositivo. Nel file di test: ~5281 m via haversine contro **5619.8 m**
ufficiali (un errore di circa il 6%, corrispondente a un passo medio di
9:05 min/km contro **8:32 min/km** ufficiali — quest'ultimo molto vicino
agli 8:22 min/km mostrati dall'app). Il campo `distance` del dispositivo
applica un filtro (es. Kalman, fusione GPS+accelerometro) e coincide
esattamente con `session.total_distance`. Per questo distanza totale e
velocità "ufficiale" si basano su questo campo; haversine resta
disponibile come fonte di confronto/diagnostica.

### Nota 2: perché la velocità del bucket non è la media delle istantanee

Una versione precedente calcolava la velocità di ogni bucket come media
aritmetica delle velocità istantanee dei singoli punti al suo interno.
Ma velocità = distanza/tempo è una funzione non lineare: la media delle
velocità istantanee di N campioni **non equivale**, in generale, alla
distanza totale percorsa diviso il tempo totale trascorso (per la
disuguaglianza AM-HM la media aritmetica di velocità tende a essere
inferiore alla vera velocità media, specie con campioni molto variabili
per rumore GPS). Nel file di test questo produceva bucket sistematicamente
più lenti di 20-40 secondi/km rispetto al passo reale nei tratti veloci,
non visibili confrontando con il grafico dell'app (es. nel tratto 2.5-4.0
km il grafico mostrava passi di 7-8 min/km, non riprodotti dalla media
delle istantanee). Il fix: la velocità del bucket si calcola ora come
distanza_totale_bucket / tempo_totale_bucket, sia per haversine sia per
la distanza ufficiale. La colonna "Speed dev." resta invece una media di
campioni puntuali, perché lì non deriviamo noi una distanza dal delta di
posizione.

## Setup

```bash
uv sync
```

Questo crea il virtualenv e installa `fitparse` (lettura .fit), `openpyxl`
(export Excel) e `pytest` (dipendenza di sviluppo, per i test).

## Uso

```bash
uv run milestats data/Corsa_dell_ora_di_pranzo.fit
```

Con un intervallo di aggregazione diverso dal default (5 secondi):

```bash
uv run milestats data/Corsa_dell_ora_di_pranzo.fit -t 10
uv run milestats data/Corsa_dell_ora_di_pranzo.fit --intervallo 30
```

oppure, con un tuo file:

```bash
uv run milestats /percorso/al/tuo/file.fit -t 15
```

## Output

Riepilogo generale (distanza totale ufficiale, tempo totale, velocità e
passo medi complessivi), seguito da una tabella con una riga per bucket e
le tre coppie speed/passo descritte sopra.

## Test

```bash
uv run pytest
```

Suite snella (~30 test, ~10 secondi): funzioni pure (haversine, conversioni
di velocità/passo, naming dell'export) con dati sintetici che non richiedono
di leggere un file `.fit`; aggregazione per bucket ed etichette
tempo/distanza sulla stessa base sintetica; export CSV/Excel; alcuni test di
integrazione sul file di esempio con bucket grandi (`-t 600`) per restare
veloci pur passando dalla pipeline completa, incluso `--process-all`. Gira
automaticamente su push/PR (`.github/workflows/tests.yml`).
