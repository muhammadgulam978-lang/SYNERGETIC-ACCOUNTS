from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Max, Q, Sum
from django.utils import timezone

from .models import Account, AuditEvent, Campus, CashSession, CloseChecklistItem, FinanceRequest, FinancialPeriod, Notification, RequestApproval, Voucher, VoucherLine

DEFAULT_ACCOUNTS = [
    ("1000", "Cash in Hand", "ASSET", "DEBIT"),
    ("1100", "Bank", "ASSET", "DEBIT"),
    ("1200", "Student Receivables", "ASSET", "DEBIT"),
    ("1300", "Inventory", "ASSET", "DEBIT"),
    ("1500", "Fixed Assets", "ASSET", "DEBIT"),
    ("2000", "Vendor Payables", "LIABILITY", "CREDIT"),
    ("2100", "Salary Payable", "LIABILITY", "CREDIT"),
    ("2200", "Tax and Deduction Payable", "LIABILITY", "CREDIT"),
    ("3000", "Capital Fund", "EQUITY", "CREDIT"),
    ("4000", "Fee Income", "INCOME", "CREDIT"),
    ("4100", "Other Income", "INCOME", "CREDIT"),
    ("5000", "Salary Expense", "EXPENSE", "DEBIT"),
    ("5100", "Operating Expense", "EXPENSE", "DEBIT"),
]


def ensure_setup(reference_date=None):
    day = reference_date or timezone.localdate()
    campus, _ = Campus.objects.get_or_create(code="HQ", defaults={"name": "Synergetic Solutions"})
    period, _ = FinancialPeriod.objects.get_or_create(
        start_date=day.replace(month=1, day=1),
        end_date=day.replace(month=12, day=31),
        defaults={"name": f"FY {day.year}", "status": "OPEN"},
    )
    accounts = {}
    for code, name, kind, normal in DEFAULT_ACCOUNTS:
        accounts[code], _ = Account.objects.get_or_create(code=code, defaults={"name": name, "account_type": kind, "normal_balance": normal})
    return campus, period, accounts


def next_voucher_number(voucher_type, voucher_date=None):
    day = voucher_date or timezone.localdate()
    prefix = f"{voucher_type}-{day.year}-"
    latest = Voucher.objects.filter(voucher_no__startswith=prefix).aggregate(last=Max("voucher_no"))["last"]
    sequence = int(latest.rsplit("-", 1)[-1]) + 1 if latest else 1
    return f"{prefix}{sequence:06d}"


def next_request_reference(request_type, request_date=None):
    day = request_date or timezone.localdate()
    prefix = f"{request_type[:3]}-{day.year}-"
    latest = FinanceRequest.objects.filter(reference__startswith=prefix).aggregate(last=Max("reference"))["last"]
    sequence = int(latest.rsplit("-", 1)[-1]) + 1 if latest else 1
    return f"{prefix}{sequence:06d}"


def validate_balanced(voucher):
    lines = list(voucher.lines.select_related("account"))
    if len(lines) < 2:
        raise ValidationError("A voucher requires at least two entry lines.")
    debit = sum((line.debit for line in lines), Decimal("0"))
    credit = sum((line.credit for line in lines), Decimal("0"))
    if debit <= 0 or debit != credit:
        raise ValidationError(f"Debit ({debit}) and credit ({credit}) must be equal and greater than zero.")
    if any(not line.account.is_active for line in lines):
        raise ValidationError("Inactive accounts cannot receive postings.")
    return debit


def validate_budget(voucher):
    for line in voucher.lines.filter(account__account_type="EXPENSE", debit__gt=0):
        budgets = line.account.budgets.filter(campus=voucher.campus, period=voucher.period)
        if voucher.cost_centre_id:
            budgets = budgets.filter(models.Q(cost_centre=voucher.cost_centre) | models.Q(cost_centre__isnull=True))
        budget = budgets.order_by("-cost_centre_id").first()
        if not budget:
            continue
        actual = VoucherLine.objects.filter(voucher__status="POSTED", voucher__campus=voucher.campus, voucher__period=voucher.period, account=line.account).aggregate(total=Sum("debit"))["total"] or Decimal("0")
        if actual + line.debit > budget.amount:
            raise ValidationError(f"Budget exceeded for {line.account}. Available: {budget.amount - actual}.")


def audit(action, actor=None, voucher=None, detail=""):
    return AuditEvent.objects.create(voucher=voucher, action=action, actor=actor, detail=detail)


def notify_admins(actor, voucher, subject, message):
    recipients = get_user_model().objects.filter(is_active=True).filter(Q(is_staff=True) | Q(is_superuser=True)).exclude(pk=actor.pk).distinct()
    Notification.objects.bulk_create([
        Notification(sender=actor, recipient=recipient, voucher=voucher, subject=subject, message=message)
        for recipient in recipients
    ])


def notify_creator(voucher, actor, subject, message):
    if voucher.created_by_id and voucher.created_by_id != actor.id:
        Notification.objects.create(sender=actor, recipient=voucher.created_by, voucher=voucher, subject=subject, message=message)


