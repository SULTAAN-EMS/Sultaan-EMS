# SULTAAN EMS - Full System Discovery and Dashboard Redesign Audit

Audit date: 2026-09-29
Scope: static code, route, template, model, migration, permission, and configuration discovery
Mode: read-only discovery. No production code, UI, schema, or data was changed for this audit.

## Executive Summary

SULTAAN EMS is a Flask and SQLAlchemy school operations platform with a server-rendered Jinja UI and vanilla JavaScript/CSS. It is no longer only an examination result system. The current product contains these connected domains:

- Academic structure and year-aware student enrollment
- Student management, imports, exports, profile photos, locking, and permanent purge
- Examination setup, result entry, publication, reports, imports, exports, and verification
- Promotion rules, immutable evaluations, outcome application, and enrollment movement
- Behavior taxonomy, events, behavior grading, and attendance with time and note history
- Exam attendance, halls, timetables, invigilators, incidents, and public incident submission
- Teachers, teacher permissions, teacher portal, teacher analytics, and activity history
- ID cards, QR verification, print/PDF output, issue dates, expiry, and status
- Seat arrangement and Seat Mixer with immutable layout revisions and QR-linked verification
- Feedback, complaints, settings, translations, loading/success overlays, and audit logs

The main product risk for a future dashboard is not lack of data. It is context selection and data-boundary consistency. The authoritative placement source is `StudentEnrollment` plus the `AcademicYear*` hierarchy. Legacy `Student` placement fields and legacy result/attendance models remain present for compatibility. A dashboard must select one year, level, class, section, and exam context before aggregating data and must show the scope used for every number.

## A. System Inventory

### Technology and runtime

- Python 3.11+ application using Flask 3.0, Flask-Login, Flask-WTF/CSRF, Flask-SQLAlchemy, and Jinja templates.
- SQLAlchemy models support SQLite for local development and PostgreSQL/MySQL provider URLs for deployed environments.
- Gunicorn is the deployed WSGI entry point. Render/Railway-style startup runs explicit migration scripts before Gunicorn.
- OpenPyXL handles Excel import/export. ReportLab and xhtml2pdf are used for PDF/print paths. QR codes use `qrcode[pil]`.
- Cloudinary is supported for image upload; local static uploads are also used.
- WhiteNoise serves static assets in deployed mode.
- Frontend is server-rendered HTML with vanilla JS and CSS. There is no React/Vue SPA or separate API service.

### Application composition

`app/__init__.py` creates the app, registers the database, CSRF, login manager, context processors, error handling, deployment-aware schema behavior, and these blueprints:

| Blueprint | Prefix | Responsibility |
| --- | --- | --- |
| `public_bp` | none | Student result portal, public APIs, QR and report verification, feedback, complaints, incident form |
| `auth_bp` | none | Admin login/logout, password change and authentication |
| `admin_bp` | `/admin` | Core admin dashboard, students, legacy results, settings, users, incidents, invigilators, configuration center, promotion rules |
| `attendance_bp` | `/admin/attendance` | Exam attendance, sessions, halls, timetable, roster, marks, exports |
| `behavior_bp` | `/admin/behavior` | Behavior setup, taxonomy, events, attendance, reports, audit/history |
| `id_cards_bp` | `/admin/id-cards` | ID card settings, generation, printing and PDF export |
| `advanced_results_bp` | `/admin/advanced-results` | Year-aware academic records, result entry, analytics, grade management, student management and verification slips |
| `teachers_bp` | `/admin/teachers` | Teacher administration, assignments, performance, activity, export and print |
| `teacher_portal_bp` | `/teacher` | Teacher login, assigned students/results, analysis, reports and exports |
| `academic_bp` | `/admin/academic-structure` | Legacy academic levels, classes and sections CRUD |
| `invigilator_bp` | `/invigilator` | Invigilator login, password/profile workflows |
| `seat_arrangement_bp` | `/admin/seat-arrangement` | Halls, seating builder, generation, optimization, save, print |
| `seat_mixer_bp` | `/admin/seat-mixer` | Halls, versions, saved revisions, layout builder, class colors, history and print |

