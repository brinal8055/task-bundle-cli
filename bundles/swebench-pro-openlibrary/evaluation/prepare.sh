#!/bin/sh
set -eu
python -c 'import pytest, web; import openlibrary.plugins.worksearch.code'
