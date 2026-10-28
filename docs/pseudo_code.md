# Campus Event & Hackathon Hub: Pseudo-code and Workflow Spec

Companion to `plan.md`. `plan.md` says **what and why**. This file says **exactly how**: logic, call order, field names, error mapping, Terraform wiring, scripts and acceptance checks.

---

## 0. How a coding agent must use this file

1. Read `plan.md` first, then this file. Where they disagree, **section 1 below wins** (it lists deliberate refinements).
2. Pseudo-code is Python-flavoured. `RAISE X` means raise that `AppError`. `ASSERT` means a guard that must hold. Anything in `<angle brackets>` is a placeholder.
3. Use the **exact** names, keys, status values, env vars, route keys and error codes defined here. Do not invent alternatives.
4. Work in the order of section 11. Do not start a task until the previous task's acceptance check passes.
5. Never run `terraform apply` or `terraform destroy` without first showing the plan or confirmation to the human.
6. Never write secrets, tokens, account IDs or real emails into source files. Use variables and `terraform output`.
7. Never use `"*"` in an IAM resource unless section 6.3 explicitly allows it.
8. If something is ambiguous or a **(verify)** item fails, stop and ask. Do not guess.
9. After each task run `make check` and `make test` (once they exist).

---

## 1. Refinements to plan.md

These override `plan.md`:

| # | Refinement | Reason |
|---|---|---|
| R1 | Event `status` has three values: `ACTIVE`, `CANCELLING`, `CANCELLED` | Lets `start_cancel` be idempotent and blocks new registrations while the workflow runs |
| R2 | **One shared Lambda package** at `build/lambda/package/`, one `archive_file`, ten functions that differ only by `handler` | Simpler, faster build. Least privilege is enforced by IAM roles, not by code splitting |
| R3 | Add `python-ulid` to runtime dependencies | Event IDs are ULIDs |
| R4 | Cognito uses the **classic hosted UI** (`managed_login_version = 1`) | Avoids needing a managed-login branding resource. **(verify)** it works on your pool tier |
| R5 | `reserved_concurrent_executions` is a variable that defaults to `null` | New accounts can have a low Lambda concurrency quota (even 10), and setting reserved concurrency then fails. Check the quota first |
| R6 | IAM has no `dynamodb:TransactWriteItems` action. A transaction needs the underlying `PutItem`/`UpdateItem`/`DeleteItem`/`ConditionCheckItem` permissions | That is how DynamoDB IAM works |
| R7 | Every attribute in a DynamoDB expression is aliased with `ExpressionAttributeNames` | `status` (and others) are reserved words |
| R8 | Handlers use the sesv2 API (`boto3.client("sesv2")`) | Current SES API |

---

## 2. Global constants

### 2.1 Naming
```
prefix            = "campus-hub-" + env                    # e.g. campus-hub-dev
table_name        = prefix
schedule_group    = prefix + "-reminders"
scheduler_role    = prefix + "-scheduler"
schedule_name(id) = "reminder-" + id                       # <= 64 chars, [A-Za-z0-9-_.]
sfn_name          = prefix + "-cancel-event"
topic_events      = prefix + "-event-notifications"
topic_ops         = prefix + "-ops-alerts"
queue_outbox      = prefix + "-email-outbox"
dlq_outbox        = prefix + "-email-outbox-dlq"
dlq_reminders     = prefix + "-reminders-dlq"
cognito_domain    = prefix + "-" + last 6 chars of account id   # must be globally unique
```

### 2.2 Status values
```
EVENT_STATUS      = ACTIVE | CANCELLING | CANCELLED
REMINDER_STATUS   = PENDING | SCHEDULED | SKIPPED | SENDING | SENT
```

### 2.3 Environment variables (set by Terraform, read in code)

| Variable | Functions | Value |
|---|---|---|
| `POWERTOOLS_SERVICE_NAME` | all | `campus-hub` |
| `POWERTOOLS_LOG_LEVEL` | all | `INFO` |
| `TABLE_NAME` | all except `health`, `email_sender` | table name |
| `TOPIC_ARN` | `register`, `send_reminder` | events topic ARN |
| `SCHEDULE_GROUP` | `create_event` | group name |
| `SCHEDULER_ROLE_ARN` | `create_event` | role ARN built from `scheduler_role` |
| `REMINDER_LAMBDA_ARN` | `create_event` | `send_reminder` function ARN |
| `REMINDER_DLQ_ARN` | `create_event` | reminders DLQ ARN |
| `REMINDER_LEAD_HOURS` | `create_event` | `24` |
| `CANCEL_STATE_MACHINE_ARN` | `start_cancel` | state machine ARN |
| `EMAIL_MODE` | `email_sender` | `ses` or `log` |
| `SENDER_EMAIL` | `email_sender` | verified SES identity |

Every handler reads its config at **module load** and fails fast if a required variable is missing.

### 2.4 Dependencies
`backend/requirements.txt` (pin exact versions): `aws-lambda-powertools[tracer]`, `pydantic`, `python-ulid`. `boto3` is provided by the Lambda runtime (do not bundle it).
`backend/requirements-dev.txt`: `pytest`, `moto[dynamodb,sns,sqs,sesv2]`, `ruff`, `mypy`, `boto3`.

---

## 3. Shared library: `backend/src/common/`

All of `backend/src/common/` and `backend/src/handlers/` need `__init__.py`.

### 3.1 `timeutil.py`
```python
FMT = "%Y-%m-%dT%H:%M:%SZ"

def now_utc() -> datetime:             # timezone-aware UTC
    return datetime.now(timezone.utc)

def to_iso(dt: datetime) -> str:       # ASSERT dt.tzinfo is not None
    return dt.astimezone(timezone.utc).strftime(FMT)

def parse_iso(s: str) -> datetime:
    # Accepts "2026-11-20T09:00:00Z" or with offset "+05:30". Rejects naive datetimes.
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None: RAISE ValidationError("INVALID_DATETIME", "startsAt must include a timezone")
    return dt.astimezone(timezone.utc)

def display_ist(iso: str) -> str:      # for emails only
    return parse_iso(iso).astimezone(ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y, %I:%M %p IST")
```
Lexicographic order of `FMT` strings equals chronological order. DynamoDB comparisons rely on this, so **always** store and compare with `to_iso`.

### 3.2 `errors.py`
```python
class AppError(Exception):
    status: int; code: str; message: str

# status  class            typical codes
# 400     ValidationError  INVALID_JSON, INVALID_FIELD, INVALID_DATETIME, INVALID_ID
# 401     Unauthorized     UNAUTHENTICATED, TOKEN_WRONG_TYPE, TOKEN_MISSING_EMAIL
# 403     Forbidden        ADMIN_ONLY
# 404     NotFound         EVENT_NOT_FOUND, NOT_REGISTERED
# 409     Conflict         EVENT_FULL, ALREADY_REGISTERED, EVENT_CANCELLED, EVENT_STARTED
# 503     Unavailable      TRY_AGAIN
# 500     InternalError    INTERNAL
```

