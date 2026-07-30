#!/bin/sh
set -eu
for forbidden in \
  /evaluation/input \
  /evaluation/harness \
  /task/input/test.patch \
  /task/input/golden.patch \
  /task/selectors.json
do
  test ! -e "$forbidden"
done
if env | grep -E 'HIDDEN|SELECTOR|GOLDEN' >/dev/null; then
  exit 70
fi
printf 'Hello World!\ncalculator.addition=enabled\n' > README
