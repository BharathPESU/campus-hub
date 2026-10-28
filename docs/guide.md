# Campus Hub: Step-by-Step Implementation and Deployment Guide

Follow the phases **in order**. Do not start a phase until the previous one's **Done when** check passes.

- Design: `docs/plan.md`. Exact logic: `docs/pseudo_code.md` (its section 1 overrides plan.md).
- Method: you write code locally, **Terraform creates everything in AWS**, and you **verify in the AWS Console** (Phase 15).
- Region used throughout: `ap-south-1` (Mumbai). Replace `<ACCOUNT_ID>` and `<YOUR_EMAIL>` where shown.
- Commands assume Linux or macOS (or WSL on Windows).

---

## Phase 0: AWS account preparation (Console)

1. **Root MFA:** sign in as root, then account menu, then *Security credentials*, then *Assign MFA device*.
2. **Budget first:** *Billing and Cost Management*, then *Budgets*, then *Create budget*, then use the **Zero spend budget** template with your email.
3. **Free Tier alerts:** *Billing preferences*, then enable *Free Tier usage alerts* and your email.
4. **Work identity (no root, no long-lived keys):**
   - Open *IAM Identity Center*, then *Enable*.
   - Create a user, then *Permission sets*, then create or use `AdministratorAccess`, then assign it to your account.
   - Note the **AWS access portal URL**.
5. **Region:** switch the console region to **Asia Pacific (Mumbai) ap-south-1**. In the console search, confirm each service opens there: Cognito, Lambda, API Gateway, DynamoDB, SNS, SQS, SES, Step Functions, EventBridge Scheduler, CodePipeline, CodeBuild, X-Ray.
6. **Quota check:** *Service Quotas*, then *AWS Lambda*, then *Concurrent executions*. Write the value down. If it is **10**, leave `lambda_reserved_concurrency = null`.
7. **Account plan:** on the Billing home page, note whether you are on the **Free plan (credits)** or a paid plan. If Terraform later says a service is "not available on the free plan", this is why.

**Done when:** you can sign in through the access portal and the budget exists.

---

## Phase 1: Local tools and repository

1. Install and verify:
   ```bash
   terraform version      # >= 1.10
   aws --version          # v2
   python3 --version      # 3.12
   node --version         # 20+
   git --version
   jq --version
   pip install pre-commit ruff pytest
   # also install: tflint, shellcheck, shfmt, trivy, gitleaks
   ```
2. Log in from the CLI:
   ```bash
   aws configure sso            # use the portal URL, region ap-south-1, profile name: campus
   aws sso login --profile campus
   export AWS_PROFILE=campus AWS_REGION=ap-south-1
   aws sts get-caller-identity
   export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
   ```
3. Unpack and initialise:
   ```bash
   unzip campus-hub.zip && cd campus-hub
   git init -b main && git add . && git commit -m "chore: starter skeleton"
   ```
4. Create a **private GitHub repo** named `campus-hub`, then:
   ```bash
   git remote add origin git@github.com:<YOU>/campus-hub.git
   git push -u origin main
   ```
5. Fill `.pre-commit-config.yaml` (terraform fmt/validate, tflint, ruff, shellcheck, shfmt, gitleaks), then `pre-commit install`.

**Done when:** `aws sts get-caller-identity` works and the repo is on GitHub.

---

## Phase 2: Terraform state bootstrap

1. Implement `infra/bootstrap/`: one S3 bucket `campus-hub-tfstate-<ACCOUNT_ID>` with **versioning on**, SSE-S3, all four public-access blocks, and a bucket policy denying requests where `aws:SecureTransport` is false. Output the bucket name.
2. Apply (local state, once):
   ```bash
   cd infra/bootstrap
   terraform init && terraform plan -var="account_id=$ACCOUNT_ID"
   terraform apply -var="account_id=$ACCOUNT_ID"
   cd ../..
   ```
3. Edit `infra/envs/dev/backend.tf`: bucket `campus-hub-tfstate-<ACCOUNT_ID>`, key `envs/dev/terraform.tfstate`, region `ap-south-1`, `encrypt = true`, `use_lockfile = true`.
4. Fill `versions.tf` (`>= 1.10.0`, aws `~> 6.0`), `providers.tf` (`default_tags`), and `variables.tf`. Set `terraform.tfvars`:
   ```hcl
   env                = "dev"
   region             = "ap-south-1"
   sender_email       = "<YOUR_EMAIL>"
   admin_email        = "<YOUR_EMAIL>"
   email_mode         = "log"
   protect_data       = false
   force_destroy      = true
   budget_limit_usd   = 1
   ```
