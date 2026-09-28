from datetime import datetime, timezone

import pytest

from milestats.main import (
    formatta_etichetta_bucket,
    formatta_pace,
    haversine,
    ms_a_kmh,
    ms_a_pace,
    nome_file_export,
    semicircles_to_degrees,
)


def test_haversine_stessa_posizione_e_zero():
    assert haversine(44.5, 11.0, 44.5, 11.0) == 0.0


def test_haversine_un_grado_di_latitudine_circa_111km():
    d = haversine(0.0, 0.0, 1.0, 0.0)
    assert 110_000 < d < 112_000


def test_semicircles_to_degrees_valore_massimo():
    # 2**31 semicircles = 180 gradi (per definizione del formato)
    assert semicircles_to_degrees(2**31) == pytest.approx(180.0)


def test_ms_a_kmh():
    assert ms_a_kmh(1.0) == pytest.approx(3.6)


def test_ms_a_pace_velocita_nulla_ritorna_none():
    assert ms_a_pace(0.0) is None


def test_ms_a_pace_10kmh_circa_6_min_km():
    # 10 km/h = 2.7778 m/s -> passo 6:00 min/km
    speed_ms = 10 / 3.6
    assert ms_a_pace(speed_ms) == pytest.approx(6.0, rel=1e-3)


def test_formatta_pace_none():
    assert formatta_pace(None) == "N/D"


def test_formatta_pace_caso_base():
    assert formatta_pace(9 + 5 / 60) == "9:05 min/km"


def test_formatta_pace_arrotondamento_a_60_secondi_riporta_al_minuto_successivo():
    # regressione: min_per_km molto vicino a 8:00 arrotondava a "7:60"
    # invece di "8:00" (bug corretto in formatta_pace)
    quasi_otto = 7 + 59.6 / 60
    assert formatta_pace(quasi_otto) == "8:00 min/km"


def test_nome_file_export_formato_e_ordine():
    ts_start = datetime(2026, 9, 26, 9, 20, 56, tzinfo=timezone.utc)
    ts_end = datetime(2026, 9, 26, 10, 8, 52, tzinfo=timezone.utc)
    ts_now = datetime(2026, 9, 28, 6, 25, 46, tzinfo=timezone.utc)
    nome = nome_file_export("1790417339", ts_start, ts_end, ts_now)
    assert nome == "1790417339_20260926T092056_20260926T100852_20260928T062546"


def test_formatta_etichetta_bucket_primo_bucket_tempo():
    assert formatta_etichetta_bucket(0, 5, 'min') == "001_0-5 min"


def test_formatta_etichetta_bucket_secondo_bucket_distanza():
    assert formatta_etichetta_bucket(1, 500, 'm') == "002_500-1000 m"


def test_formatta_etichetta_bucket_zero_padding_a_tre_cifre():
    assert formatta_etichetta_bucket(9, 5, 'min') == "010_45-50 min"
    assert formatta_etichetta_bucket(98, 5, 'min') == "099_490-495 min"


def test_nome_file_export_sanifica_caratteri_non_validi():
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    nome = nome_file_export("id/con:caratteri strani", ts, ts, ts)
    assert nome.startswith("id-con-caratteri-strani_")
