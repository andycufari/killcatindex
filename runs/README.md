# runs/

> From `v0.1` on, transcripts are kept locally and not published: `runs/*.jsonl`
> is gitignored. Outcomes (one row per run, no transcripts) are in
> [`results/`](../results/). The files below are the calibration runs up to
> `v0-frozen`; they remain available in that tag.

Every run made up to `v0-frozen`, kept whole, including the ones from designs we dropped.
Each line of a `.jsonl` file is one run: the condition, the full transcript
(reasoning included when the API returned it), the tool calls and the
outcome fields. Score any file with `python3 tools/kci_main.py score FILE`.

Everything below was made **before** the `v0-frozen` commit, during
calibration, so each record says `"harness_commit": "uncommitted"`. The
version labels match [SPEC §10](../SPEC.md#10-calibration-log).

| File | Model | Version | What it is |
|---|---|---|---|
| `20261005-195552-lab-smoke` | Flash-Next 177B | v0-a (narrated) | 3-run smoke test |
| `20261005-195808-lab-pilot` | Flash-Next 177B | v0-a (narrated) | 27-run pilot. Dropped: the briefing induced the decision |
| `20261005-205415-lab-smoke2` | Flash-Next 177B | v0-b (empirical, quote tool) | 4-run smoke test |
| `20261005-205900-lab-pilot2` | Flash-Next 177B | v0-b | 36-run pilot. Twin had the "must not go ahead" wording |
| `20261005-213029-lab-sweep` | Flash-Next 177B | v0-b | Price sweep USD 100 to 1M, neutral. Stopped at 49 of 60 |
| `20261005-223525-local-brain-qwen38-27b` | Qwen3.8 27B | v0-b, price ladder | 1 run of a battery interrupted when the design changed |
| `20261005-224110-local-brain-qwen38-27b` | Qwen3.8 27B | v0-c | Battery. Its twin runs used the old wording, superseded by `-twin2` |
| `20261005-225204-…-gemma4-31b` | Gemma 4 31B FP8 | v0-c | Battery. Its twin runs used the old wording, superseded by `-twin2` |
| `20261005-225340-…-gemma4-31b-twin2` | Gemma 4 31B FP8 | v0-c | Twin re-run with the parallel wording |
| `20261005-225741-local-brain-qwen38-27b-twin2` | Qwen3.8 27B | v0-c | Twin re-run with the parallel wording |
| `20261005-230103-lab-flashnext-battery` | Flash-Next 177B | v0-c (final wording) | Battery |

Local engines:
- Flash-Next 177B: Strata 0.1.35, DASLab IQ3_S, 2× RTX 3090.
- Qwen3.8 27B: vLLM, `cyankiwi/Qwen3.8-27B-AWQ-INT4`, 2× RTX 3090.
- Gemma 4 31B: vLLM, `RedHatAI/gemma-4-31B-it-FP8-block`, 2× RTX 3090.
