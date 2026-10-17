#!/bin/bash
# Lightsail launch script for Ubuntu 24.04 LTS. Paste this whole file into
# "Add launch script" when creating the instance; it runs once, as root, at first boot.
# It adds swap, installs Docker Engine + Compose, and clones FloatChat for the ubuntu user.
# Progress: sudo tail -f /var/log/cloud-init-output.log  (done when it prints "FloatChat launch script finished")
# Lightsail prepends its own setup and runs the result with /bin/sh (dash), so keep this POSIX:
# no pipefail, no [[ ]]. Safe to re-run by hand:  curl -fsSL <raw URL of this file> | sudo sh
set -eux
REPO_URL=https://github.com/kartiks15/FloatChat.git

# 2 GB of swap keeps Postgres, two Dash workers and image builds alive on the 2 GB bundle.
if ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# Docker Engine and the Compose plugin from Docker's apt repository.
# Wait up to 5 minutes if first-boot apt jobs hold the package lock.
export DEBIAN_FRONTEND=noninteractive
apt-get -o DPkg::Lock::Timeout=300 update
apt-get -o DPkg::Lock::Timeout=300 install -y ca-certificates curl git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get -o DPkg::Lock::Timeout=300 update
apt-get -o DPkg::Lock::Timeout=300 install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
usermod -aG docker ubuntu

if [ ! -d /home/ubuntu/FloatChat ]; then
  sudo -u ubuntu git clone "$REPO_URL" /home/ubuntu/FloatChat
fi
echo "FloatChat launch script finished"
