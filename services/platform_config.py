# services/platform_config.py
from dataclasses import dataclass


@dataclass
class PlatformConfig:
    platform: str           # "ca" or "cs"
    full_name: str          # "Chartered Accountancy" / "Computer Science"
    subject_label: str      # "CA" / "CS"
    domain_terms: str       # terms that must stay in English in Hinglish output
    style: str              # "hinglish" or "english"
    student_name: str       # e.g. "Rahul"
    teacher_name: str       # e.g. "Priya"
    teacher_gender: str     # "male" / "female"


CA_CONFIG = PlatformConfig(
    platform="ca",
    full_name="Chartered Accountancy",
    subject_label="CA",
    domain_terms="Debit, Credit, Bank Reconciliation, Trial Balance, P&L, Balance Sheet, GST, TDS",
    style="hinglish",
    student_name="Rahul",
    teacher_name="Priya",
    teacher_gender="female",
)

CS_CONFIG = PlatformConfig(
    platform="cs",
    full_name="Computer Science",
    subject_label="CS",
    domain_terms="algorithm, recursion, stack, queue, pointer, API, binary tree, OS, DBMS, networking",
    style="hinglish",   # keep hinglish for Indian CS students; change to "english" if needed
    student_name="Aryan",
    teacher_name="Anjali",
    teacher_gender="female",
)

PLATFORM_CONFIGS: dict[str, PlatformConfig] = {
    "ca": CA_CONFIG,
    "cs": CS_CONFIG,
}
