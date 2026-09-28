import csv
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .forms import AccountForm, ApprovalRuleForm, BankAccountForm, BankStatementLineForm, BudgetForm, CampusForm, CashSessionForm, CostCentreForm, DocumentForm, FinancialPeriodForm, VoucherForm, VoucherLineFormSet
from .models import Account, ApprovalRule, AuditEvent, BankAccount, BankStatementLine, Budget, Campus, CashSession, Document, FinancialPeriod, Voucher, VoucherLine
from .services import approve_voucher, audit, ensure_setup, next_voucher_number, post_voucher, reverse_voucher, submit_voucher, validate_balanced


def _money(value):
    return value or Decimal("0")


def _vouchers(request):
    qs = Voucher.objects.select_related("campus", "period", "created_by", "approved_by").prefetch_related("lines__account")
    search = request.GET.get("q", "").strip()
    if search:
        qs = qs.filter(Q(voucher_no__icontains=search) | Q(narration__icontains=search) | Q(source_reference__icontains=search) | Q(party_name__icontains=search))
    for key, field in [("status", "status"), ("type", "voucher_type"), ("campus", "campus_id")]:
        if request.GET.get(key):
            qs = qs.filter(**{field: request.GET[key]})
    if request.GET.get("start"):
        qs = qs.filter(voucher_date__gte=request.GET["start"])
    if request.GET.get("end"):
        qs = qs.filter(voucher_date__lte=request.GET["end"])
    return qs


@login_required
def dashboard(request):
    ensure_setup()
    section = request.GET.get("section", "dashboard")
    allowed = {"dashboard", "vouchers", "accounts", "parties", "cash-bank", "budgets", "documents", "reports", "audit", "settings"}
    section = section if section in allowed else "dashboard"
    posted = VoucherLine.objects.filter(voucher__status="POSTED")
    debit_minus_credit = lambda q: _money(q.aggregate(v=Sum("debit") - Sum("credit"))["v"])
    credit_minus_debit = lambda q: _money(q.aggregate(v=Sum("credit") - Sum("debit"))["v"])
    cash = debit_minus_credit(posted.filter(account__code="1000"))
    bank = debit_minus_credit(posted.filter(account__code="1100"))
    receivables = debit_minus_credit(posted.filter(account__code="1200"))
    payables = credit_minus_debit(posted.filter(account__code="2000"))
    income = credit_minus_debit(posted.filter(account__account_type="INCOME"))
    expenses = debit_minus_credit(posted.filter(account__account_type="EXPENSE"))
    voucher_qs = _vouchers(request)

    account_rows, trial_debit, trial_credit = [], Decimal("0"), Decimal("0")
    for account in Account.objects.annotate(debits=Sum("voucher_lines__debit", filter=Q(voucher_lines__voucher__status="POSTED")), credits=Sum("voucher_lines__credit", filter=Q(voucher_lines__voucher__status="POSTED"))):
        debit, credit = _money(account.debits), _money(account.credits)
        balance = debit - credit if account.normal_balance == "DEBIT" else credit - debit
        account_rows.append({"account": account, "debit": debit, "credit": credit, "balance": balance})
        trial_debit += debit
        trial_credit += credit

    budget_rows = []
    for budget in Budget.objects.select_related("campus", "account", "period", "cost_centre"):
        actual = debit_minus_credit(posted.filter(voucher__campus=budget.campus, voucher__period=budget.period, account=budget.account))
        budget_rows.append({"budget": budget, "actual": actual, "remaining": budget.amount - actual})

    parties = Voucher.objects.exclude(party_name="").values("party_name").annotate(debits=Sum("lines__debit", filter=Q(status="POSTED")), credits=Sum("lines__credit", filter=Q(status="POSTED")), vouchers=Count("id", distinct=True)).order_by("party_name")
    context = {
        "section": section,
        "summary": {"cash": cash, "bank": bank, "income": income, "expenses": expenses, "receivables": receivables, "payables": payables, "pending": Voucher.objects.filter(status="SUBMITTED").count()},
        "liquidity": cash + bank,
        "net_surplus": income - expenses,
        "recent_vouchers": Voucher.objects.select_related("campus").all()[:8],
        "vouchers": voucher_qs[:200], "voucher_count": voucher_qs.count(),
        "voucher_statuses": Voucher.STATUS_CHOICES, "voucher_types": Voucher.TYPE_CHOICES,
        "campuses": Campus.objects.all(), "account_rows": account_rows,
        "trial_debit": trial_debit, "trial_credit": trial_credit,
        "party_rows": parties[:200],
        "cash_sessions": CashSession.objects.select_related("campus", "opened_by", "approved_by")[:100],
        "bank_accounts": BankAccount.objects.select_related("campus", "ledger_account"),
        "bank_lines": BankStatementLine.objects.select_related("bank_account", "matched_voucher")[:100],
        "posted_vouchers": Voucher.objects.filter(status="POSTED")[:200],
        "budget_rows": budget_rows, "total_budget": _money(Budget.objects.aggregate(v=Sum("amount"))["v"]),
        "documents": Document.objects.select_related("voucher", "uploaded_by")[:200],
        "periods": FinancialPeriod.objects.all(), "approval_rules": ApprovalRule.objects.select_related("campus"),
        "audit_events": AuditEvent.objects.select_related("voucher", "actor")[:200],
        "unmatched_bank_count": BankStatementLine.objects.filter(matched_voucher__isnull=True).count(),
    }
    return render(request, "ledger/dashboard.html", context)


