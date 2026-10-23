#!PYTHON
import json
import os
from pathlib import Path
import sys

root = Path(os.environ['FIXTURE'])
name = Path(sys.argv[0]).name
args = sys.argv[1:]
with (root / 'calls').open('a') as log:
    log.write(json.dumps([name, args, os.isatty(0)]) + '\n')


def has(key):
    return key in os.environ.get('SCENARIO', '').split(',')


def stop(key):
    if has(key):
        sys.exit(27)


def install(tool, location=None):
    dest = location or root / 'bin' / tool
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.symlink_to(root / 'dispatcher')


def checkout(dest):
    dest.mkdir(parents=True, exist_ok=True)
    (dest / '.git').mkdir(exist_ok=True)
    (dest / 'scripts').mkdir(exist_ok=True)
    (dest / 'scripts/bootstrap.sh').write_text(
        '#!/bin/bash\nprintf "bootstrap\\n" >> "$FIXTURE/bootstrap-calls"\n'
        '[ "${SCENARIO#*bootstrap_fail}" = "$SCENARIO" ]\n')


if name == 'id':
    print(0 if has('root') else 501)
elif name == 'uname':
    print(os.environ.get('ARCH', 'arm64') if '-m' in args else os.environ.get('OS', 'Darwin'))
elif name == 'provision-brew':
    stop('installer_fail')
    if not has('installer_no_brew'):
        install('brew', root / 'prefix/bin/brew')
elif name == 'brew':
    if args == ['shellenv']:
        stop('shellenv_fail')
        print('export PATH="' + str(root / 'bin') + ':$PATH"')
    elif args[0] == 'install':
        stop('brew_fail')
        (root / 'tools-installed').touch()
        if has('hash_git'):
            install('git')
        if 'openssh' in args and not has('ssh_unavailable'):
            install('ssh')
elif name == 'sudo':
    stop('apt_fail')
    if 'install' in args:
        install('curl')
        (root / 'apt-installed').touch()
elif name in ('curl', 'wget'):
    stop('download_fail')
    dest = (args[args.index('-o') + 1] if '-o' in args else '-') if name == 'curl' else args[args.index('-qO') + 1]
    if any('Homebrew/install' in arg for arg in args):
        Path(dest).write_text('#!/bin/bash\nprovision-brew\n')
    elif dest == '-':
        if not has('empty_download'):
            print((root / 'install.sh').read_text())
    else:
        sys.exit(28)
elif name == 'git':
    if args == ['--version']:
        if has('git_missing') and not (root / 'tools-installed').exists() and not (root / 'apt-installed').exists():
            sys.exit(1)
        if has('git_unavailable'):
            sys.exit(1)
        print('git version 2.50.0')
    elif args[0] == 'ls-remote':
        if has('ssh_broken') or (has('ssh_missing') and not (root / 'logged-in').exists() and not (root / 'uploaded').exists()):
            sys.exit(1)
        print('abc\tHEAD')
    elif args[0] == 'clone':
        checkout(Path(args[-1]))
        if has('destination_race'):
            dest = Path(os.environ['DESTINATION'])
            dest.mkdir()
            (dest / 'precious').write_text('keep')
        if has('signal_term') or has('signal_int'):
            import signal
            os.kill(os.getppid(), signal.SIGTERM if has('signal_term') else signal.SIGINT)
        stop('clone_fail')
    elif args[0] == '-C':
        dest = Path(args[1])
        if args[2] == 'rev-parse':
            if not (dest / '.git').exists():
                sys.exit(1)
            print(dest.parent if has('nested_repo') else dest.resolve())
        else:
            stop('origin_missing')
            print('git@github.com:someone/other.git' if has('wrong_origin') else os.environ.get('ORIGIN', 'git@github.com:vinnyputty/foundry.git'))
    else:
        sys.exit(29)
elif name == 'gh':
    if args == ['--version']:
        if has('gh_missing') and not (root / 'tools-installed').exists():
            sys.exit(1)
        if has('gh_unavailable'):
            sys.exit(1)
    elif args[:2] == ['auth', 'status']:
        if has('auth_broken') or (has('auth_missing') and not (root / 'logged-in').exists()):
            sys.exit(1)
    elif args[:2] == ['auth', 'login']:
        stop('auth_fail')
        (root / 'logged-in').touch()
    elif args[:2] == ['ssh-key', 'add']:
        if os.environ.get('GH_HOST') != 'github.com':
            sys.exit(31)
        stop('upload_fail')
        (root / 'uploaded').touch()
    elif args[:2] == ['config', 'set']:
        stop('config_fail')
    else:
        sys.exit(30)
elif name == 'ssh-keygen':
    stop('keygen_fail')
    dest = Path(args[args.index('-f') + 1])
    dest.write_text('private-fixture')
    Path(str(dest) + '.pub').write_text('public-fixture')
elif name == 'mv':
    stop('move_fail')
    os.rename(args[0], args[1])