5. Initialise: `cd infra/envs/dev && terraform init`. Commit `.terraform.lock.hcl`.

**Done when:** `terraform plan` in `envs/dev` says no changes, and the state file appears in the bucket (Console: S3).

---

## Phase 3: Data and auth modules

1. Implement `modules/data` (pseudo_code section 6.2): one table, provisioned 5/5, key `PK`/`SK`, `GSI1` on `GSI1PK`/`GSI1SK`, projection `ALL`.
2. Implement `modules/auth`. For now pass only `http://localhost:5173/` as callback and logout URL (the CloudFront URL is added in Phase 8). Enable `ALLOW_USER_PASSWORD_AUTH` through a temporary variable `enable_test_auth = true` so you can test from the CLI. **Remove it in Phase 13.**
3. In `envs/dev/main.tf` wire only `data` and `auth`, and add their outputs.
   ```bash
   cd infra/envs/dev
   terraform plan -out=tfplan      # read it; expect only the table, pool, domain, groups, client
   terraform apply tfplan
   ```
4. Create yourself a test admin (replace values):
   ```bash
   POOL=$(terraform output -raw user_pool_id); CLIENT=$(terraform output -raw app_client_id)
   aws cognito-idp sign-up --client-id $CLIENT --username <YOUR_EMAIL> --password '<StrongPass1!>' --user-attributes Name=email,Value=<YOUR_EMAIL>
   aws cognito-idp admin-confirm-sign-up --user-pool-id $POOL --username <YOUR_EMAIL>
   aws cognito-idp admin-add-user-to-group --user-pool-id $POOL --username <YOUR_EMAIL> --group-name admin
   ```

**Done when:** the table and user pool exist in the Console, and your user is in the `admin` group (Cognito, then *Groups*).

---

## Phase 4: Backend code (local only, no AWS calls)

1. Set up the environment:
   ```bash
   cd backend && python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements-dev.txt
   ```
   Fill `requirements.txt` with pinned versions of `aws-lambda-powertools[tracer]`, `pydantic`, `python-ulid`. Do not include `boto3`.
2. Implement `src/common/` in this order, each file following its pseudo_code section: `timeutil` (3.1), `errors` (3.2), `http` (3.3), `auth` (3.4), `validation` (3.5), `keys` (3.6), `db` (3.7), `notify` and `emails` (3.8).
3. Write the unit tests for `parse_groups` and validation (`tests/unit/test_auth.py`, `test_validation.py`, pseudo_code 10.1 and 10.2), then run `pytest -q`.
4. Implement handlers in this order (pseudo_code section 4): `health`, `list_events`, `get_event`, `create_event`, `start_cancel`, `register`, `unregister`, `list_my_registrations`, `send_reminder`, `email_sender`.
5. Write `test_register.py` and `test_events_flow.py` (pseudo_code 10.3 and 10.4) using `moto`, or botocore `Stubber` where moto lacks cancellation reasons.
6. Run `ruff check . && ruff format . && pytest -q`, then commit.

**Done when:** all tests pass, including the double-registration, full-event, and claim-twice cases.

---

## Phase 5: Build scripts

1. Implement `scripts/lib.sh`, `build_backend.sh`, `build_frontend.sh`, `gen_frontend_config.sh`, `deploy_frontend.sh`, `smoke_test.sh`, `destroy.sh`, `ci_install_terraform.sh` (pseudo_code section 8).
2. Run `shellcheck scripts/*.sh && shfmt -d scripts/`.
3. Test the backend build:
   ```bash
   ./scripts/build_backend.sh
   ls build/lambda/package | head      # expect: common/ handlers/ and dependency folders
   ```
4. Implement the `Makefile` targets (plan.md section 12).

**Done when:** `build/lambda/package/handlers/register.py` exists and the build is repeatable (running it twice gives identical file contents).

---

## Phase 6: Messaging, scheduling, workflow, functions, API (infrastructure)

Implement these modules from pseudo_code section 6.2, in this order, then wire them in `envs/dev/main.tf` as in section 6.4:

