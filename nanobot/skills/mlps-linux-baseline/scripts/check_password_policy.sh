#!/usr/bin/env bash
set -u

echo "CHECK_ID=password_policy"
echo "CHECK_NAME=linux password policy baseline"
echo

echo "[LOGIN_DEFS]"
grep -E '^(PASS_MAX_DAYS|PASS_MIN_DAYS|PASS_WARN_AGE)' /etc/login.defs 2>/dev/null || echo "UNAVAILABLE:/etc/login.defs"
echo

echo "[PWQUALITY]"
grep -E '^(minlen|minclass|dcredit|ucredit|lcredit|ocredit)' /etc/security/pwquality.conf 2>/dev/null || echo "UNAVAILABLE:/etc/security/pwquality.conf"
echo

echo "[PAM_FAILLOCK]"
grep -R -E 'pam_faillock|pam_tally2|deny=|unlock_time=' /etc/pam.d 2>/dev/null || echo "UNAVAILABLE:pam faillock configuration"
echo

minlen_value="$(grep -E '^minlen' /etc/security/pwquality.conf 2>/dev/null | tail -n 1 | awk -F= '{gsub(/ /, \"\", $2); print $2}' || true)"
pass_max_days="$(grep -E '^PASS_MAX_DAYS' /etc/login.defs 2>/dev/null | awk '{print $2}' || true)"
deny_value="$(grep -R -Eo 'deny=[0-9]+' /etc/pam.d 2>/dev/null | head -n 1 | awk -F= '{print $2}' || true)"

echo "[PARSED_VALUES]"
echo "minlen=${minlen_value:-UNKNOWN}"
echo "pass_max_days=${pass_max_days:-UNKNOWN}"
echo "deny=${deny_value:-UNKNOWN}"
echo

if [ -n "$minlen_value" ] && [ "$minlen_value" -ge 8 ] 2>/dev/null; then
  echo "RESULT: PASS_OR_REVIEW"
  echo "REASON: minlen appears to be >= 8"
else
  echo "RESULT: FAIL"
  echo "REASON: minlen missing or < 8"
fi
