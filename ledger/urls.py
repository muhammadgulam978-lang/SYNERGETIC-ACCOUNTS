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
]
