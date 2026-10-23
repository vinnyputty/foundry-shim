import json
import os
from pathlib import Path
import pty
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.home = self.root / 'home'
        self.home.mkdir()
        (self.home / '.ssh').mkdir()
        (self.home / '.ssh/id_ed25519.pub').write_text('public-fixture')
        self.dest = self.home / 'repositories/foundry'
        self.env = dict(os.environ, HOME=str(self.home), PATH=str(self.bin),
                        FIXTURE=str(self.root), DESTINATION=str(self.dest),
                        TMPDIR=str(self.root), SCENARIO='', OS='Darwin', ARCH='arm64')
        dispatcher = self.root / 'dispatcher'
        dispatcher.write_text((REPO / 'tests/fake_tool.py').read_text().replace('PYTHON', os.sys.executable, 1))
        dispatcher.chmod(0o755)
        for name in ('id', 'uname', 'brew', 'git', 'gh', 'ssh', 'sudo', 'curl',
                     'wget', 'provision-brew', 'mv', 'ssh-keygen'):
            (self.bin / name).symlink_to(dispatcher)
        for name in ('mktemp', 'rm', 'mkdir', 'dirname'):
            executable = subprocess.check_output(['/bin/sh', '-c', 'command -v ' + name], text=True).strip()
            (self.bin / name).symlink_to(executable)
        self.script = self.root / 'install.sh'
        source = (REPO / 'install.sh').read_text()
        for prefix in ('/opt/homebrew', '/usr/local', '/home/linuxbrew/.linuxbrew'):
            source = source.replace(prefix + '/bin/brew', str(self.root / 'prefix/bin/brew'))
        source = source.replace('/etc/os-release', str(self.root / 'os-release'))
        self.script.write_text(source)
        (self.root / 'os-release').write_text('ID=ubuntu\n')

    def run_installer(self, scenario='', args=(), tty=True, command=None, answers='\n'):
        self.env['SCENARIO'] = scenario
        master, slave = pty.openpty()
        try:
            if tty:
                os.write(master, answers.encode())
            return subprocess.run(command or ['/bin/bash', str(self.script), *args],
                                  env=self.env, cwd=self.root, stdin=slave if tty else subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, timeout=10)
        finally:
            os.close(master)
            os.close(slave)

    def calls(self, name):
        log = self.root / 'calls'
        return [x[1] for x in map(json.loads, log.read_text().splitlines()) if x[0] == name] if log.exists() else []

    def existing(self, bootstrap=True):
        self.dest.mkdir(parents=True)
        (self.dest / '.git').mkdir()
        (self.dest / 'precious').write_text('keep')
        if bootstrap:
            (self.dest / 'scripts').mkdir()
            (self.dest / 'scripts/bootstrap.sh').write_text('printf "bootstrap\\n" >> "$FIXTURE/bootstrap-calls"\n')

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('core bootstrap completed', result.stdout)
        self.assertTrue((self.root / 'bootstrap-calls').exists())
        self.assertFalse(list(self.root.glob('foundry-shim.*')))
        self.assertFalse(list((self.home / 'repositories').glob('.foundry-shim.*')))

    def assert_failure(self, result, message=None):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn('core bootstrap completed', result.stdout)
        if message:
            self.assertIn(message, result.stdout)
        self.assertFalse(list(self.root.glob('foundry-shim.*')))
        self.assertFalse(list((self.home / 'repositories').glob('.foundry-shim.*')))

    def test_ready_rerun_preserves_checkout(self):
        self.assert_success(self.run_installer())
        self.assert_success(self.run_installer())
        self.assertEqual(len([a for a in self.calls('git') if a[0] == 'clone']), 1)
        self.assertFalse([a for a in self.calls('gh') if a[:2] == ['auth', 'login']])
        self.assertFalse([a for a in self.calls('brew') if a[0] == 'install'])
        self.assertIn(['config', 'set', 'git_protocol', 'ssh', '--host', 'github.com'], self.calls('gh'))

    def test_auth_and_ssh_recovery(self):
        for scenario, skip in [('auth_missing', True), ('auth_missing,ssh_missing', False)]:
            with self.subTest(scenario=scenario):
                (self.root / 'logged-in').unlink(missing_ok=True)
                (self.root / 'uploaded').unlink(missing_ok=True)
                (self.root / 'calls').unlink(missing_ok=True)
                self.assert_success(self.run_installer(scenario))
                login = [a for a in self.calls('gh') if a[:2] == ['auth', 'login']]
                self.assertEqual(login, [['auth', 'login', '--hostname', 'github.com', '--git-protocol', 'ssh'] + (['--skip-ssh-key'] if skip else [])])

    def test_existing_token_uploads_key_without_login(self):
        self.env['GH_TOKEN'] = 'fixture-token'
        self.assert_success(self.run_installer('ssh_missing'))
        self.assertEqual([a for a in self.calls('gh') if a[:2] == ['auth', 'login']], [])
        self.assertIn(['ssh-key', 'add', str(self.home / '.ssh/id_ed25519.pub')], self.calls('gh'))
        self.assertFalse(self.calls('ssh-keygen'))

    def test_ssh_upload_targets_github_with_enterprise_default(self):
        self.env['GH_HOST'] = 'enterprise.example.com'
        self.assert_success(self.run_installer('ssh_missing'))
        self.assertEqual([a for a in self.calls('gh') if a[:2] == ['auth', 'login']], [])
        self.assertEqual(self.env['GH_HOST'], 'enterprise.example.com')

    def test_generate_key_preserves_passphrase_prompt(self):
        shutil.rmtree(self.home / '.ssh')
        self.assert_success(self.run_installer('ssh_missing', answers='\nY\n'))
        self.assertEqual(self.calls('ssh-keygen'), [['-t', 'ed25519', '-f', str(self.home / '.ssh/id_ed25519')]])
        self.assertEqual((self.home / '.ssh/id_ed25519').read_text(), 'private-fixture')
        self.assertEqual((self.home / '.ssh').stat().st_mode & 0o777, 0o700)

    def test_default_existing_rsa_key_reused(self):
        (self.home / '.ssh/id_ed25519.pub').rename(self.home / '.ssh/id_rsa.pub')
        self.assert_success(self.run_installer('ssh_missing'))
        self.assertIn(['ssh-key', 'add', str(self.home / '.ssh/id_rsa.pub')], self.calls('gh'))

    def test_key_setup_refusals(self):
        key = self.home / '.ssh/id_ed25519.pub'
        key.unlink()
        self.assert_failure(self.run_installer('ssh_missing', answers='\nN\n'), 'declined')
        self.assert_failure(self.run_installer('ssh_missing', answers='/missing.pub\ny\n'), 'does not exist')
        self.assert_failure(self.run_installer('ssh_missing,keygen_fail', answers='\ny\n'))
        private = self.home / '.ssh/id_ed25519'
        private.write_text('keep')
        self.assert_failure(self.run_installer('ssh_missing', answers='\ny\n'), 'private key already exists')
        self.assertEqual(private.read_text(), 'keep')
        private.unlink()
        private.symlink_to(self.home / 'absent-private')
        self.assert_failure(self.run_installer('ssh_missing', answers='\ny\n'), 'private key already exists')
        self.assertTrue(private.is_symlink())
        private.unlink()
        key.symlink_to(self.home / 'absent')
        self.assert_failure(self.run_installer('ssh_missing', answers='\ny\n'), 'public key path already exists')
        self.assertFalse([a for a in self.calls('gh') if a[:2] == ['ssh-key', 'add']])

    def test_upload_failure_and_input_eof_stop(self):
        self.assert_failure(self.run_installer('ssh_missing,upload_fail'), 'SSH key upload failed')
        self.assert_failure(self.run_installer('ssh_missing', answers='\x04'))
        self.assertFalse((self.root / 'bootstrap-calls').exists())

    def test_tools_install(self):
        (self.bin / 'ssh').unlink()
        self.assert_success(self.run_installer('git_missing,gh_missing'))
        self.assertIn(['install', 'git', 'gh', 'openssh'], self.calls('brew'))

    def test_new_git_replaces_cached_developer_tools_stub(self):
        later = self.root / 'later'
        later.mkdir()
        (later / 'git').write_text('#!/bin/bash\nexit 1\n')
        (later / 'git').chmod(0o755)
        (self.bin / 'git').unlink()
        self.env['PATH'] += ':' + str(later)
        self.assert_success(self.run_installer('hash_git'))
        self.assertIn(['install', 'git'], self.calls('brew'))

    def test_existing_origin_variants(self):
        self.existing()
        for origin in ('git@github.com:vinnyputty/foundry',
                       'ssh://git@github.com/vinnyputty/foundry.git',
                       'ssh://git@github.com/vinnyputty/foundry',
                       'https://github.com/vinnyputty/foundry',
                       'https://github.com/vinnyputty/foundry.git'):
            with self.subTest(origin=origin):
                self.env['ORIGIN'] = origin
                self.assert_success(self.run_installer())
        self.assertEqual((self.dest / 'precious').read_text(), 'keep')

    def test_ssh_missing_after_install_refused(self):
        (self.bin / 'ssh').unlink()
        self.assert_failure(self.run_installer('ssh_unavailable'), 'SSH is unavailable')

    def test_homebrew_discovered_outside_path(self):
        (self.root / 'prefix/bin').mkdir(parents=True)
        (self.bin / 'brew').rename(self.root / 'prefix/bin/brew')
        self.assert_success(self.run_installer())
        self.assertFalse(self.calls('curl'))

    def test_macos_new_homebrew(self):
        (self.bin / 'brew').unlink()
        self.assert_success(self.run_installer())
        self.assertEqual(self.calls('provision-brew'), [[]])
        self.assertFalse(self.calls('sudo'))

    def test_ubuntu_fresh_both_architectures(self):
        self.env.update(OS='Linux', ARCH='x86_64')
        (self.bin / 'brew').unlink()
        self.assert_success(self.run_installer('gh_missing'))
        self.assertEqual(self.calls('sudo'), [['apt-get', 'update'], ['apt-get', 'install', '-y', 'build-essential', 'procps', 'curl', 'file', 'git', 'ca-certificates', 'openssh-client']])
        self.env['ARCH'] = 'aarch64'
        self.assert_success(self.run_installer())

    def test_wget_fallback(self):
        (self.bin / 'brew').unlink()
        (self.bin / 'curl').unlink()
        self.assert_success(self.run_installer())
        self.assertEqual(len(self.calls('wget')), 1)

    def test_ubuntu_without_downloader(self):
        self.env.update(OS='Linux', ARCH='x86_64')
        for name in ('brew', 'curl', 'wget'):
            (self.bin / name).unlink()
        self.assert_success(self.run_installer())
        self.assertEqual(len(self.calls('curl')), 1)

    def test_no_downloader_macos(self):
        for name in ('brew', 'curl', 'wget'):
            (self.bin / name).unlink()
        self.assert_failure(self.run_installer(), 'curl or wget')

    def test_platform_refusals(self):
        for system, arch, distro in [('FreeBSD', 'x86_64', 'ubuntu'),
                                     ('Linux', 's390x', 'ubuntu'),
                                     ('Linux', 'x86_64', 'debian')]:
            with self.subTest(system=system, arch=arch, distro=distro):
                self.env.update(OS=system, ARCH=arch)
                (self.root / 'os-release').write_text('ID=' + distro + '\n')
                self.assert_failure(self.run_installer())
        self.assertFalse(self.calls('brew'))

    def test_intel_existing_brew_allowed_new_install_refused(self):
        self.env['ARCH'] = 'x86_64'
        self.assert_success(self.run_installer())
        (self.bin / 'brew').unlink()
        self.assert_failure(self.run_installer(), 'Apple Silicon')

    def test_root_and_terminal_guards(self):
        self.assert_failure(self.run_installer('root'), 'without sudo')
        self.assert_failure(self.run_installer(tty=False), 'terminal')
        self.assertFalse(self.calls('brew'))

    def test_arguments(self):
        self.assertEqual(self.run_installer(args=['--help'], tty=False).returncode, 0)
        cases = [(['--wat'], 'unknown argument'),
                 (['--destination'], 'requires an absolute path'),
                 (['--destination', 'relative'], 'must be an absolute path')]
        for invalid in ('/', str(self.home) + '/../bad', str(self.home) + '/./bad', str(self.home) + '//bad'):
            cases.append((['--destination', invalid], 'non-root path'))
        for args, message in cases:
            with self.subTest(args=args):
                self.assert_failure(self.run_installer(args=args), message)
        self.assertFalse(self.calls('brew'))

    def test_destination_override(self):
        alternate = self.home / 'another checkout'
        self.assert_success(self.run_installer(args=['--destination', str(alternate)]))
        self.assertTrue((alternate / '.git').is_dir())
        self.assertFalse(self.dest.exists())

    def test_existing_checkout_keeps_data(self):
        self.existing()
        self.assert_success(self.run_installer())
        self.assertEqual((self.dest / 'precious').read_text(), 'keep')
        self.assertFalse([a for a in self.calls('git') if a[0] == 'clone'])

    def test_checkout_conflicts(self):
        self.existing()
        for scenario, message in [('wrong_origin', 'origin is not'),
                                  ('origin_missing', 'has no origin'),
                                  ('nested_repo', 'inside another')]:
            with self.subTest(scenario=scenario):
                self.assert_failure(self.run_installer(scenario), message)
                self.assertEqual((self.dest / 'precious').read_text(), 'keep')
        (self.dest / '.git').rmdir()
        self.assert_failure(self.run_installer(), 'not a Git checkout')

    def test_symlink_and_file_conflicts(self):
        self.dest.parent.mkdir(parents=True)
        target = self.home / 'precious'
        target.write_text('keep')
        self.dest.symlink_to(target)
        self.assert_failure(self.run_installer(), 'symlink')
        self.dest.unlink()
        self.dest.write_text('keep')
        self.assert_failure(self.run_installer(), 'regular checkout')
        self.assertEqual(self.dest.read_text(), 'keep')
        self.assertEqual(target.read_text(), 'keep')

    def test_gitdir_symlink_refused(self):
        self.existing()
        (self.dest / '.git').rmdir()
        (self.dest / '.git').symlink_to(self.home)
        self.assert_failure(self.run_installer(), 'regular checkout')

    def test_prerequisite_failures(self):
        for scenario in ('shellenv_fail', 'git_missing,brew_fail', 'git_unavailable',
                         'gh_unavailable', 'auth_missing,auth_fail', 'auth_broken',
                         'ssh_broken', 'config_fail', 'clone_fail', 'move_fail',
                         'bootstrap_fail'):
            with self.subTest(scenario=scenario):
                self.assert_failure(self.run_installer(scenario))
                if self.dest.exists():
                    shutil.rmtree(self.dest)
                (self.root / 'logged-in').unlink(missing_ok=True)
                (self.root / 'uploaded').unlink(missing_ok=True)

    def test_bootstrap_failure_preserves_checkout(self):
        self.assert_failure(self.run_installer('bootstrap_fail'))
        self.assertTrue((self.dest / '.git').is_dir())
        (self.dest / 'precious').write_text('keep')
        self.assert_failure(self.run_installer('bootstrap_fail'))
        self.assertEqual((self.dest / 'precious').read_text(), 'keep')
        self.assertTrue((self.dest / '.git').is_dir())
        self.assertEqual(len([a for a in self.calls('git') if a[0] == 'clone']), 1)

    def test_installer_failure_cleanup(self):
        (self.bin / 'brew').unlink()
        for scenario in ('download_fail', 'installer_fail', 'installer_no_brew'):
            with self.subTest(scenario=scenario):
                self.assert_failure(self.run_installer(scenario), 'did not provide brew' if scenario == 'installer_no_brew' else None)
                self.assertFalse((self.root / 'bootstrap-calls').exists())

    def test_apt_failure_stops(self):
        self.env.update(OS='Linux', ARCH='x86_64')
        (self.bin / 'brew').unlink()
        self.assert_failure(self.run_installer('apt_fail'))
        self.assertFalse(self.calls('curl'))

    def test_signals_cleanup_partial_clone(self):
        for scenario, code in [('signal_term', 143), ('signal_int', 130)]:
            with self.subTest(scenario=scenario):
                result = self.run_installer(scenario)
                self.assert_failure(result)
                self.assertEqual(result.returncode, code, result.stdout)
                self.assertFalse(self.dest.exists())

    def test_destination_appears_during_clone(self):
        self.assert_failure(self.run_installer('destination_race'), 'appeared during clone')
        self.assertEqual((self.dest / 'precious').read_text(), 'keep')
        self.assertFalse((self.dest / 'checkout').exists())

    def test_missing_bootstrap_keeps_existing_checkout(self):
        self.existing(bootstrap=False)
        self.assert_failure(self.run_installer(), 'no scripts/bootstrap.sh')
        self.assertEqual((self.dest / 'precious').read_text(), 'keep')

    def readme_command(self):
        return re.search(r'```sh\n(.*?)\n```', (REPO / 'README.md').read_text(), re.S).group(1)

    def test_readme_command_preserves_terminal(self):
        for downloader in ('curl', 'wget', 'apt'):
            with self.subTest(downloader=downloader):
                if downloader in ('wget', 'apt') and (self.bin / 'curl').exists():
                    (self.bin / 'curl').unlink()
                if downloader == 'apt':
                    (self.bin / 'wget').unlink()
                self.assert_success(self.run_installer('auth_missing,ssh_missing', command=['/bin/bash', '-c', self.readme_command()]))
        calls = [json.loads(line) for line in (self.root / 'calls').read_text().splitlines()]
        for name, args, tty in calls:
            if name in ('gh', 'git', 'provision-brew'):
                self.assertTrue(tty, (name, args))

    def test_readme_download_failures_stop(self):
        for scenario in ('download_fail', 'empty_download'):
            self.assert_failure(self.run_installer(scenario, command=['/bin/bash', '-c', self.readme_command()]))
        self.assertFalse(self.calls('brew'))


if __name__ == '__main__':
    unittest.main()
