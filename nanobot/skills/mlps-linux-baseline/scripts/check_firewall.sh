#!/usr/bin/env bash
set -u

echo "CHECK_ID=firewall"
echo "CHECK_NAME=linux firewall baseline"
echo

echo "[FIREWALLD_STATUS]"
systemctl is-active firewalld 2>/dev/null || echo "INACTIVE_OR_UNAVAILABLE"
echo

echo "[UFW_STATUS]"
ufw status 2>/dev/null || echo "UNAVAILABLE:ufw"
echo

echo "[IPTABLES_DEFAULT_POLICY]"
iptables -L 2>/dev/null | grep 'Chain INPUT' || echo "UNAVAILABLE:iptables"
echo

echo "[LISTENING_PORTS]"
ss -tulnp 2>/dev/null || netstat -tulnp 2>/dev/null || echo "UNAVAILABLE:ss/netstat"
echo

firewalld_state="$(systemctl is-active firewalld 2>/dev/null || true)"
ufw_state="$(ufw status 2>/dev/null | head -n 1 || true)"

if [ "$firewalld_state" = "active" ] || echo "$ufw_state" | grep -qi "Status: active"; then
  echo "RESULT: PASS_OR_REVIEW"
  echo "REASON: host firewall appears enabled"
else
  echo "RESULT: FAIL"
  echo "REASON: no active host firewall detected"
fi
