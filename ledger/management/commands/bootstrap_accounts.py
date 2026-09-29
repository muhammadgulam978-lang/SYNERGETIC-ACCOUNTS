from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from ledger.models import Account, BankAccount, BankStatementLine, Budget, CashSession, Notification, Voucher, VoucherLine
from ledger.services import DEFAULT_ACCOUNTS, approve_voucher, ensure_setup, next_voucher_number, post_voucher, submit_voucher


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
        campus, period, accounts = ensure_setup()
        for code, name, kind, normal in DEFAULT_ACCOUNTS:
            Account.objects.filter(code=code).update(name=name, account_type=kind, normal_balance=normal)
        BankAccount.objects.get_or_create(ledger_account=accounts["1100"], defaults={"campus": campus, "bank_name": "Primary Business Bank", "account_title": "Synergetic Solutions", "account_number": "PK00-SYNERGETIC-4488", "currency": "PKR"})
        Budget.objects.get_or_create(campus=campus, account=accounts["5100"], period=period, department="Operations", project="", cost_centre=None, defaults={"amount": Decimal("1200000")})
        today = timezone.localdate()
        CashSession.objects.get_or_create(campus=campus, counter_name="Head Office", session_date=today, defaults={"opening_balance": Decimal("75000"), "opened_by": maker})
        samples = [
            ("RV", today - timedelta(days=12), "Orion Retail", "Monthly consulting retainer received", "1100", "4000", "850000", "Bank transfer"),
            ("RV", today - timedelta(days=8), "Nova Tech", "Product implementation receipt", "1100", "4000", "620000", "Bank transfer"),
            ("PV", today - timedelta(days=5), "Urban Workspace", "Office lease and utilities", "5100", "1100", "185000", "Bank transfer"),
            ("PV", today - timedelta(days=2), "Team Payroll", "Monthly payroll expense", "5000", "1100", "410000", "Bank transfer"),
            ("JV", today - timedelta(days=1), "Vertex Industries", "Customer invoice raised", "1200", "4000", "325000", "Invoice"),
        ]
        for kind, day, party, narration, debit, credit, amount, method in samples:
            ref = f"DEMO-{day:%Y%m%d}-{debit}-{credit}"
            if Voucher.objects.filter(source_reference=ref).exists():
                continue
            voucher = Voucher.objects.create(voucher_no=next_voucher_number(kind, day), voucher_type=kind, voucher_date=day, period=period, campus=campus, party_name=party, narration=narration, source_module="Opening data", source_reference=ref, payment_method=method, created_by=maker)
            VoucherLine.objects.bulk_create([VoucherLine(voucher=voucher, account=accounts[debit], debit=Decimal(amount)), VoucherLine(voucher=voucher, account=accounts[credit], credit=Decimal(amount))])
            submit_voucher(voucher, maker); approve_voucher(voucher, owner); post_voucher(voucher, owner)
        bank = BankAccount.objects.first()
        BankStatementLine.objects.get_or_create(bank_account=bank, transaction_date=today - timedelta(days=1), reference="BANK-UNMATCHED-001", defaults={"description": "Incoming transfer to review", "credit": Decimal("125000")})
        Notification.objects.get_or_create(sender=maker, recipient=owner, subject="Bank line needs reconciliation", defaults={"message": "A new unmatched bank statement line is ready for review."})
        Notification.objects.get_or_create(sender=owner, recipient=maker, subject="Monthly close checklist", defaults={"message": "Please verify supporting documents before the period close."})
        self.stdout.write(self.style.SUCCESS("Synergetic Accounts is ready. User: admin"))
