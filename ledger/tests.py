from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .models import Document, Notification, Voucher, VoucherLine
from .services import approve_voucher, ensure_setup, next_voucher_number, post_voucher, submit_voucher


class SynergeticAccountsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.maker = User.objects.create_user("maker", password="test-pass", is_staff=True)
        cls.approver = User.objects.create_superuser("approver", "approver@test.local", "test-pass")
        cls.campus, cls.period, cls.accounts = ensure_setup()

    def test_dashboard_requires_login_and_renders_all_core_sections(self):
        self.assertEqual(self.client.get(reverse("ledger-dashboard")).status_code, 302)
        self.client.force_login(self.approver)
        for section in ["dashboard", "vouchers", "accounts", "parties", "cash-bank", "budgets", "documents", "tracking", "notifications", "users", "reports", "audit", "settings"]:
            response = self.client.get(reverse("ledger-dashboard"), {"section": section})
            self.assertEqual(response.status_code, 200, section)

    def test_controlled_double_entry_workflow(self):
        voucher = Voucher.objects.create(voucher_no=next_voucher_number("JV"), voucher_type="JV", period=self.period, campus=self.campus, narration="Workflow test", created_by=self.maker)
        VoucherLine.objects.create(voucher=voucher, account=self.accounts["1100"], debit=Decimal("1000"))
        VoucherLine.objects.create(voucher=voucher, account=self.accounts["4000"], credit=Decimal("1000"))
        submit_voucher(voucher, self.maker)
        approve_voucher(voucher, self.approver)
        post_voucher(voucher, self.approver)
        voucher.refresh_from_db()
        self.assertEqual(voucher.status, "POSTED")
        self.assertEqual(voucher.total_debit, voucher.total_credit)

    def test_document_vault_and_notifications_use_project_database(self):
        document = Document.objects.create(title="Test receipt", category="RECEIPT", file=SimpleUploadedFile("receipt.pdf", b"%PDF-test"), uploaded_by=self.maker)
        notice = Notification.objects.create(sender=self.approver, recipient=self.maker, subject="Receipt review", message="Please review the uploaded receipt.")
        self.assertEqual(Document.objects.get(pk=document.pk).filename, "receipt.pdf")
        self.assertFalse(Notification.objects.get(pk=notice.pk).is_read)

    def test_staff_can_create_new_user_account(self):
        self.client.force_login(self.approver)
        response = self.client.post(reverse("user-create"), {"username": "finance.user", "first_name": "Finance", "last_name": "User", "email": "finance@test.local", "is_staff": "on", "password1": "A-secure-test-pass-2026", "password2": "A-secure-test-pass-2026"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(get_user_model().objects.filter(username="finance.user", is_staff=True).exists())
