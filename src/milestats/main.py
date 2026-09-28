"""
Lettura file .fit e calcolo velocità/passo per bucket temporali da T secondi.

Richiede: pip install fitparse

Struttura del file .fit
------------------------
Ogni istante campionato produce DUE record separati con lo stesso
timestamp:
  1. un record "GPS" con `position_lat` / `position_long` (e speed/quota
     del dispositivo);
  2. un record "distanza" con il campo `distance`, la distanza cumulativa
     UFFICIALE calcolata dal dispositivo (combacia con `session.total_
     distance`), tipicamente il risultato di un filtro/fusione sensori
     più accurato della sola posizione GPS grezza.

Questi due record vengono qui uniti per timestamp in un unico punto che
contiene sia lat/long sia la distanza cumulativa ufficiale.

Modalità (--mode)
-----------------
  s (default) standard: usa solo la distanza ufficiale del dispositivo;
              haversine non viene calcolato affatto.
  h           solo haversine sulle posizioni GPS.
  a           all: dispositivo, haversine e distanza ufficiale a confronto.

ID attività ed export
---------------------
Ogni bucket riporta lat/lon e timestamp del suo PRIMO punto reale (non un
punto medio). L'id dell'attività non esiste come campo standard nel .fit:
vedi leggi_id_attivita per l'ordine con cui viene ricavato, oppure lo si
passa con --id. Con --export csv|xlsx la tabella per bucket viene scritta
in un file chiamato id_tsstart_tsend_tsnow.<estensione>. Ogni riga ha anche
la dimensione del bucket (T), il tempo registrato nel bucket (tempo_bucket_s)
e i valori cumulati fino alla fine del bucket incluso: distanza (dist_cum_m)
e tempo attivo (tempo_cum_s, senza pause). Cumulato = cumulato della riga
precedente + valore della riga corrente.

Eventi timer e pause
--------------------
Il file contiene messaggi `event` con event == 'timer' e event_type
'start' / 'stop': ogni coppia delimita una FINESTRA ATTIVA. Solo i record
dentro una finestra vengono usati. Se ci sono pause (stop seguito da un
nuovo start) il tempo tra le due finestre non conta, e nessun delta di
tempo o distanza viene calcolato attraverso la pausa. Il delta haversine
che parte dal primo punto di ciascun segmento viene scartato, perché la
posizione di quel punto può non essere ancora affidabile (GPS non
riagganciato dopo la pausa); il relativo secondo esce anche dal
denominatore della velocità haversine. La distanza ufficiale non ha questo
problema e resta calcolata. Se il file non ha eventi timer si usano tutti
i record.

Fonti di velocità confrontate
------------------------------
Per ogni bucket da T secondi vengono calcolate tre coppie speed/passo:
  1. "Speed dev." / "Passo dev.": media delle velocità ISTANTANEE
     registrate dal dispositivo (campo `speed`) — qui non deriviamo noi
     una distanza, quindi resta una media di campioni puntuali.
  2. "Speed hav." / "Passo hav.": velocità del bucket calcolata come
     (distanza haversine TOTALE del bucket) / (tempo TOTALE del bucket)
     — non come media delle velocità istantanee (vedi nota sotto).
  3. "Speed uff." / "Passo uff.": velocità del bucket calcolata come
     (delta della distanza cumulativa ufficiale nel bucket) / (tempo
     TOTALE del bucket) — stesso principio del punto 2, applicato alla
     distanza ufficiale (campo `distance`) anziché a quella haversine.

La colonna "Dist. (m)" del bucket usa la distanza ufficiale (delta del
campo `distance`), non la somma haversine punto-per-punto: è la sorgente
più affidabile disponibile nel file, e coerente con il totale riportato
dall'app (vedi nota sotto).

Nota 1: perché non affidarsi solo ad haversine
------------------------------------------------
Una versione precedente calcolava la distanza sommando gli step haversine
punto-per-punto sulla sola posizione GPS. Questo evita il bias da
"corner-cutting" dei centroidi, ma resta comunque soggetto al rumore GPS
grezzo (posizione non filtrata), che porta a una distanza totale
sistematicamente diversa da quella ufficiale del dispositivo (nel file di
test: ~5281m via haversine contro 5620m ufficiali — un errore di circa il
6%). Il campo `distance` del dispositivo applica invece un filtro (es.
Kalman, fusione GPS+accelerometro) e coincide esattamente con
`session.total_distance`, risultando molto più vicino al passo medio
mostrato dalle app di terze parti. Per questo la distanza per bucket e la
velocità "ufficiale" si basano ora sul campo `distance`; haversine resta
disponibile come confronto/diagnostica.

Nota 2: perché la velocità del bucket non è la media delle istantanee
--------------------------------------------------------------------
Una versione precedente calcolava la velocità di ogni bucket come media
aritmetica delle velocità istantanee dei singoli punti al suo interno.
Ma velocità = distanza/tempo è una funzione non lineare: la media delle
velocità istantanee di N campioni NON equivale, in generale, alla
distanza totale percorsa diviso il tempo totale trascorso (per la
disuguaglianza AM-HM la media aritmetica di velocità tende a essere
inferiore alla vera velocità media, specie con campioni molto variabili
per rumore GPS). Nel file di test questo produceva bucket sistematicamente
più lenti di 20-40s/km rispetto al passo reale nei tratti veloci, non
visibili nel confronto con il grafico dell'app. Il fix: la velocità del
bucket si calcola ora come distanza_totale_bucket / tempo_totale_bucket,
sia per haversine sia per la distanza ufficiale.

Bucket discreti per l'analisi "come ho performato dopo X minuti / D metri"
---------------------------------------------------------------------------
Oltre al bucket fine di dettaglio (-t/--intervallo, in secondi), ogni riga
riceve due etichette intere e ordinabili, indipendenti da -t:
  - time_bucket_idx: a quale finestra da --time-bucket minuti (default 5)
    di tempo ATTIVO appartiene l'inizio della riga (0 = 0-5 min, 1 = 5-10
    min, ...);
  - distance_bucket_idx: a quale finestra da --distance-bucket metri
    (default 500) di distanza appartiene l'inizio della riga (0 = 0-500 m,
    1 = 500-1000 m, ...).
Sono incluse nell'export CSV/Excel per filtrare/raggruppare, e usate per
stampare a schermo due tabelle di riepilogo (velocità/passo raggruppati per
finestra, calcolati come distanza totale della finestra / tempo totale
della finestra, non come media dei bucket fini) che rispondono direttamente
a "come sono andato dopo X minuti" e "come sono andato dopo D metri".
"""

