import re


LOWERCASE_CONNECTORS = {"a", "e", "da", "das", "de", "do", "dos"}
JOB_ACRONYMS = {"CEO", "CFO", "CIPA", "CTO", "PCD", "RH", "SAC", "TI"}


def _format_words(value, preserve_job_acronyms=False):
    words = " ".join(str(value or "").strip().split()).split(" ")
    formatted = []
    for index, word in enumerate(words):
        lower = word.lower()
        if index > 0 and lower in LOWERCASE_CONNECTORS:
            formatted.append(lower)
        elif re.fullmatch(r"(?:[A-Za-z]\.){2,}", word):
            formatted.append(word.upper().rstrip("."))
        elif preserve_job_acronyms and word.upper().rstrip(".") in JOB_ACRONYMS:
            formatted.append(word.upper().rstrip("."))
        else:
            formatted.append(lower.capitalize())
    return " ".join(formatted)


def format_person_name(value):
    return _format_words(value)


def format_job_title(value):
    return _format_words(value, preserve_job_acronyms=True)
