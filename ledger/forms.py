from django import forms
from django.forms import inlineformset_factory

from .models import Account, ApprovalRule, BankAccount, BankStatementLine, Budget, Campus, CashSession, CostCentre, Document, FinancialPeriod, Voucher, VoucherLine


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


def model_form(model, fields, widgets=None):
    return forms.modelform_factory(model, fields=fields, widgets=widgets or {})


AccountForm = model_form(Account, ["code", "name", "account_type", "normal_balance", "parent", "campus", "tax_treatment", "is_active"])
CampusForm = model_form(Campus, ["name", "code", "is_active"])
CostCentreForm = model_form(CostCentre, ["campus", "name", "code", "department", "is_active"])
FinancialPeriodForm = model_form(FinancialPeriod, ["name", "start_date", "end_date", "status"], {"start_date": DateInput(), "end_date": DateInput()})
BudgetForm = model_form(Budget, ["campus", "account", "period", "cost_centre", "department", "project", "amount"])
BankAccountForm = model_form(BankAccount, ["campus", "ledger_account", "bank_name", "account_title", "account_number", "currency", "is_active"])
CashSessionForm = model_form(CashSession, ["campus", "counter_name", "session_date", "opening_balance"], {"session_date": DateInput()})
ApprovalRuleForm = model_form(ApprovalRule, ["voucher_type", "role_name", "campus", "department", "minimum_amount", "maximum_amount", "approval_level", "is_active"])