### 3.3 `http.py`
```python
def json_response(status, body=None, headers=None):
    return {
      "statusCode": status,
      "headers": {"content-type": "application/json", "cache-control": "no-store", **(headers or {})},
      "body": "" if body is None else json.dumps(body, default=_decimal_to_int_or_float),
    }

def error_response(err: AppError, request_id):
    return json_response(err.status, {"error": {"code": err.code, "message": err.message}, "requestId": request_id})

def api_handler(fn):
    @logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_HTTP)
    @tracer.capture_lambda_handler
    def wrapper(event, context):
        request_id = event["requestContext"]["requestId"]
        try:
            return fn(event, context)
        except AppError as e:
            logger.warning("app_error", extra={"code": e.code, "status": e.status})
            return error_response(e, request_id)
        except Exception:
            logger.exception("unhandled")                 # full detail goes to logs only
            return error_response(InternalError("INTERNAL", "Something went wrong."), request_id)
    return wrapper

def parse_json_body(event) -> dict:
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"): raw = base64.b64decode(raw).decode("utf-8")
    try: data = json.loads(raw)
    except ValueError: RAISE ValidationError("INVALID_JSON", "Body must be valid JSON.")
    if not isinstance(data, dict): RAISE ValidationError("INVALID_JSON", "Body must be a JSON object.")
    return data

def path_id(event) -> str:
    v = (event.get("pathParameters") or {}).get("id", "")
    if not re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", v): RAISE ValidationError("INVALID_ID", "Invalid event id.")
    return v
```
`_decimal_to_int_or_float`: DynamoDB returns `Decimal`. Convert whole numbers to `int`, others to `float`.

### 3.4 `auth.py`
```python
@dataclass
class User: sub: str; email: str|None; groups: list[str]

def parse_groups(raw) -> list[str]:
    # HTTP API v2 delivers cognito:groups as a STRING like "[admin student]", not a list.
    if raw is None: return []
    if isinstance(raw, list): return [str(x) for x in raw]
    s = str(raw).strip().strip("[]")
    return [g for g in re.split(r"[\s,]+", s) if g]

def get_user(event) -> User | None:
    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims")
    if not claims or "sub" not in claims: return None
    if claims.get("token_use") != "id": RAISE Unauthorized("TOKEN_WRONG_TYPE", "Send the ID token.")
    return User(claims["sub"], claims.get("email"), parse_groups(claims.get("cognito:groups")))

def require_user(event, need_email=False) -> User:
    u = get_user(event)
    if u is None: RAISE Unauthorized("UNAUTHENTICATED", "Sign in required.")
    if need_email and not u.email: RAISE Unauthorized("TOKEN_MISSING_EMAIL", "Token has no email.")
    return u

def require_admin(event) -> User:
    u = require_user(event)
    if "admin" not in u.groups: RAISE Forbidden("ADMIN_ONLY", "Admins only.")
    return u
```
API Gateway has already verified the signature, issuer, audience and expiry before Lambda runs. Lambda only reads claims.

### 3.5 `validation.py`
```python
class CreateEventIn(BaseModel):                       # pydantic, extra="forbid"
    title: str        # strip; 1..120
    description: str = ""   # strip; 0..2000
    location: str     # strip; 1..120
    startsAt: str     # parsed with parse_iso; must be > now and <= now + 365 days
    capacity: int     # 1..500 (reject bool, reject float)

def validate_create_event(body: dict) -> dict:
    TRY m = CreateEventIn(**body)
    EXCEPT pydantic.ValidationError as e:
        RAISE ValidationError("INVALID_FIELD", first_error_message(e))   # never echo raw input
    starts = parse_iso(m.startsAt)
    ASSERT now < starts <= now + 365d  else ValidationError("INVALID_DATETIME", ...)
    return {**m.dict(), "startsAt": to_iso(starts)}
```

### 3.6 `keys.py`
```python
def event_pk(id):  return f"EVENT#{id}"
META_SK = "META"
def user_sk(sub):  return f"USER#{sub}"
def user_gsi_pk(sub): return f"USER#{sub}"
EVENTS_GSI_PK = "EVENTS"
def gsi_sk(starts_at, event_id): return f"{starts_at}#{event_id}"
GSI1 = "GSI1"
```

### 3.7 `db.py`
```python
_resource = boto3.resource("dynamodb")        # module level, reused across invocations
table  = _resource.Table(TABLE_NAME)
client = _resource.meta.client                 # accepts native Python types, needed for transact_write_items

def get_event(event_id) -> dict | None:
    return table.get_item(Key={"PK": event_pk(event_id), "SK": META_SK}).get("Item")

def to_public_event(it) -> dict:
    return {
      "eventId": it["PK"].removeprefix("EVENT#"), "title": it["title"], "description": it.get("description", ""),
      "location": it["location"], "startsAt": it["startsAt"], "capacity": int(it["capacity"]),
      "seatsTaken": int(it["seatsTaken"]), "seatsLeft": max(int(it["capacity"]) - int(it["seatsTaken"]), 0),
      "status": it["status"],
    }   # never expose createdBy, reminder* fields or schedule names

def cancellation_codes(client_error) -> list[str|None]:
    return [r.get("Code") for r in client_error.response.get("CancellationReasons", [])]
```
Rules for every DynamoDB expression: alias every attribute name (`#status`, `#cap`, `#seats`, `#starts`), pass every value through `ExpressionAttributeValues`.

### 3.8 `notify.py` and `emails.py`
```python
sns = boto3.client("sns")

def publish_email(kind: str, to: str, subject: str, body: str):
    sns.publish(TopicArn=TOPIC_ARN,
                Message=json.dumps({"to": to, "subject": subject, "body": body}),
                MessageAttributes={"type": {"DataType": "String", "StringValue": kind}})

def publish_email_batch(kind, messages: list[dict]):          # SNS PublishBatch max 10 entries
    for chunk in chunks(messages, 10):
        resp = sns.publish_batch(TopicArn=TOPIC_ARN, PublishBatchRequestEntries=[
            {"Id": str(i), "Message": json.dumps(m), "MessageAttributes": {"type": {...kind...}}}
            for i, m in enumerate(chunk)])
        if resp.get("Failed"): RAISE RuntimeError(f"{len(resp['Failed'])} publishes failed")

def confirmation_email(ev, reg) -> (subject, body)
def reminder_email(ev, reg)     -> (subject, body)    # "Reminder: {title} starts tomorrow", uses display_ist, location
```
Message JSON shape is a fixed contract between producers and `email_sender`: `{"to": str, "subject": str, "body": str}`.

---

## 4. Lambda handlers: `backend/src/handlers/`

Each handler's function name is `handler`. Terraform handler string: `handlers.<module>.handler`.

### 4.1 `health` (GET /api/health, public)
```
return 200 {"status": "ok", "time": to_iso(now_utc())}      # no database call, no auth
```

### 4.2 `list_events` (GET /api/events, public)
```
now = to_iso(now_utc())
items = []; start_key = None; pages = 0
LOOP while pages < 5 and len(items) < 50:
    resp = table.query(IndexName=GSI1,
        KeyConditionExpression="#p = :p AND #s >= :now",
        FilterExpression="#status = :active",
        ExpressionAttributeNames={"#p": "GSI1PK", "#s": "GSI1SK", "#status": "status"},
        ExpressionAttributeValues={":p": "EVENTS", ":now": now, ":active": "ACTIVE"},
        ScanIndexForward=True, **({"ExclusiveStartKey": start_key} if start_key else {}))
    items += resp["Items"]; start_key = resp.get("LastEvaluatedKey"); pages += 1
    BREAK if not start_key
return 200 {"events": [to_public_event(i) for i in items[:50]]}
```
`GSI1SK` starts with the ISO start time, so `>= now` selects upcoming events. No pagination cursor in the MVP.