### UI and asset inventory

The application contains about 156 templates/assets in the current tree. Important template areas include:

- Admin: dashboard, students, student form/view, result entry, results dashboard, analytics, grade management, setup, promotion rules, attendance, behavior, ID cards, teachers, invigilators, incidents, seat arrangement, Seat Mixer, settings, audit logs, users and feedback/complaints.
- Public: result portal, report view, print/download, QR landing, ID verification, incident form/success, locked result and portal widgets.
- Teacher: dashboard, subjects, classes, results, reports, analysis, grades, pass/fail, top performers, weak students, trends and comparison.
- Static assets: shared `app.css`, domain styles for behavior/results/promotion/seat mixer/verification/incident forms, admin loader and success overlays, plus JavaScript for autosave, loading UI, seat mixer, teacher dashboard, feedback widget and result animations.

## B. Module and Feature Inventory

### 1. Academic structure and configuration

The configuration center supports academic years, exam types, year-aware levels, classes, sections, subjects, promotion rules, result marking defaults, system defaults, archive/restore, dependencies, and huge-force-delete operations. A legacy academic structure area separately manages `AcademicLevel`, `AcademicClass`, and `AcademicSection`.

The year-aware path is the preferred source for new workflows:

`AcademicYear -> AcademicYearLevel -> AcademicYearClass -> AcademicSection`

Subjects are scoped through `AcademicYearSubject` to a year and level, with `subject_kind` (`exam` or `behavior`), maximum score, order, and active state.

### 2. Student management

Student management supports:

- Student code, full name, mother name, phone, gender, photo, free note, result lock/reason, active state.
- Year-aware enrollment with status, outcome, source, previous enrollment, exit date, and notes.
- Class/level/section placement, transfer/promotion/repeat movements, and compatibility snapshots on `Student`.
- New-student form, edit, permanent deletion, locking, Excel import/template/export, verification slip generation and student data endpoint.
- Student code aliases preserve an old ID after a code change without rewriting historical records.

For current placement, use `StudentEnrollment`. The legacy fields on `Student` are a compatibility projection and can disagree if old data was not migrated or a historical context is queried incorrectly.

### 3. Examination and result system

The result domain includes:

- Legacy result list/edit/save/delete/import/export/report paths in `routes_admin.py`.
- Year-aware result dashboard, class roster, student view, result entry, autosave, save, Excel import/export, PDF/class report exports, analytics, grade drill-down, grade management and report tier configuration in `routes_advanced_results.py`.
- `Exam` stores name, code, year, weight, active/final flags, optional legacy level/class/section scope, and publication state.
- `Result` stores student, exam, subject, score, grade override, comment and publication state with a student/exam/subject uniqueness constraint.
- `ExamMarkingConfiguration` stores an exact default maximum mark for year, year-level and exam.
- `GradeScale` stores grade ranges, comments, grade points, pass state and badge colors.
- `ReportVerification` and token-based public routes support report authenticity checks.

Important distinction: the core admin dashboard's current `published` card counts published exams, not the number of published result rows. That label is potentially misleading for a redesigned dashboard.

### 4. Promotion and academic outcomes

Promotion is year-, level-, and exam-aware. It supports:

- Global enable/disable setting.
- Promotion rules with overall and critical-subject thresholds.
- Critical subject associations.
- New student evaluation and re-evaluation.
- Immutable `PromotionEvaluation` snapshots with context/rule JSON, percentage, base/final outcome, status, critical-subject evidence and timestamp.
- Separate `PromotionOutcomeApplication` records for applying passed/failed outcomes, promotion, repeat or graduation.
- Enrollment movement ledger for local transfer, cross-year transfer, promotion and repeat.
- Operational status, scope summaries, consistency audit, evaluation detail, apply and transition plans.

The architecture deliberately separates evidence snapshots from actions. A dashboard should display evaluated, incomplete, invalid, outcome-applied and transition-completed counts separately instead of collapsing them into one pass/fail number.

