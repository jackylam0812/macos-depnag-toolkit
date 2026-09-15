#!/usr/bin/env python3
"""Isolated fault-injection tests. Never invoke a script against the live native file.

Only a copied script bundle is instrumented: identity, diskutil, /Volumes and the
live mount binding, plus exact tool paths for failure injection. Transaction and
validation logic is not rewritten. All fixture payloads are synthetic.
"""
from pathlib import Path
import copy, datetime, hashlib, json, os, plistlib, re, shutil, subprocess, sys

ROOT = Path(__file__).resolve().parent
BUNDLE = ROOT.parent
FIXTURES = ROOT / 'fixtures'
UUID = '11111111-2222-4333-8444-555555555555'
UUID2 = 'AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE'
RELATIVE_TARGET = 'private/var/db/ConfigurationProfiles/Settings/com.apple.mdm.depnag.plist'
SYNTHETIC = {
    'History': ['synthetic initial event', 'Unicode preservation 测试'],
    'NagUI_502': {'DateCompleted': datetime.datetime(2026, 9, 15, 5, 54, 24),
                  'DateFirstShown': datetime.datetime(2026, 9, 15, 5, 54, 17), 'Result': 0},
    'UnrelatedState': {'nested': [True, False, 27, 3.25, b'\x00\xffabc'], 'Empty': ''},
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    Path(path).write_bytes(plistlib.dumps(value, fmt=plistlib.FMT_BINARY, sort_keys=False))


def executable(path, body):
    Path(path).write_text(body)
    Path(path).chmod(0o755)


def metadata(path):
    if not Path(path).exists():
        return None
    s = Path(path).stat()
    return [s.st_uid, s.st_gid, s.st_mode & 0o7777, s.st_flags]


def snapshot(path):
    return {'sha256': sha(path) if Path(path).is_file() else None, 'metadata': metadata(path)}


class Fixture:
    def __init__(self, name, value=None, source_format='binary'):
        self.name = name
        self.root = FIXTURES / name
        if self.root.exists():
            shutil.rmtree(self.root)
        self.bundle = self.root / 'bundle with spaces'
        self.bin = self.root / 'bin'
        self.volume = self.root / 'Volumes' / 'Restored Data'
        self.live = self.root / 'live' / 'Data'
        self.target = self.volume / RELATIVE_TARGET
        self.session = self.root / 'session with spaces'
        for p in [self.bundle, self.bin, self.volume, self.live, self.target.parent]:
            p.mkdir(parents=True, exist_ok=True)
        for p in BUNDLE.iterdir():
            if p.is_file() and (p.suffix in ['.sh', '.plist'] or p.name == 'sha256-file'):
                shutil.copy2(p, self.bundle / p.name)
        if (BUNDLE / 'bin').exists():
            shutil.copytree(BUNDLE / 'bin', self.bundle / 'bin', dirs_exist_ok=True)
        initial = copy.deepcopy(SYNTHETIC if value is None else value)
        self.target.write_bytes(plistlib.dumps(initial, fmt=plistlib.FMT_BINARY if source_format == 'binary' else plistlib.FMT_XML, sort_keys=False))
        self.target.chmod(0o640)
        self.initial = snapshot(self.target)
        self.env = dict(os.environ, PATH=str(self.bin) + ':/usr/bin:/bin:/usr/sbin:/sbin',
                        FIXTURE_ROOT=str(self.root), FIXTURE_TARGET=str(self.target),
                        FIXTURE_CASE=name, FIXTURE_PHASE='prepare', FIXTURE_FAULT='',
                        FIXTURE_UUID=UUID, FIXTURE_MOUNT=str(self.volume))
        self.commands = []
        self.source_hashes = {p.name:sha(p) for p in BUNDLE.glob('*.sh')}
        self.transforms = {}
        self.make_tools()
        self.instrument()

    def make_tools(self):
        executable(self.bin / 'id', '#!/bin/sh\nprintf "0\\n"\n')
        executable(self.bin / 'diskutil', '''#!/bin/sh
printf '<?xml version="1.0"?><plist version="1.0"><dict><key>VolumeUUID</key><string>%s</string><key>MountPoint</key><string>%s</string><key>VolumeName</key><string>Restored Data</string><key>FilesystemType</key><string>apfs</string><key>APFSVolumeRole</key><array><string>Data</string></array><key>APFSVolumeRoles</key><array><string>Data</string></array></dict></plist>\n' "$FIXTURE_UUID" "$FIXTURE_MOUNT"
''')
        executable(self.bin / 'log', '''#!/bin/sh
case "$FIXTURE_CASE" in
 verify_log_failure) echo 'Injected log access failure' >&2; exit 1 ;;
 verify_no_logs) printf 'Timestamp Process Message\\n' ;;
 verify_schedule_present) printf '2026-09-15 17:41:00 mdmclient DEPNag: Removing CoreFollowUp\\n2026-09-15 17:42:00 mdmclient DEPNag: Scheduling timer\\n' ;;
 *) printf '2026-09-15 17:41:00 mdmclient DEPNag: Removing CoreFollowUp\\n' ;;
esac
''')
        executable(self.bin / 'ps', '''#!/bin/sh
case "$FIXTURE_CASE" in
 verify_minibuddy_present) printf '/System/Library/CoreServices/MiniBuddy\\n' ;;
 verify_setup_assistant_present) printf '/System/Library/CoreServices/Setup Assistant.app/Contents/MacOS/Setup Assistant\\n' ;;
 *) printf '/sbin/launchd\\n' ;;
esac
''')
        for tool in ['shasum', 'openssl']:
            executable(self.bin / tool, '#!/bin/sh\necho "$0" >> "$FIXTURE_ROOT/external-sha.log"\nexit 127\n')
        executable(self.bin / 'cat', '''#!/bin/sh
if [ "$FIXTURE_PHASE" = prepare ] && [ "${1:-}" = "$FIXTURE_TARGET" ]; then
 case "$FIXTURE_CASE" in
  prepare_snapshot_race) /usr/bin/plutil -insert RaceChanged -bool true "$FIXTURE_TARGET" ;;
  prepare_metadata_race) /bin/chmod 777 "$FIXTURE_TARGET" ;;
 esac
fi
if [ "$FIXTURE_PHASE" != prepare ]; then
 case "${1:-}" in
  */MODIFIED_FILE.plist|*/ORIGINAL_FILE.plist)
   echo "cat:$1" >> "$FIXTURE_ROOT/writes.log"
   if [ ! -e "$FIXTURE_ROOT/first-write" ]; then
    : > "$FIXTURE_ROOT/first-write"
    case "$FIXTURE_FAULT" in
     partial_write|rollback_failure) printf 'PARTIAL-WRITE'; exit 74 ;;
     postverify) printf 'INVALID-POST-WRITE'; exit 0 ;;
     signal_term) printf 'PARTIAL-WRITE'; kill -TERM "$PPID"; exit 143 ;;
     repeated_term) printf 'PARTIAL-WRITE'; PARENT=$PPID; (sleep 0.1; kill -TERM "$PARENT" 2>/dev/null || :) & kill -TERM "$PARENT"; exit 143 ;;
    esac
   elif [ "$FIXTURE_FAULT" = rollback_failure ]; then
    exit 75
   elif [ "$FIXTURE_FAULT" = repeated_term ]; then
    sleep 0.3
   fi
   ;;
 esac
fi
/bin/cat "$@"
RC=$?
if [ "$FIXTURE_PHASE" != prepare ] && [ "$FIXTURE_FAULT" = actual_metadata_drift ] && [ -e "$FIXTURE_ROOT/first-write" ]; then
 /bin/chmod 777 "$FIXTURE_TARGET"
fi
exit "$RC"
''')
        executable(self.bin / 'plutil', '''#!/bin/sh
if [ "$FIXTURE_PHASE" != prepare ] && [ "$FIXTURE_FAULT" = lint_failure ] && [ "${1:-}" = -lint ] && [ -e "$FIXTURE_ROOT/first-write" ] && [ ! -e "$FIXTURE_ROOT/lint-failed" ]; then
 : > "$FIXTURE_ROOT/lint-failed"
 echo 'Injected post-write lint failure' >&2
 exit 1
fi
exec /usr/bin/plutil "$@"
''')
        executable(self.bin / 'stat', '''#!/bin/sh
if [ "$FIXTURE_PHASE" != prepare ] && [ "$FIXTURE_FAULT" = metadata_drift ] && [ -e "$FIXTURE_ROOT/first-write" ] && [ ! -e "$FIXTURE_ROOT/stat-failed" ]; then
 : > "$FIXTURE_ROOT/stat-failed"
 echo 'INJECTED-METADATA-DRIFT'
 exit 0
fi
exec /usr/bin/stat "$@"
''')
        helper = self.bundle / 'sha256-file'
        if helper.exists():
            helper.rename(self.bundle / 'sha256-file.real')
            executable(helper, '''#!/bin/sh
BASE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$FIXTURE_CASE" = status_read_race ] && [ ! -e "$FIXTURE_ROOT/read-race" ]; then
 : > "$FIXTURE_ROOT/read-race"
 "$BASE/sha256-file.real" "$@" || exit 1
 /usr/bin/plutil -insert ReadRaceChange -bool true "$FIXTURE_TARGET"
 exit $?
fi
if [ "$FIXTURE_PHASE" != prepare ] && [ "$FIXTURE_FAULT" = digest_failure ] && [ "${1:-}" = "$FIXTURE_TARGET" ] && [ -e "$FIXTURE_ROOT/first-write" ] && [ ! -e "$FIXTURE_ROOT/digest-failed" ]; then
 : > "$FIXTURE_ROOT/digest-failed"
 echo 'Injected post-write digest failure' >&2
 exit 1
fi
exec "$BASE/sha256-file.real" "$@"
''')

    def instrument(self):
        mappings = {
            '/usr/bin/shasum': str(self.bin / 'shasum'),
            '/usr/bin/openssl': str(self.bin / 'openssl'),
            '/usr/sbin/diskutil': str(self.bin / 'diskutil'),
            '/usr/bin/plutil': str(self.bin / 'plutil'),
            '/usr/bin/stat': str(self.bin / 'stat'),
            '/usr/bin/id': str(self.bin / 'id'),
            '/usr/bin/log': str(self.bin / 'log'),
            '/bin/ps': str(self.bin / 'ps'),
            '/bin/cat': str(self.bin / 'cat'),
            '/System/Volumes/Data': str(self.live),
            '/Volumes/': str(self.root / 'Volumes') + '/',
        }
        for script in list(self.bundle.glob('*.sh')) + list((self.bundle / 'bin').glob('*.sh')):
            text = script.read_bytes().decode("utf-8")
            changes = {}
            # Only exact quoted absolute live-target literals, not $DATA/private/... concatenation.
            live_target = '/private/var/db/ConfigurationProfiles/Settings/com.apple.mdm.depnag.plist'
            for quote in [chr(39), chr(34)]:
                old = quote + live_target + quote
                changes['live target ' + quote] = text.count(old)
                text = text.replace(old, quote + str(self.live / RELATIVE_TARGET) + quote)
            # Quotes prevent a fixture path containing spaces from altering shell parsing.
            # Fixture bin paths contain no spaces; user-facing bundle/session/volume do.
            for old, new in mappings.items():
                changes[old] = text.count(old)
                text = text.replace(old, new)
            text, changes['bare id -u'] = re.subn(r'(?<![\w/])id -u', str(self.bin / 'id') + ' -u', text)
            executable(script, text)
            self.transforms[str(script.relative_to(self.bundle))] = changes
        (self.root / 'binding_transformations.json').write_text(json.dumps(self.transforms, indent=2) + '\n')

    def run(self, script, args, phase='apply', fault='', extra_env=None):
        env = dict(self.env, FIXTURE_PHASE=phase, FIXTURE_FAULT=fault)
        if extra_env:
            env.update(extra_env)
        if fault:
            for marker in ['first-write','lint-failed','stat-failed','digest-failed']:
                (self.root / marker).unlink(missing_ok=True)
        cmd = ['/bin/sh', str(self.bundle / script), *map(str, args)]
        result = subprocess.run(cmd, input='', text=True, capture_output=True, env=env, timeout=40)
        entry = dict(command=cmd, stdin='', stdout=result.stdout, stderr=result.stderr,
                     exit_status=result.returncode,
                     environment={k:env[k] for k in sorted(env) if k.startswith('FIXTURE_') or k == 'PATH'})
        self.commands.append(entry)
        return result

    def prepare(self):
        return self.run('PREPARE.sh', ['--volume', self.volume, self.session], phase='prepare')

    def apply(self, fault=''):
        return self.run('APPLY_IN_RECOVERY.sh', [self.volume, self.session], fault=fault)

    def rollback(self, fault=''):
        return self.run('ROLLBACK.sh', [self.volume, self.session], fault=fault)

    def writes(self):
        log = self.root / 'writes.log'
        return log.read_text().splitlines() if log.exists() else []


def record(f, checks):
    report = dict(name=f.name, passed=all(checks.values()), checks=checks,
                  source_hashes=f.source_hashes,
                  initial=f.initial, final=snapshot(f.target), writes=f.writes(),
                  commands=f.commands, fixture_root=str(f.root), binding_transformations=f.transforms,
                  native_target_used=False)
    (f.root / 'result.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    return report


def run_case(name):
    value = copy.deepcopy(SYNTHETIC)
    if name in ['disabled_true', 'disabled_false', 'disabled_string', 'disabled_integer']:
        value['Disabled'] = {'disabled_true':True, 'disabled_false':False,
                             'disabled_string':'true', 'disabled_integer':1}[name]
    if name == 'root_array':
        value = ['not-a-dictionary']
    elif name == 'history_wrong_type':
        value['History'] = 'not an array'
    f = Fixture(name, value=value, source_format='xml' if name == 'xml_source' else 'binary')
    checks = {}
    if name == 'missing_native':
        f.target.unlink()
    elif name == 'symlink_native':
        real = f.target.with_name('real.plist'); f.target.rename(real); f.target.symlink_to(real)
    elif name == 'ancestor_symlink_native':
        real = f.target.parent.with_name('real-settings')
        f.target.parent.rename(real)
        f.target.parent.symlink_to(real,target_is_directory=True)
    elif name == 'ancestor_symlink_session':
        real = f.root/'real-session-parent'; real.mkdir()
        link = f.root/'linked-session-parent'; link.symlink_to(real,target_is_directory=True)
        f.session=link/'new-session'
    elif name in ['prepare_snapshot_race','prepare_metadata_race']:
        pass
    elif name == 'prepare_live':
        live_target = f.live / RELATIVE_TARGET
        live_target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(f.target,live_target)
        f.env['FIXTURE_MOUNT'] = str(f.live)
    elif name == 'wrong_mountpoint':
        f.env['FIXTURE_MOUNT'] = str(f.volume.parent)
    elif name == 'new_uuid_new_content':
        f.env['FIXTURE_UUID'] = UUID2
        value['History'].append('new restoration instance')
        dump(f.target, value); f.initial = snapshot(f.target)
    prep = f.run('PREPARE.sh',['--live',f.session],phase='prepare') if name == 'prepare_live' else f.prepare()
    if name in ['prepare_snapshot_race','prepare_metadata_race']:
        checks.update(prepare_race_rejected=prep.returncode != 0,
                      incomplete_session_diagnosed='incomplete_session=' in prep.stderr,
                      no_manifest=(not (f.session/'MANIFEST.plist').exists()),
                      zero_script_native_writes=not f.writes())
        return record(f,checks)
    invalid_prepare = name in ['missing_native', 'symlink_native', 'disabled_string', 'disabled_integer', 'root_array','wrong_mountpoint','history_wrong_type','ancestor_symlink_native','ancestor_symlink_session']
    if invalid_prepare:
        checks.update(prepare_rejected=prep.returncode != 0, zero_native_writes=not f.writes(),
                      native_unchanged=snapshot(f.target) == (dict(sha256=None, metadata=None) if name == 'missing_native' else f.initial))
        return record(f, checks)
    checks['prepare_ok'] = prep.returncode == 0
    if prep.returncode:
        return record(f, checks)
    for artifact in ['ORIGINAL_FILE.plist','MODIFIED_FILE.plist','DIFF_FILE.diff','MANIFEST.plist','VERIFICATION.txt']:
        checks['artifact_' + artifact] = (f.session / artifact).is_file()
    original = f.session / 'ORIGINAL_FILE.plist'
    modified = f.session / 'MODIFIED_FILE.plist'
    original_hash, modified_hash = sha(original), sha(modified)
    checks['backup_byte_exact'] = original_hash == f.initial['sha256']
    before_value = plistlib.loads(original.read_bytes()); after_value = plistlib.loads(modified.read_bytes())
    expected = copy.deepcopy(before_value); expected['Disabled'] = True
    checks['only_disabled_changed'] = after_value == expected and type(after_value['Disabled']) is bool
    checks['prepare_no_native_write'] = snapshot(f.target) == f.initial and not f.writes()
    if name == 'prepare_live':
        checks.update(live_source_unchanged=snapshot(f.live / RELATIVE_TARGET) == f.initial,
                      manifest_live=plistlib.loads((f.session/'MANIFEST.plist').read_bytes())['PreparedFrom'] == 'live')
        return record(f,checks)
    if name == 'prepare_repeated_dir':
        again = f.prepare()
        checks.update(existing_session_rejected=again.returncode != 0,
                      existing_backup_intact=sha(original) == original_hash,
                      native_unchanged=snapshot(f.target) == f.initial)
        return record(f, checks)
    if name == 'wrong_uuid':
        f.env['FIXTURE_UUID'] = UUID2
    elif name == 'changed_native':
        changed = copy.deepcopy(value); changed['History'].append('changed after prepare'); dump(f.target, changed)
    elif name == 'corrupt_payload':
        modified.write_bytes(b'CORRUPTED-PAYLOAD')
    elif name == 'corrupt_backup':
        original.write_bytes(b'CORRUPTED-BACKUP')
    elif name == 'missing_helper':
        (f.bundle / 'sha256-file').unlink()
    elif name == 'symlink_session':
        session_real = f.session.with_name('session-real'); f.session.rename(session_real); f.session.symlink_to(session_real, target_is_directory=True)
    elif name == 'live_volume':
        f.live.rmdir(); f.live.symlink_to(f.volume, target_is_directory=True)
    elif name == 'changed_target_mode':
        f.target.chmod(0o600)
    elif name in ['manifest_type','manifest_target','manifest_disabled','payload_extra_rehashed','payload_false_rehashed']:
        manifest_path=f.session/'MANIFEST.plist'
        manifest=plistlib.loads(manifest_path.read_bytes())
        if name == 'manifest_type': manifest['SchemaVersion']='2'
        elif name == 'manifest_target': manifest['TargetRelativePath']='private/var/db/other.plist'
        elif name == 'manifest_disabled': manifest['OriginalDisabled']='true'
        else:
            changed_payload=plistlib.loads(modified.read_bytes())
            if name == 'payload_extra_rehashed': changed_payload['HiddenExtraChange']=True
            else: changed_payload['Disabled']=False
            dump(modified,changed_payload)
            manifest['ModifiedSHA256']=sha(modified)
        dump(manifest_path,manifest)
    before_apply = snapshot(f.target)
    rejection = name in ['wrong_uuid','changed_native','corrupt_payload','corrupt_backup','missing_helper','symlink_session','live_volume','changed_target_mode','manifest_type','manifest_target','manifest_disabled','payload_extra_rehashed','payload_false_rehashed']
    fault_map = dict(partial_write='partial_write', postverify='postverify', postwrite_digest='digest_failure',
                     postwrite_lint='lint_failure', metadata_drift='metadata_drift',
                     actual_metadata_drift='actual_metadata_drift', repeated_term='repeated_term',
                     signal_term='signal_term', rollback_failure='rollback_failure')
    result = f.apply(fault=fault_map.get(name,''))
    if rejection:
        checks.update(apply_rejected=result.returncode != 0, native_unchanged=snapshot(f.target) == before_apply,
                      zero_native_writes=not f.writes())
    elif name in fault_map:
        checks['failure_reported'] = result.returncode != 0
        if name in ['actual_metadata_drift','rollback_failure']:
            checks['rollback_failure_not_hidden'] = result.returncode == 3 and 'rollback=failed' in result.stderr
            if name == 'actual_metadata_drift':
                checks['bytes_restored'] = sha(f.target) == original_hash
                checks['metadata_drift_preserved_as_failure'] = metadata(f.target) != before_apply['metadata']
        else:
            checks['rollback_verified'] = 'rollback=verified' in result.stderr
            checks['native_restored_exactly'] = snapshot(f.target) == before_apply
            if name in ['signal_term','repeated_term']:
                checks['signal_status'] = result.returncode == 143
    else:
        checks.update(apply_ok=result.returncode == 0, applied_byte_exact=sha(f.target) == modified_hash,
                      metadata_preserved=metadata(f.target) == before_apply['metadata'])
        if name in ['disabled_true','apply_idempotent']:
            writes = list(f.writes()); repeat = f.apply()
            checks.update(reapply_ok=repeat.returncode == 0, reapply_zero_write=f.writes() == writes,
                          reapply_hash_preserved=sha(f.target) == modified_hash)
        if name in ['apply_rollback','rollback_partial_write','rollback_idempotent']:
            before_rollback = snapshot(f.target)
            rollback = f.rollback(fault='partial_write' if name == 'rollback_partial_write' else '')
            if name == 'rollback_partial_write':
                checks.update(rollback_write_failure_reported=rollback.returncode != 0,
                              rollback_failure_restored_modified=snapshot(f.target) == before_rollback,
                              rollback_failure_verified='rollback=verified' in rollback.stderr)
            else:
                checks.update(rollback_ok=rollback.returncode == 0,
                              rollback_byte_exact=sha(f.target) == original_hash,
                              rollback_metadata_exact=metadata(f.target) == f.initial['metadata'])
                if name == 'rollback_idempotent':
                    writes = list(f.writes()); repeated = f.rollback()
                    checks.update(repeated_rollback_ok=repeated.returncode == 0,
                                  repeated_rollback_zero_write=f.writes() == writes)
    return record(f, checks)


def run_readonly_case(name):
    value = copy.deepcopy(SYNTHETIC)
    if name not in ['status_absent', 'status_missing']:
        value['Disabled'] = {'status_false': False, 'status_string': 'true', 'status_integer': 1,
                             'verify_false':False, 'verify_bad_state':'true'}.get(name, True)
    if name == 'status_root_array': value = ['wrong root']
    f = Fixture(name, value=value)
    if name == 'status_missing':
        f.target.unlink()
    elif name == 'status_invalid_plist':
        f.target.write_bytes(b'INVALID PLIST')
    elif name == 'status_missing_helper':
        (f.bundle / 'sha256-file').unlink()
    before = snapshot(f.target)
    if name.startswith('status_'):
        result = f.run('STATUS.sh', ['--volume',f.volume], phase='readonly')
        expected_rc = {'status_absent':1, 'status_false':1, 'status_true':0}.get(name,2)
        expected_result = {'status_absent':'result=not_disabled', 'status_false':'result=not_disabled',
                           'status_true':'result=disabled', 'status_string':'result=Disabled_has_non_boolean_type',
                           'status_integer':'result=Disabled_has_non_boolean_type',
                           'status_missing':'result=state_file_missing_or_symlink',
                           'status_invalid_plist':'result=invalid_plist',
                           'status_missing_helper':'result=sha256_helper_missing_or_not_executable',
                           'status_root_array':'result=invalid_plist_root','status_read_race':'result=state_changed_during_read'}[name]
        checks = dict(expected_exit=result.returncode == expected_rc,
                      expected_result=expected_result in result.stdout + result.stderr,
                      readonly_marker='native_write_attempted=no' in result.stdout,
                      native_unchanged=snapshot(f.target) == before if name != 'status_read_race' else sha(f.target) != before['sha256'],
                      zero_script_writes=not f.writes())
    else:
        live_target = f.live / RELATIVE_TARGET
        live_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f.target, live_target)
        live_before = snapshot(live_target)
        result = f.run('VERIFY_AFTER_BOOT.sh',['--since','2026-09-15 17:40:00'], phase='readonly')
        expected_rc = {'verify_disabled_matches':0, 'verify_no_logs':0,
                       'verify_schedule_present':1, 'verify_minibuddy_present':1, 'verify_false':1,
                       'verify_log_failure':2, 'verify_bad_state':2,'verify_setup_assistant_present':1}[name]
        expected_result = {'verify_disabled_matches':'result=disabled_with_matching_boot_log',
                           'verify_no_logs':'result=disabled_no_matching_boot_log_available',
                           'verify_schedule_present':'result=review_required',
                           'verify_minibuddy_present':'result=review_required','verify_false':'result=review_required',
                           'verify_log_failure':'result=log_query_incomplete',
                           'verify_bad_state':'result=state_read_incomplete','verify_setup_assistant_present':'result=review_required'}[name]
        checks = dict(expected_exit=result.returncode == expected_rc,
                      expected_result=expected_result in result.stdout + result.stderr,
                      readonly_marker='native_write_attempted=no' in result.stdout,
                      live_fixture_unchanged=snapshot(live_target) == live_before, zero_writes=not f.writes())
    return record(f,checks)


CASES = ['prepare_apply','new_uuid_new_content','xml_source','disabled_false','disabled_true',
         'apply_idempotent','apply_rollback','rollback_idempotent','prepare_repeated_dir',
         'missing_native','symlink_native','disabled_string','disabled_integer','root_array',
         'wrong_uuid','changed_native','corrupt_payload','corrupt_backup','missing_helper',
         'symlink_session','live_volume','changed_target_mode',
         'partial_write','rollback_partial_write','postverify','postwrite_digest','postwrite_lint',
         'metadata_drift','actual_metadata_drift','signal_term','repeated_term','rollback_failure']

READONLY_CASES = ['status_absent','status_false','status_true','status_string','status_integer','status_missing',
                  'status_invalid_plist','status_missing_helper','status_root_array','status_read_race','verify_setup_assistant_present','verify_disabled_matches','verify_no_logs',
                  'verify_schedule_present','verify_minibuddy_present','verify_log_failure','verify_false','verify_bad_state']
CASES += ['prepare_live','wrong_mountpoint','prepare_snapshot_race','prepare_metadata_race',
          'manifest_type','manifest_target','manifest_disabled','payload_extra_rehashed','payload_false_rehashed',
          'history_wrong_type','ancestor_symlink_native','ancestor_symlink_session']
CASES += READONLY_CASES


def main():
    cases = sys.argv[1:] or CASES
    unknown = set(cases) - set(CASES)
    if unknown:
        raise SystemExit('Unknown cases: ' + ', '.join(sorted(unknown)))
    required = ['STATUS.sh','VERIFY_AFTER_BOOT.sh','sha256-file'] if all(c in READONLY_CASES for c in cases) else ['PREPARE.sh','APPLY_IN_RECOVERY.sh','ROLLBACK.sh','lib.sh','sha256-file']
    missing = [p for p in required if not (BUNDLE / p).is_file()]
    if missing:
        raise SystemExit('Core bundle is not complete yet: ' + ', '.join(missing))
    reports = []
    for name in cases:
        try:
            report = run_readonly_case(name) if name in READONLY_CASES else run_case(name)
        except Exception as ex:
            report = {'name':name,'passed':False,'harness_error':repr(ex)}
        reports.append(report)
        print(json.dumps({'name':name,'passed':report['passed'],
                          'failed_checks':[k for k,v in report.get('checks',{}).items() if not v],
                          **({'harness_error':report['harness_error']} if 'harness_error' in report else {})}, ensure_ascii=False), flush=True)
    output = dict(harness=str(Path(__file__).resolve()), bundle=str(BUNDLE),
                  fixture_only=True, native_writes_attempted=False,
                  passed=sum(r['passed'] for r in reports), total=len(reports), cases=reports)
    (ROOT / 'TEST_RESULTS.json').write_text(json.dumps(output, indent=2, ensure_ascii=False) + '\n')
    verification = ['ISOLATED TOOLKIT TESTS',
                    'command=' + ' '.join(['/usr/bin/python3',str(Path(__file__).resolve()), *sys.argv[1:]]),
                    'stdin=(empty)', f'result={output["passed"]}/{output["total"]}',
                    'native_writes_attempted=no',
                    'Scope: synthetic plist, fixture volume UUID, root identity/tool bindings rewritten only in isolated copies.',
                    'These tests are not actual Recovery execution or proof of future macOS behavior.',
                    'Detailed literal command/stdin/stdout/stderr/exit for every case: TEST_RESULTS.json', '']
    for r in reports:
        verification.append(f'{r["name"]}: {"PASS" if r["passed"] else "FAIL"}')
        for c in r.get('commands',[]):
            verification.extend(['command=' + json.dumps(c['command'], ensure_ascii=False), 'stdin=' + repr(c['stdin']),
                                 'stdout=' + repr(c['stdout']), 'stderr=' + repr(c['stderr']), 'exit=' + str(c['exit_status'])])
    (ROOT / 'VERIFICATION.txt').write_text('\n'.join(verification) + '\n')
    print(f'RESULT: {output["passed"]}/{output["total"]} passed; native_writes_attempted=no')
    return 0 if output['passed'] == output['total'] else 1


if __name__ == '__main__':
    sys.exit(main())