### 4.3 `get_event` (GET /api/events/{id}, public)
```
id = path_id(event)
it = db.get_event(id)
IF it is None: RAISE NotFound("EVENT_NOT_FOUND", "Event not found.")
return 200 to_public_event(it)        # includes cancelled events so the UI can show their status
```

### 4.4 `create_event` (POST /api/events, admin)
```
admin = require_admin(event)
data  = validate_create_event(parse_json_body(event))
id    = str(ULID()); now = now_utc(); starts = parse_iso(data["startsAt"])
reminder_at = starts - timedelta(hours=REMINDER_LEAD_HOURS)

item = {PK: event_pk(id), SK: "META", entity: "EVENT",
        GSI1PK: "EVENTS", GSI1SK: gsi_sk(data.startsAt, id),
        title, description, location, startsAt, capacity,
        seatsTaken: 0, status: "ACTIVE", reminderStatus: "PENDING",
        createdBy: admin.sub, createdAt: to_iso(now)}
table.put_item(Item=item, ConditionExpression="attribute_not_exists(PK)")

IF reminder_at > now + 60 seconds:
    TRY scheduler.create_schedule(
          Name=schedule_name(id), GroupName=SCHEDULE_GROUP,
          ScheduleExpression=f"at({reminder_at.strftime('%Y-%m-%dT%H:%M:%S')})",
          ScheduleExpressionTimezone="UTC",
          FlexibleTimeWindow={"Mode": "OFF"},
          ActionAfterCompletion="DELETE",
          Target={"Arn": REMINDER_LAMBDA_ARN, "RoleArn": SCHEDULER_ROLE_ARN,
                  "Input": json.dumps({"eventId": id}),
                  "RetryPolicy": {"MaximumRetryAttempts": 3, "MaximumEventAgeInSeconds": 3600},
                  "DeadLetterConfig": {"Arn": REMINDER_DLQ_ARN}})
        update_item(set reminderStatus="SCHEDULED", reminderScheduleName=schedule_name(id))
    EXCEPT Exception:
        logger.exception("schedule_failed")
        best-effort table.delete_item(event key)          # compensation: no event without its reminder
        RAISE InternalError("INTERNAL", ...)
ELSE:
    update_item(set reminderStatus="SKIPPED")
return 201 to_public_event(item)
```

### 4.5 `start_cancel` (POST /api/events/{id}/cancel, admin)
```
require_admin(event); id = path_id(event)
TRY table.update_item(Key=event key,
        UpdateExpression="SET #status = :cancelling",
        ConditionExpression="attribute_exists(PK) AND #status = :active",
        names={"#status": "status"}, values={":cancelling": "CANCELLING", ":active": "ACTIVE"})
EXCEPT ConditionalCheckFailed:
    it = db.get_event(id)
    IF it is None: RAISE NotFound("EVENT_NOT_FOUND", ...)
    return 202 {"eventId": id, "status": it["status"]}        # already CANCELLING or CANCELLED: idempotent
ev = db.get_event(id)
TRY sfn.start_execution(stateMachineArn=CANCEL_STATE_MACHINE_ARN, name=f"cancel-{id}",
        input=json.dumps({"eventId": id, "title": ev["title"], "startsAt": ev["startsAt"]}))
EXCEPT ExecutionAlreadyExists: pass                            # idempotent
EXCEPT Exception:
    revert status to ACTIVE (conditional on CANCELLING), log, RAISE InternalError
return 202 {"eventId": id, "status": "CANCELLING"}
```

### 4.6 `register` (POST /api/events/{id}/registrations, signed in)
```
user = require_user(event, need_email=True); id = path_id(event)
ev = db.get_event(id)
IF ev is None:                           RAISE NotFound("EVENT_NOT_FOUND")
now = to_iso(now_utc())
IF ev.status != "ACTIVE":                RAISE Conflict("EVENT_CANCELLED")
IF ev.startsAt <= now:                   RAISE Conflict("EVENT_STARTED")
IF ev.seatsTaken >= ev.capacity:         RAISE Conflict("EVENT_FULL")     # early exit only; the transaction decides

TRY client.transact_write_items(TransactItems=[
  { "Update": { TableName, Key: {PK: event_pk(id), SK: "META"},
      UpdateExpression: "SET #seats = #seats + :one",
      ConditionExpression: "#status = :active AND #seats < #cap AND #starts > :now",
      ExpressionAttributeNames:  {"#seats": "seatsTaken", "#cap": "capacity", "#status": "status", "#starts": "startsAt"},
      ExpressionAttributeValues: {":one": 1, ":active": "ACTIVE", ":now": now} } },
  { "Put": { TableName,
      Item: {PK: event_pk(id), SK: user_sk(user.sub), entity: "REGISTRATION",
             GSI1PK: user_gsi_pk(user.sub), GSI1SK: gsi_sk(ev.startsAt, id),
             email: user.email, eventTitle: ev.title, startsAt: ev.startsAt, registeredAt: now},
      ConditionExpression: "attribute_not_exists(PK)" } } ])
EXCEPT ClientError as e:
    IF code == "TransactionCanceledException":
        r = cancellation_codes(e)                    # index 0 = event update, index 1 = registration put
        IF len(r) > 1 and r[1] == "ConditionalCheckFailed": RAISE Conflict("ALREADY_REGISTERED")
        IF r[0] == "ConditionalCheckFailed":
            cur = db.get_event(id)                   # classify why
            IF cur is None:                      RAISE NotFound("EVENT_NOT_FOUND")
            IF cur.status != "ACTIVE":           RAISE Conflict("EVENT_CANCELLED")
            IF cur.startsAt <= now:              RAISE Conflict("EVENT_STARTED")
            RAISE Conflict("EVENT_FULL")
        IF any(c in ("TransactionConflict", "ThrottlingError", "ProvisionedThroughputExceeded") for c in r):
            RAISE Unavailable("TRY_AGAIN", "Busy, please retry.")
    IF code in ("ProvisionedThroughputExceededException", "ThrottlingException"): RAISE Unavailable("TRY_AGAIN")
    RAISE   # anything else becomes 500 via api_handler

TRY (subject, body) = confirmation_email(ev, user); publish_email("confirmation", user.email, subject, body)
EXCEPT Exception: logger.exception("confirmation_publish_failed")      # never fail a registration over an email
return 201 {"eventId": id, "registeredAt": now, "seatsLeft": max(ev.capacity - ev.seatsTaken - 1, 0)}
```

### 4.7 `unregister` (DELETE /api/events/{id}/registrations/me, signed in)
```
user = require_user(event); id = path_id(event)
TRY client.transact_write_items(TransactItems=[
  { "Delete": { Key: {PK: event_pk(id), SK: user_sk(user.sub)}, ConditionExpression: "attribute_exists(PK)" } },
  { "Update": { Key: {PK: event_pk(id), SK: "META"}, UpdateExpression: "SET #seats = #seats - :one",
      ConditionExpression: "#seats > :zero", names {"#seats": "seatsTaken"}, values {":one": 1, ":zero": 0} } } ])
EXCEPT TransactionCanceledException:
    r = cancellation_codes(e)
    IF r[0] == "ConditionalCheckFailed": RAISE NotFound("NOT_REGISTERED")
    IF r[1] == "ConditionalCheckFailed": logger.error("seat_counter_inconsistent"); RAISE InternalError
    IF throttling/conflict codes: RAISE Unavailable("TRY_AGAIN")
return 204 (empty body)
```