import argparse
import calendar
import csv
import hashlib
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from math import radians, sin, cos, sqrt, atan2
from pathlib import Path

import fitparse


# ----------------------------------------------------------------------
# Haversine: distanza in metri tra due coordinate geografiche (lat/long)
# ----------------------------------------------------------------------
def haversine(lat1, lon1, lat2, lon2):
    R = 6371000  # raggio medio della Terra in metri
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
    return 2 * R * atan2(sqrt(a), sqrt(1 - a))


def semicircles_to_degrees(value):
    """Le coordinate nei file .fit sono codificate in 'semicircles'."""
    return value * (180 / 2**31)


# ----------------------------------------------------------------------
# Lettura del file .fit: unione dei record GPS e distanza per timestamp
# ----------------------------------------------------------------------
def leggi_record(percorso_file):
    """
    Legge tutti i messaggi 'record' del file .fit e li unisce per
    timestamp in un'unica lista di punti, ciascuno con (quando presenti):
      - timestamp
      - lat, lon (dal record GPS)
      - speed_device (m/s, dal record GPS)
      - altitude (m, dal record GPS)
      - distance_ufficiale (m, cumulativa, dal record distanza)

    I record .fit con lo stesso timestamp vengono fusi in un solo punto
    (in questo file sorgente ogni istante produce un record GPS e un
    record distanza separati, con campi complementari).
    """
    fitfile = fitparse.FitFile(percorso_file)

    punti_per_timestamp = {}
    ordine_timestamp = []

    for msg in fitfile.get_messages('record'):
        dati = {campo.name: campo.value for campo in msg}
        ts = dati.get('timestamp')
        if ts is None:
            continue

        if ts not in punti_per_timestamp:
            punti_per_timestamp[ts] = {'timestamp': ts}
            ordine_timestamp.append(ts)

        punto = punti_per_timestamp[ts]

        lat = dati.get('position_lat')
        lon = dati.get('position_long')
        if lat is not None and lon is not None:
            punto['lat'] = semicircles_to_degrees(lat)
            punto['lon'] = semicircles_to_degrees(lon)
        if 'speed' in dati:
            punto['speed_device'] = dati['speed']
        if 'enhanced_altitude' in dati:
            punto['altitude'] = dati['enhanced_altitude']
        if 'distance' in dati:
            punto['distance_ufficiale'] = dati['distance']

    punti = [punti_per_timestamp[ts] for ts in ordine_timestamp]

    # scarta punti privi sia di posizione sia di distanza ufficiale
    # (non utilizzabili per nessuno dei calcoli)
    punti = [p for p in punti if 'lat' in p or 'distance_ufficiale' in p]

    return punti


# ----------------------------------------------------------------------
# Eventi timer: finestre di attività (start -> stop)
# ----------------------------------------------------------------------
def leggi_finestre_attive(percorso_file):
    """
    Legge i messaggi 'event' con event == 'timer' e costruisce la lista di
    finestre attive [(inizio, fine), ...], abbinando ogni event_type
    'start' al primo 'stop' (o 'stop_all') successivo.

    Un'attività senza pause ha una sola finestra; con pause manuali o
    auto-pause ne ha più di una, e il tempo tra uno stop e lo start
    successivo è tempo a timer fermo, da escludere dai calcoli.

    Se il file non contiene eventi timer validi ritorna None, e il
    chiamante deve usare tutti i record senza filtrare. Uno start senza
    stop finale (registrazione troncata) resta aperto fino all'ultimo
    record del file.
    """
    fitfile = fitparse.FitFile(percorso_file)

    eventi = []
    for msg in fitfile.get_messages('event'):
        dati = {campo.name: campo.value for campo in msg}
        if dati.get('event') != 'timer':
            continue
        if dati.get('timestamp') is None or dati.get('event_type') is None:
            continue
        eventi.append((dati['timestamp'], dati['event_type']))

    if not eventi:
        return None

    eventi.sort(key=lambda e: e[0])

    finestre = []
    inizio = None
    for ts, tipo in eventi:
        if tipo == 'start':
            if inizio is None:
                inizio = ts
        elif tipo in ('stop', 'stop_all'):
            if inizio is not None:
                finestre.append((inizio, ts))
                inizio = None

    if inizio is not None:
        finestre.append((inizio, None))  # start senza stop: aperta

    return finestre or None


def filtra_per_finestre(punti, finestre):
    """
    Tiene solo i punti il cui timestamp cade dentro una finestra attiva
    (estremi inclusi) e assegna a ciascuno l'indice della finestra in
    'segmento'. Se finestre è None ritorna i punti invariati con
    segmento 0.
    """
    if finestre is None:
        for p in punti:
            p['segmento'] = 0
        return punti

    filtrati = []
    for p in punti:
        for idx, (inizio, fine) in enumerate(finestre):
            if p['timestamp'] >= inizio and (fine is None or p['timestamp'] <= fine):
                p['segmento'] = idx
                filtrati.append(p)
                break
    return filtrati


