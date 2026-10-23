#!/bin/bash
set -euo pipefail

fail() {
  printf 'foundry-shim: %s\n' "$*" >&2
  exit 1
}

usage() {
  printf 'Usage: bash install.sh [--destination ABSOLUTE_PATH]\n'
}

destination="$HOME/repositories/foundry"
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h | --help)
      usage
      exit 0
      ;;
    --destination)
      [ "$#" -ge 2 ] || fail '--destination requires an absolute path'
      destination="$2"
      shift 2
      ;;
    *) fail "unknown argument: $1" ;;
  esac
done
case "$destination" in
  /*) ;;
  *) fail 'destination must be an absolute path' ;;
esac
destination="${destination%/}"
case "$destination/" in
  / | *//* | */../* | */./*) fail 'destination must be a non-root path without . or .. components' ;;
esac
[ ! -L "$destination" ] || fail 'destination must not be a symlink'
[ "$(id -u)" -ne 0 ] || fail 'run as your own user, without sudo'

os="$(uname -s)"
arch="$(uname -m)"
case "$os:$arch" in
  Darwin:arm64 | Darwin:x86_64) ;;
  Linux:x86_64 | Linux:aarch64)
    # shellcheck disable=SC1091
    . /etc/os-release
    [ "$ID" = ubuntu ] || fail 'only Ubuntu Linux is supported'
    ;;
  *) fail "unsupported platform: $os $arch" ;;
esac
[ -t 0 ] || fail 'run from a terminal; download the script before invoking Bash'

scratch=''
cleanup() {
  [ -z "$scratch" ] || rm -rf -- "$scratch"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

find_brew() {
  if command -v brew >/dev/null 2>&1; then
    command -v brew
    return
  fi
  local candidate
  for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew /home/linuxbrew/.linuxbrew/bin/brew; do
    if [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return
    fi
  done
  return 1
}

download() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$1" -o "$2"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO "$2" "$1"
  else
    fail 'curl or wget is required to download Homebrew'
  fi
}

brew_bin="$(find_brew)" || brew_bin=''
if [ -z "$brew_bin" ]; then
  if [ "$os" = Linux ]; then
    sudo apt-get update
    sudo apt-get install -y build-essential procps curl file git ca-certificates openssh-client
  else
    [ "$arch" = arm64 ] || fail 'new Homebrew installations require Apple Silicon; existing Intel Homebrew can be reused'
  fi
  scratch="$(mktemp -d "${TMPDIR:-/tmp}/foundry-shim.XXXXXX")"
  download https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh "$scratch/homebrew.sh"
  /bin/bash "$scratch/homebrew.sh"
  cleanup
  scratch=''
  brew_bin="$(find_brew)" || fail 'Homebrew installation did not provide brew'
fi
brew_env="$("$brew_bin" shellenv)"
eval "$brew_env"

missing=()
if ! git --version >/dev/null 2>&1; then missing+=(git); fi
if ! gh --version >/dev/null 2>&1; then missing+=(gh); fi
if ! command -v ssh >/dev/null 2>&1; then missing+=(openssh); fi
if [ "${#missing[@]}" -gt 0 ]; then
  "$brew_bin" install "${missing[@]}"
  hash -r
fi
git --version >/dev/null 2>&1 || fail 'Git is unavailable after installation'
gh --version >/dev/null 2>&1 || fail 'GitHub CLI is unavailable after installation'
command -v ssh >/dev/null 2>&1 || fail 'SSH is unavailable after installation'

if [ -e "$destination" ]; then
  [ -d "$destination" ] && [ ! -L "$destination/.git" ] || fail 'destination is not a regular checkout'
  checkout="$(git -C "$destination" rev-parse --show-toplevel)" || fail 'destination is not a Git checkout'
  [ "$checkout" = "$(cd "$destination" && pwd -P)" ] || fail 'destination is inside another checkout'
  origin="$(git -C "$destination" remote get-url origin)" || fail 'destination has no origin'
  case "$origin" in
    git@github.com:vinnyputty/foundry.git | git@github.com:vinnyputty/foundry | ssh://git@github.com/vinnyputty/foundry.git | ssh://git@github.com/vinnyputty/foundry | https://github.com/vinnyputty/foundry.git | https://github.com/vinnyputty/foundry) ;;
    *) fail 'destination origin is not vinnyputty/foundry' ;;
  esac
fi

remote=git@github.com:vinnyputty/foundry.git
ssh_ready=0
if git ls-remote "$remote" HEAD >/dev/null; then ssh_ready=1; fi
if ! gh auth status --hostname github.com >/dev/null 2>&1; then
  if [ "$ssh_ready" -eq 1 ]; then
    gh auth login --hostname github.com --git-protocol ssh --skip-ssh-key
  else
    gh auth login --hostname github.com --git-protocol ssh
  fi
elif [ "$ssh_ready" -eq 0 ]; then
  public_key="$HOME/.ssh/id_ed25519.pub"
  for candidate in "$HOME/.ssh/id_ed25519.pub" "$HOME/.ssh/id_ecdsa.pub" "$HOME/.ssh/id_rsa.pub"; do
    if [ -f "$candidate" ]; then
      public_key="$candidate"
      break
    fi
  done
  read -r -p "SSH public key to upload [$public_key]: " selected_key
  public_key="${selected_key:-$public_key}"
  if [ ! -f "$public_key" ]; then
    [ "$public_key" = "$HOME/.ssh/id_ed25519.pub" ] || fail 'selected public key does not exist'
    [ ! -e "$HOME/.ssh/id_ed25519" ] && [ ! -L "$HOME/.ssh/id_ed25519" ] || fail 'private key already exists; select its public key without overwriting it'
    [ ! -e "$public_key" ] && [ ! -L "$public_key" ] || fail 'public key path already exists'
    read -r -p 'Generate a new SSH key for this machine? [y/N] ' answer
    case "$answer" in
      y | Y | yes | YES)
        [ -d "$HOME/.ssh" ] || mkdir -m 700 "$HOME/.ssh"
        ssh-keygen -t ed25519 -f "$HOME/.ssh/id_ed25519"
        ;;
      *) fail 'SSH key generation declined' ;;
    esac
  fi
  GH_HOST=github.com gh ssh-key add "$public_key" || fail 'SSH key upload failed; ensure the GitHub token has permission to write SSH keys (gh auth refresh --hostname github.com --scopes admin:public_key for stored credentials)'

fi
gh auth status --hostname github.com >/dev/null 2>&1 || fail 'GitHub CLI authentication is still unavailable'
gh config set git_protocol ssh --host github.com
if [ "$ssh_ready" -eq 0 ]; then
  git ls-remote "$remote" HEAD >/dev/null || fail 'SSH access to vinnyputty/foundry is still unavailable; check the key, account, repository access, and network'
fi

if [ ! -e "$destination" ]; then
  parent="$(dirname "$destination")"
  mkdir -p "$parent"
  scratch="$(mktemp -d "$parent/.foundry-shim.XXXXXX")"
  git clone "$remote" "$scratch/checkout"
  [ ! -e "$destination" ] && [ ! -L "$destination" ] || fail 'destination appeared during clone; refusing to replace it'
  mv "$scratch/checkout" "$destination"
  cleanup
  scratch=''
fi
[ -f "$destination/scripts/bootstrap.sh" ] || fail 'checkout has no scripts/bootstrap.sh'
/bin/bash "$destination/scripts/bootstrap.sh"
printf 'foundry-shim: core bootstrap completed in %s\n' "$destination"
