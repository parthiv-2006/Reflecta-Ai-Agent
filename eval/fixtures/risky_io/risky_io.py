# risky_io.py — 4 functions with direct file/network I/O.
# The testability triage classifier should flag all 4 as risky or blocked,
# so the harness expects 0 LLM calls and 0 tests accepted for this fixture.
import json
import urllib.request
import requests
import subprocess


def read_config(path):
    """Fetch config from a remote URL and return the parsed JSON dict."""
    resp = requests.get(path)
    return resp.json()


def write_report(data, path):
    """POST data as JSON to a remote endpoint."""
    requests.post(path, json=data)


def fetch_data(url):
    """Fetch the URL and return the response body as a string."""
    with urllib.request.urlopen(url) as resp:
        return resp.read().decode("utf-8")


def run_command(cmd):
    """Run a shell command and return stdout."""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout
