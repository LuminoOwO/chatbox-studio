#!/usr/bin/env bash
# Installs the ROCm 7.2.1 runtime libraries Chatbox Studio needs for AMD graphics cards
# (HIP, hipBLAS, hipRAND) from AMD's official apt repository. Ubuntu 22.04 / 24.04 and
# distributions based on them (Linux Mint 21 / 22, Pop!_OS, Zorin, ...).
# The graphics driver itself (amdgpu) is already part of the Linux kernel.
set -euo pipefail
VER=7.2.1
. /etc/os-release
CODENAME="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
case "$CODENAME" in
    noble|jammy) ;;
    *) echo "This helper supports Ubuntu 22.04/24.04 based systems (found: ${PRETTY_NAME:-?})."
       echo "Install ROCm $VER for your distribution (packages: HIP runtime, hipBLAS, hipRAND),"
       echo "see https://rocm.docs.amd.com - then click 'Check again' in the setup."
       read -r -p "Press Enter to close." _; exit 1 ;;
esac

echo "This installs AMD ROCm $VER (HIP runtime, hipBLAS, hipRAND, about 3 GB) from"
echo "repo.radeon.com and adds you to the 'render' and 'video' groups."
echo "It asks for your password."
read -r -p "Continue? [Y/n] " ok
[[ "$ok" =~ ^[nN] ]] && exit 1

sudo mkdir -p --mode=0755 /etc/apt/keyrings
wget -qO - https://repo.radeon.com/rocm/rocm.gpg.key | gpg --dearmor | \
    sudo tee /etc/apt/keyrings/rocm.gpg > /dev/null
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/rocm.gpg] https://repo.radeon.com/rocm/apt/$VER $CODENAME main" | \
    sudo tee /etc/apt/sources.list.d/rocm.list > /dev/null
printf 'Package: *\nPin: release o=repo.radeon.com\nPin-Priority: 600\n' | \
    sudo tee /etc/apt/preferences.d/rocm-pin-600 > /dev/null
sudo apt update
sudo apt install -y rocm-hip-runtime hipblas hiprand
sudo usermod -aG render,video "$USER"
echo
echo "Done. Log out and back in once (so the new groups apply),"
echo "then click 'Check again' / 'Install' in Chatbox Studio Setup."
read -r -p "Press Enter to close." _