# ----------------------------------------------------------------------
# Calcolo velocità istantanee: haversine (GPS) e da distanza ufficiale
# ----------------------------------------------------------------------
def calcola_velocita(punti, mode='a'):
    """
    Aggiunge a ogni punto (tranne il primo) i seguenti campi:
      - distanza_haversine_m: distanza dal punto precedente via haversine
        (solo se entrambi i punti hanno lat/lon)
      - speed_haversine_ms: velocità istantanea via haversine (m/s)
      - distanza_ufficiale_m: delta del campo distance rispetto al punto
        precedente (solo se entrambi i punti hanno distance_ufficiale)
      - speed_ufficiale_ms: velocità istantanea da distanza ufficiale (m/s)
      - dt_s: intervallo di tempo dal punto precedente (secondi)

    Tra due punti appartenenti a segmenti attivi diversi (cioè separati da
    una pausa del timer) dt_s vale 0 e le distanze/velocità sono None.
    Inoltre il delta haversine che parte dal PRIMO punto di ciascun
    segmento (compreso il primo dell'attività) è None, perché la posizione
    di quel punto può non essere ancora affidabile.

    mode seleziona quali distanze calcolare: 's' solo la distanza ufficiale
    (haversine non viene calcolato), 'h' solo haversine, 'a' entrambe. Le
    grandezze non calcolate valgono None.
    """
    calcola_hav = mode in ('h', 'a')
    calcola_uff = mode in ('s', 'a')

    punti[0]['primo_del_segmento'] = True
    punti[0]['dt_s'] = 0.0
    punti[0]['distanza_haversine_m'] = 0.0
    punti[0]['speed_haversine_ms'] = 0.0
    punti[0]['distanza_ufficiale_m'] = 0.0
    punti[0]['speed_ufficiale_ms'] = 0.0

    for i in range(1, len(punti)):
        p_prev = punti[i - 1]
        p_curr = punti[i]

        # Attraverso una pausa (punti in segmenti diversi) il timer era
        # fermo: nessun tempo e nessuna distanza vanno contati, altrimenti
        # il tratto tra stop e start successivo verrebbe scambiato per
        # movimento (o per un salto di posizione).
        p_curr['primo_del_segmento'] = p_prev.get('segmento', 0) != p_curr.get('segmento', 0)
        if p_curr['primo_del_segmento']:
            p_curr['dt_s'] = 0.0
            p_curr['distanza_haversine_m'] = None
            p_curr['speed_haversine_ms'] = None
            p_curr['distanza_ufficiale_m'] = None
            p_curr['speed_ufficiale_ms'] = None
            continue

        dt = (p_curr['timestamp'] - p_prev['timestamp']).total_seconds()
        p_curr['dt_s'] = dt

        if not calcola_hav:
            # modalità 's': haversine non richiesto, nessun calcolo
            p_curr['distanza_haversine_m'] = None
            p_curr['speed_haversine_ms'] = None
        # La posizione del primo punto di un segmento può essere ancora
        # quella precedente alla pausa (GPS non riagganciato): il delta
        # haversine che parte da lì non è affidabile e viene scartato.
        # La distanza ufficiale non ha questo problema (da fermo il device
        # non la incrementa) e resta calcolata.
        elif p_prev.get('primo_del_segmento'):
            p_curr['distanza_haversine_m'] = None
            p_curr['speed_haversine_ms'] = None
        elif 'lat' in p_prev and 'lat' in p_curr:
            d_hav = haversine(p_prev['lat'], p_prev['lon'], p_curr['lat'], p_curr['lon'])
            p_curr['distanza_haversine_m'] = d_hav
            p_curr['speed_haversine_ms'] = d_hav / dt if dt > 0 else 0.0
        else:
            p_curr['distanza_haversine_m'] = None
            p_curr['speed_haversine_ms'] = None

        if calcola_uff and 'distance_ufficiale' in p_prev and 'distance_ufficiale' in p_curr:
            d_uff = p_curr['distance_ufficiale'] - p_prev['distance_ufficiale']
            p_curr['distanza_ufficiale_m'] = d_uff
            p_curr['speed_ufficiale_ms'] = d_uff / dt if dt > 0 else 0.0
        else:
            p_curr['distanza_ufficiale_m'] = None
            p_curr['speed_ufficiale_ms'] = None

    return punti


