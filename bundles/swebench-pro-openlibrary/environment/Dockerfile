FROM jefzda/sweap-images@sha256:bc8926c47ffdb38edd165d80c776044a277cf5fd0c6fab80420d928917d4a65e

# The official immutable SWE-bench Pro image contains the commit-compatible
# global Python dependencies. The Task Bundle build contract supplies the exact
# public source independently and never supplies hidden evaluation inputs.
COPY repo/ /opt/task/repo/
RUN test -f /opt/task/repo/openlibrary/plugins/worksearch/tests/test_worksearch.py
WORKDIR /workspace/repo
