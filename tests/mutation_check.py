"""Focused guard and cleanup mutations; run separately from unittest discovery."""
import io
import unittest

from test_install import InstallerTests

GUARDS = [
    ('requires an absolute path', 'test_arguments'),
    ('unknown argument:', 'test_arguments'),
    ('must be an absolute path', 'test_arguments'),
    ('must be a non-root path', 'test_arguments'),
    ('destination must not be a symlink', 'test_symlink_and_file_conflicts'),
    ('run as your own user', 'test_root_and_terminal_guards'),
    ('only Ubuntu Linux', 'test_platform_refusals'),
    ('unsupported platform:', 'test_platform_refusals'),
    ('run from a terminal', 'test_root_and_terminal_guards'),
    ('curl or wget is required', 'test_no_downloader_macos'),
    ('new Homebrew installations', 'test_intel_existing_brew_allowed_new_install_refused'),
    ('Homebrew installation did not', 'test_installer_failure_cleanup'),
    ('Git is unavailable', 'test_prerequisite_failures'),
    ('GitHub CLI is unavailable', 'test_prerequisite_failures'),
    ('SSH is unavailable', 'test_ssh_missing_after_install_refused'),
    ('not a regular checkout', 'test_gitdir_symlink_refused'),
    ('not a Git checkout', 'test_checkout_conflicts'),
    ('inside another checkout', 'test_checkout_conflicts'),
    ('has no origin', 'test_checkout_conflicts'),
    ('origin is not', 'test_checkout_conflicts'),
    ('selected public key does not', 'test_key_setup_refusals'),
    ('private key already exists', 'test_key_setup_refusals'),
    ('public key path already exists', 'test_key_setup_refusals'),
    ('SSH key generation declined', 'test_key_setup_refusals'),
    ('SSH key upload failed', 'test_upload_failure_and_input_eof_stop'),
    ('GitHub CLI authentication is still', 'test_prerequisite_failures'),
    ('SSH access to vinnyputty/foundry is still', 'test_prerequisite_failures'),
    ('destination appeared during clone', 'test_destination_appears_during_clone'),
    ('no scripts/bootstrap.sh', 'test_missing_bootstrap_keeps_existing_checkout'),
]


def remove_guard(source, marker):
    lines = source.splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines) if marker in line]
    assert len(matches) == 1, marker
    i = matches[0]
    if marker == 'unknown argument:':
        lines[i] = lines[i].replace('fail ', 'shift; : ')
    elif marker == 'requires an absolute path':
        lines[i] = '      usage; exit 0\n'
    else:
        lines[i] = lines[i].replace('fail ', ': ')
    return ''.join(lines)


def run_mutation(name, method, mutate, readme=False):
    class Mutated(InstallerTests):
        def setUp(self):
            super().setUp()
            if not readme:
                self.script.write_text(mutate(self.script.read_text()))

        def readme_command(self):
            command = super().readme_command()
            return mutate(command) if readme else command
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream).run(unittest.TestSuite([Mutated(method)]))
    killed = bool(result.failures) and not result.errors
    if result.errors:
        print(stream.getvalue(), flush=True)
    print(('caught: ' if killed else 'SURVIVED: ') + name, flush=True)
    return killed


if __name__ == '__main__':
    outcomes = [run_mutation(marker, method, lambda source, marker=marker: remove_guard(source, marker))
                for marker, method in GUARDS]
    edits = [
        ('exit cleanup trap', 'test_installer_failure_cleanup', 'trap cleanup EXIT', ':'),
        ('cleanup removes temporary directory', 'test_destination_appears_during_clone',
         '[ -z "$scratch" ] || rm -rf -- "$scratch"', ':'),
        ('successful Homebrew cleanup', 'test_macos_new_homebrew',
         '  cleanup\n  scratch=\'\'\n  brew_bin=', '  scratch=\'\'\n  brew_bin='),
        ('successful clone cleanup', 'test_ready_rerun_preserves_checkout',
         '  cleanup\n  scratch=\'\'\nfi\n[ -f', '  scratch=\'\'\nfi\n[ -f'),
        ('interrupt cleanup', 'test_signals_cleanup_partial_clone', "trap 'exit 130' INT", "trap ':' INT"),
        ('termination cleanup', 'test_signals_cleanup_partial_clone', "trap 'exit 143' TERM", "trap ':' TERM"),
        ('GitHub SSH upload host', 'test_ssh_upload_targets_github_with_enterprise_default',
         'GH_HOST=github.com gh ssh-key add', 'gh ssh-key add'),
        ('Bash cached tool path', 'test_new_git_replaces_cached_developer_tools_stub', '  hash -r', '  :'),
        ('clone failure propagation', 'test_prerequisite_failures',
         '  git clone "$remote" "$scratch/checkout"', '  git clone "$remote" "$scratch/checkout" || :'),
        ('bootstrap failure propagation', 'test_bootstrap_failure_preserves_checkout',
         '/bin/bash "$destination/scripts/bootstrap.sh"', '/bin/bash "$destination/scripts/bootstrap.sh" || :'),
    ]
    for name, method, before, after in edits:
        def mutate(source, before=before, after=after):
            assert source.count(before) == 1, before
            return source.replace(before, after)
        outcomes.append(run_mutation(name, method, mutate))
    for name, method, before, after in [
        ('download failure propagation', 'test_readme_download_failures_stop',
         'install.sh) && [ -n "$script" ] &&', 'install.sh); [ -n "$script" ] &&'),
        ('empty download refusal', 'test_readme_download_failures_stop', '[ -n "$script" ]', ':'),
        ('terminal stdin preservation', 'test_readme_command_preserves_terminal',
         '/bin/bash -c "$script"', 'printf \'%s\' "$script" | /bin/bash'),
    ]:
        outcomes.append(run_mutation(name, method, lambda command, before=before, after=after: command.replace(before, after), readme=True))
    print(f'{sum(outcomes)}/{len(outcomes)} mutations caught')
    raise SystemExit(not all(outcomes))
