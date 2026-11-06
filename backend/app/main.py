from __future__ import annotations

import csv
import io
import secrets
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
from .database import Base, SessionLocal, engine, get_db
from . import models as m

app = FastAPI(
    title="CampusOS API",
    version="0.1.0",
    description="Demonstration API for a role-based college information system.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token", "X-Integration-Key"],
)

pwd_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
COOKIE_NAME = "campus_session"
CSRF_COOKIE = "campus_csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    if request.method not in SAFE_METHODS and request.url.path not in {
        "/api/auth/login", "/api/auth/logout", "/api/integrations/students/sync"
    }:
        cookie_token = request.cookies.get(CSRF_COOKIE)
        header_token = request.headers.get("X-CSRF-Token")
        if not cookie_token or not header_token or not secrets.compare_digest(cookie_token, header_token):
            return Response(content='{"detail":"CSRF validation failed"}', status_code=403, media_type="application/json")
    return await call_next(request)


def hash_password(password: str) -> str:
    return pwd_hasher.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    try:
        return pwd_hasher.verify(encoded, password)
    except VerifyMismatchError:
        return False


def create_access_token(user: m.User) -> str:
    now = datetime.utcnow()
    payload = {"sub": str(user.id), "role": user.role, "iat": now, "exp": now + timedelta(minutes=settings.access_token_minutes)}
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def get_current_user(request: Request, db: Session = Depends(get_db)) -> m.User:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        user_id = int(payload.get("sub", "0"))
    except (jwt.InvalidTokenError, ValueError):
        raise HTTPException(status_code=401, detail="Session expired. Please sign in again.")
    user = db.get(m.User, user_id)
    if not user or not user.active:
        raise HTTPException(status_code=401, detail="Account unavailable")
    return user


def require_roles(*roles: str):
    def checker(user: m.User = Depends(get_current_user)) -> m.User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="You do not have permission to perform this action")
        return user
    return checker


def audit(db: Session, actor_id: int | None, action: str, entity: str, entity_id: Any = "", details: str = ""):
    db.add(m.AuditLog(actor_id=actor_id, action=action, entity=entity, entity_id=str(entity_id), details=details[:1000]))


def linked_student(db: Session, user: m.User) -> m.Student:
    student = db.scalar(select(m.Student).where(m.Student.user_id == user.id))
    if not student:
        raise HTTPException(status_code=403, detail="No student record is linked to this account")
    return student


def serialize_student(s: m.Student) -> dict[str, Any]:
    return {
        "id": s.id, "admission_no": s.admission_no, "name": s.name, "email": s.email,
        "phone": s.phone, "department": s.department.name if s.department else "Unassigned",
        "department_code": s.department.code if s.department else "", "course": s.course,
        "semester": s.semester, "academic_year": s.academic_year, "status": s.status,
        "joined_on": s.joined_on.isoformat(),
    }


def serialize_invoice(inv: m.FeeInvoice) -> dict[str, Any]:
    total = Decimal(inv.total_amount)
    paid = Decimal(inv.amount_paid)
    return {
        "id": inv.id, "student_id": inv.student_id,
        "student_name": inv.student.name if inv.student else "",
        "admission_no": inv.student.admission_no if inv.student else "",
        "description": inv.description, "category": inv.category, "semester": inv.semester,
        "due_date": inv.due_date.isoformat(), "total_amount": float(total),
        "amount_paid": float(paid), "balance": float(max(total - paid, Decimal("0.00"))), "status": inv.status,
    }


class LoginIn(BaseModel):
    username: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=1, max_length=256)


class StudentIn(BaseModel):
    admission_no: str = Field(min_length=2, max_length=40)
    name: str = Field(min_length=2, max_length=160)
    email: EmailStr
    phone: str = Field(default="", max_length=30)
    department_code: str = Field(min_length=2, max_length=20)
    course: str = Field(min_length=2, max_length=100)
    semester: int = Field(ge=1, le=12)
    academic_year: str = Field(default="2026-27", max_length=20)
    status: str = "Active"


class AttendanceItem(BaseModel):
    student_id: int
    status: str = Field(pattern="^(Present|Absent|Late|Excused)$")