### 5. Behavior and attendance

Behavior has its own configuration, sessions, natural-score grade bands, taxonomy, event responses and attendance integration.

- Taxonomy: category -> subcategory -> action -> action choice.
- Events record student/enrollment/config/session, category/action/choice, polarity, points, status, timestamp, notes, response snapshots, idempotency and void metadata.
- Attendance statuses have key, label, polarity, points, contribution flag, order and active state.
- Attendance records are year/level/class/enrollment/session/date scoped and retain status snapshots, points, note, auto-note flag, marking user, attendance time, arrival time, late minutes, void/delete data.
- Deleted behavior attendance rows are copied to `BehaviorAttendanceDeletion` as a read-only tombstone before source deletion.
- Behavior reports project session scores and attendance points through `behavior_service` and `behavior_reporting`; global result grade scales are intentionally not used for new Behavior sessions.
- Public student routes expose selected behavior and attendance reads when the selected report context is valid.

There are two attendance domains: the behavior attendance system and the exam-hall `AttendanceRecord` system. A future dashboard must label them explicitly because “attendance rate” can mean different things.

### 6. Exam operations

Exam operations support:

- Exam types, sessions, timetable subject assignments, halls and hall subjects.
- Hall roster assignment, transfer, remove and class removal.
- Exam attendance status and bulk status marking.
- Excel/PDF export and print sheets.
- Exam invigilator accounts, roles, activation windows, lock state, password reset/change and login history.
- Public incident-report submission using a token, status flow and success page.

### 7. Teachers and staff

Teacher administration stores identity/contact/employment/qualification/department/school-level data, photo, linked user account, active state and last login/logout. It supports assignments to legacy subjects, year-independent academic classes/sections/levels, teacher permissions, activity history, performance, export and print.

The teacher portal has role-specific pages for assigned subjects/classes, students, published results, examination analysis, subject analysis, grades, pass/fail, top performers, weak students, comparison, trends, reports, Excel/PDF/print export and an AI-insights page. Teacher assignment scoping is enforced in `teacher_analytics.py` before results are loaded.

### 8. ID cards and verification

ID cards support settings for template, colors, layout, typography, logo/photo/QR positions, barcode, issue months, footer/signature/stamp and print margin. Generation is single or bulk, with print and PDF export.

`IdCardIssue` stores token, student, academic year, issue date, expiry date, status and template. QR payloads are reused by the Seat Mixer print path and public ID verification. Expiry is derived/synchronized when ID-card screens are read or printed, so dashboard counts should use effective status, not only the stored status field.

### 9. Feedback, complaints and settings

The public portal can submit feedback and complaints tied to a student and optional exam. Admin can mark delivered/read, reply and delete. Settings store school identity, logo paths, dashboard theme/colors, report/print/verification settings, ID-card settings, attendance colors/icons, result portal settings, language, social links, upload destinations and animation preferences.

`LabelTranslation` provides database-driven labels. The app context processor injects UI settings, translations, current language, direction, permissions and asset URL resolution into templates.

### 10. Audit and deletion

Audit log rows record username, IP, action and details and are intentionally retained for accountability. Student purge and academic-year purge have read-only scan functions, dependency graphs, unknown-dependency detection and explicit confirmation requirements. Student purge is designed to remove linked operational records while retaining audit logs. Academic-year purge must be treated as a destructive administrative operation and must be excluded from ordinary dashboard quick actions.

## C. Route and API Map

### Admin core

`/admin/` dashboard; `/admin/students`, `/admin/students/new`, edit/delete/lock/import/template/export; `/admin/results`, result save/edit/delete/import/template/export; `/admin/reports/<student>/<exam>`; `/admin/classes`; `/admin/subjects`; `/admin/exams`; `/admin/academic-years`; `/admin/users`; `/admin/settings`; `/admin/audit-logs`; `/admin/falcelin-cabasho`; `/admin/incidents`; `/admin/invigilators`; `/admin/incident-settings`; `/admin/config-center`; `/admin/promotion-rules` and its evaluation/application/transition endpoints.

