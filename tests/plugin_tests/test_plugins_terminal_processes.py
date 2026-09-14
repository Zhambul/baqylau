# Copyright (c) 2026 Zhambyl Yermagambet
"""Terminal window process tests."""

from __future__ import annotations

from terminal import processes
from terminal.impl.kitty import metadata as kitty_metadata, remote_text, remote_tree
from terminal.models.values import WindowProcess

PROGRAM_PROCESS_ID = 100
SERVER_PROCESS_ID = 200
ROOT_PROCESS_ID = 1
CHILD_PROCESS_ID = 2
GRANDCHILD_PROCESS_ID = 3
UNKNOWN_PROCESS_ID = 9


def test_kitty_window_reports_its_child_process() -> None:
    """Report the process that speaks for the session, not only the program.

    Kitty names the program it started. OpenCode2 starts its native server as a
    child, and that child is the process that reports. A window that hides it
    cannot be attached to its own session.
    """
    window_info = remote_tree.KittyWindowInfo(
        id=7,
        foreground_processes=[
            remote_tree.KittyProcess(pid=PROGRAM_PROCESS_ID, cmdline=["/bin/opencode2", "--standalone"]),
        ],
    )
    server = WindowProcess(SERVER_PROCESS_ID, ("/lib/opencode2.exe", "serve"))
    process_tree = processes.ProcessTree({PROGRAM_PROCESS_ID: (server,)})

    reported = kitty_metadata.window_processes(window_info, process_tree)

    assert [process.process_id for process in reported] == [PROGRAM_PROCESS_ID, SERVER_PROCESS_ID]


def test_process_tree_reads_every_depth_once() -> None:
    """Read a whole branch, and read a process that names itself only once."""
    process_tree = processes.ProcessTree({
        ROOT_PROCESS_ID: (WindowProcess(CHILD_PROCESS_ID, ("child",)),),
        CHILD_PROCESS_ID: (
            WindowProcess(GRANDCHILD_PROCESS_ID, ("grandchild",)),
            WindowProcess(ROOT_PROCESS_ID, ("loop",)),
        ),
    })

    below = process_tree.descendants(ROOT_PROCESS_ID)

    assert [process.process_id for process in below] == [CHILD_PROCESS_ID, GRANDCHILD_PROCESS_ID]
    assert process_tree.descendants(None) == ()
    assert process_tree.descendants(UNKNOWN_PROCESS_ID) == ()


def test_kitty_paste_writes_one_paste() -> None:
    """Write the marks of the paste here, so no empty paste follows the text.

    Kitty closes `--bracketed-paste` with a second, empty paste. OpenCode2 reads
    the clipboard of the person for a paste that carries nothing, and the prompt
    then holds what the person last copied.
    """
    assert remote_text.text_payload("one", bracketed=True) == b"\x1b[200~one\x1b[201~"
    assert remote_text.text_payload("one", bracketed=False) == b"one"
