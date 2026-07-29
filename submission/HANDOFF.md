# Private repository handoff

Repository-owner actions:

```bash
gh repo create <owner>/task-bundle-cli --private --source=. --remote=origin
git push -u origin HEAD
gh api --method PUT repos/<owner>/task-bundle-cli/collaborators/Gurvir36 \
  -f permission=push
gh api --method PUT repos/<owner>/task-bundle-cli/collaborators/tanmay-a-sharma \
  -f permission=push
gh api --method PUT repos/<owner>/task-bundle-cli/collaborators/naveenr45 \
  -f permission=push
```

Then verify each invitation in the repository Settings → Collaborators page.
These collaborators have not been claimed as added by this implementation
session.

Before publishing:

```bash
scripts/verify-submission.sh
scripts/verify-submission.sh --real
git status --short
git diff --check
```

Then confirm no `.task/`, `artifacts/`, database, Docker export, generated
context, credential, local absolute path, or temporary source tree is
committed. Review `submission/reports/real-task-command-evidence.md` and
`submission/reports/final-cleanup-audit.md`.

Do not recreate the GitHub repository, push, or send invitations until the
authenticated owner confirms `<owner>` and permission to act. Required
collaborators remain:

```text
Gurvir36
tanmay-a-sharma
naveenr45
```
