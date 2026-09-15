#!/usr/bin/env python3
"""Isolated GET_TOOLKIT tests: mock only HTTPS transport/fault injection.

Run on macOS after generating the release SHA256SUMS:
    python3 tests/test_network_download.py [--json /absolute/results.json]

The tested script is copied before replacing its absolute curl path with a
local fixture transport. Archive listing/extraction, the Mach-O SHA helper,
and the distribution SELF_TEST are real. PREPARE/APPLY are never executed.
Python is a test dependency only, not a Recovery runtime dependency.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
ASSET = "macos-depnag-toolkit-v2.1.0.tar.gz"
TOP = "macos-depnag-toolkit"
RELEASE = (
    "https://github.com/jackylam0812/macos-depnag-toolkit/"
    "releases/download/v2.1.0/"
)
RESULTS = []


class DownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run(
            ["/bin/sh", str(REPO / "SELF_TEST.sh")],
            capture_output=True, text=True,
        )
        if result.returncode:
            raise RuntimeError(
                "Build the current SHA256SUMS before running these tests.\n"
                + result.stdout + result.stderr
            )

    def setUp(self):
        # resolve() avoids /var and /tmp symlinks intentionally denied by GET.
        self.temporary = tempfile.TemporaryDirectory(prefix="mdm-network-test-")
        self.root = Path(self.temporary.name).resolve()
        self.payload = self.root / "source" / TOP
        self.payload.mkdir(parents=True)
        # Mirror the release allowlist, never the repository working tree:
        # core-test fixtures, device sessions, and local logs stay outside it.
        names = [
            line.split(None, 1)[1]
            for line in (REPO / "SHA256SUMS").read_text().splitlines()
            if line.strip()
        ]
        for name in names + ["SHA256SUMS"]:
            relative = Path(name)
            self.assertFalse(relative.is_absolute())
            self.assertNotIn("..", relative.parts)
            target = self.payload / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / relative, target)
        self.parent = self.root / "destination parent with spaces"
        self.parent.mkdir()
        self.destination = self.parent / "downloaded toolkit"
        self.transport = self.root / "transport"
        self.transport.mkdir()
        self.archive = self.transport / ASSET
        self.checksum = self.transport / (ASSET + ".sha256")
        self.calls = self.root / "curl-calls.jsonl"
        self.mock_curl = self.root / "mock-curl"
        self.mock_curl.write_text(
            "#!/usr/bin/python3\n"
            "import json,os,pathlib,shutil,sys\n"
            "args=sys.argv[1:]\n"
            "with open(os.environ['MOCK_CURL_CALLS'],'a') as f: "
            "f.write(json.dumps(args)+'\\n')\n"
            "for flag in ['--fail','--location','--silent','--show-error']: "
            "assert flag in args,flag\n"
            "for flag in ['--proto','--proto-redir']: "
            "assert args[args.index(flag)+1]=='=https',flag\n"
            "assert '-k' not in args and '--insecure' not in args\n"
            "out=pathlib.Path(args[args.index('--output')+1]); url=args[-1]\n"
            "assert url.startswith(os.environ['MOCK_RELEASE']),url\n"
            "name=url[len(os.environ['MOCK_RELEASE']):]\n"
            "assert '/' not in name\n"
            "fail=os.environ.get('MOCK_CURL_FAIL','')\n"
            "if fail and ((fail=='archive' and not name.endswith('.sha256')) "
            "or (fail=='checksum' and name.endswith('.sha256'))):\n"
            " out.write_bytes(b'partial transfer'); sys.exit(22)\n"
            "shutil.copyfile(pathlib.Path(os.environ['MOCK_TRANSPORT'])/name,out)\n"
        )
        self.mock_curl.chmod(0o755)
        self.script = self.root / "GET_TOOLKIT.fixture.sh"
        source = (REPO / "GET_TOOLKIT.sh").read_text()
        needle = "CURL='/usr/bin/curl'"
        self.assertEqual(source.count(needle), 1)
        self.script.write_text(source.replace(needle, "CURL='" + str(self.mock_curl) + "'"))
        self.env = os.environ.copy()
        self.env.update({
            "MOCK_TRANSPORT": str(self.transport),
            "MOCK_CURL_CALLS": str(self.calls),
            "MOCK_RELEASE": RELEASE,
        })

    def tearDown(self):
        self.temporary.cleanup()

    def pack(self, extra=None):
        with tarfile.open(self.archive, "w:gz", format=tarfile.USTAR_FORMAT) as archive:
            archive.add(self.payload, arcname=TOP)
            if extra is not None:
                info, body = extra
                archive.addfile(info, io.BytesIO(body))
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.checksum.write_text(digest + "  " + ASSET + "\n")
        return digest

    def extra(self, name, kind=tarfile.REGTYPE, linkname="", mode=0o644):
        info = tarfile.TarInfo(name)
        info.type = kind
        info.linkname = linkname
        info.mode = mode
        return info, b""

    def run_get(self, expected, contains, destination=None):
        dest = str(destination if destination is not None else self.destination)
        command = ["/bin/sh", str(self.script), dest]
        result = subprocess.run(command, capture_output=True, text=True, env=self.env)
        RESULTS.append({
            "test": self.id(), "command": command, "stdin": "",
            "stdout": result.stdout, "stderr": result.stderr,
            "exit_status": result.returncode,
        })
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertIn(contains, result.stdout + result.stderr)
        self.assertFalse(list(self.parent.glob(".mdm-toolkit-download.*")))
        if expected != 0 and destination is None:
            self.assertFalse(self.destination.exists())
        return result

    def test_01_success_spaces_and_real_self_test(self):
        digest = self.pack()
        result = self.run_get(0, "result=toolkit_download_ok")
        self.assertIn("archive_sha256=" + digest, result.stdout)
        self.assertIn("native_write_attempted=no", result.stdout)
        self.assertIn("result=toolkit_self_test_ok", result.stdout)
        self.assertTrue((self.destination / "sha256-file").is_file())
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o700)
        self.assertTrue((self.destination / ".gitignore").is_file())
        self.assertEqual(len(self.calls.read_text().splitlines()), 2)

    def test_02_network_archive_failure_and_partial_cleanup(self):
        self.pack()
        self.env["MOCK_CURL_FAIL"] = "archive"
        self.run_get(2, "HTTPS download failed")

    def test_03_network_checksum_failure_and_partial_cleanup(self):
        self.pack()
        self.env["MOCK_CURL_FAIL"] = "checksum"
        self.run_get(2, "HTTPS download failed")

    def test_04_wrong_checksum(self):
        self.pack()
        self.checksum.write_text("0" * 64 + "  " + ASSET + "\n")
        self.run_get(2, "SHA-256 does not match")

    def test_05_malformed_checksum(self):
        self.pack()
        self.checksum.write_text("not-a-digest  " + ASSET + "\n")
        self.run_get(2, "Invalid release SHA-256")

    def test_06_multiple_checksum_entries(self):
        self.pack()
        self.checksum.write_text(self.checksum.read_text() * 2)
        self.run_get(2, "exactly one entry")

    def test_07_checksum_wrong_asset(self):
        self.pack()
        self.checksum.write_text("0" * 64 + "  another.tar.gz\n")
        self.run_get(2, "different asset")

    def test_08_missing_helper(self):
        (self.payload / "sha256-file").unlink()
        self.pack()
        self.run_get(2, "SHA-256 helper is missing")

    def test_09_helper_not_executable(self):
        (self.payload / "sha256-file").chmod(0o644)
        self.pack()
        self.run_get(2, "SHA-256 helper is missing or not executable")

    def test_10_missing_full_self_test(self):
        (self.payload / "SELF_TEST.sh").unlink()
        self.pack()
        self.run_get(2, "SELF_TEST.sh is missing")

    def test_11_missing_runtime_file(self):
        (self.payload / "lib.sh").unlink()
        self.pack()
        self.run_get(2, "full toolkit self-test failed")

    def test_12_changed_runtime_file(self):
        with (self.payload / "lib.sh").open("a") as stream:
            stream.write("\n# fixture corruption\n")
        self.pack()
        self.run_get(2, "full toolkit self-test failed")

    def test_13_existing_directory_unchanged(self):
        self.destination.mkdir()
        marker = self.destination / "keep.txt"
        marker.write_text("original")
        self.run_get(2, "already exists", self.destination)
        self.assertEqual(marker.read_text(), "original")
        self.assertFalse(self.calls.exists())

    def test_14_existing_file_unchanged(self):
        self.destination.write_text("original")
        self.run_get(2, "already exists", self.destination)
        self.assertEqual(self.destination.read_text(), "original")
        self.assertFalse(self.calls.exists())

    def test_15_symlink_destination_rejected(self):
        target = self.root / "untouched"
        target.mkdir()
        self.destination.symlink_to(target, target_is_directory=True)
        self.run_get(2, "Symlink in destination path", self.destination)
        self.assertEqual(list(target.iterdir()), [])

    def test_16_symlink_parent_rejected(self):
        link = self.root / "parent-link"
        link.symlink_to(self.parent, target_is_directory=True)
        self.run_get(2, "Symlink in destination path", link / "new-directory")
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_17_nonexistent_parent_rejected(self):
        self.run_get(2, "parent directory must already exist", self.root / "absent" / "new")

    def test_18_relative_destination_rejected(self):
        self.run_get(2, "absolute destination", "relative-directory")

    def test_19_parent_component_rejected(self):
        self.run_get(2, "parent component", str(self.parent) + "/../new")

    def test_20_archive_parent_traversal_rejected(self):
        self.pack(self.extra(TOP + "/../../escaped"))
        self.run_get(2, "unsafe path")
        self.assertFalse((self.root / "escaped").exists())

    def test_21_archive_absolute_path_rejected(self):
        self.pack(self.extra("/private/tmp/mdm-network-test-escaped"))
        self.run_get(2, "unsafe path")

    def test_22_archive_outside_top_rejected(self):
        self.pack(self.extra("other-toolkit/file"))
        self.run_get(2, "unsafe path")

    def test_23_archive_symlink_rejected(self):
        self.pack(self.extra(TOP + "/link", tarfile.SYMTYPE, "/private/tmp"))
        self.run_get(2, "Archive contains a link")

    def test_24_archive_hardlink_rejected(self):
        self.pack(self.extra(TOP + "/hard", tarfile.LNKTYPE, TOP + "/lib.sh"))
        self.run_get(2, "Archive contains a link")

    def test_25_archive_duplicate_path_rejected(self):
        self.pack(self.extra(TOP + "/lib.sh"))
        self.run_get(2, "duplicate")

    def test_26_archive_newline_path_rejected(self):
        self.pack(self.extra(TOP + "/evil\nname"))
        self.run_get(2, "unsafe path")

    def test_27_archive_device_rejected(self):
        self.pack(self.extra(TOP + "/device", tarfile.CHRTYPE))
        self.run_get(2, "special file")

    def test_28_archive_special_mode_rejected(self):
        self.pack(self.extra(TOP + "/suid", mode=0o4755))
        self.run_get(2, "special permission mode")

    def test_29_invalid_archive_rejected(self):
        self.pack()
        self.archive.write_bytes(b"this is not a tar file")
        self.run_get(2, "Archive listing failed")

    def test_30_extraction_failure_cleans_partial_work(self):
        self.pack()
        mock_tar = self.root / "mock-tar"
        mock_tar.write_text(
            '#!/bin/sh\ncase "$1" in -xzf) exit 9 ;; esac\n'
            '/usr/bin/tar "$@"\n'
        )
        mock_tar.chmod(0o755)
        self.script.write_text(self.script.read_text().replace(
            "TAR='/usr/bin/tar'", "TAR='" + str(mock_tar) + "'"
        ))
        self.run_get(2, "Archive extraction failed")

    def test_31_no_prepare_or_apply_execution(self):
        marker = self.root / "NATIVE_ACTION_ATTEMPTED"
        script = '#!/bin/sh\nprintf attempted > "' + str(marker) + '"\nexit 99\n'
        for name in ("PREPARE.sh", "APPLY_IN_RECOVERY.sh", "ROLLBACK.sh"):
            (self.payload / name).write_text(script)
        manifest = self.payload / "SHA256SUMS"
        lines = []
        for line in manifest.read_text().splitlines():
            _, name = line.split(None, 1)
            digest = hashlib.sha256((self.payload / name).read_bytes()).hexdigest()
            lines.append(digest + "  " + name)
        manifest.write_text("\n".join(lines) + "\n")
        self.pack()
        self.run_get(0, "result=toolkit_download_ok")
        self.assertFalse(marker.exists())

    def test_32_move_failure_cleans_created_destination(self):
        self.pack()
        self.script.write_text(self.script.read_text().replace(
            '/bin/mv -- "$ITEM" "$DEST/"', '/usr/bin/false'
        ))
        self.run_get(2, "Moving the verified toolkit failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, help="Optional detailed result file outside the release.")
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(DownloadTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "successful": result.wasSuccessful(),
        "runtime": "macOS normal OS; actual Recovery networking remains a separate acceptance step",
        "isolation": "Fixture-only curl substitution; real tar, native helper, and SELF_TEST; no native apply",
        "cases": RESULTS,
    }
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    raise SystemExit(0 if result.wasSuccessful() else 1)