### Attendance and behavior APIs

Attendance endpoints cover exam types, sessions, timetable data, session subjects, halls, levels/classes, hall roster data, assign/transfer/remove, attendance data, single/bulk mark, Excel/PDF export and print.

Behavior endpoints cover scope, subject/configuration/session setup, categories/subcategories/actions/choices, students/detail/report, event list/edit/detail/void/restore/history/audit, attendance records, attendance view, attendance record CRUD/void/restore/delete, attendance report and status/active-day updates.

### Advanced results APIs/pages

The advanced-results blueprint provides setup, dashboard, roster, student view, bulk save, exports, result entry/autosave/save/import/template, analytics/results report/grade drill-down, grade configuration, results settings, student management/transition/import/template/export, verification slips and delete/lock operations.

### Public and portal routes

Public routes include `/`, `/result`, `/result/view/<student_code>/<exam_id>`, `/api/results/<student_code>`, `/behavior/.../read`, `/behavior/.../attendance/read`, `/print/<student_code>`, `/download/<student_code>`, feedback/complaint APIs, top-students, `/verify/<token>`, `/verify-id/<token>`, `/qr/<token>`, and `/incident-report/<token>`.

## D. Data and Relationship Map

### Authoritative identity and placement

- Identity: `Student.id` is the relational identity; `Student.student_code` is the current user-facing code.
- Historical code resolution: `StudentCodeAlias` maps old code to the same student.
- Placement: `StudentEnrollment` is authoritative for a requested academic year.
- Year-aware structure: `AcademicYear`, `AcademicYearLevel`, `AcademicYearClass`, `AcademicSection`, `AcademicYearSubject`.
- Movement: `StudentEnrollmentMovement` is an immutable placement history.
- Compatibility: `Student.academic_year_id`, `academic_level_id`, `academic_class_id`, `academic_section_id`, `level`, `section`, and `class_id` mirror older reads and can be stale outside the selected year.

### Result data flow

`AcademicYearSubject` defines the year/level subject offering and maximum. `Exam` defines the exam context and publication. `Result` stores the score against a legacy `Subject` identity for compatibility. Result entry/import resolves the selected year/level/exam subject bindings and writes/upserts score rows. Reports and portal reads filter publication and selected exam context.

### Behavior data flow

Configuration owns sessions, status policies, taxonomy and grade bands. Events and behavior attendance rows are scoped to enrollment/session and retain snapshots. `behavior_service` calculates session and annual projections; `behavior_reporting` serializes those projections for admin/student-facing reports; promotion can consume behavior evidence through the behavior promotion adapter.

### Operational data flow

Exam sessions feed halls and hall subjects. Halls feed roster assignments and exam attendance. Seat Mixer versions and snapshots feed printed arrangements and QR-linked verification. ID-card issues provide reusable QR tokens; public verification resolves those tokens and may include seat placement for an exam context.

## E. Dashboard Metric Availability Matrix

Legend:

- AVAILABLE DIRECTLY: a persisted field or query already represents the metric in a stable scope.
- CALCULABLE: source rows exist, but the metric needs an explicit aggregation/query and context rules.
- NOT CURRENTLY AVAILABLE: no reliable persisted source or complete business definition was found.

### Core population and placement

| Metric | Status | Source / note |
| --- | --- | --- |
| Total student identities | AVAILABLE DIRECTLY | Count `Student` rows, with active/inactive stated. |
| Active students in selected year | AVAILABLE DIRECTLY | `StudentEnrollment` plus active student filter. |
| Students by school stage | CALCULABLE | `AcademicYearLevel.school_stage`; aggregate year-scoped enrollments. |
| Students by level/class/section | CALCULABLE | `StudentEnrollment` joins year-aware hierarchy. |
| New enrollments this period | CALCULABLE | `StudentEnrollment.enrolled_at` and source. |
| Transfers/promotions/repeats | CALCULABLE | `StudentEnrollmentMovement`. |
| Withdrawn/completed/archived enrollment counts | CALCULABLE | Enrollment status. |
| Placement inconsistency count | CALCULABLE | Existing `audit_student_enrollment_consistency()` logic. |
| Missing placement records | CALCULABLE | Legacy-only student/enrollment audit. |

