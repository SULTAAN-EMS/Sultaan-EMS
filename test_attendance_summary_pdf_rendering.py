import io
import unittest
from datetime import date, timedelta

from pypdf import PdfReader

from app.report_theme import build_attendance_report_pdf


class AttendanceSummaryPdfRenderingTests(unittest.TestCase):
    def test_zero_status_segments_render_for_five_day_four_hall_report(self):
        first_day = date(2026, 10, 17)
        subjects_per_session = 5
        students = []
        for hall_offset in range(4):
            events = []
            for day_offset in range(5):
                event_date = first_day + timedelta(days=day_offset)
                events.append({
                    "date": event_date.isoformat(),
                    "session": "Fadhiga 1aad",
                    "hall": f"Hall {hall_offset + 21}",
                    "level_name": "Dugsi Sare",
                    "class_name": "Form 4",
                    "subjects": [
                        {
                            "name": f"Maaddo {subject + 1}",
                            "status": "late" if hall_offset == 0 and day_offset == 0 and subject == 0 else "present",
                        }
                        for subject in range(subjects_per_session)
                    ],
                })
            students.append({
                "full_name": f"Arday Tijaabo {hall_offset + 1}",
                "student_code": f"TIS99{hall_offset}",
                "level_name": "Dugsi Sare",
                "class_name": "Form 4",
                "events": events,
            })

        totals = {
            "sessions": 20,
            "students": 20,
            "slots": 100,
            "marked": 100,
            "present": 99,
            "late": 1,
            "absent": 0,
            "absent_students": 0,
            "excused": 0,
            "sick": 0,
            "emergency": 0,
            "unmarked": 0,
        }
        report = {
            "year": "2026-2027",
            "exam_name": "Imtixaan Bileedka 1aad",
            "start_date": "Talaado, Oktoobar 17, 2026",
            "end_date": "Sabti, Oktoobar 21, 2026",
            "hall_label": "4 halls",
            "hall_count": 4,
            "session_count": 20,
            "totals": totals,
            "hall_totals": [
                {"hall": f"Hall {hall}", **{
                    **totals,
                    "sessions": 5,
                    "students": 5,
                    "slots": 25,
                    "marked": 25,
                    "present": 24 if hall == 21 else 25,
                    "late": 1 if hall == 21 else 0,
                }}
                for hall in range(21, 25)
            ],
            "day_totals": [
                {
                    "date": (first_day + timedelta(days=day)).isoformat(),
                    **{
                        **totals,
                        "sessions": 4,
                        "students": 4,
                        "slots": 20,
                        "marked": 20,
                        "present": 19 if day == 0 else 20,
                        "late": 1 if day == 0 else 0,
                    },
                }
                for day in range(5)
            ],
            "routine_students": students,
            "exception_students": [],
            "unmarked_students": [],
        }

        pdf = build_attendance_report_pdf({
            "report": report,
            "today": first_day,
            "settings": {},
        })
        self.pdf_bytes = pdf

        reader = PdfReader(io.BytesIO(pdf))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        self.assertGreaterEqual(len(reader.pages), 5)
        self.assertIn("Arday Tijaabo 4", text)
        self.assertIn("TIS990", text)
        self.assertIn("Maaddo 5", text)


if __name__ == "__main__":
    unittest.main()
