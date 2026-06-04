"""
testability.py — static, no-LLM triage of whether a target is worth attempting.

Generating + repairing a test costs scarce free-tier LLM quota. Some functions
can never produce a kept test no matter what the model writes: a module that
needs live credentials or network *at import* can't even be collected by pytest,
and a function whose whole job is a network/DB/browser/subprocess call needs
heavy mocking that the free models reliably fail at. This module spots those
*before* any provider call, using only ``ast`` — no execution, no import, no LLM.

Three verdicts:
  - BLOCKED   — importing the module does real I/O / needs secrets, so any
                generated test fails at collection. Never sent to the LLM.
  - RISKY     — the function directly performs network/DB/browser/subprocess/
                file-write I/O. Mockable in principle, but a poor quota bet;
                skipped by default (``--attempt-risky`` overrides).
  - TESTABLE  — everything else; attempted normally.

The signal is deliberately conservative: a call is only "hostile" when its root
name resolves to a hostile *import* or a module-level hostile object. A function
that receives a client/session as a parameter and calls it is dependency
injection — testable, not flagged.
"""

import ast
from dataclasses import dataclass, field

# Verdict levels (plain strings so they serialize straight into the report).
TESTABLE = "testable"
RISKY = "risky"
BLOCKED = "blocked"

# Top-level module name -> category. Matched on the first dotted component of an
# import, so "google.cloud.storage" -> "google" won't false-match; we list the
# real roots people import.
_HOSTILE_MODULES: dict[str, str] = {
    # network
    "requests": "network",
    "httpx": "network",
    "urllib": "network",
    "urllib2": "network",
    "urllib3": "network",
    "aiohttp": "network",
    "socket": "network",
    "http": "network",  # http.client
    "websocket": "network",
    "websockets": "network",
    "ftplib": "network",
    # browser automation
    "playwright": "browser",
    "selenium": "browser",
    "pyppeteer": "browser",
    # database / backend
    "supabase": "database",
    "psycopg2": "database",
    "psycopg": "database",
    "sqlalchemy": "database",
    "pymongo": "database",
    "redis": "database",
    "mysql": "database",
    "asyncpg": "database",
    # cloud / external APIs
    "boto3": "cloud",
    "botocore": "cloud",
    "firebase_admin": "cloud",
    "openai": "cloud",
    "anthropic": "cloud",
    "cohere": "cloud",
    # email / system
    "smtplib": "system",
    "subprocess": "system",
    # config / secrets
    "dotenv": "secrets",
}

# Functions that construct a long-lived client/connection — their presence at
# module scope means importing the module opens a real connection.
_CLIENT_CONSTRUCTORS = frozenset(
    {"create_client", "connect", "Client", "Session", "create_engine", "Redis"}
)

# Top-level calls that are side-effecting but harmless to import (no creds, no
# network) — do NOT block a module just because it does these on import.
_HARMLESS_TOPLEVEL = frozenset(
    {
        "load_dotenv",
        "inject_into_ssl",  # truststore
        "basicConfig",  # logging
        "getLogger",
        "filterwarnings",
        "simplefilter",
        "setrecursionlimit",
        "register",  # atexit.register etc.
    }
)


@dataclass
class Verdict:
    level: str = TESTABLE
    reason: str = ""
    categories: set[str] = field(default_factory=set)


def _import_alias_map(tree: ast.Module) -> dict[str, str]:
    """Map each bound name to the hostile category of the module it refers to.

    ``import requests`` -> {"requests": "network"};
    ``import httpx as h`` -> {"h": "network"};
    ``from supabase import create_client`` -> {"create_client": "database"};
    ``from os import environ`` -> {"environ": "_environ"}.
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                root = a.name.split(".")[0]
                cat = _HOSTILE_MODULES.get(root)
                if cat:
                    aliases[a.asname or root] = cat
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import — local, never hostile
                continue
            root = (node.module or "").split(".")[0]
            cat = _HOSTILE_MODULES.get(root)
            if cat:
                for a in node.names:
                    aliases[a.asname or a.name] = cat
            # Track `from os import environ` so env reads are detectable.
            if root == "os":
                for a in node.names:
                    if a.name == "environ":
                        aliases[a.asname or "environ"] = "_environ"
    return aliases


def _call_root_name(call: ast.Call) -> str | None:
    """Return the leftmost Name of a call's func (``a.b.c()`` -> ``a``)."""
    node = call.func
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _hostile_categories_in_calls(
    calls: list[ast.Call], aliases: dict[str, str]
) -> set[str]:
    cats: set[str] = set()
    for call in calls:
        root = _call_root_name(call)
        if root is None:
            continue
        cat = aliases.get(root)
        if cat and cat != "_environ":
            cats.add(cat)
        # open(..., "w"/"a"/"x") — a filesystem write (read-only open is fine).
        if root == "open" and _is_write_open(call):
            cats.add("filesystem")
    return cats