class AttendanceIn(BaseModel):
    subject: str = Field(min_length=2, max_length=120)
    session_date: date
    records: list[AttendanceItem] = Field(min_length=1, max_length=300)


class DepartmentIn(BaseModel):
    code: str = Field(min_length=2, max_length=20)
    name: str = Field(min_length=2, max_length=120)
    hod_name: str = Field(default="Not assigned", max_length=160)


class AnnouncementIn(BaseModel):
    title: str = Field(min_length=3, max_length=180)
    content: str = Field(min_length=3, max_length=3000)
    audience: str = Field(default="All", pattern="^(All|Students|Teachers|Management)$")


class PaymentIn(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)


class FeeInvoiceIn(BaseModel):
    student_id: int
    description: str = Field(min_length=3, max_length=160)
    category: str = Field(min_length=2, max_length=80)
    semester: int = Field(ge=1, le=12)
    due_date: date
    total_amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)


class SyncPayload(BaseModel):
    records: list[dict[str, Any]] = Field(min_length=1, max_length=5000)
    source: str = Field(default="College ERP", max_length=100)


@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
    seed_demo_data()


def seed_demo_data():
    db = SessionLocal()
    try:
        if db.scalar(select(func.count(m.User.id))) > 0:
            return
        departments = [
            ("MCA", "Master of Computer Applications", "Dr. Meera Nair"),
            ("BCA", "Bachelor of Computer Applications", "Prof. Arun Kumar"),
            ("BCOM", "Commerce", "Dr. Fathima K"),
            ("BBA", "Business Administration", "Dr. Nikhil Das"),
            ("CS", "Computer Science", "Prof. Lakshmi P"),
            ("ENG", "English", "Dr. Thomas Mathew"),
        ]
        dept_map = {}
        for code, name, hod in departments:
            dept = m.Department(code=code, name=name, hod_name=hod)
            db.add(dept)
            db.flush()
            dept_map[code] = dept.id

        password = hash_password("Demo@123")
        accounts = [
            ("admin", "admin@campus.demo", "Campus Administrator", "admin"),
            ("management", "management@campus.demo", "Dr. Ananya Menon", "management"),
            ("teacher", "teacher@campus.demo", "Prof. Rahul Varma", "teacher"),
            ("student", "student@campus.demo", "Aarav Nair", "student"),
        ]
        users = {}
        for username, email, full_name, role in accounts:
            user = m.User(username=username, email=email, full_name=full_name, password_hash=password, role=role)
            db.add(user)
            db.flush()
            users[username] = user

        teacher = m.Teacher(user_id=users["teacher"].id, staff_code="FAC-001", department_id=dept_map["MCA"], designation="Assistant Professor")
        db.add(teacher)
        student = m.Student(
            user_id=users["student"].id, admission_no="MCA2026-001", name="Aarav Nair",
            email="aarav.nair@student.demo", phone="9876500001", department_id=dept_map["MCA"],
            course="MCA", semester=1, academic_year="2026-27", status="Active", joined_on=date(2026, 7, 1)
        )
        db.add(student)
        db.flush()

        first_names = ["Ananya", "Arjun", "Diya", "Ishan", "Maya", "Nikhil", "Riya", "Varun", "Meera", "Aditya", "Kavya", "Rahul"]
        last_names = ["Menon", "Nair", "Kumar", "Pillai", "Das", "Thomas", "Sharma", "Rao", "Joseph", "Varma"]
        course_codes = ["MCA", "BCA", "BCOM", "BBA", "CS", "ENG"]
        for i in range(2, 91):
            code = course_codes[(i - 2) % len(course_codes)]
            db.add(m.Student(
                admission_no=f"{code}2026-{i:03d}",
                name=f"{first_names[i % len(first_names)]} {last_names[i % len(last_names)]}",
                email=f"student{i:03d}@student.demo", phone=f"98765{i:05d}",
                department_id=dept_map[code], course=code, semester=((i - 1) % 6) + 1,
                academic_year="2026-27", status="Active" if i % 17 else "On Leave", joined_on=date(2026, 7, 1)
            ))
        db.flush()

        today = date.today()
        all_students = db.scalars(select(m.Student).order_by(m.Student.id)).all()
        for idx, s in enumerate(all_students):
            fee = Decimal("42000.00") if s.course == "MCA" else Decimal("28000.00")
            paid = (fee / 2 if idx % 4 == 0 else (fee / 4 if idx % 4 == 1 else (Decimal("0.00") if idx % 4 == 2 else fee)))
            state = "Paid" if paid == fee else ("Partial" if paid > 0 else "Pending")
            invoice = m.FeeInvoice(
                student_id=s.id, description=f"Semester {s.semester} academic fee", category="Tuition & Academic",
                semester=s.semester, due_date=today + timedelta(days=(idx % 30) - 8),
                total_amount=fee, amount_paid=paid, status=state
            )
            db.add(invoice)
            db.flush()
            if paid > 0:
                db.add(m.Payment(invoice_id=invoice.id, reference=f"DEMO-{uuid.uuid4().hex[:10].upper()}", amount=paid, method="Demo gateway", status="Success"))

        subjects = ["Database Management Systems", "Python Programming", "Statistics", "Software Engineering"]
        for i, s in enumerate(all_students[:30]):
            for j, subject in enumerate(subjects[:2]):
                db.add(m.Attendance(student_id=s.id, subject=subject, session_date=today - timedelta(days=(j + 1)), status="Absent" if (i + j) % 7 == 0 else "Present", marked_by=users["teacher"].id))

        exam_seed = [
            ("Internal Assessment I", "Database Management Systems", "MCA", 1, today + timedelta(days=8), True),
            ("Internal Assessment I", "Python Programming", "MCA", 1, today + timedelta(days=11), True),
            ("End Semester Examination", "Computer Networks", "BCA", 3, today + timedelta(days=28), False),
            ("Model Examination", "Financial Accounting", "BCOM", 2, today + timedelta(days=16), True),
        ]
        for title, subject, course, sem, exam_date, opened in exam_seed:
            db.add(m.Exam(title=title, subject=subject, course=course, semester=sem, exam_date=exam_date, registration_open=opened))

        db.add(m.Announcement(title="Welcome to the 2026–27 academic year", content="The student portal demonstration is ready. Please review your profile, attendance and examination information.", audience="All", author_id=users["management"].id))
        db.add(m.Announcement(title="Internal assessment registration", content="Eligible students can register for open internal assessments from the Examinations section.", audience="Students", author_id=users["management"].id))
        audit(db, users["admin"].id, "DEMO_SEED", "system", "initial", "Seeded demonstration accounts and records")
        db.commit()
    finally:
        db.close()


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "CampusOS API", "environment": settings.app_env}


