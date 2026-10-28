# Campus Event & Hackathon Hub: Implementation Plan

| | |
|---|---|
| **Goal** | Use AWS free-tier services correctly, securely and reproducibly. Features are deliberately minimal. |
| **Region** | `ap-south-1` (Mumbai). Verify service availability in Phase 0. |
| **IaC** | Terraform (S3 remote state). Shell scripts build and ship artifacts only. |
| **CI/CD** | GitHub, then CodePipeline, then CodeBuild (runs Terraform). |
| **Runtime** | Python 3.12 Lambdas (arm64), React + Vite frontend |

> Free-tier numbers in this file come from third-party guides and AWS pricing pages read while researching. Re-check anything marked **(verify)** on the official AWS page before relying on it.
>
> Companion files: `pseudo_code.md` (exact logic) and `guide.md` (step-by-step deployment). Refinements R1 to R8 from `pseudo_code.md` section 1 are folded into this plan (decisions 16 to 23).

---

## 1. Scope

### MVP features (nothing else)
1. Sign up and sign in (Cognito).
2. Browse upcoming events (public, no login).
3. Register for and cancel a registration for an event (signed in).
4. Admin: create an event, cancel an event.
5. One reminder email per registered student, 24 hours before the event.
6. Admin gets an alert when something fails.

### Non-goals
Editing events, payments, QR check-in, search, file uploads, multi-region, a second deployed environment, WAF, customer-managed KMS keys.

Each non-goal either costs money or adds failure modes without adding AWS learning value.

---

## 2. Decisions and corrections

Some earlier suggestions in the brainstorm were wrong or incomplete. This table is the source of truth.

| # | Decision | Why | Replaces |
|---|---|---|---|
| 1 | **Terraform** for all infrastructure | Plan output, transferable skill | CloudFormation/SAM |
| 2 | **HTTP API** + X-Ray tracing **from Lambda only** | HTTP APIs do not support X-Ray; only REST APIs do (verify). Lambda active tracing still gives end-to-end traces from the function onward | "Turn on tracing for the API stage" |
| 3 | **Hash routing** in the SPA (`/#/events`) | CloudFront custom error responses apply to the *whole distribution* and would replace API 403/404 responses with `index.html` | "Return /index.html for 403/404" |
| 4 | API routes **start with `/api`** | CloudFront forwards the full path to the origin and cannot strip a prefix without a function | Unprefixed routes |
| 5 | **Single DynamoDB table + one GSI**, provisioned 5/5 | The always-free allowance is 25 read and 25 write units *shared* across tables and GSIs **(verify)** | Two tables |
| 6 | Student emails go through **SES**; SNS is used for notifications fan-out and ops alerts | SNS email subscriptions need a confirmation click from each recipient, so they are not suitable for transactional email | SNS emailing students directly |
| 7 | **AWS-managed keys only** (no CMKs) | A customer-managed KMS key costs about $1/month (verify) | Implicit KMS usage |
| 8 | `deletion_protection_enabled = var.protect_data` | `lifecycle.prevent_destroy` cannot take a variable | `prevent_destroy` on tables |
| 9 | **One deployed environment** (`dev`) | A second stack doubles resources and pipeline cost. The code layout still supports adding `prod` | `dev` + `prod` |
| 10 | **GitHub via CodeConnections**, no CodeCommit | One source reports new CodeCommit sign-ups blocked since May 2026 (verify) | CodeCommit |
| 11 | Terraform `archive_file` builds the Lambda zips | Deterministic zips mean no phantom diffs on every run | Zips built by scripts |
| 12 | **Local-first deploys**, pipeline takes over in Phase 8 | CodeBuild's free allowance is about 100 minutes/month (verify). Iterating on a pipeline burns it | Pipeline from day one |
| 13 | Frontend sends the Cognito **ID token** as the bearer token | It carries `email` and `cognito:groups`; the access token has no email. Accepted trade-off, documented | Access token |
| 14 | **Step Functions** runs the event-cancellation workflow | A real multi-step job (notify all registrants, delete schedule, mark cancelled), well inside 4,000 free transitions/month (verify) | Not previously used |
| 15 | **No Cognito post-confirmation trigger** | Email comes from the token at registration time. Fewer moving parts | Trigger Lambda |
| 16 | Event `status` is `ACTIVE`, `CANCELLING` or `CANCELLED` | Makes cancel idempotent and blocks new registrations while the workflow runs | `ACTIVE`/`CANCELLED` only |
| 17 | **One shared Lambda package**; the ten functions differ only by handler | Simpler, faster build. Isolation comes from per-function IAM roles | Per-function build folders |
| 18 | `python-ulid` is a runtime dependency | Sortable event IDs | none |
| 19 | Cognito **classic hosted UI** (`managed_login_version = 1`) | Avoids a managed-login branding resource (**verify** on your pool tier) | unspecified |
| 20 | Reserved Lambda concurrency defaults to `null` | New accounts can have a very low quota, and setting it then fails | Reserved concurrency on public functions |
| 21 | No `TransactWriteItems` IAM action: grant `PutItem`/`UpdateItem`/`DeleteItem` | How DynamoDB IAM works | not mentioned |
| 22 | Alias every DynamoDB attribute with `ExpressionAttributeNames` | `status` and others are reserved words | not mentioned |
| 23 | Send email with the `sesv2` API | Current SES API | not mentioned |

---

## 3. Architecture

