import shutil

import pytest

from milestats.main import main


def _run(monkeypatch, argv):
    monkeypatch.setattr('sys.argv', ['milestats'] + argv)
    main()


def test_singolo_file_stampa_e_esporta_csv(sample_fit, tmp_path, capsys, monkeypatch):
    # -t 600 riduce il file di esempio (~48 min) a poche righe: la pipeline
    # completa (lettura fit, calcolo velocità, bucket, riepilogo, export)
    # resta la stessa ma l'output è piccolo e il test rapido. Un solo run
    # copre sia la stampa a schermo sia l'export, per non fare il parsing
    # del file .fit due volte in due test separati.
    _run(monkeypatch, [str(sample_fit), '-t', '600', '-e', 'csv', '-o', str(tmp_path)])
    out = capsys.readouterr().out
    assert "ID attività:" in out
    assert "Passo medio:" in out
    assert "Performance per bucket temporale da 5 min" in out
    assert len(list(tmp_path.glob('*.csv'))) == 1


@pytest.mark.parametrize("args_extra", [[], ['-m', 'h'], ['-m', 'a']])
def test_modalita_supportate_non_sollevano_eccezioni(sample_fit, args_extra, monkeypatch):
    _run(monkeypatch, [str(sample_fit), '-t', '600'] + args_extra)


def test_percorso_fit_e_process_all_sono_mutuamente_esclusivi(sample_fit, monkeypatch):
    monkeypatch.setattr('sys.argv', ['milestats', str(sample_fit), '--process-all'])
    with pytest.raises(SystemExit):
        main()


def test_nessun_argomento_e_un_errore(monkeypatch):
    monkeypatch.setattr('sys.argv', ['milestats'])
    with pytest.raises(SystemExit):
        main()


def test_time_bucket_non_positivo_e_un_errore(sample_fit, monkeypatch):
    monkeypatch.setattr('sys.argv', ['milestats', str(sample_fit), '--time-bucket', '0'])
    with pytest.raises(SystemExit):
        main()


def test_process_all_elabora_esporta_e_sposta(sample_fit, tmp_path, monkeypatch, capsys):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    shutil.copy(sample_fit, data_dir / "corsa1.fit")
    shutil.copy(sample_fit, data_dir / "corsa2.fit")

    monkeypatch.chdir(tmp_path)
    _run(monkeypatch, ['--process-all', '-t', '600'])
    out = capsys.readouterr().out

    assert (data_dir / "processed" / "corsa1.fit").exists()
    assert (data_dir / "processed" / "corsa2.fit").exists()
    assert not (data_dir / "corsa1.fit").exists()
    assert len(list((data_dir / "output").glob('*.csv'))) == 2
    assert "Completato: 2 file elaborati, 0 con errori" in out


def test_process_all_file_non_valido_resta_in_data_e_non_blocca_gli_altri(
    sample_fit, tmp_path, monkeypatch, capsys,
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    shutil.copy(sample_fit, data_dir / "buono.fit")
    (data_dir / "rotto.fit").write_bytes(b"non e' un file .fit valido")

    monkeypatch.chdir(tmp_path)
    _run(monkeypatch, ['--process-all', '-t', '600'])
    out = capsys.readouterr().out

    assert (data_dir / "processed" / "buono.fit").exists()
    assert (data_dir / "rotto.fit").exists()  # lasciato sul posto, non spostato
    assert "Completato: 1 file elaborati, 1 con errori" in out


def test_process_all_id_esplicito_non_ammesso(sample_fit, tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr('sys.argv', ['milestats', '--process-all', '--id', 'x'])
    with pytest.raises(SystemExit):
        main()
