#!/usr/bin/env python3
"""Independent, isolated tests for ONE_CLICK.sh scene orchestration.

Run on macOS with /usr/bin/python3 tests/test_one_click.py. No native state or
real Shared directory is used. Only copied tool paths, identity, ownership,
mount bindings, observation tools and HTTPS transport are replaced. The real
core transaction, SHA helper, archive extraction and full SELF_TEST execute.
Fixture-only checksum manifests are regenerated after documented bindings.
Python is a developer test dependency, not a Recovery runtime dependency.
"""
import copy
import datetime
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest


REPO = Path(__file__).resolve().parents[1]
RELATIVE = 'private/var/db/ConfigurationProfiles/Settings/com.apple.mdm.depnag.plist'
UUID = '11111111-2222-4333-8444-555555555555'
UUID2 = 'AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE'
SYNTHETIC = {
    'History': ['synthetic one-click event', 'Unicode preservation 测试'],
    'NagUI_502': {'DateCompleted': datetime.datetime(2026, 9, 15, 5, 54, 24), 'Result': 0},
    'Unrelated': {'array': [True, False, 27, b'\x00\xff'], 'string': 'retained'},
}
RESULTS = []
SOURCE_HASHES = {}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def executable(path, text):
    Path(path).write_text(text)
    Path(path).chmod(0o755)


def snapshot(path):
    p = Path(path)
    s = p.stat()
    return {'sha256': sha(p), 'uid': s.st_uid, 'gid': s.st_gid,
            'mode': s.st_mode & 0o7777, 'flags': s.st_flags}


class OneClickTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for name in ['ONE_CLICK.sh', 'GET_TOOLKIT.sh', 'SELF_TEST.sh', 'lib.sh',
                     'PREPARE.sh', 'APPLY_IN_RECOVERY.sh', 'ROLLBACK.sh',
                     'STATUS.sh', 'VERIFY_AFTER_BOOT.sh', 'sha256-file']:
            SOURCE_HASHES[name] = sha(REPO / name)
        result = subprocess.run([str(REPO / 'sha256-file'), '--self-test'],
                                capture_output=True, text=True)
        if result.returncode or result.stdout != 'SELF_TEST_OK\n':
            raise RuntimeError('The unmodified native SHA helper self-test failed.')

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='mdm-one-click-test-')
        self.root = Path(self.temporary.name).resolve()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.live = self.root / 'live' / 'Data'
        self.live_work = self.live / 'Users' / 'Shared' / 'mdm-nag-recovery'
        self.volumes = self.root / 'Volumes'
        self.volumes.mkdir()
        self.volume = self.volumes / 'Restored Data with spaces'
        self.registry = self.root / 'volumes.json'
        self.registry.write_text('{}')
        self.native_writes = self.root / 'native-writes.jsonl'
        self.curl_calls = self.root / 'curl-calls.jsonl'
        self.payload = self.root / 'archive source' / 'macos-depnag-toolkit'
        self.payload.mkdir(parents=True)
        self.transport = self.root / 'transport'
        self.transport.mkdir()
        self.env = dict(os.environ, MOCK_ROOT=str(self.root), MOCK_REGISTRY=str(self.registry),
                        MOCK_NATIVE_WRITES=str(self.native_writes), MOCK_CURL_CALLS=str(self.curl_calls),
                        MOCK_UID='0', MOCK_TRANSPORT=str(self.transport),
                        MOCK_NATIVE_FAULT='', MOCK_CURL_FAIL='', MOCK_LOG_RC='0',
                        MOCK_UNTRUSTED_PATH='', MOCK_UNTRUSTED_META='', MOCK_POINTER_FAULT='')
        self.make_tools()
        self.make_payload()
        self.script = self.root / 'ONE CLICK fixture.sh'
        shutil.copy2(self.payload / 'ONE_CLICK.sh', self.script)
        self.work = None
        self.target = None
        self.record = {'test': self.id(), 'fixture_only': True, 'commands': [],
                       'binding_changes': self.transforms, 'source_sha256': dict(SOURCE_HASHES)}

    def tearDown(self):
        self.record['native_write_calls'] = self.writes()
        self.record['https_calls'] = self.http_calls()
        RESULTS.append(self.record)
        self.temporary.cleanup()

    def make_tools(self):
        executable(self.bin / 'id', '#!/bin/sh\nprintf "%s\\n" "$MOCK_UID"\n')
        executable(self.bin / 'diskutil', '''#!/usr/bin/python3
import json,os,plistlib,sys
r=json.load(open(os.environ['MOCK_REGISTRY'])); p=sys.argv[-1]
if p not in r: sys.exit(1)
sys.stdout.buffer.write(plistlib.dumps(r[p]))
''')
        executable(self.bin / 'stat', '''#!/usr/bin/python3
import os,subprocess,sys
a=sys.argv[1:]
if len(a)>=3 and a[0]=='-f' and a[1]=='%u:%Lp':
 p=a[-1]
 if p==os.environ.get('MOCK_UNTRUSTED_PATH',''):
  print(os.environ.get('MOCK_UNTRUSTED_META','502:755'));sys.exit(0)
 r=subprocess.run(['/usr/bin/stat','-f','%Lp',p],capture_output=True,text=True)
 if r.returncode: sys.stderr.write(r.stderr);sys.exit(r.returncode)
 print('0:'+r.stdout.strip());sys.exit(0)
os.execv('/usr/bin/stat',['stat']+a)
''')
        executable(self.bin / 'log', '''#!/bin/sh
if [ "$MOCK_LOG_RC" -ne 0 ]; then echo 'Injected fixture log failure' >&2; exit "$MOCK_LOG_RC"; fi
printf '2026-09-15 17:41:00 mdmclient DEPNag: Removing CoreFollowUp\\n'
''')
        executable(self.bin / 'ps', '#!/bin/sh\nprintf "/sbin/launchd\\n"\n')
        executable(self.bin / 'sysctl', '#!/bin/sh\nprintf "{ sec = 1789465200, usec = 0 } fixture boot\\n"\n')
        executable(self.bin / 'cat', '''#!/usr/bin/python3
import json,os,pathlib,sys,time
out=os.fstat(1); target=None
for mount in json.load(open(os.environ['MOCK_REGISTRY'])):
 p=pathlib.Path(mount)/'private/var/db/ConfigurationProfiles/Settings/com.apple.mdm.depnag.plist'
 try: s=p.stat()
 except FileNotFoundError: continue
 if (s.st_dev,s.st_ino)==(out.st_dev,out.st_ino): target=p;break
if target is not None:
 with open(os.environ['MOCK_NATIVE_WRITES'],'a') as f:
  f.write(json.dumps({'target':str(target),'input':sys.argv[1:]})+'\\n')
 root=pathlib.Path(os.environ['MOCK_ROOT']); marker=root/'fault-used'
 fault=os.environ.get('MOCK_NATIVE_FAULT','')
 if fault and not marker.exists():
  marker.write_text('1');sys.stdout.buffer.write(b'PARTIAL-WRITE');sys.stdout.buffer.flush()
  if fault in ['signal_wait','signal_wait_rollback_failure']:
   (root/'native-write-ready').write_text('1')
   if os.environ.get('MOCK_NATIVE_HOLD')=='1':
    end=time.monotonic()+15
    while not (root/'release-native-write').exists() and time.monotonic()<end:time.sleep(0.01)
   else:time.sleep(1.2)
  sys.exit(74)
 if fault=='signal_wait_rollback_failure':sys.exit(75)
os.execv('/bin/cat',['cat']+sys.argv[1:])
''')
        executable(self.bin / 'mktemp', '''#!/usr/bin/python3
import os,sys
if os.environ.get('MOCK_POINTER_FAULT')=='mktemp' and any('/.last-applied.' in a for a in sys.argv[1:]):
 sys.stderr.write('Injected pointer mktemp failure\\n');sys.exit(1)
os.execv('/usr/bin/mktemp',['mktemp']+sys.argv[1:])
''')
        executable(self.bin / 'mv', '''#!/usr/bin/python3
import os,sys
if os.environ.get('MOCK_POINTER_FAULT')=='mv' and sys.argv[-1].endswith('/LAST_APPLIED_SESSION_NAME.txt'):
 sys.stderr.write('Injected pointer rename failure\\n');sys.exit(1)
os.execv('/bin/mv',['mv']+sys.argv[1:])
''')
        executable(self.bin / 'curl', '''#!/usr/bin/python3
import json,os,pathlib,shutil,sys,time
a=sys.argv[1:]
with open(os.environ['MOCK_CURL_CALLS'],'a') as f:f.write(json.dumps(a)+'\\n')
for flag in ['--fail','--location','--silent','--show-error']:assert flag in a,flag
for flag in ['--proto','--proto-redir']:assert a[a.index(flag)+1]=='=https',flag
assert '-k' not in a and '--insecure' not in a
out=pathlib.Path(a[a.index('--output')+1]);url=a[-1];name=url.rsplit('/',1)[-1]
if name=='GET_TOOLKIT.sh':assert url==os.environ['MOCK_GET_URL'],url
else:assert url.startswith(os.environ['MOCK_RELEASE']+'/'),url
root=pathlib.Path(os.environ['MOCK_ROOT']);pause=os.environ.get('MOCK_CURL_PAUSE','')
if pause and name=='GET_TOOLKIT.sh':
 (root/'download-ready').write_text('1');time.sleep(float(pause))
fail=os.environ.get('MOCK_CURL_FAIL','')
if fail and (fail=='all' or fail==name or (fail=='archive' and name.endswith('.tar.gz'))):
 out.write_bytes(b'partial transfer');sys.exit(22)
shutil.copyfile(pathlib.Path(os.environ['MOCK_TRANSPORT'])/name,out)
''')

    def make_payload(self):
        required = re.search(r"REQUIRED_FILES='([^']*)'", (REPO / 'SELF_TEST.sh').read_text()).group(1).splitlines()
        names = list(dict.fromkeys(required + ['ONE_CLICK.sh']))
        self.transforms = {}
        for name in names:
            p = self.payload / name
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / name, p)
        mapping = {
            '/System/Volumes/Data': str(self.live),
            '/Users/Shared/mdm-nag-recovery': str(self.live_work),
            "VOLUMES_ROOT='/Volumes'": "VOLUMES_ROOT='" + str(self.volumes) + "'",
            '/Volumes/': str(self.volumes) + '/',
            '/usr/bin/id': str(self.bin / 'id'),
            '/usr/sbin/diskutil': str(self.bin / 'diskutil'),
            '/usr/bin/stat': str(self.bin / 'stat'),
            '/usr/bin/log': str(self.bin / 'log'),
            '/usr/sbin/sysctl': str(self.bin / 'sysctl'),
            '/bin/ps': str(self.bin / 'ps'),
            '/usr/bin/curl': str(self.bin / 'curl'),
            '/bin/cat': str(self.bin / 'cat'),
            '/usr/bin/mktemp': str(self.bin / 'mktemp'),
            '/bin/mv': str(self.bin / 'mv'),
        }
        # Keep the Recovery WORK=$DATA/Users/Shared/... concatenation intact;
        # only the quoted standalone LIVE_WORK constant is rebound above.
        mapping.pop('/Users/Shared/mdm-nag-recovery')
        mapping["LIVE_WORK='/Users/Shared/mdm-nag-recovery'"] = "LIVE_WORK='" + str(self.live_work) + "'"
        for p in self.payload.glob('*.sh'):
            # Preserve literal CR guards; universal-newline reads would change
            # production behavior before the intended fixture substitutions.
            text = p.read_bytes().decode('utf-8')
            counts = {}
            old = "LIVE_TARGET='/private/var/db/ConfigurationProfiles/Settings/com.apple.mdm.depnag.plist'"
            counts['live target'] = text.count(old)
            text = text.replace(old, "LIVE_TARGET='" + str(self.live / RELATIVE) + "'")
            for old, new in mapping.items():
                counts[old] = text.count(old)
                text = text.replace(old, new)
            p.write_bytes(text.encode('utf-8'))
            self.transforms[p.name] = counts
        self.version = re.search(r"VERSION='([^']+)'", (self.payload / 'ONE_CLICK.sh').read_text()).group(1)
        self.asset = 'macos-depnag-toolkit-' + self.version + '.tar.gz'
        self.env['MOCK_GET_URL'] = re.search(r"GET_URL='([^']+)'", (self.payload / 'ONE_CLICK.sh').read_text()).group(1)
        self.env['MOCK_RELEASE'] = 'https://github.com/jackylam0812/macos-depnag-toolkit/releases/download/' + self.version
        (self.payload / 'SHA256SUMS').write_text(''.join(sha(self.payload / n) + '  ' + n + '\n' for n in required))
        shutil.copy2(self.payload / 'GET_TOOLKIT.sh', self.transport / 'GET_TOOLKIT.sh')
        archive = self.transport / self.asset
        with tarfile.open(archive, 'w:gz', format=tarfile.USTAR_FORMAT) as t:
            t.add(self.payload, arcname='macos-depnag-toolkit')
        (self.transport / (self.asset + '.sha256')).write_text(sha(archive) + '  ' + self.asset + '\n')

    def add_volume(self, path=None, disabled='absent', uuid=UUID, actual_mount=None):
        p = path or self.volume
        p.mkdir(parents=True, exist_ok=True)
        (p / 'Users' / 'Shared').mkdir(parents=True, exist_ok=True)
        target = p / RELATIVE
        target.parent.mkdir(parents=True, exist_ok=True)
        data = copy.deepcopy(SYNTHETIC)
        if disabled != 'absent': data['Disabled'] = disabled
        target.write_bytes(plistlib.dumps(data, fmt=plistlib.FMT_BINARY, sort_keys=False))
        target.chmod(0o640)
        registry = json.loads(self.registry.read_text())
        registry[str(p)] = {'VolumeUUID': uuid, 'MountPoint': str(actual_mount or p),
                            'VolumeName': p.name, 'FilesystemType': 'apfs'}
        self.registry.write_text(json.dumps(registry))
        return target

    def normal(self, disabled='absent'):
        self.target = self.add_volume(self.live, disabled)
        self.work = self.live_work
        return snapshot(self.target)

    def recovery(self, disabled='absent'):
        self.target = self.add_volume(self.volume, disabled)
        self.work = self.volume / 'Users' / 'Shared' / 'mdm-nag-recovery'
        return snapshot(self.target)

    @property
    def cache(self): return self.work / ('toolkit-' + self.version)

    @property
    def pointer(self): return self.work / 'LAST_APPLIED_SESSION_NAME.txt'

    def warm_cache(self):
        self.work.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copytree(self.payload, self.cache)
        self.cache.chmod(0o700)

    def writes(self):
        return [json.loads(x) for x in self.native_writes.read_text().splitlines()] if self.native_writes.exists() else []

    def http_calls(self):
        return [json.loads(x) for x in self.curl_calls.read_text().splitlines()] if self.curl_calls.exists() else []

    def run_scene(self, *args, expected=0, contains=None):
        command = ['/bin/sh', str(self.script), *map(str, args)]
        r = subprocess.run(command, input='', text=True, capture_output=True, env=self.env, timeout=45)
        self.record['commands'].append({'command': command, 'stdin': '', 'stdout': r.stdout,
                                       'stderr': r.stderr, 'exit_status': r.returncode})
        self.assertEqual(r.returncode, expected, r.stdout + r.stderr)
        if contains is not None: self.assertIn(contains, r.stdout + r.stderr)
        if self.work is not None:
            self.assertFalse((self.work / '.operation.lock').exists(), r.stdout + r.stderr)
            self.assertFalse(list(self.work.glob('.bootstrap.*')))
            self.assertFalse(list(self.work.glob('.last-applied.*')))
        return r

    def assert_unchanged(self, before):
        self.assertEqual(snapshot(self.target), before)
        self.assertEqual(self.writes(), [])

    def test_01_no_arguments_is_help_only(self):
        self.run_scene(contains='Usage:')
        self.assertEqual(self.http_calls(), [])
        self.assertFalse(self.live_work.exists())

    def test_02_unknown_scene_and_extra_arguments(self):
        for args in [('unknown',), ('prepare', '/ignored'), ('check', '/ignored'),
                     ('apply', 'a', 'b'), ('rollback', 'a', 'b', 'c')]:
            with self.subTest(args=args): self.run_scene(*args, expected=2)
        self.assertEqual(self.http_calls(), [])

    def test_03_prepare_normal_false_only_downloads_and_status(self):
        before = self.normal(False)
        self.run_scene('prepare', contains='result=ready_for_recovery')
        self.assert_unchanged(before)
        self.assertFalse((self.work / 'sessions').exists())
        self.assertFalse(self.pointer.exists())
        self.assertEqual(len(self.http_calls()), 3)

    def test_04_prepare_normal_true_no_session(self):
        before = self.normal(True)
        self.run_scene('prepare', contains='result=already_disabled')
        self.assert_unchanged(before)
        self.assertFalse((self.work / 'sessions').exists())

    def test_05_prepare_reuses_verified_cache_without_network(self):
        before = self.normal()
        self.warm_cache()
        self.env['MOCK_CURL_FAIL'] = 'all'
        self.run_scene('prepare', contains='toolkit_cache=verified')
        self.assert_unchanged(before)
        self.assertEqual(self.http_calls(), [])

    def test_06_normal_apply_and_rollback_rejected(self):
        before = self.normal(False)
        self.run_scene('apply', self.live, expected=2, contains='only in Recovery')
        self.run_scene('rollback', self.live, expected=2, contains='only in Recovery')
        self.assert_unchanged(before)
        self.assertFalse(self.work.exists())
        self.assertEqual(self.http_calls(), [])

    def test_07_recovery_prepare_and_check_rejected(self):
        before = self.recovery()
        self.run_scene('prepare', expected=2, contains='normally booted')
        self.run_scene('check', expected=2, contains='normally booted')
        self.assert_unchanged(before)

    def test_08_nonroot_normal_prepare_rejected(self):
        before = self.normal()
        self.env['MOCK_UID'] = '502'
        self.run_scene('prepare', expected=2, contains='with sudo')
        self.assert_unchanged(before)
        self.assertFalse(self.work.exists())

    def test_09_nonroot_recovery_apply_rejected(self):
        before = self.recovery()
        self.env['MOCK_UID'] = '502'
        self.run_scene('apply', self.volume, expected=2, contains='as root')
        self.assert_unchanged(before)

    def test_10_check_preserves_success_status(self):
        before = self.normal(True)
        self.warm_cache()
        self.run_scene('check', contains='result=disabled_with_matching_boot_log')
        self.assert_unchanged(before)

    def test_11_check_preserves_review_status(self):
        before = self.normal(False)
        self.warm_cache()
        self.run_scene('check', expected=1, contains='result=review_required')
        self.assert_unchanged(before)

    def test_12_check_preserves_error_status(self):
        before = self.normal(True)
        self.warm_cache()
        self.env['MOCK_LOG_RC'] = '7'
        self.run_scene('check', expected=2, contains='result=log_query_incomplete')
        self.assert_unchanged(before)

    def test_13_explicit_apply_and_rollback_restore_exact_bytes(self):
        before = self.recovery(False)
        self.run_scene('apply', self.volume, contains='result=one_click_applied')
        self.assertTrue(plistlib.loads(self.target.read_bytes())['Disabled'])
        name = self.pointer.read_text().strip()
        session = self.work / 'sessions' / name
        self.assertEqual(sha(session / 'ORIGINAL_FILE.plist'), before['sha256'])
        self.assertEqual(plistlib.loads((session / 'MANIFEST.plist').read_bytes())['DataVolumeUUID'], UUID)
        self.run_scene('rollback', self.volume, contains='result=one_click_rolled_back')
        self.assertEqual(snapshot(self.target), before)
        self.assertEqual(self.pointer.read_text().strip(), name)
        self.assertTrue(session.is_dir())

    def test_14_unique_auto_selected_volume_and_ignored_nonmount(self):
        self.recovery()
        ignored = self.volumes / 'Not really mounted'
        self.add_volume(ignored, actual_mount=self.volume)
        self.run_scene('apply', contains='selected_data_volume=' + str(self.volume))
        self.assertTrue(self.pointer.is_file())

    def test_15_multiple_auto_candidates_rejected_without_download(self):
        before = self.recovery()
        self.add_volume(self.volumes / 'Second Data', uuid=UUID2)
        self.run_scene('apply', expected=2, contains='Select one mounted Data volume explicitly')
        self.assert_unchanged(before)
        self.assertEqual(self.http_calls(), [])

    def test_16_no_auto_candidate_rejected_without_download(self):
        self.run_scene('apply', expected=2, contains='candidate_data_volume=none')
        self.assertEqual(self.http_calls(), [])

    def test_17_explicit_unmounted_subdirectory_rejected(self):
        before = self.recovery()
        registry = json.loads(self.registry.read_text())
        registry[str(self.volume)]['MountPoint'] = str(self.volumes)
        self.registry.write_text(json.dumps(registry))
        self.run_scene('apply', self.volume, expected=2, contains='actual volume mount point')
        self.assert_unchanged(before)

    def test_18_already_true_does_not_create_session_or_pointer(self):
        before = self.recovery(True)
        self.warm_cache()
        self.run_scene('apply', self.volume, contains='result=already_disabled')
        self.assert_unchanged(before)
        self.assertFalse(self.pointer.exists())
        self.assertFalse((self.work / 'sessions').exists())

    def test_19_repeated_apply_retains_original_backup_and_pointer(self):
        self.recovery()
        self.warm_cache()
        self.run_scene('apply', self.volume, contains='result=one_click_applied')
        pointer = self.pointer.read_bytes()
        sessions = list((self.work / 'sessions').iterdir())
        backup = (sessions[0] / 'ORIGINAL_FILE.plist').read_bytes()
        writes = len(self.writes())
        self.run_scene('apply', self.volume, contains='last_applied_session_pointer=unchanged')
        self.assertEqual(self.pointer.read_bytes(), pointer)
        self.assertEqual(list((self.work / 'sessions').iterdir()), sessions)
        self.assertEqual((sessions[0] / 'ORIGINAL_FILE.plist').read_bytes(), backup)
        self.assertEqual(len(self.writes()), writes)

    def test_20_explicit_session_rollback_without_pointer(self):
        before = self.recovery()
        self.warm_cache()
        self.run_scene('apply', self.volume)
        name = self.pointer.read_text().strip()
        self.pointer.unlink()
        self.run_scene('rollback', self.volume, name, contains='result=one_click_rolled_back')
        self.assertEqual(snapshot(self.target), before)
        self.assertFalse(self.pointer.exists())

    def test_21_failed_apply_retains_prior_pointer_and_fresh_backup(self):
        self.recovery()
        self.warm_cache()
        self.run_scene('apply', self.volume)
        pointer = self.pointer.read_bytes()
        first = self.work / 'sessions' / pointer.decode().strip()
        first_backup = (first / 'ORIGINAL_FILE.plist').read_bytes()
        changed = copy.deepcopy(SYNTHETIC)
        changed['History'].append('synthetic later baseline')
        self.target.write_bytes(plistlib.dumps(changed, fmt=plistlib.FMT_BINARY))
        before = snapshot(self.target)
        self.env['MOCK_NATIVE_FAULT'] = 'partial'
        self.run_scene('apply', self.volume, expected=1, contains='rollback=verified')
        self.assertEqual(snapshot(self.target), before)
        self.assertEqual(self.pointer.read_bytes(), pointer)
        self.assertEqual((first / 'ORIGINAL_FILE.plist').read_bytes(), first_backup)
        sessions = list((self.work / 'sessions').iterdir())
        self.assertEqual(len(sessions), 2)
        fresh = next(p for p in sessions if p != first)
        self.assertEqual(sha(fresh / 'ORIGINAL_FILE.plist'), before['sha256'])

    def test_22_malicious_pointer_names_are_not_followed(self):
        before = self.recovery()
        self.warm_cache()
        (self.work / 'sessions').mkdir(mode=0o700)
        for value in ['../escape\n', '/absolute\n', 'one\ntwo\n', '\n', 'a/b\n']:
            with self.subTest(value=value):
                self.pointer.write_text(value)
                self.run_scene('rollback', self.volume, expected=2)
        self.assert_unchanged(before)

    def test_23_explicit_malicious_session_name_rejected_early(self):
        before = self.recovery()
        for name in ['../escape', '/absolute', '..', 'a/b', 'a\nb']:
            with self.subTest(name=name):
                self.run_scene('rollback', self.volume, name, expected=2, contains='SESSION_NAME')
        self.assert_unchanged(before)
        self.assertEqual(self.http_calls(), [])

    def test_24_rollback_missing_pointer_rejected(self):
        before = self.recovery()
        self.warm_cache()
        (self.work / 'sessions').mkdir(mode=0o700)
        self.run_scene('rollback', self.volume, expected=2, contains='No successful apply is recorded')
        self.assert_unchanged(before)

    def test_25_bootstrap_network_failure_preserves_native(self):
        before = self.normal()
        self.env['MOCK_CURL_FAIL'] = 'GET_TOOLKIT.sh'
        self.run_scene('prepare', expected=22)
        self.assert_unchanged(before)
        self.assertFalse(self.cache.exists())

    def test_26_archive_network_failure_cleans_partial_state(self):
        before = self.recovery()
        self.env['MOCK_CURL_FAIL'] = 'archive'
        self.run_scene('apply', self.volume, expected=2, contains='HTTPS download failed')
        self.assert_unchanged(before)
        self.assertFalse(self.cache.exists())
        self.assertFalse(self.pointer.exists())

    def test_27_corrupt_cache_rejected_without_redownload(self):
        before = self.recovery()
        self.warm_cache()
        with (self.cache / 'lib.sh').open('a') as f: f.write('\n# deliberate fixture corruption\n')
        self.run_scene('apply', self.volume, expected=2, contains='Checksum differs: lib.sh')
        self.assert_unchanged(before)
        self.assertEqual(self.http_calls(), [])
        self.assertTrue(self.cache.is_dir())

    def test_28_existing_lock_is_not_removed_automatically(self):
        before = self.recovery()
        self.warm_cache()
        lock = self.work / '.operation.lock'
        lock.mkdir()
        work = self.work
        self.work = None  # This rejected invocation did not acquire this lock.
        self.run_scene('apply', self.volume, expected=2, contains='stale lock exists')
        self.work = work
        self.assertTrue(lock.is_dir())
        self.assert_unchanged(before)

    def test_29_concurrent_download_holds_lock_until_completion(self):
        before = self.normal()
        self.env['MOCK_CURL_PAUSE'] = '1.2'
        command = ['/bin/sh', str(self.script), 'prepare']
        p = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.env)
        self.wait_marker(self.root / 'download-ready', p)
        work = self.work
        self.work = None
        self.run_scene('prepare', expected=2, contains='stale lock exists')
        self.work = work
        out, err = p.communicate(timeout=30)
        self.record['commands'].append({'command': command, 'stdin': '', 'stdout': out,
                                       'stderr': err, 'exit_status': p.returncode})
        self.assertEqual(p.returncode, 0, out + err)
        self.assertFalse((self.work / '.operation.lock').exists())
        self.assert_unchanged(before)

    def test_30_term_reaches_core_and_waits_for_verified_rollback(self):
        before = self.recovery()
        self.warm_cache()
        self.env['MOCK_NATIVE_FAULT'] = 'signal_wait'
        command = ['/bin/sh', str(self.script), 'apply', str(self.volume)]
        p = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.env)
        self.wait_marker(self.root / 'native-write-ready', p)
        p.send_signal(signal.SIGTERM)
        time.sleep(0.05)
        p.send_signal(signal.SIGTERM)
        out, err = p.communicate(timeout=30)
        self.record['commands'].append({'command': command, 'stdin': '', 'signal': 'SIGTERM twice after fixture write-ready',
                                       'stdout': out, 'stderr': err, 'exit_status': p.returncode})
        self.assertEqual(p.returncode, 143, out + err)
        self.assertIn('rollback=verified', out + err)
        self.assertEqual(snapshot(self.target), before)
        self.assertFalse(self.pointer.exists())
        self.assertFalse((self.work / '.operation.lock').exists())
        self.assertEqual(len(list((self.work / 'sessions').iterdir())), 1)

    def test_31_untrusted_work_directory_rejected(self):
        before = self.recovery()
        self.warm_cache()
        self.env['MOCK_UNTRUSTED_PATH'] = str(self.work)
        self.env['MOCK_UNTRUSTED_META'] = '502:700'
        self.run_scene('apply', self.volume, expected=2, contains='root-owned')
        self.assert_unchanged(before)

    def test_32_writable_cache_directory_rejected(self):
        before = self.recovery()
        self.warm_cache()
        self.cache.chmod(0o777)
        self.run_scene('apply', self.volume, expected=2, contains='root-owned')
        self.assert_unchanged(before)

    def test_33_symlink_volume_rejected_without_native_write(self):
        before = self.recovery()
        link = self.volumes / 'Linked Data'
        link.symlink_to(self.volume, target_is_directory=True)
        self.run_scene('apply', link, expected=2, contains='Symlink in path')
        self.assert_unchanged(before)

    def test_34_pointer_symlink_and_directory_rejected_before_apply(self):
        before = self.recovery()
        self.warm_cache()
        outside = self.root / 'untouched-pointer-target'
        outside.write_text('untouched\n')
        self.pointer.symlink_to(outside)
        self.run_scene('apply', self.volume, expected=2, contains='Symlink in path')
        self.assertEqual(outside.read_text(), 'untouched\n')
        self.assertFalse((self.work / 'sessions').exists())
        self.pointer.unlink()
        self.pointer.mkdir()
        self.run_scene('apply', self.volume, expected=2, contains='not a regular file')
        self.assert_unchanged(before)
        self.assertFalse((self.work / 'sessions').exists())

    def check_pointer_publication_failure(self, fault):
        before = self.recovery()
        self.warm_cache()
        self.pointer.write_text('previous-success\n')
        self.env['MOCK_POINTER_FAULT'] = fault
        r = self.run_scene('apply', self.volume, expected=4, contains='result=applied_pointer_update_failed')
        self.assertIn('native_applied=yes', r.stderr)
        self.assertTrue(plistlib.loads(self.target.read_bytes())['Disabled'])
        self.assertEqual(self.pointer.read_text(), 'previous-success\n')
        sessions = list((self.work / 'sessions').iterdir())
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sha(sessions[0] / 'ORIGINAL_FILE.plist'), before['sha256'])
        self.assertEqual(len(self.writes()), 1)

    def test_35_pointer_mktemp_failure_is_post_apply_exit4(self):
        self.check_pointer_publication_failure('mktemp')

    def test_36_pointer_rename_failure_is_post_apply_exit4(self):
        self.check_pointer_publication_failure('mv')

    def test_37_untrusted_cache_execution_roots_rejected_before_self_test(self):
        before = self.recovery()
        self.warm_cache()
        for name, metadata in [('SELF_TEST.sh', '502:755'), ('sha256-file', '0:777'),
                               ('SHA256SUMS', '502:644'), ('APPLY_IN_RECOVERY.sh', '0:666')]:
            with self.subTest(name=name):
                self.env['MOCK_UNTRUSTED_PATH'] = str(self.cache / name)
                self.env['MOCK_UNTRUSTED_META'] = metadata
                r = self.run_scene('apply', self.volume, expected=2, contains='root-owned')
                self.assertNotIn('SELF_TEST_OK', r.stdout)
        self.assert_unchanged(before)

    def test_38_wrong_cache_version_rejected_even_with_valid_manifest(self):
        before = self.recovery()
        self.warm_cache()
        (self.cache / 'VERSION').write_text('v0.0.0\n')
        manifest = self.cache / 'SHA256SUMS'
        names = [line.split(None, 1)[1] for line in manifest.read_text().splitlines()]
        manifest.write_text(''.join(sha(self.cache / name) + '  ' + name + '\n' for name in names))
        self.run_scene('apply', self.volume, expected=2, contains='different VERSION')
        self.assert_unchanged(before)

    def test_39_untrusted_pointer_rejected_before_apply(self):
        before = self.recovery()
        self.warm_cache()
        self.pointer.write_text('previous-success\n')
        self.env['MOCK_UNTRUSTED_PATH'] = str(self.pointer)
        self.env['MOCK_UNTRUSTED_META'] = '502:644'
        self.run_scene('apply', self.volume, expected=2, contains='root-owned')
        self.assert_unchanged(before)
        self.assertEqual(self.pointer.read_text(), 'previous-success\n')
        self.assertFalse((self.work / 'sessions').exists())

    def test_40_carriage_return_and_newline_paths_rejected(self):
        before = self.recovery()
        for suffix in ['\r', '\n']:
            with self.subTest(suffix=suffix):
                self.run_scene('apply', str(self.volume) + suffix, expected=2, contains='carriage-return paths')
        self.assert_unchanged(before)
        self.assertEqual(self.http_calls(), [])

    def test_41_core_rollback_failure_status3_wins_over_wrapper_term(self):
        before = self.recovery()
        self.warm_cache()
        self.env['MOCK_NATIVE_FAULT'] = 'signal_wait_rollback_failure'
        command = ['/bin/sh', str(self.script), 'apply', str(self.volume)]
        p = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.env)
        self.wait_marker(self.root / 'native-write-ready', p)
        p.send_signal(signal.SIGTERM)
        out, err = p.communicate(timeout=30)
        self.record['commands'].append({'command': command, 'stdin': '', 'signal': 'SIGTERM with injected rollback failure',
                                       'stdout': out, 'stderr': err, 'exit_status': p.returncode})
        self.assertEqual(p.returncode, 3, out + err)
        self.assertIn('rollback=failed', err)
        self.assertFalse(self.pointer.exists())
        self.assertFalse((self.work / '.operation.lock').exists())
        session = next((self.work / 'sessions').iterdir())
        self.assertEqual(sha(session / 'ORIGINAL_FILE.plist'), before['sha256'])
        self.assertNotEqual(sha(self.target), before['sha256'])

    def test_42_signal_between_fork_and_pid_assignment_keeps_lock_until_rollback(self):
        before = self.recovery()
        self.warm_cache()
        self.env['MOCK_NATIVE_FAULT'] = 'signal_wait'
        self.env['MOCK_NATIVE_HOLD'] = '1'
        pause = self.bin / 'pid-window-pause'
        executable(pause, '''#!/usr/bin/python3
import os,pathlib,time
r=pathlib.Path(os.environ['MOCK_ROOT']);end=time.monotonic()+15
while not (r/'native-write-ready').exists():
 if time.monotonic()>end:raise SystemExit(1)
 time.sleep(0.01)
(r/'pid-window-ready').write_text('1');time.sleep(0.4)
''')
        text = self.script.read_bytes().decode('utf-8')
        needle = ' "$@" &\n CHILD_PID=$!\n'
        self.assertEqual(text.count(needle), 1)
        replacement = (' "$@" &\n'
                       ' case "${2:-}" in */APPLY_IN_RECOVERY.sh) "' + str(pause) + '" ;; esac\n'
                       ' CHILD_PID=$!\n')
        self.script.write_bytes(text.replace(needle, replacement).encode('utf-8'))
        self.record['timing_instrumentation'] = {
            'scope': 'isolated external wrapper only; no core changes',
            'original': needle, 'replacement': replacement,
            'purpose': 'deterministic signal delivery after fork but before CHILD_PID assignment',
        }
        command = ['/bin/sh', str(self.script), 'apply', str(self.volume)]
        p = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.env)
        self.wait_marker(self.root / 'pid-window-ready', p)
        self.assertTrue((self.work / '.operation.lock').exists())
        p.send_signal(signal.SIGTERM)
        # The native writer stays held until this test explicitly releases it.
        # Check the wrapper/lock while the core is still incomplete, rather
        # than letting inherited stdout pipes mask an early wrapper exit.
        time.sleep(0.65)
        wrapper_waited = p.poll() is None
        lock_held = (self.work / '.operation.lock').exists()
        (self.root / 'release-native-write').write_text('1')
        out, err = p.communicate(timeout=30)
        self.record['commands'].append({'command': command, 'stdin': '', 'signal': 'SIGTERM in fork/PID assignment window',
                                       'stdout': out, 'stderr': err, 'exit_status': p.returncode})
        self.assertEqual(p.returncode, 143, out + err)
        self.assertTrue(wrapper_waited, 'Wrapper exited while its core child was incomplete.')
        self.assertTrue(lock_held, 'Operation lock was released before core rollback completed.')
        self.assertIn('rollback=verified', out + err)
        self.assertEqual(snapshot(self.target), before)
        self.assertFalse(self.pointer.exists())
        self.assertFalse((self.work / '.operation.lock').exists())

    def test_43_launch_window_signal_guards_return_success_under_set_e(self):
        text = self.script.read_bytes().decode('utf-8')
        marker = 'if [ "$#" -eq 0 ]; then help; exit 0; fi'
        self.assertEqual(text.count(marker), 1)
        probe = self.root / 'launch-guard-functions-only.sh'
        prefix = text.split(marker, 1)[0]
        body = '''
LAUNCHING_CHILD=1
CHILD_PID=''
on_signal 143
on_signal 129
signal_child_once
printf 'signal_rc=%s\\nlaunching_child=%s\\nguards_survived_set_e=yes\\n' "$SIGNAL_RC" "$LAUNCHING_CHILD"
'''
        probe.write_bytes((prefix + body).encode('utf-8'))
        command = ['/bin/sh', str(probe)]
        r = subprocess.run(command, capture_output=True, text=True, env=self.env, timeout=10)
        self.record['commands'].append({'command': command, 'stdin': '', 'stdout': r.stdout,
                                       'stderr': r.stderr, 'exit_status': r.returncode})
        self.record['function_probe'] = 'Real function definitions only; launch window/duplicate signal/empty-child guards, no production main.'
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout, 'signal_rc=143\nlaunching_child=1\nguards_survived_set_e=yes\n')
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.http_calls(), [])

    def wait_marker(self, path, process):
        until = time.monotonic() + 20
        while time.monotonic() < until:
            if path.exists(): return
            if process.poll() is not None:
                out, err = process.communicate()
                self.fail('Fixture exited before readiness marker: ' + out + err)
            time.sleep(0.02)
        process.kill()
        process.communicate()
        self.fail('Fixture readiness timeout')