def _is_write_open(call: ast.Call) -> bool:
    mode = None
    if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant):
        mode = call.args[1].value
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            mode = kw.value.value
    return isinstance(mode, str) and any(c in mode for c in ("w", "a", "x", "+"))


def _iter_import_time(node: ast.AST):
    """Yield descendant nodes that execute at *import time*.

    Recurses into class bodies, ``if``/``for``/``with`` blocks, etc. (all of
    which run when the module is imported) but NOT into function/method bodies
    (those only run when called). A function's decorators and default-argument
    expressions *do* run at import, so those are still visited.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in child.decorator_list:
                yield from _walk(dec)
            for default in list(child.args.defaults) + list(child.args.kw_defaults):
                if default is not None:
                    yield from _walk(default)
            # body is intentionally skipped
        else:
            yield child
            yield from _iter_import_time(child)


def _walk(node: ast.AST):
    yield node
    for child in ast.iter_child_nodes(node):
        yield from _walk(child)


def _module_import_hazards(tree: ast.Module, aliases: dict[str, str]) -> tuple[str, str]:
    """Inspect module import-time code for things that make ``import`` unsafe.

    Returns (category, reason) for the first hazard found, else ("", "").
    """
    for sub in _iter_import_time(tree):
        # Bare ``os.environ["X"]`` / ``environ["X"]`` with no default -> KeyError
        # on import when the var is unset.
        if isinstance(sub, ast.Subscript):
            val = sub.value
            if isinstance(val, ast.Attribute) and val.attr == "environ":
                return "secrets", "reads os.environ at import (needs env vars)"
            if isinstance(val, ast.Name) and aliases.get(val.id) == "_environ":
                return "secrets", "reads environ at import (needs env vars)"
        # Import-time calls.
        if isinstance(sub, ast.Call):
            root = _call_root_name(sub)
            if root is None:
                continue
            # leaf attribute / name (e.g. supabase.create_client -> create_client)
            leaf = sub.func.attr if isinstance(sub.func, ast.Attribute) else root
            if leaf in _HARMLESS_TOPLEVEL:
                continue
            cat = aliases.get(root)
            if cat and cat != "_environ":
                return cat, f"calls {cat} API at import time"
            if leaf in _CLIENT_CONSTRUCTORS:
                # create_client(...)/connect(...) at module scope opens a client.
                return "database", f"constructs a client ({leaf}) at import time"
    return "", ""


def analyze_module(source: str) -> tuple[str, str]:
    """Return (category, reason) if importing the module is unsafe, else ("","")."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return "", ""
    aliases = _import_alias_map(tree)
    return _module_import_hazards(tree, aliases)


def _find_function(tree: ast.Module, qualified_name: str):
    """Locate the FunctionDef/AsyncFunctionDef for ``qualified_name`` (``f`` or
    ``Class.method``)."""
    name = qualified_name.split(".")[-1]
    class_map: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for ln in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                class_map[ln] = node.name
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            cls = class_map.get(node.lineno, "")
            qual = f"{cls}.{node.name}" if cls else node.name
            if qual == qualified_name or node.name == qualified_name:
                return node
    return None


def classify_target(source: str, qualified_name: str) -> Verdict:
    """Static testability verdict for one target. No execution, no LLM."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Can't reason about it; let the normal pipeline try.
        return Verdict(TESTABLE)

    aliases = _import_alias_map(tree)

    # 1. Import-time hazards block every target in the module.
    hazard_cat, hazard_reason = _module_import_hazards(tree, aliases)
    if hazard_cat:
        return Verdict(BLOCKED, f"module {hazard_reason}", {hazard_cat})

    func = _find_function(tree, qualified_name)
    if func is None:
        return Verdict(TESTABLE)

    calls = [n for n in ast.walk(func) if isinstance(n, ast.Call)]
    cats = _hostile_categories_in_calls(calls, aliases)
    if cats:
        label = ", ".join(sorted(cats))
        return Verdict(RISKY, f"directly performs {label} I/O", cats)

    return Verdict(TESTABLE)
