import hashlib
import json
import logging
import uuid
from datetime import date

import fabric.functions as fn

udf = fn.UserDataFunctions()


def _uuid(value: str, field: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise fn.UserThrownError(f"{field} must be a valid identifier.") from error


def _request_hash(resource_key: str, context_option_id: str, effective_from: str, reason: str) -> str:
    payload = json.dumps(
        [resource_key, context_option_id, effective_from, reason],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@udf.connection(argName="sqlDB", alias="BusinessContextSql")
@udf.context(argName="invocationContext")
@udf.function()
def assign_business_context(
    sqlDB: fn.FabricSqlConnection,
    invocationContext: fn.UserDataFunctionContext,
    resourceKey: str,
    contextOptionId: str,
    effectiveFrom: str,
    reason: str,
    expectedVersion: int,
) -> str:
    """Assign an authorized business context to an unmapped resource."""
    resource_key = resourceKey.strip()
    context_option_id = _uuid(contextOptionId, "Context option")
    actor = invocationContext.executing_user
    actor_object_id = _uuid(actor["Oid"], "Invoking user")
    tenant_id = _uuid(actor["TenantId"], "Invoking tenant")

    if not resource_key or len(resource_key) > 128:
        raise fn.UserThrownError("Select a valid resource from the report.")
    if not isinstance(effectiveFrom, str):
        raise fn.UserThrownError("Enter an effective date.")
    try:
        effective_date = date.fromisoformat(effectiveFrom).isoformat()
    except ValueError as error:
        raise fn.UserThrownError("Effective date must use YYYY-MM-DD.") from error
    if effective_date != effectiveFrom:
        raise fn.UserThrownError("Effective date must use YYYY-MM-DD.")
    if not isinstance(reason, str) or not 10 <= len(reason.strip()) <= 1000:
        raise fn.UserThrownError("Provide a reason between 10 and 1,000 characters.")
    if not isinstance(expectedVersion, int) or expectedVersion < 1:
        raise fn.UserThrownError("Refresh the resource row and try again.")

    request_hash = _request_hash(resource_key, context_option_id, effective_date, reason.strip())
    invocation_id = str(uuid.uuid4())
    connection = None
    cursor = None
    try:
        connection = sqlDB.connect()
        cursor = connection.cursor()
        cursor.execute(
            "EXEC dbo.AssignBusinessContext ?, ?, ?, ?, ?, ?, ?, ?, ?",
            tenant_id,
            actor_object_id,
            invocation_id,
            request_hash,
            resource_key,
            context_option_id,
            effective_date,
            reason.strip(),
            expectedVersion,
        )
        result = cursor.fetchone()
        if result and result[0] in {"saved", "already_saved"}:
            connection.commit()
        else:
            connection.rollback()
    except Exception as error:
        if connection is not None:
            connection.rollback()
        logging.exception("Business context assignment failed; invocation=%s", invocation_id)
        raise fn.UserThrownError("The assignment could not be saved. Refresh the report and try again.") from error
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    messages = {
        "saved": "Business context assigned.",
        "already_saved": "This assignment was already saved.",
        "invocation_conflict": "This request ID was already used for a different assignment.",
        "resource_not_assignable": "This resource is no longer eligible for manual assignment.",
        "stale_version": "This resource changed since you opened it. Refresh the report and try again.",
        "backdated_assignment": "Assignments must take effect today or later.",
        "effective_date_conflict": "A newer or same-date assignment already exists. Refresh the report.",
        "not_authorized": "You are not authorized for this resource and business context.",
    }
    status = result[0] if result else ""
    if status not in {"saved", "already_saved"}:
        raise fn.UserThrownError(messages.get(status, "The assignment was not accepted."))
    return messages[status]