```
                         Students / Admin (browser)
                                   |
                              CloudFront
                 +-----------------+------------------+
          default behavior                       /api/* behavior
                 |                                     |
        S3 (private, OAC)                     API Gateway (HTTP API)
        React SPA + config.json                        |
                                          JWT authorizer (Cognito)
                                                       |
                                                    Lambda  ---> DynamoDB (single table)
                                                       |
                    +----------------------------------+------------------+
                    |                                  |                  |
        EventBridge Scheduler              Step Functions (cancel)    SNS: event-notifications
        (one-time, at start - 24h)                     |                  |
                    |                                  +----------------> |
              send_reminder Lambda ------------------------------------->|
                                                                          v
                                                                SQS: email-outbox (+ DLQ)
                                                                          |
                                                                   email_sender Lambda
                                                                          |
                                                                         SES

Observability: CloudWatch Logs/Alarms/Dashboard --> SNS: ops-alerts --> admin email
               X-Ray traces from Lambda
               EventBridge rule: CodePipeline FAILED --> ops-alerts

Delivery: GitHub --> CodePipeline --> CodeBuild (plan) --> Manual approval --> CodeBuild (apply + frontend deploy + smoke test)
```

### Key flows

**A. Register:** `POST /api/events/{id}/registrations`. The JWT is validated by API Gateway. Lambda runs one `TransactWriteItems` (increment `seatsTaken` only if seats remain and the event is active, create the registration only if it does not exist). It then publishes `registration.confirmed` to SNS. SNS delivers to SQS, `email_sender` sends via SES.

**B. Create event (admin):** Lambda checks the admin group, writes the event, and creates a one-time Scheduler schedule for `startsAt - 24h`. If that time is in the past, no schedule is created and `reminderStatus = SKIPPED`.

**C. Reminder:** Scheduler invokes `send_reminder` with `{eventId}`. Lambda claims the reminder with a conditional update, queries the registrants, and publishes one SNS message per registrant. Failures go to the Scheduler DLQ.

**D. Cancel event (admin):** Lambda checks the admin group and starts a Step Functions execution: query registrants, notify each one (Map state), delete the schedule, mark the event `CANCELLED`. The Lambda first sets `CANCELLING`, which blocks new registrations and makes repeated calls idempotent.

---

## 4. AWS services

| Service | Role in this project | Free-tier note (verify) |
|---|---|---|
| **Cognito** (user pool) | Sign-up, sign-in, `admin`/`student` groups | 10,000 MAU on Lite or Essentials. SAML/OIDC federation has a separate 50 MAU allowance |
| **API Gateway** (HTTP API) | Routing, JWT authorization, access logs | 1M calls/month for the first 12 months. New Free plan accounts may use credits instead |
| **Lambda** | All backend logic | 1M requests and 400,000 GB-seconds/month, always free |
| **DynamoDB** | Events and registrations | 25 GB plus 25 provisioned read/write units, always free |
| **S3** | Private bucket for the frontend build | Credit-based on new Free plan accounts; 5 GB on legacy accounts |
| **CloudFront** | HTTPS entry, serves S3 and proxies `/api/*` | 1 TB out and 10M requests/month, always free |
| **EventBridge Scheduler** | One-time reminder schedules | Check Scheduler's own free allowance |
| **EventBridge** (default bus) | Rule: pipeline failure notification | AWS service events are free |
| **SNS** | `event-notifications` (fan-out) and `ops-alerts` | 1M publishes/month |
| **SQS** | `email-outbox`, plus DLQs | 1M requests/month |
| **SES** | Sends student emails | Sandbox: verified recipients only. Free-tier terms differ for new accounts |
| **Step Functions** (Standard) | Cancel workflow | 4,000 state transitions/month |
| **CloudWatch** | Logs, alarms, dashboard | 10 alarms, 5 GB logs. Keep alarms at 8 or fewer |
| **X-Ray** | Lambda traces | 100K traces recorded/month |
| **IAM** (+ Access Analyzer) | Least-privilege roles, policy review | Free |
| **CodePipeline** | Orchestrates delivery | One V1 pipeline or 100 V2 minutes/month (sources disagree) |
| **CodeBuild** | Runs tests, `terraform plan/apply`, frontend deploy | 100 build minutes/month on `general1.small` |
| **Budgets** | Cost and usage alerts | 2 budgets free |
| **ACM** (optional) | Certificate for a custom domain, **must be in `us-east-1`** | Free |
| **KMS** | AWS-managed keys only | Do not create CMKs |
| **Amazon Q Developer** (optional) | IDE assistance while coding | Free tier exists |
| **CloudFormation** | Not used (replaced by Terraform) | n/a |

---

## 5. Repository layout

