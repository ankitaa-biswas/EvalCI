# External setup after Cycle 3

Implementation and local verification are complete. The new workflow has **not been pushed or run on GitHub**. No protection settings, registry packages, releases or deployments were changed. Publishing or changing remote settings requires separate authorization.

## 1. Introduce and exercise the workflow

After reviewing and authorizing the push, push the Cycle 3 changes. GitHub Actions must be enabled under **Settings → Actions → General**, and the pinned `actions/checkout`, `setup-python`, `upload-artifact` and `download-artifact` actions must be allowed. The runner needs Docker Compose 2.24.4 or newer. The workflow uses `ubuntu-24.04`; Linux AMD64 execution remains to be verified on that runner.

The workflow evaluates `github.sha`: a branch head for push events and GitHub's merge commit for PR events. Both events run because these are different revisions; concurrency cancels obsolete runs within each branch/PR. There is no path filter that could silently skip a required check.

Baseline selection is an optional repository variable **EVALCI_BASELINE_SHA** containing a reviewed full 40-character SHA; otherwise PRs use the base SHA, pushes use the preceding SHA, new branches use the fetched default-branch SHA, and manual runs use their explicit `baseline_sha` input or parent commit. A missing Git object fails with a fetch/configuration error. An initial default-branch run needs a distinct baseline. Every CI execution bootstraps that baseline's actual RAG source with the current evaluation harness, then compares the candidate. It does not silently reuse an unrelated database run. Review any baseline pin when the intended reference changes.

## 2. Require the stable check

The read-only audit on 2026-10-29 returned `protected: false` for `main` and an empty active-rules array. See [branch metadata](https://api.github.com/repos/ankitaa-biswas/EvalCI/branches/main) and [active branch rules](https://api.github.com/repos/ankitaa-biswas/EvalCI/rules/branches/main). Local copies are in `artifacts/cycle3/github-branch-read.json` and `github-active-rules.json`. No failing-PR merge attempt was performed.

After the first authorized workflow run registers its check:

1. Open **Settings → Rules → Rulesets → New branch ruleset** (or the equivalent branch protection UI supported by the repository plan).
2. Target `main`, set enforcement to **Active**, require a pull request, and enable **Require status checks to pass**. Select the check named **Quality Gate** from the **GitHub Actions** app. Confirm its exact displayed context in the first run; do not select a similarly named unrelated check.
3. Require branches to be up to date, restrict direct updates and bypass according to the team's policy, and save. Protect workflow/gate changes through code review; do not treat editable PR code as an independent security authority.
4. Open an authorized disposable PR containing a deliberate target regression. Confirm `Quality Gate` fails and GitHub disables merging. Record its URL and check conclusion, then close the PR. Also record a healthy PR run and an evaluation-failure run. This remote proof remains pending.

A YAML job name alone does not enforce merge blocking. Organization policy and repository plan may constrain available rules; an administrator must resolve those constraints.

## 3. Choose deterministic checks or required live quality

By default the CI check exercises **synthetic contract scores**, actual target source, HTTP requests, queueing, persistence, reporting and gate decisions. It does not measure live semantic quality or attribution accuracy. LOW/MEDIUM regressions are recorded and allowed if all absolute quality thresholds pass; any HIGH, failed threshold, error, timeout, incomplete or invalid evidence fails.

For live quality, create an **evaluation** environment under **Settings → Environments**, require trusted reviewers and prevent self-review where supported. Only then enable the following repository variables under **Settings → Secrets and variables → Actions → Variables**:

| Variable | Value/purpose |
| --- | --- |
| `EVALCI_ENABLE_LIVE_EVAL` | `true` to run eligible live evaluations |
| `EVALCI_REQUIRE_LIVE_EVAL` | `true` to make a skipped live job fail `Quality Gate` |
| `EVALCI_ALLOW_SAME_REPO_LIVE_PR` | `true` only if reviewers authorize same-repository PR execution with protected provider credentials |
| `EVAL_JUDGE_MODEL`, `EVAL_EMBEDDING_MODEL` | Explicit available judge/embedding model names |
| `GEMINI_GENERATION_MODEL`, `GEMINI_EMBEDDING_MODEL` | Explicit available target model names |
| `OPENAI_BASE_URL` | Optional compatible provider URL; omit for the default |

Put **OPENAI_API_KEY** and **GOOGLE_API_KEY** in the protected **evaluation environment secrets**, rather than generally available repository secrets. Confirm model access, billing and quota. Live runs reuse both exact images from deterministic integration; they do not rebuild them. Each evaluation allows 1,800 seconds, with a worker hard limit 60 seconds later. Timeouts fail rather than authorize delivery.

By default live execution is eligible only on trusted default-branch push/manual events. Same-repository PRs need the explicit variable above and environment approval. Fork PRs never receive provider secrets; with live quality required their gate fails because live evaluation is skipped. A maintainer must review and import approved changes into a trusted same-repository PR branch to obtain the required live result. Do not use `pull_request_target` to execute fork code with secrets. See GitHub's [secure workflow guidance](https://docs.github.com/en/actions/reference/security/secure-use).

The default deterministic check must not be described as required live quality. Enable both live variables and configure an approved PR path before claiming that behavior.

## 4. Enable continuous delivery only after authorization

Create a protected **delivery** environment with reviewers and default-branch deployment restrictions. Verify the repository/organization permits the workflow's delivery job to write GHCR packages using `GITHUB_TOKEN`. If an existing package has separate access controls, grant this repository Actions access. Then, only with authorization, set **EVALCI_ENABLE_DELIVERY=true**.

Delivery runs only for default-branch **pushes**, after the aggregate gate, deterministic integration and live evaluation all succeed. It verifies report provenance, archive checksum and image IDs, loads the already-tested image, and publishes:

```text
ghcr.io/ankitaa-biswas/evalci:sha-<full-tested-candidate-sha>
```

Only this job requests `packages: write`; other jobs use `contents: read`. It has no production/staging credentials. Fixture-only evidence cannot authorize publishing. A local dry run without `--publish` only loads/retags images and never contacts a registry.

This is **continuous delivery of a versioned container image**, not continuous deployment. Publication has not been executed. No staging target was provided or authorized. Deployment would additionally need an approved target, protected credentials, network access, an explicit rollout command, a health endpoint, and a check of the running revision/image digest against the delivered artifact. Those remain external work; no successful deployment or production use is claimed.
