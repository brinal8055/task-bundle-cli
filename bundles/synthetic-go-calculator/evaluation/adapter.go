package main

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
	PassToPass     []selector `json:"pass_to_pass"`
	FailToPass     []selector `json:"fail_to_pass"`
	TimeoutSeconds int        `json:"timeout_seconds"`
}

type executionPlanItem struct {
	ExecutionID        string   `json:"execution_id"`
	RequestedSelectors []string `json:"requested_selectors"`
	Argv               []string `json:"argv"`
	TimeoutSeconds     int      `json:"timeout_seconds"`
}

type executionPlan struct {
	SchemaVersion string              `json:"schema_version"`
	Executions    []executionPlanItem `json:"executions"`
}

type capturedExecution struct {
	ExecutionID        string   `json:"execution_id"`
	RequestedSelectors []string `json:"requested_selectors"`
	Argv               []string `json:"argv"`
	StartedAt          string   `json:"started_at"`
	FinishedAt         string   `json:"finished_at"`
	DurationMS         int      `json:"duration_ms"`
	ExitCode           *int     `json:"exit_code"`
	TimedOut           bool     `json:"timed_out"`
	Stdout             string   `json:"stdout"`
	Stderr             string   `json:"stderr"`
	StdoutTruncated    bool     `json:"stdout_truncated"`
	StderrTruncated    bool     `json:"stderr_truncated"`
}

type capturedSet struct {
	Executions []capturedExecution `json:"executions"`
}

type event struct {
	Action  string  `json:"Action"`
	Test    string  `json:"Test"`
	Elapsed float64 `json:"Elapsed"`
}

type testResult struct {
	RequestedSelector string `json:"requested_selector"`
	ObservedID        string `json:"observed_id"`
	Status            string `json:"status"`
	DurationMS        int    `json:"duration_ms"`
}

type result struct {
	SchemaVersion       string       `json:"schema_version"`
	Framework           string       `json:"framework"`
	HarnessStatus       string       `json:"harness_status"`
	CollectionSucceeded bool         `json:"collection_succeeded"`
	ExecutionStarted    bool         `json:"execution_started"`
	Command             []string     `json:"command"`
	StartedAt           string       `json:"started_at"`
	FinishedAt          string       `json:"finished_at"`
	ExitCode            int          `json:"exit_code"`
	Tests               []testResult `json:"tests"`
}

func requestedSelectors(value plan) []selector {
	return append(value.PassToPass, value.FailToPass...)
}

func buildPlan() error {
	planBytes, err := os.ReadFile("/evaluation/input/plan.json")
	if err != nil {
		return err
	}
	var requested plan
	if err := json.Unmarshal(planBytes, &requested); err != nil {
		return err
	}
	executions := []executionPlanItem{}
	for index, wanted := range requestedSelectors(requested) {
		executions = append(executions, executionPlanItem{
			ExecutionID:        fmt.Sprintf("selector-%03d", index+1),
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
		Executions:    executions,
	})
}

func parseResult() error {
	payload, err := os.ReadFile("/evaluation/trusted/executions.json")
	if err != nil {
		return err
	}
	var captured capturedSet
	if err := json.Unmarshal(payload, &captured); err != nil {
		return err
	}
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
			if err := scanner.Err(); err != nil {
				return err
			}
			item, ok := observed[requestedSelector]
			if !ok {
				status = "missing"
				collectionSucceeded = false
				observedID = ""
			} else {
				statuses := map[string]string{
					"pass": "passed",
					"fail": "failed",
					"skip": "skipped",
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
			ObservedID:        observedID,
			Status:            status,
			DurationMS:        elapsed,
		})
	}
	started := time.Now().UTC().Format(time.RFC3339Nano)
	finished := started
	if len(captured.Executions) > 0 {
		started = captured.Executions[0].StartedAt
		finished = captured.Executions[len(captured.Executions)-1].FinishedAt
	}
	output := result{
		SchemaVersion:       "1",
		Framework:           "go-test-json",
		HarnessStatus:       "completed",
		CollectionSucceeded: collectionSucceeded,
		ExecutionStarted:    len(captured.Executions) > 0,
		Command:             []string{"go", "test", "-json", "-run", "<selector>", "."},
		StartedAt:           started,
		FinishedAt:          finished,
		ExitCode:            maximumExit,
		Tests:               tests,
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
