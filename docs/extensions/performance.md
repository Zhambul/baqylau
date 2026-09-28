# Extension performance report

This report records the P08-T04 measurements. Run them again with:

```sh
python -m tests.perf.extension_benchmark --events 300 --output report.json
python -m tests.perf.extension_benchmark --events 1000 --profiles one
python -m tests.perf.extension_benchmark --events 50 --large --profiles none two
BAQYLAU_EXTENSION_CALL_SECONDS=1 python -m tests.perf.extension_benchmark --events 20 --profiles slow-worker
```

Each profile starts a private daemon, enables real worker packages (the lifecycle fixture: `keep` keeps every fact, `rawreplace` also rewrites raw input, and `slow` waits 3 seconds in each canonical call), and receives a fixed capture of native Claude Code hooks: one session start and then finished turns. Delay is the time from one hook's POST until its raw event has a verdict. Pending is the largest count of raw events without a verdict while the capture was processed. Memory is the resident memory of the daemon and its workers. Idle CPU is the CPU time of the daemon and its workers in 10 seconds with no input. `tests/extension_host/test_benchmark_smoke.py` keeps the benchmark working.

Environment (2026-09-25): macOS-26.6.2-arm64-arm-64bit, 8 CPUs, Python 3.12.1, call deadline 30 (default). The 300-event capture has SHA-256 `00636671b36d26d59ddbd320bd590f499e4040935e4168e728c1934bd5c5c597`.

| Case | Profile | Events | Events/s | Delay median (ms) | Delay p95 (ms) | Pending max | Memory (MB) | Idle CPU (s) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | none | 301 | 94.59 | 12 | 21 | 0 | 255 | 0.11 |
| baseline | one | 301 | 8.95 | 232 | 288 | 120 | 204 | 0.11 |
| baseline | two | 301 | 5.16 | 397 | 443 | 188 | 268 | 0.11 |
| long session | one | 1001 | 2.45 | 757 | 771 | 624 | 263 | 0.04 |
| large content (256 KiB) | none | 51 | 33.97 | 12 | 20 | 14 | 469 | 0.14 |
| large content (256 KiB) | two | 51 | 11.66 | 95 | 113 | 39 | 305 | 0.11 |
| slow worker, 30 s deadline | slow-worker | 21 | 0.35 | 3060 | 3070 | 20 | 526 | 0.14 |
| slow worker, 1 s deadline | slow-worker | 21 | 4.02 | 12 | 24 | 20 | 214 | 0.15 |

| after the change | none | 1001 | 111.88 | 23 | 27 | 1 | 150 | 0.10 |
| after the change | one | 1001 | 88.22 | 25 | 27 | 11 | 216 | 0.08 |
| after the change | two | 1001 | 58.68 | 26 | 44 | 259 | 287 | 0.10 |

## Findings

1. One no-op canonical transformer lowers throughput from about 95 to 9 events per second at 300 events, and to 2.5 events per second at 1,000 events (757 ms for each input). The cost grows with the session: each canonical call receives the scope's prior facts, up to 1,000 facts or 1 MiB (`extensions/models/interpretation_snapshot.py`). For each input the host decodes these facts twice (the capture and the commit check) and validates them again at each model level, because `WireModel` uses `revalidate_instances="always"`. A synthetic check shows that building a 1,000-fact snapshot costs 42 ms with revalidation and 0.2 ms without it; the SDK's tests require revalidation, because it refuses instances made with `model_construct` or `model_copy(update=...)`.
2. A slow canonical transformer blocks all core input: with a 3-second call and the 30-second deadline, throughput falls to 0.35 events per second. With a 1-second deadline the calls fail, the extension becomes failed and is disabled after 5 failures, and the delay returns to 12 ms.
3. Large content (256 KiB messages) costs 3 times the core time with two extensions (95 ms compared with 12 ms); memory stays below 500 MB.
4. The idle cost is low in every profile: about 0.1 CPU second in 10 seconds, with no difference between zero and two extensions.

## Limits and decisions

The measured limits that stay as they are: 1,000 open jobs for each owner, 1,000 prior facts or 1 MiB of prior state, 1 MiB of worker log output, and 5 consecutive failures before a failure disable.

Changes made from this evidence (2026-09-25):

- Prior state (finding 1): a canonical transformer now declares `prior_state: true` in its processing selection when it reads the scope's earlier facts (`ProcessingSelection.prior_state`, default false; only a canonical transformer can set it). The host captures and checks prior facts only when an active transformer declares it (`extensions/prior_state_selection.py`); another transformer gets an empty snapshot that makes no completeness claim. The SDK's instance revalidation stays as it is. The 1,000-event runs after the change: one no-op extension goes from 2.45 to 88 events per second and from 757 ms to 25 ms delay; two extensions reach 59 events per second.
- Transform deadline (finding 2): pure calls on the engine thread (translation, raw and canonical transforms, projection, and projection transforms) now have their own deadline, `BAQYLAU_EXTENSION_TRANSFORM_SECONDS`, default 5 seconds: 6.5 times the slowest normal call measured (771 ms at the prior-fact bound). It is never more than `BAQYLAU_EXTENSION_CALL_SECONDS`, which stays 30 seconds for commands, queries, presentation, and activation. A call that is slower than normal but inside the deadline still delays core input; the health limit handles repeated timeouts.

Still the user's decision:

- Shutdown (P08-T03): a daemon stop drains a running command, so it can wait up to the command deadline.
