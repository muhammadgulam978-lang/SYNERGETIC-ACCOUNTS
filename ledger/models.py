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


class Notification(models.Model):
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sent_account_notifications")
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="received_account_notifications")
    subject = models.CharField(max_length=180)
    message = models.TextField()
    voucher = models.ForeignKey(Voucher, on_delete=models.SET_NULL, null=True, blank=True, related_name="notifications")
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["recipient", "is_read", "created_at"])]

    def __str__(self):
        return self.subject


class FinanceRequest(models.Model):
    TYPE_CHOICES = [("EXPENSE", "Expense claim"), ("BILL", "Bill / payment request"), ("PURCHASE", "Purchase request"), ("ADVANCE", "Employee advance")]
    STATUS_CHOICES = [("DRAFT", "Draft"), ("SUBMITTED", "Submitted"), ("UNDER_REVIEW", "Under review"), ("APPROVED", "Approved"), ("REJECTED", "Rejected"), ("PAID", "Paid / issued"), ("SETTLED", "Settled"), ("POSTED", "Posted")]
    PRIORITY_CHOICES = [("NORMAL", "Normal"), ("HIGH", "High"), ("URGENT", "Urgent")]
    reference = models.CharField(max_length=40, unique=True)
    request_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="DRAFT")
    title = models.CharField(max_length=180)
    purpose = models.TextField()
    payee_name = models.CharField(max_length=160, blank=True)
    department = models.CharField(max_length=120, blank=True)
    campus = models.ForeignKey(Campus, on_delete=models.PROTECT, related_name="finance_requests")
    cost_centre = models.ForeignKey(CostCentre, on_delete=models.PROTECT, null=True, blank=True, related_name="finance_requests")
    expense_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="expense_requests")
    payment_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="payment_requests")
    request_date = models.DateField(default=date.today)
    due_date = models.DateField(null=True, blank=True)
    gross_amount = models.DecimalField(max_digits=16, decimal_places=2)
    tax_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    withholding_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    net_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default="NORMAL")
    payment_reference = models.CharField(max_length=120, blank=True)
    attachment = models.FileField(upload_to="requests/%Y/%m/", null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="finance_requests")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="reviewed_finance_requests")
    current_approval_level = models.PositiveSmallIntegerField(default=0)
    required_approval_level = models.PositiveSmallIntegerField(default=1)
    rejection_reason = models.TextField(blank=True)
    linked_voucher = models.OneToOneField(Voucher, on_delete=models.PROTECT, null=True, blank=True, related_name="finance_request")
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["request_type", "status"]), models.Index(fields=["created_by", "status"]), models.Index(fields=["due_date"])]

    def save(self, *args, **kwargs):
        self.net_amount = (self.gross_amount or 0) + (self.tax_amount or 0) - (self.withholding_amount or 0)
        total = (self.gross_amount or 0) + (self.tax_amount or 0)
        self.required_approval_level = 3 if total >= 500000 else 2 if total >= 100000 else 1
        super().save(*args, **kwargs)

    @property
    def total_amount(self):
        return self.gross_amount + self.tax_amount

    def __str__(self):
        return f"{self.reference} · {self.title}"


class FinanceRequestLine(models.Model):
    request = models.ForeignKey(FinanceRequest, on_delete=models.CASCADE, related_name="items")
    description = models.CharField(max_length=220)
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=1)
    unit_amount = models.DecimalField(max_digits=16, decimal_places=2)

    @property
    def total(self):
        return self.quantity * self.unit_amount


class RequestAttachment(models.Model):
    request = models.ForeignKey(FinanceRequest, on_delete=models.CASCADE, related_name="attachments")
    title = models.CharField(max_length=180)
    file = models.FileField(upload_to="request-files/%Y/%m/")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    uploaded_at = models.DateTimeField(auto_now_add=True)


class RequestApproval(models.Model):
    ACTION_CHOICES = [("SUBMIT", "Submitted"), ("REVIEW", "Review started"), ("APPROVE", "Approved"), ("REJECT", "Rejected"), ("PAY", "Paid / issued"), ("SETTLE", "Settled"), ("POST", "Posted"), ("COMMENT", "Comment")]
    request = models.ForeignKey(FinanceRequest, on_delete=models.CASCADE, related_name="workflow_events")
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    level = models.PositiveSmallIntegerField(default=0)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class RequestComment(models.Model):
    request = models.ForeignKey(FinanceRequest, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]


class RecurringExpense(models.Model):
    FREQUENCY_CHOICES = [("MONTHLY", "Monthly"), ("QUARTERLY", "Quarterly"), ("YEARLY", "Yearly")]
    title = models.CharField(max_length=180)
    request_type = models.CharField(max_length=20, choices=[("EXPENSE", "Expense"), ("BILL", "Bill / payment")], default="BILL")
    purpose = models.TextField()
    payee_name = models.CharField(max_length=160, blank=True)
    campus = models.ForeignKey(Campus, on_delete=models.PROTECT)
    cost_centre = models.ForeignKey(CostCentre, on_delete=models.PROTECT, null=True, blank=True)
    expense_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="recurring_expenses")
    payment_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="recurring_payments")
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    frequency = models.CharField(max_length=12, choices=FREQUENCY_CHOICES, default="MONTHLY")
    next_due_date = models.DateField()
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    last_generated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["next_due_date"]


class TaxRule(models.Model):
    name = models.CharField(max_length=120, unique=True)
    rate = models.DecimalField(max_digits=6, decimal_places=3)
    withholding_rate = models.DecimalField(max_digits=6, decimal_places=3, default=0)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class CloseChecklistItem(models.Model):
    period = models.ForeignKey(FinancialPeriod, on_delete=models.CASCADE, related_name="close_items")
    label = models.CharField(max_length=200)
    is_complete = models.BooleanField(default=False)
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [models.UniqueConstraint(fields=["period", "label"], name="uniq_close_period_label")]


class ImportBatch(models.Model):
    STATUS_CHOICES = [("PENDING", "Pending"), ("COMPLETED", "Completed"), ("FAILED", "Failed")]
    file = models.FileField(upload_to="imports/%Y/%m/")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PENDING")
    imported_count = models.PositiveIntegerField(default=0)
    error_log = models.TextField(blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
