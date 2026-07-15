# Material Inward Process — Production Setup Guide

**Client:** Supreme Industries Limited
**System:** Material Inward SAP Automation Portal
**OS:** Windows Server 2025

---

## Quick Start (Automated)

For most cases, just run the one-click setup script:

```
Right-click setup.bat → Run as administrator
```

The script handles: dependency installation, database creation, schema setup, folder creation, and Windows Service registration. Follow the prompts.

---

## Prerequisites (Install Before Running setup.bat)

### 1. Python 3.11
- Download: https://www.python.org/downloads/
- During installation: **check "Add Python to PATH"**
- Verify: open Command Prompt → `python --version`

### 2. PostgreSQL 16
- Download: https://www.postgresql.org/download/windows/
- During installation: note the `postgres` superuser password
- Keep default port: `5432`
- pgAdmin 4 installs automatically — verify it opens

### 3. SAP GUI
- Must already be installed on this machine
- SAP connection must be configured in SAP Logon
- Enable SAP GUI Scripting:
  SAP GUI → Options → Accessibility & Scripting → Scripting → Enable scripting ✓

### 4. Robot Framework Libraries
```
pip install robotframework==7.0.1
pip install robotframework-sapguilibrary==1.1.2
```

### 5. NSSM (for Windows Service — optional but recommended)
- Download: https://nssm.cc/download
- Extract `nssm.exe` to `C:\Windows\System32\` so it's in PATH

---

## Step-by-Step Manual Setup

If you prefer to run each step manually instead of using setup.bat:

### Step 1 — Copy project files
Place the project at:
```
C:\material_inward\
```

### Step 2 — Create .env file
```
cd C:\material_inward
copy .env.template .env
```
Open `.env` in Notepad and fill in every value (see Configuration section below).

### Step 3 — Install Python dependencies
```
cd C:\material_inward
pip install -r requirements.txt
```

### Step 4 — Create required folders
```
mkdir C:\material_inward\uploads
mkdir C:\material_inward\uploads\processed
mkdir C:\material_inward\uploads\failed
mkdir C:\material_inward\logs
mkdir C:\material_inward\logs\rf_output
```

### Step 5 — Create PostgreSQL database
Open pgAdmin → Query Tool → run:
```sql
CREATE USER material_user WITH PASSWORD 'your_password_here';
CREATE DATABASE material_inward OWNER material_user;
GRANT ALL PRIVILEGES ON DATABASE material_inward TO material_user;
```

### Step 6 — Run database schema
```
set PGPASSWORD=your_password_here
psql -U material_user -d material_inward -f database\schema.sql
```
Expected output: all CREATE TABLE statements followed by INSERT rows for storage locations and default admin.

### Step 7 — Test database connection
```
python -c "from database.connection import test_connection; test_connection()"
```
Expected: `Database connection test passed.`

### Step 8 — Start the application
```
cd C:\material_inward
python app.py
```
Open browser: http://localhost:5000

---

## Configuration (.env File)

Open `.env` and fill in all values:

```
# Server
SERVER_HOST=0.0.0.0        ← use 0.0.0.0 for production (accessible on network)
SERVER_PORT=5000
ALLOWED_ORIGIN=https://REPLACE_WITH_CLIENT_PRODUCTION_URL_HERE

# Database
DB_HOST=localhost
DB_PORT=5432
DB_NAME=material_inward
DB_USER=material_user
DB_PASSWORD=your_db_password

# SAP
SAP_CONNECTION_NAME=       ← must match exactly what appears in SAP Logon
SAP_CLIENT=400
SAP_USERNAME=spl_rpa
SAP_PASSWORD=your_sap_password

# Email (same mailbox for sending and receiving)
EMAIL_SENDER=spl_rpa@company.com
EMAIL_PASSWORD=your_email_password
IMAP_USERNAME=spl_rpa@company.com
IMAP_PASSWORD=your_email_password

# WatsonX AI
WATSONX_API_KEY=your_key
WATSONX_PROJECT_ID=your_project_id
WATSONX_MODEL_ID=meta-llama/llama-4-maverick-17b-128e-instruct-fp8

