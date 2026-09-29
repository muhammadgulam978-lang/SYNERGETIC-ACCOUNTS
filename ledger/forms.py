from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.forms import UserCreationForm
from django.forms import inlineformset_factory

from .models import Account, ApprovalRule, BankAccount, BankStatementLine, Budget, Campus, CashSession, CostCentre, Document, FinanceRequest, FinanceRequestLine, FinancialPeriod, ImportBatch, Notification, RecurringExpense, RequestAttachment, RequestComment, TaxRule, Voucher, VoucherLine


class DateInput(forms.DateInput):
    input_type = "date"


class VoucherForm(forms.ModelForm):
    class Meta:
        model = Voucher
        fields = ["voucher_type", "voucher_date", "period", "campus", "cost_centre", "party_name", "narration", "source_module", "source_reference", "payment_method", "attachment"]
        widgets = {"voucher_date": DateInput(), "narration": forms.Textarea(attrs={"rows": 3})}

    def clean(self):
        data = super().clean()
        period, day = data.get("period"), data.get("voucher_date")
        if period and day and not period.start_date <= day <= period.end_date:
            self.add_error("voucher_date", "Date must fall inside the selected period.")
        if period and period.status == "LOCKED":
            self.add_error("period", "The selected period is locked.")
        centre, campus = data.get("cost_centre"), data.get("campus")
        if centre and campus and centre.campus_id != campus.id:
            self.add_error("cost_centre", "Cost centre must belong to the selected branch.")
        return data


class VoucherLineForm(forms.ModelForm):
    class Meta:
        model = VoucherLine
        fields = ["account", "description", "debit", "credit"]

    def clean(self):
        data = super().clean()
        debit, credit = data.get("debit") or 0, data.get("credit") or 0
        if (debit > 0) == (credit > 0):
            raise forms.ValidationError("Enter a positive amount on exactly one side.")
        return data


VoucherLineFormSet = inlineformset_factory(Voucher, VoucherLine, form=VoucherLineForm, extra=4, min_num=2, validate_min=True, can_delete=True)


class BankStatementLineForm(forms.ModelForm):
    class Meta:
        model = BankStatementLine
        fields = ["bank_account", "transaction_date", "reference", "description", "debit", "credit"]
        widgets = {"transaction_date": DateInput()}

    def clean(self):
        data = super().clean()
        debit, credit = data.get("debit") or 0, data.get("credit") or 0
        if (debit > 0) == (credit > 0):
            raise forms.ValidationError("Enter a positive amount on exactly one side.")
        return data


class DocumentForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = ["title", "category", "file", "voucher", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}

    def clean_file(self):
        uploaded = self.cleaned_data["file"]
        if uploaded.size > 10 * 1024 * 1024:
            raise forms.ValidationError("Maximum file size is 10 MB.")
        allowed = {"pdf", "csv", "xlsx", "xls", "docx", "doc", "png", "jpg", "jpeg"}
        ext = uploaded.name.rsplit(".", 1)[-1].lower() if "." in uploaded.name else ""
        if ext not in allowed:
            raise forms.ValidationError("Upload PDF, spreadsheet, Word document or image files only.")
        return uploaded


class NotificationForm(forms.ModelForm):
    class Meta:
        model = Notification
        fields = ["recipient", "subject", "message", "voucher"]
        widgets = {"message": forms.Textarea(attrs={"rows": 5})}


class AccountUserCreationForm(UserCreationForm):
    ROLE_CHOICES = (
        ("SUBMITTER", "Submitter — create entries, upload files and track review"),
        ("ADMIN", "Finance admin — review, approve, post and manage accounts"),
    )
    email = forms.EmailField(required=True)
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    role = forms.ChoiceField(choices=ROLE_CHOICES, initial="SUBMITTER")

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ["username", "first_name", "last_name", "email", "role"]

    def save(self, commit=True):
        user = super().save(commit=False)
        is_admin = self.cleaned_data["role"] == "ADMIN"
        user.is_staff = is_admin
        if commit:
            user.save()
            group_name = "Finance Admin" if is_admin else "Accounts Submitter"
            group, _ = Group.objects.get_or_create(name=group_name)
            user.groups.add(group)
        return user


