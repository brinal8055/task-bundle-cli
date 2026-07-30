from pathlib import Path
from typing import Any

from tests.bundle_helpers import write_task


def create_synthetic_validation_bundle(
    root: Path,
    *,
    base_image: str,
    platform: str,
) -> tuple[Path, Path]:
    source = root / "source"
    bundle = root / "bundle"
    source.mkdir(parents=True)
    for directory in (
        "public",
        "evaluation/hidden",
        "candidates",
    ):
        (bundle / directory).mkdir(parents=True, exist_ok=True)
    (source / "go.mod").write_text("module example.com/calculator\n\ngo 1.22\n")
    (source / "calculator.go").write_text(
        "package calculator\n\n"
        "func Add(a, b int) int { return a - b }\n"
        "func Subtract(a, b int) int { return a - b }\n",
    )
    (source / "calculator_test.go").write_text(
        "package calculator\n\n"
        'import "testing"\n\n'
        "func TestSubtract(t *testing.T) {\n"
        "    if Subtract(5, 3) != 2 { t.Fatal(\"subtract\") }\n"
        "}\n",
    )
    (bundle / "public/description.md").write_text("Correct calculator addition.\n")
    (bundle / "evaluation/hidden/test.patch").write_text(_hidden_test_patch())
    (bundle / "evaluation/hidden/golden.patch").write_text(_golden_patch())
    (bundle / "candidates/golden.patch").write_text(_golden_patch())
    (bundle / "candidates/partial.patch").write_text(_partial_patch())
    (bundle / "candidates/regression.patch").write_text(_regression_patch())
    (bundle / "candidates/malformed.patch").write_text("not a Git patch\n")
    (bundle / "candidates/hidden-conflict.patch").write_text(
        _hidden_conflict_patch()
    )
    solver_context = root / "command-solver"
    solver_context.mkdir()
    solver = solver_context / "solve.sh"
    solver.write_text(_command_solver())
    solver.chmod(0o755)
    isolation_context = root / "hidden-isolation-solver"
    isolation_context.mkdir()
    isolation = isolation_context / "solve.sh"
    isolation.write_text(_hidden_isolation_solver())
    isolation.chmod(0o755)
    prepare = bundle / "evaluation/prepare.sh"
    prepare.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "mkdir -p /workspace/task-bundle-home /workspace/task-bundle-go-cache "
        "/workspace/task-bundle-go-tmp\n",
    )
    prepare.chmod(0o755)
    (bundle / "evaluation/adapter.go").write_text(_go_adapter())
    mapping: dict[str, Any] = {
        "schema_version": "1",
        "task": {"id": "synthetic-go-calculator", "title": "Go calculator"},
        "repository": {
            "url": "https://example.invalid/calculator.git",
            "commit": "a" * 40,
            "submodules": False,
        },
        "public": {"description": "public/description.md"},
        "environment": {
            "type": "base_image",
            "image": base_image,
            "platform": platform,
            "build": {"network": False},
            "runtime": {
                "working_directory": "/workspace/repo",
                "user": "1000:1000",
                "network": "none",
                "timeout_seconds": 300,
                "cpus": 2,
                "memory_mb": 2048,
                "pids_limit": 128,
                "read_only_root": True,
                "tmpfs": ["/tmp:size=512m"],
            },
        },
        "evaluation": {
            "test_patch": "evaluation/hidden/test.patch",
            "golden_patch": "evaluation/hidden/golden.patch",
            "prepare": {
                "command": ["/evaluation/harness/prepare.sh"],
                "network": False,
            },
            "runner": {
                "build_plan": [
                    "/usr/bin/env",
                    "HOME=/workspace",
                    "GOCACHE=/workspace/.trusted-go-cache",
                    "TMPDIR=/workspace",
                    "go",
                    "run",
                    "/evaluation/harness/adapter.go",
                    "build-plan",
                ],
                "parse_result": [
                    "go",
                    "run",
                    "/evaluation/harness/adapter.go",
                    "parse-result",
                ],
                "adapter_contract_version": "2",
                "result_schema_version": "1",
            },
            "pass_to_pass": [{"selector": "TestSubtract"}],
            "fail_to_pass": [
                {"selector": "TestAddPositive", "baseline_statuses": ["failed"]},
                {"selector": "TestAddNegative", "baseline_statuses": ["failed"]},
            ],
            "repeat": 2,
        },
    }
    write_task(bundle, mapping)
    return bundle, source