@login_required
def voucher_create(request):
    ensure_setup()
    draft = Voucher(created_by=request.user)
    if request.method == "POST":
        form = VoucherForm(request.POST, request.FILES)
        formset = VoucherLineFormSet(request.POST, instance=draft)
        if form.is_valid() and formset.is_valid():
            try:
                with transaction.atomic():
                    voucher = form.save(commit=False)
                    voucher.created_by = request.user
                    voucher.voucher_no = next_voucher_number(voucher.voucher_type, voucher.voucher_date)
                    voucher.save()
                    formset.instance = voucher
                    formset.save()
                    validate_balanced(voucher)
                    audit("CREATE", request.user, voucher)
                messages.success(request, f"{voucher.voucher_no} saved as a balanced draft.")
                return redirect("voucher-detail", pk=voucher.pk)
            except ValidationError as exc:
                form.add_error(None, "; ".join(exc.messages))
    else:
        form = VoucherForm(initial={"source_module": "Manual", "voucher_date": timezone.localdate()})
        formset = VoucherLineFormSet(instance=draft)
    return render(request, "ledger/voucher_form.html", {"form": form, "formset": formset})


@login_required
def voucher_detail(request, pk):
    voucher = get_object_or_404(Voucher.objects.select_related("campus", "period", "cost_centre", "created_by", "approved_by", "posted_by", "reversal_of").prefetch_related("lines__account", "audit_events__actor"), pk=pk)
    return render(request, "ledger/voucher_detail.html", {"voucher": voucher})