### 4.8 `list_my_registrations` (GET /api/me/registrations, signed in)
```
user = require_user(event)
resp = table.query(IndexName=GSI1, KeyConditionExpression="#p = :p",
                   names {"#p": "GSI1PK"}, values {":p": user_gsi_pk(user.sub)}, ScanIndexForward=True)
return 200 {"registrations": [{"eventId": it["PK"].removeprefix("EVENT#"), "eventTitle": it["eventTitle"],
                               "startsAt": it["startsAt"], "registeredAt": it["registeredAt"]} for it in resp["Items"]]}
```
Known limitation (document in the README): registrations of cancelled events still appear in this list.

### 4.9 `send_reminder` (invoked by EventBridge Scheduler)
Input: `{"eventId": "<ulid>"}`. Not an API handler, so no HTTP response shape. It returns a small dict and raises on real failure so Scheduler retries and then dead-letters.
```
id = event["eventId"]
ev = db.get_event(id)
IF ev is None or ev.status != "ACTIVE": log; return {"skipped": "not_active"}

TRY table.update_item(Key=event key,                                   # claim: at-least-once safe
      UpdateExpression="SET #rs = :sending, reminderSentAt = :now",
      ConditionExpression="attribute_not_exists(reminderSentAt) AND #status = :active",
      names {"#rs": "reminderStatus", "#status": "status"}, values {...})
EXCEPT ConditionalCheckFailed: return {"skipped": "already_claimed"}

TRY:
    regs = query all pages: PK = event_pk(id) AND begins_with(SK, "USER#")
    msgs = [ {"to": r.email, "subject": s, "body": b} for r in regs for (s, b) in [reminder_email(ev, r)] ]
    publish_email_batch("reminder", msgs)
    table.update_item(set reminderStatus = "SENT")
    return {"sent": len(msgs)}
EXCEPT Exception:
    table.update_item(REMOVE reminderSentAt SET reminderStatus = "SCHEDULED")   # release the claim
    RAISE                                                                          # Scheduler retries, then DLQ
```
At-least-once delivery means a retry after a partial publish can send duplicates. Accept this and document it.

### 4.10 `email_sender` (SQS event source)
```
EMAIL_MODE = env("EMAIL_MODE")            # "ses" | "log"
ses = boto3.client("sesv2")
failures = []
FOR rec IN event["Records"]:
    TRY:
        m = json.loads(rec["body"])
        ASSERT keys to, subject, body all present and non-empty strings
        IF EMAIL_MODE == "log":
            logger.info("email_logged", extra={"to": mask(m["to"]), "subject": m["subject"]})
        ELSE:
            ses.send_email(FromEmailAddress=SENDER_EMAIL,
                           Destination={"ToAddresses": [m["to"]]},
                           Content={"Simple": {"Subject": {"Data": m["subject"]},
                                               "Body": {"Text": {"Data": m["body"]}}}})
    EXCEPT Exception:
        logger.exception("send_failed"); failures.append({"itemIdentifier": rec["messageId"]})
return {"batchItemFailures": failures}
```
`mask("alice@example.com") = "a***@example.com"`. The event source mapping **must** enable `ReportBatchItemFailures`, or one bad record re-queues the whole batch.

---

## 5. Step Functions: cancel workflow

File `infra/modules/workflows/cancel.asl.json.tftpl`, rendered with `templatefile()` using `table_name`, `topic_arn`, `schedule_group`. Type `STANDARD`. Input: `{"eventId","title","startsAt"}`.

```
QueryRegistrants   (dynamodb:query)   -> NotifyEach
NotifyEach         (Map, max 5)       -> DeleteSchedule
DeleteSchedule     (scheduler:deleteSchedule, catch not-found) -> MarkCancelled
MarkCancelled      (dynamodb:updateItem) -> end
```

```json
{
  "Comment": "Cancel event: notify registrants, delete reminder schedule, mark cancelled",
  "StartAt": "QueryRegistrants",
  "States": {
    "QueryRegistrants": {
      "Type": "Task",
      "Resource": "arn:aws:states:::aws-sdk:dynamodb:query",
      "Parameters": {
        "TableName": "${table_name}",
        "KeyConditionExpression": "PK = :pk AND begins_with(SK, :prefix)",
        "ExpressionAttributeValues": {
          ":pk": { "S.$": "States.Format('EVENT#{}', $.eventId)" },
          ":prefix": { "S": "USER#" }
        }
      },
      "ResultPath": "$.registrants",
      "Next": "NotifyEach"
    },
    "NotifyEach": {
      "Type": "Map",
      "ItemsPath": "$.registrants.Items",
      "MaxConcurrency": 5,
      "ItemSelector": {
        "payload": {
          "to.$": "$$.Map.Item.Value.email.S",
          "subject.$": "States.Format('Cancelled: {}', $.title)",
          "body.$": "States.Format('{} (scheduled {} UTC) has been cancelled.', $.title, $.startsAt)"
        }
      },
      "ItemProcessor": {
        "ProcessorConfig": { "Mode": "INLINE" },
        "StartAt": "PublishCancellation",
        "States": {
          "PublishCancellation": {
            "Type": "Task",
            "Resource": "arn:aws:states:::sns:publish",
            "Parameters": {
              "TopicArn": "${topic_arn}",
              "Message.$": "States.JsonToString($.payload)",
              "MessageAttributes": { "type": { "DataType": "String", "StringValue": "cancellation" } }
            },
            "End": true
          }
        }
      },
      "ResultPath": null,
      "Next": "DeleteSchedule"
    },
    "DeleteSchedule": {
      "Type": "Task",
      "Resource": "arn:aws:states:::aws-sdk:scheduler:deleteSchedule",
      "Parameters": {
        "Name.$": "States.Format('reminder-{}', $.eventId)",
        "GroupName": "${schedule_group}"
      },
      "ResultPath": null,
      "Catch": [ { "ErrorEquals": ["Scheduler.ResourceNotFoundException"], "ResultPath": null, "Next": "MarkCancelled" } ],
      "Next": "MarkCancelled"
    },
    "MarkCancelled": {
      "Type": "Task",
      "Resource": "arn:aws:states:::aws-sdk:dynamodb:updateItem",
      "Parameters": {
        "TableName": "${table_name}",
        "Key": {
          "PK": { "S.$": "States.Format('EVENT#{}', $.eventId)" },
          "SK": { "S": "META" }
        },
        "UpdateExpression": "SET #s = :c",
        "ExpressionAttributeNames": { "#s": "status" },
        "ExpressionAttributeValues": { ":c": { "S": "CANCELLED" } }
      },
      "End": true
    }
  }
}
```
Notes for the agent:
- The exact `Catch` error name for SDK integrations is `<Service>.<Exception>`. **(verify)** against a real failed execution's history.
- The query reads one page (up to 1 MB). That is fine at campus scale. Document it.
- If the workflow fails, the event stays `CANCELLING`. Recovery (runbook): fix the cause, then start an execution manually with the same input.

---

## 6. Terraform contracts

### 6.1 Dependency order
```
data ──────────────┐
messaging ─────────┤
workflows ─────────┼──> functions ──> scheduling (needs send_reminder ARN)
                   │         │
auth <── frontend <─┴── api (needs function invoke ARNs, issuer, client id)
observability (needs names/ARNs from all of the above)
```
**Breaking cycles:** `auth` needs the CloudFront domain, `frontend` needs the API domain, `api` needs `auth` outputs. This is a module-level loop but not a resource-level one, because the API *resource* does not depend on the authorizer. Expose `api_endpoint` from the `aws_apigatewayv2_api` resource only. If Terraform still reports a cycle, split `api` into `api` (API, stage, log group) and `api_routes` (authorizer, integrations, routes, permissions).
`functions` must **not** wait on `scheduling`. Compute the scheduler role ARN from its deterministic name:
`arn:${partition}:iam::${account_id}:role/${prefix}-scheduler`.

