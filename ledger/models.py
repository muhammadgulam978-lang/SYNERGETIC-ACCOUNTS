from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum


class Campus(models.Model):
    name = models.CharField(max_length=120, unique=True)
    code = models.CharField(max_length=20, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.code} · {self.name}"


class CostCentre(models.Model):
    campus = models.ForeignKey(Campus, on_delete=models.PROTECT, related_name="cost_centres")
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=30)
    department = models.CharField(max_length=120, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["campus__name", "name"]
        constraints = [models.UniqueConstraint(fields=["campus", "code"], name="uniq_cost_centre_code")]

    def __str__(self):
        return f"{self.code} · {self.name}"


class FinancialPeriod(models.Model):
    STATUS_CHOICES = [("OPEN", "Open"), ("SOFT_CLOSED", "Soft closed"), ("LOCKED", "Locked")]
    name = models.CharField(max_length=80)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="OPEN")
    locked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-start_date"]
        constraints = [models.UniqueConstraint(fields=["start_date", "end_date"], name="uniq_period_range")]

    def __str__(self):
        return self.name


class Account(models.Model):
    TYPE_CHOICES = [("ASSET", "Asset"), ("LIABILITY", "Liability"), ("INCOME", "Income"), ("EXPENSE", "Expense"), ("EQUITY", "Equity")]
    NORMAL_CHOICES = [("DEBIT", "Debit"), ("CREDIT", "Credit")]
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=140)
    account_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    normal_balance = models.CharField(max_length=10, choices=NORMAL_CHOICES)
    parent = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="children")
    campus = models.ForeignKey(Campus, on_delete=models.PROTECT, null=True, blank=True, related_name="accounts")
    tax_treatment = models.CharField(max_length=80, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} · {self.name}"


class ApprovalRule(models.Model):
    voucher_type = models.CharField(max_length=3, blank=True)
    role_name = models.CharField(max_length=80)
    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, null=True, blank=True)
    department = models.CharField(max_length=120, blank=True)
    minimum_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    maximum_amount = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    approval_level = models.PositiveSmallIntegerField(default=1)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["approval_level", "minimum_amount"]

    def __str__(self):
        return f"Level {self.approval_level} · {self.role_name}"


class Voucher(models.Model):
    TYPE_CHOICES = [("RV", "Receipt voucher"), ("PV", "Payment voucher"), ("JV", "Journal voucher"), ("CV", "Contra voucher"), ("CN", "Credit note"), ("DN", "Debit note")]
    STATUS_CHOICES = [("DRAFT", "Draft"), ("SUBMITTED", "Pending approval"), ("APPROVED", "Approved"), ("POSTED", "Posted"), ("REJECTED", "Rejected"), ("REVERSED", "Reversed")]
    voucher_no = models.CharField(max_length=50, unique=True)
    voucher_type = models.CharField(max_length=3, choices=TYPE_CHOICES)
    voucher_date = models.DateField(default=date.today)
    period = models.ForeignKey(FinancialPeriod, on_delete=models.PROTECT, related_name="vouchers")
    campus = models.ForeignKey(Campus, on_delete=models.PROTECT, related_name="vouchers")
    cost_centre = models.ForeignKey(CostCentre, on_delete=models.PROTECT, null=True, blank=True, related_name="vouchers")
    party_name = models.CharField(max_length=160, blank=True, help_text="Customer, vendor or employee name")
    narration = models.TextField()
    source_module = models.CharField(max_length=80, default="Manual")
    source_reference = models.CharField(max_length=100, blank=True)
    payment_method = models.CharField(max_length=40, blank=True)
    attachment = models.FileField(upload_to="vouchers/%Y/%m/", null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="DRAFT")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_vouchers")
    created_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="approved_vouchers")
    approved_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="posted_vouchers")
    posted_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="reversed_vouchers")
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversal_of = models.OneToOneField("self", on_delete=models.PROTECT, null=True, blank=True, related_name="reversal_voucher")
    rejection_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-voucher_date", "-id"]
        indexes = [models.Index(fields=["status", "voucher_date"]), models.Index(fields=["campus", "voucher_date"]), models.Index(fields=["source_module", "source_reference"])]

    @property
    def total_debit(self):
        return self.lines.aggregate(total=Sum("debit"))["total"] or Decimal("0")

    @property
    def total_credit(self):
        return self.lines.aggregate(total=Sum("credit"))["total"] or Decimal("0")

    @property
    def total_amount(self):
        return self.total_debit

    def save(self, *args, **kwargs):
        if self.pk:
            previous = Voucher.objects.filter(pk=self.pk).values("status", "voucher_no", "voucher_type", "voucher_date", "period_id", "campus_id", "cost_centre_id", "party_name", "narration", "source_module", "source_reference", "payment_method").first()
            if previous and previous["status"] in {"POSTED", "REVERSED"}:
                immutable = set(previous) - {"status"}
                if any(previous[field] != getattr(self, field) for field in immutable):
                    raise ValidationError("Posted vouchers are immutable. Create a reversal entry instead.")
                if previous["status"] == "REVERSED" and self.status != "REVERSED":
                    raise ValidationError("A reversed voucher cannot be reopened.")
                if previous["status"] == "POSTED" and self.status not in {"POSTED", "REVERSED"}:
                    raise ValidationError("A posted voucher can only be reversed.")
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.voucher_no