### Results and academics

| Metric | Status | Source / note |
| --- | --- | --- |
| Exam count by year | AVAILABLE DIRECTLY | `Exam` by `academic_year_id`. |
| Published exam count | AVAILABLE DIRECTLY | `Exam.is_published`; do not label as published result rows. |
| Result row publication count | CALCULABLE | `Result.is_published` by exam/year. |
| Result completion by class/exam | CALCULABLE | Compare expected year subjects/enrollments to `Result` rows. |
| Average score/percentage | CALCULABLE | Score divided by resolved subject maximum. |
| Pass/fail count | CALCULABLE | Grade scale/rule context required. |
| Grade distribution | CALCULABLE | Existing advanced analytics and grade-scale functions. |
| Subject average and pass rate | CALCULABLE | Existing teacher analytics and result reports. |
| Top/weak students | CALCULABLE | Existing top-performer/weak-student queries; scope and publication required. |
| Locked result count | AVAILABLE DIRECTLY | `Student.is_result_locked` exists; current dashboard counts current-year distinct active students. |
| Missing results per student | CALCULABLE | Expected subject bindings versus result rows. |
| Evaluation status counts | CALCULABLE | `PromotionEvaluation.evaluation_status`. |
| Current promotion outcomes | CALCULABLE | Evaluation plus outcome application and enrollment outcome. |
| Re-evaluation changes | CALCULABLE | Immutable evaluations can be compared by student/context/evaluated_at. |
| Result import errors | CALCULABLE | Request result only; no durable import batch/audit entity found. |

### Attendance and behavior

| Metric | Status | Source / note |
| --- | --- | --- |
| Exam attendance present/absent/late | CALCULABLE | Legacy/exam `AttendanceRecord`, scoped by exam session/hall/date. |
| Exam attendance rate | CALCULABLE | Existing teacher attendance-rate logic exists but uses legacy attendance rows. |
| Behavior attendance status counts | AVAILABLE DIRECTLY | `BehaviorAttendanceRecord` snapshots and status keys. |
| Behavior attendance rate | CALCULABLE | Define whether present/late/excused count as attended; behavior and exam attendance differ. |
| Attendance by hour/date | AVAILABLE DIRECTLY | Behavior record has attendance and arrival time/date; aggregate by context. |
| Late minutes total/average | AVAILABLE DIRECTLY | `late_by_minutes` exists on behavior attendance records. |
| Attendance score | CALCULABLE | `behavior_service` canonical projection and attendance allocation. |
| Behavior points positive/negative | CALCULABLE | `BehaviorEvent.points_applied` and attendance points. |
| Behavior event count by category/action | CALCULABLE | Taxonomy snapshots on events. |
| Voided/restored/deleted attendance activity | CALCULABLE | Active rows plus void metadata and deletion tombstones. |
| Behavior grade/pass status | CALCULABLE | Session-owned grade bands and natural scores. |
| Behavior trend over time | CALCULABLE | Events/attendance timestamps exist; define time bucket and scope. |
| Live attendance currently in progress | NOT CURRENTLY AVAILABLE | No durable live-presence/session heartbeat metric found. |

### Operations, people and security