if __name__ == '__main__':
    program = unittest.main(exit=False, verbosity=2)
    result = program.result
    failed_names = {test.id() for test, _ in result.failures + result.errors}
    for record in RESULTS: record['passed'] = record['test'] not in failed_names
    output = {'fixture_only': True, 'native_host_writes_attempted': False,
              'command': ['/usr/bin/python3', str(Path(__file__).resolve()), *sys.argv[1:]],
              'tests_run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
              'passed': result.wasSuccessful(), 'cases': RESULTS, 'source_sha256': SOURCE_HASHES}
    (REPO / 'tests' / 'ONE_CLICK_RESULTS.json').write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
    summary = ['ONE_CLICK ISOLATED WRAPPER TESTS', 'command=' + json.dumps(output['command']),
               'stdin=(empty)', 'native_host_writes_attempted=no',
               f'tests_run={result.testsRun}', f'failures={len(result.failures)}', f'errors={len(result.errors)}',
               'result=' + ('PASS' if result.wasSuccessful() else 'FAIL'),
               'Scope: real core/helper/GET/archive/SELF_TEST with fixture-only paths, identity, ownership, volumes, HTTPS and observation tools.',
               'Not an actual Recovery reboot test; not proof of future system behavior.',
               'Exact command/stdin/stdout/stderr/exit and binding substitutions: ONE_CLICK_RESULTS.json', '']
    summary += [r['test'] + ': ' + ('PASS' if r['passed'] else 'FAIL') for r in RESULTS]
    (REPO / 'tests' / 'ONE_CLICK_VERIFICATION.txt').write_text('\n'.join(summary) + '\n')
    sys.exit(0 if result.wasSuccessful() else 1)