### 6.2 Module contracts

**`data`**: inputs `name`, `protect_data`. Resources: `aws_dynamodb_table` (provisioned 5/5, hash `PK` (S), range `SK` (S), attributes `GSI1PK`, `GSI1SK` (S), GSI `GSI1` projection `ALL`, 5/5, `deletion_protection_enabled = var.protect_data`, no PITR, no autoscaling). Outputs: `table_name`, `table_arn`, `gsi1_arn` (`"${table_arn}/index/GSI1"`).

**`auth`**: inputs `prefix`, `callback_urls`, `logout_urls`, `domain_prefix`. Resources: `aws_cognito_user_pool` (username attribute `email`, auto-verify email, password min length 8, MFA off, email recovery, `user_pool_tier = "ESSENTIALS"`), `aws_cognito_user_pool_domain` (`managed_login_version = 1`), two `aws_cognito_user_group` (`admin`, `student`), `aws_cognito_user_pool_client` (no secret, `allowed_oauth_flows = ["code"]`, `allowed_oauth_flows_user_pool_client = true`, scopes `openid email`, identity provider `COGNITO`, `explicit_auth_flows = ["ALLOW_REFRESH_TOKEN_AUTH"]`, `prevent_user_existence_errors = "ENABLED"`, `enable_token_revocation = true`, ID and access token validity 60 minutes, refresh 30 days). Outputs: `user_pool_id`, `client_id`, `issuer` (`https://cognito-idp.${region}.amazonaws.com/${pool_id}`), `domain_url` (`https://${domain}.auth.${region}.amazoncognito.com`).

**`messaging`**: inputs `prefix`, `sender_email`. Resources: `aws_sns_topic` events (`kms_master_key_id = "alias/aws/sns"` **(verify)** delivery to SQS), `aws_sqs_queue` DLQ (`sqs_managed_sse_enabled = true`, retention 14 days), `aws_sqs_queue` outbox (`visibility_timeout_seconds = 60`, redrive `maxReceiveCount = 3`), queue policy (allow `sns.amazonaws.com` `sqs:SendMessage` with `aws:SourceArn = topic arn`), `aws_sns_topic_subscription` (protocol `sqs`, `raw_message_delivery = true`), `aws_ses_email_identity`. Outputs: `topic_arn`, `topic_name`, `queue_arn`, `queue_name`, `dlq_name`, `ses_identity_arn`.

**`workflows`**: inputs `prefix`, `table_name`, `table_arn`, `topic_arn`, `schedule_group`. Resources: `aws_cloudwatch_log_group` (`/aws/vendedlogs/states/<name>`, retention 14), `aws_iam_role` + policy, `aws_sfn_state_machine` (`STANDARD`, `definition = templatefile(...)`, logging level `ERROR` with `log_destination = "${log_group_arn}:*"`, `include_execution_data = false`, tracing on). Outputs: `state_machine_arn`, `state_machine_name`.

**`functions`**: inputs `prefix`, `table_name`, `table_arn`, `gsi1_arn`, `topic_arn`, `queue_arn`, `ses_identity_arn`, `state_machine_arn`, `schedule_group`, `scheduler_role_arn`, `reminders_dlq_arn`, `email_mode`, `sender_email`, `reserved_concurrency` (default `null`). Resources: one `data.archive_file` of `build/lambda/package/`; `for_each` over the function map below producing a log group, IAM role, policy and `aws_lambda_function` per function. Function settings: `python3.12`, `["arm64"]`, memory 256, timeout 10, `tracing_config.mode = "Active"`, `depends_on = [log_group]`. Also an `aws_lambda_event_source_mapping` for `email_sender` (`batch_size = 10`, `function_response_types = ["ReportBatchItemFailures"]`). Outputs: `function_arns`, `function_invoke_arns`, `function_names` (maps keyed by function name).

**`scheduling`**: inputs `prefix`, `reminder_lambda_arn`. Resources: `aws_scheduler_schedule_group`, reminders DLQ (SSE-SQS), `aws_iam_role` named `${prefix}-scheduler` (trust `scheduler.amazonaws.com` with `aws:SourceAccount` condition; permissions `lambda:InvokeFunction` on the reminder ARN, `sqs:SendMessage` on the DLQ). Outputs: `group_name`, `dlq_arn`, `dlq_name`, `role_arn`.

**`api`**: inputs `prefix`, `function_invoke_arns`, `function_arns`, `issuer`, `client_id`. Resources: `aws_apigatewayv2_api` (HTTP), log group (retention 14), `aws_apigatewayv2_stage` (`$default`, `auto_deploy = true`, JSON access-log format, `default_route_settings` burst 40 and rate 20), `aws_apigatewayv2_authorizer` (JWT, identity source `$request.header.Authorization`, audience `[client_id]`, issuer), one integration + route + `aws_lambda_permission` per row of the route table. Outputs: `api_endpoint`, `api_domain` (endpoint without `https://`), `api_id`, `execution_arn`.

Route table (`route_key` is exact):

| route_key | function | auth |
|---|---|---|
| `GET /api/health` | health | NONE |
| `GET /api/events` | list_events | NONE |
| `GET /api/events/{id}` | get_event | NONE |
| `POST /api/events` | create_event | JWT |
| `POST /api/events/{id}/cancel` | start_cancel | JWT |
| `POST /api/events/{id}/registrations` | register | JWT |
| `DELETE /api/events/{id}/registrations/me` | unregister | JWT |
| `GET /api/me/registrations` | list_my_registrations | JWT |

Integration: `AWS_PROXY`, `integration_method = "POST"`, `payload_format_version = "2.0"`. Lambda permission: principal `apigateway.amazonaws.com`, `source_arn = "${execution_arn}/*/*"`.

**`frontend`**: inputs `prefix`, `api_domain`, `force_destroy`. Resources: private S3 bucket (`bucket = "${prefix}-site-${account_id}"`, all four public-access blocks `true`, `BucketOwnerEnforced`, SSE-S3), OAC (S3, `always`, `sigv4`), `aws_cloudfront_distribution` (see below), bucket policy (allow `cloudfront.amazonaws.com` `s3:GetObject` on `bucket/*` with `AWS:SourceArn = distribution arn`). Data sources by name: `Managed-CachingOptimized`, `Managed-CachingDisabled`, `Managed-AllViewerExceptHostHeader`, `Managed-SecurityHeadersPolicy`.
Distribution: `default_root_object = "index.html"`, `price_class = "PriceClass_200"` **(verify)** India coverage, default viewer certificate. **No `custom_error_response`.** Origins: `s3` (OAC) and `api` (`custom_origin_config` https-only, TLSv1.2). Default behavior to `s3` (GET/HEAD, redirect-to-https, compress, CachingOptimized, security-headers policy). Ordered behavior `/api/*` to `api` (all 7 methods allowed, CachingDisabled, AllViewerExceptHostHeader, redirect-to-https). Outputs: `bucket_name`, `distribution_id`, `domain_name`, `site_url` (`https://${domain_name}`).

**`observability`**: inputs `prefix`, `admin_email`, `budget_limit_usd`, plus names/IDs for every alarm below. Resources: ops SNS topic (**unencrypted**) with a topic policy allowing `cloudwatch.amazonaws.com` and `events.amazonaws.com` to `sns:Publish`, email subscription, the eight alarms, one dashboard, an `aws_cloudwatch_event_rule` for pipeline failures, and `aws_budgets_budget`.