# Phase rollout (change to enable more steps)
ENABLED_STEPS=gate_in,migo,miro,gst
```

To generate secret keys, run in Python:
```python
import secrets
print(secrets.token_hex(32))
```
Run twice — one for `FLASK_SECRET_KEY`, one for `JWT_SECRET_KEY`.

---

## Default Login Credentials

| Field    | Value              |
|----------|--------------------|
| Username | admin@catnip.com   |
| Password | Admin@123          |
| Role     | Admin              |

**Change this password immediately after first login via User Management.**

---

## User Roles

**Admin:**
- Access to upload sidebar (manual document upload)
- Access to User Management page
- Access to Storage Locations management
- Can perform all workflow steps (Gate In, MIGO, MIRO)

**User:**
- Access to History page and workflow tabs only
- Cannot upload documents manually
- Cannot manage users or storage locations

To create plant users: log in as Admin → User Management → Add User → select role "User".

---

## Phase Rollout

Control which workflow tabs are visible via `ENABLED_STEPS` in `.env`:

| Phase | Setting | Visible Tabs |
|-------|---------|--------------|
| Phase 1 | `ENABLED_STEPS=gate_in` | Gate In only |
| Phase 2 | `ENABLED_STEPS=gate_in,migo` | Gate In + MIGO |
| Full | `ENABLED_STEPS=gate_in,migo,miro,gst` | All tabs |

Restart the app after changing this value.

---

## Robot Framework Scripts

Location: `C:\material_inward\robot_scripts\`

| Script | Status | Notes |
|--------|--------|-------|
| gate_in.robot | Ready | Based on client VBScript — verify element IDs in SAP |
| migo_103.robot | Skeleton | Add SAP GUI commands in TODO sections |
| migo_105.robot | Skeleton | Add SAP GUI commands in TODO sections |
| miro.robot | Skeleton | Add SAP GUI commands in TODO sections |

### How to complete a TODO script
1. In SAP: Tools → Macro → Record Script
2. Perform the transaction manually while recording
3. Stop recording → a `.vbs` file is generated
4. Convert VBScript to Robot Framework:
   - `session.findById("wnd[0]/usr/txtXXX").text = "value"` → `Input Text    wnd[0]/usr/txtXXX    value`
   - `session.findById("wnd[0]/tbar[1]/btn[8]").press` → `Click Element    wnd[0]/tbar[1]/btn[8]`
5. Paste converted lines into the `# TODO` sections of the `.robot` file

### Test a single script manually
```
cd C:\material_inward
python -m robot robot_scripts\gate_in.robot
```
Logs are saved to: `C:\material_inward\logs\rf_output\`

---

## Mail Poller Setup (Windows Task Scheduler)

The mail poller runs separately from the main app. Set it up manually:

1. Open **Task Scheduler** (search in Start menu)
2. Click **Create Basic Task**
3. Name: `MaterialInward_MailPoller`
4. Trigger: **Daily**
5. Action: **Start a program**
   - Program: `C:\Python311\python.exe`
   - Arguments: `C:\material_inward\services\mail_poller.py`
   - Start in: `C:\material_inward`
6. Finish, then open the task → **Triggers** tab → Edit trigger
7. Check **Repeat task every: 5 minutes** | Duration: **Indefinitely**
8. Click OK

Test manually:
```
cd C:\material_inward
python services\mail_poller.py
```

---

## Running as Windows Service (Production)

Using NSSM (already done by setup.bat if NSSM was available):
```
nssm install MaterialInward python C:\material_inward\app.py
nssm set MaterialInward AppDirectory C:\material_inward
nssm set MaterialInward Start SERVICE_AUTO_START
nssm start MaterialInward
```

Manage the service:
```
nssm start MaterialInward
nssm stop MaterialInward
nssm restart MaterialInward
```

---

## Firewall & Network Configuration

### Server Firewall — Open port 5000
```
netsh advfirewall firewall add rule name="MaterialInward" dir=in action=allow protocol=TCP localport=5000
```

### Production URL Whitelist
Update `ALLOWED_ORIGIN` in `.env`:
```
ALLOWED_ORIGIN=https://REPLACE_WITH_CLIENT_PRODUCTION_URL_HERE
```

If using a reverse proxy (IIS, nginx):
- Point your domain to `http://localhost:5000`
- Clients access via `https://your-domain.com`

### Ports used by this application
| Port | Service | Notes |
|------|---------|-------|
| 5000 | Flask app | Open in firewall |
| 5432 | PostgreSQL | Internal only |
| 587  | SMTP (Outlook) | Outbound |
| 993  | IMAP (Outlook) | Outbound |

---

## Folder Structure After Setup

