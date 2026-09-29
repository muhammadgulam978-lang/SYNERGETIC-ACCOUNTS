from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.utils import timezone

from ledger.models import Account, BankAccount, BankStatementLine, Budget, CashSession, Notification, TaxRule, Voucher, VoucherLine
from ledger.services import DEFAULT_ACCOUNTS, approve_voucher, ensure_close_checklist, ensure_setup, next_voucher_number, post_voucher, submit_voucher


class Command(BaseCommand):
    help = "Create local finance users, account masters and representative opening data."

    def add_arguments(self, parser):
        parser.add_argument("--password", default="Synergetic@2026")

    def handle(self, *args, **options):
        User = get_user_model()
        owner, _ = User.objects.get_or_create(username="admin", defaults={"first_name": "Finance", "last_name": "Owner", "email": "accounts@synergetic.local", "is_staff": True, "is_superuser": True})
        owner.set_password(options["password"]); owner.save()
        maker, _ = User.objects.get_or_create(username="accounts.maker", defaults={"first_name": "Accounts", "last_name": "Maker", "is_staff": True})
        maker.set_password(options["password"]); maker.save()
        submitter, _ = User.objects.get_or_create(username="accounts.user", defaults={"first_name": "Accounts", "last_name": "Submitter", "email": "submitter@synergetic.local", "is_staff": False})
        submitter.is_staff = False
        submitter.set_password(options["password"]); submitter.save()
        admin_group, _ = Group.objects.get_or_create(name="Finance Admin")
        submitter_group, _ = Group.objects.get_or_create(name="Accounts Submitter")
        owner.groups.add(admin_group); maker.groups.add(admin_group); submitter.groups.add(submitter_group)
        campus, period, accounts = ensure_setup()
        ensure_close_checklist(period)
        TaxRule.objects.get_or_create(name="Standard withholding", defaults={"rate": Decimal("0"), "withholding_rate": Decimal("4.5")})
        TaxRule.objects.get_or_create(name="Sales tax", defaults={"rate": Decimal("18"), "withholding_rate": Decimal("0")})
        for code, name, kind, normal in DEFAULT_ACCOUNTS:
            Account.objects.filter(code=code).update(name=name, account_type=kind, normal_balance=normal)
        BankAccount.objects.get_or_create(ledger_account=accounts["1100"], defaults={"campus": campus, "bank_name": "Primary Business Bank", "account_title": "Synergetic Solutions", "account_number": "PK00-SYNERGETIC-4488", "currency": "PKR"})
        Budget.objects.get_or_create(campus=campus, account=accounts["5100"], period=period, department="Operations", project="", cost_centre=None, defaults={"amount": Decimal("1200000")})
        today = timezone.localdate()
        CashSession.objects.get_or_create(campus=campus, counter_name="Head Office", session_date=today, defaults={"opening_balance": Decimal("75000"), "opened_by": maker})
        samples = [
            ("opening-orion", "RV", today - timedelta(days=12), "Orion Retail", "Monthly consulting retainer received", "1100", "4000", "850000", "Bank transfer"),
            ("opening-nova", "RV", today - timedelta(days=8), "Nova Tech", "Product implementation receipt", "1100", "4000", "620000", "Bank transfer"),
            ("opening-office", "PV", today - timedelta(days=5), "Urban Workspace", "Office lease and utilities", "5100", "1100", "185000", "Bank transfer"),
            ("opening-payroll", "PV", today - timedelta(days=2), "Team Payroll", "Monthly payroll expense", "5000", "1100", "410000", "Bank transfer"),
            ("opening-vertex", "JV", today - timedelta(days=1), "Vertex Industries", "Customer invoice raised", "1200", "4000", "325000", "Invoice"),
        ]
        for sample_key, kind, day, party, narration, debit, credit, amount, method in samples:
            ref = f"DEMO-{sample_key.upper()}"
            if Voucher.objects.filter(source_reference=ref).exists():
                continue
            voucher = Voucher.objects.create(voucher_no=next_voucher_number(kind, day), voucher_type=kind, voucher_date=day, period=period, campus=campus, party_name=party, narration=narration, source_module="Opening data", source_reference=ref, payment_method=method, created_by=maker)
            VoucherLine.objects.bulk_create([VoucherLine(voucher=voucher, account=accounts[debit], debit=Decimal(amount)), VoucherLine(voucher=voucher, account=accounts[credit], credit=Decimal(amount))])
            submit_voucher(voucher, maker); approve_voucher(voucher, owner); post_voucher(voucher, owner)
        bank = BankAccount.objects.first()
        BankStatementLine.objects.get_or_create(bank_account=bank, transaction_date=today - timedelta(days=1), reference="BANK-UNMATCHED-001", defaults={"description": "Incoming transfer to review", "credit": Decimal("125000")})
        Notification.objects.get_or_create(sender=maker, recipient=owner, subject="Bank line needs reconciliation", defaults={"message": "A new unmatched bank statement line is ready for review."})
        Notification.objects.get_or_create(sender=owner, recipient=maker, subject="Monthly close checklist", defaults={"message": "Please verify supporting documents before the period close."})
        self.stdout.write(self.style.SUCCESS("Synergetic Accounts is ready. Admin: admin | Submitter: accounts.user"))
