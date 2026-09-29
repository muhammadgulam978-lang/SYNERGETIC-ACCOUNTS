from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard, name="ledger-dashboard"),
    path("vouchers/new/", views.voucher_create, name="voucher-create"),
    path("vouchers/<int:pk>/", views.voucher_detail, name="voucher-detail"),
    path("vouchers/<int:pk>/<str:action>/", views.voucher_action, name="voucher-action"),
    path("setup/<str:kind>/new/", views.record_create, name="record-create"),
    path("documents/upload/", views.document_upload, name="document-upload"),
    path("notifications/new/", views.notification_compose, name="notification-compose"),
    path("notifications/<int:pk>/", views.notification_read, name="notification-read"),
    path("users/new/", views.user_create, name="user-create"),
    path("bank-lines/<int:pk>/match/", views.bank_match, name="bank-match"),
    path("cash/<int:pk>/<str:action>/", views.cash_action, name="cash-action"),
    path("periods/<int:pk>/status/", views.period_action, name="period-action"),
    path("reports/export/", views.export_report, name="export-report"),
    path("requests/", views.finance_request_list, name="finance-request-list"),
    path("requests/new/", views.finance_request_create, name="finance-request-create"),
    path("requests/<int:pk>/", views.finance_request_detail, name="finance-request-detail"),
    path("requests/<int:pk>/edit/", views.finance_request_create, name="finance-request-edit"),
    path("requests/<int:pk>/attachments/", views.request_attachment_upload, name="request-attachment"),
    path("requests/<int:pk>/comments/", views.request_comment_create, name="request-comment"),
    path("requests/<int:pk>/<str:action>/", views.finance_request_action, name="finance-request-action"),
    path("operations/", views.operations_centre, name="operations-centre"),
    path("operations/recurring/<int:pk>/generate/", views.recurring_generate, name="recurring-generate"),
    path("operations/close/<int:pk>/toggle/", views.close_item_toggle, name="close-item-toggle"),
]
