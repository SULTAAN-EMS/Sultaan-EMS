from flask import redirect, request, session, url_for

SUPPORTED_LANGUAGES = ("so", "en", "ar", "tr")

TRANSLATIONS = {
    "en": {
        "Dashboard": "Dashboard", "Students": "Students", "Results": "Results", "Subjects": "Subjects",
        "Classes": "Classes", "Exams": "Exams", "Academic Year": "Academic Year", "Users": "Users",
        "Settings": "Settings", "Logout": "Logout", "Search": "Search", "Student ID": "Student ID",
        "Phone Number": "Phone Number", "Print Report": "Print Report", "Invalid username or password.": "Invalid username or password.",
        "You have been logged out.": "You have been logged out.", "Settings saved.": "Settings saved.",
        "Unauthorized": "Unauthorized", "Verified": "Verified", "Invalid Result": "Invalid Result",
        "Online Exam Result Management": "Online Exam Result Management", "Results Portal": "Results Portal",
        "Secure": "Secure", "Student ID lookup": "Student ID lookup", "One-page report": "One-page report",
        "Live": "Live", "Database updates": "Database updates", "Find Student Result": "Find Student Result",
        "Enter the official Student ID to view the published report.": "Enter the official Student ID to view the published report.",
        "Phone number verification failed.": "Phone number verification failed.",
    },
    "so": {
        "Dashboard": "Dashboard", "Students": "Ardayda", "Results": "Natiijooyinka", "Subjects": "Maadooyinka",
        "Classes": "Fasallada", "Exams": "Imtixaannada", "Academic Year": "Sanad Dugsiyeedka", "Users": "Isticmaaleyaal",
        "Settings": "Dejinta", "Logout": "Ka Bax", "Search": "Raadi", "Student ID": "Aqoonsiga Ardayga",
        "Phone Number": "Lambarka Taleefanka", "Print Report": "Daabac Warbixinta", "Invalid username or password.": "Magaca ama furaha waa khalad.",
        "You have been logged out.": "Waad ka baxday.", "Settings saved.": "Dejinta waa la keydiyay.",
        "Unauthorized": "Lama oggola", "Verified": "La xaqiijiyay", "Invalid Result": "Natiijo aan sax ahayn",
        "Online Exam Result Management": "Maamulka Natiijooyinka Imtixaanka", "Results Portal": "Bogga Natiijooyinka",
        "Secure": "Ammaan", "Student ID lookup": "Raadin Aqoonsi Arday", "One-page report": "Warbixin hal bog ah",
        "Live": "Toos", "Database updates": "Cusbooneysiin xogeed", "Find Student Result": "Raadi Natiijada Ardayga",
        "Enter the official Student ID to view the published report.": "Geli aqoonsiga ardayga si aad u aragto natiijada la daabacay.",
        "Phone number verification failed.": "Xaqiijinta lambarka taleefanka way fashilantay.",
    },
    "ar": {
        "Dashboard": "لوحة التحكم", "Students": "الطلاب", "Results": "النتائج", "Subjects": "المواد",
        "Classes": "الفصول", "Exams": "الاختبارات", "Academic Year": "السنة الدراسية", "Users": "المستخدمون",
        "Settings": "الإعدادات", "Logout": "تسجيل الخروج", "Search": "بحث", "Student ID": "رقم الطالب",
        "Phone Number": "رقم الهاتف", "Print Report": "طباعة التقرير", "Invalid username or password.": "اسم المستخدم أو كلمة المرور غير صحيحة.",
        "You have been logged out.": "تم تسجيل الخروج.", "Settings saved.": "تم حفظ الإعدادات.",
        "Unauthorized": "غير مصرح", "Verified": "تم التحقق", "Invalid Result": "نتيجة غير صالحة",
        "Online Exam Result Management": "إدارة نتائج الامتحانات عبر الإنترنت", "Results Portal": "بوابة النتائج",
        "Secure": "آمن", "Student ID lookup": "البحث برقم الطالب", "One-page report": "تقرير صفحة واحدة",
        "Live": "مباشر", "Database updates": "تحديثات قاعدة البيانات", "Find Student Result": "البحث عن نتيجة الطالب",
        "Enter the official Student ID to view the published report.": "أدخل رقم الطالب الرسمي لعرض النتيجة المنشورة.",
        "Phone number verification failed.": "فشل التحقق من رقم الهاتف.",
    },
    "tr": {
        "Dashboard": "Panel", "Students": "Öğrenciler", "Results": "Sonuçlar", "Subjects": "Dersler",
        "Classes": "Sınıflar", "Exams": "Sınavlar", "Academic Year": "Akademik Yıl", "Users": "Kullanıcılar",
        "Settings": "Ayarlar", "Logout": "Çıkış", "Search": "Ara", "Student ID": "Öğrenci No",
        "Phone Number": "Telefon Numarası", "Print Report": "Raporu Yazdır", "Invalid username or password.": "Kullanıcı adı veya şifre geçersiz.",
        "You have been logged out.": "Çıkış yapıldı.", "Settings saved.": "Ayarlar kaydedildi.",
        "Unauthorized": "Yetkisiz", "Verified": "Doğrulandı", "Invalid Result": "Geçersiz Sonuç",
        "Online Exam Result Management": "Çevrimiçi Sınav Sonuç Yönetimi", "Results Portal": "Sonuç Portalı",
        "Secure": "Güvenli", "Student ID lookup": "Öğrenci No arama", "One-page report": "Tek sayfalık rapor",
        "Live": "Canlı", "Database updates": "Veritabanı güncellemeleri", "Find Student Result": "Öğrenci Sonucunu Bul",
        "Enter the official Student ID to view the published report.": "Yayınlanan raporu görmek için resmi öğrenci numarasını girin.",
        "Phone number verification failed.": "Telefon numarası doğrulaması başarısız.",
    },
}

