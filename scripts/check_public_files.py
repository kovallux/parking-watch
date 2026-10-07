"""Check a staged snapshot or branch without printing matched private values."""

import argparse
from pathlib import PurePosixPath
import re
import subprocess
import sys


PATTERNS = {
    "telegram-token": re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b"),
    "github-token": re.compile(r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}\b"),
    "aws-access-key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "personal-home-path": re.compile(r"/" + r"Users/[^/\s\"']+/"),
    "personal-volume-path": re.compile(r"/" + r"Volumes/[^/\s\"']+/"),
    "private-camera-address": re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b"),
    "credential-url": re.compile(r"(?:rtsp|https?)://[^\s\"<>]+:[^\s\"<>]+@"),
}


def git(*args):
    return subprocess.check_output(["git", *args], stderr=subprocess.PIPE)


def private_path(name):
    path = PurePosixPath(name)
    return (path.parts[0] in {"data", "research", "web", ".runtime", ".tools", ".ultralytics"}
            or any(part.startswith(".venv") or part == "__pycache__" for part in path.parts)
            or path.name == "config.local.json" or path.name.endswith(".local.json")
            or path.name == ".DS_Store"
            or (path.name.startswith(".env") and path.name != ".env.example")
            or path.suffix.lower() in {".pt", ".jpg", ".jpeg", ".png", ".webp", ".pyc",
                                       ".pem", ".key", ".p12", ".pfx"})


def scan_text(name, text):
    findings = []
    for number, line in enumerate(text.splitlines(), 1):
        for kind, pattern in PATTERNS.items():
            for match in pattern.finditer(line):
                value = match.group()
                # This exact encoded value tests quoting with a documentation IP.
                if (kind == "credential-url" and name == "tests/test_camera_access.py"
                        and value == "rtsp://" + "admin:test%3A%40%2F@"
                        and "192.0.2.10" in line):
                    continue
                if kind == "credential-url" and "{" in value:
                    continue  # An unevaluated format string is not a stored credential.
                findings.append((name, number, kind))
    return findings


def snapshot(revision=None):
    if revision:
        rows = git("ls-tree", "-r", "-z", revision).split(b"\0")
    else:
        rows = git("ls-files", "--stage", "-z").split(b"\0")
    for row in rows:
        if not row:
            continue
        metadata, name = row.split(b"\t", 1)
        fields = metadata.split()
        oid = fields[2] if revision else fields[1]
        yield name.decode(), oid.decode()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", help="Check this committed tree instead of the index")
    parser.add_argument("--history", action="store_true", help="Check all ancestors of --revision")
    args = parser.parse_args(argv)
    if args.history and not args.revision:
        parser.error("--history requires --revision")
    revisions = (git("rev-list", args.revision).decode().split() if args.history
                 else [args.revision])
    seen = set()
    findings = set()
    for revision in revisions:
        if revision:
            emails = git("show", "-s", "--format=%ae%n%ce", revision).decode().splitlines()
            if any(not address.endswith("@users.noreply.github.com") for address in emails):
                findings.add(("commit " + revision[:12], 0, "personal-author-email"))
        for name, oid in snapshot(revision):
            if (name, oid) in seen:
                continue
            seen.add((name, oid))
            if private_path(name):
                findings.add((name, 0, "local-file"))
                continue
            text = git("cat-file", "blob", oid).decode(errors="replace")
            findings.update(scan_text(name, text))
    for name, number, kind in sorted(findings):
        print(f"{name}:{number}: {kind}")
    print(f"Checked {len(seen)} file versions; {len(findings)} findings.")
    return 1 if findings else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError:
        print("Could not inspect Git. Run from the repository and use an existing revision.", file=sys.stderr)
        raise SystemExit(2) from None