Alarms (all `treat_missing_data = "notBreaching"`, `alarm_actions = [ops_topic_arn]`, 1 evaluation period, period 300 s):

| # | Name suffix | Namespace / metric / dimension | Stat | Threshold |
|---|---|---|---|---|
| 1 | api-5xx | `AWS/ApiGateway` / `5xx` / `ApiId` | Sum | >= 3 |
| 2 | register-errors | `AWS/Lambda` / `Errors` / `FunctionName` | Sum | >= 1 |
| 3 | reminder-errors | `AWS/Lambda` / `Errors` / `FunctionName` | Sum | >= 1 |
| 4 | lambda-throttles | `AWS/Lambda` / `Throttles` (no dimension, account-wide) | Sum | >= 1 |
| 5 | ddb-write-throttles | `AWS/DynamoDB` / `WriteThrottleEvents` / `TableName` | Sum | >= 1 |
| 6 | reminders-dlq | `AWS/SQS` / `ApproximateNumberOfMessagesVisible` / `QueueName` | Maximum | >= 1 |
| 7 | outbox-dlq | same, outbox DLQ queue | Maximum | >= 1 |
| 8 | cancel-failed | `AWS/States` / `ExecutionsFailed` / `StateMachineArn` | Sum | >= 1 |

Pipeline-failure rule pattern: `{"source": ["aws.codepipeline"], "detail-type": ["CodePipeline Pipeline Execution State Change"], "detail": {"state": ["FAILED"]}}`, target the ops topic.
Budget: `COST`, `MONTHLY`, limit `var.budget_limit_usd` (default `1`), two notifications (`ACTUAL` 80 % and `FORECASTED` 100 %, `GREATER_THAN`) to `admin_email`.

### 6.3 Function map and IAM matrix

Each function gets logs permissions on its own log group plus `xray:PutTraceSegments` and `xray:PutTelemetryRecords` on `"*"` (the only allowed wildcard; these X-Ray actions do not support resource scoping).

| Function | DynamoDB | Other |
|---|---|---|
| `health` | none | none |
| `list_events` | `Query` on `gsi1_arn` | none |
| `get_event` | `GetItem` on table | none |
| `create_event` | `PutItem`, `UpdateItem`, `DeleteItem` on table | `scheduler:CreateSchedule` on `schedule/<group>/reminder-*`; `iam:PassRole` on scheduler role with `iam:PassedToService = scheduler.amazonaws.com` |
| `start_cancel` | `GetItem`, `UpdateItem` on table | `states:StartExecution` on the state machine ARN |
| `register` | `GetItem`, `PutItem`, `UpdateItem` on table | `sns:Publish` on events topic |
| `unregister` | `DeleteItem`, `UpdateItem` on table | none |
| `list_my_registrations` | `Query` on `gsi1_arn` | none |
| `send_reminder` | `GetItem`, `UpdateItem`, `Query` on table | `sns:Publish` on events topic |
| `email_sender` | none | `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes` on outbox queue; `ses:SendEmail` on SES identity |

If the events topic uses `alias/aws/sns`, test publishing; if it fails with `KMS` errors, add `kms:GenerateDataKey*` and `kms:Decrypt` for the key and note it in an ADR.

### 6.4 Root `infra/envs/dev/main.tf` (wiring sketch)
```hcl
locals {
  prefix             = "campus-hub-${var.env}"
  scheduler_role_arn = "arn:${data.aws_partition.this.partition}:iam::${data.aws_caller_identity.this.account_id}:role/${local.prefix}-scheduler"
  schedule_group     = "${local.prefix}-reminders"
}
module "data"      { source = "../../modules/data"      name = local.prefix  protect_data = var.protect_data }
module "messaging" { source = "../../modules/messaging" prefix = local.prefix sender_email = var.sender_email }
module "workflows" { ... table_name  table_arn  topic_arn = module.messaging.topic_arn  schedule_group = local.schedule_group }
module "functions" { ... all inputs from data, messaging, workflows; scheduler_role_arn = local.scheduler_role_arn; reminders_dlq_arn = module.scheduling.dlq_arn }
module "scheduling"{ ... reminder_lambda_arn = module.functions.function_arns["send_reminder"] }
module "auth"      { ... callback_urls = ["https://${module.frontend.domain_name}/", "http://localhost:5173/"]  logout_urls = same }
module "api"       { ... issuer = module.auth.issuer  client_id = module.auth.client_id  function_invoke_arns = module.functions.function_invoke_arns }
module "frontend"  { ... api_domain = module.api.api_domain }
module "observability" { ... }
```
`module.functions` takes `reminders_dlq_arn` from `module.scheduling`, and `module.scheduling` takes the reminder ARN from `module.functions`. If Terraform reports a cycle here, build the DLQ in `messaging` or compute its ARN from its deterministic name instead.

Root outputs (the **only** values scripts may read): `site_url`, `api_endpoint`, `region`, `user_pool_id`, `app_client_id`, `cognito_domain_url`, `frontend_bucket`, `distribution_id`.
Root variables: `env`, `region`, `sender_email`, `admin_email`, `email_mode` (default `"log"`), `protect_data`, `force_destroy`, `budget_limit_usd`, `lambda_reserved_concurrency` (default `null`).

---

## 7. Frontend logic (`frontend/src/`)

Stack: React + Vite + TypeScript (strict), `react-router-dom` with `HashRouter`, `oidc-client-ts`.

**Runtime config:** `fetch("/config.json")` before rendering. Shape:
```json
{ "region": "...", "userPoolId": "...", "clientId": "...", "cognitoDomain": "https://....auth.<region>.amazoncognito.com", "redirectUri": "https://<cf-domain>/" }
```

**Auth module (`auth.ts`)**
```
mgr = new UserManager({
  authority: `https://cognito-idp.${region}.amazonaws.com/${userPoolId}`,
  client_id: clientId, redirect_uri: redirectUri, response_type: "code",
  scope: "openid email", automaticSilentRenew: false })

ON APP START:
  IF location.search has "code=" and "state=":
      await mgr.signinRedirectCallback()
      history.replaceState({}, "", location.pathname + "#/")         # strip the query string
  user = await mgr.getUser()
  isAdmin = user?.profile["cognito:groups"]?.includes("admin")        # UI hint only; the server enforces it

login()  = mgr.signinRedirect()
logout() = mgr.removeUser(); location.href = `${cognitoDomain}/logout?client_id=${clientId}&logout_uri=${encodeURIComponent(redirectUri)}`
```
Cognito's logout endpoint is not standard OIDC, hence the manual redirect.

**API client (`api.ts`)**
```
async function api(method, path, body?):
    headers = {"content-type": "application/json"}
    user = await mgr.getUser()
    IF user and not user.expired: headers.Authorization = "Bearer " + user.id_token       # ID token, not access token
    res = await fetch("/api" + path, {method, headers, body: body && JSON.stringify(body)})
    IF res.status == 204: return null
    data = await res.json()
    IF !res.ok:
        IF res.status == 401: login()                                       # expired or missing token
        THROW new ApiError(data.error.code, data.error.message, data.requestId)
    return data