class VoucherLine(models.Model):
    voucher = models.ForeignKey(Voucher, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="voucher_lines")
    description = models.CharField(max_length=255, blank=True)
    debit = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=16, decimal_places=2, default=0)

    class Meta:
        ordering = ["id"]
        constraints = [models.CheckConstraint(condition=(models.Q(debit__gt=0, credit=0) | models.Q(credit__gt=0, debit=0)), name="voucher_line_one_positive_side")]

    def save(self, *args, **kwargs):
        if self.voucher_id and self.voucher.status in {"POSTED", "REVERSED"}:
            raise ValidationError("Lines on posted vouchers are immutable.")
        return super().save(*args, **kwargs)


class AuditEvent(models.Model):
    ACTION_CHOICES = [(x, x.title()) for x in ["CREATE", "SUBMIT", "APPROVE", "REJECT", "POST", "REVERSE", "EXPORT", "PERIOD", "RECONCILE", "UPLOAD"]]
    voucher = models.ForeignKey(Voucher, on_delete=models.PROTECT, null=True, blank=True, related_name="audit_events")
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def delete(self, *args, **kwargs):
        raise ValidationError("Financial audit events are immutable.")


class Budget(models.Model):
    campus = models.ForeignKey(Campus, on_delete=models.PROTECT, related_name="budgets")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="budgets")
    period = models.ForeignKey(FinancialPeriod, on_delete=models.PROTECT, related_name="budgets")
    cost_centre = models.ForeignKey(CostCentre, on_delete=models.PROTECT, null=True, blank=True, related_name="budgets")
    department = models.CharField(max_length=120, blank=True)
    project = models.CharField(max_length=120, blank=True)
    amount = models.DecimalField(max_digits=16, decimal_places=2)

    class Meta:
        ordering = ["period__start_date", "account__code"]


class BankAccount(models.Model):
    campus = models.ForeignKey(Campus, on_delete=models.PROTECT, related_name="bank_accounts")
    ledger_account = models.OneToOneField(Account, on_delete=models.PROTECT, related_name="bank_profile")
    bank_name = models.CharField(max_length=120)
    account_title = models.CharField(max_length=140)
    account_number = models.CharField(max_length=80)
    currency = models.CharField(max_length=10, default="PKR")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        suffix = self.account_number[-4:] if self.account_number else ""
        return f"{self.bank_name} · •••• {suffix}"


class BankStatementLine(models.Model):
    bank_account = models.ForeignKey(BankAccount, on_delete=models.CASCADE, related_name="statement_lines")
    transaction_date = models.DateField()
    reference = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True)
    debit = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    matched_voucher = models.ForeignKey(Voucher, on_delete=models.PROTECT, null=True, blank=True, related_name="bank_matches")
    reconciled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    reconciled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-transaction_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["bank_account", "transaction_date", "reference"], name="uniq_bank_statement_ref"), models.CheckConstraint(condition=(models.Q(debit__gt=0, credit=0) | models.Q(credit__gt=0, debit=0)), name="bank_line_one_positive_side")]


class CashSession(models.Model):
    STATUS_CHOICES = [("OPEN", "Open"), ("PENDING", "Pending approval"), ("CLOSED", "Closed")]
    campus = models.ForeignKey(Campus, on_delete=models.PROTECT, related_name="cash_sessions")
    counter_name = models.CharField(max_length=100)
    session_date = models.DateField(default=date.today)
    opening_balance = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    receipts = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    payments = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    physical_cash = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    variance_reason = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="OPEN")
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="opened_cash_sessions")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="approved_cash_sessions")
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-session_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["campus", "counter_name", "session_date"], name="uniq_cash_session_day")]

    @property
    def expected_cash(self):
        return self.opening_balance + self.receipts - self.payments

    @property
    def variance(self):
        return self.physical_cash - self.expected_cash if self.physical_cash is not None else None


class Document(models.Model):
    CATEGORY_CHOICES = [("INVOICE", "Invoice"), ("RECEIPT", "Receipt"), ("STATEMENT", "Bank statement"), ("CONTRACT", "Contract"), ("TAX", "Tax document"), ("OTHER", "Other")]
    title = models.CharField(max_length=180)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default="OTHER")
    file = models.FileField(upload_to="documents/%Y/%m/")
    voucher = models.ForeignKey(Voucher, on_delete=models.SET_NULL, null=True, blank=True, related_name="documents")
    notes = models.TextField(blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    @property
    def filename(self):
        return self.file.name.rsplit("/", 1)[-1]
