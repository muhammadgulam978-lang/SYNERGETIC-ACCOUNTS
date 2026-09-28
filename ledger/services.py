from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Max, Sum
from django.utils import timezone

from .models import Account, AuditEvent, Campus, CashSession, FinancialPeriod, Voucher, VoucherLine

DEFAULT_ACCOUNTS = [
    ("1000", "Cash in hand", "ASSET", "DEBIT"),
    ("1100", "Business bank", "ASSET", "DEBIT"),
    ("1200", "Accounts receivable", "ASSET", "DEBIT"),
    ("1300", "Inventory", "ASSET", "DEBIT"),
    ("1500", "Fixed assets", "ASSET", "DEBIT"),
    ("2000", "Accounts payable", "LIABILITY", "CREDIT"),
    ("2100", "Payroll payable", "LIABILITY", "CREDIT"),
    ("2200", "Tax payable", "LIABILITY", "CREDIT"),
    ("3000", "Owner equity", "EQUITY", "CREDIT"),
    ("4000", "Sales and service income", "INCOME", "CREDIT"),
    ("4100", "Other income", "INCOME", "CREDIT"),
    ("5000", "Payroll expense", "EXPENSE", "DEBIT"),
    ("5100", "Operating expense", "EXPENSE", "DEBIT"),
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


@transaction.atomic
def submit_voucher(voucher, actor):
    if voucher.status != "DRAFT":
        raise ValidationError("Only draft vouchers can be submitted.")
    validate_balanced(voucher)
    voucher.status = "SUBMITTED"
    voucher.submitted_at = timezone.now()
    voucher.save(update_fields=["status", "submitted_at"])
    audit("SUBMIT", actor, voucher)


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
    return reversal
