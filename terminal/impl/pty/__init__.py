# terminal/impl/pty/ — a terminal with nothing to look at.
#
# A real TerminalPlugin whose windows are pseudo-terminals this process owns.
# It exists because "drive a harness CLI" and "watch what it painted" are
# operations the product already has a contract for (terminal/contract.py), and
# the only implementation of that contract needed a terminal application to be
# installed and on screen. Anything that wants to run a harness without one —
# the live-harness test suite, a headless machine — used to have to re-implement
# typing, keying and screen-reading for itself, which is a second implementation
# of a contract nobody checks against the first.
#
# Requires pyte (requirements-dev.txt): a screen is what a stream of paint
# operations adds up to, and adding them up is a terminal emulator's job.