def _hidden_test_patch() -> str:
    return """\
diff --git a/calculator_hidden_test.go b/calculator_hidden_test.go
new file mode 100644
--- /dev/null
+++ b/calculator_hidden_test.go
@@ -0,0 +1,12 @@
+package calculator
+
+import "testing"
+
+func TestAddPositive(t *testing.T) {
+    if Add(2, 3) != 5 { t.Fatal("positive") }
+}
+
+func TestAddNegative(t *testing.T) {
+    if Add(-2, -3) != -5 { t.Fatal("negative") }
+}
+
"""


def _golden_patch() -> str:
    return """\
diff --git a/calculator.go b/calculator.go
--- a/calculator.go
+++ b/calculator.go
@@ -3,2 +3,2 @@
-func Add(a, b int) int { return a - b }
+func Add(a, b int) int { return a + b }
 func Subtract(a, b int) int { return a - b }
"""


def _partial_patch() -> str:
    return """\
diff --git a/calculator.go b/calculator.go
--- a/calculator.go
+++ b/calculator.go
@@ -3,2 +3,5 @@
-func Add(a, b int) int { return a - b }
+func Add(a, b int) int {
+    if a < 0 || b < 0 { return a - b }
+    return a + b
+}
 func Subtract(a, b int) int { return a - b }
"""


def _regression_patch() -> str:
    return """\
diff --git a/calculator.go b/calculator.go
--- a/calculator.go
+++ b/calculator.go
@@ -3,2 +3,2 @@
-func Add(a, b int) int { return a - b }
-func Subtract(a, b int) int { return a - b }
+func Add(a, b int) int { return a + b }
+func Subtract(a, b int) int { return a + b }
"""


def _hidden_conflict_patch() -> str:
    return """\
diff --git a/calculator_hidden_test.go b/calculator_hidden_test.go
new file mode 100644
--- /dev/null
+++ b/calculator_hidden_test.go
@@ -0,0 +1 @@
+package calculator
"""


def _command_solver() -> str:
    return """\
#!/bin/sh
set -eu
sed '0,/return a - b }/s//return a + b }/' calculator.go > /tmp/calculator.go
cp /tmp/calculator.go calculator.go
"""


def _hidden_isolation_solver() -> str:
    return """\
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
sed '0,/return a - b }/s//return a + b }/' calculator.go > /tmp/calculator.go
cp /tmp/calculator.go calculator.go
"""


