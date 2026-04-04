#!/usr/bin/env bash
set -u

echo "CHECK_ID=accounts"
echo "CHECK_NAME=linux account baseline"
echo

echo "[UID_ZERO_ACCOUNTS]"
uid_zero_accounts="$(awk -F: '($3 == 0) {print $1}' /etc/passwd 2>/dev/null || true)"
echo "$uid_zero_accounts"
echo

echo "[EMPTY_PASSWORD_ACCOUNTS]"
if [ -r /etc/shadow ]; then
  empty_password_accounts="$(awk -F: '($2 == \"\" || $2 == \"!\" || $2 == \"*\") {print $1}' /etc/shadow 2>/dev/null || true)"
  echo "$empty_password_accounts"
else
  echo "UNAVAILABLE:/etc/shadow not readable"
fi
echo

echo "[SUSPICIOUS_TEST_ACCOUNTS]"
awk -F: '{print $1}' /etc/passwd 2>/dev/null | grep -Ei 'test|guest|demo|temp' || echo "NONE"
echo

extra_uid_zero="$(echo "$uid_zero_accounts" | grep -v '^root$' | grep -v '^$' || true)"

if [ -n "$extra_uid_zero" ]; then
  echo "RESULT: FAIL"
  echo "REASON: extra uid=0 account detected"
else
  echo "RESULT: PASS_OR_REVIEW"
  echo "REASON: no extra uid=0 account detected"
fi
