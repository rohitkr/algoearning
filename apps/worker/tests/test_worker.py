from ae_worker.__main__ import main


def test_worker_starts():
    assert main([]) == 0
