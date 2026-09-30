from datetime import datetime
from pathlib import Path

import pytest

from milestats.main import (
    campiona_timestamp_meteo,
    centroide_coordinate,
    descrizione_meteo,
    elabora,
    estrai_meteo_campioni,
    etichetta_meteo_bucket,
    main,
)


def _risposta_finta():
    return {
        'hourly': {
            'time': ['2026-09-26T09:00', '2026-09-26T10:00'],
            'temperature_2m': [18.5, 19.2],
            'precipitation': [0.0, 0.1],
            'relative_humidity_2m': [70, 68],
            'wind_speed_10m': [5.4, 6.1],
            'weather_code': [1, 61],
        }
    }


def test_centroide_coordinate_media_solo_punti_con_posizione():
    punti = [{'lat': 44.0, 'lon': 10.0}, {'lat': 46.0, 'lon': 12.0}, {'no_lat': True}]
    lat, lon = centroide_coordinate(punti)
    assert lat == pytest.approx(45.0)
    assert lon == pytest.approx(11.0)


def test_centroide_coordinate_nessun_punto_con_posizione():
    assert centroide_coordinate([{'no_lat': True}]) == (None, None)


def test_campiona_timestamp_meteo_include_sempre_start_e_end():
    ts_start = datetime(2026, 9, 26, 9, 20, 56)
    ts_end = datetime(2026, 9, 26, 10, 8, 52)
    campioni = campiona_timestamp_meteo(ts_start, ts_end)

    assert campioni[0] == ts_start
    assert campioni[-1] == ts_end
    # ogni campione intermedio è esattamente 15 minuti dopo il precedente
    for prima, dopo in zip(campioni[:-2], campioni[1:-1]):
        assert (dopo - prima).total_seconds() == 15 * 60
    # l'ultimo intervallo (verso ts_end) può essere più corto di 15 minuti
    assert (campioni[-1] - campioni[-2]).total_seconds() <= 15 * 60


def test_campiona_timestamp_meteo_start_uguale_end():
    ts = datetime(2026, 1, 1, 12, 0, 0)
    assert campiona_timestamp_meteo(ts, ts) == [ts]


def test_descrizione_meteo():
    assert descrizione_meteo(None) is None
    assert descrizione_meteo(0) == "cielo sereno"
    assert descrizione_meteo(61) == "pioggia leggera"
    assert descrizione_meteo(12345) == "codice 12345"  # sconosciuto ma non un errore


def test_estrai_meteo_campioni_stessa_ora_stesso_dato():
    campioni = [
        datetime(2026, 9, 26, 9, 20, 56),
        datetime(2026, 9, 26, 9, 50, 56),
        datetime(2026, 9, 26, 10, 8, 52),
    ]
    estratti = estrai_meteo_campioni(_risposta_finta(), campioni)

    assert estratti[0]['temperatura_c'] == 18.5
    assert estratti[1]['temperatura_c'] == 18.5  # stessa ora (09:xx) del primo
    assert estratti[2]['temperatura_c'] == 19.2  # ora successiva (10:xx)
    assert estratti[2]['meteo_descrizione'] == "pioggia leggera"


def test_estrai_meteo_campioni_ora_mancante_torna_none():
    campioni = [datetime(2026, 1, 1, 3, 0, 0)]  # ora non presente nella risposta finta
    estratti = estrai_meteo_campioni(_risposta_finta(), campioni)
    assert estratti[0]['temperatura_c'] is None
    assert estratti[0]['meteo_descrizione'] is None


def test_etichetta_meteo_bucket_assegna_il_campione_non_successivo():
    campioni_meteo = estrai_meteo_campioni(_risposta_finta(), [
        datetime(2026, 9, 26, 9, 0, 0),
        datetime(2026, 9, 26, 9, 30, 0),
        datetime(2026, 9, 26, 10, 0, 0),
    ])
    bucket_list = [
        {'timestamp': datetime(2026, 9, 26, 8, 59, 0)},   # prima del primo campione
        {'timestamp': datetime(2026, 9, 26, 9, 15, 0)},   # dentro la finestra del 1° campione
        {'timestamp': datetime(2026, 9, 26, 9, 45, 0)},   # dentro la finestra del 2° campione
        {'timestamp': datetime(2026, 9, 26, 10, 0, 0)},   # esattamente sul 3° campione
    ]
    etichetta_meteo_bucket(bucket_list, campioni_meteo)

    # un timestamp precedente al primo campione riceve comunque il primo
    # campione disponibile (non esistono campioni prima dell'inizio attività)
    assert bucket_list[0]['meteo_temperatura_c'] == 18.5
    assert bucket_list[1]['meteo_temperatura_c'] == 18.5
    assert bucket_list[2]['meteo_temperatura_c'] == 18.5
    assert bucket_list[3]['meteo_temperatura_c'] == 19.2


def test_elabora_con_weather_aggiunge_colonne_meteo(sample_fit, tmp_path, monkeypatch, capsys):
    import milestats.main as m
    monkeypatch.setattr(m, 'chiama_open_meteo', lambda *a, **k: _risposta_finta())

    m.elabora(str(sample_fit), 's', 600, 5.0, 500.0, None, 'csv', str(tmp_path), weather=True)
    out = capsys.readouterr().out

    assert "Meteo (Open-Meteo" in out
    csv_file = next(tmp_path.glob('*.csv'))
    intestazione = csv_file.read_text().splitlines()[0]
    assert 'meteo_temperatura_c' in intestazione
    assert 'meteo_descrizione' in intestazione


def test_elabora_weather_fallito_non_crasha_e_non_ha_colonne(sample_fit, tmp_path, monkeypatch, capsys):
    import milestats.main as m

    def fallisce(*a, **k):
        raise OSError("rete non disponibile")

    monkeypatch.setattr(m, 'chiama_open_meteo', fallisce)

    percorso = m.elabora(str(sample_fit), 's', 600, 5.0, 500.0, None, 'csv', str(tmp_path), weather=True)
    captured = capsys.readouterr()

    assert percorso is not None  # l'export avviene comunque
    assert "Meteo: chiamata a Open-Meteo fallita" in captured.err
    intestazione = Path(percorso).read_text().splitlines()[0]
    assert 'meteo_temperatura_c' not in intestazione


def test_cli_weather_incompatibile_con_merge_output(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr('sys.argv', ['milestats', '--merge-output', '--weather'])
    with pytest.raises(SystemExit):
        main()