class FinanceRequestForm(forms.ModelForm):
    class Meta:
        model = FinanceRequest
        fields = ["request_type", "title", "purpose", "payee_name", "department", "campus", "cost_centre", "expense_account", "payment_account", "request_date", "due_date", "gross_amount", "tax_amount", "withholding_amount", "priority", "attachment"]
        widgets = {"request_date": DateInput(), "due_date": DateInput(), "purpose": forms.Textarea(attrs={"rows": 3})}

    def clean(self):
        data = super().clean()
        if data.get("due_date") and data.get("request_date") and data["due_date"] < data["request_date"]:
            self.add_error("due_date", "Due date cannot be before the request date.")
        if data.get("cost_centre") and data.get("campus") and data["cost_centre"].campus_id != data["campus"].id:
            self.add_error("cost_centre", "Cost centre must belong to the selected branch.")
        total = (data.get("gross_amount") or 0) + (data.get("tax_amount") or 0)
        if (data.get("withholding_amount") or 0) > total:
            self.add_error("withholding_amount", "Withholding cannot exceed the total amount.")
        return data


FinanceRequestLineFormSet = inlineformset_factory(FinanceRequest, FinanceRequestLine, fields=["description", "quantity", "unit_amount"], extra=3, can_delete=True)


class RequestAttachmentForm(forms.ModelForm):
    class Meta:
        model = RequestAttachment
        fields = ["title", "file"]


class RequestCommentForm(forms.ModelForm):
    class Meta:
        model = RequestComment
        fields = ["message"]
        widgets = {"message": forms.Textarea(attrs={"rows": 2, "placeholder": "Add a review note or reply…"})}


class RecurringExpenseForm(forms.ModelForm):
    class Meta:
        model = RecurringExpense
        exclude = ["created_by", "last_generated_at"]
        widgets = {"next_due_date": DateInput(), "purpose": forms.Textarea(attrs={"rows": 3})}


class TaxRuleForm(forms.ModelForm):
    class Meta:
        model = TaxRule
        fields = ["name", "rate", "withholding_rate", "is_active"]


class ImportBatchForm(forms.ModelForm):
    class Meta:
        model = ImportBatch
        fields = ["file"]

    def clean_file(self):
        uploaded = self.cleaned_data["file"]
        if not uploaded.name.lower().endswith(".csv"):
            raise forms.ValidationError("Upload a CSV file.")
        return uploaded


def model_form(model, fields, widgets=None):
    return forms.modelform_factory(model, fields=fields, widgets=widgets or {})


AccountForm = model_form(Account, ["code", "name", "account_type", "normal_balance", "parent", "campus", "tax_treatment", "is_active"])
CampusForm = model_form(Campus, ["name", "code", "is_active"])
CostCentreForm = model_form(CostCentre, ["campus", "name", "code", "department", "is_active"])
FinancialPeriodForm = model_form(FinancialPeriod, ["name", "start_date", "end_date", "status"], {"start_date": DateInput(), "end_date": DateInput()})
BudgetForm = model_form(Budget, ["campus", "account", "period", "cost_centre", "department", "amount"])
BankAccountForm = model_form(BankAccount, ["campus", "ledger_account", "bank_name", "account_title", "account_number", "currency", "is_active"])
CashSessionForm = model_form(CashSession, ["campus", "counter_name", "session_date", "opening_balance"], {"session_date": DateInput()})
ApprovalRuleForm = model_form(ApprovalRule, ["voucher_type", "role_name", "campus", "department", "minimum_amount", "maximum_amount", "approval_level", "is_active"])
