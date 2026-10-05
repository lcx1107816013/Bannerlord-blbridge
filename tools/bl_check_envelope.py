"""Validate REAL in-game responses against the envelope invariants (Python side of EnvelopeTest).

WHY THIS EXISTS (closes the gap left after PROGRESS 40):
  The C# assertion `tools/jsontest/EnvelopeTest.cs` validates envelopes built by
  `Protocol.Success/Failure` -- i.e. the CONSTRUCTOR. It never sees a response that actually
  travelled through the file-IPC channel. So "the constructor is fine" and "real responses are
  fine" were still two different claims, joined by nothing.

  This script closes that link: it takes the responses the game REALLY wrote into
  `commands/done/*.json` and applies the SAME invariants to them.

INVARIANTS (mirrors EnvelopeTest.cs + BridgeProtocol.cs doc):
  1. parses as JSON
  2. top level is an object
  3. protocolVersion == 1
  4. id is a non-empty string
  5. ok is a bool
  6. ok == true  <=> error is null   (and the `error` key must be present)
  7. on failure, error has non-empty code + message + bool outcomeUncertain
  8. process is present with pid/role/runToken/processStartedUtc/assemblyVersion/mvid/loadedSha256
  9. result key is present

CONTROL (per project discipline -- a validator that never reports failure is worthless):
  `--inject` mutates a sample of the real responses (drop `process`, flip `ok`, bump the
  version, blank the code, truncate) and requires the validator to catch EVERY one. If any
  injected defect slips through, the run FAILS.

Also cross-checks the two ids: the file name must equal the response's own id (that is how the
transport works -- the consumer looks up by id).
"""
import argparse
import io
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DONE = os.path.join(os.path.expanduser("~"), "Documents",
                    "Mount and Blade II Bannerlord", "BlBridge", "commands", "done")

REQUIRED_PROCESS_KEYS = ("pid", "role", "runToken", "processStartedUtc",
                         "assemblyVersion", "mvid", "loadedSha256")


def validate(text):
    """Return a list of violations; empty list == valid."""
    v = []
    if not text or not text.strip():
        return ["empty response"]
    try:
        obj = json.loads(text)
    except Exception as ex:
        return ["not valid JSON (%s)" % type(ex).__name__]

    if not isinstance(obj, dict):
        return ["top level is %s, not an object" % type(obj).__name__]

    if obj.get("protocolVersion") != 1:
        v.append("protocolVersion != 1 (got %r)" % obj.get("protocolVersion"))

    rid = obj.get("id")
    if not isinstance(rid, str) or not rid:
        v.append("id missing or empty")

    ok = obj.get("ok")
    if not isinstance(ok, bool):
        v.append("ok is not a bool (got %r)" % (ok,))
    else:
        has_err_key = "error" in obj
        err = obj.get("error")
        if not has_err_key:
            v.append("no 'error' key (must be explicit, null on success)")
        if ok and err is not None:
            v.append("ok=true but error != null")
        if (not ok) and err is None:
            v.append("ok=false but error is null")
        if isinstance(err, dict):
            code = err.get("code")
            if not isinstance(code, str) or not code:
                v.append("error.code missing or empty")
            if not isinstance(err.get("message"), str):
                v.append("error.message missing or not a string")
            if not isinstance(err.get("outcomeUncertain"), bool):
                v.append("error.outcomeUncertain missing or not a bool")
        elif err is not None and not ok:
            v.append("error is not an object (got %s)" % type(err).__name__)

    proc = obj.get("process")
    if not isinstance(proc, dict):
        v.append("process missing or not an object")
    else:
        for k in REQUIRED_PROCESS_KEYS:
            if k not in proc:
                v.append("process missing %s" % k)
        if not isinstance(proc.get("pid"), int) or (isinstance(proc.get("pid"), int)
                                                    and proc["pid"] <= 0):
            v.append("process.pid not a positive int (got %r)" % (proc.get("pid"),))

    if "result" not in obj:
        v.append("no 'result' key")

    return v


def load_real(limit):
    if not os.path.isdir(DONE):
        return []
    names = sorted(os.listdir(DONE))[:limit] if limit else sorted(os.listdir(DONE))
    out = []
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(DONE, name)
        try:
            with io.open(path, encoding="utf-8-sig", errors="replace") as fh:
                out.append((name, fh.read()))
        except Exception:
            pass
    return out


