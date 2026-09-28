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

Local bootstrap login:

- Username: `admin`
- Password: `Synergetic@2026`

Change the password from Django admin before real use. The secondary `accounts.maker` user exists to exercise maker/approver controls.

## Included

- Double-entry vouchers with draft, submit, approve, post, reject and reversal workflows
- Live cash, bank, receivable, payable, income and expense balances
- Chart of accounts, party balances, budgets and financial periods
- Cash closing and bank reconciliation
- File uploads with voucher links and a document vault
- Trial balance and general-ledger CSV exports
- Immutable audit history and role-aware approval controls
