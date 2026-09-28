from django.contrib import admin
from .models import Account, ApprovalRule, AuditEvent, BankAccount, BankStatementLine, Budget, Campus, CashSession, CostCentre, Document, FinancialPeriod, Voucher, VoucherLine

for model in [Account, ApprovalRule, AuditEvent, BankAccount, BankStatementLine, Budget, Campus, CashSession, CostCentre, Document, FinancialPeriod, Voucher, VoucherLine]:
    admin.site.register(model)

admin.site.site_header = "Synergetic Accounts Administration"
admin.site.site_title = "Synergetic Accounts"
