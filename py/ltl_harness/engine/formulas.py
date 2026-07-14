"""DECLARE templates rendered as LTLf formulas (ltlf2dfa syntax).

Unlike the JS prototype — which hand-compiled each template to a fixed
machine — every rule here goes through the real pipeline:

    template + params  ->  LTLf formula  ->  minimal DFA (via MONA)  ->  colored automaton

A rule may also bypass templates entirely and carry a raw `formula`.
Activity params accept lists (any-of), rendered as disjunctions.
"""

from __future__ import annotations

TEMPLATES = {}


def _as_list(x):
    return x if isinstance(x, list) else [x]


def _disj(x):
    acts = _as_list(x)
    return acts[0] if len(acts) == 1 else "(" + " | ".join(acts) + ")"


def _show(x):
    return " | ".join(_as_list(x))


def template(name):
    def deco(fn):
        TEMPLATES[name] = fn
        return fn
    return deco


@template("Existence")
def existence(a, **_):
    return f"F({_disj(a)})", f"'{_show(a)}' happens at least once before the run ends"


@template("Absence")
def absence(a, n, **_):
    if not isinstance(n, int) or n < 0:
        raise ValueError(f"Absence needs integer n >= 0, got {n!r}")
    # "more than n occurrences" = n+1 nested eventualities; forbid it.
    inner = f"F({_disj(a)})"
    for _i in range(n):
        inner = f"F({_disj(a)} & X({inner}))"
    return f"!({inner})", f"'{_show(a)}' happens at most {n} times"


@template("Response")
def response(a, b, **_):
    return (
        f"G({_disj(a)} -> F({_disj(b)}))",
        f"after '{_show(a)}', '{_show(b)}' must eventually follow",
    )


@template("Precedence")
def precedence(a, b, **_):
    # weak until: no b before a, or no b at all
    return (
        f"(!{_disj(b)} U {_disj(a)}) | G(!{_disj(b)})",
        f"'{_show(b)}' can only happen after '{_show(a)}'",
    )


@template("ChainResponse")
def chain_response(a, b, **_):
    return (
        f"G({_disj(a)} -> X({_disj(b)}))",
        f"the step immediately after '{_show(a)}' must be '{_show(b)}'",
    )


@template("NotSuccession")
def not_succession(a, b, **_):
    return (
        f"G({_disj(a)} -> G(!{_disj(b)}))",
        f"once '{_show(a)}' happens, '{_show(b)}' never happens afterwards",
    )


@template("Init")
def init(a, **_):
    return f"{_disj(a)}", f"the first recorded action must be '{_show(a)}'"


def build_formula(template_name: str, params: dict) -> tuple[str, str]:
    """Return (ltlf_formula, english) for a template instance."""
    fn = TEMPLATES.get(template_name)
    if fn is None:
        raise ValueError(
            f"Unknown DECLARE template '{template_name}'. Known: {', '.join(TEMPLATES)}"
        )
    if "b" in params:
        overlap = set(_as_list(params["a"])) & set(_as_list(params["b"]))
        if overlap:
            raise ValueError(
                f"Template {template_name}: activities {sorted(overlap)} appear in both a and b"
            )
    return fn(**params)
