#!/usr/bin/env bash
# Job-scoped staging authentication; sudo Docker always receives the explicit config.
set -euo pipefail
set +x

staging_auth_identity() {
  [[ "${GITHUB_REPOSITORY:-}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ &&
     "${GITHUB_RUN_ID:-}" =~ ^[1-9][0-9]*$ &&
     "${GITHUB_RUN_ATTEMPT:-}" =~ ^[1-9][0-9]*$ &&
     "${CI_DOCKER_AUTH_SCOPE:-}" =~ ^(build|engine-(infinity|elasticsearch))$ ]] || {
    echo "Invalid staging Docker auth ownership identity" >&2; return 1;
  }
  STAGING_AUTH_ROOT=$(realpath -e -- "${RUNNER_TEMP:?}")
  [[ -d "$STAGING_AUTH_ROOT" && -w "$STAGING_AUTH_ROOT" &&
     "$STAGING_AUTH_ROOT" != *$'\n'* && "$STAGING_AUTH_ROOT" != *$'\r'* ]] || return 1
  STAGING_AUTH_PREFIX="ragflow-docker-${GITHUB_REPOSITORY//\//-}-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-${CI_DOCKER_AUTH_SCOPE}."
  STAGING_AUTH_OWNER="${GITHUB_REPOSITORY}:${GITHUB_RUN_ID}:${GITHUB_RUN_ATTEMPT}:${CI_DOCKER_AUTH_SCOPE}"
}

