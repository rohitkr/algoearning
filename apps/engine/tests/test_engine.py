from ae_engine.__main__ import main


def test_engine_starts_and_exits_cleanly():
    assert main(["--once"]) == 0