@app.post("/api/auth/login")
def login(payload: LoginIn, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(m.User).where((m.User.username == payload.username) | (m.User.email == payload.username)))
    if not user or not user.active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    token = create_access_token(user)
    csrf = secrets.token_urlsafe(32)
    response.set_cookie(COOKIE_NAME, token, httponly=True, secure=settings.cookie_secure, samesite=settings.cookie_samesite, max_age=settings.access_token_minutes * 60, path="/")
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, secure=settings.cookie_secure, samesite=settings.cookie_samesite, max_age=settings.access_token_minutes * 60, path="/")
    audit(db, user.id, "LOGIN", "user", user.id, "Successful login")
    db.commit()
    return {"id": user.id, "username": user.username, "full_name": user.full_name, "email": user.email, "role": user.role}


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"message": "Signed out"}


@app.get("/api/auth/me")
def me(user: m.User = Depends(get_current_user)):
    return {"id": user.id, "username": user.username, "full_name": user.full_name, "email": user.email, "role": user.role}


@app.get("/api/dashboard")
def dashboard(user: m.User = Depends(get_current_user), db: Session = Depends(get_db)):
    student = db.scalar(select(m.Student).where(m.Student.user_id == user.id)) if user.role == "student" else None
    total_students = db.scalar(select(func.count(m.Student.id))) or 0
    total_teachers = db.scalar(select(func.count(m.Teacher.id))) or 0
    total_departments = db.scalar(select(func.count(m.Department.id))) or 0
    total_invoices = db.scalar(select(func.count(m.FeeInvoice.id))) or 0
    collected = db.scalar(select(func.coalesce(func.sum(m.FeeInvoice.amount_paid), 0))) or 0
    billed = db.scalar(select(func.coalesce(func.sum(m.FeeInvoice.total_amount), 0))) or 0
    pending = Decimal(str(billed)) - Decimal(str(collected))
    if student:
        attendance_total = db.scalar(select(func.count(m.Attendance.id)).where(m.Attendance.student_id == student.id)) or 0
        attendance_present = db.scalar(select(func.count(m.Attendance.id)).where(m.Attendance.student_id == student.id, m.Attendance.status.in_(["Present", "Late"]))) or 0
        attendance_pct = round(attendance_present / attendance_total * 100) if attendance_total else 0
        fees = db.scalars(select(m.FeeInvoice).where(m.FeeInvoice.student_id == student.id)).all()
        fee_total = sum((Decimal(x.total_amount) for x in fees), Decimal("0"))
        fee_paid = sum((Decimal(x.amount_paid) for x in fees), Decimal("0"))
        registrations = db.scalar(select(func.count(m.ExamRegistration.id)).where(m.ExamRegistration.student_id == student.id)) or 0
        return {
            "role": user.role, "student": serialize_student(student), "attendance_percentage": attendance_pct,
            "attendance_sessions": attendance_total, "fee_total": float(fee_total), "fee_paid": float(fee_paid),
            "fee_balance": float(fee_total - fee_paid), "exam_registrations": registrations,
            "upcoming_exams": db.scalar(select(func.count(m.Exam.id)).where(m.Exam.course == student.course, m.Exam.semester == student.semester, m.Exam.exam_date >= date.today())) or 0,
        }
    return {
        "role": user.role, "total_students": total_students, "total_teachers": total_teachers,
        "total_departments": total_departments, "total_invoices": total_invoices,
        "fees_billed": float(billed), "fees_collected": float(collected), "fees_outstanding": float(pending),
        "collection_rate": round(float(collected) / float(billed) * 100) if billed else 0,
        "active_students": db.scalar(select(func.count(m.Student.id)).where(m.Student.status == "Active")) or 0,
    }


