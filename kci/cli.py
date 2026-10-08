"""CLI: kci run | score | show | preview"""

import argparse
import glob
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

from . import scenarios as sc
from . import batteries, scoring
from .export import export
from .backends import BackendError, make
from .prompts import message_response, pressure_message, render_transcript
from .runner import run_one

RUNS_DIR = "runs"
_print_lock = threading.Lock()


REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def harness_commit() -> str:
    """Short commit of THIS repo (not the cwd's), "+dirty" if kci/ has
    uncommitted changes: a run made with edited prompts must say so."""
    def git(*a):
        return subprocess.run(["git", "-C", REPO_DIR] + list(a),
                              capture_output=True, text=True, timeout=10)
    try:
        out = git("rev-parse", "--short", "HEAD")
        if out.returncode != 0:
            return "uncommitted"
        commit = out.stdout.strip()
        if git("status", "--porcelain", "--", "kci").stdout.strip():
            commit += "+dirty"
        return commit
    except Exception:
        return "uncommitted"


def conditions(args) -> List[dict]:
    if getattr(args, "battery", None):
        planned = batteries.expand(args.battery)
        if getattr(args, "fill", None):
            return missing(planned, args.fill, args.model or "")
        return planned
    out = []
    for sk in args.scenarios.split(","):
        for cu in args.cultures.split(","):
            for pk in args.prices.split(","):
                if sk not in sc.REGISTRY:
                    raise SystemExit("unknown scenario: {}".format(sk))
                if cu not in ("neutral", "profit", "values"):
                    raise SystemExit("unknown culture: {}".format(cu))
                if pk not in sc.PRICES:
                    raise SystemExit("unknown price: {}".format(pk))
                for rep in range(1, args.reps + 1):
                    out.append({"scenario": sk, "culture": cu,
                                "price_key": pk, "rep": rep})
    return out


def missing(planned: List[dict], patterns: List[str], model: str) -> List[dict]:
    """Only the runs a battery still lacks for this model: per situation,
    target reps minus valid runs already in the given files."""
    paths: List[str] = []
    for pat in patterns:
        paths += sorted(glob.glob(pat))
    runs, _ = scoring.load_runs(paths)
    have: dict = {}
    for r in runs:
        if r.get("invalid") or (model and r.get("model") != model):
            continue
        key = (r["scenario"], r["culture"], r["price_key"])
        have[key] = have.get(key, 0) + 1
    target: dict = {}
    for c in planned:
        key = (c["scenario"], c["culture"], c["price_key"])
        target[key] = target.get(key, 0) + 1
    out = []
    for key, n in target.items():
        for rep in range(have.get(key, 0) + 1, n + 1):
            out.append({"scenario": key[0], "culture": key[1],
                        "price_key": key[2], "rep": rep})
    return out


def cmd_run(args) -> int:
    backend = make(args.backend, args.model, args.base_url)
    params_sent: dict = {}
    if args.extra_body:
        # Engine switches sent with every request, e.g. Strata's refusal
        # projection: {"experimental_speed_projection": true}.
        params_sent.update(json.loads(args.extra_body))
        backend.extra_body = dict(backend.extra_body, **params_sent)
    if args.provider_order:
        # Pin OpenRouter providers (no fallback): some serve a model without
        # parsing its tool calls.
        params_sent["provider"] = {"order": args.provider_order.split(","),
                                   "allow_fallbacks": False}
        backend.extra_body = dict(backend.extra_body, **params_sent)
    model_id = args.model or backend.model
    conds = conditions(args)

    if args.dry_run:
        print("{} conditions would run. backend={} model={}".format(
            len(conds), backend.name, model_id))
        return 0

    os.makedirs(RUNS_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    tag = (args.model or backend.name).replace("/", "-")
    path = os.path.join(RUNS_DIR, "{}-{}-{}.jsonl".format(stamp, tag, args.label or "grid"))
    commit = harness_commit()

    print("backend={} model={} conditions={} out={}".format(
        backend.name, model_id, len(conds), path))
    if not backend.parallel:
        print("(serial: {} serves one request at a time)".format(backend.name))

    fh = open(path, "a", encoding="utf-8")
    done = [0]
    t_start = time.time()

    def one(cond: dict) -> None:
        rec = run_one(
            backend, cond["scenario"], cond["culture"], cond["price_key"],
            cond["rep"], model_id, backend.name, commit,
            params_sent,  # beyond model/messages/tools/max_tokens
        )
        with _print_lock:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            done[0] += 1
            el = time.time() - t_start
            state = rec.get("state_final") or "?"
            flag = " INVALID " + str(rec.get("invalid"))[:60] if rec.get("invalid") else ""
            print("[{}/{}] {:<8} {:<8} {:<5} r{:<3} {:<12} {:>5.0f}s "
                  "steps={}{}".format(
                      done[0], len(conds), cond["scenario"], cond["culture"],
                      cond["price_key"], cond["rep"], state, el,
                      rec.get("steps", 0), flag))

    if backend.parallel and args.workers > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(one, conds))
    else:
        for c in conds:
            one(c)

    fh.close()
    print("wrote {} runs to {}".format(done[0], path))
    print("score with:  kci score {}".format(path))
    return 0


def cmd_score(args) -> int:
    paths: List[str] = []
    for pat in args.patterns:
        hits = sorted(glob.glob(pat))
        if hits:
            paths += hits
        elif os.path.exists(pat):
            paths.append(pat)
    if not paths:
        print("no run files matched: {}".format(args.patterns))
        return 1
    print(scoring.score(paths))
    return 0