| Metric | Status | Source / note |
| --- | --- | --- |
| Active teachers | AVAILABLE DIRECTLY | `Teacher.employment_status`, `is_active`. |
| Teachers by department/school level | CALCULABLE | Teacher fields. |
| Teacher assignment coverage | CALCULABLE | Teacher/class/section/subject join tables. |
| Teacher activity count/recent activity | AVAILABLE DIRECTLY | `TeacherActivity`. |
| Active invigilators | AVAILABLE DIRECTLY | `ExamInvigilator.status`, `is_active`, active date window. |
| Failed/locked invigilator logins | AVAILABLE DIRECTLY | `InvigilatorLoginHistory`. |
| Open incident reports | AVAILABLE DIRECTLY | `IncidentReport.status`. |
| Incidents by severity/category/status | CALCULABLE | Incident report, severity and category relations. |
| Incident response time | CALCULABLE | Incident date/time versus reviewed timestamp. |
| ID cards active/expired/blocked | CALCULABLE | `IdCardIssue` plus effective expiry synchronization. |
| Cards expiring soon | CALCULABLE | Issue expiry date and selected horizon. |
| Seat assignment coverage | CALCULABLE | `SeatMixerAssignment` / `SeatAssignment` versus scoped students. |
| Hall capacity utilization | CALCULABLE | Hall capacity versus assigned students. |
| Feedback/complaint unread counts | AVAILABLE DIRECTLY | Read/delivered fields and admin queries. |
| Audit events by action/user/time | AVAILABLE DIRECTLY | `AuditLog`. |
| Login success/failure for admin users | NOT CURRENTLY AVAILABLE | Admin login audit is not equivalent to invigilator login history; durable admin login event model was not found. |
| Backup health/last successful backup | NOT CURRENTLY AVAILABLE | Permission names exist, but no persisted backup job/health model was found in the inspected app. |
| Current system uptime/worker health | NOT CURRENTLY AVAILABLE | Deployment/runtime telemetry is outside the application data model. |

## F. Current Dashboard Audit

The current admin dashboard (`app/templates/admin/dashboard.html` and `admin.dashboard`) has:

- Six statistic cards: students, classes, exams, published and subjects, locked results.
- A chart repeating the same result-centric values.
- A recent-results table and activity timeline built from the newest `Result` rows.
- Current-year scoping for active students, classes, subjects and locked results.
- No explicit selected-scope banner beyond the current-year query.
- No attendance, behavior, incident, teacher, invigilator, ID-card, seating, feedback, complaint, promotion, import-health or schema-health view.
- No dashboard JSON endpoint; values are calculated server-side and rendered in one page.

### Dashboard risks

1. “Published” currently represents published exams, not published result rows.
2. “Classes” and “Subjects” are year-scoped, but other routes still have legacy unscoped models; a future dashboard must identify the source in the UI or query layer.
3. Student totals must use active enrollments in a selected year; counting `Student` alone can include students outside the selected academic context.
4. Attendance has two separate domains and two schemas; combining them without a label will produce misleading rates.
5. Promotion is snapshot/action based. The latest evaluation, applied outcome and current enrollment outcome are different facts.
6. Expiry-dependent ID card counts require effective status calculation, not just stored status.

## G. Role and Permission Audit

The permission registry includes dashboard, students, results, subjects, classes, exams, years, users, settings, import/export/reports/print, publication, result locks, attendance, ID cards, advanced results, teachers, QR verification, system settings, backup/restore, academic setup, seating, and behavior-specific permissions:

`behavior.view`, `behavior.manage`, `behavior.record`, `behavior.edit`, `behavior.void`, `behavior.configure`, `behavior.audit`.

Endpoint permissions are mapped centrally in `app/permissions.py`; the registered blueprints call `enforce_endpoint_permission()` or their domain-specific login decorator. Teacher permissions are a separate assignment set, and the teacher portal additionally scopes data by teacher assignments. Invigilator access is a separate session/authentication model.

### Dashboard role opportunities

- System administrator: global operational health, schema/data warnings, all modules and audit activity.
- Academic/results manager: selected-year result completion, publication, pass/fail, promotion readiness and imports.
- Behavior/attendance manager: selected-level attendance coverage, late/absence trends, behavior points, unresolved records and scoring readiness.
- Exam operations manager: timetable, halls, seating coverage, invigilator status, attendance and incidents.
- Teacher: assigned class/subject result completion, attendance and behavior views only within assignment scope.
- Invigilator: current exam/session, hall roster, attendance marking and incident quick actions; no broad admin metrics.