1. `modules/messaging`: events SNS topic, outbox queue and DLQ, queue policy, SNS-to-SQS subscription (raw delivery), SES email identity.
2. `modules/workflows`: log group, role, state machine from `cancel.asl.json.tftpl` (pseudo_code section 5).
3. `modules/functions`: one `archive_file`, ten functions, IAM per the matrix in 6.3, the SQS event source mapping with `ReportBatchItemFailures`.
4. `modules/scheduling`: schedule group, reminders DLQ, scheduler role (named `campus-hub-dev-scheduler`).
5. `modules/api`: HTTP API, `$default` stage, JWT authorizer, eight routes with the exact `route_key`s, Lambda permissions.

Deploy:
```bash
make build
cd infra/envs/dev
terraform fmt -recursive && terraform validate && tflint
terraform plan -out=tfplan        # read every line; no "*" IAM resources except X-Ray
terraform apply tfplan
terraform plan                    # run again: must show NO changes
```
If Terraform reports a dependency cycle between `functions` and `scheduling`, follow the fallback in pseudo_code section 6.4.

**Done when:** `terraform output api_endpoint` prints a URL and the second plan shows no changes.

---

## Phase 7: Test the API directly

1. Health (no token), using the raw API endpoint (CloudFront comes next phase):
   ```bash
   API=$(terraform output -raw api_endpoint)
   curl -s $API/api/health
   curl -s -o /dev/null -w '%{http_code}\n' -X POST $API/api/events      # expect 401
   ```
2. Get an ID token with the test user from Phase 3 (sign in **after** adding to the `admin` group):
   ```bash
   TOKEN=$(aws cognito-idp initiate-auth --client-id $CLIENT --auth-flow USER_PASSWORD_AUTH \
     --auth-parameters USERNAME=<YOUR_EMAIL>,PASSWORD='<StrongPass1!>' --query AuthenticationResult.IdToken --output text)
   ```
3. Create an event for **three days ahead** (use a future UTC time):
   ```bash
   curl -s -X POST $API/api/events -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
     -d '{"title":"Hack Night","location":"Lab 2","startsAt":"2026-12-05T10:00:00Z","capacity":1}'
   ```
   Copy the returned `eventId` into `EID`.
4. Walk through the registration rules:
   ```bash
   curl -s -X POST $API/api/events/$EID/registrations -H "Authorization: Bearer $TOKEN"   # 201
   curl -s -X POST $API/api/events/$EID/registrations -H "Authorization: Bearer $TOKEN"   # 409 ALREADY_REGISTERED
   curl -s $API/api/me/registrations -H "Authorization: Bearer $TOKEN"
   curl -s -X DELETE $API/api/events/$EID/registrations/me -H "Authorization: Bearer $TOKEN"   # 204
   curl -s $API/api/events
   ```
5. For a 409 `EVENT_FULL`, create a second user the same way as Phase 3 (without the admin group), register both on a capacity-1 event, and check the second one is rejected.
6. Console check: DynamoDB, then *Explore items*, to see `EVENT#...` and `USER#...` rows; Lambda, then *Monitor*, to see invocations.

**Done when:** every response matches the expected status above.

---

## Phase 8: Frontend and CloudFront

1. Implement `modules/frontend` (pseudo_code 6.2): private S3 bucket, OAC, CloudFront with the `/api/*` behavior, bucket policy. **No custom error responses.**
2. Update `modules/auth` inputs in `main.tf`: callback and logout URLs now include `https://${module.frontend.domain_name}/` plus `http://localhost:5173/`.
3. Apply:
   ```bash
   cd infra/envs/dev && terraform plan -out=tfplan && terraform apply tfplan     # CloudFront takes several minutes
   ```
4. Build the React app (pseudo_code section 7): `main.tsx`, `auth.ts`, `api.ts`, `App.tsx`, four pages. Install dependencies: `react`, `react-dom`, `react-router-dom`, `oidc-client-ts`, `vite`, `typescript`.
5. Deploy:
   ```bash
   make build
   ./scripts/gen_frontend_config.sh dev
   ./scripts/deploy_frontend.sh dev
   ./scripts/smoke_test.sh dev
   terraform -chdir=infra/envs/dev output -raw site_url
   ```
