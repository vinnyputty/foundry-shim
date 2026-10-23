# foundry-shim

Prepare macOS or Ubuntu, authenticate to GitHub, clone [`vinnyputty/foundry`](https://github.com/vinnyputty/foundry) into `~/repositories/foundry`, and run its default core bootstrap. Access to that private repository is required.

Run this command in a terminal as your own user:

```sh
/bin/bash -c 'set -e; url=https://raw.githubusercontent.com/vinnyputty/foundry-shim/main/install.sh; if command -v curl >/dev/null 2>&1; then script="$(curl -fsSL "$url")"; elif command -v wget >/dev/null 2>&1; then script="$(wget -qO - "$url")"; else sudo apt-get update; sudo apt-get install -y curl ca-certificates; script="$(curl -fsSL "$url")"; fi; [ -n "$script" ]; /bin/bash -c "$script"'
```

The command downloads before executing, preserving terminal input for every installer and authentication prompt. Stock macOS includes curl; Ubuntu can use curl or wget, or install curl with sudo and apt when both are missing. A failed or empty download stops execution. To inspect the installer first, download [`install.sh`](install.sh), read it, then run `bash install.sh`. Choose another checkout with `bash install.sh --destination "$HOME/another/foundry"`.

Fresh installs target Apple Silicon macOS 15 or newer, and Ubuntu on x86_64 or aarch64, subject to [Homebrew's current platform requirements](https://docs.brew.sh/Installation) and [Linux requirements](https://docs.brew.sh/Homebrew-on-Linux). Existing Intel macOS Homebrew installations can be reused; new Intel installations are unsupported by the current upstream installer. Older operating systems may work with already installed tools, but are not promised fresh-install support.

The shim reuses Homebrew from PATH or its standard prefixes, activates it in this process, and installs missing Git, GitHub CLI, or SSH. Ubuntu gets Homebrew's initial build/download prerequisites through apt when Homebrew is absent. The [official Homebrew installer](https://github.com/Homebrew/install/blob/HEAD/install.sh) handles missing macOS developer tools, including its software-update and graphical installation prompts. Package selection, dotfiles, and machine configuration belong to foundry's bootstrap.

Expect these prompts when the corresponding setup is missing:

- sudo password, Homebrew confirmation, and macOS developer-tool installation or license acceptance as required by the upstream installers;
- SSH host-key verification against [GitHub's published fingerprints](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints), and any existing SSH key passphrase;
- GitHub browser/device authorization and SSH key selection or generation/upload through [`gh auth login`](https://cli.github.com/manual/gh_auth_login) when CLI authentication is missing;
- an existing public-key path, or confirmation to generate a per-machine Ed25519 key with native `ssh-keygen` passphrase prompts, when GitHub CLI authentication already works but SSH access does not. The existing token is reused for `gh ssh-key add`; it needs SSH-key write permission. A restricted environment token may need replacement with a suitable token. Custom key paths must already be usable through your SSH agent or SSH configuration;
- any prompts from foundry's default core bootstrap.

SSH access is checked against the actual private repository. Existing working SSH keys and CLI authentication are reused; GitHub CLI's default protocol for github.com is set to SSH. Keys and credentials are never replaced automatically. Upload or access failures stop with an error; account access, network, or SSH configuration problems may need manual repair.

Reruns reuse the correct existing checkout without pulling, resetting, or changing its origin. Conflicting paths, destination symlinks, and unrelated repositories are refused. Downloads and partial clones use temporary directories that are cleaned on failure; an existing checkout and a successfully cloned checkout are preserved if core bootstrap fails. Prerequisites already installed are retained. Run one shim instance per destination.

Verification uses Python stdlib tests with isolated homes, fake provisioners/network tools, and terminal file descriptors. CI runs shell syntax, ShellCheck, shfmt, and those tests on macOS and Ubuntu. **No real fresh-machine installation has been tested yet.** The tests establish orchestration and failure handling, not successful live package installation or browser authentication.

```sh
bash -n install.sh
shellcheck install.sh
shfmt -d -i 2 -ci install.sh
python3 -m unittest discover -s tests -v
```

Run `python3 tests/mutation_check.py` for focused checks that remove refusal and cleanup behavior and require the relevant test assertions to fail.
