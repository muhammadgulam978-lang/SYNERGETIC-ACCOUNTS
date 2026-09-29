from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http.request import validate_host
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from synergetic_accounts.settings import _allowed_hosts

from .models import Document, FinanceRequest, Notification, Voucher, VoucherLine
from .services import approve_finance_request, approve_voucher, ensure_setup, next_request_reference, next_voucher_number, pay_and_post_finance_request, post_voucher, submit_finance_request, submit_voucher


class AllowedHostsTests(SimpleTestCase):
    def test_vercel_wildcard_environment_value_matches_deployment_domains(self):
        with patch.dict("os.environ", {"SYNERGETIC_ALLOWED_HOSTS": "*.vercel.app"}):
            allowed_hosts = _allowed_hosts()

        self.assertIn(".vercel.app", allowed_hosts)
        self.assertTrue(validate_host("synergetic-accounts.vercel.app", allowed_hosts))

    def test_sqlite_requires_explicit_opt_in(self):
        with patch.dict("os.environ", {"SYNERGETIC_USE_SQLITE": "1"}, clear=True):
            from importlib import reload
            import synergetic_accounts.settings as settings_module

            reloaded = reload(settings_module)
            self.assertEqual(reloaded.DATABASES["default"]["ENGINE"], "django.db.backends.sqlite3")


class SynergeticAccountsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.maker = User.objects.create_user("maker", password="test-pass", is_staff=True)
        cls.approver = User.objects.create_superuser("approver", "approver@test.local", "test-pass")
        cls.submitter = User.objects.create_user("submitter", password="test-pass", is_staff=False)
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
        saved_name = Document.objects.get(pk=document.pk).filename
        self.assertTrue(saved_name.startswith("receipt"))
        self.assertTrue(saved_name.endswith(".pdf"))
        self.assertFalse(Notification.objects.get(pk=notice.pk).is_read)

    def test_staff_can_create_new_user_account(self):
        self.client.force_login(self.approver)
        response = self.client.post(reverse("user-create"), {"username": "finance.user", "first_name": "Finance", "last_name": "User", "email": "finance@test.local", "role": "ADMIN", "password1": "A-secure-test-pass-2026", "password2": "A-secure-test-pass-2026"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(get_user_model().objects.filter(username="finance.user", is_staff=True).exists())

    def test_submitter_gets_separate_portal_and_cannot_see_other_users_records(self):
        other = Voucher.objects.create(voucher_no=next_voucher_number("JV"), voucher_type="JV", period=self.period, campus=self.campus, narration="Private admin entry", created_by=self.maker)
        self.client.force_login(self.submitter)
        response = self.client.get(reverse("ledger-dashboard"))
        self.assertContains(response, "SUBMITTER PORTAL")
        self.assertNotContains(response, "Chart of accounts")
        self.assertEqual(self.client.get(reverse("voucher-detail", args=[other.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("export-report")).status_code, 302)

    def test_submitter_can_submit_but_cannot_approve_and_admin_is_notified(self):
        voucher = Voucher.objects.create(voucher_no=next_voucher_number("JV"), voucher_type="JV", period=self.period, campus=self.campus, narration="Submitter entry", created_by=self.submitter)
        VoucherLine.objects.create(voucher=voucher, account=self.accounts["1100"], debit=Decimal("500"))
        VoucherLine.objects.create(voucher=voucher, account=self.accounts["4000"], credit=Decimal("500"))
        self.client.force_login(self.submitter)
        self.client.post(reverse("voucher-action", args=[voucher.pk, "submit"]))
        voucher.refresh_from_db()
        self.assertEqual(voucher.status, "SUBMITTED")
        self.assertTrue(Notification.objects.filter(recipient=self.approver, voucher=voucher, subject__contains="Review required").exists())
        self.client.post(reverse("voucher-action", args=[voucher.pk, "approve"]))
        voucher.refresh_from_db()
        self.assertEqual(voucher.status, "SUBMITTED")

    def test_submitter_document_and_message_forms_are_scoped(self):
        own = Voucher.objects.create(voucher_no=next_voucher_number("JV"), voucher_type="JV", period=self.period, campus=self.campus, narration="Own entry", created_by=self.submitter)
        other = Voucher.objects.create(voucher_no=next_voucher_number("PV"), voucher_type="PV", period=self.period, campus=self.campus, narration="Other entry", created_by=self.maker)
        self.client.force_login(self.submitter)
        document_response = self.client.get(reverse("document-upload"))
        self.assertIn(own, document_response.context["form"].fields["voucher"].queryset)
        self.assertNotIn(other, document_response.context["form"].fields["voucher"].queryset)
        notification_response = self.client.get(reverse("notification-compose"))
        self.assertIn(self.approver, notification_response.context["form"].fields["recipient"].queryset)
        self.assertNotIn(self.submitter, notification_response.context["form"].fields["recipient"].queryset)

    def test_finance_request_approval_payment_and_ledger_posting(self):
        item = FinanceRequest.objects.create(reference=next_request_reference("EXPENSE"), request_type="EXPENSE", title="Travel reimbursement", purpose="Client meeting travel", campus=self.campus, expense_account=self.accounts["5100"], payment_account=self.accounts["1100"], gross_amount=Decimal("10000"), tax_amount=Decimal("1000"), withholding_amount=Decimal("500"), created_by=self.submitter)
        submit_finance_request(item, self.submitter)
        approve_finance_request(item, self.approver)
        pay_and_post_finance_request(item, self.approver, "BANK-TEST-100")
        item.refresh_from_db()
        self.assertEqual(item.status, "POSTED")
        self.assertEqual(item.net_amount, Decimal("10500"))
        self.assertEqual(item.linked_voucher.status, "POSTED")
        self.assertEqual(item.linked_voucher.total_debit, item.linked_voucher.total_credit)
        self.client.force_login(self.submitter)
        self.assertContains(self.client.get(reverse("finance-request-detail", args=[item.pk])), "Travel reimbursement")
        self.client.force_login(self.approver)
        self.assertEqual(self.client.get(reverse("operations-centre")).status_code, 200)

    def test_finance_request_portal_is_scoped_by_owner(self):
        private = FinanceRequest.objects.create(reference=next_request_reference("BILL"), request_type="BILL", title="Admin bill", purpose="Private", campus=self.campus, expense_account=self.accounts["5100"], payment_account=self.accounts["1100"], gross_amount=Decimal("5000"), created_by=self.maker)
        self.client.force_login(self.submitter)
        response = self.client.get(reverse("finance-request-list"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Admin bill")
        self.assertEqual(self.client.get(reverse("finance-request-detail", args=[private.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("operations-centre")).status_code, 302)