```
Map error codes to friendly messages in one table (`EVENT_FULL`, `ALREADY_REGISTERED`, `EVENT_CANCELLED`, `EVENT_STARTED`, `TRY_AGAIN`, `ADMIN_ONLY`).

**Routes (hash)**

| Hash route | Page | Calls |
|---|---|---|
| `#/` | Events list | `GET /events` |
| `#/events/:id` | Detail + Register/Unregister button | `GET /events/{id}`, `POST/DELETE .../registrations` |
| `#/me` | My registrations | `GET /me/registrations` |
| `#/admin` | Create event form, cancel button (admin only) | `POST /events`, `POST /events/{id}/cancel` |

Show times converted to `Asia/Kolkata` with `Intl.DateTimeFormat`. Send `datetime-local` input values as UTC ISO strings (`new Date(value).toISOString()`).

**Local development:** `vite.config.ts` proxies `/api` to the deployed CloudFront domain. `./scripts/gen_frontend_config.sh dev --local` writes a git-ignored `frontend/public/config.json` with `redirectUri = http://localhost:5173/`.

---

## 8. Shell scripts

### 8.1 `scripts/lib.sh`
```bash
log()  { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
die()  { log "ERROR: $*"; exit 1; }
require_cmd() { for c in "$@"; do command -v "$c" >/dev/null || die "missing command: $c"; done; }
tf_dir()    { echo "$ROOT/infra/envs/$1"; }
tf_output() { terraform -chdir="$(tf_dir "$1")" output -raw "$2"; }   # string outputs only
```

### 8.2 `build_backend.sh`
```
set -euo pipefail; source lib.sh
require_cmd python3 pip
OUT="$ROOT/build/lambda/package"
rm -rf "$OUT"; mkdir -p "$OUT"
pip install -r backend/requirements.txt --target "$OUT" --upgrade \
    --platform manylinux2014_aarch64 --implementation cp --python-version 3.12 --only-binary=:all:
cp -R backend/src/. "$OUT/"
find "$OUT" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$OUT" -exec touch -d '2020-01-01 00:00:00' {} +        # deterministic mtimes, stable hash
log "built $OUT"
```
Do not bundle `boto3` or `botocore` (remove them from `requirements.txt` resolution if pulled in transitively).

### 8.3 `build_frontend.sh`
```
cd frontend; npm ci; npm run build        # output: frontend/dist/
```

### 8.4 `gen_frontend_config.sh <env> [--local]`
```
ENV=$1
jq -n --arg region "$(tf_output $ENV region)" \
      --arg pool   "$(tf_output $ENV user_pool_id)" \
      --arg client "$(tf_output $ENV app_client_id)" \
      --arg domain "$(tf_output $ENV cognito_domain_url)" \
      --arg redirect "${LOCAL:+http://localhost:5173/}${LOCAL:-$(tf_output $ENV site_url)/}" \
   '{region:$region,userPoolId:$pool,clientId:$client,cognitoDomain:$domain,redirectUri:$redirect}' \
   > "$TARGET/config.json"          # TARGET = frontend/dist (deploy) or frontend/public (--local)
```

### 8.5 `deploy_frontend.sh <env>`
```
BUCKET=$(tf_output $ENV frontend_bucket); DIST=$(tf_output $ENV distribution_id)
aws s3 sync frontend/dist "s3://$BUCKET" --delete \
    --exclude index.html --exclude config.json \
    --cache-control "public,max-age=31536000,immutable"
aws s3 cp frontend/dist/index.html  "s3://$BUCKET/index.html"  --cache-control "no-cache" --content-type text/html
aws s3 cp frontend/dist/config.json "s3://$BUCKET/config.json" --cache-control "no-cache" --content-type application/json
ID=$(aws cloudfront create-invalidation --distribution-id "$DIST" --paths /index.html /config.json --query Invalidation.Id --output text)
aws cloudfront wait invalidation-completed --distribution-id "$DIST" --id "$ID"
```

### 8.5b `smoke_test.sh <env>`
```
BASE=$(tf_output $ENV site_url)
check() {   # check <expected_status> <method> <path> [jq_filter]
    FOR attempt IN 1..10:
        code=$(curl -s -o /tmp/body -w '%{http_code}' -X "$2" "$BASE$3")
        IF code == expected: BREAK ; ELSE sleep 5
    [ "$code" = "$1" ] || die "$2 $3 expected $1 got $code"
    IF jq_filter given: jq -e "$4" /tmp/body >/dev/null || die "bad body for $3"
}
check 200 GET /                    ; grep -qi '<html' /tmp/body
check 200 GET /api/health          '.status == "ok"'
check 200 GET /api/events          '.events | type == "array"'
check 401 POST /api/events
check 401 GET  /api/me/registrations
log "smoke OK"
```

### 8.6 `destroy.sh <env>`
```
read -rp "Type the environment name to confirm destroy: " ans; [ "$ans" = "$ENV" ] || die "aborted"
BUCKET=$(tf_output $ENV frontend_bucket)
aws s3 rm "s3://$BUCKET" --recursive
terraform -chdir="$(tf_dir $ENV)" destroy              # interactive; shows the plan
aws scheduler list-schedules --group-name "campus-hub-$ENV-reminders" || true   # expect: group not found or empty
```
Prerequisite: `protect_data=false` and `force_destroy=true` applied in dev first.

### 8.7 `ci_install_terraform.sh <version>`
```
curl -fsSLo tf.zip  "https://releases.hashicorp.com/terraform/$V/terraform_${V}_linux_amd64.zip"
curl -fsSLo SUMS    "https://releases.hashicorp.com/terraform/$V/terraform_${V}_SHA256SUMS"
grep "terraform_${V}_linux_amd64.zip" SUMS | sha256sum -c -
unzip -o tf.zip -d /usr/local/bin; terraform version
```
CodeBuild's standard Linux image is x86_64, so use `linux_amd64` here even though Lambdas are arm64.

---

## 9. CI/CD

### 9.1 `buildspec.plan.yml`
```
install:   runtime-versions python 3.12, nodejs 20; ci_install_terraform.sh $TF_VERSION; pip install -r backend/requirements-dev.txt
pre_build: cd backend; ruff check .; pytest -q ; cd ..
build:     build_backend.sh; build_frontend.sh
           terraform -chdir=infra/envs/dev init -input=false
           terraform -chdir=infra/envs/dev validate
           terraform -chdir=infra/envs/dev plan -input=false -out=tfplan
           terraform -chdir=infra/envs/dev show -no-color tfplan      # reviewer reads this at the approval gate
artifacts: infra/**, build/**, frontend/dist/**, scripts/**, Makefile    # NOT node_modules, NOT .git
```

### 9.2 `buildspec.apply.yml`
```
install:   ci_install_terraform.sh $TF_VERSION
build:     terraform -chdir=infra/envs/dev init -input=false
           terraform -chdir=infra/envs/dev apply -input=false tfplan
           ./scripts/gen_frontend_config.sh dev
           ./scripts/deploy_frontend.sh dev
           ./scripts/smoke_test.sh dev
```
If the saved plan fails to apply (for example a version or path mismatch), fall back to `terraform plan -out=tfplan && terraform apply tfplan` in this stage and record why in an ADR.

### 9.3 Pipeline (`infra/pipeline/`)
```
Stage Source   : CodeConnections (GitHub), branch main, output artifact "src"
Stage Build    : CodeBuild project "plan"  (buildspec.plan.yml, BUILD_GENERAL1_SMALL, LINUX_CONTAINER)
Stage Approve  : Manual approval
Stage Deploy   : CodeBuild project "apply" (buildspec.apply.yml)
```
Env vars on both projects: `TF_VERSION`, `AWS_REGION`. CodeBuild role: scoped to `campus-hub-*` resources and the state bucket, tightened with IAM Access Analyzer. One pipeline only.

