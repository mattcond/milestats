import pytest

from milestats.main import (
    aggrega_per_bucket_temporale,
    calcola_velocita,
    etichetta_bucket_discreti,
    filtra_per_finestre,
    raggruppa_per_bucket_discreto,
)


def _pipeline(punti_sintetici, t_secondi, mode='a', time_bucket_s=5, distance_bucket_m=10):
    punti = filtra_per_finestre(list(punti_sintetici), None)
    punti = calcola_velocita(punti, mode)
    bucket_list = aggrega_per_bucket_temporale(punti, t_secondi, mode)
    bucket_list = etichetta_bucket_discreti(bucket_list, time_bucket_s, distance_bucket_m)
    return punti, bucket_list


def test_filtra_per_finestre_none_assegna_segmento_zero(punti_sintetici):
    punti = filtra_per_finestre(list(punti_sintetici), None)
    assert all(p['segmento'] == 0 for p in punti)


def test_aggrega_bucket_numero_e_dimensione(punti_sintetici):
    _, bucket_list = _pipeline(punti_sintetici, t_secondi=5)
    # 10 punti (istanti 0..9s) raggruppati in bucket da 5s -> 2 bucket: [0-5), [5-10)
    assert len(bucket_list) == 2
    assert all(b['dim_bucket_s'] == 5 for b in bucket_list)


def test_aggrega_bucket_distanza_cumulata_coerente(punti_sintetici):
    _, bucket_list = _pipeline(punti_sintetici, t_secondi=5)
    # ogni punto avanza di 3 m: la distanza cumulata dell'ultimo bucket deve
    # combaciare con distanza ufficiale totale (27 m sui 9 delta da 3 m)
    assert bucket_list[-1]['dist_cum_m'] == round(9 * 3.0, 3)
    # cumulato = cumulato precedente + valore del bucket corrente, sui numeri
    # esattamente riportati (vedi README)
    atteso = 0.0
    for b in bucket_list:
        atteso = round(atteso + b['distanza_m'], 3)
        assert b['dist_cum_m'] == atteso


def test_aggrega_bucket_velocita_da_distanza_su_tempo_non_media_istantanee(punti_sintetici):
    # velocità ufficiale del bucket = distanza_totale_bucket / tempo_totale_bucket,
    # qui costante a 3 m/s in tutti i punti tranne il primo (delta nullo)
    _, bucket_list = _pipeline(punti_sintetici, t_secondi=5)
    for b in bucket_list:
        assert b['speed_ufficiale_bucket_ms'] == 3.0


def test_etichetta_bucket_discreti_indici_crescenti(punti_sintetici):
    # bucket fini da 1s: l'etichetta segue il tempo attivo cumulato PRIMA di
    # ogni bucket fine, quindi il primo bucket della finestra 5-10s (idx 1)
    # è quello che chiude a tempo_cum_s=6 (inizio a 5s), non quello a 5s
    _, bucket_list = _pipeline(punti_sintetici, t_secondi=1, time_bucket_s=5, distance_bucket_m=1000)
    indici = [b['time_bucket_idx'] for b in bucket_list]
    assert indici == [0, 0, 0, 0, 0, 0, 1, 1, 1, 1]


def test_raggruppa_per_bucket_discreto_somma_correttamente(punti_sintetici):
    _, bucket_list = _pipeline(punti_sintetici, t_secondi=1, time_bucket_s=5, distance_bucket_m=1000)
    gruppi = raggruppa_per_bucket_discreto(bucket_list, 'time_bucket_idx', 'a')
    assert [g['idx'] for g in gruppi] == [0, 1]
    # gruppo 0: 6 bucket fini, 5 dei quali con un delta di 3 m (il primissimo
    # punto dell'attività non ha un delta precedente)
    assert gruppi[0]['distanza_m'] == pytest.approx(15.0)
    assert gruppi[0]['tempo_attivo_s'] == pytest.approx(5.0)
    assert gruppi[0]['speed_ufficiale_ms'] == pytest.approx(3.0)
    # gruppo 1: 4 bucket fini, tutti con un delta di 3 m
    assert gruppi[1]['distanza_m'] == pytest.approx(12.0)
    assert gruppi[1]['speed_ufficiale_ms'] == pytest.approx(3.0)
