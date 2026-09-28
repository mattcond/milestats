import pytest

from milestats.main import (
    aggrega_per_bucket_temporale,
    calcola_velocita,
    etichetta_bucket_discreti,
    filtra_per_finestre,
    raggruppa_per_bucket_discreto,
)


def _pipeline(punti_sintetici, t_secondi, mode='a', time_bucket_min=5 / 60, distance_bucket_m=10):
    punti = filtra_per_finestre(list(punti_sintetici), None)
    punti = calcola_velocita(punti, mode)
    bucket_list = aggrega_per_bucket_temporale(punti, t_secondi, mode)
    bucket_list = etichetta_bucket_discreti(bucket_list, time_bucket_min, distance_bucket_m)
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


def test_etichetta_bucket_discreti_formato_e_cambio_al_momento_giusto(punti_sintetici):
    # bucket fini da 1s, macro da 5s (5/60 min): l'etichetta segue il tempo
    # attivo cumulato PRIMA di ogni bucket fine, quindi il cambio di
    # finestra avviene al settimo bucket fine (indice 6), non al sesto
    _, bucket_list = _pipeline(punti_sintetici, t_secondi=1, time_bucket_min=5 / 60, distance_bucket_m=1000)
    etichette = [b['time_bucket_idx'] for b in bucket_list]

    assert etichette[0].startswith("001_")
    assert etichette[6].startswith("002_")
    assert etichette[:6] == [etichette[0]] * 6
    assert etichette[6:] == [etichette[6]] * 4
    # zero padding a 3 cifre -> l'ordinamento testuale coincide con quello
    # temporale
    assert etichette[0] < etichette[6]


def test_raggruppa_per_bucket_discreto_somma_correttamente(punti_sintetici):
    _, bucket_list = _pipeline(punti_sintetici, t_secondi=1, time_bucket_min=5 / 60, distance_bucket_m=1000)
    gruppi = raggruppa_per_bucket_discreto(bucket_list, 'time_bucket_idx', 'a')
    assert len(gruppi) == 2
    assert gruppi[0]['idx'] < gruppi[1]['idx']
    # gruppo 0: 6 bucket fini, 5 dei quali con un delta di 3 m (il primissimo
    # punto dell'attività non ha un delta precedente)
    assert gruppi[0]['distanza_m'] == pytest.approx(15.0)
    assert gruppi[0]['tempo_attivo_s'] == pytest.approx(5.0)
    assert gruppi[0]['speed_ufficiale_ms'] == pytest.approx(3.0)
    # gruppo 1: 4 bucket fini, tutti con un delta di 3 m
    assert gruppi[1]['distanza_m'] == pytest.approx(12.0)
    assert gruppi[1]['speed_ufficiale_ms'] == pytest.approx(3.0)