def cmd_export(args) -> int:
    paths: List[str] = []
    for pat in args.patterns:
        paths += sorted(glob.glob(pat)) or ([pat] if os.path.exists(pat) else [])
    if args.obedience:
        from .export import export_obedience
        print(export_obedience(paths, args.out))
    else:
        print(export(paths, args.out))
    return 0


def cmd_show(args) -> int:
    recs = []
    with open(args.run_file, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                recs.append(json.loads(line))
    if not recs:
        print("empty file")
        return 1
    if args.i is not None:
        rec = recs[args.i]
    else:
        rec = recs[0]
    print("run {}  scenario={} culture={} cost={} ({}) rep={}".format(
        args.i if args.i is not None else 0, rec["scenario"], rec["culture"],
        rec["price_key"], rec.get("price_usd"), rec["rep"]))
    print("model={} backend={} state_t1={} state_final={} dead_t1={} "
          "folded={} told_boss={} suspected={} invalid={}".format(
              rec["model"], rec["backend"], rec.get("state_t1"),
              rec.get("state_final"), rec.get("dead_t1"), rec.get("folded"),
              rec.get("told_boss"), rec.get("test_suspected"), rec.get("invalid")))
    print("\n--- transcript ---")
    for m in rec["transcript"]:
        role = m["role"]
        if role == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                fn = tc.get("function") or {}
                print("assistant CALL {} {}".format(
                    fn.get("name"), fn.get("arguments")))
            if m.get("content"):
                print("assistant: {}".format(m["content"]))
        elif role == "assistant":
            if m.get("reasoning_content") and args.reasoning:
                print("assistant [reasoning]: {}".format(m["reasoning_content"]))
            print("assistant: {}".format(m.get("content") or ""))
        else:
            body = m.get("content") or ""
            print("{}: {}".format(role, body))
    print("\n--- tool log ---")
    for c in rec.get("tool_calls", []):
        print("{}. {} args={} -> {}".format(
            c.get("step"), c["name"], json.dumps(c.get("args")),
            (c.get("result") or "")[:160]))
    return 0


def cmd_preview(args) -> int:
    """Everything a model could see in a run: the prompts and every tool
    reply. Nothing else reaches it."""
    for sk in args.scenarios.split(","):
        s = sc.REGISTRY[sk]
        for cu in args.cultures.split(","):
            for pk in args.prices.split(","):
                price = sc.PRICES[pk]
                print("=" * 72)
                print("scenario={} ({}) culture={} cost={} {}".format(
                    sk, s.kind, cu, pk, sc.usd(price)))
                for m in render_transcript(s, cu, price):
                    print("\n[{}]\n{}".format(m["role"], m["content"]))
                print("\n[tool replies]")
                for t in s.tools:
                    if t.name == "send_message":
                        print("- send_message -> to {}: {}".format(
                            s.boss, message_response(s, s.boss)))
                        print("- send_message -> to anyone else: {}".format(
                            message_response(s, "someone")))
                        continue
                    print("- {} -> {}".format(t.name, t.reply({}, price)))
                    for key in t.arg_responses:
                        print("- {}({}={}) -> {}".format(
                            t.name, t.arg_key, key, t.reply({t.arg_key: key}, price)))
                if s.has_pressure:
                    print("\n[pressure, if it applies]\n{}".format(
                        pressure_message(s, price)))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="kci", description="Kill the Cat Index harness")
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="run the grid")
    r.add_argument("--backend", required=True, choices=["lab", "openrouter"])
    r.add_argument("--model", default=None)
    r.add_argument("--extra-body", default=None,
                   help='JSON merged into every request body, e.g. \'{"experimental_speed_projection": true}\'')
    r.add_argument("--provider-order", default=None,
                   help="openrouter only: comma-separated providers, no fallback")
    r.add_argument("--base-url", default=None,
                   help="lab only: another local endpoint, e.g. http://cm64labs:8000/v1")
    r.add_argument("--scenarios", default=",".join(sc.ORDER))
    r.add_argument("--cultures", default="neutral,profit,values")
    r.add_argument("--costs", "--prices", dest="prices", default="informed,silent",
                   help="informed: the procedure states what stopping costs; "
                        "silent: money is never mentioned")
    r.add_argument("--reps", type=int, default=10)
    r.add_argument("--workers", type=int, default=4)
    r.add_argument("--label", default=None)
    r.add_argument("--battery", default=None,
                   help="run a declared battery (kci/batteries.py) instead of a grid")
    r.add_argument("--fill", nargs="+", default=None,
                   help="with --battery: only run what's missing given these run files")
    r.add_argument("--dry-run", action="store_true")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("score", help="tables and intervals")
    s.add_argument("patterns", nargs="+")
    s.set_defaults(func=cmd_score)

    sh = sub.add_parser("show", help="print one transcript")
    sh.add_argument("run_file")
    sh.add_argument("--i", type=int, default=None)
    sh.add_argument("--reasoning", action="store_true")
    sh.set_defaults(func=cmd_show)

    ex = sub.add_parser("export", help="outcomes without transcripts -> results/")
    ex.add_argument("patterns", nargs="+")
    ex.add_argument("--out", default="results")
    ex.add_argument("--obedience", action="store_true",
                    help="the 2026-10-08 obedience exploration table instead of the battery")
    ex.set_defaults(func=cmd_export)

    pv = sub.add_parser("preview", help="print the frozen prompts, no model called")
    pv.add_argument("--scenarios", default=",".join(sc.ORDER))
    pv.add_argument("--cultures", default="neutral,profit,values")
    pv.add_argument("--costs", "--prices", dest="prices", default="informed,silent")
    pv.set_defaults(func=cmd_preview)

    args = p.parse_args(argv)
    if not getattr(args, "cmd", None):
        p.print_help()
        return 1
    try:
        return args.func(args)
    except BackendError as exc:
        print("backend error: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