owned_staging_docker_config() {
  [[ -n "${CI_DOCKER_CONFIG:-}" ]] || return 1
  staging_auth_identity || return 1
  if [[ -L "$CI_DOCKER_CONFIG" ]]; then
    echo "Refusing symlink staging Docker auth cleanup" >&2; return 1
  fi
  [[ -d "$CI_DOCKER_CONFIG" ]] || return 1
  local resolved suffix
  resolved=$(realpath -e -- "$CI_DOCKER_CONFIG")
  [[ "$resolved" == "$STAGING_AUTH_ROOT/$STAGING_AUTH_PREFIX"* ]] || {
    echo "Refusing staging Docker auth cleanup outside owned temp path" >&2; return 1;
  }
  suffix=${resolved#"$STAGING_AUTH_ROOT/$STAGING_AUTH_PREFIX"}
  [[ "$suffix" =~ ^[A-Za-z0-9]{6}$ && -d "$resolved" &&
     ! -L "$resolved/.ragflow-owner" && -f "$resolved/.ragflow-owner" &&
     "$(cat "$resolved/.ragflow-owner")" == "$STAGING_AUTH_OWNER" ]] || {
    echo "Refusing foreign staging Docker auth cleanup" >&2; return 1;
  }
  STAGING_AUTH_OWNED_PATH=$resolved
}

cleanup_staging_docker_auth() {
  [[ -n "${CI_DOCKER_CONFIG:-}" ]] || return 0
  [[ -e "$CI_DOCKER_CONFIG" || -L "$CI_DOCKER_CONFIG" ]] || return 0
  owned_staging_docker_config || return 1
  # Docker/buildx can create root-owned subdirectories in this verified job config.
  sudo -n rm -rf -- "$STAGING_AUTH_OWNED_PATH"
}

job_registry_authority() {
  python3 - "${1:-}" "${2:-}" <<'PY'
import re,sys
repository,image=sys.argv[1:]
label=r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?'
host=label+r'(?:\.'+label+r')*'
component=r'[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*'
match=re.fullmatch(r'('+host+r'(?::[0-9]{1,5})?)/('+component+r'(?:/'+component+r')*)',repository)
if not match or (image and not re.fullmatch(re.escape(repository)+r'(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}|@sha256:[0-9a-f]{64})',image)):
    raise SystemExit('Invalid registry repository/image auth prerequisite')
authority=match[1]
if '.' not in authority and ':' not in authority and authority != 'localhost':
    raise SystemExit('Registry requires an explicit authority')
if ':' in authority and not 1 <= int(authority.rsplit(':',1)[1]) <= 65535:
    raise SystemExit('Invalid registry authority')
print(authority)
PY
}

login_job_registry() {
  set +x
  local registry=$1 user=$2 password=$3
  owned_staging_docker_config || return 1
  [[ -f "$CI_DOCKER_CONFIG/config.json" && ! -L "$CI_DOCKER_CONFIG/config.json" ]] || {
    echo "Invalid job registry auth file" >&2; return 1;
  }
  # A credential helper would write outside the ephemeral job config.
  if ! python3 - "$CI_DOCKER_CONFIG/config.json" <<'PY'
import json,sys
try:
    with open(sys.argv[1]) as source:
        data=json.load(source)
    valid=(isinstance(data,dict) and not data.get('credsStore')
           and isinstance(data.get('credHelpers',{}),dict)
           and not any(data.get('credHelpers',{}).values())
           and isinstance(data.get('auths'),dict) and bool(data['auths']))
except (OSError,ValueError,TypeError):
    valid=False
if not valid:
    raise SystemExit('Invalid job registry auth store')
PY
  then
    return 1
  fi
  if ! printf '%s\n' "$password" | sudo -n docker --config "$CI_DOCKER_CONFIG" \
      login "$registry" --username "$user" --password-stdin >/dev/null 2>&1; then
    echo "Job registry login failed" >&2; return 1
  fi
  # Docker login atomically replaces config.json as root. Raw Docker consumers also
  # need to read it; restore only this verified-owned file to the runner, mode 600.
  if [[ ! -f "$CI_DOCKER_CONFIG/config.json" || -L "$CI_DOCKER_CONFIG/config.json" ]] ||
     ! sudo -n chown "$(id -u):$(id -g)" "$CI_DOCKER_CONFIG/config.json" >/dev/null 2>&1 ||
     ! chmod 600 "$CI_DOCKER_CONFIG/config.json"; then
    echo "Job registry config ownership failed" >&2; return 1
  fi
}

prepare_staging_docker_auth() {
  set +x
  local role=${1:?Specify build or engine} user password image registry
  case "$role" in
    build)
      CI_DOCKER_AUTH_SCOPE=build
      user=${NEXUS_STAGING_BUILD_USER:-}
      password=${NEXUS_STAGING_BUILD_PASSWORD:-}
      image=${REPLAY_IMAGE:-${STAGING_IMAGE:-}}
      ;;
    engine)
      CI_DOCKER_AUTH_SCOPE="engine-${DOC_ENGINE:?}"
      user=${NEXUS_STAGING_READ_USER:-}
      password=${NEXUS_STAGING_READ_PASSWORD:-}
      image=${RAGFLOW_IMAGE:-}
      ;;
    *) echo "Unsupported staging Docker auth role" >&2; return 1 ;;
  esac
  unset NEXUS_STAGING_BUILD_USER NEXUS_STAGING_BUILD_PASSWORD NEXUS_STAGING_READ_USER NEXUS_STAGING_READ_PASSWORD
  unset DOCKER_AUTH_CONFIG
  [[ -n "$user" && -n "$password" ]] || {
    echo "Missing staging Docker auth prerequisite" >&2; return 1;
  }
  [[ -n "$image" ]] || { echo "Missing staging image auth prerequisite" >&2; return 1; }
  registry=$(job_registry_authority "${STAGING_IMAGE_REPOSITORY:-}" "$image") || return 1
  staging_auth_identity || return 1
  [[ -z "${CI_DOCKER_CONFIG:-}" ]] || {
    echo "Staging Docker auth already initialized" >&2; return 1;
  }
  CI_DOCKER_CONFIG=$(mktemp -d "$STAGING_AUTH_ROOT/${STAGING_AUTH_PREFIX}XXXXXX")
  export CI_DOCKER_CONFIG CI_DOCKER_AUTH_SCOPE DOCKER_CONFIG="$CI_DOCKER_CONFIG"
  # Publish ownership before login so the workflow's always cleanup also handles cancellation.
  if ! {
    chmod 700 "$CI_DOCKER_CONFIG" &&
    printf '%s\n' "$STAGING_AUTH_OWNER" > "$CI_DOCKER_CONFIG/.ragflow-owner" &&
    chmod 600 "$CI_DOCKER_CONFIG/.ragflow-owner" &&
    # Nonempty auths prevents Docker from auto-detecting a persistent credential store.
    printf '{"auths":{"%s":{}}}\n' "$registry" > "$CI_DOCKER_CONFIG/config.json" &&
    chmod 600 "$CI_DOCKER_CONFIG/config.json" &&
    printf 'CI_DOCKER_CONFIG=%s\nDOCKER_CONFIG=%s\nCI_DOCKER_AUTH_SCOPE=%s\nDOCKER_AUTH_CONFIG=\n' \
      "$CI_DOCKER_CONFIG" "$CI_DOCKER_CONFIG" "$CI_DOCKER_AUTH_SCOPE" >> "${GITHUB_ENV:?}"
  }; then
    # This path was just allocated by mktemp, before any credential was written.
    [[ "$CI_DOCKER_CONFIG" == "$STAGING_AUTH_ROOT/$STAGING_AUTH_PREFIX"* &&
       ! -L "$CI_DOCKER_CONFIG" ]] && rm -rf -- "$CI_DOCKER_CONFIG"
    return 1
  fi
  if ! login_job_registry "$registry" "$user" "$password"; then
    unset password user
    cleanup_staging_docker_auth
    echo "Staging Docker login failed" >&2
    return 1
  fi
  unset password user
}

authenticate_previous_release_read() {
  set +x
  local user=${NEXUS_RELEASE_READ_USER:-} password=${NEXUS_RELEASE_READ_PASSWORD:-} registry staging
  unset NEXUS_RELEASE_READ_USER NEXUS_RELEASE_READ_PASSWORD
  unset DOCKER_AUTH_CONFIG
  [[ -n "$user" && -n "$password" && "${CI_DOCKER_AUTH_SCOPE:-}" == build ]] || {
    echo "Missing previous-release read auth prerequisite" >&2; return 1;
  }
  owned_staging_docker_config || return 1
  staging=$(job_registry_authority "${STAGING_IMAGE_REPOSITORY:-}") || return 1
  registry=$(job_registry_authority "${RELEASE_IMAGE_REPOSITORY:-}") || return 1
  [[ "$registry" != "$staging" ]] || {
    echo "Previous-release registry auth requires a separate authority" >&2; return 1;
  }
  login_job_registry "$registry" "$user" "$password"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  case "${1:-}" in
    prepare) prepare_staging_docker_auth "${2:?Specify build or engine}" ;;
    previous-read) authenticate_previous_release_read ;;
    cleanup) cleanup_staging_docker_auth ;;
    *) echo "Usage: staging-docker-auth.sh prepare build|engine OR previous-read OR cleanup" >&2; exit 1 ;;
  esac
fi
