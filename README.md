Download the zip folder and export

Open the folder

Click --> Clrl + L

Enter --> cmd and press Enter

In cmd Enter :-
 
```bash 

cd college-portal\backend

py -3.11 -m venv .venv

.venv\Scripts\activate

pip install -r requirements.txt

copy .env.example .env

uvicorn app.main:app --reload --port 8000

```

Open the folder again

Click --> Clrl + L

Enter --> cmd and press Enter

In cmd Enter :-
 
```bash

cd college-portal\frontend

npm install

npm run dev

```

Now Ctrl click the local host




# CampusOS — College Management & Student Portal (Demonstration)

CampusOS is a role-based college information portal built with **React + TypeScript** and **Python FastAPI**. It includes student, teacher, management and administrator workspaces backed by a shared database.

> **Important:** This is a demonstration starter, not a production-ready college ERP. Use dummy data only. The payment action is simulated and does not transfer money. Before using real student records, financial data or 50,000+ users, complete the production checklist below and conduct an independent security and load assessment.

## What is included

- Four role-based portals: student, teacher, management and administrator.
- Cookie-based login session, Argon2id password hashing, CSRF validation and backend role checks.
- Student directory with server-side search and pagination.
- Admin CSV import preview and commit, with validation, duplicate detection and admission-number upsert. Imported student profiles do not automatically create login accounts; connect the institution’s identity provisioning/invitation process before student rollout.
- Trusted ERP-to-portal student synchronization API using a server-side integration key.
- Department directory and administrator department creation.
- Attendance history and teacher/admin class attendance marking.
- Examination calendar and student registration with course/semester eligibility checks.
- Dedicated fee account view and simulated student payments with transaction references.
- Announcements, management reports, audit log, demo timetable and learning hub preview.
- SQLite for a quick local demo; PostgreSQL supported through `DATABASE_URL` and included in Docker Compose.

Some academic capabilities shown in the UI (for example assignment submission, file-backed study resources, full results processing, timetable management and PDF/export reports) are preview/scaffold screens and need additional backend modules before production use.

## Demo accounts

All demo accounts use the password **`Demo@123`**.

| Portal | Username | Password |
|---|---|---|
| Student | `student` | `Demo@123` |
| Teacher | `teacher` | `Demo@123` |
| Management | `management` | `Demo@123` |
| Admin | `admin` | `Demo@123` |

The student demo account is linked to admission number `MCA2026-001`.

## Run locally

Requirements: Python 3.11+ and Node.js 20+.

### 1. Backend

```bash
cd backend
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
# source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env   # Windows PowerShell: Copy-Item .env.example .env
# macOS/Linux: cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

The API is available at `http://localhost:8000`; interactive API documentation is at `http://localhost:8000/docs`.

The default database is SQLite (`backend/college_portal.db`). Tables and demonstration records are created automatically on first startup. To use PostgreSQL, set `DATABASE_URL` in `backend/.env`, for example:

```env
DATABASE_URL=postgresql+psycopg://campus:change-this-password@localhost:5432/campusos
```

The PostgreSQL driver is included in `requirements.txt`. For production, use Alembic migrations rather than relying on `create_all`.

### 2. Frontend

Open a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173` and sign in with one of the demo accounts above. The frontend expects the API at `http://localhost:8000`; copy `frontend/.env.example` to `frontend/.env` and override `VITE_API_URL` when needed.

## Docker Compose

A PostgreSQL service and backend container are included. The frontend can be run separately with `npm run dev` during development.

```bash
# From the project root
cp backend/.env.example backend/.env
# Edit backend/.env and set a strong SECRET_KEY, INTEGRATION_API_KEY, and COOKIE_SECURE=false for local HTTP development.
docker compose up --build
```

The backend API will be at `http://localhost:8000`. For the browser frontend, run `cd frontend && npm install && npm run dev`.

## Student data import

### CSV import

In the Admin portal, open **Students → Import data**. Upload a UTF-8 CSV file, validate the preview, then commit the valid records.

Required CSV columns:

```csv
admission_no,name,email,department_code,course,semester
MCA2026-101,Sample Student,sample@student.demo,MCA,MCA,1
```

Optional columns: `phone`, `academic_year`.

Rules:
- Maximum 5 MB and 5,000 records per request.
- Admission number is the matching key.
- Existing records are updated; new admission numbers create records.
- Unknown department codes and invalid rows are skipped.
- The preview does not modify the database.
- Imports are recorded in the audit log.

### Trusted ERP API sync

A trusted ERP can push student records to:

`POST http://localhost:8000/api/integrations/students/sync`

Header:

`X-Integration-Key: <INTEGRATION_API_KEY>`

Example request:

```json
{
  "source": "College ERP",
  "records": [
    {
      "admission_no": "MCA2026-102",
      "name": "Example Student",
      "email": "example@student.demo",
      "phone": "9876500010",
      "department_code": "MCA",
      "course": "MCA",
      "semester": 1,
      "academic_year": "2026-27"
    }
  ]
}
```

The endpoint validates the integration key and upserts by admission number. Keep the key only in the ERP/server environment; never place it in React/browser code. For production, use HTTPS, key rotation, request signing/HMAC, replay protection, source IP/network restrictions, per-source audit identity, and a field-level conflict policy agreed with the existing ERP owner.

## Security notes

The demo includes several baseline controls, but it is not a completed security certification:

- Session JWT is stored in an HttpOnly cookie; browser state-changing requests must provide the CSRF cookie value in `X-CSRF-Token`.
- Passwords use Argon2id.
- API endpoints enforce roles on the server; student fee and academic queries are scoped to the linked student record.
- Payment simulation is clearly identified and must not be connected to real bank/card/UPI details.
- No card number, CVV, UPI PIN or bank credential is collected or stored.
- Demo default secrets are not suitable for production.

### Required before a real college launch

1. Set strong, unique secrets and `COOKIE_SECURE=true` behind HTTPS; configure an exact production CORS origin and appropriate cookie policy.
2. Replace demo credentials/seed logic with the institution's identity provisioning, password reset, MFA policy and account lifecycle process.
3. Add Alembic migrations, automated backups, point-in-time recovery, restore drills, monitoring and alerting.
4. Complete an independent OWASP ASVS/security review, dependency/SBOM scanning, penetration testing and privacy/data-protection review.
5. Add complete domain modules for results, internal marks, timetable, assignments, certificates, leave, hostel/transport and approved fee adjustments.
6. Integrate a real payment provider in test mode first. Use server-verified signatures/webhooks, idempotency keys, reconciliation, refunds and an immutable financial ledger; do not trust frontend payment success.
7. Implement scoped department/faculty permissions, fine-grained RBAC, audit retention, admin MFA and least-privilege access.
8. Run realistic load tests for 50,000+ student records and expected peak concurrent usage. Add proper PostgreSQL indexes, connection-pool sizing, caching, background jobs and object storage based on measured results.
9. Confirm the data contract with the existing ERP and test synchronization in a staging environment before allowing updates to real records.

## Project structure

```text
college-portal/
├── backend/
│   ├── app/
│   │   ├── config.py
│   │   ├── database.py
│   │   ├── main.py
│   │   └── models.py
│   ├── .env.example
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── api.ts
│   │   ├── App.tsx
│   │   ├── main.tsx
│   │   └── styles.css
│   ├── package.json
│   └── vite.config.ts
├── docker-compose.yml
└── README.md
```
