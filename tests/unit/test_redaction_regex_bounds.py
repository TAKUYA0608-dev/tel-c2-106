"""Redaction regexes must not match inside a longer digit / decimal run (unbounded-regex class).

A phone-number (or ID-number) redactor with no boundary eats the zeros of an amount such as
90000000.0 and turns it into 9[REDACTED].0. Each pattern below is the module-level object the
node/service actually uses, so the guarantee is checked on the real thing, not a copy.
"""

import importlib

CASES = [("src.nodes.post_process_node", "_PHONE"), ("src.nodes.pre_process_node", "_PHONE")]

UNTOUCHED = [
    "amount 90000000.0 yen",
    "claim 1234567.89 paid",
    "ratio 0.6000000000000001 x",
    "premium ¥90,000,000.00 due",
    "pi 3.14159265358979 x",
]

PROBES = UNTOUCHED + [
    "total 123456789012 units",
    "id 20260916123456 ok",
    "count 0000000000 z",
    "tel 03-1234-5678 ok",
]


def _patterns():
    for module_name, var in CASES:
        yield module_name, var, getattr(importlib.import_module(module_name), var)


def test_decimal_amounts_are_not_redacted():
    for module_name, var, rx in _patterns():
        for text in UNTOUCHED:
            assert rx.sub("[X]", text) == text, f"{module_name}.{var} matched inside a number: {text!r}"


def test_no_match_is_a_strict_sub_run_of_a_number():
    # The property the bounds provide: whatever the redactor matches, it never starts or ends next to
    # another digit or a decimal point (a whole 12-digit run may legitimately be an ID or a phone).
    for module_name, var, rx in _patterns():
        for text in PROBES:
            for m in rx.finditer(text):
                left = text[m.start() - 1] if m.start() > 0 else " "
                right = text[m.end()] if m.end() < len(text) else " "
                assert (
                    left not in "0123456789." and right not in "0123456789."
                ), f"{module_name}.{var} matched {m.group(0)!r} inside {text!r}"


POSITIVES = [
    "tel 03-1234-5678 ok",
    "tel 090-1234-5678 ok",
    "tel 09012345678 ok",
    "tel +81 90 1234 5678 ok",
    "my number 123456789012 here",
    "account 1234567890 here",
    "ref 1234567 x",
]


def test_the_redactor_still_matches_its_own_target():
    for module_name, var, rx in _patterns():
        assert any(rx.search(text) for text in POSITIVES), f"{module_name}.{var} no longer matches any positive probe"
