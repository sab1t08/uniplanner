"""تنظیمات مرکزی پروژه. همه‌ی ثابت‌ها فقط از اینجا خوانده می‌شوند."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# --- پایگاه داده ---
BASE_DB = Path(os.getenv("UNI_BASE_DB", ROOT / "university.db"))
OUTPUT_DB = Path(os.getenv("UNI_OUTPUT_DB", ROOT / "schedules_output.db"))

# --- ساختار زمانی ---
DAYS = ("شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه")
TIME_SLOTS = ("07:30-09:30", "09:30-11:30", "13:00-15:00", "15:00-17:00")
SLOT_HOURS = 2  # طول هر اسلات؛ تعداد جلسات هر درس = ceil(weekly_hours / SLOT_HOURS)

# --- نیمسال‌ها ---
SEMESTERS = ("1405-1", "1405-2", "1404-2")
DEFAULT_SEMESTER = "1405-1"

# --- انواع فضا ---
ROOM_THEORY, ROOM_LAB, ROOM_SPORTS = "THEORY", "LAB", "SPORTS"
ROOM_TYPES = (ROOM_THEORY, ROOM_LAB, ROOM_SPORTS)

# --- وزن‌های قیدهای نرم (برای سولور) ---
SLOT_COST = {"07:30-09:30": 2, "09:30-11:30": 0, "13:00-15:00": 0, "15:00-17:00": 1}
DAY_SOFT_LIMIT = 2        # بیش از این تعداد جلسه در روز برای یک رشته/ترم جریمه دارد
DAY_OVERLOAD_COST = 10
MOVE_COST = 5             # هزینه‌ی جابه‌جایی نسبت به برنامه‌ی قبلی

UNDO_DEPTH = 20           # تعداد مراحل قابل بازگشت برای هر نیمسال
SOLVER_TIME_LIMIT = float(os.getenv("UNI_SOLVER_TIME", "30"))
BIG_REWARD = 1000         # پاداش هر واحد درس چیده‌شده (باید از همه‌ی جریمه‌ها بزرگ‌تر باشد)

# --- مدل زبانی ---
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "120"))

# اتصال محلی نباید از پروکسی/فیلترشکن سیستم عبور کند
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")