def notify_request_admins(item, actor, subject, message):
    recipients = get_user_model().objects.filter(is_active=True).filter(Q(is_staff=True) | Q(is_superuser=True)).exclude(pk=actor.pk).distinct()
    Notification.objects.bulk_create([Notification(sender=actor, recipient=user, subject=subject, message=f"{item.reference}: {message}") for user in recipients])


def notify_request_owner(item, actor, subject, message):
    if item.created_by_id != actor.id:
        Notification.objects.create(sender=actor, recipient=item.created_by, subject=subject, message=f"{item.reference}: {message}")


@transaction.atomic
def submit_finance_request(item, actor):
    if item.status not in {"DRAFT", "REJECTED"}:
        raise ValidationError("Only draft or rejected requests can be submitted.")
    item.status, item.submitted_at, item.rejection_reason = "SUBMITTED", timezone.now(), ""
    item.save(update_fields=["status", "submitted_at", "rejection_reason", "net_amount", "required_approval_level", "updated_at"])
    RequestApproval.objects.create(request=item, action="SUBMIT", actor=actor)
    notify_request_admins(item, actor, f"Review required: {item.reference}", item.title)


@transaction.atomic
def approve_finance_request(item, actor):
    if item.status not in {"SUBMITTED", "UNDER_REVIEW"}:
        raise ValidationError("This request is not awaiting approval.")
    if item.created_by_id == actor.id:
        raise ValidationError("The request maker cannot approve their own request.")
    if item.workflow_events.filter(action="APPROVE", actor=actor).exists():
        raise ValidationError("A different finance administrator must approve the next level.")
    level = item.current_approval_level + 1
    item.current_approval_level, item.reviewed_by, item.reviewed_at = level, actor, timezone.now()
    item.status = "APPROVED" if level >= item.required_approval_level else "UNDER_REVIEW"
    item.save(update_fields=["current_approval_level", "reviewed_by", "reviewed_at", "status", "net_amount", "required_approval_level", "updated_at"])
    RequestApproval.objects.create(request=item, action="APPROVE", level=level, actor=actor)
    if item.status == "APPROVED":
        notify_request_owner(item, actor, f"{item.reference} approved", "Your request is approved and ready for payment processing.")


@transaction.atomic
def reject_finance_request(item, actor, reason):
    if item.status not in {"SUBMITTED", "UNDER_REVIEW"}:
        raise ValidationError("This request is not awaiting review.")
    if not reason:
        raise ValidationError("A rejection reason is required.")
    item.status, item.rejection_reason, item.reviewed_by, item.reviewed_at = "REJECTED", reason, actor, timezone.now()
    item.current_approval_level = 0
    item.save(update_fields=["status", "rejection_reason", "reviewed_by", "reviewed_at", "current_approval_level", "net_amount", "required_approval_level", "updated_at"])
    RequestApproval.objects.create(request=item, action="REJECT", actor=actor, comment=reason)
    notify_request_owner(item, actor, f"{item.reference} needs correction", reason)


@transaction.atomic
def pay_and_post_finance_request(item, actor, payment_reference):
    if item.status != "APPROVED":
        raise ValidationError("Only approved requests can be paid and posted.")
    if not payment_reference:
        raise ValidationError("Payment reference is required.")
    period = FinancialPeriod.objects.filter(start_date__lte=item.request_date, end_date__gte=item.request_date).first()
    if not period or period.status == "LOCKED":
        raise ValidationError("No open financial period covers this request date.")
    voucher = Voucher.objects.create(voucher_no=next_voucher_number("PV", item.request_date), voucher_type="PV", voucher_date=item.request_date, period=period, campus=item.campus, cost_centre=item.cost_centre, party_name=item.payee_name or item.created_by.get_full_name() or item.created_by.username, narration=f"{item.get_request_type_display()}: {item.title}", source_module="Finance Requests", source_reference=item.reference, payment_method="Bank / cash", status="APPROVED", created_by=item.created_by, approved_by=actor, approved_at=timezone.now())
    lines = [VoucherLine(voucher=voucher, account=item.expense_account, description=item.title, debit=item.total_amount)]
    if item.withholding_amount:
        tax_account = Account.objects.filter(code="2200").first()
        if not tax_account:
            raise ValidationError("Tax and Deduction Payable account (2200) is missing.")
        lines.append(VoucherLine(voucher=voucher, account=tax_account, description="Withholding", credit=item.withholding_amount))
    lines.append(VoucherLine(voucher=voucher, account=item.payment_account, description=payment_reference, credit=item.net_amount))
    VoucherLine.objects.bulk_create(lines)
    post_voucher(voucher, actor)
    item.status, item.payment_reference, item.paid_at, item.linked_voucher = "POSTED", payment_reference, timezone.now(), voucher
    item.save(update_fields=["status", "payment_reference", "paid_at", "linked_voucher", "net_amount", "required_approval_level", "updated_at"])
    RequestApproval.objects.create(request=item, action="POST", actor=actor, comment=payment_reference)
    notify_request_owner(item, actor, f"{item.reference} paid and posted", f"Payment reference: {payment_reference}")