```
C:\material_inward\
├── app.py                     ← Main application
├── setup.bat                  ← One-click setup
├── requirements.txt
├── .env                       ← Your credentials (never share)
├── .env.template              ← Template (safe to share)
├── config\
│   ├── config.py              ← All settings loaded from .env
│   └── logger.py              ← Logging configuration
├── database\
│   ├── schema.sql             ← Run once to create all tables
│   ├── connection.py          ← PostgreSQL connection pool
│   ├── db_operations.py       ← History + document operations
│   ├── gatein_operations.py
│   ├── migo_operations.py
│   ├── miro_operations.py
│   ├── user_operations.py
│   ├── storage_location_operations.py
│   └── rf_queue_operations.py
├── services\
│   ├── extract.py             ← WatsonX OCR
│   ├── mail_service.py        ← Email notifications
│   ├── mail_poller.py         ← IMAP inbox watcher (run via Task Scheduler)
│   ├── rf_runner.py           ← Calls Robot Framework scripts
│   └── rf_queue_worker.py     ← Background RF job queue
├── robot_scripts\
│   ├── gate_in.robot          ← SAP Gate In automation
│   ├── migo_103.robot         ← SAP MIGO 103 automation
│   ├── migo_105.robot         ← SAP MIGO 105 automation
│   └── miro.robot             ← SAP MIRO automation
├── static\
│   ├── css\
│   │   ├── main.css
│   │   └── extracted_data.css
│   ├── js\
│   │   ├── main.js
│   │   ├── rf_poller.js       ← Queue polling utility
│   │   └── tabs\
│   │       └── extracted_data.js
│   └── images\
│       ├── SPL_Logo.png       ← Drop your logo here
│       └── spl-new-logo.png   ← Drop your logo here
├── templates\
│   ├── login.html
│   ├── history.html
│   ├── index.html
│   ├── user_management.html
│   └── tabs\
│       ├── extracted_data.html
│       ├── documents.html
│       ├── gate_in.html
│       ├── migo.html
│       ├── miro.html
│       └── gst_report.html
├── uploads\                   ← Incoming PDFs
│   ├── processed\             ← Successfully OCR'd PDFs
│   └── failed\                ← Failed OCR PDFs
└── logs\
    ├── application.log        ← All activity
    ├── errors.log             ← Errors only
    └── rf_output\             ← Robot Framework run logs
```

---

## Log Files

| File | What it contains |
|------|-----------------|
| `logs/application.log` | All INFO and above — normal flow |
| `logs/errors.log` | Errors only — check this when something breaks |
| `logs/rf_output/<script>_<timestamp>/` | RF logs per SAP transaction |

Monitor logs in real time:
```powershell
Get-Content C:\material_inward\logs\application.log -Wait -Tail 50
```

---

## Troubleshooting

### App won't start
- Check `logs/errors.log`
- Verify `.env` file exists and all values are filled
- Run: `python -c "from database.connection import test_connection; test_connection()"`

### Database connection fails
- Verify PostgreSQL service is running (check Windows Services)
- Check `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD` in `.env`

### Robot Framework script fails
- Check `logs/rf_output/` for the specific run log
- Ensure SAP GUI Scripting is enabled
- Ensure `SAP_CONNECTION_NAME` matches exactly what's in SAP Logon
- Test manually: `python -m robot robot_scripts\gate_in.robot`

### Mail poller not picking up emails
- Check `logs/application.log` for IMAP errors
- Test manually: `python services\mail_poller.py`
- Verify `IMAP_USERNAME` and `IMAP_PASSWORD` in `.env`

### OCR returns empty data
- Check WatsonX credentials in `.env`
- Check `logs/errors.log` for API errors
- Failed files are in `uploads/failed/` — can be manually re-uploaded via Admin portal

### Gate In number not captured
- Check `logs/rf_output/gate_in_*/log.html` for the SAP status bar message
- Update the regex in `robot_scripts/gate_in.robot` to match the actual message format

---

## Storage Locations Management

Log in as Admin → User Management → scroll down to "Storage Locations" section.

- **Add**: enter code + description → Save
- **Edit**: change description inline → Save button
- **Deactivate**: hides location from plant users' dropdown (historical records unaffected)
- **Activate**: re-enables a previously deactivated location

All changes take effect immediately — no restart required.

---

## Notification Emails

Edit `.env` to add recipient emails when available:
```
GATEIN_OWNER_EMAIL=gatein.person@company.com
MIGO_OWNER_EMAIL=migo.person@company.com
MIRO_OWNER_EMAIL=miro.person@company.com
```
Restart the app after editing `.env`.

---

*Setup Guide v3 — Material Inward Process*
*Prepared by Catnip Technologies*