6. Open the site URL. Sign up through the hosted login page (the verification code arrives from Cognito's default email sender). Re-add your account to the `admin` group if you used a new email, then sign out and in.

**Done when:** you can sign in, see events, register and unregister, and the browser console shows no CORS errors.

---

## Phase 9: Real emails with SES

1. Set `email_mode = "ses"` in `terraform.tfvars` and apply.
2. **Console:** *Amazon SES* in `ap-south-1`, then *Identities*. Open your sender email's verification message and click the link. The status must become *Verified*.
3. SES starts in **sandbox**: every recipient must also be a verified identity. Add and verify each test recipient (your teammates) under *Identities*.
4. Register a verified recipient for an event. A confirmation email should arrive within a minute.
5. If nothing arrives: check the `email_sender` log group in CloudWatch, then the outbox DLQ message count in SQS.

**Done when:** one confirmation email is delivered. If you cannot get out of the sandbox, set `email_mode = "log"` and continue; the SNS to SQS to Lambda path is still demonstrated.

---

## Phase 10: Test reminders

1. Temporarily set `REMINDER_LEAD_HOURS` to `0` in `create_event`'s environment and apply (the variable name stays the same).
2. Create an event starting about **10 minutes from now**, register a verified user.
3. **Console:** *EventBridge*, then *Scheduler*, then *Schedules*, group `campus-hub-dev-reminders`: the schedule `reminder-<id>` is listed.
4. After the start time passes, the schedule disappears (`ActionAfterCompletion = DELETE`), and the reminder email (or log line) appears.
5. Restore `REMINDER_LEAD_HOURS = 24` and apply. Confirm a second plan shows no changes.

**Done when:** one reminder fires once, and a start time less than 24 hours away creates an event with `reminderStatus = SKIPPED` and no schedule.

---

## Phase 11: Test event cancellation

1. Create an event, register two users, then call `POST /api/events/{id}/cancel` as admin (or use the admin page).
2. **Console:** *Step Functions*, then *State machines*, then the cancel machine, then the latest execution. All states should be green.
3. Verify: the event status is `CANCELLED` in DynamoDB, its schedule is gone, and both users received (or logged) a cancellation message.
4. Cancel the same event again: it returns 202 and starts no new execution.

**Done when:** one clean execution and an idempotent second call.

---

## Phase 12: Observability

1. Implement `modules/observability` (pseudo_code 6.2): ops topic, eight alarms, dashboard, pipeline-failure rule, budget. Apply.
2. **Confirm the SNS subscription email** sent to `admin_email` (click *Confirm subscription*).
3. Prove alarms reach you:
   ```bash
   aws cloudwatch set-alarm-state --alarm-name campus-hub-dev-api-5xx --state-value ALARM --state-reason "test"
   ```
   An email should arrive within a minute.
4. **Console:** *CloudWatch*, then *Dashboards*, to open the dashboard. *CloudWatch*, then *X-Ray traces*, then *Traces*: after a few API calls you should see traces from the Lambda functions.

**Done when:** a test alarm email arrives and a trace opens.

---

## Phase 13: Hardening

1. Remove the temporary `enable_test_auth` (`ALLOW_USER_PASSWORD_AUTH`) from `modules/auth` and apply.
2. Walk through plan.md section 14 and tick every box.
3. Run `trivy config infra/` and fix or document every finding.
4. IAM Access Analyzer: *IAM*, then *Access Analyzer*, then create an analyzer (free) and review findings.
5. Set Lambda `reserved_concurrency` only if the Phase 0 quota allows it.
6. Confirm the budget alert email subscription.

**Done when:** the security checklist is complete and `make check` is green.

---

## Phase 14: CI/CD pipeline

1. Implement `infra/pipeline/`: artifact bucket, CodeConnections connection to GitHub, IAM roles, two CodeBuild projects (`BUILD_GENERAL1_SMALL`), one CodePipeline with stages Source, Build (plan), Approve, Deploy (apply). Write `buildspec.plan.yml` and `buildspec.apply.yml` (pseudo_code section 9).
2. Apply it locally once:
   ```bash
   cd infra/pipeline && terraform init && terraform plan -out=tfplan && terraform apply tfplan
   ```
3. **Console, one-time:** *Developer Tools*, then *Settings*, then *Connections*. Open the connection showing **Pending**, click *Update pending connection*, and authorise GitHub.
4. Commit and push to `main`:
   ```bash
   git add . && git commit -m "feat: ci/cd pipeline" && git push
   ```
5. **Console:** *CodePipeline*, then your pipeline. After Build finishes, open the plan log, then click *Review*, then *Approve*. Watch the Deploy stage.
6. If Terraform is denied an action, read the CodeBuild log, add that **specific** permission to the CodeBuild role, and re-run. Do not use `AdministratorAccess`.
7. Prove failure alerts: push a commit with a deliberate Terraform error, confirm Build fails and an alert email arrives, then revert.

**Done when:** a push to `main` ends in a deployed site passing the smoke test with no manual commands.

---

## Phase 15: Verify everything in the AWS Console

Open each page in `ap-south-1` (CloudFront and IAM are global).

| Service | Console location | You should see |
|---|---|---|
| Cognito | *User pools* | Pool, app client (no secret), groups `admin` and `student` |
| API Gateway | *APIs* | HTTP API, `$default` stage, JWT authorizer, 8 routes |
| Lambda | *Functions* | 10 functions, runtime Python 3.12, arm64, active tracing on |
| DynamoDB | *Tables* | One table, provisioned 5/5, index `GSI1` |
| S3 | *Buckets* | Site bucket (public access blocked) and state bucket |
| CloudFront | *Distributions* | Two origins, `/api/*` behavior, HTTPS redirect |
| EventBridge | *Scheduler*, *Rules* | Schedule group; pipeline-failure rule |
| SNS / SQS | *Topics*, *Queues* | Events and ops topics; outbox and two DLQs (all empty) |
| SES | *Identities* | Sender verified |
| Step Functions | *State machines* | Cancel machine with succeeded executions |
| CloudWatch | *Alarms*, *Dashboards*, *Log groups* | 8 alarms in OK; every log group has 14-day retention |
| X-Ray | *Traces* | Recent Lambda traces |
| CodePipeline / CodeBuild | *Pipelines*, *Build projects* | Latest run succeeded |
| IAM | *Roles* | One role per Lambda; no `*` resource except X-Ray |
| Billing | *Free Tier*, *Budgets* | No service near its limit; budget active |

**Done when:** each row matches. Then run `./scripts/smoke_test.sh dev` one last time.

---

## Phase 16: Teardown (when finished, to stop any cost)

1. In `terraform.tfvars` confirm `protect_data = false` and `force_destroy = true`, and apply.
2. `make destroy ENV=dev` (type `dev` to confirm).
3. `cd infra/pipeline && terraform destroy`, then delete the GitHub connection if it remains.
4. Empty all versions in the state bucket (Console: S3, then *Show versions*, then delete), then `cd infra/bootstrap && terraform destroy`.
5. Check in the Console that no schedules, log groups, or CloudFront distributions remain. Re-check Billing the next day.

---

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `401` on every authenticated call | Wrong token type (use the **ID** token), or authorizer audience is not the app client ID |
| Login page says `redirect_mismatch` | Callback URL in Cognito differs from the page URL (check trailing slash and `https://`) |
| `403` from CloudFront for the site | Bucket policy or OAC missing, or the distribution is still deploying |
| API calls from the browser return `index.html` | A `custom_error_response` exists on the distribution: remove it |
| `AccessDenied ... iam:PassRole` on create event | `create_event` role needs `iam:PassRole` for the scheduler role with `PassedToService` condition |
| `AccessDeniedException ... TransactWriteItems` | Grant `PutItem`/`UpdateItem`/`DeleteItem`, not a transaction action |
| `ValidationException ... reserved keyword` | An attribute (for example `status`) is not aliased with `ExpressionAttributeNames` |
| Reserved concurrency error on apply | Account quota too low: set `lambda_reserved_concurrency = null` |
| No SES email | Sender or recipient not verified (sandbox), or `email_mode` still `log` |
| Alarm email never arrives | SNS subscription not confirmed, or ops topic is encrypted with an AWS-managed key (keep it unencrypted) |
| Reminder never fires | Check the Scheduler schedule exists, the DLQ for messages, and the `send_reminder` log group |
| Every plan wants to update Lambda | Zip is not deterministic: rerun `build_backend.sh` (it fixes file mtimes) and avoid rebuilding in between |
| `Error acquiring the state lock` | A previous run was interrupted: remove the lock object `terraform.tfstate.tflock` in the state bucket only if no run is active |
| Service "not available" on apply | Account is on the Free plan: upgrade the plan or choose another service |
| Pipeline Deploy cannot apply saved plan | Version or path mismatch: use the re-plan fallback in pseudo_code section 9.2 |
