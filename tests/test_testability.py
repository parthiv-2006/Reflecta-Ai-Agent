from reflecta.testability import (
    BLOCKED,
    RISKY,
    TESTABLE,
    analyze_module,
    classify_target,
)


def test_pure_function_is_testable():
    src = "def add(a, b):\n    return a + b\n"
    v = classify_target(src, "add")
    assert v.level == TESTABLE


def test_function_calling_requests_is_risky():
    src = (
        "import requests\n\n"
        "def fetch(url):\n"
        "    r = requests.get(url)\n"
        "    return r.json()\n"
    )
    v = classify_target(src, "fetch")
    assert v.level == RISKY
    assert "network" in v.categories


def test_function_using_supabase_is_risky():
    src = (
        "from supabase import create_client\n\n"
        "def seed(client, row):\n"
        "    return do_work(row)\n\n"
        "def insert(table, row):\n"
        "    c = create_client('u', 'k')\n"
        "    return c.table(table).insert(row)\n"
    )
    # insert constructs+uses a db client inside the function body.
    assert classify_target(src, "insert").level == RISKY
    # seed just receives a client param and calls a local helper -> testable.
    assert classify_target(src, "seed").level == TESTABLE


def test_param_injected_client_is_not_flagged():
    # Dependency injection: session is a parameter, not an import. Testable.
    src = (
        "def fetch(session, url):\n"
        "    return session.get(url).json()\n"
    )
    assert classify_target(src, "fetch").level == TESTABLE


def test_module_level_env_read_blocks_all_targets():
    src = (
        "import os\n"
        "API_KEY = os.environ['CANLII_API_KEY']\n\n"
        "def pure(x):\n"
        "    return x + 1\n"
    )
    v = classify_target(src, "pure")
    assert v.level == BLOCKED
    assert "import" in v.reason


def test_module_level_client_construction_blocks():
    src = (
        "from supabase import create_client\n"
        "import os\n"
        "client = create_client('url', 'key')\n\n"
        "def helper(x):\n"
        "    return x * 2\n"
    )
    assert classify_target(src, "helper").level == BLOCKED


def test_harmless_toplevel_calls_do_not_block():
    # load_dotenv / truststore / logging at import are safe — module not blocked,
    # and a pure function in it stays testable.
    src = (
        "import logging\n"
        "from dotenv import load_dotenv\n"
        "import truststore\n"
        "load_dotenv()\n"
        "truststore.inject_into_ssl()\n"
        "logging.basicConfig()\n\n"
        "def clean(text):\n"
        "    return text.strip().lower()\n"
    )
    assert analyze_module(src) == ("", "")
    assert classify_target(src, "clean").level == TESTABLE


def test_async_network_function_is_risky():
    src = (
        "import httpx\n\n"
        "async def get(url):\n"
        "    async with httpx.AsyncClient() as c:\n"
        "        return await c.get(url)\n"
    )
    assert classify_target(src, "get").level == RISKY


def test_file_write_is_risky_but_read_is_not():
    write_src = (
        "def dump(path, data):\n"
        "    with open(path, 'w') as f:\n"
        "        f.write(data)\n"
    )
    assert classify_target(write_src, "dump").level == RISKY

    read_src = (
        "def load(path):\n"
        "    with open(path) as f:\n"
        "        return f.read()\n"
    )
    assert classify_target(read_src, "load").level == TESTABLE


def test_subprocess_call_is_risky():
    src = (
        "import subprocess\n\n"
        "def run_it(cmd):\n"
        "    return subprocess.run(cmd, capture_output=True)\n"
    )
    assert classify_target(src, "run_it").level == RISKY


def test_unparseable_source_defaults_testable():
    assert classify_target("def broken(:\n", "broken").level == TESTABLE


def test_method_target_classified():
    src = (
        "import requests\n\n"
        "class Api:\n"
        "    def fetch(self, url):\n"
        "        return requests.get(url)\n"
        "    def pure(self, x):\n"
        "        return x + 1\n"
    )
    assert classify_target(src, "Api.fetch").level == RISKY
    assert classify_target(src, "Api.pure").level == TESTABLE
