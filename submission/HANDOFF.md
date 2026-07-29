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

Before publishing, rerun `scripts/verify-submission.sh`, inspect `git status`,
and confirm the ignored `.task/`/`artifacts/` demo state is absent from the
commit.