def ensure_close_checklist(period):
    labels = ["Review pending expense and payment requests", "Settle employee advances", "Reconcile all bank statement lines", "Post approved vouchers", "Verify supporting documents", "Review tax and withholding payable", "Confirm trial balance"]
    for label in labels:
        CloseChecklistItem.objects.get_or_create(period=period, label=label)


@transaction.atomic
def submit_voucher(voucher, actor):
    if voucher.status != "DRAFT":
        raise ValidationError("Only draft vouchers can be submitted.")
    validate_balanced(voucher)
    voucher.status = "SUBMITTED"
    voucher.submitted_at = timezone.now()
    voucher.save(update_fields=["status", "submitted_at"])
    audit("SUBMIT", actor, voucher)
    notify_admins(actor, voucher, f"Review required: {voucher.voucher_no}", f"{actor.get_full_name() or actor.username} submitted {voucher.voucher_no} for review and approval.")


@transaction.atomic
def approve_voucher(voucher, actor):
    if voucher.status != "SUBMITTED":
        raise ValidationError("Only pending vouchers can be approved.")
    if voucher.created_by_id == actor.id:
        raise ValidationError("The voucher maker cannot approve their own voucher.")
    amount = validate_balanced(voucher)
    validate_budget(voucher)
    rules = voucher.campus.approvalrule_set.filter(is_active=True, minimum_amount__lte=amount).filter(models.Q(voucher_type="") | models.Q(voucher_type=voucher.voucher_type)).filter(models.Q(maximum_amount__isnull=True) | models.Q(maximum_amount__gte=amount))
    rule = rules.order_by("-approval_level").first()
    if rule and not actor.is_superuser and not actor.groups.filter(name=rule.role_name).exists():
        raise ValidationError(f"This amount requires approval by {rule.role_name}.")
    voucher.status = "APPROVED"
    voucher.approved_by = actor
    voucher.approved_at = timezone.now()
    voucher.save(update_fields=["status", "approved_by", "approved_at"])
    audit("APPROVE", actor, voucher)
    notify_creator(voucher, actor, f"{voucher.voucher_no} approved", "Your submission has been approved and is ready for posting.")


@transaction.atomic
def post_voucher(voucher, actor):
    if voucher.status != "APPROVED":
        raise ValidationError("Only approved vouchers can be posted.")
    if voucher.period.status == "LOCKED":
        raise ValidationError("This financial period is locked.")
    validate_balanced(voucher)
    validate_budget(voucher)
    voucher.status = "POSTED"
    voucher.posted_by = actor
    voucher.posted_at = timezone.now()
    voucher.save(update_fields=["status", "posted_by", "posted_at"])
    cash_line = voucher.lines.filter(account__code="1000").first()
    if cash_line:
        session = CashSession.objects.filter(campus=voucher.campus, session_date=voucher.voucher_date, status="OPEN").first()
        if session:
            CashSession.objects.filter(pk=session.pk).update(receipts=F("receipts") + cash_line.debit, payments=F("payments") + cash_line.credit)
    audit("POST", actor, voucher)
    notify_creator(voucher, actor, f"{voucher.voucher_no} posted", "Your approved submission has been posted to the general ledger.")


@transaction.atomic
def reverse_voucher(voucher, actor, reason):
    if voucher.status != "POSTED":
        raise ValidationError("Only posted vouchers can be reversed.")
    if voucher.period.status == "LOCKED":
        raise ValidationError("A voucher in a locked period cannot be reversed.")
    reversal = Voucher.objects.create(voucher_no=next_voucher_number("JV", voucher.voucher_date), voucher_type="JV", voucher_date=voucher.voucher_date, period=voucher.period, campus=voucher.campus, cost_centre=voucher.cost_centre, party_name=voucher.party_name, narration=f"Reversal of {voucher.voucher_no}: {reason}", source_module="Reversal", source_reference=voucher.voucher_no, status="APPROVED", created_by=actor, approved_by=actor, approved_at=timezone.now(), reversal_of=voucher)
    VoucherLine.objects.bulk_create([VoucherLine(voucher=reversal, account=line.account, description=line.description, debit=line.credit, credit=line.debit) for line in voucher.lines.all()])
    post_voucher(reversal, actor)
    voucher.status = "REVERSED"
    voucher.reversed_by = actor
    voucher.reversed_at = timezone.now()
    voucher.save(update_fields=["status", "reversed_by", "reversed_at"])
    audit("REVERSE", actor, voucher, f"{reason}. Reversal: {reversal.voucher_no}")
    notify_creator(voucher, actor, f"{voucher.voucher_no} reversed", f"A correction was posted as {reversal.voucher_no}. Reason: {reason}")
    return reversal