---

## 10. Test specification

### 10.1 `parse_groups`
| Input | Expected |
|---|---|
| `None` | `[]` |
| `"[admin student]"` | `["admin","student"]` |
| `"admin"` | `["admin"]` |
| `"[admin]"` | `["admin"]` |
| `["admin"]` | `["admin"]` |
| `""` or `"[]"` | `[]` |

### 10.2 Validation (`validate_create_event`)
Reject: missing title; title of 121 characters; capacity `0`, `501`, `True`, `"5"`, `5.5`; unknown field; `startsAt` without timezone, in the past, or more than 365 days ahead; non-object body; invalid JSON. Accept: `+05:30` offset (normalized to `...Z`). Error responses must not echo raw input.

### 10.3 Registration (moto or botocore `Stubber`)
| Scenario | Expected |
|---|---|
| Happy path | 201, `seatsTaken` +1, registration item exists |
| Same user twice | 409 `ALREADY_REGISTERED`, `seatsTaken` unchanged |
| Capacity 1, second user | 409 `EVENT_FULL` |
| Event `CANCELLING` or `CANCELLED` | 409 `EVENT_CANCELLED` |
| Start time in the past | 409 `EVENT_STARTED` |
| Unknown event | 404 `EVENT_NOT_FOUND` |
| Malformed id | 400 `INVALID_ID` |
| Token without email | 401 `TOKEN_MISSING_EMAIL` |
| SNS publish raises | still 201, error logged |
| Unregister registered user | 204, `seatsTaken` -1 |
| Unregister when not registered | 404 `NOT_REGISTERED` |

If moto does not return realistic `CancellationReasons`, use `Stubber` for those cases.

### 10.4 Create/cancel/reminder
- `create_event` with start 48 h ahead: schedule created with expected name and expression; `reminderStatus = SCHEDULED`.
- Start 2 h ahead: no schedule, `reminderStatus = SKIPPED`.
- Scheduler call raises: event item deleted, 500 returned.
- Non-admin: 403 `ADMIN_ONLY`.
- `start_cancel` twice: both return 202, `start_execution` called once (second call sees `CANCELLING`).
- `send_reminder`: cancelled event gives `skipped`; already claimed gives `skipped`; publish failure releases the claim and re-raises; success marks `SENT`; zero registrants still marks `SENT`.
- `email_sender`: `log` mode never calls SES; malformed record is returned in `batchItemFailures`; good records in the same batch are not.

### 10.5 Infra checks (no unit test, run by command)
`terraform validate`, `tflint`, `trivy config infra/`, `terraform plan` shows **no changes** on the second run.

---

## 11. Agent task list (do in order)

Each task has an **acceptance check**. Stop and report if it fails.

| ID | Task | Acceptance check |
|---|---|---|
| T01 | Create repo skeleton from plan.md section 5, `.gitignore`, `.editorconfig`, pre-commit config | `make check` passes on the skeleton |
| T02 | `infra/bootstrap` (state bucket) | Human applies it; `terraform init` in `envs/dev` succeeds |
| T03 | `modules/data` | `terraform plan` shows exactly one table with GSI1, 5/5 |
| T04 | `modules/auth` | Human signs up, signs in on the hosted UI, adds self to `admin` group |
| T05 | `common/` library (3.1 to 3.8) with unit tests | `make test` green for sections 10.1 and 10.2 |
| T06 | `build_backend.sh` and `modules/functions` (all ten functions, stub handlers allowed for the last two) | `make plan` shows ten functions, one archive, no wildcard IAM resources except X-Ray |
| T07 | Handlers 4.1 to 4.8 | Unit tests in 10.3 green |
| T08 | `modules/api` | `curl $API/api/health` returns 200; unauthenticated `POST /api/events` returns 401 |
| T09 | Manual API walk-through with a real ID token | create event, register, duplicate (409), full (409), unregister |
| T10 | `modules/frontend`, React app, scripts 8.2 to 8.5b | Browser loads CloudFront URL, login round-trip works, events render, no CORS errors |
| T11 | `modules/messaging` and `email_sender` (`EMAIL_MODE=log`) | Register produces a log line in `email_sender` logs |
| T12 | SES: verify sender and test recipient, switch `email_mode = "ses"` | Confirmation email arrives for the verified recipient |
| T13 | `modules/scheduling`, extend `create_event`, implement `send_reminder` | Event 25 minutes ahead with a temporary 24 h lead override: reminder arrives; then restore the 24 h lead |
| T14 | `modules/workflows`, `start_cancel` | Cancel notifies every registrant; event is `CANCELLED`; schedule is gone |
| T15 | `modules/observability` | Induced error produces an alarm email; an X-Ray trace opens |
| T16 | Hardening checklist (plan.md section 14), `trivy config`, IAM tightening | Checklist ticked; scan clean or suppressions documented |
| T17 | `infra/pipeline`, buildspecs, `ci_install_terraform.sh` | Push to `main`: plan, approval, apply, deploy, smoke all pass; a deliberately broken commit fails at Build and alerts |
| T18 | README, `docs/runbook.md`, ADRs for plan.md section 2 and this file's section 1 | Fresh-clone deploy works from the README alone |

---

## 12. Edge-case matrix

| Situation | Required behavior |
|---|---|
| Two students take the last seat at once | Exactly one succeeds; the other gets 409 `EVENT_FULL`. Never `seatsTaken > capacity` |
| Student double-clicks Register | Second request returns 409 `ALREADY_REGISTERED`; counter increments once |
| Event created less than 24 h before start | No schedule; `reminderStatus = SKIPPED`; event still created |
| Schedule creation fails | Event row removed; 500 returned; nothing orphaned |
| Reminder invoked twice | Second invocation returns `skipped: already_claimed` |
| Reminder publish fails midway | Claim released, exception raised, Scheduler retries then DLQ, alarm fires |
| Admin cancels an event that has no schedule | `DeleteSchedule` not-found is caught; workflow continues |
| Admin cancels twice | Both return 202; one execution |
| Event cancelled after a reminder was sent | Cancellation emails still go out; event ends `CANCELLED` |
| SES sandbox rejects an unverified recipient | Record retried 3 times, lands in outbox DLQ, alarm fires |
| Token is an access token | 401 `TOKEN_WRONG_TYPE` (checked in `get_user`) |
| `cognito:groups` arrives as `"[admin]"` | Parsed by `parse_groups`; admin routes work |
| Request body is base64 or invalid JSON | 400 `INVALID_JSON`; never a 500 |
| DynamoDB throttles under load | 503 `TRY_AGAIN`; write-throttle alarm fires |
| API returns 403 or 404 | Passed through CloudFront unchanged (no custom error pages) |
| Browser refreshes on `#/events/123` | Works, because routing is in the hash |

---

## 13. Items to confirm before or during the build

- [ ] `managed_login_version = 1` is accepted for the pool tier you choose (R4).
- [ ] `Scheduler.ResourceNotFoundException` is the exact error name in a real failed execution.
- [ ] SNS topic with `alias/aws/sns` delivers to the SQS queue in your account.
- [ ] Lambda concurrency quota in your account, before setting `reserved_concurrency`.
- [ ] `PriceClass_200` serves India from an edge near you.
- [ ] Current Terraform and AWS provider versions to pin.
- [ ] Everything marked **(verify)** in `plan.md` section 19.