# --- injected defects: each MUST be caught -----------------------------------
def injections(text):
    """Yield (label, mutated_text, must_mention) pairs."""
    pairs = []
    try:
        obj = json.loads(text)
    except Exception:
        return pairs
    if not isinstance(obj, dict):
        return pairs

    def dump(o):
        return json.dumps(o, ensure_ascii=False)

    o = json.loads(text)
    o.pop("process", None)
    pairs.append(("drop process", dump(o), "process"))

    o = json.loads(text)
    o["ok"] = not o.get("ok", True)
    pairs.append(("flip ok", dump(o), "error"))

    o = json.loads(text)
    o["protocolVersion"] = 2
    pairs.append(("bump protocolVersion", dump(o), "protocolVersion"))

    o = json.loads(text)
    if isinstance(o.get("process"), dict):
        o["process"].pop("loadedSha256", None)
    pairs.append(("drop process.loadedSha256", dump(o), "loadedSha256"))

    o = json.loads(text)
    o.pop("result", None)
    pairs.append(("drop result", dump(o), "result"))

    # NOTE: the error-shape injections only make sense on a FAILURE response -- a successful one
    # legitimately has `error: null`, so "blank the code" would be a no-op and the control would
    # report a false MISS. (First version of this script made exactly that mistake; the injection
    # control caught it, which is the whole point of having one.)
    if isinstance(obj.get("error"), dict):
        o = json.loads(text)
        o["error"]["code"] = ""
        pairs.append(("blank error.code", dump(o), "code"))

        o = json.loads(text)
        o["error"].pop("outcomeUncertain", None)
        pairs.append(("drop error.outcomeUncertain", dump(o), "outcomeUncertain"))

        o = json.loads(text)
        o["error"].pop("message", None)
        pairs.append(("drop error.message", dump(o), "message"))

    pairs.append(("truncate", text[:max(1, len(text) // 2)], "JSON"))
    return pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="only the first N responses (0 = all)")
    ap.add_argument("--inject", action="store_true", help="also run the injection control")
    ap.add_argument("--sample", type=int, default=40, help="how many real responses to mutate")
    args = ap.parse_args()

    real = load_real(args.limit)
    print("=== real responses found in commands/done: %d ===" % len(real))
    if not real:
        print("!! none -- start the game / send some commands first")
        return 2

    bad = 0
    bad_samples = []
    ok_true = ok_false = 0
    id_mismatch = 0
    for name, text in real:
        v = validate(text)
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                if obj.get("ok") is True:
                    ok_true += 1
                elif obj.get("ok") is False:
                    ok_false += 1
                expect_id = name[:-5]          # strip .json
                if obj.get("id") and obj["id"] != expect_id:
                    id_mismatch += 1
        except Exception:
            pass
        if v:
            bad += 1
            if len(bad_samples) < 10:
                bad_samples.append((name, v))

    print("   ok=true : %d" % ok_true)
    print("   ok=false: %d" % ok_false)
    print("   INVALID : %d" % bad)
    print("   id != file name: %d" % id_mismatch)
    for name, v in bad_samples:
        print("     %s -> %s" % (name, "; ".join(v)))

    fail = 0
    if bad:
        print("\n!! %d real responses violate the envelope invariants" % bad)
        fail += 1
    if id_mismatch:
        print("\n!! %d responses whose id does not match the file name" % id_mismatch)
        fail += 1

    # --- injection control ---------------------------------------------------
    if args.inject:
        sample = real[: args.sample]
        caught = missed = total = 0
        print("\n=== injection control (mutate %d real responses) ===" % len(sample))
        for name, text in sample:
            for label, mutated, must in injections(text):
                total += 1
                v = validate(mutated)
                if v and any(must.lower() in x.lower() for x in v):
                    caught += 1
                else:
                    missed += 1
                    if missed <= 8:
                        print("   MISSED [%s] on %s -> got %s" % (label, name, v))
        print("   injected=%d  caught=%d  missed=%d" % (total, caught, missed))
        if total == 0 or missed:
            print("   !! injection control FAILED (a validator that cannot go red is worthless)")
            fail += 1
        else:
            print("   OK: every injected defect was caught (the validator does go red)")

    print()
    print("RESULT: %s" % ("FAIL" if fail else "PASS"))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