@login_required
def voucher_action(request, pk, action):
    voucher = get_object_or_404(Voucher, pk=pk)
    if request.method != "POST":
        return redirect("voucher-detail", pk=pk)
    try:
        if action == "submit": submit_voucher(voucher, request.user)
        elif action == "approve": approve_voucher(voucher, request.user)
        elif action == "post": post_voucher(voucher, request.user)
        elif action == "reject":
            if voucher.status != "SUBMITTED": raise ValidationError("Only pending vouchers can be rejected.")
            voucher.status, voucher.rejection_reason, voucher.approved_by = "REJECTED", request.POST.get("reason", "").strip(), request.user
            voucher.save(update_fields=["status", "rejection_reason", "approved_by"])
            audit("REJECT", request.user, voucher, voucher.rejection_reason)
        elif action == "reverse": reverse_voucher(voucher, request.user, request.POST.get("reason", "").strip() or "Correction")
        else: raise ValidationError("Unknown workflow action.")
        messages.success(request, "Voucher workflow updated successfully.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("voucher-detail", pk=pk)


FORM_CONFIG = {
    "account": (AccountForm, "accounts", "Ledger account"), "campus": (CampusForm, "settings", "Branch / campus"),
    "cost-centre": (CostCentreForm, "settings", "Cost centre"), "period": (FinancialPeriodForm, "settings", "Financial period"),
    "budget": (BudgetForm, "budgets", "Budget"), "bank-account": (BankAccountForm, "cash-bank", "Bank account"),
    "bank-line": (BankStatementLineForm, "cash-bank", "Bank statement line"), "cash-session": (CashSessionForm, "cash-bank", "Cash session"),
    "approval-rule": (ApprovalRuleForm, "settings", "Approval rule"),
}


@login_required
def record_create(request, kind):
    config = FORM_CONFIG.get(kind)
    if not config: return redirect("ledger-dashboard")
    FormClass, section, label = config
    form = FormClass(request.POST or None, request.FILES or None)
    if form.is_valid():
        obj = form.save(commit=False)
        if kind == "cash-session": obj.opened_by = request.user
        obj.save()
        messages.success(request, f"{label} saved.")
        return redirect(f"{reverse('ledger-dashboard')}?section={section}")
    return render(request, "ledger/record_form.html", {"form": form, "label": label, "section": section})


@login_required
def document_upload(request):
    form = DocumentForm(request.POST or None, request.FILES or None)
    if form.is_valid():
        document = form.save(commit=False)
        document.uploaded_by = request.user
        document.save()
        audit("UPLOAD", request.user, document.voucher, f"Uploaded {document.title}")
        messages.success(request, "File uploaded to the document vault.")
        return redirect(f"{reverse('ledger-dashboard')}?section=documents")
    return render(request, "ledger/record_form.html", {"form": form, "label": "Upload financial document", "section": "documents"})


@login_required
def bank_match(request, pk):
    line = get_object_or_404(BankStatementLine, pk=pk)
    if request.method == "POST":
        voucher = get_object_or_404(Voucher, pk=request.POST.get("voucher_id"), status="POSTED")
        line.matched_voucher, line.reconciled_by, line.reconciled_at = voucher, request.user, timezone.now()
        line.save(update_fields=["matched_voucher", "reconciled_by", "reconciled_at"])
        audit("RECONCILE", request.user, voucher, f"Matched bank line {line.reference}")
    return redirect(f"{reverse('ledger-dashboard')}?section=cash-bank")


@login_required
def cash_action(request, pk, action):
    session = get_object_or_404(CashSession, pk=pk)
    try:
        if request.method == "POST" and action == "submit" and session.status == "OPEN":
            session.physical_cash = Decimal(request.POST.get("physical_cash", "0"))
            session.variance_reason = request.POST.get("variance_reason", "").strip()
            if session.variance and not session.variance_reason: raise ValidationError("Variance reason is required.")
            session.status = "PENDING"; session.save(update_fields=["physical_cash", "variance_reason", "status"])
        elif request.method == "POST" and action == "approve" and session.status == "PENDING":
            if session.opened_by_id == request.user.id: raise ValidationError("The cashier cannot approve their own closing.")
            session.status, session.approved_by, session.closed_at = "CLOSED", request.user, timezone.now()
            session.save(update_fields=["status", "approved_by", "closed_at"])
    except ValidationError as exc: messages.error(request, "; ".join(exc.messages))
    return redirect(f"{reverse('ledger-dashboard')}?section=cash-bank")


@login_required
def period_action(request, pk):
    period = get_object_or_404(FinancialPeriod, pk=pk)
    if request.method == "POST" and request.POST.get("status") in dict(FinancialPeriod.STATUS_CHOICES):
        status = request.POST["status"]
        if status == "LOCKED" and period.vouchers.exclude(status__in=["POSTED", "REVERSED", "REJECTED"]).exists():
            messages.error(request, "Finalize all vouchers before locking this period.")
        else:
            period.status = status
            period.locked_by = request.user if status == "LOCKED" else None
            period.locked_at = timezone.now() if status == "LOCKED" else None
            period.save()
            audit("PERIOD", request.user, detail=f"{period.name} changed to {status}")
    return redirect(f"{reverse('ledger-dashboard')}?section=settings")


@login_required
def export_report(request):
    report = request.GET.get("report", "vouchers")
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="synergetic-{report}-{timezone.localdate()}.csv"'
    writer = csv.writer(response)
    if report == "trial-balance":
        writer.writerow(["Code", "Account", "Debit", "Credit"])
        for account in Account.objects.all():
            lines = account.voucher_lines.filter(voucher__status="POSTED")
            writer.writerow([account.code, account.name, _money(lines.aggregate(v=Sum("debit"))["v"]), _money(lines.aggregate(v=Sum("credit"))["v"])])
    else:
        writer.writerow(["Voucher", "Date", "Type", "Party", "Narration", "Reference", "Status", "Debit", "Credit"])
        for voucher in _vouchers(request): writer.writerow([voucher.voucher_no, voucher.voucher_date, voucher.get_voucher_type_display(), voucher.party_name, voucher.narration, voucher.source_reference, voucher.get_status_display(), voucher.total_debit, voucher.total_credit])
    audit("EXPORT", request.user, detail=f"Exported {report}")
    return response
