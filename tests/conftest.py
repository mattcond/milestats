from datetime import datetime, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_FIT = REPO_ROOT / "data" / "Corsa_dell_ora_di_pranzo.fit"


@pytest.fixture
def sample_fit():
    return SAMPLE_FIT


@pytest.fixture
def punti_sintetici():
    """
    10 punti sintetici, un secondo l'uno dall'altro, senza passare da
    fitparse: ogni punto avanza di ~3 m in linea retta (lat costante, lon
    che cresce) sia nella distanza ufficiale sia nella posizione GPS, per
    poter verificare deterministicamente aggregazione e velocità senza
    dover leggere un file .fit vero (lento e non isolato dal contenuto
    del file di esempio).
    """
    # fitparse restituisce timestamp NAIVE (UTC implicito, senza tzinfo):
    # replichiamo lo stesso qui, altrimenti l'export xlsx fallirebbe per un
    # motivo che non si presenta mai con un file .fit vero
    t0 = datetime(2026, 1, 1, 12, 0, 0)
    punti = []
    for i in range(10):
        punti.append({
            'timestamp': t0 + timedelta(seconds=i),
            'lat': 44.5,
            'lon': 11.0 + i * 0.00003,  # ~3 m per punto a questa latitudine
            'distance_ufficiale': i * 3.0,
            'speed_device': 3.0,
        })
    return punti
