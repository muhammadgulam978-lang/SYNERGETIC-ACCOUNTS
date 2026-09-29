import calendar
from datetime import date

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from ledger.models import FinanceRequest, Notification, RecurringExpense
from ledger.services import next_request_reference


def advance(day, frequency):
    months = {"MONTHLY": 1, "QUARTERLY": 3, "YEARLY": 12}[frequency]
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


class Command(BaseCommand):
    help = "Generate due recurring finance drafts and notify administrators about overdue requests."

    def handle(self, *args, **options):
        today = timezone.localdate()
        generated = 0
        for recurring in RecurringExpense.objects.filter(is_active=True, next_due_date__lte=today).select_related("created_by"):
            FinanceRequest.objects.create(reference=next_request_reference(recurring.request_type, recurring.next_due_date), request_type=recurring.request_type, title=recurring.title, purpose=recurring.purpose, payee_name=recurring.payee_name, campus=recurring.campus, cost_centre=recurring.cost_centre, expense_account=recurring.expense_account, payment_account=recurring.payment_account, request_date=recurring.next_due_date, due_date=recurring.next_due_date, gross_amount=recurring.amount, created_by=recurring.created_by)
            recurring.last_generated_at = timezone.now()
            recurring.next_due_date = advance(recurring.next_due_date, recurring.frequency)
            recurring.save(update_fields=["last_generated_at", "next_due_date"])
            generated += 1
        admins = get_user_model().objects.filter(is_active=True, is_staff=True)
        sender = admins.filter(is_superuser=True).first() or admins.first()
        overdue = FinanceRequest.objects.filter(due_date__lt=today, status__in=["SUBMITTED", "UNDER_REVIEW", "APPROVED"])
        reminders = 0
        if sender:
            for item in overdue:
                for admin in admins:
                    _, created = Notification.objects.get_or_create(sender=sender, recipient=admin, subject=f"Overdue: {item.reference} ({today})", defaults={"message": f"{item.title} was due on {item.due_date} and still requires action."})
                    reminders += int(created)
        self.stdout.write(self.style.SUCCESS(f"Generated {generated} recurring drafts; sent {reminders} overdue reminders."))
