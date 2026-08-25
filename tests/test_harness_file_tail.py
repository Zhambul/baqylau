import builtins

from harness.file_tail import CompleteLineTail


def test_unchanged_tail_does_not_reopen_the_file(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"type":"first"}\n')
    tail = CompleteLineTail(str(path))

    first = tail.read(None, 100)
    assert len(first) == 1
    assert tail.read(str(first[0].position), 100) == ()

    opened: list[str] = []
    real_open = builtins.open

    def recording_open(name, *args, **kwargs):
        opened.append(str(name))
        return real_open(name, *args, **kwargs)

    monkeypatch.setattr("harness.file_tail.open", recording_open, raising=False)

    assert tail.read(str(first[0].position), 100) == ()
    assert opened == []

    with path.open("ab") as destination:
        destination.write(b'{"type":"second"}\n')
    changed = tail.read(str(first[0].position), 100)

    assert [line.content for line in changed] == [b'{"type":"second"}\n']
    assert opened == [str(path)]


def test_tail_waits_for_a_complete_appended_line(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"type":"first"}\n')
    tail = CompleteLineTail(str(path))
    first = tail.read(None, 100)

    with path.open("ab") as destination:
        destination.write(b'{"type":"second"}')
    assert tail.read(str(first[0].position), 100) == ()

    with path.open("ab") as destination:
        destination.write(b"\n")
    changed = tail.read(str(first[0].position), 100)

    assert [line.content for line in changed] == [b'{"type":"second"}\n']
