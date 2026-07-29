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
    runner = bundle / "evaluation/run-tests.sh"
    runner.write_text(
        "#!/bin/sh\n"
        "set +e\n"
        "export HOME=/tmp/task-bundle-home\n"
        "export GOCACHE=/tmp/task-bundle-go-cache\n"
        "mkdir -p \"$HOME\" \"$GOCACHE\"\n"
        "go test -json ./... > /evaluation/output/go-test.json\n"
        "status=$?\n"
        "go run /evaluation/harness/parse-results.go "
        "/evaluation/input/plan.json "
        "/evaluation/output/go-test.json "
        "/evaluation/output/results.json \"$status\"\n"
        "exit \"$status\"\n",
    )
    runner.chmod(0o755)
    (bundle / "evaluation/parse-results.go").write_text(_go_parser())
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
            "runner": {
                "command": ["/evaluation/harness/run-tests.sh"],
                "result_file": "/evaluation/output/results.json",
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


def _go_parser() -> str:
    return r'''package main

import (
    "bufio"
    "encoding/json"
    "os"
    "strconv"
    "time"
)

type selector struct {
    Selector string `json:"selector"`
}
type plan struct {
    PassToPass []selector `json:"pass_to_pass"`
    FailToPass []selector `json:"fail_to_pass"`
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

func main() {
    started := time.Now().UTC()
    planBytes, err := os.ReadFile(os.Args[1])
    if err != nil { panic(err) }
    var requested plan
    if err := json.Unmarshal(planBytes, &requested); err != nil { panic(err) }
    input, err := os.Open(os.Args[2])
    if err != nil { panic(err) }
    defer input.Close()
    observed := map[string]event{}
    scanner := bufio.NewScanner(input)
    for scanner.Scan() {
        var item event
        if json.Unmarshal(scanner.Bytes(), &item) == nil && item.Test != "" {
            if item.Action == "pass" || item.Action == "fail" || item.Action == "skip" {
                observed[item.Test] = item
            }
        }
    }
    if err := scanner.Err(); err != nil { panic(err) }
    selectors := append(requested.PassToPass, requested.FailToPass...)
    tests := make([]testResult, 0, len(selectors))
    for _, wanted := range selectors {
        item, ok := observed[wanted.Selector]
        status := "missing"
        if ok {
            statuses := map[string]string{
                "pass":"passed", "fail":"failed", "skip":"skipped",
            }
            status = statuses[item.Action]
        }
        tests = append(tests, testResult{
            RequestedSelector: wanted.Selector,
            ObservedID: wanted.Selector,
            Status: status,
            DurationMS: int(item.Elapsed * 1000),
        })
    }
    exitCode, err := strconv.Atoi(os.Args[4])
    if err != nil { panic(err) }
    output := result{
        SchemaVersion: "1",
        Framework: "go-test-json",
        HarnessStatus: "completed",
        CollectionSucceeded: true,
        ExecutionStarted: true,
        Command: []string{"go", "test", "-json", "./..."},
        StartedAt: started.Format(time.RFC3339Nano),
        FinishedAt: time.Now().UTC().Format(time.RFC3339Nano),
        ExitCode: exitCode,
        Tests: tests,
    }
    destination, err := os.Create(os.Args[3])
    if err != nil { panic(err) }
    defer destination.Close()
    encoder := json.NewEncoder(destination)
    if err := encoder.Encode(output); err != nil { panic(err) }
}
'''
