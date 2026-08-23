# Phase 2G Legacy Placement Audit

## Canonical rule

`Student` remains the permanent identity. `StudentEnrollment` is the canonical
source for year, level, class, and section whenever a business operation has a
year or exam context. The legacy placement columns on `students` are retained
for compatibility and are not dropped or mass-updated.

## Converted operational reads

- Results Hub student scopes, class rosters, rank calculation, student views,
  whole-class exports, and analytics projections use the selected year's
  enrollment first.
- Attendance hall rosters, status updates, bulk attendance, and attendance
  session payloads validate and display the selected year's enrollment.
- Seat Arrangement and Seat Mixer candidate lists, metrics, saved-layout
  projections, and print output resolve the hall exam's academic year.
- Public result, print, download, feedback subject, incident subject, and
  historical verification flows resolve the exam or ID-card year before using
  a student placement.
- Student/result import templates and result imports scope classes and subject
  columns to the selected academic year and enrollment.
- Admin incident and feedback/complaint class filters use the row's exam year
  when one is present, rather than the student's mutable current class.

## Compatibility-only reads that remain intentionally

- `app/enrollment_service.py` contains the legacy fallback and the legacy-field
  synchronization used for pre-Phase-2 records and older database schemas.
- `app/academic_hierarchy.py` keeps legacy-field reads inside the explicit
  hierarchy backfill/migration path.
- Student edit/import transition code mirrors a selected enrollment to legacy
  columns so older routes and integrations continue to function.
- Nullable historical feedback, complaint, and incident rows without an
  `exam_id` use the existing legacy placement fallback because their original
  year is not stored on those rows. These ambiguous records are not rewritten.
- Teacher permission/resource checks and a few legacy templates still expose
  compatibility fields where no historical year context is available; their
  year-aware student query paths are already enrollment-first.

## Safety notes

No legacy placement column was removed, nulled, or destructively remapped.
Historical `Result`, `AttendanceRecord`, feedback, complaint, incident, hall,
and seat rows were not rewritten. The remaining compatibility reads are
documented here for a future separate cleanup after production historical data
has been verified.
