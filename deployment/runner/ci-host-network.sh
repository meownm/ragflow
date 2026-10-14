#!/usr/bin/env bash
# Hypervisor administrator policy; never execute this from a CI job.
set -euo pipefail
[[ ${EUID} -eq 0 && ${CI_HYPERVISOR_CONFIRMED:-} == yes ]] || {
  echo 'Run as the confirmed hypervisor administrator.' >&2; exit 1;
}
if systemd-detect-virt --vm >/dev/null || systemd-detect-virt --container >/dev/null; then
  echo 'This policy belongs to the physical management host.' >&2; exit 1
fi
: "${TEST_BRIDGE:?}" "${RELEASE_BRIDGE:?}" "${REGISTRY_IPV4:?}" "${MODEL_IPV4:?}"
[[ "$TEST_BRIDGE" =~ ^[a-zA-Z0-9_-]{1,15}$ && "$RELEASE_BRIDGE" =~ ^[a-zA-Z0-9_-]{1,15}$ && "$TEST_BRIDGE" != "$RELEASE_BRIDGE" ]]
python3 - "$REGISTRY_IPV4" "$MODEL_IPV4" <<'PY'
import ipaddress, sys
for value in sys.argv[1:]:
    assert str(ipaddress.IPv4Address(value)) == value
PY

policy=$(mktemp)
trap 'rm -f -- "$policy"' EXIT
{
  printf '*filter\n'
  for chain in RF_CI_HOST RF_CI_TEST RF_CI_PUBLISH; do
    printf ':%s - [0:0]\n-F %s\n' "$chain" "$chain"
    printf '%s\n' "-A $chain -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT"
  done
  printf '%s\n' '-A RF_CI_HOST -p udp -m multiport --dports 53,67 -j ACCEPT' \
    '-A RF_CI_HOST -p tcp --dport 53 -j ACCEPT' '-A RF_CI_HOST -j DROP'
  printf '%s\n' "-A RF_CI_TEST -p tcp -d $MODEL_IPV4 --dport 11435 -j ACCEPT" \
    "-A RF_CI_TEST -p tcp -m conntrack --ctorigdst $REGISTRY_IPV4 --ctorigdstport 5443 -j ACCEPT"
  for chain in RF_CI_TEST RF_CI_PUBLISH; do
    for port in 8444 8445; do
      printf '%s\n' "-A $chain -p tcp -m conntrack --ctorigdst $REGISTRY_IPV4 --ctorigdstport $port -j ACCEPT"
    done
    for subnet in 0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 192.168.0.0/16 198.18.0.0/15 224.0.0.0/4 240.0.0.0/4; do
      printf '%s\n' "-A $chain -d $subnet -j DROP"
    done
    printf '%s\n' "-A $chain -p tcp -m multiport --dports 80,443 -j ACCEPT" "-A $chain -j DROP"
  done
  printf 'COMMIT\n'
} > "$policy"
# Only these owned chains are replaced, in one atomic table update.
iptables-restore --wait 10 --noflush < "$policy"
iptables -w 10 -nL DOCKER-USER >/dev/null 2>&1 || iptables -w 10 -N DOCKER-USER
for entry in "$TEST_BRIDGE:RF_CI_TEST" "$RELEASE_BRIDGE:RF_CI_PUBLISH"; do
  bridge=${entry%:*}
  chain=${entry#*:}
  iptables -w 10 -C INPUT -i "$bridge" -j RF_CI_HOST 2>/dev/null || iptables -w 10 -I INPUT 1 -i "$bridge" -j RF_CI_HOST
  for parent in FORWARD DOCKER-USER; do
    iptables -w 10 -C "$parent" -i "$bridge" -j "$chain" 2>/dev/null || iptables -w 10 -I "$parent" 1 -i "$bridge" -j "$chain"
  done
  iptables -w 10 -C FORWARD -o "$bridge" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || \
    iptables -w 10 -I FORWARD 1 -o "$bridge" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
  for parent in INPUT FORWARD; do
    ip6tables -w 10 -C "$parent" -i "$bridge" -j DROP 2>/dev/null || ip6tables -w 10 -I "$parent" 1 -i "$bridge" -j DROP
  done
done