```
campus-hub/
├── README.md
├── AGENT_START_HERE.md
├── Makefile
├── buildspec.plan.yml
├── buildspec.apply.yml
├── .pre-commit-config.yaml
├── .editorconfig
├── .gitignore                      # *.tfstate*, .terraform/, build/, node_modules/, .env*
├── docs/
│   ├── plan.md  pseudo_code.md  guide.md
│   ├── architecture.md
│   ├── adr/                        # one short file per decision in section 2
│   └── runbook.md                  # deploy, rollback, teardown, common errors
├── infra/
│   ├── bootstrap/                  # state bucket; applied once, locally
│   │   ├── main.tf  variables.tf  outputs.tf  versions.tf
│   ├── pipeline/                   # CodePipeline, CodeBuild, roles; applied once, locally
│   │   ├── main.tf  variables.tf  outputs.tf  versions.tf
│   ├── modules/
│   │   ├── auth/                   # Cognito pool, client, groups, domain
│   │   ├── data/                   # DynamoDB table + GSI
│   │   ├── api/                    # HTTP API, authorizer, routes, log group
│   │   ├── functions/              # Lambda functions, roles, log groups, archive_file
│   │   ├── frontend/               # S3, CloudFront, OAC, policies
│   │   ├── messaging/              # SNS, SQS, DLQs, SES identity
│   │   ├── scheduling/             # Scheduler group, role, DLQ
│   │   ├── workflows/              # Step Functions state machine + role
│   │   └── observability/          # alarms, dashboard, ops topic, budget, pipeline-failure rule
│   └── envs/
│       └── dev/
│           ├── backend.tf  versions.tf  providers.tf
│           ├── main.tf  variables.tf  outputs.tf
│           └── terraform.tfvars    # non-secret values only
├── backend/
│   ├── pyproject.toml              # ruff, pytest, mypy config
│   ├── requirements.txt            # runtime deps (pinned)
│   ├── requirements-dev.txt
│   ├── src/
│   │   ├── common/                 # logging, errors, auth, db, responses, validation
│   │   └── handlers/               # one folder or file per Lambda
│   │       ├── list_events.py   get_event.py   create_event.py   start_cancel.py
│   │       ├── register.py      unregister.py  list_my_registrations.py   health.py
│   │       ├── send_reminder.py
│   │       └── email_sender.py
│   └── tests/
│       ├── unit/
│       └── fixtures/               # sample API Gateway events
├── frontend/
│   ├── package.json  package-lock.json  vite.config.ts  tsconfig.json
│   ├── public/
│   └── src/                        # pages: Events, EventDetail, MyRegistrations, Admin
└── scripts/
    ├── lib.sh                      # shared helpers (logging, require_cmd, tf_output)
    ├── build_backend.sh
    ├── build_frontend.sh
    ├── gen_frontend_config.sh
    ├── deploy_frontend.sh
    ├── smoke_test.sh
    ├── destroy.sh
    └── ci_install_terraform.sh
```

**Rules**
- One module = one concern. Modules never read another module's files; they pass values through `variables.tf` / `outputs.tf`.
- `build/` is generated and git-ignored.
- Secrets never go in the repo. Do not commit `terraform.tfstate`, `.tfvars` containing secrets, or credentials.

---

## 6. Conventions

- **Names:** `campus-hub-<env>-<purpose>`, for example `campus-hub-dev-register`. S3 bucket names add the account ID because they are globally unique.
- **Tags:** set once through `default_tags` in the provider block: `Project`, `Env`, `ManagedBy = terraform`.
- **Time:** store UTC ISO-8601 everywhere (`2026-11-20T09:00:00Z`). Convert to `Asia/Kolkata` only in the UI. Scheduler gets `schedule_expression_timezone = "UTC"`.
- **IDs:** ULIDs for event IDs (sortable, no collisions).
- **Commits:** Conventional Commits (`feat:`, `fix:`, `infra:`, `docs:`). Small commits, one concern each.
- **Branches:** `main` is deployable. Work on short-lived branches and merge via pull request.

---

## 7. Terraform standards

### Versions and providers
```hcl
# versions.tf
terraform {
  required_version = ">= 1.10.0"          # needed for S3 native locking
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }   # pin to the current major
  }
}
```
Commit `.terraform.lock.hcl`. Upgrade providers deliberately, not by accident.

```hcl
# providers.tf
provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = "campus-hub", Env = var.env, ManagedBy = "terraform" }
  }
}
# Only if you add a custom domain (ACM for CloudFront must live in us-east-1):
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}
```

### Remote state
```hcl
# envs/dev/backend.tf
terraform {
  backend "s3" {
    bucket       = "campus-hub-tfstate-<ACCOUNT_ID>"
    key          = "envs/dev/terraform.tfstate"
    region       = "ap-south-1"
    encrypt      = true
    use_lockfile = true      # S3-native locking, no DynamoDB lock table
  }
}
```
The state bucket (created in `infra/bootstrap`) has versioning on, SSE-S3, all public access blocked, and a bucket policy denying non-TLS requests.

### Rules
1. Run `terraform fmt -recursive`, `terraform validate` and `tflint` before every commit (pre-commit hook).
2. Never use `-target` except to recover from a broken state.
3. Every variable has a `type` and `description`. Validate inputs with `validation {}` blocks where mistakes are likely (region, env, email format).
4. Every output that scripts consume is documented. Mark sensitive outputs `sensitive = true`.
5. No hardcoded account IDs or ARNs: use `data.aws_caller_identity`, `data.aws_region`, `data.aws_partition`.
6. IAM: one role per Lambda, built from `aws_iam_policy_document`, specific actions and ARNs. No `"*"` resources unless the API requires it (document why in a comment).
7. Use `for_each` over `count` for named resources so adding or removing one does not shift the others.
8. Explicit `depends_on` only where Terraform cannot infer the dependency (comment why).
9. Data protection is variable-driven: `deletion_protection_enabled = var.protect_data` for DynamoDB and `force_destroy = var.force_destroy` for S3. In `dev` set `protect_data = false` so teardown works.
10. Create every Lambda log group in Terraform with `retention_in_days = 14` *before* the function (the function otherwise creates an unmanaged group that retains logs forever).
11. Lambda code updates are detected by `source_code_hash = data.archive_file.<fn>.output_base64sha256`.
12. Run `trivy config` (or `checkov`) over `infra/` and fix or explicitly suppress each finding with a reason.

