#!/bin/bash
# ENGINEERING_GLUE: existing audited ROS environment exports; no old run.sh dispatch.
set -e
cache_app_root="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cache_legacy_root="${EXPLORER_LEGACY_ROOT:-/home/vlad/Explorer}"
cache_env_file="$cache_legacy_root/bin/env.sh"
cache_env_digest="$(sha256sum "$cache_env_file")"
cache_env_digest="${cache_env_digest%% *}"
if [ "$cache_env_digest" != '27baa57c5d488dfe217e88e3a551859f6505978bb8e283c58154f3cf4bb44323' ]; then
  echo 'Audited ROS environment exports changed; cache startup refused' >&2
  exit 1
fi
source "$cache_env_file"
export FASTRTPS_DEFAULT_PROFILES_FILE="$cache_legacy_root/config/fastdds-rgbd.xml"
export PYTHONPATH="$cache_app_root/shared:${PYTHONPATH:-}"
export PYTHONDONTWRITEBYTECODE=1
exec /usr/bin/python3 -B -m explorer_shared.nano_camera_cache --legacy-root "$cache_legacy_root" "$@"
