LOWERCASE_CONNECTORS = {"a", "e", "da", "das", "de", "do", "dos"}
JOB_ACRONYMS = {"CEO", "CFO", "CIPA", "CTO", "PCD", "RH", "SAC", "TI"}


def _format_words(value, preserve_job_acronyms=False):
    words = " ".join(str(value or "").strip().split()).split(" ")
    formatted = []
    for index, word in enumerate(words):
        lower = word.lower()
        letters_without_dots = word.rstrip(".").replace(".", "")
        is_dotted_acronym = (
            "." in word
            and len(letters_without_dots) >= 2
            and letters_without_dots.isalpha()
        )
        if index > 0 and lower in LOWERCASE_CONNECTORS:
            formatted.append(lower)
        elif is_dotted_acronym:
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