---

## 8. Data model (single table)

Table `campus-hub-dev`, provisioned **5 RCU / 5 WCU**, one GSI (`GSI1`, also 5/5). No autoscaling. Total 10/10, well under 25/25 **(verify)**.

| Item | PK | SK | GSI1PK | GSI1SK | Attributes |
|---|---|---|---|---|---|
| Event | `EVENT#<ulid>` | `META` | `EVENTS` | `<startsAt>#<ulid>` | `title`, `description`, `location`, `startsAt`, `capacity`, `seatsTaken`, `entity`, `status` (`ACTIVE` / `CANCELLING` / `CANCELLED`), `reminderStatus` (`PENDING` / `SCHEDULED` / `SKIPPED` / `SENDING` / `SENT`), `reminderScheduleName`, `reminderSentAt`, `createdBy`, `createdAt` |
| Registration | `EVENT#<ulid>` | `USER#<sub>` | `USER#<sub>` | `<startsAt>#<ulid>` | `entity`, `email`, `eventTitle` (denormalized), `startsAt`, `registeredAt` |

**Access patterns**

| Need | Query |
|---|---|
| Upcoming events | GSI1: `GSI1PK = EVENTS`, `GSI1SK >= now`, filter `status = ACTIVE` |
| One event | Table: `PK = EVENT#id`, `SK = META` |
| Registrants of an event | Table: `PK = EVENT#id`, `SK begins_with USER#` |
| A student's registrations | GSI1: `GSI1PK = USER#sub` |

**Registration transaction (the part that must be right)**
```
TransactWriteItems
 1. Update EVENT#id / META
      SET seatsTaken = seatsTaken + 1
      CONDITION status = ACTIVE AND seatsTaken < capacity AND startsAt > now
 2. Put EVENT#id / USER#sub
      CONDITION attribute_not_exists(PK)
```
- `TransactionCanceledException` with the cancellation reasons decides the response: full event, then **409 EVENT_FULL**; already registered, then **409 ALREADY_REGISTERED**; cancelled, then **409 EVENT_CANCELLED**; already started, then **409 EVENT_STARTED**.
- Transactions use double the write units of normal writes, which matters at 5 WCU.
- Unregister is the mirror image: delete the registration (condition: exists) and decrement `seatsTaken` (condition: `seatsTaken > 0`), in one transaction.

---

## 9. API contract

All paths carry the `/api` prefix. JSON in, JSON out. Timestamps in UTC.

| Method | Path | Auth | Handler | Success |
|---|---|---|---|---|
| GET | `/api/health` | public | `health` | 200 |
| GET | `/api/events` | public | `list_events` | 200 |
| GET | `/api/events/{id}` | public | `get_event` | 200 / 404 |
| POST | `/api/events` | admin | `create_event` | 201 |
| POST | `/api/events/{id}/cancel` | admin | `start_cancel` | 202 |
| POST | `/api/events/{id}/registrations` | user | `register` | 201 / 409 |
| DELETE | `/api/events/{id}/registrations/me` | user | `unregister` | 204 / 404 |
| GET | `/api/me/registrations` | user | `list_my_registrations` | 200 |

**Error shape (every handler, no exceptions)**
```json
{ "error": { "code": "EVENT_FULL", "message": "This event has no seats left." }, "requestId": "..." }
```
Never return stack traces or raw exception text. Log the details, return the code.

---

## 10. Service configuration checklists and gotchas

### Cognito
- Authorization code flow with **PKCE**, public app client, **no client secret**.
- Use the classic hosted UI (`managed_login_version = 1`) to avoid needing a managed-login branding resource. **(verify)**
- Callback and sign-out URLs must match exactly, including `https://` and trailing slash. Add `http://localhost:5173/` for local development.
- Email is the sign-in alias, auto-verified. Create groups `admin` and `student`.
- Cognito's default email sender has a low daily limit (50/day per one source, **verify**). Fine for a demo; do not load-test sign-ups.
- Every sign-up counts as a monthly active user, including unconfirmed ones.
- Put your own admin account into the `admin` group with a Terraform `aws_cognito_user_in_group` or a one-off CLI command (documented in the runbook).

### API Gateway (HTTP API)
- JWT authorizer: `issuer = https://cognito-idp.<region>.amazonaws.com/<pool-id>`, `audience = [app client id]`. A wrong audience causes confusing 401s.
- Public routes (`/api/health`, `GET /api/events*`) simply have no authorizer attached.
- Use the `$default` stage with auto-deploy. Enable JSON access logs to a Terraform-managed log group (retention 14 days). Set stage throttling (for example 20 rps burst 40).
- **Admin check happens in Lambda.** In HTTP API v2 events, `cognito:groups` arrives as a *string* like `"[admin student]"`, not a list. Parse it defensively (strip brackets, split on spaces) and unit-test it.
- No CORS configuration needed: the browser only talks to the CloudFront domain.

