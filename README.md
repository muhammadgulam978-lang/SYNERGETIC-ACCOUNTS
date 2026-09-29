# Synergetic Accounts

Independent accounting web application for Synergetic Solutions. This project has its own Django application, PostgreSQL database (`synergetic_accounts`), database role (`synergetic_app`), uploaded files, users, migrations and settings. It does not import or share EduPilot models.

## First run

PostgreSQL 18 is already configured locally for this project. In PowerShell:

```powershell
cd 'D:\synergetic accounts'
.\setup.ps1
.\run.ps1
```

Open `http://127.0.0.1:8090`.

Local bootstrap logins:

- Admin review portal: `admin` / `Synergetic@2026`
- User submitter portal: `accounts.user` / `Synergetic@2026`

Change both passwords before real use. The secondary `accounts.maker` finance user exists to exercise maker/approver controls.

## Included

- Double-entry vouchers with draft, submit, approve, post, reject and reversal workflows
- Live cash, bank, receivable, payable, income and expense balances
- Chart of accounts, party balances, budgets and financial periods
- Cash closing and bank reconciliation
- File uploads with voucher links and a document vault
- Internal notification inbox/sent links and voucher references
- End-to-end transaction tracking from creation through posting
- Admin-controlled creation of new finance user accounts
- Trial balance and general-ledger CSV exports
- Immutable audit history and role-aware approval controls
- Separate admin and submitter portals with server-side record isolation
- Automatic admin alerts on new submissions/uploads and user alerts on approval, rejection, posting or reversal
- Unified expense claims, bill/payment requests, purchase requests and employee advances
- Amount-based multi-level approvals, correction/resubmission, comments and supporting files
- Automatic posting of approved payments into the existing double-entry voucher ledger
- Recurring expense schedules, due/overdue reminders, tax/withholding rules and month-end checklist
- Controlled CSV import for bulk draft requests

## Finance automation

To generate due recurring drafts and send overdue reminders (normally scheduled daily):

```powershell
.\.venv\Scripts\python.exe manage.py process_finance_automation
```

CSV request imports accept: `type,title,purpose,payee,department,amount`. Supported types are `EXPENSE`, `BILL`, `PURCHASE` and `ADVANCE`.