# ----------------------------------------------------------------------
# Aggregazione dei punti in bucket temporali da T secondi
# ----------------------------------------------------------------------
def aggrega_per_bucket_temporale(punti, t_secondi, mode='a'):
    """
    Raggruppa i punti (già arricchiti da calcola_velocita) in bucket
    consecutivi di durata t_secondi, a partire dal timestamp del primo
    punto (t0). Per ogni bucket calcola:
      - timestamp: ISTANTE DI INIZIO del bucket (t0 + idx_bucket*t_secondi)
      - n_punti: numero di punti grezzi nel bucket
      - distanza_m: distanza del bucket per la colonna "Dist. (m)": somma
        dei delta ufficiali (modalità 's' e 'a') oppure haversine ('h')
      - tempo_s: tempo su cui è calcolata distanza_m (per 'h' esclude i
        secondi dei delta haversine scartati)
      - dim_bucket_s: dimensione del bucket in secondi (il parametro T)
      - tempo_attivo_s: tempo registrato nel bucket (colonna tempo_bucket_s):
        somma dei dt dei suoi punti, cioè il tempo dall'ultimo punto del
        bucket precedente all'ultimo punto di questo (nel primo bucket parte
        dal primo punto); le pause valgono 0. Non è sempre T: dove mancano
        campioni vale qualche secondo in più o in meno.
      - dist_cum_m: distanza cumulata dall'inizio fino alla fine del bucket
        INCLUSO, cioè somma progressiva di distanza_m
      - tempo_cum_s: tempo attivo cumulato dall'inizio fino all'ultimo punto
        del bucket incluso, cioè somma progressiva di tempo_attivo_s
      - lat, lon, ts_punto: coordinate e timestamp del PRIMO punto reale
        del bucket (non un punto medio). Se il primo record del bucket non
        ha coordinate si usa il primo che le ha, e ts_punto dice quale;
        se nessun record del bucket le ha i tre campi valgono None.
      - speed_device_media_ms: media delle velocità istantanee del
        dispositivo (campo speed) — media di campioni puntuali, non
        derivata da una distanza calcolata da noi
      - speed_haversine_bucket_ms: distanza haversine TOTALE del bucket
        diviso tempo TOTALE del bucket (non media di istantanee)
      - speed_ufficiale_bucket_ms: distanza ufficiale TOTALE del bucket
        diviso tempo TOTALE del bucket (non media di istantanee)
    Il campo speed_device_media_ms è calcolato solo in modalità 'a'.
    """
    if not punti:
        return []

    t0 = punti[0]['timestamp']
    bucket_corrente = []
    bucket_idx_corrente = 0
    bucket_list = []

    def media(valori):
        valori = [v for v in valori if v is not None]
        return sum(valori) / len(valori) if valori else 0.0

    def chiudi_bucket(bucket, idx_bucket):
        n = len(bucket)
        ts_inizio = t0 + timedelta(seconds=idx_bucket * t_secondi)

        punti_con_dt = [p for p in bucket if p['dt_s'] > 0]

        distanza_haversine_tot = sum(
            p['distanza_haversine_m'] for p in punti_con_dt
            if p['distanza_haversine_m'] is not None
        )
        distanza_ufficiale_tot = sum(
            p['distanza_ufficiale_m'] for p in punti_con_dt
            if p['distanza_ufficiale_m'] is not None
        )
        # Ogni fonte usa al denominatore solo il tempo dei punti per cui ha
        # una distanza valida: se un delta haversine è stato scartato, anche
        # il suo secondo esce dal calcolo, altrimenti la velocità verrebbe
        # sottostimata.
        tempo_hav = sum(p['dt_s'] for p in punti_con_dt
                        if p['distanza_haversine_m'] is not None)
        tempo_uff = sum(p['dt_s'] for p in punti_con_dt
                        if p['distanza_ufficiale_m'] is not None)

        speed_device_media = (media(p.get('speed_device') for p in bucket)
                              if mode == 'a' else 0.0)
        speed_haversine_bucket = distanza_haversine_tot / tempo_hav if tempo_hav > 0 else 0.0
        speed_ufficiale_bucket = distanza_ufficiale_tot / tempo_uff if tempo_uff > 0 else 0.0

        primo = next((p for p in bucket if 'lat' in p), None)
        tempo_attivo = sum(p['dt_s'] for p in punti_con_dt)

        if mode == 'h':
            distanza_m, tempo_s = distanza_haversine_tot, tempo_hav
        else:
            distanza_m, tempo_s = distanza_ufficiale_tot, tempo_uff

        return {
            'timestamp': ts_inizio,
            'n_punti': n,
            'lat': primo['lat'] if primo else None,
            'lon': primo['lon'] if primo else None,
            'ts_punto': primo['timestamp'] if primo else None,
            'dim_bucket_s': t_secondi,
            'distanza_m': distanza_m,
            'tempo_s': tempo_s,
            'tempo_attivo_s': tempo_attivo,
            'speed_device_media_ms': speed_device_media,
            'speed_haversine_bucket_ms': speed_haversine_bucket,
            'speed_ufficiale_bucket_ms': speed_ufficiale_bucket,
            # totali "grezzi" del bucket per fonte, conservati per poter
            # raggruppare più bucket fini (es. per time_bucket_idx /
            # distance_bucket_idx) sommando distanza e tempo PRIMA di
            # dividere, invece di mediare velocità già calcolate
            'distanza_haversine_tot': distanza_haversine_tot,
            'tempo_hav': tempo_hav,
            'distanza_ufficiale_tot': distanza_ufficiale_tot,
            'tempo_uff': tempo_uff,
        }

    for p in punti:
        offset = (p['timestamp'] - t0).total_seconds()
        idx_bucket = int(offset // t_secondi)

        if idx_bucket != bucket_idx_corrente and bucket_corrente:
            bucket_list.append(chiudi_bucket(bucket_corrente, bucket_idx_corrente))
            bucket_corrente = []
            bucket_idx_corrente = idx_bucket

        bucket_corrente.append(p)

    if bucket_corrente:
        bucket_list.append(chiudi_bucket(bucket_corrente, bucket_idx_corrente))

    # Valori cumulati: cumulato = cumulato del bucket precedente + valore del
    # bucket corrente. La distanza del bucket viene arrotondata al millimetro
    # PRIMA di sommare (le velocità sono già state calcolate coi valori non
    # arrotondati), così la regola vale esattamente sui numeri riportati, in
    # tutte le modalità, senza scarti da arrotondamento indipendente. Lo
    # stesso vale per il tempo del bucket.
    dist_cum = 0.0
    tempo_cum = 0.0
    for b in bucket_list:
        b['distanza_m'] = round(b['distanza_m'], 3)
        dist_cum = round(dist_cum + b['distanza_m'], 3)
        b['tempo_attivo_s'] = round(b['tempo_attivo_s'], 3)
        tempo_cum = round(tempo_cum + b['tempo_attivo_s'], 3)
        b['dist_cum_m'] = dist_cum
        b['tempo_cum_s'] = tempo_cum

    return bucket_list


# ----------------------------------------------------------------------
# Etichette discrete e ordinabili: bucket "macro" di tempo/distanza
# ----------------------------------------------------------------------
def etichetta_bucket_discreti(bucket_list, time_bucket_s, distance_bucket_m):
    """
    Aggiunge a ogni bucket fine (quello da -t/--intervallo) due etichette
    intere, discrete e ordinabili:
      - time_bucket_idx: indice del bucket "macro" da time_bucket_s secondi
        a cui appartiene l'INIZIO del bucket fine (tempo attivo cumulato
        prima del bucket, diviso time_bucket_s, troncato);
      - distance_bucket_idx: indice del bucket "macro" da distance_bucket_m
        metri a cui appartiene l'INIZIO del bucket fine (distanza cumulata
        prima del bucket, divisa per distance_bucket_m, troncata).

    Esempio: con time_bucket_s = 300 (5 min) un bucket fine che inizia al
    minuto 7 di tempo attivo ha time_bucket_idx = 1 (finestra 5-10 min).
    Le etichette permettono di raggruppare i bucket fini (vedi
    raggruppa_per_bucket_discreto) per rispondere a "come ho performato
    dopo X minuti / D metri", indipendentemente dalla granularità -t usata
    per il calcolo di dettaglio.
    """
    for b in bucket_list:
        tempo_inizio = b['tempo_cum_s'] - b['tempo_attivo_s']
        dist_inizio = b['dist_cum_m'] - b['distanza_m']
        b['time_bucket_idx'] = int(tempo_inizio // time_bucket_s) if time_bucket_s > 0 else 0
        b['distance_bucket_idx'] = int(dist_inizio // distance_bucket_m) if distance_bucket_m > 0 else 0
    return bucket_list


def raggruppa_per_bucket_discreto(bucket_list, campo_idx, mode):
    """
    Raggruppa i bucket fini per l'etichetta discreta campo_idx
    ('time_bucket_idx' o 'distance_bucket_idx') e per ciascun gruppo
    calcola distanza/tempo totali e le velocità delle fonti attive in
    mode come (somma delle distanze del gruppo) / (somma dei tempi del
    gruppo) — stesso principio di aggrega_per_bucket_temporale, non una
    media dei bucket fini già calcolati (vedi nota 2 nel README).
    Ritorna la lista dei gruppi ordinata per indice crescente.
    """
    gruppi = {}
    for b in bucket_list:
        idx = b[campo_idx]
        g = gruppi.setdefault(idx, {
            'idx': idx, 'n_bucket': 0, 'distanza_m': 0.0, 'tempo_attivo_s': 0.0,
            'distanza_haversine_tot': 0.0, 'tempo_hav': 0.0,
            'distanza_ufficiale_tot': 0.0, 'tempo_uff': 0.0,
            'speed_device_sum': 0.0, 'n_punti': 0,
        })
        g['n_bucket'] += 1
        g['distanza_m'] += b['distanza_m']
        g['tempo_attivo_s'] += b['tempo_attivo_s']
        g['distanza_haversine_tot'] += b['distanza_haversine_tot']
        g['tempo_hav'] += b['tempo_hav']
        g['distanza_ufficiale_tot'] += b['distanza_ufficiale_tot']
        g['tempo_uff'] += b['tempo_uff']
        g['speed_device_sum'] += b['speed_device_media_ms'] * b['n_punti']
        g['n_punti'] += b['n_punti']

    risultati = []
    for idx in sorted(gruppi):
        g = gruppi[idx]
        g['speed_device_media_ms'] = (g['speed_device_sum'] / g['n_punti']) if g['n_punti'] else 0.0
        g['speed_haversine_ms'] = g['distanza_haversine_tot'] / g['tempo_hav'] if g['tempo_hav'] > 0 else 0.0
        g['speed_ufficiale_ms'] = g['distanza_ufficiale_tot'] / g['tempo_uff'] if g['tempo_uff'] > 0 else 0.0
        risultati.append(g)
    return risultati


def stampa_analisi_bucket_discreti(bucket_list, mode, time_bucket_min, distance_bucket_m):
    """Stampa le tabelle di performance per bucket temporale e di distanza."""

    def header_e_righe(gruppi, etichetta_fn):
        if mode == 'a':
            header = (
                f"{'Bucket':<16} {'Dist. (m)':<11} {'Tempo (s)':<11} "
                f"{'Speed dev. (km/h)':<19} {'Passo dev.':<13} "
                f"{'Speed hav. (km/h)':<19} {'Passo hav.':<13} "
                f"{'Speed uff. (km/h)':<19} {'Passo uff.':<13}"
            )
        else:
            sigla = 'hav.' if mode == 'h' else 'uff.'
            header = (
                f"{'Bucket':<16} {'Dist. (m)':<11} {'Tempo (s)':<11} "
                f"{'Speed ' + sigla + ' (km/h)':<19} {'Passo ' + sigla:<13}"
            )
        print(header)
        for g in gruppi:
            base = f"{etichetta_fn(g['idx']):<16} {g['distanza_m']:<11.1f} {g['tempo_attivo_s']:<11.0f} "
            if mode == 'a':
                pace_dev = ms_a_pace(g['speed_device_media_ms'])
                pace_hav = ms_a_pace(g['speed_haversine_ms'])
                pace_uff = ms_a_pace(g['speed_ufficiale_ms'])
                print(
                    base
                    + f"{ms_a_kmh(g['speed_device_media_ms']):<19.2f} {formatta_pace(pace_dev):<13} "
                    + f"{ms_a_kmh(g['speed_haversine_ms']):<19.2f} {formatta_pace(pace_hav):<13} "
                    + f"{ms_a_kmh(g['speed_ufficiale_ms']):<19.2f} {formatta_pace(pace_uff):<13}"
                )
            else:
                speed_ms = g['speed_haversine_ms'] if mode == 'h' else g['speed_ufficiale_ms']
                print(base + f"{ms_a_kmh(speed_ms):<19.2f} {formatta_pace(ms_a_pace(speed_ms)):<13}")

    print()
    print(f"Performance per bucket temporale da {time_bucket_min:g} min "
          f"(come sono andato dopo X minuti):")
    gruppi_tempo = raggruppa_per_bucket_discreto(bucket_list, 'time_bucket_idx', mode)
    header_e_righe(
        gruppi_tempo,
        lambda idx: f"{idx * time_bucket_min:g}-{(idx + 1) * time_bucket_min:g} min",
    )

    print()
    print(f"Performance per bucket di distanza da {distance_bucket_m:g} m "
          f"(come sono andato dopo D metri):")
    gruppi_distanza = raggruppa_per_bucket_discreto(bucket_list, 'distance_bucket_idx', mode)
    header_e_righe(
        gruppi_distanza,
        lambda idx: f"{idx * distance_bucket_m:g}-{(idx + 1) * distance_bucket_m:g} m",
    )


# ----------------------------------------------------------------------
# ID attività
# ----------------------------------------------------------------------
def leggi_id_attivita(percorso_file):
    """
    Ritorna (id, fonte). Il formato FIT non ha un campo "id attività"
    universale, quindi si usa il primo di questi che è disponibile:
      1. `live_activity_id` (developer field di Strava nel messaggio
         session), se diverso da 0;
      2. `file_id.time_created` come epoch UTC in secondi: nello standard
         FIT l'identità di un file è data da manufacturer, product,
         serial_number e time_created, e questo file non ha serial_number;
      3. i primi 12 caratteri dell'hash SHA-1 del file.
    L'id reale di Strava (quello nell'URL) NON è contenuto nel file .fit.
    """
    fitfile = fitparse.FitFile(percorso_file)

    for msg in fitfile.get_messages('session'):
        for campo in msg:
            if campo.name == 'live_activity_id' and campo.value not in (None, 0, '0', ''):
                return str(campo.value), "live_activity_id (sessione)"

    for msg in fitfile.get_messages('file_id'):
        creato = {c.name: c.value for c in msg}.get('time_created')
        if creato is not None:
            return str(calendar.timegm(creato.timetuple())), "file_id.time_created (epoch UTC)"

    with open(percorso_file, 'rb') as fh:
        return hashlib.sha1(fh.read()).hexdigest()[:12], "hash SHA-1 del file"


# ----------------------------------------------------------------------
# Export CSV / Excel
# ----------------------------------------------------------------------
FORMATO_TS_FILE = "%Y%m%dT%H%M%S"


def nome_file_export(id_attivita, ts_start, ts_end, ts_now):
    """
    Nome standard, senza estensione:  id_ts-start_ts-end_ts-now
    con i timestamp in UTC nel formato compatto AAAAMMGGTHHMMSS.
    I caratteri non adatti a un nome di file nell'id diventano '-'.
    """
    id_pulito = re.sub(r'[^A-Za-z0-9._-]', '-', str(id_attivita))
    return "_".join([
        id_pulito,
        ts_start.strftime(FORMATO_TS_FILE),
        ts_end.strftime(FORMATO_TS_FILE),
        ts_now.strftime(FORMATO_TS_FILE),
    ])


def righe_export(bucket_list, mode, id_attivita):
    """
    Costruisce (intestazione, righe) per l'export, con valori NUMERICI
    (non stringhe formattate) così si aprono correttamente in Excel:
      id_attivita, timestamp (inizio bucket), ts_punto/lat/lon (primo punto
      reale del bucket), dim_bucket_s (T), n_punti, dist_m, dist_cum_m,
      tempo_bucket_s (tempo registrato nel bucket), tempo_cum_s (cumulati
      fino alla fine del bucket incluso: cumulato = cumulato della riga
      precedente + valore della riga corrente), time_bucket_idx e
      distance_bucket_idx (etichette intere e ordinabili del bucket "macro"
      di tempo/distanza a cui appartiene la riga, vedi
      etichetta_bucket_discreti), e per ogni fonte della modalità
      speed_<fonte>_kmh e pace_<fonte>_min_km (minuti per km, decimali).
    """
    campi = {
        'dev': 'speed_device_media_ms',
        'hav': 'speed_haversine_bucket_ms',
        'uff': 'speed_ufficiale_bucket_ms',
    }
    sigle = ['dev', 'hav', 'uff'] if mode == 'a' else (['hav'] if mode == 'h' else ['uff'])

    intestazione = ['id_attivita', 'timestamp', 'ts_punto', 'lat', 'lon',
                    'dim_bucket_s', 'n_punti', 'dist_m', 'dist_cum_m',
                    'tempo_bucket_s', 'tempo_cum_s',
                    'time_bucket_idx', 'distance_bucket_idx']
    for sg in sigle:
        intestazione += [f'speed_{sg}_kmh', f'pace_{sg}_min_km']

    righe = []
    for b in bucket_list:
        riga = [
            id_attivita, b['timestamp'], b['ts_punto'],
            round(b['lat'], 7) if b['lat'] is not None else None,
            round(b['lon'], 7) if b['lon'] is not None else None,
            int(b['dim_bucket_s']) if b['dim_bucket_s'] == int(b['dim_bucket_s']) else b['dim_bucket_s'],
            b['n_punti'], round(b['distanza_m'], 3),
            round(b['dist_cum_m'], 3), round(b['tempo_attivo_s'], 3),
            round(b['tempo_cum_s'], 3),
            b['time_bucket_idx'], b['distance_bucket_idx'],
        ]
        for sg in sigle:
            v = b[campi[sg]]
            riga += [round(ms_a_kmh(v), 3), round(ms_a_pace(v), 3) if v > 0 else None]
        righe.append(riga)
    return intestazione, righe


def esporta(intestazione, righe, formato, cartella, nome_base):
    """Scrive il file (csv o xlsx) in cartella e ritorna il percorso."""
    cartella = Path(cartella)
    cartella.mkdir(parents=True, exist_ok=True)
    percorso = cartella / f"{nome_base}.{formato}"

    if formato == 'csv':
        with open(percorso, 'w', newline='', encoding='utf-8') as fh:
            w = csv.writer(fh)
            w.writerow(intestazione)
            w.writerows(righe)
        return percorso

    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "bucket"
    ws.append(intestazione)
    for riga in righe:
        ws.append(riga)

    for c in ws[1]:
        c.font = Font(bold=True)
    ws.freeze_panes = "A2"

    colonne_ts = [i for i, n in enumerate(intestazione, 1) if n in ('timestamp', 'ts_punto')]
    for i in colonne_ts:
        for r in range(2, ws.max_row + 1):
            ws.cell(row=r, column=i).number_format = 'yyyy-mm-dd hh:mm:ss'
    for i, nome in enumerate(intestazione, 1):
        larghezza = max(len(nome), 19 if i in colonne_ts else 0,
                        *(len(str(riga[i - 1])) for riga in righe[:200] if riga[i - 1] is not None))
        ws.column_dimensions[get_column_letter(i)].width = larghezza + 2

    wb.save(percorso)
    return percorso


# ----------------------------------------------------------------------
# Conversioni di unità
# ----------------------------------------------------------------------
def ms_a_kmh(speed_ms):
    return speed_ms * 3.6


def ms_a_pace(speed_ms):
    """Passo in min/km. Ritorna None se la velocità è nulla."""
    return (1000 / speed_ms) / 60 if speed_ms > 0 else None


def formatta_pace(min_per_km):
    if min_per_km is None:
        return "N/D"
    minuti = int(min_per_km)
    secondi = int(round((min_per_km - minuti) * 60))
    if secondi == 60:
        minuti += 1
        secondi = 0
    return f"{minuti}:{secondi:02d} min/km"


# ----------------------------------------------------------------------
# Elaborazione di un singolo file .fit (stampa + export opzionale)
# ----------------------------------------------------------------------
def elabora(percorso_fit, mode, intervallo, time_bucket_min, distance_bucket_m,
            id_attivita_arg, export_formato, output_dir):
    """
    Esegue l'analisi completa di un file .fit (lettura, calcolo velocità,
    aggregazione per bucket, stampa a schermo) e, se export_formato non è
    None, esporta la tabella per bucket in output_dir. Ritorna il percorso
    del file esportato, o None se export_formato è None.

    Solleva ValueError se il file non contiene record validi o se nessun
    record cade dentro le finestre attive del timer, così il chiamante
    (elaborazione di un singolo file o di più file con --process-all) può
    decidere come reagire senza terminare il processo.
    """
    punti = leggi_record(percorso_fit)
    if not punti:
        raise ValueError("Nessun record valido trovato nel file.")
    n_punti_file = len(punti)

    finestre = leggi_finestre_attive(percorso_fit)
    punti = filtra_per_finestre(punti, finestre)
    if not punti:
        raise ValueError("Nessun record cade dentro le finestre attive del timer.")

    punti = calcola_velocita(punti, mode)
    bucket_list = aggrega_per_bucket_temporale(punti, intervallo, mode)
    bucket_list = etichetta_bucket_discreti(
        bucket_list, time_bucket_min * 60, distance_bucket_m,
    )

    if id_attivita_arg:
        id_attivita, fonte_id = id_attivita_arg, "argomento --id"
    else:
        id_attivita, fonte_id = leggi_id_attivita(percorso_fit)

    distanza_totale = sum(b['distanza_m'] for b in bucket_list)
    # tempo a timer attivo: somma dei dt tra punti consecutivi dello stesso
    # segmento (le pause valgono 0, vedi calcola_velocita)
    tempo_totale = sum(p['dt_s'] for p in punti)
    # tempo su cui è calcolata la distanza della fonte scelta (in modalità 'h'
    # esclude i secondi dei delta haversine scartati)
    tempo_fonte = sum(b['tempo_s'] for b in bucket_list)
    speed_media_globale_ms = distanza_totale / tempo_fonte if tempo_fonte > 0 else 0.0
    passo_medio_globale = ms_a_pace(speed_media_globale_ms)

    if finestre is None:
        print("Eventi timer: nessuno nel file, uso tutti i record")
    else:
        print(f"Eventi timer: {len(finestre)} finestra/e attiva/e")
        for i, (a, b) in enumerate(finestre, 1):
            fine = str(b) if b is not None else "(aperta, fino a fine file)"
            print(f"  {i}. {a} -> {fine}")
    etichette = {
        's': "standard (distanza ufficiale del dispositivo)",
        'h': "haversine (posizioni GPS)",
        'a': "all (dispositivo, haversine e distanza ufficiale a confronto)",
    }
    print(f"Modalità: {mode} - {etichette[mode]}")
    print(f"ID attività: {id_attivita} (fonte: {fonte_id})")
    print(f"Punti letti (GPS+distanza uniti per timestamp): {n_punti_file}, "
          f"dentro le finestre: {len(punti)}")
    print(f"Bucket da {intervallo:g}s: {len(bucket_list)}")
    fonte_dist = "haversine" if mode == 'h' else "distanza ufficiale"
    print(f"Distanza totale (da {fonte_dist}): {distanza_totale:.1f} m")
    print(f"Tempo totale: {tempo_totale:.0f} s")
    if abs(tempo_fonte - tempo_totale) > 0.5:
        print(f"  (velocità calcolata su {tempo_fonte:.0f} s: esclusi i secondi dei delta haversine scartati)")
    print(f"Velocità media (da distanza/tempo totali): {ms_a_kmh(speed_media_globale_ms):.2f} km/h")
    print(f"Passo medio: {formatta_pace(passo_medio_globale)}")
    print()

    print(f"Dettaglio per bucket (bucket da {intervallo:g}s):")
    if mode == 'a':
        header = (
            f"{'Timestamp':<21} {'Lat':<11} {'Lon':<11} {'Dim. (s)':<9} {'N punti':<9} "
            f"{'Dist. (m)':<11} {'Dist. cum. (m)':<15} {'Tempo (s)':<11} {'Tempo cum. (s)':<15} "
            f"{'Speed dev. (km/h)':<19} {'Passo dev.':<13} "
            f"{'Speed hav. (km/h)':<19} {'Passo hav.':<13} "
            f"{'Speed uff. (km/h)':<19} {'Passo uff.':<13}"
        )
    else:
        sigla = 'hav.' if mode == 'h' else 'uff.'
        header = (
            f"{'Timestamp':<21} {'Lat':<11} {'Lon':<11} {'Dim. (s)':<9} {'N punti':<9} "
            f"{'Dist. (m)':<11} {'Dist. cum. (m)':<15} {'Tempo (s)':<11} {'Tempo cum. (s)':<15} "
            f"{'Speed ' + sigla + ' (km/h)':<19} {'Passo ' + sigla:<13}"
        )
    print(header)
    # decimali mostrati per le distanze: abbastanza da rendere visibile che
    # cumulato = cumulato precedente + distanza del bucket corrente
    dec = 3 if mode == 'h' else 2
    for b in bucket_list:
        lat_txt = f"{b['lat']:.6f}" if b['lat'] is not None else "-"
        lon_txt = f"{b['lon']:.6f}" if b['lon'] is not None else "-"
        base = (f"{str(b['timestamp']):<21} {lat_txt:<11} {lon_txt:<11} "
                f"{b['dim_bucket_s']:<9g} {b['n_punti']:<9} {b['distanza_m']:<11.{dec}f} "
                f"{b['dist_cum_m']:<15.{dec}f} {b['tempo_attivo_s']:<11.0f} {b['tempo_cum_s']:<15.0f} ")
        if mode == 'a':
            pace_device = ms_a_pace(b['speed_device_media_ms'])
            pace_hav = ms_a_pace(b['speed_haversine_bucket_ms'])
            pace_uff = ms_a_pace(b['speed_ufficiale_bucket_ms'])
            print(
                base
                + f"{ms_a_kmh(b['speed_device_media_ms']):<19.2f} {formatta_pace(pace_device):<13} "
                + f"{ms_a_kmh(b['speed_haversine_bucket_ms']):<19.2f} {formatta_pace(pace_hav):<13} "
                + f"{ms_a_kmh(b['speed_ufficiale_bucket_ms']):<19.2f} {formatta_pace(pace_uff):<13}"
            )
        else:
            speed_ms = b['speed_haversine_bucket_ms'] if mode == 'h' else b['speed_ufficiale_bucket_ms']
            print(base + f"{ms_a_kmh(speed_ms):<19.2f} {formatta_pace(ms_a_pace(speed_ms)):<13}")

    stampa_analisi_bucket_discreti(bucket_list, mode, time_bucket_min, distance_bucket_m)

    if export_formato is None:
        return None

    formato = "xlsx" if export_formato == "excel" else export_formato
    nome = nome_file_export(
        id_attivita,
        punti[0]['timestamp'],       # primo record dell'attività
        punti[-1]['timestamp'],      # ultimo record dell'attività
        datetime.now(timezone.utc),  # momento dell'export
    )
    intestazione, righe = righe_export(bucket_list, mode, id_attivita)
    percorso = esporta(intestazione, righe, formato, output_dir, nome)
    print()
    print(f"Esportato ({formato}, {len(righe)} righe): {percorso}")
    return percorso


# ----------------------------------------------------------------------
# --process-all: elabora tutti i file .fit di data/, esporta in
# data/output e sposta i .fit elaborati in data/processed
# ----------------------------------------------------------------------
def elabora_tutti(mode, intervallo, time_bucket_min, distance_bucket_m, export_formato):
    """
    Cerca tutti i file .fit in data/ (non ricorsivo), li elabora uno alla
    volta con elabora() esportando sempre il risultato (formato csv se
    export_formato non è specificato) in data/output, e sposta ogni file
    .fit elaborato con successo in data/processed. Un file che fallisce
    l'elaborazione (es. nessun record valido) viene segnalato e lasciato
    in data/, così da non perdere l'originale.
    """
    cartella_data = Path("data")
    cartella_output = cartella_data / "output"
    cartella_processed = cartella_data / "processed"

    file_fit = sorted(cartella_data.glob("*.fit"))
    if not file_fit:
        print(f"Nessun file .fit trovato in {cartella_data}/.", file=sys.stderr)
        return

    formato = "xlsx" if export_formato == "excel" else (export_formato or "csv")
    n_ok, n_errori = 0, 0

    for percorso in file_fit:
        print(f"=== {percorso.name} ===")
        try:
            elabora(str(percorso), mode, intervallo, time_bucket_min, distance_bucket_m,
                    None, formato, str(cartella_output))
        except ValueError as e:
            print(f"Errore: {e} File lasciato in {cartella_data}/.", file=sys.stderr)
            n_errori += 1
            print()
            continue

        cartella_processed.mkdir(parents=True, exist_ok=True)
        destinazione = cartella_processed / percorso.name
        shutil.move(str(percorso), str(destinazione))
        print(f"Spostato in {destinazione}")
        print()
        n_ok += 1

    print(f"Completato: {n_ok} file elaborati, {n_errori} con errori "
          f"(su {len(file_fit)} trovati).")


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        prog="milestats",
        description="Calcola velocità/passo da un file .fit per bucket temporali da "
                     "T secondi, confrontando velocità del dispositivo, haversine (GPS) "
                     "e distanza cumulativa ufficiale.",
    )
    parser.add_argument(
        "percorso_fit",
        nargs="?",
        default=None,
        help="Percorso del file .fit da analizzare (obbligatorio se non si usa --process-all)",
    )
    parser.add_argument(
        "-t", "--intervallo",
        type=float,
        default=5.0,
        help="Durata del bucket temporale in secondi per l'aggregazione (default: 5)",
    )
    parser.add_argument(
        "-m", "--mode",
        type=str.lower,
        choices=["s", "h", "a"],
        default="s",
        help="s = standard: solo distanza ufficiale del dispositivo, senza calcolare "
             "haversine; h = solo haversine sulle posizioni GPS; a = all: tutte le "
             "fonti a confronto, inclusa la velocità del dispositivo (default: s)",
    )
    parser.add_argument(
        "-e", "--export",
        type=str.lower,
        choices=["csv", "xlsx", "excel"],
        default=None,
        help="Esporta la tabella per bucket in CSV o Excel (excel = xlsx). Nome file: "
             "id_tsstart_tsend_tsnow (timestamp UTC AAAAMMGGTHHMMSS)",
    )
    parser.add_argument(
        "-o", "--output-dir",
        default=".",
        help="Cartella di destinazione dell'export, creata se manca (default: cartella corrente)",
    )
    parser.add_argument(
        "--id",
        dest="id_attivita",
        default=None,
        help="ID attività da usare al posto di quello ricavato dal file .fit",
    )
    parser.add_argument(
        "--time-bucket",
        dest="time_bucket_min",
        type=float,
        default=5.0,
        help="Dimensione (minuti) dei bucket temporali discreti e ordinabili usati "
             "per l'analisi 'come ho performato dopo X minuti' (default: 5)",
    )
    parser.add_argument(
        "--distance-bucket",
        dest="distance_bucket_m",
        type=float,
        default=500.0,
        help="Dimensione (metri) dei bucket di distanza discreti e ordinabili usati "
             "per l'analisi 'come ho performato dopo D metri' (default: 500)",
    )
    parser.add_argument(
        "--process-all",
        action="store_true",
        help="Elabora tutti i file .fit in data/, esporta l'output (nome standard) in "
             "data/output/ e sposta ogni file .fit elaborato in data/processed/. In "
             "questa modalità percorso_fit e --id non vanno indicati; se --export non "
             "è specificato l'export usa csv",
    )
    args = parser.parse_args()
    mode = args.mode

    if args.time_bucket_min <= 0:
        parser.error("--time-bucket deve essere maggiore di 0")
    if args.distance_bucket_m <= 0:
        parser.error("--distance-bucket deve essere maggiore di 0")
    if args.process_all and args.percorso_fit:
        parser.error("--process-all non accetta un percorso_fit esplicito")
    if args.process_all and args.id_attivita:
        parser.error("--id non è compatibile con --process-all")
    if not args.process_all and not args.percorso_fit:
        parser.error("specificare un percorso_fit oppure --process-all")

    if args.process_all:
        elabora_tutti(mode, args.intervallo, args.time_bucket_min, args.distance_bucket_m, args.export)
        return

    try:
        elabora(args.percorso_fit, mode, args.intervallo, args.time_bucket_min,
                args.distance_bucket_m, args.id_attivita, args.export, args.output_dir)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