## H. Technical Architecture and Integration Notes

### Strengths

- Clear domain separation in modules and services.
- Year-aware enrollment and academic hierarchy are modeled explicitly.
- Promotion evidence is immutable and separate from outcome application.
- Behavior attendance keeps snapshots and deletion tombstones for auditability.
- Request-local caches exist in teacher analytics and behavior grading paths.
- Imports have dedicated validation/normalization paths for students and results.
- QR/verification tokens connect ID cards, reports, seats and public verification.
- Deployment configuration distinguishes local auto-initialization from production migration responsibility.

### Risks and constraints for dashboard work

- Legacy and canonical paths coexist. A dashboard query needs a documented canonical source and fallback policy.
- The app is server-rendered; high-frequency interactive widgets would need small JSON endpoints and careful query caching, not a client-only assumption.
- Several derived metrics currently exist inside service/report functions but are not persisted as daily snapshots. Historical trend charts may require grouping live rows or adding a future aggregate table.
- `AuditLog` is a general text log and is useful for activity, but it is not a typed event stream with guaranteed entity IDs.
- File uploads depend on local storage or Cloudinary configuration; a deployment dashboard should surface storage configuration/health only if a durable source is added.
- Production uses explicit migration scripts; `db.create_all` or local compatibility synchronization must not be used as a production dashboard-side repair mechanism.

### Recommended dashboard query boundary

Create a read-only dashboard service layer later with explicit context objects:

`DashboardScope(year_id, year_level_id, class_id, section_id, exam_id, behavior_session_id, date_from, date_to)`

Every card/query should receive this scope and return:

- value
- display label
- source context
- freshness/generated timestamp
- zero/empty state reason
- optional drill-down URL

This avoids mixing current-year student counts, legacy exam records, and behavior sessions from different scopes.

## I. Recommended Dashboard Product Design

### Global scope bar

Place year first, then level, class, section, exam/session and date range. Dependent selectors should clear downstream values when an upstream context changes. Show a visible “Data scope” summary beside the cards.

### Priority overview cards

1. Active students in scope.
2. Result completion percentage.
3. Published result rows and unpublished rows.
4. Pass/fail or evaluation readiness.
5. Attendance rate with explicit attendance domain label.
6. Open incidents and feedback/complaint items.
7. ID cards expiring soon.
8. Seating coverage for the selected exam/hall.

### Operational queues

Use actionable lists rather than decorative totals:

- Missing results by class/student.
- Promotion evaluations incomplete or changed since last evaluation.
- Attendance dates not marked or records needing correction.
- Incidents pending review.
- ID cards missing or expiring.
- Students with placement inconsistencies.
- Imports with rejected rows.

### Drill-down behavior

Every number should link to the existing filtered page: students management, result entry, analytics report, behavior attendance/report, incidents, ID cards, promotion evaluation, seating builder or audit logs. Do not create duplicate CRUD interfaces inside the dashboard.

### Empty and uncertainty states

Show “No data in this selected scope” when the context is valid but empty. Show “Configuration required” when a grade scale, behavior session, timetable, or placement is missing. Do not show zero for an unavailable metric.

## J. Audit Conclusion and Readiness

The application has enough persisted information to support a serious role-aware operations dashboard. The first dashboard release should prioritize canonical scope handling and read-only aggregates over visual polish. The safest implementation sequence is:

1. Define and reuse one year/level/class/section/exam scope contract.
2. Add read-only query/service functions with tests for canonical and legacy fallback behavior.
3. Add dashboard JSON endpoints only for cards and drill-down summaries that need live refresh.
4. Re-label existing cards so “published exam” and “published results” cannot be confused.
5. Add operational queues for incomplete data and unresolved configuration.
6. Add role-specific visibility through the existing permission map and teacher assignment scope.
7. Add query/index review and performance tests before adding frequent polling or animations.

No production UI or behavior was changed during this discovery audit. The only artifact produced is this report.

DISCOVERY AUDIT COMPLETE — READY FOR DASHBOARD PRODUCT DESIGN