def _go_adapter() -> str:
    return r'''package main

import (
    "bufio"
    "bytes"
    "encoding/json"
    "fmt"
    "os"
    "regexp"
    "time"
)

type selector struct {
    Selector string `json:"selector"`
}
type plan struct {
    PassToPass []selector `json:"pass_to_pass"`
    FailToPass []selector `json:"fail_to_pass"`
    TimeoutSeconds int `json:"timeout_seconds"`
}
type executionPlanItem struct {
    ExecutionID string `json:"execution_id"`
    RequestedSelectors []string `json:"requested_selectors"`
    Argv []string `json:"argv"`
    TimeoutSeconds int `json:"timeout_seconds"`
}
type executionPlan struct {
    SchemaVersion string `json:"schema_version"`
    Executions []executionPlanItem `json:"executions"`
}
type capturedExecution struct {
    ExecutionID string `json:"execution_id"`
    RequestedSelectors []string `json:"requested_selectors"`
    Argv []string `json:"argv"`
    StartedAt string `json:"started_at"`
    FinishedAt string `json:"finished_at"`
    DurationMS int `json:"duration_ms"`
    ExitCode *int `json:"exit_code"`
    TimedOut bool `json:"timed_out"`
    Stdout string `json:"stdout"`
    Stderr string `json:"stderr"`
    StdoutTruncated bool `json:"stdout_truncated"`
    StderrTruncated bool `json:"stderr_truncated"`
}
type capturedSet struct {
    Executions []capturedExecution `json:"executions"`
}
type event struct {
    Action string `json:"Action"`
    Test string `json:"Test"`
    Elapsed float64 `json:"Elapsed"`
}
type testResult struct {
    RequestedSelector string `json:"requested_selector"`
    ObservedID string `json:"observed_id"`
    Status string `json:"status"`
    DurationMS int `json:"duration_ms"`
}
type result struct {
    SchemaVersion string `json:"schema_version"`
    Framework string `json:"framework"`
    HarnessStatus string `json:"harness_status"`
    CollectionSucceeded bool `json:"collection_succeeded"`
    ExecutionStarted bool `json:"execution_started"`
    Command []string `json:"command"`
    StartedAt string `json:"started_at"`
    FinishedAt string `json:"finished_at"`
    ExitCode int `json:"exit_code"`
    Tests []testResult `json:"tests"`
}

func requestedSelectors(value plan) []selector {
    return append(value.PassToPass, value.FailToPass...)
}

func buildPlan() error {
    planBytes, err := os.ReadFile("/evaluation/input/plan.json")
    if err != nil { panic(err) }
    var requested plan
    if err := json.Unmarshal(planBytes, &requested); err != nil { return err }
    executions := []executionPlanItem{}
    for index, wanted := range requestedSelectors(requested) {
        executions = append(executions, executionPlanItem{
            ExecutionID: fmt.Sprintf("selector-%03d", index + 1),
            RequestedSelectors: []string{wanted.Selector},
            Argv: []string{
                "/usr/bin/env",
                "HOME=/workspace/task-bundle-home",
                "GOCACHE=/workspace/task-bundle-go-cache",
                "GOTMPDIR=/workspace/task-bundle-go-tmp",
                "go", "test", "-json", "-run",
                "^" + regexp.QuoteMeta(wanted.Selector) + "$", ".",
            },
            TimeoutSeconds: requested.TimeoutSeconds,
        })
    }
    return json.NewEncoder(os.Stdout).Encode(executionPlan{
        SchemaVersion: "2",
        Executions: executions,
    })
}

func parseResult() error {
    payload, err := os.ReadFile("/evaluation/trusted/executions.json")
    if err != nil { return err }
    var captured capturedSet
    if err := json.Unmarshal(payload, &captured); err != nil { return err }
    tests := []testResult{}
    collectionSucceeded := true
    maximumExit := 0
    for _, execution := range captured.Executions {
        if len(execution.RequestedSelectors) != 1 {
            return fmt.Errorf("synthetic adapter requires one selector per execution")
        }
        requestedSelector := execution.RequestedSelectors[0]
        status := "error"
        elapsed := execution.DurationMS
        observedID := requestedSelector
        if execution.StdoutTruncated || execution.StderrTruncated {
            return fmt.Errorf("captured test output was truncated")
        }
        if execution.TimedOut {
            status = "timeout"
        } else {
            observed := map[string]event{}
            scanner := bufio.NewScanner(bytes.NewReader([]byte(execution.Stdout)))
            for scanner.Scan() {
                var item event
                if json.Unmarshal(scanner.Bytes(), &item) == nil && item.Test != "" {
                    if item.Action == "pass" || item.Action == "fail" ||
                        item.Action == "skip" {
                        observed[item.Test] = item
                    }
                }
            }
            if err := scanner.Err(); err != nil { return err }
            item, ok := observed[requestedSelector]
            if !ok {
                status = "missing"
                collectionSucceeded = false
                observedID = ""
            } else {
                statuses := map[string]string{
                    "pass": "passed", "fail": "failed", "skip": "skipped",
                }
                status = statuses[item.Action]
                elapsed = int(item.Elapsed * 1000)
            }
        }
        if execution.ExitCode != nil && *execution.ExitCode > maximumExit {
            maximumExit = *execution.ExitCode
        }
        if execution.ExitCode != nil && *execution.ExitCode > 1 {
            collectionSucceeded = false
        }
        tests = append(tests, testResult{
            RequestedSelector: requestedSelector,
            ObservedID: observedID,
            Status: status,
            DurationMS: elapsed,
        })
    }
    started := time.Now().UTC().Format(time.RFC3339Nano)
    finished := started
    if len(captured.Executions) > 0 {
        started = captured.Executions[0].StartedAt
        finished = captured.Executions[len(captured.Executions)-1].FinishedAt
    }
    output := result{
        SchemaVersion: "1",
        Framework: "go-test-json",
        HarnessStatus: "completed",
        CollectionSucceeded: collectionSucceeded,
        ExecutionStarted: len(captured.Executions) > 0,
        Command: []string{"go", "test", "-json", "-run", "<selector>", "."},
        StartedAt: started,
        FinishedAt: finished,
        ExitCode: maximumExit,
        Tests: tests,
    }
    if !collectionSucceeded {
        output.HarnessStatus = "collection_failed"
    }
    return json.NewEncoder(os.Stdout).Encode(output)
}

func main() {
    var err error
    if len(os.Args) != 2 {
        err = fmt.Errorf("usage: adapter build-plan|parse-result")
    } else if os.Args[1] == "build-plan" {
        err = buildPlan()
    } else if os.Args[1] == "parse-result" {
        err = parseResult()
    } else {
        err = fmt.Errorf("unknown adapter mode")
    }
    if err != nil {
        fmt.Fprintln(os.Stderr, err)
        os.Exit(2)
    }
}
'''