### CloudFront and S3
- S3: block all public access, bucket policy allows only the CloudFront distribution via **Origin Access Control**.
- Default behavior to S3, `default_root_object = "index.html"`. **Do not** add custom error responses (see decision 3).
- `/api/*` behavior to the API origin (`<api-id>.execute-api.<region>.amazonaws.com`) with:
  - cache policy `Managed-CachingDisabled`,
  - origin request policy `Managed-AllViewerExceptHostHeader` (the API must not receive the CloudFront `Host` header),
  - all HTTP methods allowed.
  Look the managed policies up by name with the `aws_cloudfront_cache_policy` and `aws_cloudfront_origin_request_policy` data sources instead of hardcoding IDs.
- Attach the managed security-headers response policy to the default behavior.
- `viewer_protocol_policy = "redirect-to-https"`.
- A CloudFront distribution takes several minutes to deploy or update. This is normal.
- Custom domain (optional): ACM certificate in `us-east-1`, validated by a DNS CNAME at your registrar, which avoids paying for a Route 53 hosted zone.

### Lambda
- Runtime `python3.12`, architecture `arm64`, memory 256 MB, timeout 10 s (well under API Gateway's 29 s limit), `tracing_config { mode = "Active" }`.
- Use **AWS Lambda Powertools for Python** (structured logging, tracing, input parsing). Bundle it into the package with the build script.
- Build arm64-compatible dependencies explicitly: `pip install --platform manylinux2014_aarch64 --only-binary=:all: --target ...`.
- Create AWS SDK clients at module level (outside the handler) so warm invocations reuse them.
- Reserved concurrency is optional (`lambda_reserved_concurrency`, default `null`). Check the account's Lambda concurrency quota first: new accounts can have a quota of 10, and setting it then fails.
- Async/event-source functions get a DLQ or on-failure destination.

### DynamoDB
- See section 8. Never use `Scan` in request paths.
- Use `ConditionExpression` for every write that has a business rule.
- Alias every attribute with `ExpressionAttributeNames` (`#status`, `#seats`, ...). `status` is a reserved word.
- IAM: a transaction needs `dynamodb:PutItem`/`UpdateItem`/`DeleteItem` on the table. There is no `TransactWriteItems` IAM action.
- Point-in-time recovery adds cost **(verify)**; leave off in `dev`, document the decision.

### EventBridge Scheduler
- One-time schedule: `at(2026-11-19T09:00:00)` with timezone UTC, inside a dedicated **schedule group** `campus-hub-<env>-reminders`.
- `action_after_completion = "DELETE"` so finished schedules do not pile up.
- A dedicated IAM role lets Scheduler invoke only `send_reminder`. The creating Lambda needs `scheduler:CreateSchedule` and `iam:PassRole` for that one role (scope the PassRole to it).
- Set a retry policy (for example 3 attempts) and an SQS DLQ.
- Schedule times in the past are rejected. Handle that case before calling the API.
- Reminder handling must be idempotent: Scheduler is at-least-once. Claim with `UpdateItem ... CONDITION attribute_not_exists(reminderSentAt)`; on a later failure, release the claim and re-raise so the DLQ catches it.

### SNS and SQS
- `event-notifications` topic to `email-outbox` queue subscription with **raw message delivery** on. Put message attributes on the SNS message so subscriptions can filter by `type`.
- Queue policy must allow `sns.amazonaws.com` to `SendMessage` from that specific topic ARN.
- `email-outbox` redrive policy: `maxReceiveCount = 3` to a DLQ. Queue visibility timeout must be at least 6x the consumer Lambda timeout.
- Use SSE-SQS on queues (`sqs_managed_sse_enabled = true`).
- **Gotcha:** CloudWatch alarms cannot publish to an SNS topic encrypted with an AWS-managed key. Leave the `ops-alerts` topic unencrypted (it carries no PII). For `event-notifications` (contains email addresses), enable SSE with `alias/aws/sns` and **verify** delivery end to end.
- SNS topic policy for `ops-alerts` allows `cloudwatch.amazonaws.com` and `events.amazonaws.com` to publish.
- The admin must click the confirmation link in the subscription email.

### SES
- Verify a sender email identity (`aws_ses_email_identity`) and send with the `sesv2` API. In the sandbox, every *recipient* must be verified too. Use the team's addresses for demos, and request production access only if you truly need it.
- `email_sender` reads `EMAIL_MODE` (`ses` or `log`). Use `log` in tests and in any environment where SES is not ready; the rest of the pipeline still demonstrates SNS, SQS and Lambda.

### Step Functions (Standard)
- Use direct SDK integrations to avoid extra Lambdas: DynamoDB `Query`, SNS `Publish`, Scheduler `DeleteSchedule`, DynamoDB `UpdateItem`.
- `Map` state over registrants with `MaxConcurrency = 5`.
- `start_cancel` sets the event `CANCELLING` before starting the execution. The last state sets `CANCELLED`. If the workflow fails, the event stays `CANCELLING` (runbook: re-run it manually).
- `Catch` `ResourceNotFoundException` on `DeleteSchedule` (a skipped or already-fired reminder has no schedule).
- Enable logging (level `ERROR`) and X-Ray tracing.
- The workflow reads at most one page of query results. That is fine at campus-event scale; document the limit.

### CloudWatch and X-Ray
- Alarms (keep to **8 or fewer** because of the 10-alarm free allowance, **verify**):
  1. API 5xx rate
  2. Lambda errors: `register`
  3. Lambda errors: `send_reminder`
  4. Lambda throttles (any function)
  5. DynamoDB throttled requests
  6. DLQ depth: scheduler reminders
  7. DLQ depth: `email-outbox`
  8. Step Functions failed executions
- One dashboard (API requests/errors/latency, Lambda errors/duration, DLQ depth).
- X-Ray: use the default sampling rule at first; patch `boto3` through Powertools so DynamoDB/SNS calls show up as subsegments.

### Budgets
- `aws_budgets_budget`: monthly cost budget of $1 with email alerts at 80% actual and 100% forecasted. Add a second budget only if needed (two are free).
- Enable Free Tier usage alerts in Billing preferences (console setting, not Terraform).

---

## 11. Application code standards

**Structure**
- Handlers are thin: parse and validate input, call a function in `common/`, map the result to an HTTP response. Business rules live in `common/` so they can be unit-tested without Lambda events.
- One IAM role per handler (see Terraform rules), so a handler may only touch what it needs.

**Style and tooling**
- Python: `ruff` (lint and format), `mypy` on `common/`, type hints on all public functions, docstrings only where intent is not obvious.
- TypeScript strict mode on the frontend. `eslint` + `prettier`.
- Pin dependency versions (`requirements.txt`, `package-lock.json`). Review upgrades; no floating versions.

**Behavior**
- Validate every input at the edge (Powertools parser or Pydantic). Reject unknown fields, enforce lengths and types (title ≤ 120 chars, capacity 1 to 500, `startsAt` must be in the future).
- Use custom exception classes mapped to HTTP statuses in one place (`common/errors.py`). Handlers do not build error responses by hand.
- Structured JSON logs with `requestId`, `eventId`, and `userSub`. **Never log tokens, emails in bulk, or full request bodies.**
- Configuration comes from environment variables (table name, topic ARN, schedule group, role ARN, `EMAIL_MODE`). No constants for resource names in code.
- Retries only where operations are idempotent. Use conditional writes to make them so.
- No secrets in code, env files committed to Git, or frontend bundles. The frontend config contains only public identifiers (pool ID, client ID, domain).

**Frontend**
- Hash routing, a thin API client that attaches the ID token and maps error codes to messages, and token handling through a maintained OIDC library (for example `oidc-client-ts`) rather than hand-rolled PKCE.
- Vite dev server proxies `/api` to the CloudFront domain so local development needs no CORS.
- `config.json` is loaded at runtime (generated at deploy time), so one build works in any environment.

**Testing**
- Unit tests (`pytest`) for: group-claim parsing, validation, the registration transaction logic (with `moto`), error mapping, reminder time computation (including the "already in the past" and DST-free UTC cases).
- Fixtures: captured API Gateway v2 events in `tests/fixtures/`.
- Target: critical paths covered, not a coverage percentage.

---

## 12. Shell scripting standards

Scripts **never create or change AWS resources**: Terraform does that. Scripts build, ship, and verify.

**Every script**
```bash
#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"

ENV="${1:?usage: $(basename "$0") <env>}"
require_cmd terraform aws jq npm
```

**Rules**
- `shellcheck` clean, formatted with `shfmt`.
- Idempotent: running twice produces the same result.
- No hardcoded bucket names, distribution IDs, or account IDs. Read them with `terraform -chdir=infra/envs/$ENV output -raw <name>`.
- Quote every variable. Use `mktemp -d` and `trap 'rm -rf "$TMP"' EXIT` for temp files.
- Print what is happening (`log "Syncing frontend to s3://$BUCKET"`), fail loudly with a non-zero exit code and a clear message.
- Destructive scripts (`destroy.sh`) require typing the environment name to confirm.

| Script | Does |
|---|---|
| `build_backend.sh` | Installs pinned arm64 deps and copies `src/` into the single shared package `build/lambda/package/` with fixed file mtimes. Terraform zips it once; all ten functions use it |
| `build_frontend.sh` | `npm ci && npm run build` into `frontend/dist/` |
| `gen_frontend_config.sh` | Writes `dist/config.json` from Terraform outputs (pool ID, client ID, Cognito domain, region) |
| `deploy_frontend.sh` | `aws s3 sync` (hashed assets: long cache; `index.html` and `config.json`: `no-cache`), then CloudFront invalidation for `/index.html` and `/config.json` |
| `smoke_test.sh` | `GET /` returns 200 HTML, `GET /api/health` returns 200, `GET /api/events` returns 200, unauthenticated `POST /api/events` returns 401 |
| `destroy.sh` | Empties buckets, runs `terraform destroy` after confirmation |
| `ci_install_terraform.sh` | Installs a pinned Terraform in CodeBuild and verifies its checksum |

**Makefile**
```make
ENV ?= dev
TF  := terraform -chdir=infra/envs/$(ENV)

.PHONY: check test build plan apply deploy-frontend smoke destroy

check:            ## lint + validate everything
	pre-commit run --all-files
test:             ## unit tests
	cd backend && pytest -q
build:            ## build Lambda dirs and frontend
	./scripts/build_backend.sh && ./scripts/build_frontend.sh
plan: build
	$(TF) init && $(TF) plan -out=tfplan
apply:
	$(TF) apply tfplan
deploy-frontend:
	./scripts/gen_frontend_config.sh $(ENV) && ./scripts/deploy_frontend.sh $(ENV)
smoke:
	./scripts/smoke_test.sh $(ENV)
destroy:
	./scripts/destroy.sh $(ENV)
```

---

## 13. CI/CD

```
GitHub (main) --CodeConnections--> CodePipeline
  1. Source
  2. Build (CodeBuild, buildspec.plan.yml)    lint, tests, build, terraform plan -out=tfplan
  3. Approve (manual)                         reviewer reads the plan output
  4. Deploy (CodeBuild, buildspec.apply.yml)  terraform apply tfplan, deploy frontend, smoke test
```

**Pipeline rules**
- Exactly **one** pipeline (extra pipelines cost money). Trigger on `main` only.
- CodeBuild: `LINUX_CONTAINER`, `BUILD_GENERAL1_SMALL` (the size the free minutes cover, **verify**). Keep builds short; they draw from a small monthly allowance.
- Run `make check test` locally before pushing so CodeBuild minutes are not spent on lint failures.
- Pin the Terraform version in the buildspec and verify the download checksum.
- `infra/pipeline/` is applied by hand and is *not* managed by the pipeline it creates.
- The CodeBuild role is scoped to resources named `campus-hub-*` plus the state bucket. Start by recording what Terraform needs, then tighten using IAM Access Analyzer's generated policy. Do not leave `AdministratorAccess` in place.
- The saved plan (`tfplan`) travels as a pipeline artifact. Both stages must use the same Terraform and provider versions and the same directory layout, or fall back to re-planning in the Deploy stage.
- The GitHub connection needs a one-time manual authorization in the console after Terraform creates it.
- An EventBridge rule sends `FAILED` pipeline executions to `ops-alerts`.

**`buildspec.plan.yml` (sketch)**
```yaml
version: 0.2
env:
  variables:
    TF_VERSION: "1.10.5"        # pin; confirm it is current
phases:
  install:
    runtime-versions: { python: 3.12, nodejs: 20 }
    commands:
      - ./scripts/ci_install_terraform.sh "$TF_VERSION"
      - pip install -r backend/requirements-dev.txt
  build:
    commands:
      - cd backend && ruff check . && pytest -q && cd ..
      - ./scripts/build_backend.sh
      - ./scripts/build_frontend.sh
      - terraform -chdir=infra/envs/dev init -input=false
      - terraform -chdir=infra/envs/dev validate
      - terraform -chdir=infra/envs/dev plan -input=false -out=tfplan
      - terraform -chdir=infra/envs/dev show -no-color tfplan
artifacts:
  files: ['**/*']
```
(`ci_install_terraform.sh` is listed in the `scripts/` table in section 12.)

---

## 14. Security checklist

- [ ] Root account has MFA and is not used day to day.
- [ ] Work happens through an IAM Identity Center user or an IAM role with MFA. No long-lived access keys in the repo, shell history, or CodeBuild env vars.
- [ ] State bucket: private, versioned, encrypted, TLS-only.
- [ ] Frontend bucket: private, reachable only through CloudFront OAC.
- [ ] Each Lambda has its own least-privilege role.
- [ ] Admin actions are checked in Lambda (group claim), not only in the UI.
- [ ] No secrets in Git history. Run a secret scanner (for example `gitleaks`) in pre-commit.
- [ ] CloudFront redirects HTTP to HTTPS. Security-headers policy attached.
- [ ] API throttling is set. Lambda concurrency is capped.
- [ ] Logs contain no tokens or bulk personal data.
- [ ] IAM Access Analyzer enabled and findings reviewed.
- [ ] Budget alerts confirmed (subscription email clicked).

---

## 15. Cost guardrails

- Create the **budget and alerts before anything else** (Phase 0).
- Check once whether the account is on the **Free plan (credits) or a paid plan**. The Free plan blocks some services and ends at 6 months or when credits run out; Terraform errors about "service not available" usually mean this.
- Keep alarms at 8 or fewer, log retention at 14 days, one pipeline, one environment.
- Provisioned DynamoDB at 5/5 is a ceiling. A load test will throttle it; do not load-test.
- Avoid anything outside this plan: NAT Gateways, Elastic IPs, RDS/Aurora, EC2, WAF, Route 53 hosted zones, CMKs, X-Ray on high sampling.
- Weekly during development: open Billing, then Free Tier usage, and confirm nothing is near its limit.

---

## 16. Phased implementation plan

Size: **S** ≈ half a day or less, **M** ≈ about a day, **L** ≈ two days or more.

`guide.md` gives the practical deployment order (all modules are applied together in its Phase 6). `pseudo_code.md` section 11 lists the agent tasks T01 to T18.

### Phase 0: Prepare (S)
- Root MFA, admin identity, **budget + alerts**, choose region, confirm service availability in `ap-south-1` for every service in section 4.
- Install: Terraform ≥ 1.10, AWS CLI v2, Python 3.12, Node 20, `pre-commit`, `tflint`, `shellcheck`, `shfmt`, `trivy`.
- Create the repo, folder skeleton, `.gitignore`, pre-commit config, README stub.
- **Done when:** `aws sts get-caller-identity` works and `make check` passes on the empty skeleton.

### Phase 1: State bootstrap (S)
- `infra/bootstrap`: state bucket with versioning, encryption, public-access block, TLS-only policy. Apply locally.
- Wire `envs/dev/backend.tf` and run `terraform init`.
- **Done when:** `terraform init` in `envs/dev` succeeds against remote state and a second run shows no changes.

### Phase 2: Data and auth (M)
- `modules/data`: DynamoDB table + GSI.
- `modules/auth`: user pool, app client (PKCE, no secret), groups, hosted/managed login domain.
- **Done when:** you can sign up and sign in through the Cognito login page and put yourself in the `admin` group.

### Phase 3: API and core Lambdas (L)
- `modules/functions` and `modules/api`: `health`, `list_events`, `get_event`, `create_event` (without scheduling yet), `register`, `unregister`, `list_my_registrations`.
- `common/` package with errors, auth, validation, logging.
- Unit tests for claim parsing, validation, and the registration transaction.
- **Done when:** with a real ID token, `curl` can create an event, register, hit 409 on a duplicate and on a full event, and unregister. `health` works without a token.

### Phase 4: Frontend and CloudFront (M)
- `modules/frontend`, the React app (4 pages), `build_frontend.sh`, `gen_frontend_config.sh`, `deploy_frontend.sh`.
- **Done when:** the CloudFront URL loads the app, login round-trips, the events list renders, and `/api/*` works from the browser with no CORS errors.

### Phase 5: Messaging, scheduling, workflow (L)
- `modules/messaging`: SNS, SQS (+ DLQs), SES identity, `email_sender`.
- `modules/scheduling`: schedule group, role, DLQ. Extend `create_event`; add `send_reminder`.
- `modules/workflows`: cancel state machine and `start_cancel`.
- **Done when:** registering sends a confirmation (or a log line in `log` mode); an event created with a start time ~25 minutes ahead triggers its reminder within a minute or two of the schedule time (use a temporary shortened offset); cancelling notifies every registrant and marks the event `CANCELLED`.

### Phase 6: Observability (M)
- `modules/observability`: log groups verified, 8 alarms, dashboard, `ops-alerts`, pipeline-failure rule, budget.
- Force a failure (for example, make `register` throw) to prove an alarm reaches your inbox.
- **Done when:** one induced error produces an alarm email and an X-Ray trace you can open.

### Phase 7: Hardening (M)
- Work through the security checklist (section 14), run `trivy config`, tighten IAM with Access Analyzer, add Lambda concurrency caps and API throttling.
- **Done when:** the checklist is fully ticked and scan findings are fixed or documented.

### Phase 8: CI/CD (L)
- `infra/pipeline`: connection, pipeline, two CodeBuild projects, roles, artifact bucket. Apply locally, authorize the GitHub connection.
- `buildspec.plan.yml`, `buildspec.apply.yml`, `ci_install_terraform.sh`.
- **Done when:** a commit to `main` produces a plan, waits for approval, applies, deploys the frontend and passes the smoke test with no manual commands. A deliberately broken commit fails at Build and raises an alert.

### Phase 9: Documentation and demo (S)
- README (architecture diagram, how to deploy, how to tear down), `docs/runbook.md`, ADRs for section 2.
- Record a short demo: sign up, register, trigger a reminder, cancel an event, show the dashboard and a trace.
- **Done when:** someone else can deploy from a clean account by following the README only.

---

## 17. Testing and verification

| Level | What | Where |
|---|---|---|
| Static | `terraform fmt/validate`, `tflint`, `trivy config`, `ruff`, `mypy`, `shellcheck`, `eslint` | pre-commit and Build stage |
| Unit | Claim parsing, validation, transaction logic (`moto`), error mapping, reminder timing | `make test` |
| Local invoke | Handlers against fixture events | pytest |
| Smoke | `scripts/smoke_test.sh` after every deploy | Deploy stage |
| End-to-end (manual) | Sign up, register, duplicate and full cases, reminder, cancel, alarm | Phase 3, 5, 6 checkpoints |
| Plan hygiene | A second `terraform plan` immediately after apply shows **no changes** | Phase 1 onward |

---

## 18. Teardown

1. `make destroy ENV=dev` (set `protect_data = false` and `force_destroy = true` in dev first; `destroy.sh` empties the frontend bucket).
2. Check that no schedules remain in the reminders schedule group.
3. Destroy `infra/pipeline`, then delete the GitHub connection if Terraform did not.
4. Delete log groups that outlived their stack.
5. Destroy `infra/bootstrap` last, after emptying all versions in the state bucket.
6. Re-check the Billing dashboard the next day.

---

## 19. Items to verify before relying on them

- [ ] Every service in section 4 is available in `ap-south-1`.
- [ ] Which free offering applies to your account (credit-based Free plan vs legacy 12-month).
- [ ] HTTP API X-Ray support (this plan assumes none).
- [ ] DynamoDB always-free capacity numbers and whether on-demand mode is covered.
- [ ] EventBridge Scheduler free allowance.
- [ ] SES free-tier terms and sandbox limits for your account.
- [ ] CodePipeline free allowance (V1 pipeline vs V2 minutes) and CodeBuild free minutes for your compute type.
- [ ] CloudWatch free alarm count and whether metric-math alarms count extra.
- [ ] SNS SSE with `alias/aws/sns` delivering to SQS in your setup.
- [ ] Current Terraform and AWS provider versions to pin.
- [ ] Cognito classic hosted UI works on your chosen pool tier.
- [ ] Exact Step Functions error name for a missing schedule (`Scheduler.ResourceNotFoundException`).
- [ ] Your account's Lambda concurrency quota.

---

## 20. Definition of done

- Entire stack deploys from a clean account with `make` targets or the pipeline, and a second plan shows no changes.
- All eight API routes behave per section 9, including the 409 cases.
- A reminder and a cancellation notification arrive (or are logged in `log` mode).
- One induced failure produces an alarm email and a trace.
- Security checklist complete, scans clean or documented.
- Budget alerts confirmed; Free Tier usage page shows nothing near a limit.
- README lets a new reader deploy and tear down without asking you anything.
