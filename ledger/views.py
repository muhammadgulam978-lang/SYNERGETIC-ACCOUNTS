import csv
import io
import calendar
from datetime import date
from decimal import Decimal
from functools import wraps

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .forms import AccountForm, AccountUserCreationForm, ApprovalRuleForm, BankAccountForm, BankStatementLineForm, BudgetForm, CampusForm, CashSessionForm, CostCentreForm, DocumentForm, FinanceRequestForm, FinanceRequestLineFormSet, FinancialPeriodForm, ImportBatchForm, NotificationForm, RecurringExpenseForm, RequestAttachmentForm, RequestCommentForm, TaxRuleForm, VoucherForm, VoucherLineFormSet
from .models import Account, ApprovalRule, AuditEvent, BankAccount, BankStatementLine, Budget, Campus, CashSession, CloseChecklistItem, Document, FinanceRequest, FinancialPeriod, ImportBatch, Notification, RecurringExpense, RequestApproval, RequestAttachment, TaxRule, Voucher, VoucherLine
from .services import approve_finance_request, approve_voucher, audit, ensure_close_checklist, ensure_setup, next_request_reference, next_voucher_number, notify_admins, notify_creator, pay_and_post_finance_request, post_voucher, reject_finance_request, reverse_voucher, submit_finance_request, submit_voucher, validate_balanced


def _money(value):
    return value or Decimal("0")


def _staff_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_staff:
            messages.error(request, "This action is available to finance administrators only.")
            return redirect("ledger-dashboard")
        return view(request, *args, **kwargs)
    return wrapped


def _vouchers(request, base_qs=None):
    qs = base_qs if base_qs is not None else Voucher.objects.all()
    qs = qs.select_related("campus", "period", "created_by", "approved_by").prefetch_related("lines__account")
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


def _submitter_dashboard(request):
    section = request.GET.get("section", "dashboard")
    allowed = {"dashboard", "submissions", "documents", "tracking", "notifications"}
    section = section if section in allowed else "dashboard"
    own_vouchers = Voucher.objects.filter(created_by=request.user)
    voucher_qs = _vouchers(request, own_vouchers)
    track_query = request.GET.get("track_q", "").strip()
    tracked = own_vouchers.select_related("campus", "created_by", "approved_by", "posted_by").prefetch_related("audit_events")
    if track_query:
        tracked = tracked.filter(Q(voucher_no__icontains=track_query) | Q(source_reference__icontains=track_query) | Q(party_name__icontains=track_query) | Q(narration__icontains=track_query))
    inbox = Notification.objects.filter(recipient=request.user).select_related("sender", "voucher")
    sent = Notification.objects.filter(sender=request.user).select_related("recipient", "voucher")
    own_requests = FinanceRequest.objects.filter(created_by=request.user)
    status_counts = {
        "DRAFT": own_requests.filter(status="DRAFT").count(),
        "SUBMITTED": own_requests.filter(status__in=["SUBMITTED", "UNDER_REVIEW"]).count(),
        "APPROVED": own_requests.filter(status="APPROVED").count(),
        "POSTED": own_requests.filter(status__in=["POSTED", "SETTLED"]).count(),
    }
    context = {
        "portal_kind": "submitter", "section": section,
        "submission_counts": status_counts,
        "recent_finance_requests": own_requests.select_related("campus")[:8],
        "recent_vouchers": own_vouchers.select_related("campus")[:8],
        "vouchers": voucher_qs[:200], "voucher_count": voucher_qs.count(),
        "voucher_statuses": Voucher.STATUS_CHOICES, "voucher_types": Voucher.TYPE_CHOICES,
        "documents": Document.objects.filter(uploaded_by=request.user).select_related("voucher", "uploaded_by")[:200],
        "tracked_vouchers": tracked[:100], "track_query": track_query,
        "notification_tab": request.GET.get("tab", "inbox"), "inbox_notifications": inbox[:100], "sent_notifications": sent[:100],
        "unread_notifications": inbox.filter(is_read=False).count(),
    }
    return render(request, "ledger/user_dashboard.html", context)