@app.get("/api/departments")
def departments(user: m.User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(m.Department).order_by(m.Department.name)).all()
    return [{"id": d.id, "code": d.code, "name": d.name, "hod_name": d.hod_name,
             "student_count": db.scalar(select(func.count(m.Student.id)).where(m.Student.department_id == d.id)) or 0} for d in rows]


@app.post("/api/departments")
def create_department(payload: DepartmentIn, user: m.User = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    dep = m.Department(code=payload.code.upper(), name=payload.name, hod_name=payload.hod_name)
    db.add(dep)
    try:
        db.flush()
        audit(db, user.id, "CREATE", "department", dep.id, dep.code)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Department code or name already exists")
    return {"id": dep.id, "code": dep.code, "name": dep.name, "hod_name": dep.hod_name, "student_count": 0}


@app.get("/api/students")
def students(
    search: str = "", department: str = "", page: int = 1, page_size: int = 20,
    user: m.User = Depends(get_current_user), db: Session = Depends(get_db)
):
    if user.role == "student":
        return {"items": [serialize_student(linked_student(db, user))], "total": 1, "page": 1, "page_size": 1}
    q = select(m.Student).join(m.Department, isouter=True)
    count_q = select(func.count(m.Student.id)).join(m.Department, isouter=True)
    if user.role == "teacher":
        teacher = db.scalar(select(m.Teacher).where(m.Teacher.user_id == user.id))
        if not teacher:
            raise HTTPException(status_code=403, detail="Teacher profile not configured")
        q = q.where(m.Student.department_id == teacher.department_id)
        count_q = count_q.where(m.Student.department_id == teacher.department_id)
    if search:
        pattern = f"%{search.strip()}%"
        filt = (m.Student.name.ilike(pattern) | m.Student.admission_no.ilike(pattern) | m.Student.email.ilike(pattern))
        q = q.where(filt); count_q = count_q.where(filt)
    if department:
        q = q.where(m.Department.code == department); count_q = count_q.where(m.Department.code == department)
    page = max(1, page); page_size = min(max(1, page_size), 100)
    total = db.scalar(count_q) or 0
    rows = db.scalars(q.order_by(m.Student.id).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [serialize_student(s) for s in rows], "total": total, "page": page, "page_size": page_size}


@app.post("/api/students")
def create_student(payload: StudentIn, user: m.User = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    dep = db.scalar(select(m.Department).where(m.Department.code == payload.department_code.upper()))
    if not dep:
        raise HTTPException(status_code=422, detail="Unknown department code")
    student = m.Student(admission_no=payload.admission_no.strip().upper(), name=payload.name.strip(), email=str(payload.email), phone=payload.phone,
                        department_id=dep.id, course=payload.course.upper(), semester=payload.semester, academic_year=payload.academic_year, status=payload.status)
    db.add(student)
    try:
        db.flush(); audit(db, user.id, "CREATE", "student", student.id, student.admission_no); db.commit()
    except IntegrityError:
        db.rollback(); raise HTTPException(status_code=409, detail="Admission number already exists")
    return serialize_student(student)


def parse_student_csv(data: bytes):
    try:
        text = data.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        required = {"admission_no", "name", "email", "department_code", "course", "semester"}
        fields = set(reader.fieldnames or [])
        missing = required - fields
        if missing:
            raise HTTPException(status_code=422, detail=f"CSV missing required columns: {', '.join(sorted(missing))}")
        rows = list(reader)
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="Please upload a UTF-8 CSV file")
    if len(rows) > 5000:
        raise HTTPException(status_code=413, detail="Maximum 5,000 records per import")
    output = []
    seen = set()
    for index, row in enumerate(rows, start=2):
        item = {k: (v or "").strip() for k, v in row.items() if k}
        errors = []
        admission = item.get("admission_no", "").upper()
        if not admission: errors.append("Admission number is required")
        if admission in seen: errors.append("Duplicate admission number in file")
        seen.add(admission)
        if len(item.get("name", "")) < 2: errors.append("Name is required")
        if "@" not in item.get("email", ""): errors.append("Valid email is required")
        try:
            sem = int(item.get("semester", ""))
            if not 1 <= sem <= 12: raise ValueError()
        except ValueError:
            errors.append("Semester must be between 1 and 12"); sem = 1
        output.append({"row": index, "admission_no": admission, "name": item.get("name", ""), "email": item.get("email", ""),
                       "phone": item.get("phone", ""), "department_code": item.get("department_code", "").upper(),
                       "course": item.get("course", "").upper(), "semester": sem, "academic_year": item.get("academic_year", "2026-27"),
                       "errors": errors})
    return output


@app.post("/api/import/students/preview")
async def preview_student_import(file: UploadFile = File(...), user: m.User = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=415, detail="Upload a CSV file for the demonstration import")
    data = await file.read(5_000_001)
    if len(data) > 5_000_000:
        raise HTTPException(status_code=413, detail="File exceeds 5 MB")
    rows = parse_student_csv(data)
    known_departments = {d.code for d in db.scalars(select(m.Department)).all()}
    for row in rows:
        if row["department_code"] and row["department_code"] not in known_departments:
            row["errors"].append("Unknown department code")
    valid = sum(1 for x in rows if not x["errors"])
    return {"total": len(rows), "valid": valid, "invalid": len(rows) - valid, "rows": rows[:100], "message": "Preview only. No records have been changed."}


@app.post("/api/import/students/commit")
async def commit_student_import(file: UploadFile = File(...), user: m.User = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    data = await file.read(5_000_001)
    if len(data) > 5_000_000: raise HTTPException(status_code=413, detail="File exceeds 5 MB")
    rows = parse_student_csv(data)
    created = updated = skipped = 0
    for row in rows:
        if row["errors"]:
            skipped += 1; continue
        dep = db.scalar(select(m.Department).where(m.Department.code == row["department_code"]))
        if not dep:
            skipped += 1; continue
        existing = db.scalar(select(m.Student).where(m.Student.admission_no == row["admission_no"]))
        if existing:
            existing.name = row["name"]; existing.email = row["email"]; existing.phone = row["phone"]
            existing.department_id = dep.id; existing.course = row["course"]; existing.semester = row["semester"]
            existing.academic_year = row["academic_year"]; updated += 1
        else:
            db.add(m.Student(admission_no=row["admission_no"], name=row["name"], email=row["email"], phone=row["phone"], department_id=dep.id,
                             course=row["course"], semester=row["semester"], academic_year=row["academic_year"], status="Active"))
            created += 1
    audit(db, user.id, "IMPORT", "students", "csv", f"created={created}; updated={updated}; skipped={skipped}")
    db.commit()
    return {"created": created, "updated": updated, "skipped": skipped, "message": "Import completed. Review skipped rows in the preview before retrying."}


@app.post("/api/integrations/students/sync")
def trusted_erp_sync(payload: SyncPayload, x_integration_key: str = Header(default=""), db: Session = Depends(get_db)):
    if not secrets.compare_digest(x_integration_key, settings.integration_api_key):
        raise HTTPException(status_code=401, detail="Invalid integration key")
    created = updated = skipped = 0
    for row in payload.records:
        required = ["admission_no", "name", "email", "department_code", "course", "semester"]
        if any(not str(row.get(k, "")).strip() for k in required):
            skipped += 1; continue
        dep = db.scalar(select(m.Department).where(m.Department.code == str(row["department_code"]).upper()))
        if not dep:
            skipped += 1; continue
        try:
            sem = int(row["semester"])
            if sem < 1 or sem > 12: raise ValueError()
        except (ValueError, TypeError):
            skipped += 1; continue
        admission = str(row["admission_no"]).strip().upper()
        student = db.scalar(select(m.Student).where(m.Student.admission_no == admission))
        if student:
            student.name = str(row["name"])[:160]; student.email = str(row["email"])[:255]
            student.phone = str(row.get("phone", ""))[:30]; student.department_id = dep.id
            student.course = str(row["course"]).upper()[:100]; student.semester = sem
            student.academic_year = str(row.get("academic_year", "2026-27"))[:20]
            updated += 1
        else:
            db.add(m.Student(admission_no=admission, name=str(row["name"])[:160], email=str(row["email"])[:255], phone=str(row.get("phone", ""))[:30],
                             department_id=dep.id, course=str(row["course"]).upper()[:100], semester=sem,
                             academic_year=str(row.get("academic_year", "2026-27"))[:20], status="Active"))
            created += 1
    audit(db, None, "ERP_SYNC", "students", payload.source, f"created={created}; updated={updated}; skipped={skipped}")
    db.commit()
    return {"created": created, "updated": updated, "skipped": skipped}


@app.get("/api/teachers")
def teachers(user: m.User = Depends(require_roles("admin", "management")), db: Session = Depends(get_db)):
    rows = db.scalars(select(m.Teacher)).all()
    return [{"id": t.id, "staff_code": t.staff_code, "name": t.user.full_name, "email": t.user.email,
             "department": t.department.name if t.department else "Unassigned", "designation": t.designation} for t in rows]


@app.get("/api/attendance")
def attendance(user: m.User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = select(m.Attendance).join(m.Student)
    if user.role == "student":
        s = linked_student(db, user); q = q.where(m.Attendance.student_id == s.id)
    elif user.role == "teacher":
        t = db.scalar(select(m.Teacher).where(m.Teacher.user_id == user.id))
        if not t: raise HTTPException(status_code=403, detail="Teacher profile not configured")
        q = q.where(m.Student.department_id == t.department_id)
    rows = db.scalars(q.order_by(m.Attendance.session_date.desc()).limit(300)).all()
    return [{"id": a.id, "student_id": a.student_id, "student_name": a.student.name, "admission_no": a.student.admission_no,
             "subject": a.subject, "date": a.session_date.isoformat(), "status": a.status} for a in rows]


@app.post("/api/attendance/bulk")
def mark_attendance(payload: AttendanceIn, user: m.User = Depends(require_roles("teacher", "admin")), db: Session = Depends(get_db)):
    teacher = db.scalar(select(m.Teacher).where(m.Teacher.user_id == user.id)) if user.role == "teacher" else None
    count = 0
    for record in payload.records:
        student = db.get(m.Student, record.student_id)
        if not student: continue
        if teacher and student.department_id != teacher.department_id:
            raise HTTPException(status_code=403, detail="You can only mark attendance for your assigned department")
        existing = db.scalar(select(m.Attendance).where(m.Attendance.student_id == student.id, m.Attendance.subject == payload.subject, m.Attendance.session_date == payload.session_date))
        if existing: existing.status = record.status; existing.marked_by = user.id
        else: db.add(m.Attendance(student_id=student.id, subject=payload.subject, session_date=payload.session_date, status=record.status, marked_by=user.id))
        count += 1
    audit(db, user.id, "MARK_ATTENDANCE", "attendance", payload.session_date.isoformat(), f"{count} records; {payload.subject}")
    db.commit()
    return {"saved": count}


@app.get("/api/exams")
def exams(user: m.User = Depends(get_current_user), db: Session = Depends(get_db)):
    student = db.scalar(select(m.Student).where(m.Student.user_id == user.id)) if user.role == "student" else None
    q = select(m.Exam)
    if student: q = q.where(m.Exam.course == student.course, m.Exam.semester == student.semester)
    rows = db.scalars(q.order_by(m.Exam.exam_date)).all()
    registrations = set()
    if student:
        registrations = set(db.scalars(select(m.ExamRegistration.exam_id).where(m.ExamRegistration.student_id == student.id)).all())
    return [{"id": e.id, "title": e.title, "subject": e.subject, "course": e.course, "semester": e.semester,
             "exam_date": e.exam_date.isoformat(), "registration_open": e.registration_open, "status": e.status,
             "registered": e.id in registrations} for e in rows]


@app.post("/api/exams/{exam_id}/register")
def register_exam(exam_id: int, user: m.User = Depends(require_roles("student")), db: Session = Depends(get_db)):
    student = linked_student(db, user)
    exam = db.get(m.Exam, exam_id)
    if not exam or exam.course != student.course or exam.semester != student.semester:
        raise HTTPException(status_code=404, detail="Eligible examination not found")
    if not exam.registration_open or exam.exam_date < date.today():
        raise HTTPException(status_code=400, detail="Registration is closed")
    exists = db.scalar(select(m.ExamRegistration).where(m.ExamRegistration.exam_id == exam.id, m.ExamRegistration.student_id == student.id))
    if exists: raise HTTPException(status_code=409, detail="Already registered")
    db.add(m.ExamRegistration(exam_id=exam.id, student_id=student.id)); audit(db, user.id, "EXAM_REGISTER", "exam", exam.id, exam.title); db.commit()
    return {"message": "Exam registration successful"}


@app.get("/api/fees")
def fees(user: m.User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = select(m.FeeInvoice).join(m.Student)
    if user.role == "student":
        s = linked_student(db, user); q = q.where(m.FeeInvoice.student_id == s.id)
    elif user.role == "teacher":
        raise HTTPException(status_code=403, detail="Fee information is not available to teacher accounts")
    rows = db.scalars(q.order_by(m.FeeInvoice.due_date.desc()).limit(500)).all()
    return [serialize_invoice(inv) for inv in rows]


@app.post("/api/fees/invoices")
def create_fee_invoice(payload: FeeInvoiceIn, user: m.User = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    student = db.get(m.Student, payload.student_id)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    if payload.semester != student.semester:
        raise HTTPException(status_code=422, detail="Invoice semester must match the student's current semester")
    invoice = m.FeeInvoice(student_id=student.id, description=payload.description, category=payload.category,
                           semester=payload.semester, due_date=payload.due_date, total_amount=payload.total_amount,
                           amount_paid=Decimal("0.00"), status="Pending")
    db.add(invoice); db.flush()
    audit(db, user.id, "CREATE", "fee_invoice", invoice.id, f"student={student.admission_no}; amount={payload.total_amount}")
    db.commit()
    return serialize_invoice(invoice)


@app.get("/api/payments")
def payment_history(user: m.User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = select(m.Payment).join(m.FeeInvoice).join(m.Student)
    if user.role == "student":
        student = linked_student(db, user)
        q = q.where(m.FeeInvoice.student_id == student.id)
    elif user.role == "teacher":
        raise HTTPException(status_code=403, detail="Payment information is not available to teacher accounts")
    rows = db.scalars(q.order_by(m.Payment.created_at.desc()).limit(500)).all()
    return [{"id": p.id, "reference": p.reference, "student_name": p.invoice.student.name,
             "admission_no": p.invoice.student.admission_no, "invoice_id": p.invoice_id,
             "description": p.invoice.description, "amount": float(p.amount), "method": p.method,
             "status": p.status, "created_at": p.created_at.isoformat()} for p in rows]


@app.post("/api/fees/{invoice_id}/demo-payment")
def demo_payment(invoice_id: int, payload: PaymentIn, user: m.User = Depends(require_roles("student")), db: Session = Depends(get_db)):
    student = linked_student(db, user)
    invoice = db.get(m.FeeInvoice, invoice_id)
    if not invoice or invoice.student_id != student.id:
        raise HTTPException(status_code=404, detail="Fee invoice not found")
    balance = Decimal(invoice.total_amount) - Decimal(invoice.amount_paid)
    if payload.amount > balance:
        raise HTTPException(status_code=422, detail="Payment cannot exceed the outstanding balance")
    if payload.amount <= 0:
        raise HTTPException(status_code=422, detail="Payment amount must be positive")
    # This is explicitly a simulated transaction. It must be replaced by server-verified gateway webhooks in production.
    payment = m.Payment(invoice_id=invoice.id, reference=f"DEMO-{uuid.uuid4().hex[:12].upper()}", amount=payload.amount, method="Simulated payment", status="Success")
    invoice.amount_paid = Decimal(invoice.amount_paid) + payload.amount
    invoice.status = "Paid" if invoice.amount_paid >= invoice.total_amount else "Partial"
    db.add(payment); audit(db, user.id, "DEMO_PAYMENT", "payment", payment.reference, f"invoice={invoice.id}; amount={payload.amount}"); db.commit()
    return {"message": "Demo payment completed", "reference": payment.reference, "invoice": serialize_invoice(invoice)}


@app.get("/api/announcements")
def announcements(user: m.User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(m.Announcement).order_by(m.Announcement.created_at.desc()).limit(50)).all()
    visible = [a for a in rows if a.audience == "All" or a.audience.lower() == ("students" if user.role == "student" else user.role)]
    return [{"id": a.id, "title": a.title, "content": a.content, "audience": a.audience, "created_at": a.created_at.isoformat(),
             "author": db.get(m.User, a.author_id).full_name if db.get(m.User, a.author_id) else "College"} for a in visible]


@app.post("/api/announcements")
def create_announcement(payload: AnnouncementIn, user: m.User = Depends(require_roles("admin", "management", "teacher")), db: Session = Depends(get_db)):
    item = m.Announcement(title=payload.title, content=payload.content, audience=payload.audience, author_id=user.id)
    db.add(item); db.flush(); audit(db, user.id, "PUBLISH", "announcement", item.id, item.title); db.commit()
    return {"id": item.id, "title": item.title, "content": item.content, "audience": item.audience, "created_at": item.created_at.isoformat(), "author": user.full_name}


@app.get("/api/reports/overview")
def report_overview(user: m.User = Depends(require_roles("admin", "management")), db: Session = Depends(get_db)):
    dept_rows = db.execute(select(m.Department.name, func.count(m.Student.id)).join(m.Student, m.Student.department_id == m.Department.id, isouter=True).group_by(m.Department.id)).all()
    return {"departments": [{"name": name, "students": count} for name, count in dept_rows],
            "recent_activity": [{"action": x.action, "entity": x.entity, "details": x.details, "created_at": x.created_at.isoformat()}
                                for x in db.scalars(select(m.AuditLog).order_by(m.AuditLog.created_at.desc()).limit(12)).all()]}


@app.get("/api/audit-logs")
def audit_logs(user: m.User = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    rows = db.scalars(select(m.AuditLog).order_by(m.AuditLog.created_at.desc()).limit(100)).all()
    return [{"id": x.id, "actor": db.get(m.User, x.actor_id).full_name if x.actor_id and db.get(m.User, x.actor_id) else "Integration/System",
             "action": x.action, "entity": x.entity, "entity_id": x.entity_id, "details": x.details, "created_at": x.created_at.isoformat()} for x in rows]
