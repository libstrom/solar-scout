#!/usr/bin/env bash
# Idempotently install solar-scout's Python deps so Claude Code web sessions can
# boot and run the app. Quiet and NON-FATAL: all output goes to a log and we
# always exit 0 — a slow or offline install must never block a session, and this
# runs alongside the superpowers SessionStart hook (which emits JSON), so we must
# print nothing to stdout.
#
# Wired into SessionStart (matcher: startup) in .claude/settings.json.

LOG="${TMPDIR:-/tmp}/solar-scout-setup-deps.log"
REPO="$(cd "$(dirname "$0")/../.." 2>/dev/null && pwd)"

{
  date
  # Fast path: if the heavy deps already import, the container is warm — skip.
  if python -c "import streamlit, supabase, googlemaps" 2>/dev/null; then
    echo "deps present — nothing to do"
    exit 0
  fi
  echo "installing deps from ${REPO}/requirements.txt ..."
  # googlemaps' sdist fails to build under Debian's patched setuptools
  # (install_layout AttributeError) unless we force stdlib distutils.
  SETUPTOOLS_USE_DISTUTILS=stdlib python -m pip install -q -r "${REPO}/requirements.txt" \
    || echo "pip install -r requirements.txt failed (continuing)"
  # supabase pulls a PyJWT that pip cannot uninstall (Debian-managed, no RECORD).
  python -m pip install -q supabase --ignore-installed PyJWT \
    || echo "supabase install failed (continuing)"
  echo "done"
} >"${LOG}" 2>&1

exit 0