@login_required
def dashboard(request):
    ensure_setup()
    if not request.user.is_staff:
        return _submitter_dashboard(request)
    section = request.GET.get("section", "dashboard")
    allowed = {"dashboard", "vouchers", "accounts", "parties", "cash-bank", "budgets", "documents", "tracking", "notifications", "users", "reports", "audit", "settings"}
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
    track_query = request.GET.get("track_q", "").strip()
    tracked = Voucher.objects.select_related("campus", "created_by", "approved_by", "posted_by").prefetch_related("audit_events").all()
    if track_query:
        tracked = tracked.filter(Q(voucher_no__icontains=track_query) | Q(source_reference__icontains=track_query) | Q(party_name__icontains=track_query) | Q(narration__icontains=track_query))
    inbox = Notification.objects.filter(recipient=request.user).select_related("sender", "voucher")
    sent = Notification.objects.filter(sender=request.user).select_related("recipient", "voucher")
    context = {
        "portal_kind": "admin",
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
        "tracked_vouchers": tracked[:100], "track_query": track_query,
        "notification_tab": request.GET.get("tab", "inbox"), "inbox_notifications": inbox[:100], "sent_notifications": sent[:100],
        "unread_notifications": inbox.filter(is_read=False).count(),
        "account_users": get_user_model().objects.order_by("first_name", "username"),
        "staff_user_count": get_user_model().objects.filter(is_staff=True, is_active=True).count(),
        "submitter_user_count": get_user_model().objects.filter(is_staff=False, is_active=True).count(),
        "periods": FinancialPeriod.objects.all(), "approval_rules": ApprovalRule.objects.select_related("campus"),
        "audit_events": AuditEvent.objects.select_related("voucher", "actor")[:200],
        "unmatched_bank_count": BankStatementLine.objects.filter(matched_voucher__isnull=True).count(),
        "matched_bank_count": BankStatementLine.objects.filter(matched_voucher__isnull=False).count(),
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
    vouchers = Voucher.objects.select_related("campus", "period", "cost_centre", "created_by", "approved_by", "posted_by", "reversal_of").prefetch_related("lines__account", "audit_events__actor")
    if not request.user.is_staff:
        vouchers = vouchers.filter(created_by=request.user)
    voucher = get_object_or_404(vouchers, pk=pk)
    return render(request, "ledger/voucher_detail.html", {"voucher": voucher})


@login_required
def voucher_action(request, pk, action):
    vouchers = Voucher.objects.all()
    if not request.user.is_staff:
        vouchers = vouchers.filter(created_by=request.user)
    voucher = get_object_or_404(vouchers, pk=pk)
    if request.method != "POST":
        return redirect("voucher-detail", pk=pk)
    try:
        if action != "submit" and not request.user.is_staff:
            raise ValidationError("Only finance administrators can review or post submissions.")
        if action == "submit": submit_voucher(voucher, request.user)
        elif action == "approve": approve_voucher(voucher, request.user)
        elif action == "post": post_voucher(voucher, request.user)
        elif action == "reject":
            if voucher.status != "SUBMITTED": raise ValidationError("Only pending vouchers can be rejected.")
            voucher.status, voucher.rejection_reason, voucher.approved_by = "REJECTED", request.POST.get("reason", "").strip(), request.user
            voucher.save(update_fields=["status", "rejection_reason", "approved_by"])
            audit("REJECT", request.user, voucher, voucher.rejection_reason)
            notify_creator(voucher, request.user, f"{voucher.voucher_no} was rejected", voucher.rejection_reason or "Please review and resubmit the entry.")
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
@_staff_required
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
    if not request.user.is_staff:
        form.fields["voucher"].queryset = Voucher.objects.filter(created_by=request.user)
    if form.is_valid():
        document = form.save(commit=False)
        document.uploaded_by = request.user
        document.save()
        audit("UPLOAD", request.user, document.voucher, f"Uploaded {document.title}")
        if not request.user.is_staff:
            reference = f" for {document.voucher.voucher_no}" if document.voucher else ""
            notify_admins(request.user, document.voucher, f"New document: {document.title}", f"{request.user.get_full_name() or request.user.username} uploaded a document{reference}.")
        messages.success(request, "File uploaded to the document vault.")
        return redirect(f"{reverse('ledger-dashboard')}?section=documents")
    return render(request, "ledger/record_form.html", {"form": form, "label": "Upload financial document", "section": "documents"})


@login_required
def notification_compose(request):
    form = NotificationForm(request.POST or None)
    if not request.user.is_staff:
        form.fields["recipient"].queryset = get_user_model().objects.filter(is_active=True, is_staff=True)
        form.fields["voucher"].queryset = Voucher.objects.filter(created_by=request.user)
    if form.is_valid():
        notification = form.save(commit=False)
        notification.sender = request.user
        notification.save()
        messages.success(request, "Notification sent.")
        return redirect(f"{reverse('ledger-dashboard')}?section=notifications&tab=sent")
    return render(request, "ledger/record_form.html", {"form": form, "label": "Send notification", "section": "notifications"})


@login_required
def notification_read(request, pk):
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    if not notification.is_read:
        notification.is_read = True
        notification.save(update_fields=["is_read"])
    return render(request, "ledger/notification_detail.html", {"notification": notification, "section": "notifications"})


@login_required
@_staff_required
def user_create(request):
    form = AccountUserCreationForm(request.POST or None)
    if form.is_valid():
        user = form.save()
        messages.success(request, f"Account created for {user.get_full_name() or user.username}.")
        return redirect(f"{reverse('ledger-dashboard')}?section=users")
    return render(request, "ledger/record_form.html", {"form": form, "label": "Create user account", "section": "users"})


@login_required
@_staff_required
def bank_match(request, pk):
    line = get_object_or_404(BankStatementLine, pk=pk)
    if request.method == "POST":
        voucher = get_object_or_404(Voucher, pk=request.POST.get("voucher_id"), status="POSTED")
        line.matched_voucher, line.reconciled_by, line.reconciled_at = voucher, request.user, timezone.now()
        line.save(update_fields=["matched_voucher", "reconciled_by", "reconciled_at"])
        audit("RECONCILE", request.user, voucher, f"Matched bank line {line.reference}")
    return redirect(f"{reverse('ledger-dashboard')}?section=cash-bank")


@login_required
@_staff_required
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
@_staff_required
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
@_staff_required
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


def _request_queryset(request):
    qs = FinanceRequest.objects.select_related("campus", "cost_centre", "expense_account", "payment_account", "created_by", "reviewed_by", "linked_voucher")
    return qs if request.user.is_staff else qs.filter(created_by=request.user)


@login_required
def finance_request_list(request):
    qs = _request_queryset(request)
    request_type = request.GET.get("type", "")
    status = request.GET.get("status", "")
    search = request.GET.get("q", "").strip()
    if request_type: qs = qs.filter(request_type=request_type)
    if status: qs = qs.filter(status=status)
    if search: qs = qs.filter(Q(reference__icontains=search) | Q(title__icontains=search) | Q(payee_name__icontains=search) | Q(purpose__icontains=search))
    all_scoped = _request_queryset(request)
    context = {
        "section": "finance-requests", "requests": qs[:250], "request_types": FinanceRequest.TYPE_CHOICES, "request_statuses": FinanceRequest.STATUS_CHOICES,
        "request_count": qs.count(), "pending_count": all_scoped.filter(status__in=["SUBMITTED", "UNDER_REVIEW"]).count(),
        "approved_count": all_scoped.filter(status="APPROVED").count(), "posted_count": all_scoped.filter(status="POSTED").count(),
        "total_requested": _money(all_scoped.exclude(status="REJECTED").aggregate(v=Sum("net_amount"))["v"]),
        "unread_notifications": Notification.objects.filter(recipient=request.user, is_read=False).count(),
    }
    return render(request, "ledger/finance_request_list.html", context)


@login_required
def finance_request_create(request, pk=None):
    item = None
    if pk:
        item = get_object_or_404(_request_queryset(request), pk=pk)
        if item.status not in {"DRAFT", "REJECTED"}:
            messages.error(request, "Only draft or rejected requests can be edited.")
            return redirect("finance-request-detail", pk=pk)
    instance = item or FinanceRequest(created_by=request.user)
    form = FinanceRequestForm(request.POST or None, request.FILES or None, instance=instance)
    formset = FinanceRequestLineFormSet(request.POST or None, instance=instance)
    if form.is_valid() and formset.is_valid():
        with transaction.atomic():
            saved = form.save(commit=False)
            if not saved.pk:
                saved.reference = next_request_reference(saved.request_type, saved.request_date)
                saved.created_by = request.user
            saved.save()
            formset.instance = saved
            formset.save()
            line_total = sum((line.total for line in saved.items.all()), Decimal("0"))
            if line_total:
                saved.gross_amount = line_total
                saved.save(update_fields=["gross_amount", "net_amount", "required_approval_level", "updated_at"])
            RequestApproval.objects.create(request=saved, action="COMMENT", actor=request.user, comment="Request saved as draft")
        messages.success(request, f"{saved.reference} saved successfully.")
        return redirect("finance-request-detail", pk=saved.pk)
    return render(request, "ledger/finance_request_form.html", {"form": form, "formset": formset, "item": item, "section": "finance-requests"})


@login_required
def finance_request_detail(request, pk):
    item = get_object_or_404(_request_queryset(request).prefetch_related("items", "attachments", "comments__author", "workflow_events__actor"), pk=pk)
    return render(request, "ledger/finance_request_detail.html", {"item": item, "attachment_form": RequestAttachmentForm(), "comment_form": RequestCommentForm(), "section": "finance-requests", "unread_notifications": Notification.objects.filter(recipient=request.user, is_read=False).count()})


@login_required
def finance_request_action(request, pk, action):
    item = get_object_or_404(_request_queryset(request), pk=pk)
    if request.method != "POST": return redirect("finance-request-detail", pk=pk)
    try:
        if action == "submit":
            if item.created_by_id != request.user.id and not request.user.is_staff: raise ValidationError("You can only submit your own request.")
            submit_finance_request(item, request.user)
        elif not request.user.is_staff:
            raise ValidationError("Only finance administrators can perform this action.")
        elif action == "approve": approve_finance_request(item, request.user)
        elif action == "reject": reject_finance_request(item, request.user, request.POST.get("reason", "").strip())
        elif action == "pay": pay_and_post_finance_request(item, request.user, request.POST.get("payment_reference", "").strip())
        elif action == "settle":
            if item.request_type != "ADVANCE" or item.status != "POSTED": raise ValidationError("Only a paid advance can be settled.")
            item.status = "SETTLED"; item.save(update_fields=["status", "net_amount", "required_approval_level", "updated_at"])
            RequestApproval.objects.create(request=item, action="SETTLE", actor=request.user, comment=request.POST.get("reason", ""))
        else: raise ValidationError("Unknown workflow action.")
        messages.success(request, "Workflow updated successfully.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("finance-request-detail", pk=pk)


@login_required
def request_attachment_upload(request, pk):
    item = get_object_or_404(_request_queryset(request), pk=pk)
    form = RequestAttachmentForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        attachment = form.save(commit=False); attachment.request = item; attachment.uploaded_by = request.user; attachment.save()
        messages.success(request, "Supporting file uploaded.")
    else:
        for error in form.errors.values(): messages.error(request, " ".join(error))
    return redirect("finance-request-detail", pk=pk)


@login_required
def request_comment_create(request, pk):
    item = get_object_or_404(_request_queryset(request), pk=pk)
    form = RequestCommentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        comment = form.save(commit=False); comment.request = item; comment.author = request.user; comment.save()
        RequestApproval.objects.create(request=item, action="COMMENT", actor=request.user, comment=comment.message)
        if request.user.is_staff:
            Notification.objects.create(sender=request.user, recipient=item.created_by, subject=f"Comment on {item.reference}", message=comment.message)
        else:
            from .services import notify_request_admins
            notify_request_admins(item, request.user, f"New comment: {item.reference}", comment.message)
    return redirect("finance-request-detail", pk=pk)


@login_required
@_staff_required
def operations_centre(request):
    recurring_form = RecurringExpenseForm(prefix="recurring")
    tax_form = TaxRuleForm(prefix="tax")
    import_form = ImportBatchForm(prefix="import")
    if request.method == "POST":
        kind = request.POST.get("kind")
        if kind == "recurring":
            recurring_form = RecurringExpenseForm(request.POST, prefix="recurring")
            if recurring_form.is_valid():
                obj = recurring_form.save(commit=False); obj.created_by = request.user; obj.save(); messages.success(request, "Recurring expense saved."); return redirect("operations-centre")
        elif kind == "tax":
            tax_form = TaxRuleForm(request.POST, prefix="tax")
            if tax_form.is_valid(): tax_form.save(); messages.success(request, "Tax rule saved."); return redirect("operations-centre")
        elif kind == "import":
            import_form = ImportBatchForm(request.POST, request.FILES, prefix="import")
            if import_form.is_valid():
                batch = import_form.save(commit=False); batch.uploaded_by = request.user; batch.save()
                errors, imported = [], 0
                try:
                    rows = csv.DictReader(io.StringIO(batch.file.read().decode("utf-8-sig")))
                    campus, _, accounts = ensure_setup()
                    for number, row in enumerate(rows, 2):
                        try:
                            request_type = row.get("type", "EXPENSE").upper()
                            if request_type not in dict(FinanceRequest.TYPE_CHOICES): raise ValueError("invalid type")
                            amount = Decimal(row["amount"])
                            FinanceRequest.objects.create(reference=next_request_reference(request_type), request_type=request_type, title=row["title"], purpose=row.get("purpose") or row["title"], payee_name=row.get("payee", ""), department=row.get("department", ""), campus=campus, expense_account=accounts["5100"], payment_account=accounts["1100"], gross_amount=amount, created_by=request.user)
                            imported += 1
                        except Exception as exc: errors.append(f"Row {number}: {exc}")
                    batch.status = "COMPLETED" if not errors else "FAILED"; batch.imported_count = imported; batch.error_log = "\n".join(errors); batch.save()
                    messages.success(request, f"Imported {imported} draft requests. {len(errors)} rows need correction."); return redirect("operations-centre")
                except Exception as exc:
                    batch.status = "FAILED"; batch.error_log = str(exc); batch.save(); messages.error(request, f"Import failed: {exc}")
    period = FinancialPeriod.objects.first()
    if period: ensure_close_checklist(period)
    return render(request, "ledger/operations_centre.html", {"section": "operations", "recurring_form": recurring_form, "tax_form": tax_form, "import_form": import_form, "recurring_items": RecurringExpense.objects.select_related("campus", "expense_account"), "tax_rules": TaxRule.objects.all(), "period": period, "close_items": period.close_items.all() if period else [], "imports": ImportBatch.objects.select_related("uploaded_by")[:20], "unread_notifications": Notification.objects.filter(recipient=request.user, is_read=False).count()})


def _advance_due_date(day, frequency):
    months = {"MONTHLY": 1, "QUARTERLY": 3, "YEARLY": 12}[frequency]
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


@login_required
@_staff_required
def recurring_generate(request, pk):
    recurring = get_object_or_404(RecurringExpense, pk=pk, is_active=True)
    if request.method == "POST":
        item = FinanceRequest.objects.create(reference=next_request_reference(recurring.request_type, recurring.next_due_date), request_type=recurring.request_type, title=recurring.title, purpose=recurring.purpose, payee_name=recurring.payee_name, campus=recurring.campus, cost_centre=recurring.cost_centre, expense_account=recurring.expense_account, payment_account=recurring.payment_account, request_date=recurring.next_due_date, due_date=recurring.next_due_date, gross_amount=recurring.amount, created_by=recurring.created_by)
        recurring.last_generated_at = timezone.now(); recurring.next_due_date = _advance_due_date(recurring.next_due_date, recurring.frequency); recurring.save(update_fields=["last_generated_at", "next_due_date"])
        messages.success(request, f"Draft {item.reference} generated for review.")
    return redirect("operations-centre")


@login_required
@_staff_required
def close_item_toggle(request, pk):
    item = get_object_or_404(CloseChecklistItem, pk=pk)
    if request.method == "POST":
        item.is_complete = not item.is_complete
        item.completed_by = request.user if item.is_complete else None
        item.completed_at = timezone.now() if item.is_complete else None
        item.save()
    return redirect("operations-centre")