TRANSLATIONS["so"].update({
    "Student Management": "Maamulka Ardayda", "Add Student": "Ku dar Arday", "Export": "Dhoofin", "Import": "Soo dejin", "Template": "Qaab",
    "Student Directory": "Liiska Ardayda", "ID": "Aqoonsi", "Name": "Magac", "Mother": "Hooyo", "Class": "Fasal", "Year": "Sanad", "Result Lock": "Qufulka Natiijada", "Actions": "Falal",
    "Edit": "Wax ka beddel", "Delete": "Tirtir", "Save Student": "Keydi Ardayga", "Full Name": "Magaca Buuxa", "Mother Name": "Magaca Hooyada", "Active Student": "Arday Firfircoon",
    "Result Management": "Maamulka Natiijooyinka", "Academic Records": "Diiwaannada Waxbarashada", "Add or Edit Results": "Ku dar ama wax ka beddel Natiijooyin", "Save Results": "Keydi Natiijooyinka", "Import Results": "Soo deji Natiijooyin", "Export Results": "Dhoofin Natiijooyin",
    "Latest Results": "Natiijooyinkii Ugu Dambeeyay", "Published": "La daabacay", "Score": "Dhibco", "Subject": "Maado", "Exam": "Imtixaan",
    "Add Admin": "Ku dar Maamule", "Role": "Door", "Permissions": "Ogolaansho", "Active": "Firfircoon", "Reset Password": "Dib u deji Furaha",
    "Audit Log": "Diiwaanka Kormeerka", "Security Audit Log": "Diiwaanka Amniga", "Date / Time": "Taariikh / Waqti", "Username": "Magac isticmaale", "IP Address": "Cinwaanka IP", "Action": "Fal", "Details": "Faahfaahin",
    "Import Wizard": "Hagaha Soo Dejinta", "Confirm Import": "Xaqiiji Soo Dejinta", "Settings saved.": "Dejinta waa la keydiyay.",
})
TRANSLATIONS["ar"].update({
    "Student Management": "إدارة الطلاب", "Add Student": "إضافة طالب", "Export": "تصدير", "Import": "استيراد", "Template": "قالب",
    "Student Directory": "دليل الطلاب", "ID": "المعرف", "Name": "الاسم", "Mother": "الأم", "Class": "الفصل", "Year": "السنة", "Result Lock": "قفل النتيجة", "Actions": "الإجراءات",
    "Edit": "تعديل", "Delete": "حذف", "Save Student": "حفظ الطالب", "Full Name": "الاسم الكامل", "Mother Name": "اسم الأم", "Active Student": "طالب نشط",
    "Result Management": "إدارة النتائج", "Academic Records": "السجلات الأكاديمية", "Add or Edit Results": "إضافة أو تعديل النتائج", "Save Results": "حفظ النتائج", "Import Results": "استيراد النتائج", "Export Results": "تصدير النتائج",
    "Latest Results": "أحدث النتائج", "Published": "منشور", "Score": "الدرجة", "Subject": "المادة", "Exam": "الاختبار",
    "Add Admin": "إضافة مدير", "Role": "الدور", "Permissions": "الصلاحيات", "Active": "نشط", "Reset Password": "إعادة تعيين كلمة المرور",
    "Audit Log": "سجل التدقيق", "Security Audit Log": "سجل تدقيق الأمان", "Date / Time": "التاريخ / الوقت", "Username": "اسم المستخدم", "IP Address": "عنوان IP", "Action": "الإجراء", "Details": "التفاصيل",
    "Import Wizard": "معالج الاستيراد", "Confirm Import": "تأكيد الاستيراد", "Settings saved.": "تم حفظ الإعدادات.",
})
TRANSLATIONS["tr"].update({
    "Student Management": "Öğrenci Yönetimi", "Add Student": "Öğrenci Ekle", "Export": "Dışa Aktar", "Import": "İçe Aktar", "Template": "Şablon",
    "Student Directory": "Öğrenci Listesi", "ID": "No", "Name": "Ad", "Mother": "Anne", "Class": "Sınıf", "Year": "Yıl", "Result Lock": "Sonuç Kilidi", "Actions": "İşlemler",
    "Edit": "Düzenle", "Delete": "Sil", "Save Student": "Öğrenciyi Kaydet", "Full Name": "Tam Ad", "Mother Name": "Anne Adı", "Active Student": "Aktif Öğrenci",
    "Result Management": "Sonuç Yönetimi", "Academic Records": "Akademik Kayıtlar", "Add or Edit Results": "Sonuç Ekle veya Düzenle", "Save Results": "Sonuçları Kaydet", "Import Results": "Sonuçları İçe Aktar", "Export Results": "Sonuçları Dışa Aktar",
    "Latest Results": "Son Sonuçlar", "Published": "Yayınlandı", "Score": "Puan", "Subject": "Ders", "Exam": "Sınav",
    "Add Admin": "Yönetici Ekle", "Role": "Rol", "Permissions": "İzinler", "Active": "Aktif", "Reset Password": "Şifreyi Sıfırla",
    "Audit Log": "Denetim Kaydı", "Security Audit Log": "Güvenlik Denetim Kaydı", "Date / Time": "Tarih / Saat", "Username": "Kullanıcı Adı", "IP Address": "IP Adresi", "Action": "İşlem", "Details": "Detaylar",
    "Import Wizard": "İçe Aktarma Sihirbazı", "Confirm Import": "İçe Aktarmayı Onayla", "Settings saved.": "Ayarlar kaydedildi.",
})


def current_language(default="en"):
    lang = request.args.get("lang") or session.get("language") or default or "en"
    if lang not in SUPPORTED_LANGUAGES:
        lang = "en"
    session["language"] = lang
    return lang


def translate(label):
    lang = session.get("language", "en")
    return TRANSLATIONS.get(lang, TRANSLATIONS["en"]).get(label, label)


def active_translations():
    lang = session.get("language", "en")
    return TRANSLATIONS.get(lang, TRANSLATIONS["en"])


def language_redirect(lang):
    if lang in SUPPORTED_LANGUAGES:
        session["language"] = lang
    return redirect(request.referrer or url_for("public.portal"))
